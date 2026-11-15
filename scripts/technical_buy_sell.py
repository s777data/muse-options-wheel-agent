"""Deterministic Buy + Sell technicals for the 11 AM covered-call brief.

BUY: the copied `technical-analysis-buy-recommend` package from
~/workspace/muse-top-500-recommend (skills/technical-analysis-buy-recommend/code),
invoked directly as code via that repo's technical_adapter (Robinhood MCP,
weekly + monthly, 0-100 bullish score).

SELL: `technical-analysis-sell-recommend` (s777data/options-wheel-agent
@0b0d64a, skills/technical-analysis-sell-recommend/SKILL.md, SHA 84105e1)
ships as a SKILL.md spec only — no code package. This module implements
that spec deterministically on the SAME weekly/monthly candles and
indicators the buy package computes (same aggregation, SMAs, Wilder RSI,
MACD, ATR, pivots), so both scores for a ticker come from one data pull.
A higher sell score = stronger bearish confirmation; it is NOT 100-buy.

Usage:
  python3 technical_buy_sell.py META MU CRDO ZETA
Prints one JSON object: {ticker: {buy: {...}, sell: {...}}}
Fail-closed: a ticker that cannot be scored returns status != "ok"
with score/verdict null — never a fabricated number.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

MAG500 = Path.home() / "workspace" / "muse-top-500-recommend"
sys.path.insert(0, str(MAG500))
sys.path.insert(0, str(MAG500 / "skills" / "technical-analysis-buy-recommend" / "code"))

from scripts import technical_adapter as ta  # noqa: E402


def _sell_from_bars(ticker: str) -> dict:
    from technical_analysis_recommend.aggregation import aggregate_weekly, aggregate_monthly, get_closes, get_highs, get_lows, get_volumes
    from technical_analysis_recommend.indicators import sma, rsi_wilder, macd, atr, pivot_points

    entry = ta._CACHE.get(ticker) or {}
    bars = entry.get("bars")
    if not bars:
        return {"status": "insufficient_data", "ticker": ticker, "score": None, "verdict": None,
                "warnings": ["no cached bars from buy prefetch"]}
    weekly = aggregate_weekly(bars)
    monthly = aggregate_monthly(bars)
    if len(weekly) < 30 or len(monthly) < 26:
        return {"status": "insufficient_data", "ticker": ticker, "score": None, "verdict": None,
                "warnings": [f"insufficient candles: {len(weekly)} weekly, {len(monthly)} monthly"]}

    wc, wh, wl, wv = get_closes(weekly), get_highs(weekly), get_lows(weekly), get_volumes(weekly)
    mc, mh, ml, mv = get_closes(monthly), get_highs(monthly), get_lows(monthly), get_volumes(monthly)

    def last(series, default):
        v = series[-1]
        return default if v is None else v

    w_sma10, w_sma30 = last(sma(wc, 10), wc[-1]), last(sma(wc, 30), wc[-1])
    w_rsi = last(rsi_wilder(wc, 14), 50.0)
    _, _, w_hist = macd(wc, 12, 26, 9); w_hist = last(w_hist, 0.0)
    w_atr = last(atr(wh, wl, wc, 14), 0.0); w_atr_pct = w_atr / wc[-1] * 100 if wc[-1] else 0.0
    ret4w = (wc[-1] / wc[-5] - 1) * 100 if len(wc) >= 5 else 0.0
    ret13w = (wc[-1] / wc[-14] - 1) * 100 if len(wc) >= 14 else 0.0
    m_sma10, m_sma20 = last(sma(mc, 10), mc[-1]), last(sma(mc, 20), mc[-1])
    m_rsi = last(rsi_wilder(mc, 14), 50.0)
    _, _, m_hist = macd(mc, 12, 26, 9); m_hist = last(m_hist, 0.0)
    ret3m = (mc[-1] / mc[-4] - 1) * 100 if len(mc) >= 4 else 0.0
    ret12m = (mc[-1] / mc[-13] - 1) * 100 if len(mc) >= 13 else 0.0

    # Levels / pivots (latest 52 weekly candles, two-bar wing)
    lookback = min(52, len(weekly))
    rh_, rl_ = wh[-lookback:], wl[-lookback:]
    ph, pl = pivot_points(rh_, rl_, window=2)
    close = wc[-1]
    low52 = min(wl[-52:]) if len(wl) >= 52 else min(wl)
    high52 = max(wh[-52:]) if len(wh) >= 52 else max(wh)
    support = low52
    for idx in reversed(pl):
        if rl_[idx] < close:
            support = rl_[idx]; break
    resistance = high52
    for idx in ph:
        if rh_[idx] > close:
            resistance = rh_[idx]; break
    broken = None
    for idx in reversed(pl):
        if rl_[idx] >= close:
            broken = rl_[idx]; break

    # Breakdown confirmation: a COMPLETED weekly candle closing below a
    # pivot low established at least 2 candles before it.
    breakdown = False; breakdown_vol = False
    completed = [(i, c) for i, c in enumerate(weekly) if c.status == "complete"]
    if completed:
        bi, bc = completed[-1]
        # pivots are in lookback coordinates; convert: lookback start index
        start = len(weekly) - lookback
        for p in pl:
            p_abs = start + p
            if p_abs + 2 < bi and bc.close < wl[p_abs]:
                breakdown = True
                if len(wv) >= 21:
                    avg20 = sum(wv[bi - 20:bi]) / 20 if bi >= 20 else sum(wv[-21:-1]) / 20
                    breakdown_vol = bool(avg20 > 0 and bc.volume >= 1.2 * avg20)
                break

    # --- Bearish scoring (sell SKILL.md, exact bands) ---
    weekly_bear = (8 if close < w_sma10 else 0) + (8 if close < w_sma30 else 0) \
        + (6 if w_sma10 < w_sma30 else 0) \
        + (4 if ret13w < 0 else 2 if ret13w <= 5 else 0) \
        + (4 if w_hist < 0 else 0)
    monthly_bear = (8 if close < m_sma10 else 0) + (8 if close < m_sma20 else 0) \
        + (6 if m_sma10 < m_sma20 else 0) \
        + (4 if ret12m < 0 else 2 if ret12m <= 5 else 0) \
        + (4 if m_hist < 0 else 0)

    def rsi_pts(r):
        return 6 if 35 <= r < 50 else 4 if 30 <= r <= 55 else 2 if 25 <= r <= 60 else 0

    def ret_pts(r):
        return 4 if -12 <= r < 0 else 2 if -30 < r < -12 else 2 if 0 <= r <= 5 else 0

    momentum = rsi_pts(w_rsi) + rsi_pts(m_rsi) + ret_pts(ret4w) + ret_pts(ret3m)

    ext = (close / w_sma10 - 1) * 100 if w_sma10 else 0.0
    ext_pts = 6 if -5 <= ext <= 0 else 4 if -10 <= ext < -5 else 2 if -15 <= ext < -10 else 4 if 0 < ext <= 3 else 0
    atr_pts = 6 if w_atr_pct <= 4 else 5 if w_atr_pct <= 7 else 3 if w_atr_pct <= 10 else 1
    dts = (close / support - 1) * 100 if support else 0.0
    dts_pts = 4 if 3 <= dts <= 10 else 2 if 0 <= dts < 3 else 3 if 10 < dts <= 20 else 1 if dts > 20 else 0
    reclaim = (resistance / close - 1) * 100 if close else 0.0
    reclaim_pts = 4 if reclaim <= 5 else 3 if reclaim <= 8 else 2 if reclaim <= 12 else 1
    breakdown_pts = ext_pts + atr_pts + dts_pts + reclaim_pts

    score = max(0, min(100, weekly_bear + monthly_bear + momentum + breakdown_pts))

    weekly_conf = close < w_sma10 and close < w_sma30 and w_sma10 < w_sma30 and w_hist < 0
    monthly_conf = close < m_sma10 and close < m_sma20 and m_sma10 < m_sma20 and m_hist < 0
    monthly_weak = sum([close < m_sma10, close < m_sma20, m_sma10 < m_sma20, m_hist < 0])
    provisional = weekly[-1].status != "complete"
    warnings = []
    if w_rsi < 25 or m_rsi < 25:
        warnings.append("oversold: an RSI is below the 25 floor — fresh high-conviction sell blocked")
    elif w_rsi < 20 or m_rsi < 20:
        warnings.append("oversold warning (RSI below 20)")

    if score >= 90 and weekly_conf and monthly_conf and breakdown and breakdown_vol and w_rsi >= 25 and m_rsi >= 25 and not provisional:
        verdict = "STRONG_SELL"
    elif score >= 85 and weekly_conf and monthly_conf and w_rsi >= 25 and m_rsi >= 25:
        verdict = "SELL"
    elif score >= 75 and weekly_conf and monthly_weak >= 2 and w_rsi >= 20 and m_rsi >= 20:
        verdict = "CONDITIONAL_SELL"
    else:
        verdict = "NO_SELL_SIGNAL"

    sell_trigger = broken if breakdown else support
    return {
        "status": "ok", "ticker": ticker, "score": score, "verdict": verdict,
        "score_components": {"weekly_bearish_trend": weekly_bear, "monthly_bearish_trend": monthly_bear,
                             "downside_momentum_quality": momentum, "breakdown_quality_and_risk": breakdown_pts},
        "confirmation": {"weekly_bearish_confirmed": weekly_conf, "monthly_bearish_confirmed": monthly_conf,
                         "monthly_weakness_count": int(monthly_weak),
                         "support_breakdown_confirmed": breakdown, "breakdown_volume_confirmed": breakdown_vol},
        "indicators": {
            "weekly": {"close": round(close, 2), "sma_10": round(w_sma10, 2), "sma_30": round(w_sma30, 2),
                       "rsi_14": round(w_rsi, 1), "macd_histogram": round(w_hist, 2),
                       "atr_14_pct": round(w_atr_pct, 1), "return_4w_pct": round(ret4w, 1), "return_13w_pct": round(ret13w, 1),
                       "candle_status": weekly[-1].status},
            "monthly": {"close": round(close, 2), "sma_10": round(m_sma10, 2), "sma_20": round(m_sma20, 2),
                        "rsi_14": round(m_rsi, 1), "macd_histogram": round(m_hist, 2),
                        "return_3m_pct": round(ret3m, 1), "return_12m_pct": round(ret12m, 1),
                        "candle_status": monthly[-1].status}},
        "levels": {"weekly_support": round(support, 2), "weekly_resistance": round(resistance, 2),
                   "broken_support": round(broken, 2) if broken is not None else None,
                   "extension_from_10w_pct": round(ext, 1), "downside_to_support_pct": round(dts, 1),
                   "reclaim_risk_pct": round(reclaim, 1)},
        "recommendation": {"sell_trigger": round(sell_trigger, 2) if sell_trigger else None,
                           "trigger_status": "confirmed" if breakdown else "pending" if sell_trigger else "unavailable",
                           "bearish_invalidation": round(min(x for x in [broken, w_sma10, resistance] if x is not None), 2) if verdict != "NO_SELL_SIGNAL" else None,
                           "next_support": round(support, 2),
                           "confirmation_basis": "weekly_close"},
        "warnings": warnings,
    }


def run_both(tickers: list[str]) -> dict:
    tickers = [t.strip().upper() for t in tickers if t.strip()]
    ta.prefetch_technicals(tickers)
    out = {}
    for t in tickers:
        try:
            buy = ta.run_technical(t)
        except Exception as e:
            buy = {"status": "failed", "ticker": t, "score": None, "verdict": None, "warnings": [str(e)]}
        try:
            sell = _sell_from_bars(t)
        except Exception as e:
            sell = {"status": "failed", "ticker": t, "score": None, "verdict": None, "warnings": [str(e)]}
        out[t] = {"buy": buy, "sell": sell}
    return out


if __name__ == "__main__":
    print(json.dumps(run_both(sys.argv[1:]), indent=2))
