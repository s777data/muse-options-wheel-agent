"""Phase 5 contract validation (adapted from the PRD §7 Phase 5 contract).

This is NOT the source repo's company_valuation_score engine and does not
copy its contract.py. It validates the dashboard-derived response objects
produced by scripts/valuation_scorer.py before Phase 6 consumes them.

contract_version:    "1.0-wheel.1"
methodology_version: "dashboard-v5.4+gav-code"
"""
from __future__ import annotations

import math

CONTRACT_VERSION = "1.0-wheel.1"
METHODOLOGY_VERSION = "dashboard-v5.4+gav-code"
CONFIDENCE_LEVELS = {"high", "medium", "low", "insufficient"}


class ContractError(ValueError):
    pass


def _num_or_none(value):
    return value is None or (isinstance(value, (int, float))
                             and math.isfinite(value) and 0 <= value <= 100)


def validate_response(resp: dict) -> dict:
    """Validate one Phase 5 result object; return it or raise ContractError."""
    if not isinstance(resp, dict):
        raise ContractError("response is not an object")
    if resp.get("contract_version") != CONTRACT_VERSION:
        raise ContractError(f"contract_version mismatch: {resp.get('contract_version')!r}")
    if resp.get("methodology_version") != METHODOLOGY_VERSION:
        raise ContractError(
            f"methodology_version mismatch: {resp.get('methodology_version')!r}")
    if not resp.get("run_id"):
        raise ContractError("run_id missing/empty")
    if not resp.get("ticker"):
        raise ContractError("ticker missing")
    status = resp.get("status")
    if status not in ("ok", "insufficient_data"):
        raise ContractError(f"status invalid: {status!r}")
    if resp.get("confidence") not in CONFIDENCE_LEVELS:
        raise ContractError(f"confidence invalid: {resp.get('confidence')!r}")
    bq = resp.get("business_quality") or {}
    if bq.get("status") not in ("ok", "insufficient_data"):
        raise ContractError("business_quality.status invalid")
    if not _num_or_none(resp.get("final_score")):
        raise ContractError("final_score must be a finite 0-100 number or null")
    if not _num_or_none(bq.get("score")):
        raise ContractError("business_quality.score must be 0-100 or null")
    cov = bq.get("coverage_ratio")
    if cov is not None and not (isinstance(cov, (int, float)) and 0 <= cov <= 1):
        raise ContractError("business_quality.coverage_ratio must be in [0,1] or null")
    if status == "ok":
        if resp.get("final_score") is None:
            raise ContractError("status ok requires a final_score")
        if bq.get("status") != "ok" or bq.get("score") is None:
            raise ContractError("status ok requires business_quality ok with a score")
        gav = resp.get("gav_code") or {}
        if gav.get("score") is None:
            raise ContractError("status ok requires a gav_code score")
    else:  # insufficient_data must stay unscored
        if resp.get("final_score") is not None:
            raise ContractError("insufficient_data must have final_score null")
    tp = resp.get("target_prices") or {}
    for key in ("current", "target_price_A"):
        v = tp.get(key)
        if v is not None and not (isinstance(v, (int, float)) and math.isfinite(v)):
            raise ContractError(f"target_prices.{key} must be a finite number or null")
    return resp
