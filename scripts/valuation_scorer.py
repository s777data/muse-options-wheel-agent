"""Phase 5 valuation adapter — dashboard engine edition.

REPLACES the source repo's valuation_scorer.py, which shelled out to the
placeholder ``company_valuation_score`` CLI. That engine's code is NOT
copied here (per BIG SANDY's instruction). Instead this adapter calls the
Muse Business Evaluation Dashboard engine (V5.4 + GAV-Code, vendored
unmodified under vendor/muse-business-evaluation-dashboard/, source repo
s777data/muse-business-evaluation-dashboard @ ac8deaf) IN-PROCESS:

    from src.evaluator.run import evaluate
    result = evaluate(symbol, cfg)          # cfg = config.load_all()

Public signatures and the batch envelope shape are preserved from the
source adapter so csp_screening.py works with minimal change:
``run_sequential_valuation(tickers, timeout_seconds=..., fresh_retry=...)``
returns {execution_mode, ticker_count, refresh_flag_passed, no_cache,
fresh_retry, comparison_table, records}.

Scoring (all from ONE evaluate() run per ticker, 0-10 engine scale):
  business_100 = scores.business.total * 10
  value_100    = scores.value.total * 10
  gav_code     = result["gav_code"]["score"]            (0-100, fail-closed)
  FINAL-CODE   = round(0.5*business_100 + 0.25*value_100 + 0.25*gav_code, 1)
FINAL-CODE replaces the placeholder final_score as the composite `score`.

Fail-closed: any exception, missing Business/Value, or a gav_code
missing_reason produces a terminal `insufficient_data` record with the
reason preserved, score None — never fabricated, never retried to force
a score. ONE approved exception (BIG SANDY, 2026-11-09): when Business
and Value both scored but GAV-Code failed ONLY for missing TTM revenue
growth, a flagged fallback derives the growth band from yfinance
annual revenue (see _gav_yfinance_fallback) and marks the result
prominently (result["fallback"], gav_code.source). `fresh_retry` is accepted for signature compatibility but NEVER
triggers a --no-cache/--refresh retry (PRD §11 gap 5): the dashboard
engine's own 12h EDGAR disk cache governs freshness.

Tickers absent from the vendored engine config (the common scanner case):
evaluate() requires a config entry with a CIK. The adapter resolves the
CIK from SEC EDGAR's official company_tickers.json and injects an
in-memory config entry (no beta — the engine computes beta on the fly,
V5.1). Vendored files on disk are never modified. Symbols are normalized
for EDGAR/Yahoo ("BRK.B" -> "BRK-B").
"""
from __future__ import annotations

import copy
import json
import sys
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_VENDOR = _ROOT / "vendor" / "muse-business-evaluation-dashboard"
if str(_VENDOR) not in sys.path:
    sys.path.insert(0, str(_VENDOR))

from scripts.valuation_contract import (  # noqa: E402
    CONTRACT_VERSION, METHODOLOGY_VERSION, validate_response)

VALUATION_TICKER_TIMEOUT_SECONDS = 600
_EDGAR_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"

_cfg_cache = None
_cik_cache: dict[str, dict] | None = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_cfg():
    global _cfg_cache
    if _cfg_cache is None:
        from src.evaluator import config as cfgmod
        _cfg_cache = cfgmod.load_all()
    return _cfg_cache


def _user_agent(cfg) -> str:
    return (cfg.get("sources") or {}).get("sources", {}).get(
        "user_agent", "Muse Options Wheel Agent admin@example.com")


def _resolve_cik(engine_symbol: str, cfg) -> dict | None:
    """Resolve {cik, name} for a symbol from EDGAR company_tickers.json."""
    global _cik_cache
    if _cik_cache is None:
        req = urllib.request.Request(
            _EDGAR_TICKERS_URL, headers={"User-Agent": _user_agent(cfg)})
        with urllib.request.urlopen(req, timeout=60) as fh:
            raw = json.loads(fh.read().decode("utf-8"))
        _cik_cache = {}
        for row in raw.values():
            _cik_cache[row["ticker"].upper()] = {
                "cik": str(row["cik_str"]).zfill(10), "name": row["title"]}
    return _cik_cache.get(engine_symbol.upper())


