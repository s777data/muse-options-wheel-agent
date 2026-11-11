#!/usr/bin/env python3
"""
Covered Call Evaluation — Real-time agent execution.
Loads: Trading-account-covered-call-process, trading-account-session-management
Outputs: Covered call recommendations for eligible lots + Telegram delivery
"""

import sys
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # repo root (D-10)

# Transport (D-06): the source imports Hermes bindings from hermes_tools.
# Here the same names are bound to the local read-only client shim; each
# returns the Hermes envelope {"result": "<json>"} parsed below.
# get_option_positions / get_option_orders are bound additionally for the
# D-07 reserved-share accounting.
from scripts.robinhood_transport import hermes_envelope as _hermes_envelope


def _bind(tool_name):
    def _call(**arguments):
        return _hermes_envelope(tool_name, arguments)
    return _call


mcp__robinhood_trading__get_accounts = _bind("get_accounts")
mcp__robinhood_trading__get_equity_positions = _bind("get_equity_positions")
mcp__robinhood_trading__get_equity_tax_lots = _bind("get_equity_tax_lots")
mcp__robinhood_trading__get_option_chains = _bind("get_option_chains")
mcp__robinhood_trading__get_option_instruments = _bind("get_option_instruments")
mcp__robinhood_trading__get_option_quotes = _bind("get_option_quotes")
mcp__robinhood_trading__get_equity_technical_indicators = _bind("get_equity_technical_indicators")
mcp__robinhood_trading__get_option_positions = _bind("get_option_positions")
mcp__robinhood_trading__get_option_orders = _bind("get_option_orders")

# ==================== CONFIG (from skill) ====================
STANDARD_OPTION_CONTRACT_SHARES = 100
MIN_DTE = 30
MAX_DTE = 45
MIN_DELTA = 0.20
MAX_DELTA = 0.35
MIN_ROC = 0.02  # 2% return on collateral
MAX_BID_ASK_SPREAD = 0.10


# ==================== D-07 RESERVED-SHARE ACCOUNTING (PRD gap 7) ====================
def _payload_results(payload: dict) -> list:
    """Rows from a Hermes-envelope payload's data object."""
    data = payload.get("data") or {}
    rows = data.get("results")
    if isinstance(rows, list):
        return rows
    for key in ("positions", "orders", "instruments"):
        if isinstance(data.get(key), list):
            return data[key]
    return []


def _instrument_type(option_id, chain_symbol=None,
                     expiration_date=None) -> Optional[str]:
    """Resolve an option instrument's type ("call"/"put").

    D-23: the MCP rejects get_option_instruments lookups by `ids`
    (JSON-RPC error, both full runs + direct retry); resolve chain-scoped
    by chain_symbol + expiration_date and match the instrument id locally
    — the same technique D-21 applied in Phase 2. Fail-closed: an
    unresolvable type raises, per D-07's read-failure rule.
    """
    if not chain_symbol:
        raise ValueError(
            f"cannot resolve option type for {option_id}: no chain_symbol")
    kwargs = {"chain_symbol": chain_symbol}
    if expiration_date:
        kwargs["expiration_dates"] = expiration_date
    result = mcp__robinhood_trading__get_option_instruments(**kwargs)
    rows = _payload_results(json.loads(result["result"]))
    for row in rows:
        if str(row.get("id") or row.get("option_id") or "") == str(option_id):
            return row.get("type")
    raise ValueError(
        f"instrument {option_id} not found in {chain_symbol} chain")


