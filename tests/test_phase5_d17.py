"""D-17 unit checks for build_phase5_valuation_rows (synthetic, no network).

Run: .venv/bin/python tests/test_phase5_d17.py
Covers:
  1. insufficient_data terminal record with result=None (LEGN shape: a
     20-F foreign filer, engine raises "No 10-K found") -> structured
     unscored row, same schema, failure_reason preserved, sorted last.
  2. Any other terminal status with a missing result -> still raises
     (fail closed; D-17 must not swallow retrieval errors).
  3. Scored-only cohort is unchanged: scored row fields pass through.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.csp_screening import build_phase5_valuation_rows  # noqa: E402

UNIVERSE = {
    "MU": {"market_cap": 700_000_000_000, "price": 640.0, "fy_yoy": 0.48,
           "fy_qoq": 0.22, "conditions": {"rsi_ok": True}},
    "LEGN": {"market_cap": 3_100_000_000, "price": 18.5, "fy_yoy": 0.31,
             "fy_qoq": 0.09, "conditions": {"rsi_ok": True}},
}

OK_RECORD = {
    "ticker": "MU", "status": "ok", "confidence": "high",
    "failure_reason": None,
    "result": {
        "business_quality": {"score": 82.0, "grade": "A",
                             "explanation": "biz"},
        "valuation": {"score": 41.0, "grade": "C", "explanation": "val"},
        "gav_code": {"score": 81.9, "label": "Strong",
                     "source": "ttm_bridge"},
        "final_score": 68.8, "classification": "compounder",
        "confidence": "high", "regime": "ordinary", "fallback": None,
        "as_of": "2026-11-11", "run_id": "x", "contract_version": "1",
        "methodology_version": "V5.3", "failure_reason": None,
        "modifiers": [],
    },
}

LEGN_RECORD = {
    "ticker": "LEGN", "status": "insufficient_data",
    "confidence": "insufficient", "result": None,
    "failure_reason": "RuntimeError: No 10-K found in EDGAR submissions "
                      "for CIK 0001801198",
}

failures = []


def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        failures.append(name)


# 1. Mixed cohort: LEGN rides along unscored.
rows, summary = build_phase5_valuation_rows(
    UNIVERSE, ["MU", "LEGN"],
    {"records": [OK_RECORD, LEGN_RECORD]})
check("mixed cohort reconciles 1:1", len(rows) == 2)
check("summary counts", summary["scored_count"] == 1
      and summary["unscored_count"] == 1)
check("status counts carry insufficient_data",
      summary["status_counts"].get("insufficient_data") == 1)
legn = [r for r in rows if r["ticker"] == "LEGN"][0]
mu = [r for r in rows if r["ticker"] == "MU"][0]
check("LEGN score/final_code None", legn["score"] is None
      and legn["final_code"] is None)
check("LEGN component scores None", legn["valuation_score"] is None
      and legn["business_quality_score"] is None
      and legn["gav_code_score"] is None)
check("LEGN failure_reason preserved",
      "No 10-K found" in (legn["failure_reason"] or ""))
check("LEGN phase-4 fields survive", legn["current_price"] == 18.5
      and legn["fy_yoy"] == 0.31)
check("LEGN market cap falls back to phase 4",
      legn["market_cap_raw"] == 3_100_000_000)
check("same row schema", set(legn.keys()) == set(mu.keys()))
check("unscored sorts last", rows[-1]["ticker"] == "LEGN")

# 2. Non-insufficient_data missing result still fails closed.
for bad_status in ("error", "timeout", "unknown"):
    bad = {"ticker": "LEGN", "status": bad_status, "confidence": None,
           "result": None, "failure_reason": "boom"}
    try:
        build_phase5_valuation_rows(UNIVERSE, ["MU", "LEGN"],
                                    {"records": [OK_RECORD, bad]})
        check(f"status={bad_status} raises", False)
    except RuntimeError:
        check(f"status={bad_status} raises", True)

# 3. Scored row unchanged.
check("MU final_code passthrough", mu["score"] == 68.8
      and mu["final_code"] == 68.8)
check("MU components passthrough", mu["business_quality_score"] == 82.0
      and mu["valuation_score"] == 41.0 and mu["gav_code_score"] == 81.9)

print("\n%d failure(s)" % len(failures))
sys.exit(1 if failures else 0)