def _cfg_for(engine_symbol: str, cfg):
    """Return (cfg, injected_bool, reason). Injects an in-memory ticker
    entry via EDGAR CIK lookup when the symbol is not configured."""
    tickers = cfg["tickers"]["tickers"]
    if engine_symbol in tickers:
        return cfg, False, None
    info = _resolve_cik(engine_symbol, cfg)
    if info is None:
        return cfg, False, f"cik_not_found_on_edgar_for_{engine_symbol}"
    cfg2 = copy.deepcopy(cfg)
    cfg2["tickers"]["tickers"][engine_symbol] = {
        "name": info["name"], "cik": info["cik"], "exchange": "",
        "currency": "USD",
        # no beta: engine resolves beta on the fly (V5.1), disclosed in
        # the run's data_quality.
    }
    return cfg2, True, None


def _grade(score):
    if score is None:
        return None
    for floor, grade in ((97, "A+"), (93, "A"), (90, "A-"), (87, "B+"),
                         (83, "B"), (80, "B-"), (77, "C+"), (73, "C"),
                         (70, "C-"), (67, "D+"), (63, "D"), (60, "D-")):
        if score >= floor:
            return grade
    return "F"


def _confidence(business: dict, value: dict) -> tuple[str, float, bool]:
    missing = len(business.get("missing_metrics") or []) \
        + len(value.get("missing_metrics") or [])
    cov_b = business.get("weights_renormalised_over") or 0.0
    cov_v = value.get("weights_renormalised_over") or 0.0
    coverage = round(min(cov_b, cov_v), 4)
    passed = coverage >= 0.999 and missing == 0
    if passed:
        return "high", coverage, True
    if coverage >= 0.7:
        return "medium", coverage, False
    return "low", coverage, False


def _yfinance_annual_revenue_growth(engine_symbol: str):
    """Annual revenue growth from yfinance (two most recent annual
    Total Revenue values), as a fraction — or None when unavailable.
    This is the adapter's ONLY non-EDGAR input, used solely by the
    flagged GAV-Code fallback below; the engine itself stays
    EDGAR-only."""
    try:
        import math

        import yfinance as yf
        fin = yf.Ticker(engine_symbol).financials
        if fin is None or "Total Revenue" not in fin.index:
            return None
        vals = []
        for v in fin.loc["Total Revenue"].tolist():
            if v is None:
                continue
            fv = float(v)
            if math.isfinite(fv):
                vals.append(fv)
        if len(vals) < 2 or vals[1] <= 0:
            return None
        return vals[0] / vals[1] - 1
    except Exception:
        return None


def _gav_yfinance_fallback(engine_symbol: str, value_total: float):
    """Flagged GAV-Code fallback (approved by BIG SANDY 2026-11-09):
    when the engine produced Business and Value but no GAV-Code
    because TTM revenue growth is missing on EDGAR, derive the growth
    band from yfinance annual revenue with the engine's OWN band table
    and label set (imported from the vendored scoring module, so the
    bands can never drift apart). The substitute is disclosed in the
    result three ways: gav_code.source / gav_code.growth_source =
    "yfinance_annual_fallback" and result.fallback =
    "gav_growth_yfinance_annual". Returns the gav block or None."""
    growth = _yfinance_annual_revenue_growth(engine_symbol)
    if growth is None:
        return None
    from src.evaluator.scoring import gav_label, growth_band_score
    band = growth_band_score(growth * 100)
    if band is None:
        return None
    score = round(0.5 * band + 0.5 * value_total * 10, 1)
    return {"score": score, "label": gav_label(score),
            "growth_score": band,
            "ttm_revenue_growth_pct": round(growth * 100, 4),
            "growth_source": "yfinance_annual_fallback",
            "source": "yfinance_annual_fallback",
            "value_score": value_total,
            "weights": {"growth": 0.5, "value": 0.5},
            "missing_reason": None}


