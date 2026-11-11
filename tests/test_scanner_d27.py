"""D-27 unit checks for the banded scanner (synthetic, no network).

Run: .venv/bin/python tests/test_scanner_d27.py
Covers:
  1. A universe larger than the 200-row frontend limit is recovered by
     adaptive price-band splitting; merged rows are complete + unique.
  2. A small universe stays a single band (no splits).
  3. A band still truncated at minimum width fails closed.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import csp_screening as cs

# Fake universe: prices spread log-uniformly-ish across $0.50-$12.00
FAKE = []
for i in range(231):
    price = 0.50 + (i * 11.50 / 230.0)
    FAKE.append({
        "ticker": f"T{i:03d}",
        "columns": {"Last": price, "Market cap": 2_000_000_000.0,
                    "Implied volatility": 1.0, "RSI": 40.0,
                    "Average volume": 600_000.0},
        "_price": price,
    })

def fake_create_scan(filters=None, title=None):
    lo = hi = None
    for f in filters:
        if f["filter_type"] == "FILTER_TYPE_LAST" and f["predicate"] == ">=":
            lo = float(f["values"][0])
        if f["filter_type"] == "FILTER_TYPE_LAST" and f["predicate"] == "<":
            hi = float(f["values"][0])
    sid = f"scan-{lo}-{hi}"
    FAKE_SCANS[sid] = (lo, hi)
    return {"result": json.dumps({"data": {"result": {"scan_id": sid}}})} if False else _payload({"scan_id": sid})

def _payload(result):
    import json as _json
    return {"result": _json.dumps({"data": {"result": result}})}

FAKE_SCANS = {}

def fake_run_scan(scan_id=None):
    lo, hi = FAKE_SCANS[scan_id]
    rows = [r for r in FAKE if lo <= r["_price"] < hi]
    total = len(rows)
    page = [{k: v for k, v in r.items() if not k.startswith("_")} for r in rows[:200]]
    return _payload({"results": page, "total_items": total, "scan_id": scan_id})

import json
cs.mcp__robinhood_trading__create_scan = fake_create_scan
cs.mcp__robinhood_trading__run_scan = fake_run_scan
cs.MAX_UNDERLYING_PRICE_USD = 12.62

universe, payload = cs.run_single_scan()
assert len(universe) == 231, len(universe)
assert payload["complete"] is True
print(f"wide universe: 231 tickers recovered, bands={payload['metadata']['d27_bands']}")

# Small universe: no split
FAKE_SMALL = FAKE[:50]
FAKE_BACKUP = FAKE
FAKE = FAKE_SMALL
universe2, payload2 = cs.run_single_scan()
assert len(universe2) == 50 and payload2["metadata"]["d27_bands"] == 1
print("small universe: single band, 50 tickers")
FAKE = FAKE_BACKUP

# Permanently truncated dense band -> fail closed
def fake_run_scan_dense(scan_id=None):
    return _payload({"results": FAKE[:200], "total_items": 99999, "scan_id": scan_id})
cs.mcp__robinhood_trading__run_scan = fake_run_scan_dense
try:
    cs.run_single_scan()
    raise SystemExit("expected RuntimeError for dense band")
except RuntimeError as exc:
    print("dense band fails closed:", str(exc)[:60])
print("D-27 tests OK")
