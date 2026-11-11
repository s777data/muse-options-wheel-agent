"""Full wheel orchestrator — PRD Phase 0 scaffold + source phase bodies.

Session 2 merge: the verbatim phase bodies are now copied from
s777data/options-wheel-agent @ 0b0d64a (SHA-verified; see INVENTORY.md).
The source's own scripts/full_wheel.py orchestrator (async, CSP-first,
catch-and-continue, Hermes Telegram print placeholder) is NOT used:
per PRD §11 gaps 1/3/10 this scaffold's structure governs —

  - run manifest (run_id, started_at, workflow version) + duplicate-run
    guard for the same schedule window (gap 10);
  - PRD order: snapshot -> profit taking -> CSP eligibility/screening ->
    covered calls (gap 1; the source runs CSP first);
  - a required-phase failure makes the workflow FAILED — no
    catch-and-continue (gap 3); final status is PASS/FAILED, never PARTIAL;
  - each phase's telegram_message is delivered via the deduped
    send_telegram() below (gap 8 pattern from order_lifecycle.py);
    with no Telegram credentials configured, payloads are written to the
    run artifacts and logged telegram_not_configured;
  - the Phase 1 manifest entry stores only the artifact payload (masked
    account), never the runtime context with the full account number
    (D-11 artifact hygiene).
  - D-20 (2026-11-11, BIG SANDY's rule): the CSP screening block
    (Phases 3-7) does not run when effective buying power is below
    $700 — at that level no realistic cash-secured put can be funded,
    so the scan/gates/valuation/chain work is wasted. Effective buying
    power is the Phase 1 unleveraged buying power, or, when the D-16
    dry-run scan-cap override is set, the override-implied premise
    (cap x 100) so $40-cap rehearsals still run. The skip is recorded
    in the manifest (status "skipped", reason
    "buying_power_below_csp_minimum"); Phases 1, 2 and 8 still run —
    they manage existing positions, not new exposure. The threshold is
    a config value (env WHEEL_MIN_CSP_BUYING_POWER_USD, default 700),
    never hard-coded at the call site.
"""
from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

WORKFLOW_VERSION = "1.0.0"
# D-20: minimum effective buying power (USD) for the CSP screening
# block (Phases 3-7) to run at all. Config value, env-overridable.
MIN_CSP_BUYING_POWER_USD = float(
    os.environ.get("WHEEL_MIN_CSP_BUYING_POWER_USD", "700"))
_ROOT = Path(__file__).resolve().parents[1]
RUNS_DIR = _ROOT / "outputs" / "robinhood-wheel-agent" / "runs"
STATE_PATH = _ROOT / "outputs" / ".notification_state.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _notification_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _load_notification_state() -> dict:
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    return {"hashes": []}


def _write_notification_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")


def send_telegram(message: str, run_dir: Path | None = None) -> dict:
    """Deduped notification. No Telegram credentials are configured:
    the payload is persisted to the run artifacts and the result is
    telegram_not_configured. Never invents a token or a send."""
    h = _notification_hash(message)
    state = _load_notification_state()
    if h in state["hashes"]:
        return {"sent": False, "reason": "duplicate_deduped", "hash": h}
    if run_dir is not None:
        (run_dir / f"telegram_payload_{h[:12]}.txt").write_text(
            message, encoding="utf-8")
    state["hashes"].append(h)
    _write_notification_state(state)
    return {"sent": False, "reason": "telegram_not_configured", "hash": h}


def _schedule_window(now: datetime) -> str:
    return now.strftime("%Y-%m-%d")


