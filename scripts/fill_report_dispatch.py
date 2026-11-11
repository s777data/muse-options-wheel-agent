#!/usr/bin/env python3
"""Code-side 1:05 PM fill-report dispatch (D-31).

Scheduled by code, not by an agent: when a fill is confirmed,
``schedule_fill_report()`` in the order lifecycle writes the request
artifact AND creates a one-shot systemd timer for 13:05 PT that runs
this script. Nothing polls in between; the timer fires once and is
gone (``--collect``).

What this script does at 13:05, all in code:
  1. Finds pending ``fill_report_request`` artifacts that are due.
  2. Broker-verifies each one read-only (``get_option_orders``): the
     order must be state ``filled``; anything else stays pending and
     is reported as a failure in the dispatch output.
  3. Generates the day's Hermes-format report (``daily_wheel_report.py``
     subprocess: .md + .png under outputs/daily-wheel/).
  4. Writes the dispatch report to ``outputs/daily-wheel/DATE-dispatch.md``
     and appends a line to ``.state/dispatch_log.jsonl``.
  5. Marks verified artifacts delivered.

Exit codes: 0 = nothing due or all due artifacts dispatched;
2 = at least one due artifact failed verification.

Run with system python3 (Pillow needed by the PNG renderer).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

PT = ZoneInfo("America/Los_Angeles")
DEFAULT_ARTIFACT_DIR = REPO / ".state" / "fill_reports"
DEFAULT_OUT_DIR = REPO / "outputs" / "daily-wheel"
DEFAULT_LOG = REPO / ".state" / "dispatch_log.jsonl"


def due_artifacts(artifact_dir: Path, now: datetime) -> list[tuple[Path, dict]]:
    due = []
    if not artifact_dir.exists():
        return due
    for path in sorted(artifact_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if data.get("kind") != "fill_report_request":
            continue
        if data.get("status") != "pending":
            continue
        raw = data.get("deliver_after")
        if not raw:
            continue
        try:
            deliver_after = datetime.fromisoformat(raw)
        except ValueError:
            continue
        if deliver_after <= now:
            due.append((path, data))
    return due


def verify_fill(transport, account: str, order_id: str) -> dict:
    """Read-only broker verification of one filled order."""
    result = transport.call_mcp(
        "get_option_orders", {"account_number": account})
    data = result.get("data") or {}
    # The MCP envelope has carried the rows under both keys today
    # (results in the morning, orders by the afternoon) — read both,
    # the same defensive shape dwr uses.
    orders = data.get("results") or data.get("orders") or []
    for order in orders:
        if order.get("id") != order_id:
            continue
        if order.get("state") != "filled":
            return {"ok": False, "order_id": order_id,
                    "reason": f"broker state is {order.get('state')!r}, not 'filled'"}
        legs = order.get("legs") or [{}]
        leg = legs[0]
        executions = leg.get("executions") or order.get("executions") or []
        qty = 0.0
        total = 0.0
        filled_at = None
        for ex in executions:
            q = float(ex.get("quantity") or 0)
            p = float(ex.get("price") or 0)
            qty += q
            total += q * p
            filled_at = ex.get("timestamp") or filled_at
        return {
            "ok": True,
            "order_id": order_id,
            "symbol": order.get("chain_symbol"),
            "direction": order.get("direction"),
            "position_effect": leg.get("position_effect"),
            "quantity": qty or float(order.get("processed_quantity") or 0),
            "avg_price": (total / qty) if qty else None,
            "filled_at": filled_at,
        }
    return {"ok": False, "order_id": order_id, "reason": "order not found at broker"}


def mark_delivered(path: Path, data: dict, now: datetime) -> None:
    data["status"] = "delivered"
    data["delivered_at"] = now.isoformat()
    data["delivered_via"] = "systemd_dispatch (D-31)"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def run_dispatch(now: datetime, artifact_dir: Path, out_dir: Path,
                 log_path: Path, account: str, transport=None,
                 day_report_cmd: list[str] | None = None) -> dict:
    if transport is None:
        import robinhood_transport as transport  # noqa: N806
    due = due_artifacts(artifact_dir, now)
    if not due:
        return {"dispatched": 0, "reason": "no pending fill reports due"}
    verified, failures = [], []
    for path, data in due:
        try:
            outcome = verify_fill(transport, account, str(data.get("order_id")))
        except Exception as exc:  # one bad artifact must not kill the dispatch
            outcome = {"ok": False, "order_id": str(data.get("order_id")),
                       "reason": f"verification error: {type(exc).__name__}: {exc}"[:300]}
        if outcome["ok"]:
            verified.append((path, data, outcome))
        else:
            failures.append(outcome)
    date_str = now.strftime("%Y-%m-%d")
    day_md = out_dir / f"{date_str}.md"
    day_png = out_dir / f"{date_str}.png"
    if verified:
        cmd = day_report_cmd or [sys.executable,
                                 str(REPO / "scripts" / "daily_wheel_report.py"),
                                 "--date", date_str]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        # dwr writes to its own out-dir and reports the paths as JSON;
        # trust its report, fall back to the conventional locations.
        try:
            reported = json.loads(proc.stdout.strip().splitlines()[-1])
            day_md = Path(reported.get("md") or day_md)
            day_png = Path(reported.get("png") or day_png)
        except (ValueError, IndexError, AttributeError):
            pass
        day_generated = day_md.exists() and day_png.exists()
    else:
        proc, day_generated = None, False
    lines = [f"Wheel fill-report dispatch — {date_str} 13:05 PT (code, D-31)", ""]
    for _, _, v in verified:
        lines.append(
            f"- {v['symbol']} {v['direction']}/{v['position_effect']} "
            f"qty {v['quantity']:g} @ ${v['avg_price']:.2f} avg — filled {v['filled_at']} "
            f"(order {v['order_id']}, broker-verified)")
    for f in failures:
        lines.append(f"- FAILED verification: order {f['order_id']} — {f['reason']}")
    lines.append("")
    lines.append(f"Day report: {day_md if day_generated else 'NOT generated'}")
    lines.append(f"Day image:  {day_png if day_generated else 'NOT generated'}")
    out_dir.mkdir(parents=True, exist_ok=True)
    dispatch_file = out_dir / f"{date_str}-dispatch.md"
    dispatch_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    for path, data, _ in verified:
        mark_delivered(path, data, now)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "at": now.isoformat(), "dispatched": len(verified),
            "failures": failures, "dispatch_file": str(dispatch_file),
            "day_report": str(day_md) if day_generated else None,
        }, sort_keys=True) + "\n")
    return {"dispatched": len(verified), "failures": failures,
            "dispatch_file": str(dispatch_file),
            "day_report": str(day_md) if day_generated else None,
            "generator_stdout": (proc.stdout or "")[-300:] if proc else None}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact-dir", type=Path, default=DEFAULT_ARTIFACT_DIR)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--log", type=Path, default=DEFAULT_LOG)
    parser.add_argument("--account", default="")
    parser.add_argument("--now", default="", help="ISO override (tests)")
    args = parser.parse_args()
    now = datetime.fromisoformat(args.now) if args.now else datetime.now(PT)
    account = args.account or _account_from_artifacts(args.artifact_dir)
    result = run_dispatch(now, args.artifact_dir, args.out_dir, args.log, account)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 2 if result.get("failures") else 0


def _account_from_artifacts(artifact_dir: Path) -> str:
    for _, data in due_artifacts(artifact_dir, datetime.max.replace(tzinfo=PT)):
        if data.get("account_number"):
            return str(data["account_number"])
    import os
    return os.environ.get("WHEEL_ACCOUNT_NUMBER", "")


if __name__ == "__main__":
    raise SystemExit(main())
