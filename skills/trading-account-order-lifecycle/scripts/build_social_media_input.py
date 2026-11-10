#!/usr/bin/env python3
"""Collect broker data through Hermes MCP and build renderer input.

The default path calls the read-only Robinhood MCP tools through the existing
Hermes MCP bridge. ``--snapshot`` is provided for deterministic tests and
replays; it must be a previously captured broker response snapshot.
"""
from __future__ import annotations

import argparse
import json
import subprocess
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo
from typing import Any

BRIDGE = Path(__file__).resolve().parents[2] / "company_valuation_score/scripts/mcp_bridge.py"

class InputBuildError(ValueError):
    pass

def _dec(value: Any, field: str) -> Decimal:
    try:
        return Decimal(str(value))
    except Exception as exc:
        raise InputBuildError(f"Invalid numeric field: {field}") from exc

def _mcp_call(tool: str, arguments: dict[str, Any]) -> Any:
    proc = subprocess.run(
        ["python3", str(BRIDGE), "robinhood-trading", tool],
        input=json.dumps(arguments), text=True, capture_output=True, check=False,
    )
    lines = [line for line in proc.stdout.splitlines() if line.strip()]
    if proc.returncode != 0 or not lines:
        raise InputBuildError(f"Robinhood MCP call failed: {tool}: {proc.stderr[-500:]}")
    try:
        envelope = json.loads(lines[-1])
        if isinstance(envelope.get("structuredContent"), dict):
            return envelope["structuredContent"].get("data", envelope["structuredContent"])
        result = envelope.get("result")
        if isinstance(result, str):
            parsed = json.loads(result)
            return parsed.get("data", parsed)
        return envelope.get("data", envelope)
    except (json.JSONDecodeError, TypeError) as exc:
        raise InputBuildError(f"Invalid Robinhood MCP response: {tool}") from exc

def _collect_snapshot(as_of: date, account_number: str | None) -> dict[str, Any]:
    accounts = _mcp_call("get_accounts", {})
    account_rows = accounts.get("accounts", [])
    if account_number:
        selected = next((a for a in account_rows if a.get("account_number") == account_number), None)
    else:
        selected = next((a for a in account_rows if a.get("agentic_allowed") is True), None)
    if not selected:
        raise InputBuildError("No usable Robinhood agentic account was found")
    account = selected["account_number"]
    start = date(as_of.year, 1, 1).isoformat()
    return {
        "as_of": as_of.isoformat(),
        "account": {"account_number": account},
        "orders": _mcp_call("get_option_orders", {"account_number": account, "created_at_gte": start}),
        "positions": _mcp_call("get_option_positions", {"account_number": account, "nonzero": True}),
        "portfolio": _mcp_call("get_portfolio", {"account_number": account}),
        "realized_pnl": _mcp_call("get_realized_pnl", {"account_number": account, "start_date": start, "end_date": as_of.isoformat(), "asset_classes": ["option"]}),
    }

def _rows(snapshot: dict[str, Any], key: str) -> list[dict[str, Any]]:
    value = snapshot.get(key, {})
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        for candidate in (key, "orders", "positions", "results"):
            if isinstance(value.get(candidate), list):
                return value[candidate]
    raise InputBuildError(f"Snapshot is missing broker rows: {key}")