def run_full_wheel(force_rerun: bool = False) -> dict:
    """Run the wheel in PRD order. Terminates at AWAITING_USER_ACTION on
    success; FAILED (with the phase and reason) on any required-phase
    failure. Order placement is never part of a scheduled run."""
    now = datetime.now(timezone.utc)
    run_id = f"{now.strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"
    run_dir = RUNS_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "run_id": run_id, "started_at": _now(),
        "workflow_version": WORKFLOW_VERSION,
        "schedule_window": _schedule_window(now),
        "source_baseline": "s777data/options-wheel-agent@0b0d64a (copied, SHA-verified; deviations in DEVIATIONS.md)",
        "valuation_methodology": "dashboard-v5.3+gav-code",
        "status": "PREFLIGHT", "phases": [],
    }
    manifest_path = run_dir / "00_run_manifest.json"

    # Duplicate-run guard (Phase 0): one run per schedule window.
    if not force_rerun:
        for other in sorted(RUNS_DIR.glob("*/00_run_manifest.json")):
            if other.parent == run_dir:
                continue
            try:
                prev = json.loads(other.read_text(encoding="utf-8"))
            except Exception:
                continue
            if prev.get("schedule_window") == manifest["schedule_window"] \
                    and prev.get("status") in ("PASS", "AWAITING_USER_ACTION"):
                manifest["status"] = "FAILED"
                manifest["failure"] = (
                    f"duplicate_run_guard: window {manifest['schedule_window']} "
                    f"already has run {prev.get('run_id')}; pass force_rerun=True")
                manifest_path.write_text(json.dumps(manifest, indent=2),
                                         encoding="utf-8")
                return manifest

    phases = []
    try:
        from scripts import csp_screening, profit_taking, covered_calls
        # Phase 1 snapshot / preflight
        phases.append(("01_account_snapshot", csp_screening.run_account_preflight))
        # Phase 2 position management BEFORE new exposure (PRD order)
        phases.append(("02_profit_taking", profit_taking.run_profit_taking_check))
        # Phases 3-7 CSP eligibility + screening
        phases.append(("03_07_csp_screening", csp_screening.run_csp_screening))
        # Phase 8 covered calls
        phases.append(("08_covered_calls", covered_calls.run_covered_call_evaluation))
        account_artifact: dict = {}
        for name, fn in phases:
            started = _now()
            if name == "03_07_csp_screening":
                # D-20 gate: skip new-exposure screening when effective
                # buying power cannot fund a realistic cash-secured put.
                bp = account_artifact.get("unleveraged_buying_power_usd")
                override = account_artifact.get("scan_cap_override_usd")
                effective_bp = (float(override) * 100.0
                                if override is not None else bp)
                if effective_bp is not None \
                        and float(effective_bp) < MIN_CSP_BUYING_POWER_USD:
                    manifest["phases"].append({
                        "phase": name, "started_at": started,
                        "finished_at": _now(), "status": "skipped",
                        "result": {
                            "reason": "buying_power_below_csp_minimum",
                            "buying_power_usd": bp,
                            "effective_buying_power_usd": effective_bp,
                            "min_required_usd": MIN_CSP_BUYING_POWER_USD,
                            "rule": "D-20",
                        }})
                    continue
            result = fn()
            entry = {"phase": name, "started_at": started,
                     "finished_at": _now(), "status": "ok"}
            if name == "01_account_snapshot" and isinstance(result, tuple):
                # D-11: manifest keeps only the masked artifact payload;
                # the runtime context carries the full account number.
                result = result[1]
            if name == "01_account_snapshot" and isinstance(result, dict):
                account_artifact = result
            entry["result"] = result
            if isinstance(result, dict) and result.get("telegram_message"):
                entry["telegram"] = send_telegram(
                    result["telegram_message"], run_dir=run_dir)
            manifest["phases"].append(entry)
        manifest["status"] = "AWAITING_USER_ACTION"
    except Exception as exc:  # required-phase failure => FAILED (gap 3)
        manifest["status"] = "FAILED"
        manifest["failure"] = f"{type(exc).__name__}: {exc}"[:1000]
    manifest["finished_at"] = _now()
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


if __name__ == "__main__":
    import sys
    m = run_full_wheel(force_rerun="--force-rerun" in sys.argv)
    print(json.dumps({"run_id": m["run_id"], "status": m["status"],
                      "failure": m.get("failure")}, indent=2))
    raise SystemExit(0 if m["status"] != "FAILED" else 1)
