#!/usr/bin/env python3
"""
CSP Screening — Real-time agent execution.
Loads: trading-account-csp-process, mcp-precomputation-patterns, trading-account-session-management
Outputs: phase-specific JSON artifacts + top 5 CSP recommendations + Telegram delivery
"""
# muse-options-wheel-agent: verbatim copy of s777data/options-wheel-agent
# @ 0b0d64a scripts/csp_screening.py (SHA-verified), with ONLY the logged
# deviations D-05 (Phase 5), D-06 (transport), D-10 (local paths).
# See DEVIATIONS.md.


import os
import sys
import json
import math
import atexit
import shutil
import argparse

import time
import textwrap
import httpx
import yfinance as yf
import pickle
from datetime import datetime, timedelta, timezone, date
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any
from zoneinfo import ZoneInfo

# D-12 (environment hygiene): some sandboxes export no_proxy entries in
# bracketed-IPv6 form ("[::1]") that httpx's URLPattern parser rejects
# when the module-level SEC client is constructed with trust_env=True.
# Drop ONLY the bracketed duplicates (the same hosts are present
# unbracketed) so the verbatim client construction below works.
for _np_var in ("no_proxy", "NO_PROXY"):
    _np_val = os.environ.get(_np_var)
    if _np_val:
        os.environ[_np_var] = ",".join(
            part for part in _np_val.split(",")
            if not (part.startswith("[") and part.endswith("]")))

# Ensure repository-local imports resolve when the script is launched directly.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # repo root (D-10)
from scripts.valuation_scorer import run_sequential_valuation

# ==================== ROBINHOOD TRANSPORT (D-06) ====================
# The source connects through the Hermes MCP runtime (hermes_cli
# registry). This repo routes every call through
# scripts/robinhood_transport.py, which shells to the local READ-ONLY
# Robinhood client; the binding names and the Hermes result envelope
# ({"result": "<json>"}) are preserved, so every call site below is
# unchanged. initialize/shutdown are no-ops (per-call subprocess
# transport). Write tools raise WriteToolDisabled and non-allowlisted
# read tools raise ToolNotAllowlisted in the shim (see INVENTORY.md).

from scripts.robinhood_transport import hermes_envelope as _hermes_envelope


def initialize_mcp_runtime() -> None:
    """No-op in this repo: transport is per-call via the local client."""
    return None


def shutdown_mcp_runtime() -> None:
    """No-op counterpart of initialize_mcp_runtime (D-06)."""
    return None


def call_robinhood_tool(tool_name: str, **arguments) -> dict:
    """Call one Robinhood MCP tool and preserve the existing result envelope."""
    return _hermes_envelope(tool_name, arguments)


def _robinhood_tool(tool_name: str):
    return lambda **arguments: call_robinhood_tool(tool_name, **arguments)


mcp__robinhood_trading__create_scan = _robinhood_tool("create_scan")
mcp__robinhood_trading__run_scan = _robinhood_tool("run_scan")
mcp__robinhood_trading__get_equity_fundamentals = _robinhood_tool("get_equity_fundamentals")
mcp__robinhood_trading__get_equity_technical_indicators = _robinhood_tool("get_equity_technical_indicators")
mcp__robinhood_trading__get_option_chains = _robinhood_tool("get_option_chains")
mcp__robinhood_trading__get_option_instruments = _robinhood_tool("get_option_instruments")
mcp__robinhood_trading__get_option_quotes = _robinhood_tool("get_option_quotes")
mcp__robinhood_trading__get_equity_quotes = _robinhood_tool("get_equity_quotes")
mcp__robinhood_trading__get_accounts = _robinhood_tool("get_accounts")
mcp__robinhood_trading__get_portfolio = _robinhood_tool("get_portfolio")

# ==================== CONFIG (from skill catalog) ====================
MIN_AVERAGE_VOLUME_SHARES = 500_000
CONFIGURED_MIN_MARKET_CAP_USD = float(os.environ.get("MIN_MARKET_CAP_USD", "1000000000"))
MIN_MARKET_CAP_USD = CONFIGURED_MIN_MARKET_CAP_USD
LOW_CASH_MIN_MARKET_CAP_USD = 500_000_000.0
LOW_CASH_MAX_UNDERLYING_PRICE_USD = 25.0
ELIGIBLE_CSP_ACCOUNT_TYPES = {"cash", "limited_margin"}
MAX_MARKET_CAP_USD = 500_000_000_000
MIN_ATM_PUT_IV = 0.70
MAX_ATM_PUT_IV = 10.0
# Runtime override: CSP workflow may set this from agentic buying power / 100.
# The default preserves the catalog value when no override is supplied.
MAX_UNDERLYING_PRICE_USD = float(os.environ.get("MAX_UNDERLYING_PRICE_USD", "110.0"))
MIN_SEC_ANNUAL_YOY_REVENUE_GROWTH = 0.15
REQUIRED_COUNTRY = "US"
CSP_MAX_RSI = 55
SMA_MEDIUM_PERIOD_SESSIONS = 50
SMA_LONG_PERIOD_SESSIONS = 200
BOLLINGER_PERIOD_SESSIONS = 20
BOLLINGER_STDDEV_MULTIPLIER = 2
MAX_SMA_50_DISTANCE = 0.10
MAX_SMA_200_DISTANCE = 0.10
MAX_BOLLINGER_PERCENT_B = 0.35
MIN_CSP_SHORT_POP = 0.60
MAX_CSP_SHORT_POP = 0.85
MAX_BID_ASK_SPREAD_RATIO = 0.30
MIN_OPTION_DTE = int(os.environ.get("MIN_OPTION_DTE", "30"))
MAX_OPTION_DTE = int(os.environ.get("MAX_OPTION_DTE", "45"))
FALLBACK_MIN_OPTION_DTE = 25
FALLBACK_MAX_OPTION_DTE = 60
MIN_CSP_NET_ROC = 0.08
MIN_OPTION_OPEN_INTEREST = 100
MIN_OPTION_DAILY_VOLUME = 1
MAX_CSP_CANDIDATES = 5
MAX_PHASE5_VALUATION_CANDIDATES = 5
VALUATION_TICKER_TIMEOUT_SECONDS = 300
MAX_CONTRACTS_PER_TICKER_IN_CSP_RANKING = 1
FINAL_RANK_RUNNER_UP_COUNT = 1
STANDARD_OPTION_CONTRACT_SHARES = 100
QUOTE_MAX_AGE_SECONDS = 1200
DAYS_PER_YEAR = 365


def parse_mcp_payload(call_result: dict, tool_name: str) -> dict:
    """Decode the JSON payload returned inside a Hermes MCP result envelope."""
    try:
        payload = json.loads(call_result["result"])
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            f"Robinhood MCP tool {tool_name} returned an invalid result envelope"
        ) from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), dict):
        raise RuntimeError(
            f"Robinhood MCP tool {tool_name} returned no data object"
        )
    return payload["data"]


def finite_nonnegative_number(value: Any, field_name: str) -> float:
    """Parse a broker numeric field and fail closed on missing/invalid values."""
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"{field_name} is missing or non-numeric") from exc
    if not math.isfinite(number) or number < 0:
        raise RuntimeError(f"{field_name} must be finite and non-negative")
    return number


def is_eligible_csp_account(account: dict) -> bool:
    """Return whether an account may supply CSP screening buying power."""
    return (
        account.get("agentic_allowed") is True
        and account.get("type") in ELIGIBLE_CSP_ACCOUNT_TYPES
        and account.get("option_level") in {"option_level_2", "option_level_3"}
        and account.get("state") == "active"
        and not account.get("deactivated")
        and not account.get("permanently_deactivated")
    )


def run_account_preflight() -> Tuple[dict, dict]:
    """Select the sole eligible agentic account and derive CSP limits.

    Returns ``(runtime_context, artifact_payload)``. The runtime context keeps
    the full account number for subsequent read-only broker calls; the artifact
    contains only its masked suffix.
    """
    global MAX_UNDERLYING_PRICE_USD, MIN_MARKET_CAP_USD

    accounts_data = parse_mcp_payload(
        mcp__robinhood_trading__get_accounts(), "get_accounts"
    )
    accounts = accounts_data.get("accounts")
    if not isinstance(accounts, list):
        raise RuntimeError("get_accounts returned no accounts list")

    eligible = [
        account
        for account in accounts
        if is_eligible_csp_account(account)
    ]
    if len(eligible) != 1:
        raise RuntimeError(
            "Expected exactly one active agentic cash or limited-margin account "
            "with option level "
            f"2 or 3; found {len(eligible)}"
        )

    account = eligible[0]
    account_number = account.get("account_number")
    if not account_number:
        raise RuntimeError("Eligible account is missing account_number")

    portfolio_data = parse_mcp_payload(
        mcp__robinhood_trading__get_portfolio(account_number=account_number),
        "get_portfolio",
    )
    buying_power_data = portfolio_data.get("buying_power")
    if not isinstance(buying_power_data, dict):
        raise RuntimeError("get_portfolio returned no buying_power object")
    buying_power = finite_nonnegative_number(
        buying_power_data.get("unleveraged_buying_power"),
        "unleveraged_buying_power",
    )

    # Preserve the exact broker-derived cap for the scanner's strict '<' test.
    MAX_UNDERLYING_PRICE_USD = buying_power / STANDARD_OPTION_CONTRACT_SHARES
    # D-16 (dry-run scan-cap override, requested by BIG SANDY 2026-11-09):
    # when WHEEL_SCAN_PRICE_CAP_USD is set, the scanner cap uses it so a
    # dry run can rehearse a funded account (e.g. 40 = $4,000 buying
    # power). Analysis scope only: collateral capacity checks keep the
    # real buying power, and order placement stays hard-disabled.
    broker_cap_usd = MAX_UNDERLYING_PRICE_USD
    _cap_override = os.environ.get("WHEEL_SCAN_PRICE_CAP_USD")
    if _cap_override:
        MAX_UNDERLYING_PRICE_USD = float(_cap_override)
    MIN_MARKET_CAP_USD = (
        LOW_CASH_MIN_MARKET_CAP_USD
        if MAX_UNDERLYING_PRICE_USD <= LOW_CASH_MAX_UNDERLYING_PRICE_USD
        else CONFIGURED_MIN_MARKET_CAP_USD
    )

    masked_account = f"••••{str(account_number)[-4:]}"
    runtime_context = {
        "account_number": account_number,
        "buying_power": buying_power,
        "max_underlying_price_usd": MAX_UNDERLYING_PRICE_USD,
        "min_market_cap_usd": MIN_MARKET_CAP_USD,
    }
    artifact_payload = {
        "status": "PASS",
        "accounts_seen": len(accounts),
        "eligible_accounts": len(eligible),
        "selected_account": masked_account,
        "account_type": account.get("type"),
        "option_level": account.get("option_level"),
        "agentic_allowed": account.get("agentic_allowed"),
        "account_state": account.get("state"),
        "unleveraged_buying_power_usd": buying_power,
        "max_underlying_price_usd": MAX_UNDERLYING_PRICE_USD,
        "broker_derived_cap_usd": broker_cap_usd,
        "scan_cap_override_usd": (
            float(_cap_override) if _cap_override else None),
        "min_market_cap_usd": MIN_MARKET_CAP_USD,
        "source": "Robinhood MCP get_accounts + get_portfolio",
    }
    return runtime_context, artifact_payload


def calculate_cash_secured_capacity(
    buying_power: float,
    strike: float,
    premium_per_contract: float = 0.0,
    multiplier: int = STANDARD_OPTION_CONTRACT_SHARES,
    credit_reduces_requirement: bool = True,
) -> dict:
    """Calculate CSP capacity from available cash buying power only.

    Portfolio/account market value is not collateral capacity.  A CSP
    recommendation must fail closed when buying power is unavailable or
    invalid, and collateral may never exceed the supplied buying power.
    """
    bp = float(buying_power)
    strike_value = float(strike)
    premium_value = float(premium_per_contract)
    multiplier_value = int(multiplier)
    if not math.isfinite(bp) or bp < 0:
        raise ValueError("buying_power must be a finite non-negative number")
    if not math.isfinite(strike_value) or strike_value <= 0:
        raise ValueError("strike must be a finite positive number")
    if not math.isfinite(premium_value) or premium_value < 0:
        raise ValueError("premium_per_contract must be a finite non-negative number")
    if multiplier_value <= 0:
        raise ValueError("multiplier must be positive")

    collateral_per_contract = strike_value * multiplier_value
    net_collateral_per_contract = collateral_per_contract - premium_value
    if net_collateral_per_contract <= 0:
        raise ValueError("premium_per_contract must be less than collateral_per_contract")
    required_cash_per_contract = (
        net_collateral_per_contract
        if credit_reduces_requirement
        else collateral_per_contract
    )
    contract_count = math.floor(bp / required_cash_per_contract)
    total_collateral = contract_count * collateral_per_contract
    total_premium = contract_count * premium_value
    return {
        "buying_power": bp,
        "collateral_per_contract": collateral_per_contract,
        "premium_per_contract": premium_value,
        "net_collateral_per_contract": net_collateral_per_contract,
        "required_cash_per_contract": required_cash_per_contract,
        "contract_count": contract_count,
        "total_collateral": round(total_collateral, 2),
        "total_premium": round(total_premium, 2),
        "remaining_buying_power": round(bp + total_premium - total_collateral, 2),
    }

