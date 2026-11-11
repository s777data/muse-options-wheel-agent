#!/usr/bin/env python3
"""
Profit Taking Decider — Real-time agent execution.
Loads: profit-taking-decider, trading-account-session-management
Outputs: Profit-taking signals for all open short options + Telegram delivery
"""

import sys
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # repo root (D-10)

# Transport (D-06): the source imports Hermes bindings from hermes_tools.
# Here the same names are bound to the local read-only client shim; each
# returns the Hermes envelope {"result": "<json>"} parsed below.
from scripts.robinhood_transport import hermes_envelope as _hermes_envelope


def _bind(tool_name):
    def _call(**arguments):
        return _hermes_envelope(tool_name, arguments)
    return _call


mcp__robinhood_trading__get_accounts = _bind("get_accounts")
mcp__robinhood_trading__get_option_positions = _bind("get_option_positions")
mcp__robinhood_trading__get_option_quotes = _bind("get_option_quotes")
mcp__robinhood_trading__get_option_instruments = _bind("get_option_instruments")
mcp__robinhood_trading__get_equity_quotes = _bind("get_equity_quotes")


# ==================== D-21: INSTRUMENT RESOLUTION ====================
# Live option positions carry expiration_date and opened_at but no
# strike or put/call type, and the Robinhood MCP errors on
# get_option_instruments queried by ids. Resolve by chain + expiration
# + type (the Phase 6 call shape) and match the position's option_id.
def _resolve_option_instrument(chain_id: str, expiration: str,
                               option_id: str) -> Optional[dict]:
    for option_type in ("put", "call"):
        cursor = None
        for _page in range(10):
            args = {"chain_id": chain_id, "expiration_dates": expiration,
                    "type": option_type}
            if cursor:
                args["cursor"] = cursor
            data = json.loads(
                mcp__robinhood_trading__get_option_instruments(**args)["result"])
            payload = data.get("data", {})
            instruments = (payload.get("instruments")
                           or payload.get("results") or [])
            for inst in instruments:
                if inst.get("id") == option_id:
                    return inst
            cursor = payload.get("next_cursor") or payload.get("cursor")
            if not cursor:
                nxt = payload.get("next")
                if isinstance(nxt, str) and "cursor=" in nxt:
                    cursor = nxt.split("cursor=", 1)[1].split("&", 1)[0]
            if not cursor:
                break
    return None

# ==================== CONFIG ====================
STANDARD_OPTION_CONTRACT_SHARES = 100

# ==================== PROFIT TAKING FORMULA ====================
# D-22: fixed minimum premium-capture gate from the profit-taking-
# decider skill spec. Fixed policy, NOT a configurable threshold.
MIN_PROFIT_CAPTURE_PERCENT = 50.00

def profit_taking_signal(premium_received: float, days_elapsed: int, total_days: int,
                          current_price: float, strike: float, option_type: str,
                          current_mark: float | None = None) -> Dict:
    """
    Profit-taking signal per the profit-taking-decider skill spec (D-22).

    profit_percent = 100 * (premium_received - current_mark)
                     / premium_received
    required_profit_percent = 100 * sqrt(days_elapsed / total_days)

    signal = TAKE only when BOTH strict comparisons hold:
        profit_percent > MIN_PROFIT_CAPTURE_PERCENT (50.00, fixed gate)
        AND profit_percent > required_profit_percent
    Equality at either boundary is HOLD. (Before D-22 this function
    used an underlying-price trigger — underlying vs strike -/+ a
    premium boundary — and never computed profit percent at all; that
    variant is NOT the skill spec and could emit TAKE below the 50%
    gate, e.g. FIGR Dec 18 $20P at 48.7% captured on 2026-11-11.)

    `boundary` / `trigger_price` are retained as informational fields
    only (premium dollars on the sqrt curve; underlying trigger of the
    legacy variant). They do not drive the signal.
    """
    if total_days <= 0 or days_elapsed < 0:
        return {"error": "Invalid days", "signal": "HOLD", "boundary": 0}
    if premium_received <= 0:
        return {"error": "Invalid premium", "signal": "HOLD", "boundary": 0}
    if current_mark is None:
        return {"error": "current_mark_missing", "signal": "HOLD",
                "boundary": 0}

    elapsed_fraction = min(days_elapsed, total_days) / total_days
    sqrt_pct = math.sqrt(elapsed_fraction)
    boundary = premium_received * sqrt_pct
    if option_type == "put":
        trigger_price = strike - boundary
    else:  # call
        trigger_price = strike + boundary

    profit_percent = 100.0 * (premium_received - current_mark) \
        / premium_received
    required_profit_percent = 100.0 * sqrt_pct
    signal = "TAKE" if (profit_percent > MIN_PROFIT_CAPTURE_PERCENT
                        and profit_percent > required_profit_percent) \
        else "HOLD"

    return {
        "signal": signal,
        "boundary": boundary,
        "trigger_price": trigger_price,
        "profit_percent": profit_percent,
        "required_profit_percent": required_profit_percent,
        "min_profit_capture_percent": MIN_PROFIT_CAPTURE_PERCENT,
        "current_mark": current_mark,
        "premium_received": premium_received,
        "days_elapsed": days_elapsed,
        "total_days": total_days,
        "pct_lifetime_elapsed": elapsed_fraction,
        "sqrt_pct": sqrt_pct,
        "current_price": current_price,
        "strike": strike,
        "option_type": option_type
    }

