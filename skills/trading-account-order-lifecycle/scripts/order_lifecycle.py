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
import shutil
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


# D-24 (Stage 0 of the order-placement enablement plan): the broker
# path no longer shells to the source repo's mcp_bridge.py — that file
# was never copied into this repo (D-08 note), so every lifecycle MCP
# call failed. call_mcp() below now routes through the wheel's own
# transport, scripts/robinhood_transport.py, which enforces the client
# allowlist and raises WriteToolDisabled for every order write tool.
# Notifications and the fill report no longer assume a Hermes profile
# or a Telegram bot: they are recorded as artifacts for delivery in
# Muse chat (see record_notification / schedule_fill_report).
REPO_ROOT = Path(__file__).resolve().parents[3]
PACIFIC = ZoneInfo("America/Los_Angeles")
NOTIFICATION_STATE_FILE = REPO_ROOT / ".state" / "robinhood_notification_dedup.json"
NOTIFICATION_OUTBOX_FILE = REPO_ROOT / ".state" / "robinhood_notification_outbox.jsonl"
FILL_REPORT_DIR = REPO_ROOT / ".state" / "fill_reports"
NOTIFICATION_STATE_VERSION = 1
NOTIFICATION_PROVIDER = "claude_chat"


class LifecycleError(RuntimeError):
    pass


@dataclass(frozen=True)
class Config:
    account_number: str
    notify_to: str
    max_attempts: int
    wait_seconds: int
    replacement_step: Decimal
    min_credit: Decimal | None



def _decimal(value: Any, field: str) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise LifecycleError(f"Invalid numeric field: {field}") from exc


def _transport_call(tool: str, arguments: dict[str, Any]) -> Any:
    """Single seam to the wheel transport (scripts/robinhood_transport.py).

    Imported lazily so this module imports cleanly on its own and unit
    tests can patch this one function. The transport returns the MCP
    structuredContent payload and raises WriteToolDisabled for write
    tools / ToolNotAllowlisted for non-allowlisted tools before any
    subprocess runs.
    """
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    from scripts.robinhood_transport import call_robinhood_tool
    return call_robinhood_tool(tool, arguments)