BASE_OUTPUT_DIR = Path(os.environ.get(
    "WHEEL_OUTPUT_DIR",
    str(Path(__file__).resolve().parents[1] / "outputs" / "robinhood-wheel-agent")))  # D-10
RUN_ID = os.environ.get("RUN_ID") or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
OUTPUT_DIR = BASE_OUTPUT_DIR / RUN_ID

# The scanner is intentionally one query. The result is retrieved synchronously;
# transient scanner failures retry twice after a fixed five-second delay.
SCAN_MAX_ATTEMPTS = 3
SCAN_RETRY_DELAY_SECONDS = 5
FUNDAMENTALS_BATCH_SIZE = 10
FUNDAMENTALS_MAX_ATTEMPTS = 3
FUNDAMENTALS_RETRY_DELAY_SECONDS = 5
YFINANCE_MAX_ATTEMPTS = 3
YFINANCE_RETRY_DELAY_SECONDS = 2
TECHNICAL_MAX_ATTEMPTS = 3
TECHNICAL_RETRY_DELAY_SECONDS = 2

# SEC EDGAR
_CACHE_BASE = Path(os.environ.get(
    "WHEEL_CACHE_DIR",
    str(Path(__file__).resolve().parents[1] / "outputs" / "cache")))  # D-10
SEC_DIR = _CACHE_BASE / "sec_facts"
SEC_DIR.mkdir(parents=True, exist_ok=True)
CACHE_DIR = _CACHE_BASE
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# CIK mapping
CIK_MAP = {}
cik_path = CACHE_DIR / "cik_mapping.pkl"
if cik_path.exists():
    with open(cik_path, 'rb') as f:
        CIK_MAP = pickle.load(f).get('map', {})


def persist_phase_json(phase_number: int, phase_name: str, payload: dict) -> Path:
    """Persist one auditable JSON artifact for a completed workflow phase."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    artifact = {
        "phase": phase_number,
        "phase_name": phase_name,
        "run_id": RUN_ID,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        **payload,
    }
    path = OUTPUT_DIR / f"phase{phase_number}_{phase_name}.json"
    path.write_text(json.dumps(artifact, indent=2, default=str) + "\n", encoding="utf-8")
    print(f"  JSON artifact written: {path}", flush=True)
    return path

REVENUE_CONCEPTS = [
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "RevenueFromContractWithCustomerIncludingAssessedTax",
    "Revenues",
    "SalesRevenueNet",
    "SalesRevenueGoodsNet",
    "RegulatedAndUnregulatedOperatingRevenue",
    "OperatingRevenues",
    "Revenue",
]
ANNUAL_FORMS = {"10-K", "20-F", "40-F", "10-K/A", "20-F/A", "40-F/A"}
QUARTERLY_FORMS = {"10-Q", "10-Q/A"}

# HTTP client with rate limiting
client = httpx.Client(timeout=60.0, headers={"User-Agent": "OptionsWheelAgent/1.0 (contact@example.com)"})
_last_req = [0.0]

def rate_limit():
    el = time.time() - _last_req[0]
    if el < 0.11:
        time.sleep(0.11 - el)
    _last_req[0] = time.time()

def sec_get(url: str):
    rate_limit()
    r = client.get(url)
    r.raise_for_status()
    return r.json()

def get_company_facts(cik: str) -> Optional[dict]:
    cp = SEC_DIR / f"{cik}.json"
    if cp.exists() and (datetime.now().timestamp() - cp.stat().st_mtime) < 86400 * 3:
        with open(cp) as f:
            return json.load(f)
    try:
        facts = sec_get(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json")
        with open(cp, 'w') as f:
            json.dump(facts, f)
        return facts
    except Exception:
        return None

def compute_annual_yoy(facts: dict) -> Tuple[Optional[float], str]:
    all_facts = []
    for tax in ("us-gaap", "ifrs-full"):
        src = facts.get("facts", {}).get(tax, {})
        for concept in REVENUE_CONCEPTS:
            if concept not in src: continue
            for unit, arr in src[concept].get("units", {}).items():
                if unit != "USD": continue
                for f in arr:
                    if f.get("form") not in ANNUAL_FORMS: continue
                    end, val, filed, start = f.get("end"), f.get("val"), f.get("filed"), f.get("start")
                    if end and val is not None and filed and start:
                        try:
                            dur = (datetime.fromisoformat(end) - datetime.fromisoformat(start)).days
                            all_facts.append({"end": end, "val": float(val), "filed": filed, "dur": dur})
                        except: pass
    by_end = {}
    for f in all_facts:
        e = f["end"]
        if e not in by_end or (f["filed"] or "") > (by_end[e]["filed"] or ""):
            by_end[e] = f
    ann = sorted([f for f in by_end.values() if 250 <= f["dur"] <= 450], key=lambda x: x["end"], reverse=True)
    today = datetime.now(timezone.utc).date()
    if len(ann) < 2:
        return None, "ANNUAL FACT MISSING"
    latest, prev = ann[0], ann[1]
    le = datetime.fromisoformat(latest["end"]).date()
    pe = datetime.fromisoformat(prev["end"]).date()
    gap = (le - pe).days
    age = (today - le).days
    if age > 550: return None, "ANNUAL FACT STALE"
    if not (250 <= gap <= 500): return None, "ANNUAL PERIODS NOT COMPARABLE"
    if prev["val"] == 0: return None, "PRIOR REVENUE ZERO"
    g = latest["val"] / prev["val"] - 1
    if g > MIN_SEC_ANNUAL_YOY_REVENUE_GROWTH:
        return g, "PASS"
    return g, "ANNUAL YOY AT OR BELOW 15%"

def compute_quarterly_qoq(facts: dict) -> Tuple[Optional[float], str]:
    all_facts = []
    for tax in ("us-gaap", "ifrs-full"):
        src = facts.get("facts", {}).get(tax, {})
        for concept in REVENUE_CONCEPTS:
            if concept not in src: continue
            for unit, arr in src[concept].get("units", {}).items():
                if unit != "USD": continue
                for f in arr:
                    if f.get("form") not in QUARTERLY_FORMS: continue
                    end, val, filed = f.get("end"), f.get("val"), f.get("filed")
                    if end and val is not None and filed:
                        all_facts.append({"end": end, "val": float(val), "filed": filed})
    by_end = {}
    for f in all_facts:
        e = f["end"]
        if e not in by_end or (f["filed"] or "") > (by_end[e]["filed"] or ""):
            by_end[e] = f
    qtrs = sorted(by_end.values(), key=lambda x: x["end"], reverse=True)
    if len(qtrs) < 2:
        return None, "QUARTERLY FACT MISSING"
    latest, prev = qtrs[0], qtrs[1]
    le = datetime.fromisoformat(latest["end"]).date()
    pe = datetime.fromisoformat(prev["end"]).date()
    gap = (le - pe).days
    if not (60 <= gap <= 150):
        return None, "QUARTERLY PERIODS NOT COMPARABLE"
    if prev["val"] == 0: return None, "PRIOR REVENUE ZERO"
    g = latest["val"] / prev["val"] - 1
    return g, "PASS"

# ==================== SCANNER ====================
def run_single_scan() -> Tuple[Dict[str, dict], dict]:
    """Run one synchronous filtered scan and extract its rows.

    The complete create+run operation is retried at most two times after the
    initial attempt. A third failure is raised so the workflow cannot continue
    with a partial scanner universe.
    """
    print(f"  Running single scanner query (price < ${MAX_UNDERLYING_PRICE_USD:.2f})...", flush=True)
    filters = [
        {"filter_type": "FILTER_TYPE_INSTRUMENT_TYPE", "predicate": "=", "values": ["STOCK"]},
        {"filter_type": "FILTER_TYPE_AVERAGE_VOLUME", "interval": "1d", "length": 30, "predicate": ">", "values": [str(MIN_AVERAGE_VOLUME_SHARES)]},
        {"filter_type": "FILTER_TYPE_MARKET_CAP", "predicate": "BETWEEN", "values": [str(int(MIN_MARKET_CAP_USD)), str(int(MAX_MARKET_CAP_USD))]},
        {"filter_type": "FILTER_TYPE_IMPLIED_VOLATILITY", "predicate": "BETWEEN", "values": [str(MIN_ATM_PUT_IV), str(MAX_ATM_PUT_IV)]},
        {"filter_type": "FILTER_TYPE_LAST", "predicate": ">=", "values": ["0.01"]},
        {"filter_type": "FILTER_TYPE_LAST", "predicate": "<", "values": [str(MAX_UNDERLYING_PRICE_USD)]},
        {"filter_type": "FILTER_TYPE_RSI", "interval": "1d", "length": 14, "predicate": "<", "values": [str(CSP_MAX_RSI)]},
    ]

    last_error = None
    for attempt in range(1, SCAN_MAX_ATTEMPTS + 1):
        try:
            create_result = mcp__robinhood_trading__create_scan(
                filters=filters,
                title=f"CSP Single Scan <${MAX_UNDERLYING_PRICE_USD:.2f}",
            )
            create_data = parse_mcp_payload(create_result, "create_scan")
            create_scan_result = create_data.get("result")
            if not isinstance(create_scan_result, dict) or not create_scan_result.get("scan_id"):
                raise RuntimeError("create_scan returned no scan_id")
            scan_id = create_scan_result["scan_id"]

            # run_scan is synchronous: do not start extraction until its result
            # has returned.
            run_result = mcp__robinhood_trading__run_scan(scan_id=scan_id)
            run_data = parse_mcp_payload(run_result, "run_scan")
            scan_result = run_data.get("result")
            if not isinstance(scan_result, dict):
                raise RuntimeError("run_scan returned no result object")
            rows = scan_result.get("results", [])
            if not isinstance(rows, list):
                raise RuntimeError("run_scan results must be a list")
            total_items = scan_result.get("total_items")
            if total_items is not None:
                try:
                    total_items = int(total_items)
                except (TypeError, ValueError) as exc:
                    raise RuntimeError("run_scan total_items is not an integer") from exc
                if total_items != len(rows):
                    raise RuntimeError(
                        f"run_scan returned {len(rows)} rows but reported {total_items} total_items"
                    )
            print(f"  Single scanner query complete: {len(rows)} rows", flush=True)
            break
        except Exception as exc:
            last_error = exc
            if attempt == SCAN_MAX_ATTEMPTS:
                raise RuntimeError(
                    f"Single scanner query failed after {SCAN_MAX_ATTEMPTS} attempts"
                ) from exc
            print(
                f"  Scanner attempt {attempt}/{SCAN_MAX_ATTEMPTS} failed; "
                f"retrying in {SCAN_RETRY_DELAY_SECONDS} seconds: {exc}",
                flush=True,
            )
            time.sleep(SCAN_RETRY_DELAY_SECONDS)
    else:  # pragma: no cover - loop either breaks or raises
        raise RuntimeError("Single scanner query failed") from last_error

    universe = {}
    for row_number, item in enumerate(rows, start=1):
        if not isinstance(item, dict):
            raise RuntimeError(f"Scanner row {row_number} is not an object")
        ticker = item.get("ticker")
        cols = item.get("columns")
        if not isinstance(ticker, str) or not ticker.strip():
            raise RuntimeError(f"Scanner row {row_number} has no ticker")
        if ticker in universe:
            raise RuntimeError(f"Scanner returned duplicate ticker {ticker}")
        if not isinstance(cols, dict):
            raise RuntimeError(f"Scanner row {row_number} ({ticker}) has no columns object")
        try:
            price = float(cols["Last"])
            market_cap = float(cols["Market cap"])
            iv = float(cols["Implied volatility"])
            rsi = float(cols["RSI"])
            avg_vol = float(cols["Average volume"])
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError(
                f"Scanner row {row_number} ({ticker}) has invalid required columns: {exc}"
            ) from exc
        numeric_values = (price, market_cap, iv, rsi, avg_vol)
        if not all(math.isfinite(value) for value in numeric_values):
            raise RuntimeError(f"Scanner row {row_number} ({ticker}) contains non-finite values")
        if not (0.01 <= price < MAX_UNDERLYING_PRICE_USD):
            raise RuntimeError(f"Scanner row {row_number} ({ticker}) violates price filters")
        if not (MIN_MARKET_CAP_USD <= market_cap <= MAX_MARKET_CAP_USD):
            raise RuntimeError(f"Scanner row {row_number} ({ticker}) violates market-cap filters")
        if not (MIN_ATM_PUT_IV <= iv <= MAX_ATM_PUT_IV):
            raise RuntimeError(f"Scanner row {row_number} ({ticker}) violates IV filters")
        if not rsi < CSP_MAX_RSI:
            raise RuntimeError(f"Scanner row {row_number} ({ticker}) violates RSI filter")
        if not avg_vol > MIN_AVERAGE_VOLUME_SHARES:
            raise RuntimeError(f"Scanner row {row_number} ({ticker}) violates average-volume filter")
        universe[ticker] = {
            "price": price,
            "market_cap": market_cap,
            "iv": iv,
            "rsi": rsi,
            "avg_vol": avg_vol,
        }
    scan_metadata = {
        key: value
        for key, value in scan_result.items()
        if key != "results"
    }
    return universe, {
        "scan_id": scan_id,
        "rows": rows,
        "filters": filters,
        "metadata": scan_metadata,
        "complete": True,
    }

# ==================== FUNDAMENTALS ====================
def fetch_fundamentals(universe: Dict[str, dict]) -> dict:
    """Fetch reporting-only sector and P/E data with exact symbol reconciliation."""
    tickers = list(universe.keys())
    print(f"  Fetching fundamentals (sector, P/E) for {len(tickers)} tickers...", flush=True)
    returned_tickers = set()
    for i in range(0, len(tickers), FUNDAMENTALS_BATCH_SIZE):
        batch = tickers[i:i + FUNDAMENTALS_BATCH_SIZE]
        last_error = None
        for attempt in range(1, FUNDAMENTALS_MAX_ATTEMPTS + 1):
            try:
                fin_result = mcp__robinhood_trading__get_equity_fundamentals(
                    symbols=batch, bounds="regular"
                )
                fin_data = parse_mcp_payload(fin_result, "get_equity_fundamentals")
                results = fin_data.get("results")
                if not isinstance(results, list):
                    raise RuntimeError("get_equity_fundamentals returned no results list")
                by_symbol = {}
                for row in results:
                    if not isinstance(row, dict) or not row.get("symbol"):
                        raise RuntimeError("Fundamentals response contains a row without symbol")
                    symbol = str(row["symbol"]).upper()
                    if symbol in by_symbol:
                        raise RuntimeError(f"Fundamentals response duplicated {symbol}")
                    by_symbol[symbol] = row
                missing = [ticker for ticker in batch if ticker not in by_symbol]
                unexpected = sorted(set(by_symbol).difference(batch))
                if missing or unexpected:
                    raise RuntimeError(
                        f"Fundamentals symbol mismatch: missing={missing}, unexpected={unexpected}"
                    )
                break
            except Exception as exc:
                last_error = exc
                if attempt == FUNDAMENTALS_MAX_ATTEMPTS:
                    raise RuntimeError(
                        f"Fundamentals batch {batch} failed after "
                        f"{FUNDAMENTALS_MAX_ATTEMPTS} attempts"
                    ) from exc
                print(
                    f"    Fundamentals attempt {attempt}/{FUNDAMENTALS_MAX_ATTEMPTS} "
                    f"failed; retrying in {FUNDAMENTALS_RETRY_DELAY_SECONDS} seconds: {exc}",
                    flush=True,
                )
                time.sleep(FUNDAMENTALS_RETRY_DELAY_SECONDS)
        else:  # pragma: no cover
            raise RuntimeError("Fundamentals batch failed") from last_error

        for ticker in batch:
            row = by_symbol[ticker]
            sector = row.get("sector") or "NOT RETURNED"
            pe_raw = row.get("pe_ratio")
            try:
                pe = float(pe_raw) if pe_raw not in (None, "") else None
                if pe is not None and not math.isfinite(pe):
                    pe = None
            except (TypeError, ValueError):
                pe = None
            universe[ticker]["sector"] = sector
            universe[ticker]["pe"] = pe
            returned_tickers.add(ticker)

    if returned_tickers != set(tickers):
        raise RuntimeError("Fundamentals reconciliation failed after all batches")
    return {
        "requested_count": len(tickers),
        "returned_count": len(returned_tickers),
        "sector_returned_count": sum(
            universe[ticker].get("sector") != "NOT RETURNED" for ticker in tickers
        ),
        "pe_returned_count": sum(universe[ticker].get("pe") is not None for ticker in tickers),
        "batch_size": FUNDAMENTALS_BATCH_SIZE,
    }

# ==================== REVENUE GATES (Yahoo Finance via yfinance) ====================
def _yf_revenue_frame(ticker: str, quarterly: bool = False):
    """Return Yahoo Finance income-statement data for one ticker."""
    try:
        yf_ticker = yf.Ticker(ticker)
        frame = yf_ticker.quarterly_financials if quarterly else yf_ticker.financials
        if frame is None or frame.empty:
            return None
        return frame
    except Exception as exc:
        print(f"    Warning: yfinance revenue retrieval failed for {ticker}: {exc}", flush=True)
        return None


def _yf_revenue_series(frame):
    """Select a whole-company revenue row and return (period, value) pairs."""
    preferred = [
        "Total Revenue", "Operating Revenue", "Operating Revenues",
        "Revenue", "Revenues", "Sales Revenue",
    ]
    normalized = {str(index).strip().lower(): index for index in frame.index}
    row = None
    for name in preferred:
        row = normalized.get(name.lower())
        if row is not None:
            break
    if row is None:
        # Conservative fallback: only accept an unambiguous revenue-labelled row.
        matches = [idx for idx in frame.index if "revenue" in str(idx).lower()]
        if len(matches) == 1:
            row = matches[0]
    if row is None:
        return []

    values = []
    for period, value in frame.loc[row].items():
        try:
            if value is None:
                continue
            number = float(value)
            if number != number or number in (float("inf"), float("-inf")):
                continue
            values.append((datetime.fromtimestamp(period.timestamp(), timezone.utc).date(), number))
        except Exception:
            continue
    return sorted(values, key=lambda item: item[0], reverse=True)


def _yf_country(ticker: str) -> Optional[str]:
    """Return Yahoo Finance's normalized country code for one ticker."""
    try:
        info = yf.Ticker(ticker).info
        raw_country = info.get("country") if isinstance(info, dict) else None
        if raw_country is None:
            return None
        country = str(raw_country).strip().upper()
        if country in {"US", "USA", "UNITED STATES", "UNITED STATES OF AMERICA"}:
            return "US"
        return country or None
    except Exception as exc:
        print(f"    Warning: yfinance country retrieval failed for {ticker}: {exc}", flush=True)
        return None


