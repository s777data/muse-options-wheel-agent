"""D-37 unit checks for the Phase 6 future-quote guard (synthetic, no network).

Run: .venv/bin/python tests/test_phase6_future_quote.py
(Also pytest-collectable: every check is a test_* function.)

Background: the 2026-11-18 live run failed Phase 6 outright on
ValueError "quote timestamp is in the future" — one underlying quote with
an updated_at ahead of the run clock aborted qualification for the whole
cohort. D-37 (approved by BIG SANDY 2026-11-18):

  * _quote_age_seconds clamps small negative ages (<= 60s clock skew) to 0
    and raises FutureQuoteError (a ValueError) beyond the tolerance.
  * _fetch_underlying_quotes_reconciled excludes just the offending ticker
    and records {ticker, updated_at, run_clock, quote_age_seconds}; the
    rest of the cohort still reconciles. Stale quotes still fail closed.
  * _qualify_options_window_hardened marks excluded tickers
    EXCLUDED_FUTURE_QUOTE and records summary["excluded_future_quote"];
    qualify_options_hardened aggregates/dedupes it across attempts.
  * A future-dated option (chain) quote excludes just that contract with
    rejection reason FUTURE_QUOTE and its updated_at recorded.

Every check runs against BOTH scripts/csp_screening.py and
scripts/csp_screening_momentum.py (the D-35 copy carries the same guard).
"""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import csp_screening as cs  # noqa: E402
from scripts import csp_screening_momentum as cm  # noqa: E402

MODULES = [cs, cm]
NOW = datetime(2026, 11, 18, 20, 0, 0, tzinfo=timezone.utc)


def _iso(dt):
    return dt.isoformat()


def _envelope(data):
    return {"result": json.dumps({"data": data})}


def _equity_payload(quotes_by_symbol):
    """quotes_by_symbol: {symbol: (price, venue_last_trade_time_iso)}"""
    return _envelope({
        "results": [
            {"quote": {
                "symbol": symbol,
                "last_trade_price": str(price),
                "venue_last_trade_time": ts,
            }}
            for symbol, (price, ts) in quotes_by_symbol.items()
        ]
    })


def _fresh_ts(seconds_ago=10):
    return _iso(datetime.now(timezone.utc) - timedelta(seconds=seconds_ago))


def _future_ts(seconds_ahead):
    return _iso(datetime.now(timezone.utc) + timedelta(seconds=seconds_ahead))


# 1. _quote_age_seconds: normal, clamp, boundary, beyond tolerance.
def test_quote_age_clamp_and_raise():
    for mod in MODULES:
        assert mod._quote_age_seconds(_iso(NOW - timedelta(seconds=90)), NOW) == 90.0
        # 30s ahead of the run clock: inside the 60s skew tolerance -> 0.
        assert mod._quote_age_seconds(_iso(NOW + timedelta(seconds=30)), NOW) == 0.0
        # Boundary: exactly 60s ahead still clamps.
        assert mod._quote_age_seconds(_iso(NOW + timedelta(seconds=60)), NOW) == 0.0
        # 61s ahead: beyond tolerance -> FutureQuoteError carrying the age.
        try:
            mod._quote_age_seconds(_iso(NOW + timedelta(seconds=61)), NOW)
        except mod.FutureQuoteError as exc:
            assert isinstance(exc, ValueError)
            assert abs(exc.age_seconds - (-61.0)) < 1e-6
            assert "in the future" in str(exc)
        else:
            raise AssertionError("expected FutureQuoteError beyond tolerance")


# 2. Underlying fetch: future ticker excluded, skewed ticker clamped in,
#    fresh ticker reconciles, nothing raises.
def test_underlying_future_ticker_excluded_not_fatal():
    for mod in MODULES:
        payload = _equity_payload({
            "GOOD": (50.0, _fresh_ts(10)),
            "SKEW": (25.0, _future_ts(30)),
            "FUT": (10.0, _future_ts(600)),
        })
        original = mod.mcp__robinhood_trading__get_equity_quotes
        mod.mcp__robinhood_trading__get_equity_quotes = lambda symbols: payload
        try:
            reconciled, excluded = mod._fetch_underlying_quotes_reconciled(
                ["GOOD", "SKEW", "FUT"], datetime.now(timezone.utc)
            )
        finally:
            mod.mcp__robinhood_trading__get_equity_quotes = original
        assert set(reconciled) == {"GOOD", "SKEW"}, reconciled.keys()
        assert reconciled["SKEW"]["quote_age_seconds"] == 0.0
        assert len(excluded) == 1
        record = excluded[0]
        assert record["ticker"] == "FUT"
        assert record["updated_at"] is not None
        assert record["run_clock"] is not None
        assert record["quote_age_seconds"] < -mod.QUOTE_FUTURE_TOLERANCE_SECONDS


# 3. Stale underlying quotes still fail closed (guard unchanged).
def test_underlying_stale_still_raises():
    for mod in MODULES:
        payload = _equity_payload({"OLD": (50.0, _fresh_ts(3600))})
        original = mod.mcp__robinhood_trading__get_equity_quotes
        mod.mcp__robinhood_trading__get_equity_quotes = lambda symbols: payload
        try:
            try:
                mod._fetch_underlying_quotes_reconciled(
                    ["OLD"], datetime.now(timezone.utc)
                )
            except RuntimeError as exc:
                assert "stale underlying quote" in str(exc)
            else:
                raise AssertionError("expected stale-quote RuntimeError")
        finally:
            mod.mcp__robinhood_trading__get_equity_quotes = original


