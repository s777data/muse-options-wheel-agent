"""D-35 unit checks for the momentum-first CSP screen (synthetic, no network).

Run: .venv/bin/python tests/test_momentum_screening.py
(Also pytest-collectable: every check is a test_* function.)

Covers compute_momentum (scripts/csp_screening_momentum.py):
  1. Textbook cross-up: flat base -> linear washout decline -> strong
     rebound on expanding volume. MACD crosses up from below zero,
     Wilder RSI crosses 50, close above SMA20 -> momentum_met True.
  2. Continuing downtrend -> False.
  3. Same cross, but 10 sessions stale -> False (cross_age > lookback).
  4. MACD cross-up with RSI still < 50 -> False (RSI is the blocker).
  5. Insufficient bars -> UNRESOLVED / False, no exception.
And the full_wheel D-35 wrapper (monkeypatched screens, no network):
  6. Momentum runs first; empty momentum recommendations -> regular fallback.
  7. Momentum recommendation -> regular screen never runs.
  8. Momentum raises -> exception propagates, regular screen never runs.
"""
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import csp_screening as cs  # noqa: E402
from scripts import csp_screening_momentum as cm  # noqa: E402
from scripts import full_wheel as fw  # noqa: E402

BASE_VOLUME = 1_000_000


def _series(rise_pcts, *, base_bars=0, spike_last=False):
    """Flat 100 (30 bars) -> linear decline to 70 (45 bars) -> optional
    flat base bars -> percentage rises. Returns (closes, volumes)."""
    closes = [100.0] * 30
    price = 100.0
    step = 30.0 / 45
    for _ in range(45):
        price -= step
        closes.append(price)
    for _ in range(base_bars):
        closes.append(price)
    for pct in rise_pcts:
        price *= (1 + pct)
        closes.append(price)
    volumes = [BASE_VOLUME] * len(closes)
    if spike_last:
        volumes[-1] = 2_500_000
    return closes, volumes


def _bars(closes, volumes):
    return [{"close": c, "volume": v} for c, v in zip(closes, volumes)]


def test_textbook_cross_up_is_met():
    closes, volumes = _series([0.02, 0.025, 0.03, 0.035, 0.04], spike_last=True)
    r = cm.compute_momentum(_bars(closes, volumes))
    assert r["momentum_status"] == "PASS", r
    assert r["momentum_met"] is True, r
    assert r["macd_cross_age_sessions"] is not None \
        and r["macd_cross_age_sessions"] <= cm.MOMENTUM_MACD_CROSS_LOOKBACK_SESSIONS, r
    assert r["macd_at_cross"] < 0, r
    assert r["macd_value"] > r["macd_signal"], r
    assert cm.MOMENTUM_RSI_MIN <= r["momentum_rsi"] <= cm.MOMENTUM_RSI_MAX, r
    assert r["rsi_cross_50"] is True, r
    assert closes[-1] > r["sma_20"], r
    assert r["volume_ratio"] >= cm.MOMENTUM_VOLUME_EXPANSION_MIN, r


def test_continuing_downtrend_is_not_met():
    closes = [100.0] * 30 + [100.0 - (30.0 / 45) * i for i in range(1, 91)]
    volumes = [BASE_VOLUME] * len(closes)
    r = cm.compute_momentum(_bars(closes, volumes))
    assert r["momentum_status"] == "PASS", r
    assert r["momentum_met"] is False, r


def test_stale_cross_is_not_met():
    closes, volumes = _series([0.02, 0.025, 0.03, 0.035, 0.04], spike_last=True)
    price = closes[-1]
    for i in range(1, 11):  # 10 more sessions after the cross window
        price *= 1.005
        closes.append(price)
        volumes.append(BASE_VOLUME)
    r = cm.compute_momentum(_bars(closes, volumes))
    assert r["momentum_met"] is False, r
    assert r["macd_cross_age_sessions"] is not None \
        and r["macd_cross_age_sessions"] > cm.MOMENTUM_MACD_CROSS_LOOKBACK_SESSIONS, r


def test_cross_with_rsi_below_50_is_not_met():
    # A real MACD cross-up from below zero happens here, but the drift
    # is too weak for RSI to reach 50 — RSI is the blocking condition.
    closes, volumes = _series([0.006] * 6)
    r = cm.compute_momentum(_bars(closes, volumes))
    assert r["momentum_met"] is False, r
    assert r["macd_cross_age_sessions"] is not None \
        and r["macd_cross_age_sessions"] <= cm.MOMENTUM_MACD_CROSS_LOOKBACK_SESSIONS, r
    assert r["macd_at_cross"] < 0 and r["macd_value"] > r["macd_signal"], r
    assert r["momentum_rsi"] < cm.MOMENTUM_RSI_MIN, r


def test_insufficient_bars_unresolved_no_exception():
    bars = [{"close": 100.0 - i, "volume": BASE_VOLUME} for i in range(10)]
    r = cm.compute_momentum(bars)
    assert r["momentum_met"] is False, r
    assert r["momentum_status"] == "UNRESOLVED", r
    assert r["momentum_error"], r
    r_empty = cm.compute_momentum([])
    assert r_empty["momentum_met"] is False \
        and r_empty["momentum_status"] == "UNRESOLVED", r_empty