def compute_yf_annual_yoy(ticker: str) -> Tuple[Optional[float], str]:
    frame = _yf_revenue_frame(ticker, quarterly=False)
    if frame is None:
        return None, "YFINANCE RETRIEVAL FAILURE"
    annual = _yf_revenue_series(frame)
    if len(annual) < 2:
        return None, "ANNUAL FACT MISSING"
    latest, previous = annual[0], annual[1]
    gap = (latest[0] - previous[0]).days
    age = (datetime.now(timezone.utc).date() - latest[0]).days
    if age > 550:
        return None, "ANNUAL FACT STALE"
    if not (250 <= gap <= 500):
        return None, "ANNUAL PERIODS NOT COMPARABLE"
    if previous[1] == 0:
        return None, "PRIOR REVENUE ZERO"
    growth = latest[1] / previous[1] - 1
    if growth > MIN_SEC_ANNUAL_YOY_REVENUE_GROWTH:
        return growth, "PASS"
    return growth, "ANNUAL YOY AT OR BELOW 15%"


def compute_yf_quarterly_qoq(ticker: str) -> Tuple[Optional[float], str]:
    frame = _yf_revenue_frame(ticker, quarterly=True)
    if frame is None:
        return None, "YFINANCE RETRIEVAL FAILURE"
    quarters = _yf_revenue_series(frame)
    if len(quarters) < 2:
        return None, "QUARTERLY FACT MISSING"
    latest, previous = quarters[0], quarters[1]
    gap = (latest[0] - previous[0]).days
    if not (60 <= gap <= 150):
        return None, "QUARTERLY PERIODS NOT COMPARABLE"
    if previous[1] == 0:
        return None, "PRIOR REVENUE ZERO"
    return latest[1] / previous[1] - 1, "PASS"


def yfinance_annual_yoy_gate(universe: Dict[str, dict]) -> List[str]:
    print("  Running annual YoY revenue + US-country gate via yfinance...", flush=True)
    passed = []
    for i, (ticker, data) in enumerate(universe.items(), 1):
        country = _yf_country(ticker)
        country_pass = country == REQUIRED_COUNTRY
        growth = None
        reason = "YFINANCE RETRIEVAL FAILURE"
        attempts = 0
        for attempts in range(1, YFINANCE_MAX_ATTEMPTS + 1):
            growth, reason = compute_yf_annual_yoy(ticker)
            if reason != "YFINANCE RETRIEVAL FAILURE":
                break
            if attempts < YFINANCE_MAX_ATTEMPTS:
                time.sleep(YFINANCE_RETRY_DELAY_SECONDS)
        data["country"] = country
        data["country_pass"] = country_pass
        data["fy_yoy"] = growth
        data["fy_yoy_reason"] = reason
        data["fy_yoy_attempts"] = attempts
        data["revenue_source"] = "Yahoo Finance via yfinance"
        if (
            country_pass
            and growth is not None
            and growth > MIN_SEC_ANNUAL_YOY_REVENUE_GROWTH
        ):
            passed.append(ticker)
        if i % 25 == 0:
            print(f"    {i}/{len(universe)} checked, {len(passed)} pass", flush=True)
    print(f"  yfinance annual YoY + US-country gate PASS: {len(passed)}", flush=True)
    return passed


def yfinance_quarterly_qoq(universe: Dict[str, dict], passed: List[str]):
    print("  Computing quarterly QoQ revenue via yfinance...", flush=True)
    for ticker in passed:
        growth = None
        reason = "YFINANCE RETRIEVAL FAILURE"
        attempts = 0
        for attempts in range(1, YFINANCE_MAX_ATTEMPTS + 1):
            growth, reason = compute_yf_quarterly_qoq(ticker)
            if reason != "YFINANCE RETRIEVAL FAILURE":
                break
            if attempts < YFINANCE_MAX_ATTEMPTS:
                time.sleep(YFINANCE_RETRY_DELAY_SECONDS)
        universe[ticker]["fy_qoq"] = growth
        universe[ticker]["fy_qoq_reason"] = reason
        universe[ticker]["fy_qoq_attempts"] = attempts
        universe[ticker]["revenue_source"] = "Yahoo Finance via yfinance"

# ==================== TECHNICALS ====================
def _latest_indicator(
    *,
    ticker: str,
    indicator_type: str,
    period: int,
    start_time: str,
) -> dict:
    """Fetch and validate one latest technical-indicator point."""
    last_error = None
    for attempt in range(1, TECHNICAL_MAX_ATTEMPTS + 1):
        try:
            response = mcp__robinhood_trading__get_equity_technical_indicators(
                symbol=ticker,
                type=indicator_type,
                period=period,
                interval="day",
                start_time=start_time,
                output="latest",
            )
            data = parse_mcp_payload(response, "get_equity_technical_indicators")
            results = data.get("results")
            if not isinstance(results, list) or len(results) != 1:
                raise RuntimeError(
                    "Technical response must contain exactly one result"
                )
            result = results[0]
            if not isinstance(result, dict):
                raise RuntimeError("Technical response result must be an object")
            if result.get("symbol") != ticker:
                raise RuntimeError(
                    f"Technical response symbol mismatch: expected {ticker}, got {result.get('symbol')}"
                )
            indicators = result.get("indicators")
            if not isinstance(indicators, list) or len(indicators) != 1:
                raise RuntimeError("Technical response must contain exactly one indicator")
            indicator = indicators[0]
            if indicator.get("type") != indicator_type:
                raise RuntimeError(
                    f"Technical type mismatch for {ticker}: {indicator.get('type')}"
                )
            series = indicator.get("series")
            if not isinstance(series, list) or len(series) != 1:
                raise RuntimeError("Latest technical response must contain exactly one point")
            return {
                "point": series[0],
                "attempts": attempt,
                "interval": result.get("interval"),
                "bounds": result.get("bounds"),
            }
        except Exception as exc:
            last_error = exc
            if attempt < TECHNICAL_MAX_ATTEMPTS:
                time.sleep(TECHNICAL_RETRY_DELAY_SECONDS)
    raise RuntimeError(
        f"{indicator_type}({period}) failed for {ticker} after "
        f"{TECHNICAL_MAX_ATTEMPTS} attempts"
    ) from last_error