# 4. Window qualification: an all-future cohort yields EXCLUDED_FUTURE_QUOTE
#    outcomes and the artifact summary record — no exception, no chain calls.
def test_window_all_future_cohort_excluded():
    for mod in MODULES:
        payload = _equity_payload({
            "FUT1": (10.0, _future_ts(900)),
            "FUT2": (20.0, _future_ts(1200)),
        })
        original = mod.mcp__robinhood_trading__get_equity_quotes
        mod.mcp__robinhood_trading__get_equity_quotes = lambda symbols: payload
        try:
            outcomes, ranked, summary = mod._qualify_options_window_hardened(
                {}, ["FUT1", "FUT2"], 1000.0,
                mod.MIN_OPTION_DTE, mod.MAX_OPTION_DTE, "strict",
            )
        finally:
            mod.mcp__robinhood_trading__get_equity_quotes = original
        assert ranked == []
        assert outcomes["FUT1"]["status"] == "EXCLUDED_FUTURE_QUOTE"
        assert outcomes["FUT2"]["status"] == "EXCLUDED_FUTURE_QUOTE"
        assert outcomes["FUT1"]["future_quote_exclusion"]["ticker"] == "FUT1"
        assert outcomes["FUT1"]["current_price"] is None
        recorded = {r["ticker"] for r in summary["excluded_future_quote"]}
        assert recorded == {"FUT1", "FUT2"}
        assert summary["status_counts"].get("EXCLUDED_FUTURE_QUOTE") == 2


# 5. qualify_options_hardened aggregates + dedupes exclusions across the
#    strict and fallback attempts over the same cohort.
def test_qualify_aggregates_exclusions_across_attempts():
    for mod in MODULES:
        payload = _equity_payload({"FUT": (10.0, _future_ts(900))})
        original = mod.mcp__robinhood_trading__get_equity_quotes
        mod.mcp__robinhood_trading__get_equity_quotes = lambda symbols: payload
        try:
            outcomes, ranked, summary = mod.qualify_options_hardened(
                {}, ["FUT"], 1000.0
            )
        finally:
            mod.mcp__robinhood_trading__get_equity_quotes = original
        assert ranked == []
        assert summary["attempt_count"] >= 2  # strict + fallback both ran
        tickers = [r["ticker"] for r in summary["excluded_future_quote"]]
        assert tickers == ["FUT"], tickers  # deduped across attempts


# 6. Chain quotes: a future-dated option quote excludes just that contract
#    with FUTURE_QUOTE and its updated_at recorded; the ticker's outcome is
#    NO_CONTRACT_PASSED, not a phase failure.
def test_chain_future_quote_excludes_contract_only():
    for mod in MODULES:
        expiration = (
            datetime.now(ZoneInfo("America/Los_Angeles")).date()
            + timedelta(days=35)
        ).isoformat()
        equity = _equity_payload({"GOOD": (50.0, _fresh_ts(10))})
        chains = _envelope({"chains": [{
            "id": "chain1",
            "trade_value_multiplier": 100,
            "can_open_position": True,
            "expiration_dates": [expiration],
        }]})
        instrument = {
            "id": "opt1", "chain_id": "chain1", "chain_symbol": "GOOD",
            "expiration_date": expiration, "type": "put", "state": "active",
            "tradability": "tradable", "strike_price": "47.50",
        }
        future_quote = {
            "bid_price": "1.00", "ask_price": "1.10", "delta": "-0.25",
            "open_interest": 500, "volume": 100,
            "updated_at": _future_ts(600),
            "chance_of_profit_short": "",
        }
        originals = (
            mod.mcp__robinhood_trading__get_equity_quotes,
            mod.mcp__robinhood_trading__get_option_chains,
            mod._fetch_option_instruments_complete,
            mod._fetch_option_quotes_reconciled,
        )
        mod.mcp__robinhood_trading__get_equity_quotes = lambda symbols: equity
        mod.mcp__robinhood_trading__get_option_chains = (
            lambda underlying_symbol: chains
        )
        mod._fetch_option_instruments_complete = (
            lambda chain_id, expiration: ([instrument], {"pages": 1, "returned": 1})
        )
        mod._fetch_option_quotes_reconciled = lambda option_ids: (
            {"opt1": future_quote}, {"requested": 1, "returned": 1, "batches": 1}
        )
        try:
            outcomes, ranked, summary = mod._qualify_options_window_hardened(
                {}, ["GOOD"], 1000.0,
                mod.MIN_OPTION_DTE, mod.MAX_OPTION_DTE, "strict",
            )
        finally:
            (
                mod.mcp__robinhood_trading__get_equity_quotes,
                mod.mcp__robinhood_trading__get_option_chains,
                mod._fetch_option_instruments_complete,
                mod._fetch_option_quotes_reconciled,
            ) = originals
        assert ranked == []
        outcome = outcomes["GOOD"]
        assert outcome["status"] == "NO_CONTRACT_PASSED", outcome["status"]
        assert len(outcome["contracts"]) == 1
        contract = outcome["contracts"][0]
        assert contract["rejection_reasons"] == ["FUTURE_QUOTE"], contract
        assert contract["quote_updated_at"] == future_quote["updated_at"]
        assert contract["quote_age_seconds"] < -mod.QUOTE_FUTURE_TOLERANCE_SECONDS
        assert summary["excluded_future_quote"] == []


if __name__ == "__main__":
    tests = [(name, fn) for name, fn in sorted(globals().items())
             if name.startswith("test_") and callable(fn)]
    failures = 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASS {name}")
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"FAIL {name}: {type(exc).__name__}: {exc}")
    print(f"{len(tests) - failures}/{len(tests)} passed")
    sys.exit(1 if failures else 0)