def _build_result(ticker: str, engine_symbol: str, raw: dict,
                  injected: bool) -> dict:
    scores = raw.get("scores") or {}
    business = scores.get("business") or {}
    value = scores.get("value") or {}
    gav = raw.get("gav_code") or {}
    b_total, v_total = business.get("total"), value.get("total")
    run_id = uuid.uuid4().hex

    def insufficient(reason: str) -> dict:
        resp = {
            "contract_version": CONTRACT_VERSION,
            "methodology_version": METHODOLOGY_VERSION,
            "run_id": run_id, "ticker": ticker, "engine_symbol": engine_symbol,
            "status": "insufficient_data", "final_score": None,
            "grade": None, "classification": None,
            "confidence": "insufficient", "regime": "ordinary",
            "as_of": _now(), "coverage_passed": False,
            "failure_reason": reason,
            "business_quality": {
                "status": "insufficient_data",
                "score": None if b_total is None else round(b_total * 10, 1),
                "grade": None, "classification": None,
                "confidence": "insufficient",
                "coverage_ratio": business.get("weights_renormalised_over"),
                "missing_metrics": business.get("missing_metrics") or []},
            "valuation": {
                "status": "insufficient_data",
                "score": None if v_total is None else round(v_total * 10, 1),
                "missing_metrics": value.get("missing_metrics") or []},
            "gav_code": gav,
            "target_prices": {
                "current": (raw.get("dcf") or {}).get("current_price"),
                "target_price_Aplus": None,
                "target_price_A": (raw.get("dcf") or {}).get("fair_value_per_share"),
                "target_price_Aminus": ((raw.get("dcf") or {}).get(
                    "stress_floor") or {}).get("fair_value_per_share")},
            "cik_injected_from_edgar": injected,
        }
        return validate_response(resp)

    if b_total is None:
        return insufficient("business_score_missing")
    if v_total is None:
        return insufficient("value_score_missing")
    fallback = None
    if gav.get("score") is None \
            and gav.get("missing_reason") == "ttm_revenue_growth_missing":
        fb_gav = _gav_yfinance_fallback(engine_symbol, v_total)
        if fb_gav is not None:
            gav = fb_gav
            fallback = "gav_growth_yfinance_annual"
    if gav.get("score") is None:
        return insufficient(gav.get("missing_reason")
                            or "gav_code_missing")

    business_100 = round(b_total * 10, 1)
    value_100 = round(v_total * 10, 1)
    final_code = round(0.5 * business_100 + 0.25 * value_100
                       + 0.25 * gav["score"], 1)
    confidence, coverage, cov_passed = _confidence(business, value)
    dcf = raw.get("dcf") or {}
    resp = {
        "contract_version": CONTRACT_VERSION,
        "methodology_version": METHODOLOGY_VERSION,
        "run_id": run_id, "ticker": ticker, "engine_symbol": engine_symbol,
        "status": "ok", "final_score": final_code,
        "fallback": fallback,
        "grade": _grade(final_code),
        "classification": gav.get("label"),
        "confidence": confidence, "regime": "ordinary",
        "as_of": _now(), "coverage_passed": cov_passed,
        "failure_reason": None,
        "business_quality": {
            "status": "ok", "score": business_100,
            "grade": _grade(business_100),
            "classification": None, "confidence": confidence,
            "coverage_ratio": coverage,
            "missing_metrics": business.get("missing_metrics") or [],
            "explanation": (raw.get("explanations") or {}).get("business")},
        "valuation": {
            "status": "ok", "score": value_100, "grade": _grade(value_100),
            "explanation": (raw.get("explanations") or {}).get("value")},
        "gav_code": gav,
        "final_code": {
            "score": final_code,
            "formula": "0.5*business*10 + 0.25*value*10 + 0.25*gav_code",
            "business_100": business_100, "value_100": value_100,
            "gav_code_score": gav["score"]},
        "target_prices": {
            "current": dcf.get("current_price"),
            "target_price_Aplus": None,
            "target_price_A": dcf.get("fair_value_per_share"),
            "target_price_Aminus": (dcf.get("stress_floor") or {}).get(
                "fair_value_per_share")},
        "snapshot_date": raw.get("snapshot_date"),
        "cik_injected_from_edgar": injected,
    }
    return validate_response(resp)