def _finite_indicator_value(value: Any, *, ticker: str, field: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"{ticker} {field} is missing or non-numeric") from exc
    if not math.isfinite(number):
        raise RuntimeError(f"{ticker} {field} is non-finite")
    return number


def fetch_technicals(universe: Dict[str, dict], tickers: List[str]) -> dict:
    print(f"  Fetching technical indicators for {len(tickers)} tickers...", flush=True)
    now = datetime.now(timezone.utc)
    start_50 = (now - timedelta(days=400)).strftime("%Y-%m-%dT%H:%M:%SZ")
    start_200 = (now - timedelta(days=800)).strftime("%Y-%m-%dT%H:%M:%SZ")
    start_bb = (now - timedelta(days=100)).strftime("%Y-%m-%dT%H:%M:%SZ")
    completed = []
    unresolved = []

    for t in tickers:
        try:
            sma50 = _latest_indicator(
                ticker=t, indicator_type="sma", period=SMA_MEDIUM_PERIOD_SESSIONS,
                start_time=start_50,
            )
            sma200 = _latest_indicator(
                ticker=t, indicator_type="sma", period=SMA_LONG_PERIOD_SESSIONS,
                start_time=start_200,
            )
            bb = _latest_indicator(
                ticker=t, indicator_type="bollinger_bands",
                period=BOLLINGER_PERIOD_SESSIONS, start_time=start_bb,
            )
            sma50_val = _finite_indicator_value(
                sma50["point"].get("value"), ticker=t, field="sma_50"
            )
            sma200_val = _finite_indicator_value(
                sma200["point"].get("value"), ticker=t, field="sma_200"
            )
            bb_latest = bb["point"]
            bb_lower = _finite_indicator_value(
                bb_latest.get("lower"), ticker=t, field="bb_lower"
            )
            bb_middle = _finite_indicator_value(
                bb_latest.get("middle"), ticker=t, field="bb_middle"
            )
            bb_upper = _finite_indicator_value(
                bb_latest.get("upper"), ticker=t, field="bb_upper"
            )
            if not bb_lower < bb_upper:
                raise RuntimeError(f"{t} Bollinger lower band is not below upper band")
            universe[t].update({
                "sma_50": sma50_val,
                "sma_200": sma200_val,
                "bb_lower": bb_lower,
                "bb_middle": bb_middle,
                "bb_upper": bb_upper,
                "technical_status": "PASS",
                "technical_error": None,
                "technical_attempts": {
                    "sma_50": sma50["attempts"],
                    "sma_200": sma200["attempts"],
                    "bollinger_bands": bb["attempts"],
                },
                "technical_as_of": {
                    "sma_50": sma50["point"].get("begins_at"),
                    "sma_200": sma200["point"].get("begins_at"),
                    "bollinger_bands": bb_latest.get("begins_at"),
                },
            })
            completed.append(t)
        except Exception as exc:
            print(f"    Warning: Technical indicators failed for {t}: {exc}", flush=True)
            universe[t].update({
                "sma_50": None, "sma_200": None, "bb_lower": None,
                "bb_middle": None, "bb_upper": None,
                "technical_status": "UNRESOLVED", "technical_error": str(exc),
            })
            unresolved.append(t)
    return {
        "requested_count": len(tickers),
        "completed_count": len(completed),
        "unresolved_count": len(unresolved),
        "completed_tickers": completed,
        "unresolved_tickers": unresolved,
    }

# ==================== POST-SCREEN ====================
TECHNICAL_CONDITION_KEYS = ("B", "200", "50")
MIN_MET_TECHNICAL_CONDITIONS = 2


def build_technical_conditions(*, bb_met: bool, sma200_met: bool, sma50_met: bool) -> dict:
    """Return the stable Phase 4 condition contract in B/200/50 order."""
    return {
        "B": "MET" if bb_met else "NOT MET",
        "200": "MET" if sma200_met else "NOT MET",
        "50": "MET" if sma50_met else "NOT MET",
    }


def select_phase5_tickers(universe: Dict[str, dict], technical_pass_tickers: List[str]) -> List[str]:
    """Filter and order the Phase 4 records passed into Phase 5.

    A ticker must have at least two MET technical conditions. Ordering is
    deterministic: market cap descending, price descending, then the number
    of MET conditions descending (3 before 2).
    """
    eligible = []
    for ticker in technical_pass_tickers:
        record = universe[ticker]
        conditions = record.get("conditions", {})
        met_count = sum(1 for key in TECHNICAL_CONDITION_KEYS if conditions.get(key) == "MET")
        record["conditions_met"] = met_count
        # Disabled per user request: do not require at least 2 of 3
        # technical conditions to be MET for Phase 5 eligibility.
        # if met_count >= MIN_MET_TECHNICAL_CONDITIONS:
        eligible.append(ticker)

    eligible.sort(
        key=lambda ticker: (
            -(float(universe[ticker].get("market_cap") or 0)),
            -(float(universe[ticker].get("price") or 0)),
            -int(universe[ticker].get("conditions_met") or 0),
        )
    )
    return eligible


def evaluate_post_screen(universe: Dict[str, dict], passed: List[str]) -> List[str]:
    print("  Evaluating post-screen filters...", flush=True)
    technical_pass_tickers = []
    for t in passed:
        d = universe[t]
        p = d["price"]
        rsi = d["rsi"]
        fy_growth = d.get("fy_yoy")
        
        sma50 = d.get("sma_50")
        sma200 = d.get("sma_200")
        bb_lower = d.get("bb_lower")
        bb_middle = d.get("bb_middle")
        bb_upper = d.get("bb_upper")
        
        rev_ok = fy_growth is not None and fy_growth > MIN_SEC_ANNUAL_YOY_REVENUE_GROWTH
        rsi_ok = rsi < CSP_MAX_RSI
        
        sma50_dist = (p - sma50) / sma50 if sma50 and sma50 > 0 else 1.0
        sma200_dist = (p - sma200) / sma200 if sma200 and sma200 > 0 else 1.0
        bb_width = bb_upper - bb_lower if d.get("bb_upper") and d.get("bb_lower") else 0
        bb_pct_b = (p - bb_lower) / bb_width if bb_width and bb_width > 0 else 1.0
        
        sma50_met = sma50_dist <= MAX_SMA_50_DISTANCE
        sma200_met = sma200_dist <= MAX_SMA_200_DISTANCE
        bb_met = bb_pct_b <= MAX_BOLLINGER_PERCENT_B
        tech_or = "T" if (sma50_met or sma200_met or bb_met) else "F"
        
        flag_parts = []
        if tech_or == "T":
            if bb_met: flag_parts.append("B")
            if sma200_met: flag_parts.append("200")
            if sma50_met: flag_parts.append("50")
        tech_or_flag = "|".join(flag_parts)
        conditions = build_technical_conditions(
            bb_met=bb_met,
            sma200_met=sma200_met,
            sma50_met=sma50_met,
        )
        conditions_met = sum(value == "MET" for value in conditions.values())
        post_screen_pass = rev_ok and rsi_ok and tech_or == "T"
        d.update({
            "market_cap": d.get("market_cap"),
            "bb_pct_b": bb_pct_b, "sma_50_dist": sma50_dist, "sma_200_dist": sma200_dist,
            "sma_50_met": sma50_met, "sma_200_met": sma200_met, "bb_met": bb_met,
            "tech_or_group": tech_or, "tech_or_flag": tech_or_flag,
            "conditions": conditions, "conditions_met": conditions_met,
            "post_screen_pass": post_screen_pass, "overall_pass": 1 if post_screen_pass else 0,
        })
        if post_screen_pass:
            technical_pass_tickers.append(t)
    passing_tickers = select_phase5_tickers(universe, technical_pass_tickers)
    print(
        f"  {len(technical_pass_tickers)} tickers passed technical/revenue post-screen; "
        f"{len(passing_tickers)} passed the >= {MIN_MET_TECHNICAL_CONDITIONS}-MET Phase 5 filter",
        flush=True,
    )
    return passing_tickers


# ==================== VALUATION HANDOFF ====================
def prepare_valuation_mcp_adapter() -> Path:
    """Phase 5 adapter preparation (D-05).

    The source writes a hermes_tools.py runtime adapter so the
    placeholder valuation CLI subprocess can reach the MCP runtime.
    In this repo Phase 5 runs IN-PROCESS via scripts/valuation_scorer.py
    (dashboard engine), so there is no subprocess and no hermes_tools
    adapter to write. This keeps the source call site working: it
    verifies the adapter module exists and returns its path for the
    Phase 5 artifact's runtime_adapter field.
    """
    adapter_path = Path(__file__).resolve().parent / "valuation_scorer.py"
    if not adapter_path.exists():
        raise RuntimeError(f"Phase 5 adapter missing: {adapter_path}")
    return adapter_path


def _format_market_cap(value: Any) -> Optional[str]:
    """Format a finite market capitalization using T/B/M display units."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    if number >= 1_000_000_000_000:
        return f"${number / 1_000_000_000_000:.2f}T"
    if number >= 1_000_000_000:
        return f"${number / 1_000_000_000:.2f}B"
    if number >= 1_000_000:
        return f"${number / 1_000_000:.2f}M"
    return f"${number:,.0f}"


def _valuation_market_cap(result: dict, phase4_record: dict) -> Any:
    """Prefer the valuation run's market cap, then the Phase 4 value."""
    for modifier in result.get("modifiers") or []:
        if modifier.get("name") == "funding":
            value = (modifier.get("details") or {}).get("market_cap")
            if value is not None:
                return value
    return phase4_record.get("market_cap")


def build_phase5_valuation_rows(
    universe: Dict[str, dict],
    input_tickers: List[str],
    valuation_result: dict,
) -> Tuple[List[dict], dict]:
    """Build the complete Phase 5 row schema and composite-score ordering.

    Phase 5 never truncates to a top-N set. Every input receives one row. A
    later phase may select from these already-sorted rows.

    DEVIATION D-05: the composite ``score`` is Final-Code from the
    dashboard adapter (result.final_score =
    round(0.5*business_quality + 0.25*valuation + 0.25*GAV-Code, 1)),
    replacing the source's confidence-weighted composite
    (medium: 0.30*valuation + 0.70*business_quality; high:
    0.40*valuation + 0.60*business_quality; low/unavailable: None).
    Row schema and ordering (score desc, ticker asc, unscored last)
    are the source's.
    """
    records = valuation_result.get("records") or []
    by_ticker = {str(record.get("ticker") or "").upper(): record for record in records}
    normalized_inputs = [ticker.strip().upper() for ticker in input_tickers]
    if len(by_ticker) != len(records) or set(by_ticker) != set(normalized_inputs):
        raise RuntimeError("Phase 5 valuation records do not reconcile one-to-one with input")

    rows = []
    confidence_counts = {}
    status_counts = {}
    for ticker in normalized_inputs:
        phase4_record = universe[ticker]
        terminal = by_ticker[ticker]
        result = terminal.get("result")
        if not isinstance(result, dict) or not result:
            # DEVIATION D-17 (revived 2026-11-11 at BIG SANDY's instruction
            # after LEGN, a 20-F foreign filer with no 10-K, killed a live
            # run at this merge): a terminal insufficient_data record is a
            # genuine no-data outcome (no 10-K / shell / recent IPO), not a
            # retrieval failure. Carry it as a structured unscored row —
            # same schema, scores None, failure_reason preserved — so one
            # no-data name no longer stops the whole cohort. Any other
            # terminal status with a missing result still fails closed.
            if str(terminal.get("status") or "") != "insufficient_data":
                raise RuntimeError(
                    f"Phase 5 valuation result missing for {ticker}; "
                    f"terminal_status={terminal.get('status')!r}, "
                    "valuation package output was not retrieved"
                )
            result = {}
        business_quality = result.get("business_quality") or {}
        valuation_block = result.get("valuation") or {}
        status = str(terminal.get("status") or result.get("status") or "unknown")
        confidence = result.get("confidence") or terminal.get("confidence")
        confidence_key = str(confidence or "unavailable").lower()
        confidence_counts[confidence_key] = confidence_counts.get(confidence_key, 0) + 1
        status_counts[status] = status_counts.get(status, 0) + 1

        valuation_score = valuation_block.get("score")
        business_quality_score = business_quality.get("score")
        gav_code = result.get("gav_code") or {}
        composite_score = None
        try:
            final_number = float(result.get("final_score"))
        except (TypeError, ValueError):
            final_number = None
        if (
            status == "ok"
            and final_number is not None
            and math.isfinite(final_number)
        ):
            composite_score = round(final_number, 1)

        market_cap_raw = _valuation_market_cap(result, phase4_record)
        rows.append({
            "ticker": ticker,
            "market_cap": _format_market_cap(market_cap_raw),
            "market_cap_raw": market_cap_raw,
            "current_price": phase4_record.get("price"),
            "fy_yoy": phase4_record.get("fy_yoy"),
            "fy_qoq": phase4_record.get("fy_qoq"),
            "valuation_score": valuation_score,
            "valuation_grade": valuation_block.get("grade"),
            "valuation_grade_explanation": valuation_block.get("explanation"),
            "business_quality_score": business_quality_score,
            "business_quality_grade": business_quality.get("grade"),
            "business_quality_grade_explanation": business_quality.get(
                "explanation"
            ),
            "gav_code_score": gav_code.get("score"),
            "gav_code_label": gav_code.get("label"),
            "gav_code_source": gav_code.get("source")
                               or gav_code.get("growth_source"),
            "fallback": result.get("fallback"),
            "final_code": composite_score,
            "score": composite_score,
            "classification": result.get("classification"),
            "confidence": confidence,
            "regime": result.get("regime"),
            "conditions": phase4_record.get("conditions"),
            "data_cutoff_at": result.get("as_of") or result.get("data_cutoff_at"),
            "run_id": result.get("run_id"),
            "contract_version": result.get("contract_version"),
            "methodology_version": result.get("methodology_version"),
            "failure_reason": result.get("failure_reason")
                              or terminal.get("failure_reason"),
        })

    rows.sort(key=lambda row: (
        row["score"] is None,
        -(row["score"] if row["score"] is not None else 0.0),
        row["ticker"],
    ))
    summary = {
        "input_count": len(normalized_inputs),
        "row_count": len(rows),
        "scored_count": sum(row["score"] is not None for row in rows),
        "unscored_count": sum(row["score"] is None for row in rows),
        "status_counts": status_counts,
        "valuation_confidence_counts": confidence_counts,
        "ordering": ["score_desc", "ticker_asc", "unscored_last"],
        "composite_formula": {
            "final_code": (
                "round(0.5 * business_quality_score + 0.25 * valuation_score "
                "+ 0.25 * gav_code_score, 1)"
            ),
            "engine": ("muse-business-evaluation-dashboard V5.3 + GAV-Code "
                       "(in-process adapter, D-05)"),
            "replaced_source_formula": {
                "medium_valuation_confidence": (
                    "0.30 * valuation_score + 0.70 * business_quality_score"
                ),
                "high_valuation_confidence": (
                    "0.40 * valuation_score + 0.60 * business_quality_score"
                ),
                "low_or_unavailable_valuation_confidence": None,
            },
        },
    }
    return rows, summary