def _committed_call_shares(account_number) -> Dict[str, float]:
    """Shares per symbol already committed to (a) existing short call
    positions and (b) open/pending sell-to-open call orders.

    D-07 (PRD §11 gap 7): the source computed eligible lots from equity
    quantity alone. Fail-closed: any broker read failure propagates,
    because an undercount would overstate eligible lots.
    """
    committed: Dict[str, float] = {}
    pos_result = mcp__robinhood_trading__get_option_positions(
        account_number=account_number, nonzero=True)
    for pos in _payload_results(json.loads(pos_result["result"])):
        if pos.get("type") != "short":
            continue
        option_id = pos.get("option_id")
        symbol = pos.get("chain_symbol")
        try:
            qty = float(pos.get("quantity", 0) or 0)
        except (TypeError, ValueError):
            qty = 0.0
        if not option_id or not symbol or qty <= 0:
            continue
        if _instrument_type(option_id, chain_symbol=symbol,
                            expiration_date=pos.get("expiration_date")) != "call":
            continue
        committed[symbol] = (committed.get(symbol, 0.0)
                             + qty * STANDARD_OPTION_CONTRACT_SHARES)
    ord_result = mcp__robinhood_trading__get_option_orders(
        account_number=account_number)
    open_states = {"queued", "confirmed", "unconfirmed",
                   "partially_filled", "pending"}
    for order in _payload_results(json.loads(ord_result["result"])):
        state = str(order.get("state") or order.get("status") or "").lower()
        if state not in open_states:
            continue
        legs = order.get("legs") or [order]
        for leg in legs:
            if not isinstance(leg, dict):
                continue
            effect = str(leg.get("position_effect")
                         or order.get("position_effect") or "").lower()
            side = str(leg.get("side") or order.get("side") or "").lower()
            if "open" not in effect or side != "sell":
                continue
            option_id = (leg.get("option_id") or leg.get("instrument_id")
                         or order.get("option_id"))
            symbol = leg.get("chain_symbol") or order.get("chain_symbol")
            try:
                qty = float(leg.get("quantity") or order.get("quantity") or 0)
            except (TypeError, ValueError):
                qty = 0.0
            if not option_id or not symbol or qty <= 0:
                continue
            if _instrument_type(option_id, chain_symbol=symbol,
                                expiration_date=(leg.get("expiration_date")
                                                 or order.get("expiration_date"))) != "call":
                continue
            committed[symbol] = (committed.get(symbol, 0.0)
                                 + qty * STANDARD_OPTION_CONTRACT_SHARES)
    return committed