# ==================== LINEAR COMPARISON ====================
def linear_boundary(premium_received: float, days_elapsed: int, total_days: int) -> float:
    """Linear boundary for comparison: premium * (days_elapsed / total_days)"""
    if total_days <= 0:
        return 0
    return premium_received * (min(days_elapsed, total_days) / total_days)

# ==================== MAIN ====================
def run_profit_taking_check() -> dict:
    """Check profit-taking signals for all open short options."""
    print(f"[{datetime.now()}] Starting profit-taking check...", flush=True)
    
    # Get agentic account
    accounts_result = mcp__robinhood_trading__get_accounts()
    accounts_data = json.loads(accounts_result["result"])
    agentic_account = None
    for acc in accounts_data["data"]["results"]:
        if acc.get("agentic_allowed"):
            agentic_account = acc["account_number"]
            break
    
    if not agentic_account:
        return {"error": "No agentic account found", "signals": []}
    
    # Get open option positions
    pos_result = mcp__robinhood_trading__get_option_positions(account_number=agentic_account, nonzero=True)
    pos_data = json.loads(pos_result["result"])
    
    short_positions = []
    for pos in pos_data["data"]["results"]:
        if pos.get("type") == "short" and float(pos.get("quantity", 0) or 0) > 0:
            short_positions.append(pos)
    
    if not short_positions:
        msg = "📊 **Profit Taking Check**\n\nNo open short option positions found."
        return {"signals": [], "telegram_message": msg}
    
    # Get quotes for all short positions
    option_ids = [p["option_id"] for p in short_positions]
    quotes_result = mcp__robinhood_trading__get_option_quotes(instrument_ids=option_ids)
    quotes_data = json.loads(quotes_result["result"])
    
    quote_map = {}
    for q in quotes_data["data"]["results"]:
        qq = q.get("quote", q)
        quote_map[qq["instrument_id"]] = qq
    
    # D-21: live underlying prices, one call for all position symbols.
    symbols = sorted({p.get("chain_symbol") for p in short_positions
                      if p.get("chain_symbol")})
    underlying_prices: Dict[str, float] = {}
    if symbols:
        eq = json.loads(mcp__robinhood_trading__get_equity_quotes(
            symbols=symbols)["result"])
        for r in eq["data"]["results"]:
            q = r.get("quote", r)
            if q.get("symbol") and q.get("last_trade_price"):
                underlying_prices[q["symbol"]] = float(q["last_trade_price"])

    signals = []
    for pos in short_positions:
        option_id = pos["option_id"]
        quote = quote_map.get(option_id)
        if not quote:
            continue

        # Get position details. average_price is signed per-contract
        # dollars x100 convention: -396.6667 == $3.9667/share premium
        # received on a short; per-share premium divides by the
        # trade-value multiplier (D-21 units fix).
        quantity = float(pos.get("quantity", 0) or 0)
        multiplier = float(pos.get("trade_value_multiplier", 100) or 100)
        avg_price = float(pos.get("average_price", 0))
        premium_per_share = abs(avg_price) / multiplier \
            if multiplier else abs(avg_price) / 100.0
        current_bid = float(quote.get("bid_price", 0) or 0)
        current_ask = float(quote.get("ask_price", 0) or 0)
        if current_bid and current_ask:
            current_mid = (current_bid + current_ask) / 2
        else:
            current_mid = float(quote.get("mark_price", 0) or 0)

        symbol = pos.get("chain_symbol", "UNKNOWN")
        expiration = pos.get("expiration_date", "")

        # D-21: resolve strike + put/call type from the instrument
        # (positions don't carry them), then run the real sqrt-boundary
        # signal with days from opened_at -> expiration_date.
        instrument = None
        if pos.get("chain_id") and expiration:
            instrument = _resolve_option_instrument(
                pos["chain_id"], expiration, option_id)
        if instrument is not None:
            strike = float(instrument.get("strike_price", 0) or 0)
            option_type = instrument.get("type", "put")
        else:
            strike = float(pos.get("strike_price", 0) or 0)
            option_type = pos.get("option_type", "put")

        row = {
            "option_id": option_id,
            "symbol": symbol,
            "option_type": option_type,
            "quantity": quantity,
            "strike": strike,
            "expiration": expiration,
            "premium_received": premium_per_share,
            "current_mid": current_mid,
            "unrealized_pnl": (premium_per_share - current_mid)
                * quantity * multiplier,
            "underlying_price": underlying_prices.get(symbol),
            "signal": "CALCULATE",
        }
        underlying = underlying_prices.get(symbol)
        opened_raw = pos.get("opened_at")
        if instrument is not None and underlying and opened_raw:
            opened_date = datetime.fromisoformat(
                opened_raw.replace("Z", "+00:00")).date()
            expiry_date = datetime.fromisoformat(expiration).date()
            today = datetime.now(timezone.utc).date()
            total_days = (expiry_date - opened_date).days
            days_elapsed = max(0, (today - opened_date).days)
            result = profit_taking_signal(
                premium_per_share, days_elapsed, total_days,
                underlying, strike, option_type,
                current_mark=current_mid)
            if "error" not in result:
                row.update({
                    "signal": result["signal"],
                    "boundary": result["boundary"],
                    "trigger_price": result["trigger_price"],
                    "profit_percent": result["profit_percent"],
                    "required_profit_percent":
                        result["required_profit_percent"],
                    "min_profit_capture_percent":
                        result["min_profit_capture_percent"],
                    "days_elapsed": days_elapsed,
                    "total_days": total_days,
                    "pct_lifetime_elapsed": result["pct_lifetime_elapsed"],
                })
            else:
                row["signal_error"] = result["error"]
        elif instrument is None:
            row["signal_error"] = "instrument_resolution_failed"
        signals.append(row)
    
    # Build telegram message
    lines = ["📊 **Profit Taking Check**", f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S %Z')}", ""]
    
    if not signals:
        lines.append("No short positions with valid quotes.")
    else:
        for s in signals:
            lines.append(f"**{s['symbol']}** {s['option_type'].upper()} ${s['strike']:.2f} {s['expiration']}")
            lines.append(f"  Qty: {s['quantity']} | Premium: ${s['premium_received']:.2f} | Current: ${s['current_mid']:.2f}")
            lines.append(f"  Unrealized P&L: ${s['unrealized_pnl']:+.2f}")
            if "trigger_price" in s:
                lines.append(f"  Underlying: ${s['underlying_price']:.2f} | Trigger: ${s['trigger_price']:.2f} | Boundary: ${s['boundary']:.2f} | Day {s['days_elapsed']}/{s['total_days']}")
                lines.append(f"  Captured: {s['profit_percent']:.1f}% | Required: {s['required_profit_percent']:.1f}% (sqrt curve) + >{s['min_profit_capture_percent']:.0f}% gate")
                lines.append(f"  Signal: {s['signal']}")
            else:
                lines.append(f"  Signal: {s['signal']} ({s.get('signal_error', 'insufficient data')})")
            lines.append("")

    lines.append("⚠️ Rule (profit-taking-decider skill): TAKE only if captured % > 50.00% AND > 100 * sqrt(elapsed_days / total_days)")
    
    return {
        "signals": signals,
        "telegram_message": "\n".join(lines)
    }

if __name__ == "__main__":
    result = run_profit_taking_check()
    print(json.dumps(result, indent=2, default=str))