# ==================== OPTION CONTRACTS ====================
def fetch_option_contracts(
    universe: Dict[str, dict],
    passing_tickers: List[str],
    min_option_dte: int = MIN_OPTION_DTE,
    max_option_dte: int = MAX_OPTION_DTE,
) -> Dict[str, List[dict]]:
    print(f"  Fetching option contracts for {len(passing_tickers)} tickers...", flush=True)
    all_contracts = {}
    for t in passing_tickers:
        contracts = []
        try:
            chains = mcp__robinhood_trading__get_option_chains(underlying_symbol=t)
            chain_data = json.loads(chains["result"])
            p = universe[t]["price"]
            
            for chain in chain_data["data"]["results"]:
                if not chain.get("can_open_position"): continue
                if chain.get("trade_value_multiplier") != 100: continue
                
                exp_date = chain["expiration_date"]
                exp_dt = datetime.strptime(exp_date, "%Y-%m-%d").date()
                today = datetime.now().date()
                dte = (exp_dt - today).days
                if not (min_option_dte <= dte <= max_option_dte): continue
                
                inst = mcp__robinhood_trading__get_option_instruments(chain_id=chain["id"], expiration_dates=exp_date, type="put")
                inst_data = json.loads(inst["result"])
                
                for opt in inst_data["data"]["results"]:
                    if opt["state"] != "active": continue
                    strike = float(opt["strike_price"])
                    if not (0.90 <= strike / p <= 1.00): continue
                    
                    option_id = opt["id"]
                    quote = mcp__robinhood_trading__get_option_quotes(instrument_ids=[option_id])
                    qdata = json.loads(quote["result"])
                    
                    if qdata["data"]["results"]:
                        q = qdata["data"]["results"][0]
                        bid = float(q.get("bid_price", 0) or 0)
                        ask = float(q.get("ask_price", 0) or 0)
                        delta = float(q.get("delta", 0) or 0) if q.get("delta") else None
                        oi = int(q.get("open_interest", 0) or 0)
                        vol = int(q.get("volume", 0) or 0)
                        
                        if bid > 0 and ask > 0:
                            mid = (bid + ask) / 2
                            spread = (ask - bid) / mid if mid > 0 else 1.0
                            premium = mid
                            roc = (premium / strike) * 100 if strike > 0 else 0
                            pop = 1 - abs(delta) if delta else 0.65
                            
                            qualifies = int(
                                MIN_CSP_SHORT_POP <= pop <= MAX_CSP_SHORT_POP and
                                delta is not None and bid > 0 and mid > 0 and
                                spread <= MAX_BID_ASK_SPREAD_RATIO and strike < p
                            )
                            
                            contracts.append({
                                "option_id": option_id, "strike": strike, "expiration": exp_date,
                                "dte": dte, "delta": delta, "premium": premium,
                                "roc": roc, "pop": pop, "spread_pct": spread,
                                "oi": oi, "volume": vol, "qualifies": qualifies
                            })
        except Exception as e:
            print(f"    Warning: Option chain failed for {t}: {e}", flush=True)
        all_contracts[t] = contracts
    return all_contracts

# ==================== RANKING ====================
def rank_csp_recommendations(universe: Dict[str, dict], contracts_by_ticker: Dict[str, List[dict]]) -> List[dict]:
    """Rank CSPs per master-value-screen-csp-recommend.md: sort by ROC desc, then premium desc."""
    ranked = []
    for t, contracts in contracts_by_ticker.items():
        qualifying = [c for c in contracts if c["qualifies"]]
        if not qualifying:
            continue
        # Best contract per ticker (max ROC, then max premium)
        best = max(qualifying, key=lambda c: (c["roc"], c["premium"]))
        p = universe[t]["price"]
        cltr = strike = best["strike"]
        total_premium = best["premium"] * STANDARD_OPTION_CONTRACT_SHARES
        ret_pct = best["roc"]
        ranked.append({
            "ticker": t, "price": p, "strike": strike, "expiration": best["expiration"],
            "dte": best["dte"], "premium_per_contract": best["premium"],
            "total_premium": total_premium, "collateral": cltr,
            "return_pct": ret_pct, "pop": best["pop"], "delta": best["delta"],
            "spread_pct": best["spread_pct"], "oi": best["oi"], "volume": best["volume"]
        })
    # Sort: ROC desc, then premium desc
    ranked.sort(key=lambda x: (x["return_pct"], x["total_premium"]), reverse=True)
    return ranked[:MAX_CSP_CANDIDATES]

def qualify_options_with_dte_fallback(
    universe: Dict[str, dict], passing_tickers: List[str]
) -> Tuple[Dict[str, List[dict]], List[dict], dict]:
    """Qualify 30-45 DTE first, then retry once with 25-60 only if empty."""
    dte_window = {"min_dte": MIN_OPTION_DTE, "max_dte": MAX_OPTION_DTE, "fallback_used": False}
    contracts_by_ticker = fetch_option_contracts(universe, passing_tickers, MIN_OPTION_DTE, MAX_OPTION_DTE)
    ranked = rank_csp_recommendations(universe, contracts_by_ticker)
    if ranked:
        return contracts_by_ticker, ranked, dte_window
    dte_window = {"min_dte": FALLBACK_MIN_OPTION_DTE, "max_dte": FALLBACK_MAX_OPTION_DTE, "fallback_used": True}
    contracts_by_ticker = fetch_option_contracts(universe, passing_tickers, FALLBACK_MIN_OPTION_DTE, FALLBACK_MAX_OPTION_DTE)
    ranked = rank_csp_recommendations(universe, contracts_by_ticker)
    return contracts_by_ticker, ranked, dte_window

def _finite_number(value: Any, field: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} is not numeric") from exc
    if not math.isfinite(number):
        raise ValueError(f"{field} is not finite")
    return number


def _parse_utc_timestamp(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} is missing")
    text = value.strip().replace("Z", "+00:00")
    if "." in text:
        prefix, suffix = text.split(".", 1)
        zone_index = max(suffix.find("+"), suffix.find("-"))
        if zone_index >= 0:
            fraction, zone = suffix[:zone_index], suffix[zone_index:]
        else:
            fraction, zone = suffix, ""
        text = f"{prefix}.{fraction[:6]}{zone}"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        raise ValueError(f"{field} has no timezone")
    return parsed.astimezone(timezone.utc)


def _quote_age_seconds(updated_at: Any, now_utc: datetime) -> float:
    age = (now_utc - _parse_utc_timestamp(updated_at, "updated_at")).total_seconds()
    if age < 0:
        raise ValueError("quote timestamp is in the future")
    return age


def _extract_next_cursor(data: dict) -> Optional[str]:
    cursor = data.get("next_cursor") or data.get("cursor")
    if cursor:
        return str(cursor)
    next_url = data.get("next")
    if isinstance(next_url, str) and "cursor=" in next_url:
        return next_url.split("cursor=", 1)[1].split("&", 1)[0]
    return None


def _fetch_option_instruments_complete(
    chain_id: str, expiration: str
) -> Tuple[List[dict], dict]:
    instruments: List[dict] = []
    cursors_seen = set()
    cursor = None
    page_count = 0
    while True:
        arguments = {
            "chain_id": chain_id,
            "expiration_dates": expiration,
            "type": "put",
        }
        if cursor:
            arguments["cursor"] = cursor
        data = parse_mcp_payload(
            mcp__robinhood_trading__get_option_instruments(**arguments),
            "get_option_instruments",
        )
        page_count += 1
        page = data.get("instruments")
        if not isinstance(page, list):
            raise RuntimeError("get_option_instruments returned no instruments list")
        instruments.extend(page)
        cursor = _extract_next_cursor(data)
        if not cursor:
            break
        if cursor in cursors_seen:
            raise RuntimeError("option instrument pagination repeated a cursor")
        cursors_seen.add(cursor)
    ids = [instrument.get("id") for instrument in instruments]
    if any(not isinstance(option_id, str) or not option_id for option_id in ids):
        raise RuntimeError("option instrument response contains a missing ID")
    if len(ids) != len(set(ids)):
        raise RuntimeError("option instrument response contains duplicate IDs")
    return instruments, {"pages": page_count, "returned": len(instruments)}


def _fetch_option_quotes_reconciled(option_ids: List[str]) -> Tuple[Dict[str, dict], dict]:
    quotes: Dict[str, dict] = {}
    batch_count = 0
    for start in range(0, len(option_ids), 20):
        requested = option_ids[start:start + 20]
        data = parse_mcp_payload(
            mcp__robinhood_trading__get_option_quotes(instrument_ids=requested),
            "get_option_quotes",
        )
        batch_count += 1
        results = data.get("results")
        if not isinstance(results, list):
            raise RuntimeError("get_option_quotes returned no results list")
        returned = []
        for item in results:
            quote = item.get("quote") if isinstance(item, dict) else None
            option_id = quote.get("instrument_id") if isinstance(quote, dict) else None
            if not isinstance(option_id, str) or not option_id:
                raise RuntimeError("option quote is missing instrument_id")
            if option_id in quotes:
                raise RuntimeError(f"duplicate option quote for {option_id}")
            if option_id not in requested:
                raise RuntimeError(f"unexpected option quote for {option_id}")
            quotes[option_id] = quote
            returned.append(option_id)
        missing = sorted(set(requested).difference(returned))
        if missing:
            raise RuntimeError(
                "get_option_quotes silently omitted requested IDs: " + ", ".join(missing)
            )
    return quotes, {
        "requested": len(option_ids),
        "returned": len(quotes),
        "batches": batch_count,
    }


def _fetch_underlying_quotes_reconciled(tickers: List[str], now_utc: datetime) -> Dict[str, dict]:
    data = parse_mcp_payload(
        mcp__robinhood_trading__get_equity_quotes(symbols=tickers),
        "get_equity_quotes",
    )
    results = data.get("results")
    if not isinstance(results, list):
        raise RuntimeError("get_equity_quotes returned no results list")
    reconciled: Dict[str, dict] = {}
    for item in results:
        quote = item.get("quote") if isinstance(item, dict) else None
        symbol = str(quote.get("symbol", "")).upper() if isinstance(quote, dict) else ""
        if symbol not in tickers:
            raise RuntimeError(f"unexpected underlying quote for {symbol or 'UNKNOWN'}")
        if symbol in reconciled:
            raise RuntimeError(f"duplicate underlying quote for {symbol}")
        price = _finite_number(quote.get("last_trade_price"), "last_trade_price")
        if price <= 0:
            raise RuntimeError(f"non-positive underlying price for {symbol}")
        quote_age = _quote_age_seconds(
            quote.get("venue_last_trade_time"), datetime.now(timezone.utc)
        )
        if quote_age > QUOTE_MAX_AGE_SECONDS:
            raise RuntimeError(f"stale underlying quote for {symbol}: {quote_age:.1f}s")
        reconciled[symbol] = {
            "current_price": price,
            "quoted_at": quote.get("venue_last_trade_time"),
            "quote_age_seconds": round(quote_age, 3),
        }
    missing = sorted(set(tickers).difference(reconciled))
    if missing:
        raise RuntimeError("missing underlying quotes: " + ", ".join(missing))
    return reconciled


