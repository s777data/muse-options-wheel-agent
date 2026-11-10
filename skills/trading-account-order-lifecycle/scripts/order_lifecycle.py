#!/usr/bin/env python3
"""Run the post-confirmation Robinhood option-order lifecycle.

This script is intentionally bounded. The initial reviewed order and user
confirmation authorize the initial submission plus at most ``--max-attempts``
controlled replacements. It never retries an unknown, partial, rejected, or
alerted order. Runtime values are supplied in the reviewed-order JSON; no
account, contract, price, or performance values are hardcoded.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_FLOOR, ROUND_CEILING
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo


# =====================================================================
# muse-options-wheel-agent HARD DISABLE (DEVIATIONS.md D-08)
# This module is a verbatim copy of the source order_lifecycle.py with
# ONLY this guard added. place(), cancel() and run() raise
# OrderLifecycleDisabled unless BOTH user-controlled conditions hold:
# env WHEEL_ORDER_LIFECYCLE_ENABLE=1 AND the flag file named by env
# WHEEL_ORDER_LIFECYCLE_FLAG exists. Both default to absent. The local
# Robinhood client also blocks all write tools (defense in depth).
# =====================================================================

class OrderLifecycleDisabled(RuntimeError):
    """Raised when a placement/cancel/run entry point is invoked while
    the order lifecycle is hard-disabled (see D-08)."""


def _lifecycle_enabled() -> bool:
    flag = os.environ.get("WHEEL_ORDER_LIFECYCLE_FLAG", "")
    return (os.environ.get("WHEEL_ORDER_LIFECYCLE_ENABLE") == "1"
            and bool(flag) and Path(flag).exists())


def _guard(action: str) -> None:
    if not _lifecycle_enabled():
        raise OrderLifecycleDisabled(
            f"order_lifecycle_disabled: {action} requires "
            "WHEEL_ORDER_LIFECYCLE_ENABLE=1 and the user-controlled "
            "flag file named by WHEEL_ORDER_LIFECYCLE_FLAG "
            "(both default absent)")


BRIDGE = Path(__file__).resolve().parents[2] / "company_valuation_score/scripts/mcp_bridge.py"
REPO_ROOT = Path(__file__).resolve().parents[3]
PROFILE = "options-wheel-agent"
PACIFIC = ZoneInfo("America/Los_Angeles")
NOTIFICATION_STATE_FILE = REPO_ROOT / ".state" / "robinhood_notification_dedup.json"
NOTIFICATION_STATE_VERSION = 1


class LifecycleError(RuntimeError):
    pass


@dataclass(frozen=True)
class Config:
    account_number: str
    telegram_to: str
    max_attempts: int
    wait_seconds: int
    replacement_step: Decimal
    min_credit: Decimal | None



def _decimal(value: Any, field: str) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise LifecycleError(f"Invalid numeric field: {field}") from exc


def _run(command: list[str], *, env: dict[str, str] | None = None, input_text: str | None = None) -> str:
    proc = subprocess.run(command, input=input_text, text=True, capture_output=True, env=env, check=False)
    if proc.returncode != 0:
        raise LifecycleError(f"Command failed ({proc.returncode}): {' '.join(command)}: {proc.stderr[-500:]}")
    return proc.stdout.strip()


def call_mcp(tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    output = _run(["python3", str(BRIDGE), "robinhood-trading", tool], input_text=json.dumps(arguments))
    try:
        envelope = json.loads(output.splitlines()[-1])
    except (IndexError, json.JSONDecodeError) as exc:
        raise LifecycleError(f"Invalid MCP response for {tool}") from exc
    if envelope.get("error"):
        raise LifecycleError(f"{tool}: {envelope['error']}")
    if isinstance(envelope.get("structuredContent"), dict):
        data = envelope["structuredContent"].get("data", envelope["structuredContent"])
    elif isinstance(envelope.get("result"), str):
        parsed = json.loads(envelope["result"])
        data = parsed.get("data", parsed)
    else:
        data = envelope.get("data", envelope)
    if not isinstance(data, dict):
        raise LifecycleError(f"Unexpected MCP data for {tool}")
    return data


def order_row(data: dict[str, Any]) -> dict[str, Any]:
    orders = data.get("orders") or []
    if not orders:
        raise LifecycleError("Broker returned no order")
    return orders[0]


def _notification_hash(provider: str, destination: str, message: str) -> str:
    canonical = json.dumps(
        {"provider": provider, "destination": destination, "message": message},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _load_notification_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"version": NOTIFICATION_STATE_VERSION, "notifications": {}}
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LifecycleError(f"Invalid notification deduplication state: {path}") from exc
    if not isinstance(state, dict) or state.get("version") != NOTIFICATION_STATE_VERSION:
        raise LifecycleError(f"Unsupported notification deduplication state: {path}")
    notifications = state.get("notifications")
    if not isinstance(notifications, dict):
        raise LifecycleError(f"Invalid notification deduplication entries: {path}")
    return state


def _write_notification_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(state, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except OSError as exc:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise LifecycleError(f"Could not persist notification deduplication state: {path}") from exc


def send_telegram(destination: str, message: str, *, state_path: Path = NOTIFICATION_STATE_FILE) -> bool:
    target = destination if destination.startswith("telegram:") else f"telegram:{destination}"
    provider = "telegram"
    destination_value = target.removeprefix("telegram:")
    notification_hash = _notification_hash(provider, destination_value, message)
    state = _load_notification_state(state_path)
    if notification_hash in state["notifications"]:
        return False
    env = dict(os.environ)
    env["HERMES_PROFILE"] = PROFILE
    _run(["hermes", "send", "--to", target, message], env=env)
    state["notifications"][notification_hash] = {
        "provider": provider,
        "destination": destination_value,
        "message_hash": notification_hash,
        "sent_at": datetime.now(tz=ZoneInfo("UTC")).isoformat(),
    }
    _write_notification_state(state_path, state)
    return True


def notify_placed(order: dict[str, Any], destination: str) -> None:
    leg = (order.get("legs") or [{}])[0]
    message = (
        f"📤 Order placed\n"
        f"{order.get('chain_symbol', 'N/A')} {leg.get('option_type', 'option')} "
        f"${leg.get('strike_price', 'N/A')} exp {leg.get('expiration_date', 'N/A')}\n"
        f"Action: {'SELL TO OPEN' if leg.get('side') == 'sell' else 'BUY TO CLOSE'}\n"
        f"Quantity: {order.get('quantity', 'N/A')}\n"
        f"Limit: ${order.get('price', 'N/A')}\n"
        f"Status: {order.get('state', 'unknown')}"
    )
    send_telegram(destination, message)


def notify_fill(order: dict[str, Any], destination: str) -> None:
    leg = (order.get("legs") or [{}])[0]
    executions = leg.get("executions") or []
    execution = executions[-1] if executions else {}
    message = (
        f"✅ Order filled — broker verified\n"
        f"{order.get('chain_symbol', 'N/A')} {leg.get('option_type', 'option')} "
        f"${leg.get('strike_price', 'N/A')} exp {leg.get('expiration_date', 'N/A')}\n"
        f"Action: {'SELL TO OPEN' if leg.get('side') == 'sell' else 'BUY TO CLOSE'}\n"
        f"Filled quantity: {execution.get('quantity', order.get('processed_quantity', 'N/A'))}\n"
        f"Average fill price: ${execution.get('price', 'N/A')}\n"
        f"Status: {order.get('state', 'unknown')}\n"
        f"Remaining quantity: {order.get('pending_quantity', '0')}\n"
        f"Timestamp: {execution.get('timestamp', order.get('updated_at', 'N/A'))}"
    )
    send_telegram(destination, message)


def notify_status(destination: str, text: str) -> None:
    send_telegram(destination, text)


def _round_toward(value: Decimal, tick: Decimal, direction: str) -> Decimal:
    units = value / tick
    rounding = ROUND_FLOOR if direction == "down" else ROUND_CEILING
    return units.to_integral_value(rounding=rounding) * tick


def replacement_price(reviewed: dict[str, Any], quote: dict[str, Any], step: Decimal) -> Decimal:
    prior = _decimal(reviewed["price"], "reviewed.price")
    bid = _decimal(quote["bid_price"], "quote.bid_price")
    ask = _decimal(quote["ask_price"], "quote.ask_price")
    tick = _decimal(reviewed.get("tick_size", "0.01"), "tick_size")
    if bid < 0 or ask < bid or ask <= 0 or tick <= 0:
        raise LifecycleError("Invalid replacement quote")
    side = str((reviewed.get("legs") or [{}])[0].get("side", "")).lower()
    if side == "sell":
        raw = prior - step
        candidate = _round_toward(raw, tick, "down")
        if candidate < bid or candidate > ask:
            candidate = _round_toward(ask - step, tick, "down")
    else:
        raw = prior + step
        candidate = _round_toward(raw, tick, "up")
        if candidate < bid or candidate > ask:
            candidate = _round_toward(bid + step, tick, "up")
    if candidate < bid or candidate > ask:
        raise LifecycleError("Replacement price is outside the current bid/ask")
    minimum = reviewed.get("min_credit")
    if side == "sell" and minimum is not None and candidate < _decimal(minimum, "min_credit"):
        raise LifecycleError("Replacement price reached the configured minimum credit")
    return candidate.quantize(tick)


def place(reviewed: dict[str, Any], config: Config, price: Decimal) -> dict[str, Any]:
    _guard("place")
    payload = dict(reviewed)
    payload["account_number"] = config.account_number
    payload["price"] = format(price, "f")
    for field in ("ref_id", "max_attempts", "tick_size", "min_credit", "chain_symbol", "underlying_type"):
        payload.pop(field, None)
    result = call_mcp("place_option_order", payload)
    return result.get("order", result)


def status(config: Config, order_id: str) -> dict[str, Any]:
    return order_row(call_mcp("get_option_orders", {"account_number": config.account_number, "order_id": order_id}))


def cancel(config: Config, order_id: str) -> dict[str, Any]:
    _guard("cancel")
    call_mcp("cancel_option_order", {"account_number": config.account_number, "order_id": order_id})
    for _ in range(10):
        current = status(config, order_id)
        if current.get("state") != "pending_cancelled":
            return current
        time.sleep(1)
    raise LifecycleError("Cancellation remained pending after verification attempts")


def setup_cron(config: Config, order: dict[str, Any], now: datetime | None = None) -> bool:
    now = now or datetime.now(PACIFIC)
    run_at = now.replace(hour=13, minute=5, second=0, microsecond=0)
    if now >= run_at:
        run_at += timedelta(days=1)
    prompt = (
        "Run the lifecycle fill-report workflow for the broker-verified fill from today. "
        "Execute the Python builder and renderer under "
        "/home/s777data/.hermes/profiles/options-wheel-agent/skills/"
        "trading-account-order-lifecycle/scripts/, verify non-empty artifacts, and deliver "
        "the generated Telegram message through the configured options-wheel-agent bot. "
        "Do not place, cancel, replace, or recursively schedule any order."
    )
    env = dict(os.environ)
    env["HERMES_PROFILE"] = PROFILE
    _run([
        "hermes", "cron", "create",
        run_at.isoformat(),
        prompt,
        "--name", f"order-fill-report-{order.get('chain_symbol', 'option')}",
        "--deliver", config.telegram_to if config.telegram_to.startswith("telegram:") else f"telegram:{config.telegram_to}",
    ], env=env)
    return True


def run(reviewed: dict[str, Any], config: Config, sleep_fn: Callable[[float], None] = time.sleep) -> dict[str, Any]:
    _guard("run")
    if config.max_attempts < 1:
        raise LifecycleError("max_attempts must be positive")
    if not reviewed.get("legs"):
        raise LifecycleError("Reviewed order must include legs")
    config = Config(config.account_number or str(reviewed.get("account_number", "")), config.telegram_to, config.max_attempts, config.wait_seconds, config.replacement_step, config.min_credit)
    if not config.account_number:
        raise LifecycleError("An account number is required")
    current_review = dict(reviewed)
    price = _decimal(current_review["price"], "price")
    history: list[dict[str, Any]] = []
    for attempt in range(1, config.max_attempts + 1):
        order = place(current_review, config, price)
        notify_placed(order, config.telegram_to)
        order_id = str(order.get("id", ""))
        if not order_id:
            raise LifecycleError("Placement response did not contain an order identifier")
        sleep_fn(config.wait_seconds)
        current = status(config, order_id)
        state = str(current.get("state", "")).lower()
        history.append({"attempt": attempt, "state": state, "price": str(price)})
        if state == "filled":
            notify_fill(current, config.telegram_to)
            cron_created = setup_cron(config, current)
            return {"status": "filled", "attempts": attempt, "history": history, "cron_created": cron_created}
        if state in {"partially_filled", "pending_cancelled", "unknown", "unconfirmed"}:
            notify_status(config.telegram_to, f"⚠️ Order stopped: status {state}; no automatic replacement was made.")
            return {"status": state, "attempts": attempt, "history": history, "cron_created": False}
        if state not in {"confirmed", "queued", "new"}:
            notify_status(config.telegram_to, f"⚠️ Order stopped: broker status {state}; no automatic replacement was made.")
            return {"status": state, "attempts": attempt, "history": history, "cron_created": False}
        cancelled = cancel(config, order_id)
        if str(cancelled.get("state", "")).lower() != "cancelled":
            raise LifecycleError(f"Replacement blocked; cancellation state is {cancelled.get('state')}")
        if attempt == config.max_attempts:
            notify_status(config.telegram_to, "⏹️ Order stopped after the configured replacement limit; no fill.")
            return {"status": "attempt_limit", "attempts": attempt, "history": history, "cron_created": False}
        quote_data = call_mcp("get_option_quotes", {"instrument_ids": [current_review["legs"][0]["option_id"]]})
        quote = (quote_data.get("results") or [{}])[0].get("quote", {})
        price = replacement_price(current_review, quote, config.replacement_step)
        current_review["price"] = format(price, "f")
    raise LifecycleError("Lifecycle exited without a terminal result")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reviewed-order", required=True, type=Path)
    parser.add_argument("--telegram-to", required=True, help="Configured destination, e.g. 8917262950")
    parser.add_argument("--account-number", required=True)
    parser.add_argument("--max-attempts", type=int, default=3)
    parser.add_argument("--wait-seconds", type=int, default=60)
    parser.add_argument("--replacement-step", default="0.05")
    parser.add_argument("--min-credit")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        reviewed = json.loads(args.reviewed_order.read_text(encoding="utf-8"))
        config = Config(args.account_number, args.telegram_to, args.max_attempts, args.wait_seconds, Decimal(args.replacement_step), Decimal(args.min_credit) if args.min_credit else None)
        result = run(reviewed, config)
        print(json.dumps(result, indent=2))
        return 0
    except (OSError, json.JSONDecodeError, LifecycleError, ValueError, OrderLifecycleDisabled) as exc:
        print(f"ORDER LIFECYCLE FAILED: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
