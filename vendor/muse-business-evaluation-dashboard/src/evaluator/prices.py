"""Free price feeds, tried in config order. Fails CLOSED: if no feed returns
usable daily history, this raises and the whole run stops with a clear error.
Stooq is listed second: during development it returned an empty body from
this network, while the Yahoo chart API returned full daily history.
"""
import csv
import io
import json
from datetime import datetime, timezone

from .util import fetch_bytes


def _from_yahoo(raw, feed_name):
    data = json.loads(raw.decode("utf-8", "replace"))
    result = (data.get("chart", {}).get("result") or [None])[0]
    if not result:
        return None
    quotes = result["indicators"]["quote"][0]
    stamps = result.get("timestamp") or []
    rows = []
    for ts, close in zip(stamps, quotes.get("close") or []):
        if close is not None:
            rows.append((datetime.fromtimestamp(ts, tz=timezone.utc).date().isoformat(), float(close)))
    if not rows:
        return None
    closes = [c for _, c in rows]
    return {"source": feed_name, "last_close": rows[-1][1], "last_date": rows[-1][0],
            "high_52w": max(closes), "low_52w": min(closes), "points": len(rows)}


def _from_stooq(raw, feed_name):
    text = raw.decode("utf-8", "replace").strip()
    if not text or "Date" not in text.splitlines()[0]:
        return None
    rows = [(r["Date"], float(r["Close"])) for r in csv.DictReader(io.StringIO(text))
            if r.get("Close") not in (None, "", "N/D")]
    if not rows:
        return None
    closes = [c for _, c in rows]
    return {"source": feed_name, "last_close": rows[-1][1], "last_date": rows[-1][0],
            "high_52w": max(closes), "low_52w": min(closes), "points": len(rows)}


PARSERS = {"yahoo_chart": _from_yahoo, "stooq_daily_csv": _from_stooq}

# ---------------------------------------------------------------------------
# Beta: config input first, then the SAME computation the config betas
# were made with (OLS slope of ~2y of weekly returns vs SPY, Yahoo chart
# API), then missing. The Yahoo statistics-feed beta is NOT used (it is
# unreliable / often absent). A computed beta needs >= MIN_BETA_POINTS
# weekly return points; below that it stays missing (fail closed).
# ---------------------------------------------------------------------------
MIN_BETA_POINTS = 52
_WEEKLY_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=2y&interval=1wk"


def _weekly_closes(raw):
    data = json.loads(raw.decode("utf-8", "replace"))
    result = (data.get("chart", {}).get("result") or [None])[0]
    if not result:
        return {}
    closes = result["indicators"]["quote"][0].get("close") or []
    return {ts: float(c) for ts, c in zip(result.get("timestamp") or [], closes)
            if c is not None}


def ols_slope_beta(stock_closes, spy_closes, min_points=MIN_BETA_POINTS):
    """OLS slope of the stock's weekly simple returns on SPY's, over the
    shared weekly timestamps. Returns (beta, n_points); beta is None
    when there are fewer than min_points return points or SPY variance
    is zero. Pure function — unit-tested without network."""
    common = sorted(set(stock_closes) & set(spy_closes))
    stock_rets, spy_rets = [], []
    for prev, cur in zip(common, common[1:]):
        if stock_closes[prev] and spy_closes[prev]:
            stock_rets.append(stock_closes[cur] / stock_closes[prev] - 1)
            spy_rets.append(spy_closes[cur] / spy_closes[prev] - 1)
    n = len(stock_rets)
    if n < min_points:
        return None, n
    mean_s = sum(spy_rets) / n
    mean_t = sum(stock_rets) / n
    var = sum((x - mean_s) ** 2 for x in spy_rets)
    if not var:
        return None, n
    cov = sum((x - mean_s) * (y - mean_t) for x, y in zip(spy_rets, stock_rets))
    return round(cov / var, 4), n


def beta_precedence(config_beta, computed_beta):
    """(origin, value): config wins, then the on-the-fly computation,
    then missing. Pure function — unit-tested without network."""
    if config_beta is not None:
        return "config", config_beta
    if computed_beta is not None:
        return "computed_on_the_fly", computed_beta
    return "missing", None


def compute_beta(sources_cfg, symbol):
    """Beta computed on the fly by the house method. Returns
    {"value", "points", "source"} or None when the history is too
    short or the feed fails (fail soft — caller marks beta missing)."""
    src = sources_cfg["sources"]
    try:
        stock = _weekly_closes(fetch_bytes(_WEEKLY_URL.format(symbol=symbol),
                                           src["user_agent"], src["timeout_seconds"],
                                           src["request_delay_seconds"]))
        spy = _weekly_closes(fetch_bytes(_WEEKLY_URL.format(symbol="SPY"),
                                         src["user_agent"], src["timeout_seconds"],
                                         src["request_delay_seconds"]))
    except Exception:
        return None
    value, n = ols_slope_beta(stock, spy)
    if value is None:
        return None
    from datetime import date as _date
    return {"value": value, "points": n,
            "source": (f"computed on the fly {_date.today().isoformat()}: OLS slope of weekly "
                       f"returns vs SPY (Yahoo chart API), {n} weekly return points — "
                       f"same method as config betas")}


def resolve_beta(sources_cfg, ticker_cfg, symbol):
    """Resolve the beta for a run: config input -> computed on the fly
    -> missing. Returns {"value", "origin", "source", "points"}."""
    config_beta = (ticker_cfg or {}).get("beta")
    computed = compute_beta(sources_cfg, symbol) if config_beta is None else None
    origin, value = beta_precedence(config_beta, computed["value"] if computed else None)
    if origin == "config":
        return {"value": value, "origin": origin,
                "source": (ticker_cfg or {}).get("beta_source"), "points": None}
    if origin == "computed_on_the_fly":
        return {"value": value, "origin": origin,
                "source": computed["source"], "points": computed["points"]}
    return {"value": None, "origin": "missing", "source": None, "points": None}


def get_price(sources_cfg, symbol):
    src = sources_cfg["sources"]
    errors = []
    for feed in src["price_feeds"]:
        url = feed["url"].format(symbol=symbol, symbol_lower=symbol.lower())
        try:
            raw = fetch_bytes(url, src["user_agent"], src["timeout_seconds"],
                              src["request_delay_seconds"])
            parsed = PARSERS[feed["name"]](raw, feed["name"])
            if parsed:
                parsed["url"] = url
                return parsed
            errors.append(f"{feed['name']}: empty/unusable response")
        except Exception as exc:  # keep trying the next configured feed
            errors.append(f"{feed['name']}: {exc}")
    raise RuntimeError(f"No free price feed returned data for {symbol}. Tried: {errors}")