def _filled_legs(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    filled: list[dict[str, Any]] = []
    for order in _rows(snapshot, "orders"):
        if str(order.get("state", "")).lower() != "filled":
            continue
        if _dec(order.get("processed_quantity", 0), "processed_quantity") <= 0:
            continue
        for leg in order.get("legs", []):
            executions = leg.get("executions") or []
            if not executions:
                continue
            for execution in executions:
                quantity = _dec(execution.get("quantity", 0), "execution.quantity")
                if quantity <= 0:
                    continue
                item = dict(leg)
                item.update({"order": order, "execution": execution, "quantity": quantity})
                filled.append(item)
    if not filled:
        raise InputBuildError("No broker-verified filled option executions were found")
    return filled

def _trade_date(item: dict[str, Any]) -> date:
    raw = item["execution"].get("trade_date") or item["order"].get("created_at", "")[:10]
    return date.fromisoformat(raw)

def _group_key(item: dict[str, Any]) -> tuple[str, str]:
    return (str(item["order"].get("chain_symbol", "")).upper(), str(item.get("strike_price", "")))

def _price(item: dict[str, Any]) -> Decimal:
    return _dec(item["execution"].get("price"), "execution.price")

def _premium(item: dict[str, Any]) -> Decimal:
    return _price(item) * item["quantity"] * _dec(item.get("trade_value_multiplier", 100), "trade_value_multiplier")

def _item_fees(item: dict[str, Any]) -> Decimal:
    value = item.get("execution", {}).get("fees")
    if value is None:
        value = item.get("order", {}).get("fees", 0)
    if isinstance(value, dict):
        value = value.get("amount", value.get("value", 0))
    if isinstance(value, list):
        return sum((_dec(v.get("amount", v.get("value", 0)), "fee") if isinstance(v, dict) else _dec(v, "fee") for v in value), Decimal(0))
    return _dec(value or 0, "fee")

def _quantity_text(value: Decimal) -> str:
    return str(int(value)) if value == value.to_integral_value() else format(value.normalize(), "f")


def _actions(items: list[dict[str, Any]]) -> str:
    grouped: dict[str, list[tuple[Decimal, Decimal]]] = defaultdict(list)
    for item in items:
        action = "Sell" if item.get("position_effect") == "open" and item.get("side") == "sell" else "BTC"
        grouped[action].append((item["quantity"], _price(item)))
    parts = []
    for action in ("Sell", "BTC"):
        if action not in grouped:
            continue
        quantity = sum((q for q, _ in grouped[action]), Decimal(0))
        prices = [p for _, p in grouped[action]]
        low, high = min(prices), max(prices)
        price_text = f"${low:.2f}" if low == high else f"${low:.2f}–${high:.2f}"
        parts.append(f"{action} {_quantity_text(quantity)} @ {price_text}")
    return " · ".join(parts)

def build_payload(snapshot: dict[str, Any], valuation_history_path: Path) -> dict[str, Any]:
    as_of = date.fromisoformat(snapshot["as_of"])
    filled = _filled_legs(snapshot)
    today = [item for item in filled if _trade_date(item) == as_of]
    if not today:
        raise InputBuildError(f"No filled option executions on {as_of.isoformat()}")
    positions = {str(p.get("option_id")): p for p in _rows(snapshot, "positions")}
    by_today: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    by_all: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for item in filled:
        by_all[_group_key(item)].append(item)
        if item in today:
            by_today[_group_key(item)].append(item)
    today_positions = []
    for key, items in by_today.items():
        first = items[0]
        open_qty = Decimal(0)
        for item in by_all[key]:
            signed = item["quantity"] if item.get("position_effect") == "open" else -item["quantity"]
            open_qty += signed
        pos = positions.get(str(first.get("option_id")), {})
        if pos:
            open_qty = _dec(pos.get("quantity"), "position.quantity")
        expiration = date.fromisoformat(str(first["expiration_date"]))
        strike = _dec(first["strike_price"], "strike_price")
        opens = [i for i in by_all[key] if i.get("position_effect") == "open" and i.get("side") == "sell"]
        closes = [i for i in by_all[key] if i.get("position_effect") == "close" and i.get("side") == "buy"]
        opening_premium = sum((_premium(i) for i in opens), Decimal(0))
        closing_debit = sum((_premium(i) for i in closes), Decimal(0))
        fees = sum((_item_fees(i) for i in by_today[key]), Decimal(0))
        realized_gross = opening_premium - closing_debit
        realized_net = realized_gross - fees
        closed_qty = sum((i["quantity"] for i in closes), Decimal(0))
        profit_taking = bool(closes and closed_qty > 0 and realized_gross > 0)
        today_positions.append({
            "ticker": key[0],
            "contract": f"${strike:.2f}P",
            "expiration": expiration.strftime("%m/%d"),
            "dte": (expiration - as_of).days,
            "actions": _actions(items),
            "event_type": "profit_taking" if profit_taking else "opening_or_adjustment",
            "net_premium": float(realized_net if profit_taking else sum((_premium(i) * (1 if i.get("side") == "sell" else -1) for i in items), Decimal(0))),
            "collateral": float(strike * Decimal(100) * open_qty),
            "collateral_added": float(strike * Decimal(100) * sum((i["quantity"] for i in items if i.get("position_effect") == "open" and i.get("side") == "sell"), Decimal(0))),
            "collateral_released": float(strike * Decimal(100) * closed_qty),
            "open_contracts": int(open_qty),
            "original_premium": float(opening_premium),
            "closing_debit": float(closing_debit),
            "realized_profit_gross": float(realized_gross),
            "realized_profit_net": float(realized_net),
            "fees": float(fees),
        })
    portfolio = snapshot.get("portfolio", {})
    if isinstance(portfolio.get("data"), dict): portfolio = portfolio["data"]
    realized = snapshot.get("realized_pnl", {})
    if isinstance(realized.get("data"), dict): realized = realized["data"]
    total_groups = len(by_all)
    open_groups = sum(1 for key in by_all if sum((i["quantity"] if i.get("position_effect") == "open" else -i["quantity"] for i in by_all[key]), Decimal(0)) > 0)
    profitable_closed = sum(1 for key, items in by_all.items() if sum((i["quantity"] if i.get("position_effect") == "open" else -i["quantity"] for i in items), Decimal(0)) <= 0 and sum((_premium(i) * (1 if i.get("side") == "sell" else -1) for i in items), Decimal(0)) > 0)
    realized_total = realized.get("total_returns", realized.get("total", realized.get("realized_profit", 0)))
    if isinstance(realized_total, dict): realized_total = realized_total.get("amount", 0)
    cash = _dec(portfolio.get("cash", 0), "portfolio.cash")
    open_premiums = sum((_dec(p.get("average_price", 0), "position.average_price").copy_abs() * _dec(p.get("quantity", 0), "position.quantity") for p in positions.values()), Decimal(0))
    net_ytd = sum((_premium(i) * (1 if i.get("side") == "sell" else -1) for i in filled), Decimal(0))
    expiration = min(p["expiration"] for p in today_positions)
    return {"today": {"expiration": expiration, "positions": today_positions}, "ytd": {"successful_aggregated_positions": total_groups, "open_positions": open_groups, "closed_profitable_positions": profitable_closed, "closed_positions": total_groups - open_groups, "assignments": 0, "realized_profit": float(_dec(realized_total, "realized_profit")), "cash_position": float(cash), "premiums_on_open_positions": float(open_premiums), "net_premium_collected": float(net_ytd), "total_cash": float(cash)}, "valuation_history_path": str(valuation_history_path)}

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--valuation-history", required=True, type=Path)
    parser.add_argument("--snapshot", type=Path)
    parser.add_argument("--as-of", type=date.fromisoformat, default=date.today())
    parser.add_argument("--account-number")
    args = parser.parse_args()
    snapshot = json.loads(args.snapshot.read_text(encoding="utf-8")) if args.snapshot else _collect_snapshot(args.as_of, args.account_number)
    payload = build_payload(snapshot, args.valuation_history)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(args.output)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
