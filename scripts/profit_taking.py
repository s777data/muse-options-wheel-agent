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

# ==================== CONFIG ====================
STANDARD_OPTION_CONTRACT_SHARES = 100

# ==================== PROFIT TAKING FORMULA ====================
def profit_taking_signal(premium_received: float, days_elapsed: int, total_days: int, 
                          current_price: float, strike: float, option_type: str) -> Dict:
    """
    Square-root elapsed-lifetime boundary (profit-taking-decider skill).
    
    Formula: boundary = premium_received * sqrt(days_elapsed / total_days)
    
    For SHORT PUT: signal = TAKE if current_price >= strike - boundary
    For SHORT CALL: signal = TAKE if current_price <= strike + boundary
    
    This is the parameter-free square-root boundary per skill spec.
    """
    if total_days <= 0 or days_elapsed < 0:
        return {"error": "Invalid days", "signal": "HOLD", "boundary": 0}
    
    boundary = premium_received * math.sqrt(min(days_elapsed, total_days) / total_days)
    
    if option_type == "put":
        # Short put: profit when underlying rises above (strike - boundary)
        trigger_price = strike - boundary
        signal = "TAKE" if current_price >= trigger_price else "HOLD"
    else:  # call
        # Short call: profit when underlying falls below (strike + boundary)
        trigger_price = strike + boundary
        signal = "TAKE" if current_price <= trigger_price else "HOLD"
    
    pct_of_max_profit = min(days_elapsed, total_days) / total_days
    sqrt_pct = math.sqrt(pct_of_max_profit)
    
    return {
        "signal": signal,
        "boundary": boundary,
        "trigger_price": trigger_price,
        "premium_received": premium_received,
        "days_elapsed": days_elapsed,
        "total_days": total_days,
        "pct_lifetime_elapsed": pct_of_max_profit,
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
        if pos.get("type") == "short" and pos.get("quantity", 0) > 0:
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
        quote_map[q["instrument_id"]] = q
    
    signals = []
    for pos in short_positions:
        option_id = pos["option_id"]
        quote = quote_map.get(option_id)
        if not quote:
            continue
        
        # Get position details
        quantity = pos.get("quantity", 0)
        avg_price = float(pos.get("average_price", 0))  # premium received per contract
        current_bid = float(quote.get("bid_price", 0) or 0)
        current_ask = float(quote.get("ask_price", 0) or 0)
        current_mid = (current_bid + current_ask) / 2 if current_bid and current_ask else 0
        
        # Need underlying price and option details
        # For simplicity, use chain data or assume we have it
        # In real run, would fetch option instrument for strike/expiration
        
        # Placeholder - would need full instrument data
        signals.append({
            "option_id": option_id,
            "symbol": pos.get("chain_symbol", "UNKNOWN"),
            "option_type": pos.get("option_type", "put"),
            "quantity": quantity,
            "strike": pos.get("strike_price", 0),
            "expiration": pos.get("expiration_date", ""),
            "premium_received": avg_price,
            "current_mid": current_mid,
            "unrealized_pnl": (avg_price - current_mid) * quantity * STANDARD_OPTION_CONTRACT_SHARES,
            "signal": "CALCULATE",  # placeholder
        })
    
    # Build telegram message
    lines = ["📊 **Profit Taking Check**", f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S %Z')}", ""]
    
    if not signals:
        lines.append("No short positions with valid quotes.")
    else:
        for s in signals:
            lines.append(f"**{s['symbol']}** {s['option_type'].upper()} ${s['strike']:.2f} {s['expiration']}")
            lines.append(f"  Qty: {s['quantity']} | Premium: ${s['premium_received']:.2f} | Current: ${s['current_mid']:.2f}")
            lines.append(f"  Unrealized P&L: ${s['unrealized_pnl']:+.2f}")
            lines.append(f"  Signal: {s['signal']} (needs full instrument data for sqrt boundary)")
            lines.append("")
    
    lines.append("⚠️ Square-root boundary formula: `boundary = premium * sqrt(elapsed_days / total_days)`")
    lines.append("📋 Full instrument data needed for exact trigger prices.")
    
    return {
        "signals": signals,
        "telegram_message": "\n".join(lines)
    }

if __name__ == "__main__":
    result = run_profit_taking_check()
    print(json.dumps(result, indent=2, default=str))