def _expiration_distance_from_strict_window(dte: int) -> int:
    if dte < MIN_OPTION_DTE:
        return MIN_OPTION_DTE - dte
    if dte > MAX_OPTION_DTE:
        return dte - MAX_OPTION_DTE
    return 0


def _qualify_options_window_hardened(
    universe: Dict[str, dict],
    input_tickers: List[str],
    buying_power: float,
    min_dte: int,
    max_dte: int,
    qualification_mode: str,
) -> Tuple[Dict[str, dict], List[dict], dict]:
    """Read-only, fail-closed qualification for one explicit DTE window."""
    if qualification_mode not in {"strict", "fallback"}:
        raise ValueError("qualification_mode must be strict or fallback")
    normalized = [str(ticker).strip().upper() for ticker in input_tickers]
    if len(normalized) != len(set(normalized)) or any(not ticker for ticker in normalized):
        raise RuntimeError("Phase 6 input tickers must be unique and non-empty")
    now_utc = datetime.now(timezone.utc)
    today_la = now_utc.astimezone(ZoneInfo("America/Los_Angeles")).date()
    underlying_quotes = _fetch_underlying_quotes_reconciled(normalized, now_utc)
    outcomes: Dict[str, dict] = {}
    strict_candidates: List[dict] = []

    for ticker in normalized:
        outcome = {
            "ticker": ticker,
            "status": None,
            "current_price": underlying_quotes[ticker]["current_price"],
            "underlying_quote": underlying_quotes[ticker],
            "dte_window": {"min": min_dte, "max": max_dte},
            "expiration_mode": None,
            "selected_expirations": [],
            "chain_count": 0,
            "raw_instrument_count": 0,
            "instrument_count": 0,
            "quote_count": 0,
            "contracts": [],
            "reconciliation": {},
            "error": None,
        }
        try:
            chain_data = parse_mcp_payload(
                mcp__robinhood_trading__get_option_chains(underlying_symbol=ticker),
                "get_option_chains",
            )
            chains = chain_data.get("chains")
            if not isinstance(chains, list):
                raise RuntimeError("get_option_chains returned no chains list")
            eligible_chains = []
            expirations = []
            for chain in chains:
                multiplier = _finite_number(
                    chain.get("trade_value_multiplier"), "trade_value_multiplier"
                )
                if not chain.get("can_open_position") or multiplier != STANDARD_OPTION_CONTRACT_SHARES:
                    continue
                chain_id = chain.get("id")
                if not isinstance(chain_id, str) or not chain_id:
                    raise RuntimeError("eligible option chain is missing ID")
                eligible_chains.append(chain)
                dates = chain.get("expiration_dates")
                if not isinstance(dates, list):
                    raise RuntimeError("eligible option chain has no expiration_dates list")
                for expiration in dates:
                    try:
                        expiration_date = datetime.strptime(expiration, "%Y-%m-%d").date()
                    except (TypeError, ValueError) as exc:
                        raise RuntimeError(f"invalid expiration date: {expiration!r}") from exc
                    dte = (expiration_date - today_la).days
                    if dte >= 0:
                        expirations.append((chain_id, expiration, dte))
            outcome["chain_count"] = len(eligible_chains)
            if not eligible_chains:
                outcome["status"] = "DATA_QUOTE_FAILURE"
                outcome["error"] = "NO ELIGIBLE STANDARD OPTION CHAIN"
                outcomes[ticker] = outcome
                continue

            selected = [item for item in expirations if min_dte <= item[2] <= max_dte]
            outcome["expiration_mode"] = qualification_mode
            if not selected:
                outcome["status"] = "NO_EXPIRATION_IN_WINDOW"
                outcomes[ticker] = outcome
                continue

            selected = sorted(set(selected), key=lambda item: (item[2], item[1], item[0]))
            outcome["selected_expirations"] = [
                {"chain_id": chain_id, "expiration": expiration, "dte": dte}
                for chain_id, expiration, dte in selected
            ]
            instruments_by_id: Dict[str, dict] = {}
            instrument_pages = 0
            for chain_id, expiration, dte in selected:
                instruments, page_summary = _fetch_option_instruments_complete(
                    chain_id, expiration
                )
                instrument_pages += page_summary["pages"]
                outcome["raw_instrument_count"] += page_summary["returned"]
                for instrument in instruments:
                    option_id = instrument["id"]
                    if option_id in instruments_by_id:
                        raise RuntimeError(f"duplicate option instrument ID {option_id}")
                    if instrument.get("chain_id") != chain_id:
                        raise RuntimeError(f"option {option_id} returned for wrong chain")
                    if instrument.get("chain_symbol") != ticker:
                        raise RuntimeError(f"option {option_id} returned for wrong ticker")
                    if instrument.get("expiration_date") != expiration:
                        raise RuntimeError(f"option {option_id} returned for wrong expiration")
                    if instrument.get("type") != "put" or instrument.get("state") != "active":
                        continue
                    if instrument.get("tradability") != "tradable":
                        continue
                    strike = _finite_number(instrument.get("strike_price"), "strike_price")
                    current_price = outcome["current_price"]
                    moneyness = strike / current_price
                    if strike >= current_price or not (0.90 <= moneyness <= 1.00):
                        continue
                    instrument_copy = dict(instrument)
                    instrument_copy["dte"] = dte
                    instrument_copy["strike"] = strike
                    instrument_copy["moneyness"] = moneyness
                    instruments_by_id[option_id] = instrument_copy
            outcome["instrument_count"] = len(instruments_by_id)
            option_ids = sorted(instruments_by_id)
            quotes, quote_summary = _fetch_option_quotes_reconciled(option_ids)
            outcome["quote_count"] = len(quotes)
            outcome["reconciliation"] = {
                "instrument_pages": instrument_pages,
                "eligible_instruments": len(option_ids),
                **quote_summary,
            }

            for option_id in option_ids:
                instrument = instruments_by_id[option_id]
                quote = quotes[option_id]
                reasons = []
                try:
                    bid = _finite_number(quote.get("bid_price"), "bid_price")
                    ask = _finite_number(quote.get("ask_price"), "ask_price")
                    delta = _finite_number(quote.get("delta"), "delta")
                    open_interest = int(quote.get("open_interest"))
                    volume = int(quote.get("volume"))
                    quote_age = _quote_age_seconds(
                        quote.get("updated_at"), datetime.now(timezone.utc)
                    )
                    if bid <= 0:
                        reasons.append("BID_NOT_POSITIVE")
                    if ask <= 0:
                        reasons.append("ASK_NOT_POSITIVE")
                    if ask < bid:
                        reasons.append("CROSSED_MARKET")
                    midpoint = (bid + ask) / 2.0 if bid > 0 and ask > 0 else None
                    spread_ratio = (
                        (ask - bid) / midpoint
                        if midpoint is not None and midpoint > 0 and ask >= bid
                        else None
                    )
                    pop_value = quote.get("chance_of_profit_short")
                    pop = (
                        _finite_number(pop_value, "chance_of_profit_short")
                        if pop_value not in (None, "")
                        else 1.0 - abs(delta)
                    )
                    strike = instrument["strike"]
                    premium_per_contract = bid * STANDARD_OPTION_CONTRACT_SHARES
                    collateral_per_contract = strike * STANDARD_OPTION_CONTRACT_SHARES
                    net_collateral = collateral_per_contract - premium_per_contract
                    net_roc = (
                        premium_per_contract / net_collateral if net_collateral > 0 else None
                    )
                    if not (MIN_CSP_SHORT_POP <= pop <= MAX_CSP_SHORT_POP):
                        reasons.append("POP_OUTSIDE_RANGE")
                    if spread_ratio is None or spread_ratio > MAX_BID_ASK_SPREAD_RATIO:
                        reasons.append("SPREAD_TOO_WIDE")
                    if open_interest < MIN_OPTION_OPEN_INTEREST:
                        reasons.append("OPEN_INTEREST_TOO_LOW")
                    if volume < MIN_OPTION_DAILY_VOLUME:
                        reasons.append("DAILY_VOLUME_TOO_LOW")
                    if quote_age > QUOTE_MAX_AGE_SECONDS:
                        reasons.append("QUOTE_STALE")
                    if net_roc is None or net_roc < MIN_CSP_NET_ROC:
                        reasons.append("NET_ROC_TOO_LOW")
                    capacity = calculate_cash_secured_capacity(
                        buying_power,
                        strike,
                        premium_per_contract,
                        STANDARD_OPTION_CONTRACT_SHARES,
                        credit_reduces_requirement=True,
                    )
                    if capacity["contract_count"] < 1:
                        reasons.append("INSUFFICIENT_BUYING_POWER")
                    passes_contract_gates = not reasons
                    qualified = passes_contract_gates
                    contract = {
                        "ticker": ticker,
                        "option_id": option_id,
                        "expiration": instrument.get("expiration_date"),
                        "dte": instrument["dte"],
                        "expiration_mode": outcome["expiration_mode"],
                        "strike": strike,
                        "moneyness": instrument["moneyness"],
                        "bid": bid,
                        "ask": ask,
                        "midpoint": midpoint,
                        "spread_ratio": spread_ratio,
                        "delta": delta,
                        "short_pop": pop,
                        "open_interest": open_interest,
                        "daily_volume": volume,
                        "quote_updated_at": quote.get("updated_at"),
                        "quote_age_seconds": round(quote_age, 3),
                        "premium_per_contract": premium_per_contract,
                        "collateral_per_contract": collateral_per_contract,
                        "net_collateral_per_contract": net_collateral,
                        "net_roc": net_roc,
                        "capacity": capacity,
                        "passes_contract_gates": passes_contract_gates,
                        "qualifies": qualified,
                        "qualifies_strict": (
                            qualified and qualification_mode == "strict"
                        ),
                        "qualifies_fallback": (
                            qualified and qualification_mode == "fallback"
                        ),
                        "rejection_reasons": reasons,
                    }
                    outcome["contracts"].append(contract)
                    if qualified:
                        strict_candidates.append(contract)
                except (TypeError, ValueError) as exc:
                    outcome["contracts"].append({
                        "ticker": ticker,
                        "option_id": option_id,
                        "expiration": instrument.get("expiration_date"),
                        "dte": instrument["dte"],
                        "qualifies": False,
                        "qualifies_strict": False,
                        "qualifies_fallback": False,
                        "passes_contract_gates": False,
                        "rejection_reasons": ["MALFORMED_QUOTE"],
                        "quote_error": str(exc),
                    })

            qualified_for_ticker = [
                contract for contract in outcome["contracts"]
                if contract.get("qualifies")
            ]
            if qualified_for_ticker:
                outcome["status"] = "QUALIFIED"
            else:
                outcome["status"] = "NO_CONTRACT_PASSED"
        except Exception as exc:
            outcome["status"] = "DATA_QUOTE_FAILURE"
            outcome["error"] = str(exc)
        outcomes[ticker] = outcome

    ranked = []
    for ticker in normalized:
        ticker_contracts = [
            contract for contract in strict_candidates if contract["ticker"] == ticker
        ]
        if ticker_contracts:
            ranked.append(max(
                ticker_contracts,
                key=lambda item: (item["net_roc"], item["bid"], item["option_id"]),
            ))
    ranked.sort(key=lambda item: (-item["net_roc"], -item["bid"], item["ticker"]))
    status_counts: Dict[str, int] = {}
    for outcome in outcomes.values():
        status = outcome["status"]
        status_counts[status] = status_counts.get(status, 0) + 1
    summary = {
        "snapshot_at": now_utc.isoformat(),
        "calendar_timezone": "America/Los_Angeles",
        "calendar_date": today_la.isoformat(),
        "input_count": len(normalized),
        "outcome_count": len(outcomes),
        "status_counts": status_counts,
        "qualification_mode": qualification_mode,
        "qualified_ticker_count": len(ranked),
        "qualified_contract_count": sum(
            contract.get("qualifies", False)
            for outcome in outcomes.values()
            for contract in outcome["contracts"]
        ),
        "total_raw_instrument_count": sum(
            outcome["raw_instrument_count"] for outcome in outcomes.values()
        ),
        "total_moneyness_eligible_instrument_count": sum(
            outcome["instrument_count"] for outcome in outcomes.values()
        ),
        "total_option_quote_count": sum(
            outcome["quote_count"] for outcome in outcomes.values()
        ),
        "thresholds": {
            "dte": [min_dte, max_dte],
            "moneyness": [0.90, 1.00],
            "short_pop": [MIN_CSP_SHORT_POP, MAX_CSP_SHORT_POP],
            "max_spread_ratio": MAX_BID_ASK_SPREAD_RATIO,
            "min_open_interest": MIN_OPTION_OPEN_INTEREST,
            "min_daily_volume": MIN_OPTION_DAILY_VOLUME,
            "max_quote_age_seconds": QUOTE_MAX_AGE_SECONDS,
            "min_net_roc": MIN_CSP_NET_ROC,
            "contract_multiplier": STANDARD_OPTION_CONTRACT_SHARES,
        },
    }
    return outcomes, ranked, summary


