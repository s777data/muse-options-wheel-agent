#!/usr/bin/env python3
"""Generate a privacy-safe wheel social-media Telegram markdown message.

All trade, account, performance, and valuation values are supplied at runtime.
The ``--input`` JSON must contain ``today`` and ``ytd`` objects. ``today`` must
contain ``expiration`` and a ``positions`` array. Each position must contain
``ticker``, ``contract``, ``dte``, ``actions``, ``net_premium``, ``collateral``,
and ``open_contracts``. ``ytd`` must contain the numeric fields used by
``render_message``. Pass valuation records with ``--valuation-history`` or
provide ``valuation_history_path`` in the input JSON.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


class SocialMessageError(ValueError):
    """Raised when required source data is missing or invalid."""


def _money(value: float | int) -> str:
    return f"${value:,.0f}" if float(value).is_integer() else f"${value:,.2f}"


def _signed_money(value: float | int) -> str:
    sign = "+" if float(value) >= 0 else "-"
    return f"{sign}{_money(abs(float(value)))}"


def _pct(value: float) -> str:
    return f"{value:.2f}%"


def _success_pct(value: float) -> str:
    return f"{value:.0f}%" if value.is_integer() else f"{value:.2f}%"


def _grade(value: str) -> str:
    return value.replace("-", "−")


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SocialMessageError(f"Missing JSON input: {path}") from exc
    except json.JSONDecodeError as exc:
        raise SocialMessageError(f"Invalid JSON input: {path}: {exc}") from exc


def _latest_valuation_records(path: Path) -> dict[str, dict[str, Any]]:
    records = _load_json(path)
    if not isinstance(records, list):
        raise SocialMessageError("valuation_history.json must contain a top-level array")

    latest: dict[str, dict[str, Any]] = {}
    for record in records:
        ticker = str(record.get("ticker", "")).strip().upper()
        if not ticker:
            continue
        prior = latest.get(ticker)
        if prior is None or str(record.get("data_cutoff_at", "")) > str(prior.get("data_cutoff_at", "")):
            latest[ticker] = record
    return latest


def _valuation_labels(record: dict[str, Any], ticker: str) -> str:
    try:
        valuation = record["valuation"]
        value = valuation["grade"]
        quality = valuation["business_quality"]
        value_grade = _grade(str(value["grade"]))
        value_score = float(value["score"])
        quality_grade = _grade(str(quality["grade"]))
        quality_score = float(quality["score"])
    except (KeyError, TypeError, ValueError) as exc:
        raise SocialMessageError(f"Incomplete valuation record for {ticker}") from exc
    return (
        f"🏷️ Value {value_grade} ({value_score:.2f}) · "
        f"🏢 Quality {quality_grade} ({quality_score:.2f})"
    )


def _validate_number(data: dict[str, Any], key: str) -> float:
    value = data.get(key)
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise SocialMessageError(f"Missing or invalid numeric field: {key}")
    return float(value)


def render_message(payload: dict[str, Any], valuation_history_path: Path | None = None) -> str:
    try:
        today = payload["today"]
        ytd = payload["ytd"]
        positions = today["positions"]
    except KeyError as exc:
        raise SocialMessageError(f"Missing required input section: {exc.args[0]}") from exc
    if not positions:
        raise SocialMessageError("today.positions must not be empty")

    valuation_path = valuation_history_path or Path(payload["valuation_history_path"])
    valuations = _latest_valuation_records(valuation_path)

    total_collateral = 0.0
    total_today_premium = 0.0
    total_profit_taken = 0.0
    total_collateral_released = 0.0
    sections: list[str] = [f"📊 **Today’s Wheel | {today['expiration']}**", ""]
    for position in positions:
        ticker = str(position.get("ticker", "")).strip().upper()
        if not ticker or ticker not in valuations:
            raise SocialMessageError(f"No matching valuation record for {ticker or '<blank ticker>'}")
        for field in ("contract", "dte", "actions", "net_premium", "collateral", "open_contracts"):
            if field not in position:
                raise SocialMessageError(f"Missing today position field: {field}")
        premium = _validate_number(position, "net_premium")
        collateral = _validate_number(position, "collateral")
        score_labels = _valuation_labels(valuations[ticker], ticker)
        if position.get("event_type") == "profit_taking":
            original = _validate_number(position, "original_premium")
            closing = _validate_number(position, "closing_debit")
            gross = _validate_number(position, "realized_profit_gross")
            net = _validate_number(position, "realized_profit_net")
            fees = _validate_number(position, "fees")
            released = _validate_number(position, "collateral_released")
            total_profit_taken += net
            total_collateral_released += released
            fee_text = f" after {_money(fees)} fees" if fees > 0 else ""
            sections.extend(
                [
                    f"🔹 **{ticker} {position['contract']} · {position['dte']} DTE**",
                    "✅ **Profit taken**",
                    str(position["actions"]),
                    f"💵 Original premium: **{_signed_money(original)}**",
                    f"💳 Closing debit: **-{_money(closing)}**",
                    f"📈 Realized profit: **{_signed_money(gross)} gross**",
                    f"💰 Net profit: **{_signed_money(net)}{fee_text}**",
                    f"🔓 Collateral released: **{_money(released)}** · {position['open_contracts']} open",
                    score_labels,
                    "",
                ]
            )
        else:
            new_collateral = float(position.get("collateral_added", collateral))
            total_today_premium += premium
            total_collateral += new_collateral
            sections.extend(
                [
                    f"🔹 **{ticker} {position['contract']} · {position['dte']} DTE**",
                    str(position["actions"]),
                    f"💵 {_signed_money(premium)} net · 🔒 {_money(new_collateral)} new collateral · {position['open_contracts']} open",
                    score_labels,
                    "",
                ]
            )

    if total_collateral <= 0 and total_today_premium > 0:
        raise SocialMessageError("Total collateral must be positive for opening or adjustment activity")
    today_premium_pct = total_today_premium / total_collateral * 100 if total_collateral else 0.0

    total_positions = _validate_number(ytd, "successful_aggregated_positions")
    open_positions = _validate_number(ytd, "open_positions")
    closed_profitable = _validate_number(ytd, "closed_profitable_positions")
    closed_positions = _validate_number(ytd, "closed_positions")
    assignments = _validate_number(ytd, "assignments")
    denominator = total_positions - open_positions
    if denominator <= 0:
        raise SocialMessageError("YTD success-rate denominator must be positive")
    success_pct = closed_profitable / denominator * 100

    realized_profit = _validate_number(ytd, "realized_profit")
    cash_position = _validate_number(ytd, "cash_position")
    open_premiums = _validate_number(ytd, "premiums_on_open_positions")
    realized_denominator = cash_position - open_premiums
    if realized_denominator <= 0:
        raise SocialMessageError("YTD realized-percentage denominator must be positive")
    realized_pct = realized_profit / realized_denominator * 100

    net_premium = _validate_number(ytd, "net_premium_collected")
    total_cash = _validate_number(ytd, "total_cash")
    if total_cash <= 0:
        raise SocialMessageError("Total cash must be positive")
    net_premium_pct = net_premium / total_cash * 100

    summary_lines: list[str] = []
    if total_today_premium:
        summary_lines.extend([
            f"💵 **New premium: {_signed_money(total_today_premium)} ({_pct(today_premium_pct)} of new collateral)**",
            f"🔒 **New collateral: {_money(total_collateral)}**",
        ])
    if total_profit_taken:
        summary_lines.append(f"📈 **Profit taken: {_signed_money(total_profit_taken)}**")
        summary_lines.append(f"🔓 **Collateral released: {_money(total_collateral_released)}**")
    if summary_lines:
        sections.extend(summary_lines + [""])
    sections.extend(
        [
            "📈 **YTD Wheel**",
            "",
            f"✅ Closed wins: **{int(closed_profitable)}/{int(denominator)} · {_success_pct(success_pct)}**",
            f"📂 **{int(total_positions)} positions · {int(open_positions)} open · {int(closed_positions)} closed · {int(assignments)} assignments**",
            f"💰 Realized: **{_signed_money(realized_profit)} ({_pct(realized_pct)})**",
            f"💵 Net premiums: **{_money(net_premium)} ({_pct(net_premium_pct)}) before fees**",
            "",
        ]
    )
    return "\n".join(sections).rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path, help="Normalized wheel/social metrics JSON")
    parser.add_argument("--valuation-history", type=Path, help="Override valuation_history_path from input JSON")
    parser.add_argument("--output", required=True, type=Path, help="Output markdown path")
    args = parser.parse_args()

    payload = _load_json(args.input)
    if not isinstance(payload, dict):
        raise SystemExit("Input JSON must contain an object")
    message = render_message(payload, args.valuation_history)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(message, encoding="utf-8")
    print(str(args.output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