def run_one_ticker(ticker: str, timeout_seconds: int | None = None) -> dict:
    """Evaluate one ticker with the dashboard engine ( sequential,
    in-process). Returns one batch-envelope record. timeout_seconds is
    accepted for signature compatibility; the in-process engine call is
    bounded by the engine's own HTTP timeouts."""
    ticker_u = (ticker or "").strip().upper()
    engine_symbol = ticker_u.replace(".", "-")
    started = _now()
    record = {
        "ticker": ticker_u,
        "command": ["in-process", "src.evaluator.run.evaluate", engine_symbol],
        "started_at": started, "finished_at": None, "returncode": 1,
        "stdout": "", "stderr": "", "status": "insufficient_data",
        "score": None, "grade": None, "confidence": "insufficient",
        "regime": "ordinary", "attempt_count": 1, "retry_count": 0,
        "attempts": [], "result": None,
    }
    try:
        from src.evaluator.run import evaluate
        cfg, injected, reason = _cfg_for(engine_symbol, _load_cfg())
        if reason is not None:
            raise LookupError(reason)
        raw = evaluate(engine_symbol, cfg)
        result = _build_result(ticker_u, engine_symbol, raw, injected)
        record["result"] = result
        record["status"] = result["status"]
        record["returncode"] = 0 if result["status"] == "ok" else 1
        record["score"] = result["final_score"]
        record["grade"] = result["grade"]
        record["confidence"] = result["confidence"]
        if result["status"] != "ok":
            record["stderr"] = result.get("failure_reason") or ""
    except Exception as exc:  # fail closed, reason preserved
        record["stderr"] = f"{type(exc).__name__}: {exc}"[:1000]
        record["result"] = None
        record["failure_reason"] = record["stderr"]
    record["finished_at"] = _now()
    return record


def run_sequential_valuation(tickers, timeout_seconds: int | None = None,
                             fresh_retry: bool = True) -> dict:
    """Sequential batch adapter — same envelope as the source adapter.

    fresh_retry is accepted but MUST NOT trigger a no-cache retry
    (PRD §11 gap 5); it is recorded in the envelope and logged as
    disabled. The dashboard EDGAR cache governs freshness."""
    ordered, seen = [], set()
    for t in tickers or []:
        u = (t or "").strip().upper()
        if u and u not in seen:
            seen.add(u)
            ordered.append(u)
    records = [run_one_ticker(t, timeout_seconds=timeout_seconds)
               for t in ordered]
    comparison = [{
        "Ticker": r["ticker"],
        "Score": (r["result"] or {}).get("final_score"),
        "Grade": r["grade"],
        "Regime": r["regime"],
        "Confidence": r["confidence"],
        "Status": r["status"],
    } for r in records]
    return {
        "execution_mode": "sequential_in_process_dashboard_engine",
        "ticker_count": len(ordered),
        "refresh_flag_passed": False,
        "no_cache": False,
        "fresh_retry": fresh_retry,
        "fresh_retry_effect": "disabled_per_prd_gap_5_no_cache_forbidden",
        "comparison_table": comparison,
        "records": records,
    }
