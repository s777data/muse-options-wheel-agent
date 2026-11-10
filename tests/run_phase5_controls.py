"""Phase 5 control test: MU, NVDA, V, BRK.B through the dashboard adapter.

Expected structure (Nov 6 basis, verified GAV-Code runs): GAV-Code
MU ~81.9, NVDA ~58.5, V ~35.0, BRK.B None -> BRK.B MUST be
insufficient_data with final_score None. Live prices/filings move the
exact numbers; structural mismatches are failures.
Run: .venv/bin/python tests/run_phase5_controls.py [tickers...]
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.valuation_scorer import run_sequential_valuation  # noqa: E402
from scripts.csp_screening import build_phase5_valuation_rows  # noqa: E402

tickers = sys.argv[1:] or ["MU", "NVDA", "V", "BRK.B"]
batch = run_sequential_valuation(tickers, fresh_retry=True)
# Source-signature row builder (session 2): (universe, input_tickers,
# valuation_result) -> (rows, summary). Universe records only carry the
# Phase 4 fields the builder reads here.
universe = {t: {"market_cap": None, "price": None,
                "fy_yoy": None, "fy_qoq": None, "conditions": None}
            for t in tickers}
rows, summary = build_phase5_valuation_rows(universe, tickers, batch)
out = {"comparison_table": batch["comparison_table"], "rows": rows,
       "summary": summary,
       "records": [{k: r.get(k) for k in
                    ("ticker", "status", "score", "grade", "confidence",
                     "stderr")} for r in batch["records"]]}
for r in batch["records"]:
    res = r.get("result") or {}
    fc = res.get("final_code") or {}
    print(f"{r['ticker']:6} status={r['status']:18} "
          f"Biz={fc.get('business_100')} Val={fc.get('value_100')} "
          f"GAV-Code={(res.get('gav_code') or {}).get('score')} "
          f"Final-Code={res.get('final_score')} "
          f"err={(r.get('stderr') or '')[:90]}")
print("rows order:", [(r["ticker"], r["score"],
                        r["score"] is not None) for r in rows])
Path("/tmp/phase5_controls.json").write_text(json.dumps(out, indent=2, default=str))
print("wrote /tmp/phase5_controls.json")