def call_mcp(tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Call a broker tool through the wheel transport (D-24).

    Read tools (get_option_orders, get_option_positions,
    review_option_order, ...) return the payload's ``data`` object,
    matching the shape the copied lifecycle was written against.
    WriteToolDisabled propagates unchanged — it is the signal that an
    order write was attempted through a hard-disabled path; other
    transport failures surface as LifecycleError.
    """
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    from scripts.robinhood_transport import RobinhoodTransportError, WriteToolDisabled
    try:
        structured = _transport_call(tool, arguments)
    except WriteToolDisabled:
        raise
    except RobinhoodTransportError as exc:
        raise LifecycleError(str(exc)) from exc
    if isinstance(structured, dict) and isinstance(structured.get("data"), dict):
        data = structured["data"]
    else:
        data = structured
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


def record_notification(destination: str, message: str, *, state_path: Path = NOTIFICATION_STATE_FILE,
                        outbox_path: Path = NOTIFICATION_OUTBOX_FILE) -> bool:
    """Record a notification for Muse chat delivery (D-24).

    There is no Hermes profile and no Telegram bot in this build, so
    nothing is "sent" here: the message is appended to the notification
    outbox artifact (JSONL under .state/), which the interactive Muse
    session surfaces in chat. Deduplication semantics are unchanged
    from the copied Telegram version — an identical
    (provider, destination, message) is recorded only once, and a
    failed record is not marked as recorded. Returns True when a new
    record was written.
    """
    notification_hash = _notification_hash(NOTIFICATION_PROVIDER, destination, message)
    state = _load_notification_state(state_path)
    if notification_hash in state["notifications"]:
        return False
    record = {
        "recorded_at": datetime.now(tz=ZoneInfo("UTC")).isoformat(),
        "provider": NOTIFICATION_PROVIDER,
        "destination": destination,
        "message": message,
        "message_hash": notification_hash,
        "delivery": "claude_chat_outbox: recorded, not sent — the interactive Muse session delivers it in chat",
    }
    try:
        outbox_path.parent.mkdir(parents=True, exist_ok=True)
        with outbox_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, sort_keys=True) + "\n")
    except OSError as exc:
        raise LifecycleError(f"Could not record notification to outbox: {outbox_path}") from exc
    state["notifications"][notification_hash] = {
        "provider": NOTIFICATION_PROVIDER,
        "destination": destination,
        "message_hash": notification_hash,
        "sent_at": record["recorded_at"],
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
    record_notification(destination, message)


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
    record_notification(destination, message)


def notify_status(destination: str, text: str) -> None:
    record_notification(destination, text)


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


REPO_ROOT = Path(__file__).resolve().parents[3]
DISPATCH_SCRIPT = REPO_ROOT / "scripts" / "fill_report_dispatch.py"


def create_dispatch_timer(run_at: datetime, account_number: str) -> dict[str, Any]:
    """Schedule the 13:05 dispatch in code: a one-shot systemd timer (D-31).

    Created at fill-confirmation time via systemd-run; nothing polls in
    between and the unit is collected after it fires. One timer per
    trade day: if that day's timer already exists, it is reused.
    Best-effort by design — any failure is reported in the returned
    dict (and recorded on the artifact), never raised, so a scheduling
    problem can never break order close-out. The artifact remains the
    durable request either way. Set WHEEL_DISPATCH_TIMER=off to skip.
    """
    if os.environ.get("WHEEL_DISPATCH_TIMER", "on").lower() in ("off", "0", "disabled"):
        return {"created": False, "reason": "disabled by WHEEL_DISPATCH_TIMER"}
    if run_at <= datetime.now(PACIFIC):
        return {"created": False, "reason": "run_at is not in the future"}
    unit = f"wheel-fill-dispatch-{run_at.strftime('%Y%m%d')}"
    try:
        show = subprocess.run(
            ["systemctl", "show", f"{unit}.timer", "-p", "LoadState", "--value"],
            capture_output=True, text=True, timeout=15)
        if show.stdout.strip() == "loaded":
            return {"created": False, "reason": "timer already scheduled for this trade day",
                    "unit": unit}
        python3 = shutil.which("python3") or "/usr/bin/python3"
        calendar = run_at.strftime("%Y-%m-%d %H:%M:%S") + " America/Los_Angeles"
        # The service runs under the system manager with a bare
        # environment; two things must be baked in or the dispatch
        # cannot reach the broker (both caught by D-31 smoke tests):
        # HOME — the Robinhood transport resolves its client from
        # Path.home(), which is /root for the system manager; and the
        # proxy variables — the MCP endpoint is reachable only through
        # this sandbox's HTTP proxy, which services do not inherit.
        # Values are passed through from this process's environment at
        # schedule time and never logged or written to the artifact.
        setenv = [f"--setenv=HOME={Path.home()}"]
        for name in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
                     "all_proxy", "ALL_PROXY", "no_proxy", "NO_PROXY"):
            if os.environ.get(name):
                setenv.append(f"--setenv={name}={os.environ[name]}")
        proc = subprocess.run(
            ["systemd-run", f"--on-calendar={calendar}", f"--unit={unit}", "--collect",
             *setenv,
             python3, str(DISPATCH_SCRIPT), "--account", str(account_number)],
            capture_output=True, text=True, timeout=30)
        if proc.returncode != 0:
            return {"created": False, "unit": unit,
                    "reason": (proc.stderr or proc.stdout or "systemd-run failed").strip()[-300:]}
        return {"created": True, "unit": unit, "fires_at": run_at.isoformat()}
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"created": False, "unit": unit, "reason": f"{type(exc).__name__}: {exc}"}


def schedule_fill_report(config: Config, order: dict[str, Any], now: datetime | None = None,
                         report_dir: Path = FILL_REPORT_DIR) -> bool:
    """Record the post-fill report request AND schedule its dispatch (D-31).

    The source scheduled a Hermes cron job for 13:05 PT. Here the same
    13:05 PT timing is computed, the fill-report request artifact is
    written under .state/fill_reports/, and create_dispatch_timer()
    schedules a one-shot systemd timer that runs
    scripts/fill_report_dispatch.py at that time — the code schedules
    the dispatch at fill time; no agent-created job and no polling.
    Returns True when the artifact was written.
    """
    now = now or datetime.now(PACIFIC)
    run_at = now.replace(hour=13, minute=5, second=0, microsecond=0)
    if now >= run_at:
        run_at += timedelta(days=1)
    symbol = str(order.get("chain_symbol", "option"))
    artifact = {
        "kind": "fill_report_request",
        "status": "pending",
        "delivery": "claude_chat",
        "deliver_after": run_at.isoformat(),
        "destination": config.notify_to,
        "instruction": (
            "Run the lifecycle fill-report workflow for the broker-verified fill from today. "
            "Execute the Python builder and renderer under "
            "skills/trading-account-order-lifecycle/scripts/ in this repo, verify non-empty "
            "artifacts, and deliver the generated report in Muse chat. "
            "Do not place, cancel, replace, or recursively schedule any order."
        ),
        "order_id": order.get("id"),
        "chain_symbol": symbol,
        "state": order.get("state"),
        "account_number": config.account_number,
        "dispatch_timer": create_dispatch_timer(run_at, config.account_number),
    }
    try:
        report_dir.mkdir(parents=True, exist_ok=True)
        name = f"fill-report-{symbol}-{order.get('id') or run_at.strftime('%Y%m%d')}.json"
        (report_dir / name).write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except OSError as exc:
        raise LifecycleError(f"Could not write fill-report artifact: {report_dir}") from exc
    return True


def close_out_fill(config: Config, order: dict[str, Any]) -> bool:
    """Single close-out path for a broker-verified fill (D-26).

    Every placement route — run()'s attempt loop, or place_single()
    below — must end a filled order here, so the fill notification and
    the 13:05 PT fill-report request are recorded exactly once and
    cannot be bypassed by choosing a lower-level entry point (the
    Stage 2 pilot placed via place() directly and the report was never
    scheduled). Returns True when the fill-report artifact was written.
    The wheel-fill-report-due hook (D-30) watches for these artifacts
    and wakes the 13:05 PT dispatch that delivers the report in chat —
    no standing cron, and no dispatch on days without fills.
    """
    notify_fill(order, config.notify_to)
    return schedule_fill_report(config, order)


def place_single(reviewed: dict[str, Any], config: Config, price: Decimal) -> dict[str, Any]:
    """Single-shot placement + reconcile + close-out (D-26).

    The Stage 2+ placement entry point: place once (no replacement,
    per the confirmed-payload rule), reconcile once against the
    broker, and — on a verified fill — close out through
    close_out_fill() so the 13:05 PT fill report is always scheduled.
    """
    order = place(reviewed, config, price)
    notify_placed(order, config.notify_to)
    order_id = str(order.get("id", ""))
    if not order_id:
        raise LifecycleError("Placement response did not contain an order identifier")
    current = status(config, order_id)
    state = str(current.get("state", "")).lower()
    report_recorded = close_out_fill(config, current) if state == "filled" else False
    return {"status": state, "order": current, "fill_report_scheduled": report_recorded}


def run(reviewed: dict[str, Any], config: Config, sleep_fn: Callable[[float], None] = time.sleep) -> dict[str, Any]:
    _guard("run")
    if config.max_attempts < 1:
        raise LifecycleError("max_attempts must be positive")
    if not reviewed.get("legs"):
        raise LifecycleError("Reviewed order must include legs")
    config = Config(config.account_number or str(reviewed.get("account_number", "")), config.notify_to, config.max_attempts, config.wait_seconds, config.replacement_step, config.min_credit)
    if not config.account_number:
        raise LifecycleError("An account number is required")
    current_review = dict(reviewed)
    price = _decimal(current_review["price"], "price")
    history: list[dict[str, Any]] = []
    for attempt in range(1, config.max_attempts + 1):
        order = place(current_review, config, price)
        notify_placed(order, config.notify_to)
        order_id = str(order.get("id", ""))
        if not order_id:
            raise LifecycleError("Placement response did not contain an order identifier")
        sleep_fn(config.wait_seconds)
        current = status(config, order_id)
        state = str(current.get("state", "")).lower()
        history.append({"attempt": attempt, "state": state, "price": str(price)})
        if state == "filled":
            report_recorded = close_out_fill(config, current)
            return {"status": "filled", "attempts": attempt, "history": history, "fill_report_scheduled": report_recorded}
        if state in {"partially_filled", "pending_cancelled", "unknown", "unconfirmed"}:
            notify_status(config.notify_to, f"⚠️ Order stopped: status {state}; no automatic replacement was made.")
            return {"status": state, "attempts": attempt, "history": history, "fill_report_scheduled": False}
        if state not in {"confirmed", "queued", "new"}:
            notify_status(config.notify_to, f"⚠️ Order stopped: broker status {state}; no automatic replacement was made.")
            return {"status": state, "attempts": attempt, "history": history, "fill_report_scheduled": False}
        cancelled = cancel(config, order_id)
        if str(cancelled.get("state", "")).lower() != "cancelled":
            raise LifecycleError(f"Replacement blocked; cancellation state is {cancelled.get('state')}")
        if attempt == config.max_attempts:
            notify_status(config.notify_to, "⏹️ Order stopped after the configured replacement limit; no fill.")
            return {"status": "attempt_limit", "attempts": attempt, "history": history, "fill_report_scheduled": False}
        quote_data = call_mcp("get_option_quotes", {"instrument_ids": [current_review["legs"][0]["option_id"]]})
        quote = (quote_data.get("results") or [{}])[0].get("quote", {})
        price = replacement_price(current_review, quote, config.replacement_step)
        current_review["price"] = format(price, "f")
    raise LifecycleError("Lifecycle exited without a terminal result")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reviewed-order", required=True, type=Path)
    parser.add_argument("--notify-to", "--telegram-to", dest="notify_to", required=True,
                        help="Notification destination label for Muse chat delivery "
                             "(--telegram-to is a legacy alias from the copied source)")
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
        config = Config(args.account_number, args.notify_to, args.max_attempts, args.wait_seconds, Decimal(args.replacement_step), Decimal(args.min_credit) if args.min_credit else None)
        result = run(reviewed, config)
        print(json.dumps(result, indent=2))
        return 0
    except (OSError, json.JSONDecodeError, LifecycleError, ValueError, OrderLifecycleDisabled) as exc:
        print(f"ORDER LIFECYCLE FAILED: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