def qualify_options_hardened(
    universe: Dict[str, dict],
    primary_tickers: List[str],
    buying_power: float,
    secondary_tickers: Optional[List[str]] = None,
) -> Tuple[Dict[str, dict], List[dict], dict]:
    """Expand cohorts within each DTE tier until a qualifying contract is found.

    Attempt order is deterministic: strict top 1–5, strict top 6–10,
    fallback top 1–5, then fallback top 6–10. The next attempt runs only
    when the preceding attempt produced zero qualifying contracts.
    """
    primary = [str(ticker).strip().upper() for ticker in primary_tickers]
    secondary = [
        str(ticker).strip().upper() for ticker in (secondary_tickers or [])
    ]
    combined = primary + secondary
    if not primary:
        raise RuntimeError("Phase 6 primary cohort must not be empty")
    if len(primary) > MAX_PHASE5_VALUATION_CANDIDATES:
        raise RuntimeError("Phase 6 primary cohort exceeds five tickers")
    if len(secondary) > MAX_PHASE5_VALUATION_CANDIDATES:
        raise RuntimeError("Phase 6 secondary cohort exceeds five tickers")
    if any(not ticker for ticker in combined) or len(combined) != len(set(combined)):
        raise RuntimeError("Phase 6 cohorts must contain unique non-empty tickers")

    attempt_plan = [
        ("strict_top_1_5", primary, MIN_OPTION_DTE, MAX_OPTION_DTE, "strict"),
        ("strict_top_6_10", secondary, MIN_OPTION_DTE, MAX_OPTION_DTE, "strict"),
        (
            "fallback_top_1_5",
            primary,
            FALLBACK_MIN_OPTION_DTE,
            FALLBACK_MAX_OPTION_DTE,
            "fallback",
        ),
        (
            "fallback_top_6_10",
            secondary,
            FALLBACK_MIN_OPTION_DTE,
            FALLBACK_MAX_OPTION_DTE,
            "fallback",
        ),
    ]
    attempts = []
    final_outcomes: Dict[str, dict] = {}
    final_ranked: List[dict] = []
    final_summary: dict = {}
    selected_attempt = None

    for label, cohort, min_dte, max_dte, mode in attempt_plan:
        if not cohort:
            continue
        outcomes, ranked, attempt_summary = _qualify_options_window_hardened(
            universe,
            cohort,
            buying_power,
            min_dte,
            max_dte,
            mode,
        )
        attempt_record = {
            "attempt_number": len(attempts) + 1,
            "label": label,
            "cohort": "top_1_5" if cohort is primary else "top_6_10",
            "tickers": list(cohort),
            "qualification_mode": mode,
            "dte_window": [min_dte, max_dte],
            "summary": attempt_summary,
            "outcomes": outcomes,
        }
        attempts.append(attempt_record)
        final_outcomes = outcomes
        final_ranked = ranked
        final_summary = attempt_summary
        if ranked:
            selected_attempt = label
            break

    summary = dict(final_summary)
    summary.update({
        "attempt_order": [
            "strict_top_1_5",
            "strict_top_6_10",
            "fallback_top_1_5",
            "fallback_top_6_10",
        ],
        "attempt_count": len(attempts),
        "attempts": attempts,
        "selected_attempt": selected_attempt,
        "all_attempts_exhausted": selected_attempt is None,
        "secondary_cohort_used": any(
            attempt["cohort"] == "top_6_10" for attempt in attempts
        ),
        "fallback_used": any(
            attempt["qualification_mode"] == "fallback" for attempt in attempts
        ),
    })
    if summary["fallback_used"]:
        summary["fallback_trigger"] = (
            "NO STRICT 30-45 DTE QUALIFYING CONTRACTS ACROSS TOP 1-10"
        )
    return final_outcomes, final_ranked, summary


def build_phase7_ranking(
    phase6_payload: dict,
    phase5_rows: List[dict],
    phase4_records: Dict[str, dict],
) -> dict:
    """Build a deterministic, read-only Phase 7 ranking from Phase 6 output."""
    candidates = phase6_payload.get("qualified_candidates")
    if not isinstance(candidates, list):
        raise RuntimeError("Phase 6 artifact has no qualified_candidates list")
    phase5_by_ticker = {
        str(row.get("ticker", "")).upper(): row
        for row in phase5_rows
        if isinstance(row, dict) and row.get("ticker")
    }
    buying_power = _finite_number(
        phase6_payload.get("account", {}).get("unleveraged_buying_power_usd"),
        "unleveraged_buying_power_usd",
    )
    seen_tickers = set()
    normalized = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            raise RuntimeError("Phase 6 candidate is not an object")
        ticker = str(candidate.get("ticker", "")).upper()
        option_id = candidate.get("option_id")
        if not ticker or not isinstance(option_id, str) or not option_id:
            raise RuntimeError("Phase 6 candidate identity is incomplete")
        if ticker in seen_tickers:
            raise RuntimeError(f"Phase 6 contains more than one ranked candidate for {ticker}")
        seen_tickers.add(ticker)
        if not candidate.get("qualifies") or candidate.get("rejection_reasons"):
            raise RuntimeError(f"Phase 6 candidate {ticker} is not fully qualified")
        capacity = candidate.get("capacity")
        if not isinstance(capacity, dict):
            raise RuntimeError(f"Phase 6 candidate {ticker} has no capacity object")
        collateral = _finite_number(
            candidate.get("collateral_per_contract"), "collateral_per_contract"
        )
        premium = _finite_number(
            candidate.get("premium_per_contract"), "premium_per_contract"
        )
        net_roc = _finite_number(candidate.get("net_roc"), "net_roc")
        contract_count = int(capacity.get("contract_count"))
        if contract_count < 1:
            raise RuntimeError(f"Phase 6 candidate {ticker} is unaffordable")
        # Verify total net collateral does not exceed buying power (premium reduces requirement)
        net_collateral = collateral - premium
        if net_collateral * contract_count > buying_power:
            raise RuntimeError(f"Phase 6 candidate {ticker} exceeds buying power")
        valuation = phase5_by_ticker.get(ticker)
        technical = phase4_records.get(ticker)
        if not isinstance(valuation, dict) or not isinstance(technical, dict):
            raise RuntimeError(f"Phase 7 enrichment source missing for {ticker}")
        normalized.append((candidate, collateral, premium, net_roc, contract_count, valuation, technical))

    normalized.sort(
        key=lambda item: (
            -item[3],
            -item[2],
            item[0]["ticker"],
            item[0]["option_id"],
        )
    )
    recommendations = []
    for rank, (candidate, collateral, premium, net_roc, contract_count, valuation, technical) in enumerate(normalized, 1):
        capacity = candidate["capacity"]
        recommendations.append({
            "rank": rank,
            "ticker": candidate["ticker"],
            "option_id": candidate["option_id"],
            "option_type": "put",
            "position_effect": "open",
            "qualification_mode": candidate["expiration_mode"],
            "expiration": candidate["expiration"],
            "dte": candidate["dte"],
            "underlying_price": phase6_payload["outcomes"][candidate["ticker"]]["current_price"],
            "strike": candidate["strike"],
            "moneyness": candidate["moneyness"],
            "executable_credit_per_share": candidate["bid"],
            "bid": candidate["bid"],
            "ask": candidate["ask"],
            "spread_ratio": candidate["spread_ratio"],
            "premium_per_contract": premium,
            "collateral_per_contract": collateral,
            "net_collateral_per_contract": candidate["net_collateral_per_contract"],
            "net_roc": net_roc,
            "short_pop": candidate["short_pop"],
            "delta": candidate["delta"],
            "open_interest": candidate["open_interest"],
            "daily_volume": candidate["daily_volume"],
            "quote_updated_at": candidate["quote_updated_at"],
            "quote_age_seconds": candidate["quote_age_seconds"],
            "independent_max_contracts": contract_count,
            "independent_total_collateral": capacity["total_collateral"],
            "independent_total_premium": capacity["total_premium"],
            "independent_remaining_buying_power": capacity["remaining_buying_power"],
            "capacity_basis": "independent mathematical maximum; not a simultaneous portfolio allocation",
            "valuation": {
                "composite_score": valuation.get("score"),
                "valuation_score": valuation.get("valuation_score"),
                "valuation_grade": valuation.get("valuation_grade"),
                "business_quality_score": valuation.get("business_quality_score"),
                "business_quality_grade": valuation.get("business_quality_grade"),
                "valuation_grade_explanation": valuation.get("valuation_grade_explanation"),
                "business_quality_grade_explanation": valuation.get("business_quality_grade_explanation"),
                "confidence": valuation.get("confidence"),
                "classification": valuation.get("classification"),
                "regime": valuation.get("regime"),
            },
            "technical": {
                "conditions": technical.get("conditions"),
                "conditions_met": technical.get("conditions_met"),
                "post_screen_pass": technical.get("post_screen_pass"),
            },
        })

    message_lines = [
        "CSP Phase 7 ranking",
        f"Buying power: ${buying_power:,.2f}",
        "Capacity is shown independently per candidate; no orders were placed.",
        "",
        build_recommendation_telegram_message(recommendations, buying_power),
    ]
    return {
        "status": "PASS",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_phase6_run_id": phase6_payload.get("run_id"),
        "buying_power": buying_power,
        "ranking": ["net_roc_desc", "premium_per_contract_desc", "ticker_asc", "option_id_asc"],
        "allocation_policy": "independent cash-secured capacity only; user selection required before any order action",
        "recommendation_count": len(recommendations),
        "recommendations": recommendations,
        "telegram_message": "\n".join(message_lines),
    }


def build_recommendation_telegram_message(recommendations: List[dict], buying_power: float) -> str:
    """Format recommendation blocks from Phase 7 data without performing MCP calls."""
    if not recommendations:
        return "❌ No qualifying CSP contracts found"

    blocks = []
    for rec in recommendations:
        valuation = rec.get("valuation", {}) or {}
        technical = rec.get("technical", {}) or {}
        conditions = technical.get("conditions", {}) or {}
        condition_text = " | ".join(
            f"{key}: {value}" for key, value in conditions.items()
        ) or "N/A"
        multiplier = rec.get("multiplier", STANDARD_OPTION_CONTRACT_SHARES)
        contract_count = rec.get("independent_max_contracts", 0)
        total_collateral = rec.get("independent_total_collateral", 0.0)
        total_premium = rec.get("independent_total_premium", 0.0)
        remaining_buying_power = rec.get(
            "independent_remaining_buying_power", buying_power
        )
        blocks.append("\n".join([
            f"📊 {rec['ticker']} | Current price: ${rec['underlying_price']:,.2f}",
            f"📅 ${rec['strike']:,.2f} Put • {rec['expiration']} • DTE {rec['dte']}",
            f"Contracts: {contract_count} | Collateral: ${total_collateral:,.2f}",
            f"Premium: ${rec['executable_credit_per_share']:,.2f}/contract × {multiplier} = ${rec['premium_per_contract']:,.2f}",
            f"💰 Total premium: ${total_premium:,.2f} (ROC: {rec['net_roc']:.2%})",
            f"Remaining buying power: ${remaining_buying_power:,.2f}",
            f"Spread: {rec['spread_ratio']:.1%} | OI: {rec['open_interest']}",
            f"🏷 Valuation explanation\n{valuation.get('valuation_grade_explanation') or 'N/A'}",
            f"🏢 Business quality explanation\n{valuation.get('business_quality_grade_explanation') or 'N/A'}",
            f"✅ Conditions: {condition_text}",
        ]))
    return "\n\n".join(blocks)