# ==================== MAIN ====================
def run_covered_call_evaluation() -> dict:
    """Evaluate eligible share lots for covered calls."""
    print(f"[{datetime.now()}] Starting covered call evaluation...", flush=True)
    
    # Get agentic account
    accounts_result = mcp__robinhood_trading__get_accounts()
    accounts_data = json.loads(accounts_result["result"])
    agentic_account = None
    for acc in accounts_data["data"]["results"]:
        if acc.get("agentic_allowed"):
            agentic_account = acc["account_number"]
            break
    
    if not agentic_account:
        return {"error": "No agentic account found", "recommendations": []}

    # D-07: shares committed to existing short calls / pending call
    # orders are not available for new covered calls (fail-closed).
    committed_call_shares = _committed_call_shares(agentic_account)
    
    # Get equity positions
    pos_result = mcp__robinhood_trading__get_equity_positions(account_number=agentic_account)
    pos_data = json.loads(pos_result["result"])
    
    recommendations = []
    
    for pos in pos_data["data"]["results"]:
        symbol = pos.get("symbol")
        try:
            quantity = float(pos.get("quantity") or 0)
        except (TypeError, ValueError):
            quantity = 0.0
        
        if quantity < STANDARD_OPTION_CONTRACT_SHARES:
            continue  # Need at least 100 shares per contract
        
        committed = committed_call_shares.get(symbol, 0.0)
        available_shares = quantity - committed
        if available_shares < STANDARD_OPTION_CONTRACT_SHARES:
            continue  # Fully committed to existing short calls/orders (D-07)
        contracts_available = int(available_shares // STANDARD_OPTION_CONTRACT_SHARES)
        
        # Get tax lots for cost basis
        lots_result = mcp__robinhood_trading__get_equity_tax_lots(account_number=agentic_account, symbol=symbol)
        lots_data = json.loads(lots_result["result"])
        
        # Calculate weighted average cost basis.
        # D-23: live tax lots arrive under data.tax_lots (verified), not
        # data.results; per-share cost may be a total cost_basis instead.
        total_shares = 0
        total_cost = 0
        lot_rows = (lots_data.get("data") or {}).get("tax_lots")
        if not isinstance(lot_rows, list):
            lot_rows = _payload_results(lots_data)
        for lot in lot_rows:
            try:
                qty = float(lot.get("quantity") or 0)
                cost = float(lot.get("cost_basis_per_share") or 0)
                if cost <= 0 and lot.get("cost_basis"):
                    cost = float(lot["cost_basis"]) / qty if qty > 0 else 0
            except (TypeError, ValueError):
                continue
            if qty > 0 and cost > 0:
                total_shares += qty
                total_cost += qty * cost
        
        avg_cost = total_cost / total_shares if total_shares > 0 else 0
        
        # Get option chains
        try:
            chains_result = mcp__robinhood_trading__get_option_chains(underlying_symbol=symbol)
            chains_data = json.loads(chains_result["result"])
            
            # D-23: live chains arrive under data.chains, each carrying an
            # expiration_dates LIST and trade_value_multiplier as the
            # string "100.0000" (both verified live 2026-11-11).
            chain_rows = (chains_data.get("data") or {}).get("chains")
            if not isinstance(chain_rows, list):
                chain_rows = _payload_results(chains_data)
            for chain in chain_rows:
                if not chain.get("can_open_position"): continue
                try:
                    multiplier = float(chain.get("trade_value_multiplier") or 0)
                except (TypeError, ValueError):
                    continue
                if multiplier != 100: continue

                exp_dates = chain.get("expiration_dates") or []
                if not exp_dates and chain.get("expiration_date"):
                    exp_dates = [chain["expiration_date"]]
                for exp_date in exp_dates:
                    exp_dt = datetime.strptime(exp_date, "%Y-%m-%d").date()
                    today = datetime.now().date()
                    dte = (exp_dt - today).days
                    if not (MIN_DTE <= dte <= MAX_DTE): continue
                
                    # Get call options
                    inst_result = mcp__robinhood_trading__get_option_instruments(
                        chain_id=chain["id"], expiration_dates=exp_date, type="call"
                    )
                    inst_data = json.loads(inst_result["result"])
                
                    for opt in _payload_results(inst_data):
                        if opt.get("state") not in (None, "active"): continue
                        strike = float(opt["strike_price"])
                        if strike <= avg_cost: continue  # Don't sell below cost basis
                    
                        option_id = opt["id"]
                        quote_result = mcp__robinhood_trading__get_option_quotes(instrument_ids=[option_id])
                        quote_data = json.loads(quote_result["result"])
                    
                        quote_rows = _payload_results(quote_data)
                        if not quote_rows: continue
                        q = quote_rows[0]
                        if isinstance(q, dict) and isinstance(q.get("quote"), dict):
                            q = q["quote"]  # D-23: live quotes nest under "quote"
                    
                        bid = float(q.get("bid_price", 0) or 0)
                        ask = float(q.get("ask_price", 0) or 0)
                        try:
                            delta = float(q.get("delta")) if q.get("delta") is not None else None
                        except (TypeError, ValueError):
                            delta = None
                    
                        if bid > 0 and ask > 0 and delta:
                            mid = (bid + ask) / 2
                            spread = (ask - bid) / mid if mid > 0 else 1.0
                            roc = (mid / strike) if strike > 0 else 0
                        
                            if (MIN_DELTA <= delta <= MAX_DELTA and 
                                spread <= MAX_BID_ASK_SPREAD and 
                                roc >= MIN_ROC):
                            
                                # Technical check (RSI < 70). D-23: timedelta
                                # was never imported and the call used symbol=
                                # where the MCP takes symbols=[...] — both errors
                                # were swallowed by the bare except, so this gate
                                # never filtered anything. Fixed call + tolerant
                                # parse; the source's fail-open default stays.
                                try:
                                    rsi_result = mcp__robinhood_trading__get_equity_technical_indicators(
                                        symbols=[symbol], type="rsi", period=14, interval="day",
                                        start_time=(datetime.now(timezone.utc) - timedelta(days=60)).strftime("%Y-%m-%dT%H:%M:%SZ")
                                    )
                                    rsi_data = json.loads(rsi_result["result"])
                                    rdata = rsi_data.get("data") or {}
                                    indicators = rdata.get("indicators")
                                    if indicators is None:
                                        for row in (rdata.get("results") or []):
                                            if isinstance(row, dict) and row.get("indicators"):
                                                indicators = row["indicators"]
                                                break
                                    first = (indicators or [])[0]
                                    series = first.get("data") or first.get("series") or []
                                    rsi = float(series[-1]["value"])
                                    rsi_ok = rsi < 70
                                except Exception as rsi_err:
                                    print(f"  RSI check unavailable for {symbol}: {rsi_err}", flush=True)
                                    rsi_ok = True  # Source default: ok if technical fails
                            
                                if rsi_ok:
                                    premium_total = mid * STANDARD_OPTION_CONTRACT_SHARES * contracts_available
                                    annualized_roc = roc * (365 / dte)
                                
                                    recommendations.append({
                                        "symbol": symbol,
                                        "contracts_available": contracts_available,
                                        "shares_committed_to_short_calls": committed,
                                        "avg_cost_basis": avg_cost,
                                        "current_price": float(pos.get("last_price") or pos.get("average_buy_price") or 0),
                                        "strike": strike,
                                        "expiration": exp_date,
                                        "dte": dte,
                                        "delta": delta,
                                        "premium_per_contract": mid,
                                        "total_premium": premium_total,
                                        "roc": roc,
                                        "annualized_roc": annualized_roc,
                                        "spread_pct": spread,
                                        "call_away_price": strike,
                                        "call_away_profit": (strike - avg_cost) * STANDARD_OPTION_CONTRACT_SHARES * contracts_available
                                    })
        except Exception as e:
            print(f"  Warning: Covered call eval failed for {symbol}: {e}", flush=True)
    
    # Sort by ROC desc
    recommendations.sort(key=lambda x: x["roc"], reverse=True)
    recommendations = recommendations[:10]  # Top 10
    
    # Build telegram message
    lines = ["📊 **Covered Call Evaluation**", f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S %Z')}", ""]
    
    if not recommendations:
        lines.append("No qualifying covered call opportunities found.")
        lines.append("Criteria: DTE 30-45, Δ 0.20-0.35, ROC ≥2%, spread ≤10%, strike > cost basis, RSI < 70")
    else:
        lines.append(f"Found {len(recommendations)} opportunities:")
        for i, r in enumerate(recommendations, 1):
            lines.append(
                f"{i}. **{r['symbol']}** ${r['strike']:.2f} Call {r['expiration']} (DTE {r['dte']})"
                f"\n   📦 {r['contracts_available']} contracts avail | Cost: ${r['avg_cost_basis']:.2f} | Curr: ${r['current_price']:.2f}"
                f"\n   💰 Premium: ${r['premium_per_contract']:.2f}/contract = ${r['total_premium']:.2f} total"
                f"\n   📊 ROC: {r['roc']:.2%} ({r['annualized_roc']:.1%} ann.) | Δ: {r['delta']:.2f} | Spread: {r['spread_pct']:.1%}"
                f"\n   🎯 Call-away: ${r['call_away_price']:.2f} → Profit: ${r['call_away_profit']:+.2f}"
            )
            lines.append("")
    
    lines.append("⚠️ This is analysis only — no orders placed without explicit confirmation.")
    
    return {
        "recommendations": recommendations,
        "telegram_message": "\n".join(lines)
    }

if __name__ == "__main__":
    result = run_covered_call_evaluation()
    print(json.dumps(result, indent=2, default=str))