def _patched(momentum_fn, regular_fn):
    orig_mom, orig_reg = fw.csp_screening_momentum, fw.csp_screening
    fw.csp_screening_momentum = SimpleNamespace(run_csp_screening=momentum_fn)
    fw.csp_screening = SimpleNamespace(run_csp_screening=regular_fn)
    return orig_mom, orig_reg


def _restore(orig_mom, orig_reg):
    fw.csp_screening_momentum, fw.csp_screening = orig_mom, orig_reg


def test_fallback_on_empty_momentum_recommendations():
    calls = []

    def mom():
        calls.append("momentum")
        return {"recommendations": [], "passing": 0,
                "error": "No tickers passed Yahoo Finance YoY gate",
                "telegram_message": "momentum msg"}

    def reg():
        calls.append("regular")
        return {"recommendations": [{"ticker": "XYZ"}], "passing": 3,
                "telegram_message": "regular msg"}

    orig = _patched(mom, reg)
    try:
        out = fw.run_csp_screening_momentum_first()
    finally:
        _restore(*orig)
    assert calls == ["momentum", "regular"], calls
    assert out["screen_mode"] == "regular_fallback", out
    assert out["recommendations"] == [{"ticker": "XYZ"}], out
    assert out["telegram_message"] == "regular msg", out
    assert out["momentum_result_summary"]["recommendations_count"] == 0, out
    assert out["momentum_result_summary"]["passing"] == 0, out


def test_no_fallback_when_momentum_recommends():
    calls = []

    def mom():
        calls.append("momentum")
        return {"recommendations": [{"ticker": "ABC"}], "passing": 1,
                "telegram_message": "momentum msg"}

    def reg():
        calls.append("regular")
        return {"recommendations": [{"ticker": "XYZ"}], "passing": 3}

    orig = _patched(mom, reg)
    try:
        out = fw.run_csp_screening_momentum_first()
    finally:
        _restore(*orig)
    assert calls == ["momentum"], calls
    assert out["screen_mode"] == "momentum", out
    assert out["recommendations"] == [{"ticker": "ABC"}], out
    assert out["telegram_message"] == "momentum msg", out


def test_no_fallback_when_momentum_raises():
    calls = []

    def mom():
        calls.append("momentum")
        raise RuntimeError("momentum phase failed")

    def reg():
        calls.append("regular")
        return {"recommendations": [{"ticker": "XYZ"}]}

    orig = _patched(mom, reg)
    try:
        try:
            fw.run_csp_screening_momentum_first()
            raised = False
        except RuntimeError:
            raised = True
    finally:
        _restore(*orig)
    assert raised, "momentum exception must propagate (fail-closed)"
    assert calls == ["momentum"], calls


ALL_TESTS = [
    test_textbook_cross_up_is_met,
    test_continuing_downtrend_is_not_met,
    test_stale_cross_is_not_met,
    test_cross_with_rsi_below_50_is_not_met,
    test_insufficient_bars_unresolved_no_exception,
    test_fallback_on_empty_momentum_recommendations,
    test_no_fallback_when_momentum_recommends,
    test_no_fallback_when_momentum_raises,
]

# ---------------- D-36: Phase 6 Final-score gate (both screens) ----------------

def _rows(*specs):
    return [{"ticker": t, "score": sc} for t, sc in specs]


def test_final_gate_excludes_below_60_both_screens():
    rows = _rows(("AAA", 81.8), ("TNGX", 20.5), ("BBB", 59.9), ("CCC", 60.0))
    for mod in (cs, cm):
        got = mod.select_phase6_cohort_tickers(rows)
        assert got == ["AAA", "CCC"], (mod.__name__, got)
        excluded = mod.phase6_excluded_below_gate(rows)
        assert excluded == ["TNGX", "BBB"], (mod.__name__, excluded)


def test_final_gate_unscored_excluded_and_cap_applies():
    rows = _rows(*[(f"T{i:02d}", 60.0 + i) for i in range(12)], ("UNS", None))
    for mod in (cs, cm):
        got = mod.select_phase6_cohort_tickers(rows)
        assert len(got) == 2 * mod.MAX_PHASE5_VALUATION_CANDIDATES == 10, got
        assert "UNS" not in got
        assert mod.phase6_excluded_below_gate(rows) == []


def test_final_gate_all_below_60_empty_cohort():
    rows = _rows(("TNGX", 20.5), ("LOW", 12.0))
    for mod in (cs, cm):
        assert mod.select_phase6_cohort_tickers(rows) == []
        assert mod.phase6_excluded_below_gate(rows) == ["TNGX", "LOW"]


ALL_TESTS += [
    test_final_gate_excludes_below_60_both_screens,
    test_final_gate_unscored_excluded_and_cap_applies,
    test_final_gate_all_below_60_empty_cohort,
]


if __name__ == "__main__":
    failures = 0
    for test in ALL_TESTS:
        try:
            test()
            print(f"PASS {test.__name__}")
        except Exception as exc:
            failures += 1
            print(f"FAIL {test.__name__}: {type(exc).__name__}: {exc}")
    print(f"\n{len(ALL_TESTS) - failures}/{len(ALL_TESTS)} momentum tests passed")
    sys.exit(1 if failures else 0)