# ==================== TELEGRAM MESSAGE ====================
def build_telegram_message(universe: Dict[str, dict], passing_tickers: List[str], ranked: List[dict], artifact_paths: List[Path]) -> str:
    """Build one data-driven Telegram block per recommendation without MCP calls."""
    if not ranked:
        return "❌ No qualifying CSP contracts found"
    blocks = []
    for rec in ranked:
        ticker = rec.get("ticker", "UNKNOWN")
        source = universe.get(ticker, {})
        current_price = rec.get("current_price", source.get("current_price", source.get("price")))
        strike = rec.get("strike")
        expiration = rec.get("expiration", "UNKNOWN")
        dte = rec.get("dte")
        premium_per_contract = rec.get("premium_per_contract")
        multiplier = rec.get("multiplier", STANDARD_OPTION_CONTRACT_SHARES)
        buying_power = rec.get("buying_power", source.get("buying_power"))
        contract_count = rec.get("contract_count")
        if contract_count is None and buying_power is not None and strike is not None and premium_per_contract is not None:
            capacity = calculate_cash_secured_capacity(buying_power, strike, float(premium_per_contract) * float(multiplier), multiplier)
            contract_count = capacity["contract_count"]
            total_collateral = capacity["total_collateral"]
            total_premium = capacity["total_premium"]
            remaining_buying_power = capacity["remaining_buying_power"]
        else:
            contract_count = contract_count if contract_count is not None else "N/A"
            total_collateral = rec.get("total_collateral", rec.get("collateral"))
            total_premium = rec.get("total_premium")
            remaining_buying_power = rec.get("remaining_buying_power")
        valuation_explanation = rec.get("valuation_grade_explanation", source.get("valuation_grade_explanation", "N/A"))
        business_quality_explanation = rec.get("business_quality_grade_explanation", source.get("business_quality_grade_explanation", "N/A"))
        conditions = rec.get("conditions", source.get("conditions", {})) or {}
        condition_text = " | ".join(f"{key}: {value}" for key, value in conditions.items()) or "N/A"
        current_price_text = f"${float(current_price):,.2f}" if current_price is not None else "N/A"
        strike_text = f"${float(strike):,.2f}" if strike is not None else "N/A"
        dte_text = str(dte) if dte is not None else "N/A"
        premium_text = f"${float(premium_per_contract):,.2f}" if premium_per_contract is not None else "N/A"
        collateral_text = f"${float(total_collateral):,.2f}" if total_collateral is not None else "N/A"
        total_premium_text = f"${float(total_premium):,.2f}" if total_premium is not None else "N/A"
        remaining_text = f"${float(remaining_buying_power):,.2f}" if remaining_buying_power is not None else "N/A"
        roc = rec.get("collateral_return", rec.get("return_pct"))
        if roc is None and total_collateral not in (None, 0) and total_premium is not None:
            roc = float(total_premium) / float(total_collateral)
        roc_text = f"{float(roc):.2%}" if roc is not None else "N/A"
        spread_text = f"{float(rec.get('spread_pct')):.1%}" if rec.get("spread_pct") is not None else "N/A"
        oi_text = str(rec.get("oi", "N/A"))
        premium_line = "Premium: N/A"
        if premium_per_contract is not None:
            premium_line = f"Premium: {premium_text}/contract × {multiplier} = ${float(premium_per_contract) * float(multiplier):,.2f}"
        blocks.append("\n".join([
            f"📊 {ticker} | Current price: {current_price_text}",
            f"📅 {strike_text} Put • {expiration} • DTE {dte_text}",
            f"Contracts: {contract_count} | Collateral: {collateral_text}",
            premium_line,
            f"💰 Total premium: {total_premium_text} (ROC: {roc_text})",
            f"Remaining buying power: {remaining_text}",
            f"Spread: {spread_text} | OI: {oi_text}",
            f"🏷 Valuation explanation\n{valuation_explanation}",
            f"🏢 Business quality explanation\n{business_quality_explanation}",
            f"✅ Conditions: {condition_text}",
        ]))
    return "\n\n".join(blocks)

# ==================== MAIN ====================
def run_csp_screening() -> dict:
    """Full end-to-end CSP screening pipeline."""
    print(f"[{datetime.now()}] Starting CSP screening...", flush=True)
    artifact_paths = []

    # 1. Account selection and live buying-power preflight. This phase is
    # fail-closed: no market scan may start without one eligible account and a
    # valid unleveraged buying-power value.
    try:
        account_context, phase1_payload = run_account_preflight()
    except Exception as exc:
        phase1_payload = {"status": "FAIL", "error": str(exc)}
        artifact_paths.append(
            persist_phase_json(1, "account_preflight", phase1_payload)
        )
        raise RuntimeError("Phase 1 account preflight failed") from exc
    artifact_paths.append(persist_phase_json(1, "account_preflight", phase1_payload))

    # 2. One synchronous scanner query. The returned rows are extracted only
    # after run_scan returns, then persisted in the phase output artifact.
    universe, scan_payload = run_single_scan()
    if not universe:
        artifact_paths.append(persist_phase_json(2, "market_screen", {
            "universe": {},
            "count": 0,
            "scan_id": scan_payload["scan_id"],
            "scan_rows": scan_payload["rows"],
            "filters": scan_payload["filters"],
            "error": "No tickers from scanner",
        }))
        return {"error": "No tickers from scanner", "phase_artifacts": [str(p) for p in artifact_paths], "recommendations": [], "passing": 0}
    artifact_paths.append(persist_phase_json(2, "market_screen", {
        "universe": universe,
        "count": len(universe),
        "scan_id": scan_payload["scan_id"],
        "scan_rows": scan_payload["rows"],
        "filters": scan_payload["filters"],
        "scan_metadata": scan_payload["metadata"],
        "complete": scan_payload["complete"],
        "price_cap_usd": MAX_UNDERLYING_PRICE_USD,
        "min_market_cap_usd": MIN_MARKET_CAP_USD,
    }))

    # 3. Fundamentals and annual revenue gate
    fundamentals_summary = fetch_fundamentals(universe)
    passed = yfinance_annual_yoy_gate(universe)
    yfinance_quarterly_qoq(universe, passed)
    annual_reason_counts = {}
    for record in universe.values():
        reason = record.get("fy_yoy_reason", "NOT EVALUATED")
        annual_reason_counts[reason] = annual_reason_counts.get(reason, 0) + 1
    quarterly_reason_counts = {}
    for ticker in passed:
        reason = universe[ticker].get("fy_qoq_reason", "NOT EVALUATED")
        quarterly_reason_counts[reason] = quarterly_reason_counts.get(reason, 0) + 1
    unresolved_annual_tickers = sorted(
        ticker
        for ticker, record in universe.items()
        if record.get("fy_yoy_reason") == "YFINANCE RETRIEVAL FAILURE"
    )
    artifact_paths.append(persist_phase_json(3, "revenue_gate", {
        "evaluated": len(universe),
        "fundamentals_summary": fundamentals_summary,
        "passed": passed,
        "passed_count": len(passed),
        "annual_reason_counts": annual_reason_counts,
        "quarterly_reason_counts": quarterly_reason_counts,
        "unresolved_annual_tickers": unresolved_annual_tickers,
        "yfinance_max_attempts": YFINANCE_MAX_ATTEMPTS,
        "records": universe,
    }))
    if not passed:
        return {"error": "No tickers passed Yahoo Finance YoY gate", "phase_artifacts": [str(p) for p in artifact_paths], "recommendations": [], "passing": 0}

    # 4. Technical validation
    technical_fetch_summary = fetch_technicals(universe, passed)
    passing_tickers = evaluate_post_screen(universe, passed)
    technical_pass_tickers = [t for t in passed if universe[t].get("post_screen_pass")]
    phase4_rows = [
        {
            "ticker": t,
            "market_cap": universe[t].get("market_cap"),
            "price": universe[t].get("price"),
            "conditions": universe[t].get("conditions", {}),
            "conditions_met": universe[t].get("conditions_met", 0),
        }
        for t in passing_tickers
    ]
    artifact_paths.append(persist_phase_json(4, "technical_validation", {
        "input_count": len(passed),
        "technical_fetch_summary": technical_fetch_summary,
        "technical_pass_tickers": technical_pass_tickers,
        "technical_pass_count": len(technical_pass_tickers),
        "passing_tickers": passing_tickers,
        "passing_count": len(passing_tickers),
        "phase4_tickers": passing_tickers,
        "phase4_count": len(passing_tickers),
        "phase4_order": phase4_rows,
        "filter": {"minimum_met_conditions": MIN_MET_TECHNICAL_CONDITIONS, "condition_keys": list(TECHNICAL_CONDITION_KEYS)},
        "ordering": ["market_cap_desc", "price_desc", "conditions_met_desc"],
        "records": {t: universe[t] for t in passed},
    }))

    # 5. Valuation enrichment must run immediately after technical validation.
    # Only this run's dynamically generated passing_tickers are permitted as
    # input; never substitute valuation_scorer's command-line default universe.
    valuation_adapter_path = prepare_valuation_mcp_adapter()
    valuation_result = run_sequential_valuation(
        passing_tickers,
        timeout_seconds=VALUATION_TICKER_TIMEOUT_SECONDS,
        fresh_retry=True,
    )
    valuation_rows, valuation_summary = build_phase5_valuation_rows(
        universe, passing_tickers, valuation_result
    )
    valuation_by_ticker = {
        row["ticker"]: row for row in valuation_rows
    }
    artifact_paths.append(persist_phase_json(5, "final_valuation", {
        "input_tickers": passing_tickers,
        "input_count": len(passing_tickers),
        "execution_mode": valuation_result.get("execution_mode"),
        "runtime_adapter": str(valuation_adapter_path),
        "valuation_summary": valuation_summary,
        "ticker_count": len(valuation_rows),
        "scored_count": valuation_summary["scored_count"],
        "unscored_count": valuation_summary["unscored_count"],
        "composite_formula": valuation_summary["composite_formula"],
        "ordering": valuation_summary["ordering"],
        "rows": valuation_rows,
    }))

    # 6. Option-chain qualification
    # Phase 5 remains complete and untruncated. Phase 6 creates two ordered
    # five-ticker cohorts from Phase 5's deterministic composite-score order.
    phase6_ranked_tickers = [
        row["ticker"] for row in valuation_rows if row["score"] is not None
    ][:2 * MAX_PHASE5_VALUATION_CANDIDATES]
    phase6_primary_tickers = phase6_ranked_tickers[:MAX_PHASE5_VALUATION_CANDIDATES]
    phase6_secondary_tickers = phase6_ranked_tickers[MAX_PHASE5_VALUATION_CANDIDATES:]
    if not phase6_primary_tickers:
        msg = build_telegram_message(universe, [], [], artifact_paths)
        return {"phase_artifacts": [str(p) for p in artifact_paths], "recommendations": [], "passing": 0, "telegram_message": msg}

    phase6_outcomes, ranked, phase6_summary = qualify_options_hardened(
        universe,
        phase6_primary_tickers,
        account_context["buying_power"],
        phase6_secondary_tickers,
    )
    phase6_payload = {
        "primary_tickers": phase6_primary_tickers,
        "secondary_tickers": phase6_secondary_tickers,
        "input_tickers": phase6_ranked_tickers,
        "input_rows": valuation_rows,
        "input_count": len(phase6_ranked_tickers),
        "source_phase5_ordering": ["score_desc", "ticker_asc", "unscored_last"],
        "account": {
            "selected_account": phase1_payload["selected_account"],
            "unleveraged_buying_power_usd": account_context["buying_power"],
        },
        "summary": phase6_summary,
        "outcomes": phase6_outcomes,
        "qualified_candidates": ranked,
    }
    phase6_payload["run_id"] = RUN_ID
    artifact_paths.append(
        persist_phase_json(6, "option_chain_qualification", phase6_payload)
    )

    # 7. CSP ranking
    phase7_payload = build_phase7_ranking(
        phase6_payload,
        valuation_rows,
        {ticker: universe[ticker] for ticker in phase6_ranked_tickers},
    )
    artifact_paths.append(persist_phase_json(7, "csp_ranking", phase7_payload))
    telegram_msg = phase7_payload["telegram_message"]

    print(f"[{datetime.now()}] CSP screening complete!", flush=True)
    return {
        "phase_artifacts": [str(p) for p in artifact_paths],
        "recommendations": phase7_payload["recommendations"],
        "passing": len(phase6_ranked_tickers),
        "telegram_message": telegram_msg
    }

if __name__ == "__main__":
    argparse.ArgumentParser(
        description="Run the CSP screening pipeline.",
    ).parse_args()
    exit_code = 0
    try:
        initialize_mcp_runtime()
        result = run_csp_screening()
        if result.get("error"):
            exit_code = 1
    except Exception as exc:
        exit_code = 1
        result = {
            "error": str(exc),
            "error_type": type(exc).__name__,
            "run_id": RUN_ID,
            "phase_artifacts": [],
        }
    finally:
        shutdown_mcp_runtime()
    print(json.dumps(result, indent=2, default=str))
    raise SystemExit(exit_code)