"""Derived KPIs from EDGAR series. Pure arithmetic, no inputs from
anywhere else.

Two families of numbers, kept deliberately separate:
- ANNUAL history (latest FY, 5-year medians, CAGR, DCF base) — through-
  cycle by design; the scoring config chooses medians for margin levels.
- TRAILING TWELVE MONTHS (TTM) for trailing valuation multiples (P/S,
  EV/S, trailing P/E, FCF yield). TTM is built as
      latest annual + current-FY year-to-date - same YTD a year earlier
  from filed 10-K/10-Q duration rows. Summing the last four quarters is
  not robust (a fiscal Q4 is often never filed standalone), and using the
  latest annual as if it were TTM understates a fast grower's trailing
  revenue badly (NVDA: $215.9B annual vs ~$302.9B true TTM in Nov 2026).
"""
import statistics
from datetime import date, timedelta


def _by_end(series):
    return {row["end"]: row["value"] for row in series}


def _latest(series):
    return series[-1] if series else None


def _aligned_ratio(num, den):
    d = _by_end(den)
    return [(end, v / d[end] * 100) for end, v in _by_end(num).items()
            if end in d and d[end]]


def _median_last(values, n=5):
    tail = values[-n:]
    return statistics.median(tail) if tail else None


def _day(s):
    y, m, d = (int(p) for p in s.split("-"))
    return date(y, m, d)


def ttm_from_periods(rows, annual_start, annual_end, annual_value, as_of_end=None):
    """TTM from filed duration rows for one flow metric.

    rows: [{start, end, value, filed}] covering annual + interim periods.
    Returns a dict: value (None if a post-annual YTD exists but the
    prior-year YTD cannot be found), basis, and the components used.

    as_of_end (optional): ignore interim rows ending after this date —
    used to rebuild the TTM as it stood one year earlier (the prior-year
    TTM behind revenue_ttm_growth_pct). Default None = current behaviour.
    """
    out = {"value": None, "basis": "unavailable", "annual_value": annual_value,
           "annual_end": annual_end, "ytd_current": None, "ytd_prior": None,
           "ttm_end": annual_end}
    if annual_value is None or not rows:
        return out
    a_end = _day(annual_end)
    cap = _day(as_of_end) if as_of_end else None
    post = [r for r in rows if _day(r["end"]) > a_end
            and (cap is None or _day(r["end"]) <= cap)]
    if not post:
        out.update(value=annual_value, basis="annual_only_no_interim_filings_since_fy_end")
        return out
    ttm_end = max(_day(r["end"]) for r in post)
    out["ttm_end"] = ttm_end.isoformat()
    # Current-FY YTD row: ends at the latest interim end and starts at the
    # fiscal-year start (day after the annual period ended). Prefer the
    # row with the closest start; fall back to the longest duration.
    target_start = a_end + timedelta(days=1)
    ending = [r for r in rows if _day(r["end"]) == ttm_end]
    ending.sort(key=lambda r: (abs((_day(r["start"]) - target_start).days),
                               -(_day(r["end"]) - _day(r["start"])).days))
    ytd_cur = ending[0]
    if abs((_day(ytd_cur["start"]) - target_start).days) > 15:
        ytd_cur = max(ending, key=lambda r: (_day(r["end"]) - _day(r["start"])).days)
    out["ytd_current"] = ytd_cur["value"]
    dur = (ttm_end - _day(ytd_cur["start"])).days
    # Prior-year YTD: starts at the annual period's own start (i.e. the
    # beginning of the latest fiscal year) with about the same duration.
    a_start = _day(annual_start)
    prior_cands = []
    for r in rows:
        if abs((_day(r["start"]) - a_start).days) <= 15 and a_start < _day(r["end"]) < a_end:
            if abs(((_day(r["end"]) - _day(r["start"])).days) - dur) <= 20:
                prior_cands.append(r)
    if not prior_cands:
        out["basis"] = "ttm_unavailable_prior_year_ytd_not_filed"
        return out
    prior_cands.sort(key=lambda r: (abs(((_day(r["end"]) - _day(r["start"])).days) - dur),
                                    -(date.fromisoformat(r["filed"]).toordinal() if r.get("filed") else 0)))
    ytd_prior = prior_cands[0]
    out["ytd_prior"] = ytd_prior["value"]
    out["value"] = annual_value + ytd_cur["value"] - ytd_prior["value"]
    out["basis"] = "annual_plus_current_fy_ytd_minus_prior_year_ytd"
    return out


def _annual_start(period_rows, annual_end):
    """Fiscal-year start of the annual period: the 300-400 day row in the
    period rows that ends at the annual end."""
    for r in period_rows or []:
        if r["end"] == annual_end and 300 <= (_day(r["end"]) - _day(r["start"])).days <= 400:
            return r["start"]
    return None


def _first_at(group, end):
    """First concept (config priority order) with an instant value at end."""
    for concept, by_end in (group or {}).items():
        if end in by_end:
            return concept, by_end[end]
    return None


def _debt_families(debt_instants):
    """Family maps for resolution: {name: (current_map, noncurrent_map)}
    where each map is {concept: {end: value}}. Accepts the v4 structure
    ({"families": {name: {"current": ..., "noncurrent": ...}}}) and the
    legacy flat structure ({"current": ..., "noncurrent": ...}), which is
    treated as a single family named "primary"."""
    fams = {}
    for name, fam in (debt_instants.get("families") or {}).items():
        fams[name] = (fam.get("current") or {}, fam.get("noncurrent") or {})
    if debt_instants.get("current") or debt_instants.get("noncurrent"):
        fams.setdefault("primary", (debt_instants.get("current") or {},
                                    debt_instants.get("noncurrent") or {}))
    return fams


BORROWING_COMPONENT_CLASSES = {"combined_borrowings", "convertible_notes",
                               "lines_of_credit", "short_term_borrowings"}


def _component_sum_at(components, end):
    """v5.5 component sum at one balance date: for each component
    class, the total concept when filed, else the sum of whichever of
    its current / noncurrent lines are filed (the classes are disjoint
    balance-sheet lines). Returns None unless at least one BORROWING
    class is present — finance leases alone are not a debt resolution.
    A class whose filed lines are explicitly 0 contributes 0 and still
    counts as present: zero is filed data, not missing data."""
    classes, total, borrowing_found = [], 0.0, False
    for name, cls in (components or {}).items():
        tot = _first_at(cls.get("total") or {}, end)
        if tot:
            value = tot[1]
            parts = {"total_concept": tot[0], "total_value": tot[1]}
        else:
            cur = _first_at(cls.get("current") or {}, end)
            non = _first_at(cls.get("noncurrent") or {}, end)
            if not cur and not non:
                continue
            value = (cur[1] if cur else 0) + (non[1] if non else 0)
            parts = {}
            if cur:
                parts.update({"current_concept": cur[0], "current_value": cur[1]})
            if non:
                parts.update({"noncurrent_concept": non[0], "noncurrent_value": non[1]})
        classes.append({"class": name, "value": value, **parts})
        total += value
        if name in BORROWING_COMPONENT_CLASSES:
            borrowing_found = True
    if classes and borrowing_found:
        return {"total": total, "classes": classes}
    return None


def resolve_total_debt(debt_instants, fallback_latest=None):
    """Resolve total debt at the latest determinable balance date.

    debt_instants: {"totals": {concept: {end: value}},
    "grand_totals": {concept: {end: value}}, "families": {name:
    {"current": {...}, "noncurrent": {...}}}, "components": {class:
    {"total": {...}, "current": {...}, "noncurrent": {...}}}} as built
    by edgar.get_company_metrics (a legacy flat {"current",
    "noncurrent"} map is treated as one family). Dates are tried
    newest first; at each date (config/sources.yaml documents the
    priority):
      (a) a carrying-amount GRAND TOTAL (e.g.
          DebtInstrumentCarryingAmount) wins and REPLACES all
          components — never added to them;
      (b) FAMILY PAIRS summed across families: for each debt family
          (bank/debt, notes payable) with both a current and a
          noncurrent value at the date, take the pair sum; families
          coexist on one balance sheet, so the sums add;
      (c) a single TOTAL concept (first in config list order);
      (d) v5.5 COMPONENT SUM (see _component_sum_at): the sum of the
          disjoint balance-sheet component classes — combined
          borrowings, convertible notes, lines of credit, short-term
          borrowings, finance leases — for filers whose borrowings
          live only under component tags (SMCI: combined-amount loans
          + convertible senior notes, no aggregate at all). Fires
          only when (a)-(c) all failed at that date, so a component
          is never added on top of an aggregate containing it.
    FAIL CLOSED: when nothing resolves, value is None — missing debt
    must never be read as zero debt.
    fallback_latest: {"end", "value"} from the legacy single-concept
    balance-latest path, used only when no instant map was supplied.
    """
    if not debt_instants:
        if fallback_latest is not None:
            return {"value": fallback_latest["value"], "as_of": fallback_latest["end"],
                    "method": "balance_latest_single_concept", "components": {}}
        return {"value": None, "as_of": None, "method": "not_determinable", "components": {}}
    totals = debt_instants.get("totals") or {}
    grand = debt_instants.get("grand_totals") or {}
    fams = _debt_families(debt_instants)
    components = debt_instants.get("components") or {}
    date_set = {e for by_end in totals.values() for e in by_end}
    date_set |= {e for by_end in grand.values() for e in by_end}
    for cur_map, non_map in fams.values():
        for grp in (cur_map, non_map):
            for by_end in grp.values():
                date_set |= set(by_end)
    for cls in components.values():
        for side_map in cls.values():
            for by_end in side_map.values():
                date_set |= set(by_end)
    for end in sorted(date_set, reverse=True):
        gt = _first_at(grand, end)
        if gt:
            return {"value": gt[1], "as_of": end,
                    "method": f"single_total:{gt[0]}",
                    "components": {"concept": gt[0]}}
        parts = []
        for name, (cur_map, non_map) in fams.items():
            cur = _first_at(cur_map, end)
            non = _first_at(non_map, end)
            if cur and non:
                parts.append({"family": name,
                              "current_concept": cur[0], "current_value": cur[1],
                              "noncurrent_concept": non[0], "noncurrent_value": non[1],
                              "family_total": cur[1] + non[1]})
        if parts:
            if len(parts) == 1:
                p = parts[0]
                return {"value": p["family_total"], "as_of": end,
                        "method": "current_plus_noncurrent_pair",
                        "components": {"family": p["family"],
                                       "current_concept": p["current_concept"],
                                       "current_value": p["current_value"],
                                       "noncurrent_concept": p["noncurrent_concept"],
                                       "noncurrent_value": p["noncurrent_value"]}}
            return {"value": sum(p["family_total"] for p in parts), "as_of": end,
                    "method": "family_pairs_sum",
                    "components": {"families": parts}}
        tot = _first_at(totals, end)
        if tot:
            return {"value": tot[1], "as_of": end,
                    "method": f"single_total:{tot[0]}",
                    "components": {"concept": tot[0]}}
        comp = _component_sum_at(components, end)
        if comp:
            return {"value": comp["total"], "as_of": end,
                    "method": "component_sum_fallback",
                    "components": {"classes": comp["classes"]}}
    return {"value": None, "as_of": None, "method": "not_determinable", "components": {}}


def _quarterly_sum_ttm_growth(period_rows):
    """TTM growth from standalone-quarter durations: the sum of the
    latest 4 single quarters vs the sum of the 4 before them.

    A fallback for filers whose YTD bridge cannot be built (post-IPO
    names without a prior-year YTD pair, fiscal-year changes) but who
    file clean standalone quarters. Single-quarter rows only (60-115
    day durations — YTD and annual frames are excluded by the window);
    when two rows share an end, the one closest to a calendar quarter
    wins, then the most recently filed. Fail-closed: fewer than 8
    quarters, a gap between consecutive quarter ends outside 60-130
    days, a non-positive prior window, or a negative current window
    all return None."""
    by_end = {}
    for r in period_rows or []:
        dur = (_day(r["end"]) - _day(r["start"])).days
        if not (60 <= dur <= 115):
            continue
        cur = by_end.get(r["end"])
        if cur is None:
            by_end[r["end"]] = r
            continue
        cur_dur = (_day(cur["end"]) - _day(cur["start"])).days
        if abs(dur - 91) < abs(cur_dur - 91) or (
                abs(dur - 91) == abs(cur_dur - 91)
                and (r.get("filed") or "") > (cur.get("filed") or "")):
            by_end[r["end"]] = r
    ends = sorted(by_end, reverse=True)
    if len(ends) < 8:
        return None
    chosen = ends[:8]
    for newer, older in zip(chosen, chosen[1:]):
        if not (60 <= (_day(newer) - _day(older)).days <= 130):
            return None
    rows = [by_end[e] for e in chosen]
    current = sum(r["value"] for r in rows[:4])
    prior = sum(r["value"] for r in rows[4:])
    if prior <= 0 or current < 0:
        return None
    return {"growth_pct": (current / prior - 1) * 100,
            "current_value": current, "prior_value": prior,
            "prior_end": rows[4]["end"]}


def _latest_period_value(period_rows):
    """Most recent period's value: max end, then shortest duration (the
    standalone quarter, not the YTD cumulative), then latest filed."""
    if not period_rows:
        return None
    max_end = max(r["end"] for r in period_rows)
    cands = [r for r in period_rows if r["end"] == max_end]
    cands.sort(key=lambda r: ((_day(r["end"]) - _day(r["start"])).days,
                              -(date.fromisoformat(r["filed"]).toordinal() if r.get("filed") else 0)))
    return cands[0]["value"]


def _split_adjusted_series(rows):
    """Restate an annual share-count series onto post-split units.

    A step whose ratio is (within 3%) a clean integer factor of 2..25
    is a stock split whose older filings were never restated (SMCI:
    FY21 53.5M -> FY22 536.2M, ratio 10.02 — the 10-for-1 split; the
    FY22+ points were filed post-split in the Feb-2025 10-K while the
    FY21 point still comes from a pre-split filing). Every point
    before such a step is scaled by the split factor (the reciprocal
    for a reverse split) so growth is computed apples-to-apples.
    Returns (adjusted_rows, split_events, unresolved): unresolved is
    True when a >5x / <0.2x step is NOT a clean split — a shares-vs-
    thousands units break runs ~1000x and stays fail-closed, exactly
    as before."""
    rows = sorted((r for r in rows if r.get("value")), key=lambda r: r["end"])
    scales = [1.0] * len(rows)
    events, unresolved = [], False
    cum = 1.0
    for i in range(len(rows) - 1, 0, -1):
        a, b = rows[i - 1]["value"], rows[i]["value"]
        if a > 0:
            ratio = b / a
            if ratio > 5 or ratio < 0.2:
                if ratio > 1:
                    k = round(ratio)
                    clean = 2 <= k <= 25 and abs(ratio - k) / k <= 0.03
                    step = float(k)
                else:
                    k = round(1 / ratio)
                    clean = 2 <= k <= 25 and abs(ratio - 1 / k) / (1 / k) <= 0.03
                    step = 1.0 / k
                if clean:
                    cum *= step
                    events.append({"first_post_split_end": rows[i]["end"],
                                   "factor": step})
                else:
                    unresolved = True
        scales[i - 1] = cum
    adjusted = [dict(r, value=r["value"] * s) for r, s in zip(rows, scales)]
    if not unresolved:
        unresolved = any(
            (b["value"] / a["value"] > 5 or b["value"] / a["value"] < 0.2)
            for a, b in zip(adjusted, adjusted[1:]) if a["value"] > 0)
    return adjusted, events, unresolved


def _quarterly_share_yoy(period_rows):
    """YoY growth of the diluted share count from standalone quarters:
    the latest single quarter (60-115 day duration) against the
    quarter ending 300-430 days before it, with the same clean-split
    adjustment as the annual path. None when either point is absent."""
    by_end = {}
    for r in period_rows or []:
        dur = (_day(r["end"]) - _day(r["start"])).days
        if not (60 <= dur <= 115):
            continue
        cur = by_end.get(r["end"])
        if cur is None or (r.get("filed") or "") > (cur.get("filed") or ""):
            by_end[r["end"]] = r
    if not by_end:
        return None
    latest_end = max(by_end)
    latest = by_end[latest_end]["value"]
    cands = [r for e, r in by_end.items()
             if 300 <= (_day(latest_end) - _day(e)).days <= 430]
    if not cands or not latest:
        return None
    prior_row = min(cands, key=lambda r: abs(
        (_day(latest_end) - _day(r["end"])).days - 365))
    prior = prior_row["value"]
    if not prior or prior <= 0:
        return None
    ratio = latest / prior
    if ratio > 5 or ratio < 0.2:
        if ratio > 1:
            k = round(ratio)
            if not (2 <= k <= 25 and abs(ratio - k) / k <= 0.03):
                return None
            prior = prior * k
        else:
            k = round(1 / ratio)
            if not (2 <= k <= 25 and abs(ratio - 1 / k) / (1 / k) <= 0.03):
                return None
            prior = prior / k
    return (latest / prior - 1) * 100


def compute_metrics(series, tax_rate_pct, inflection_cfg=None):
    rev = series.get("revenue", [])
    periods = series.get("_periods", {})
    balance_latest = series.get("_balance_latest", {})
    out = {"history_years": [r["end"] for r in rev]}
    if not rev:
        return out
    latest_rev = _latest(rev)
    out["revenue_latest"] = latest_rev["value"]
    out["fiscal_year_end"] = latest_rev["end"]
    if len(rev) >= 2:
        prev = rev[-2]["value"]
        out["revenue_growth_latest_pct"] = (latest_rev["value"] / prev - 1) * 100 if prev else None
    n = min(5, len(rev) - 1)
    if n >= 2:
        base = rev[-1 - n]["value"]
        out["revenue_cagr_5y_pct"] = ((latest_rev["value"] / base) ** (1 / n) - 1) * 100 if base else None
        out["revenue_cagr_years"] = n

    # --- TTM flow values (for trailing multiples; annual history above
    # and the median/DCF logic below are intentionally untouched) ---
    ttm_details = {}
    for metric, out_key in (("revenue", "revenue"), ("eps_diluted", "eps_diluted"),
                            ("net_income", "net_income"),
                            ("operating_income", "operating_income"),
                            ("interest_expense", "interest_expense"),
                            ("operating_cash_flow", "operating_cash_flow"),
                            ("share_based_compensation", "share_based_compensation"),
                            ("depreciation_amortization", "depreciation_amortization"),
                            ("capex", "capex")):
        annual = _latest(series.get(metric, []))
        if not annual:
            continue
        start = _annual_start(periods.get(metric), annual["end"])
        if start is None:
            continue
        t = ttm_from_periods(periods.get(metric, []), start, annual["end"], annual["value"])
        out[f"{out_key}_ttm"] = t["value"]
        ttm_details[out_key] = t
    out["ttm_details"] = ttm_details
    out["ttm_end"] = (ttm_details.get("revenue") or {}).get("ttm_end")

    # Prior-year TTM revenue and TTM growth (GAV-Code input), resolved
    # by a fail-closed fallback chain — each step fires only when the
    # previous one produced nothing, and the winning source is recorded
    # in revenue_ttm_growth_source:
    #   1. "ttm_bridge"    — rebuild the TTM as it stood at the same
    #      point of the previous fiscal year, using the same bridge
    #      anchored on the previous annual period with the interim
    #      search capped at the matching date (+20d tolerance, the
    #      same slack the bridge itself allows). A non-positive prior
    #      TTM, or TTM ends that are not roughly a year apart
    #      (300-430 days), fails the step.
    #   2. "quarterly_sum" — sum of the latest 4 standalone quarters
    #      vs the 4 before them (see _quarterly_sum_ttm_growth), for
    #      filers whose YTD pairs cannot be built (post-IPO histories,
    #      fiscal-year changes).
    #   3. "fy_annual"     — latest fiscal-year revenue vs the prior
    #      fiscal year. (When the current TTM basis is annual-only this
    #      is exactly the figure the bridge path produced before the
    #      chain existed: TTM == latest annual there.)
    out["revenue_ttm_prior"] = None
    out["revenue_ttm_prior_end"] = None
    out["revenue_ttm_growth_pct"] = None
    out["revenue_ttm_growth_source"] = None
    rev_ttm = out.get("revenue_ttm")
    growth = None  # (pct, prior_value, prior_end, source)
    if rev_ttm is not None and len(rev) >= 2:
        prev_annual = rev[-2]
        rev_basis = (ttm_details.get("revenue") or {}).get("basis")
        prior_val = prior_end = None
        if rev_basis == "annual_only_no_interim_filings_since_fy_end":
            pass  # step 3 reproduces this figure exactly (TTM == annual)
        elif out.get("ttm_end"):
            p_start = _annual_start(periods.get("revenue"), prev_annual["end"])
            if p_start is not None:
                offset = (_day(out["ttm_end"]) - _day(latest_rev["end"])).days
                cap = (_day(prev_annual["end"])
                       + timedelta(days=offset + 20)).isoformat()
                t_prior = ttm_from_periods(periods.get("revenue", []), p_start,
                                           prev_annual["end"],
                                           prev_annual["value"],
                                           as_of_end=cap)
                if t_prior["value"] is not None:
                    gap = (_day(out["ttm_end"]) - _day(t_prior["ttm_end"])).days
                    if 300 <= gap <= 430:
                        prior_val, prior_end = t_prior["value"], t_prior["ttm_end"]
        if prior_val:
            growth = ((rev_ttm / prior_val - 1) * 100, prior_val, prior_end,
                      "ttm_bridge")
    if growth is None:
        q = _quarterly_sum_ttm_growth(periods.get("revenue", []))
        if q is not None:
            growth = (q["growth_pct"], q["prior_value"], q["prior_end"],
                      "quarterly_sum")
    if growth is None and len(rev) >= 2 and rev[-2]["value"]:
        growth = ((rev[-1]["value"] / rev[-2]["value"] - 1) * 100,
                  rev[-2]["value"], rev[-2]["end"], "fy_annual")
    if growth is not None:
        out["revenue_ttm_growth_pct"] = growth[0]
        out["revenue_ttm_prior"] = growth[1]
        out["revenue_ttm_prior_end"] = growth[2]
        out["revenue_ttm_growth_source"] = growth[3]

    # TTM FCF is SBC-adjusted owner FCF (v3): operating cash flow adds
    # stock comp back, so raw OCF - capex treats equity pay as free.
    # Fail closed — when SBC is reported annually but its TTM bridge
    # cannot be built, or no SBC is reported at all, fcf_ttm stays None
    # (flagged below via sbc_not_reported_fcf_unadjusted_not_used and
    # sbc_ttm_unavailable) rather than silently using the raw figure.
    ocf_t, capex_t = out.get("operating_cash_flow_ttm"), out.get("capex_ttm")
    sbc_t = out.get("share_based_compensation_ttm")
    out["sbc_ttm"] = sbc_t
    out["fcf_raw_ttm"] = (ocf_t - capex_t) if (ocf_t is not None and capex_t is not None) else None
    out["fcf_ttm"] = (ocf_t - capex_t - sbc_t) if (
        ocf_t is not None and capex_t is not None and sbc_t is not None) else None
    out["sbc_ttm_unavailable"] = bool(series.get("share_based_compensation")) and sbc_t is None

    shares_period = _latest_period_value(periods.get("diluted_shares"))
    out["diluted_shares_latest_period"] = shares_period

    # Gross profit: direct concept, else derived as revenue - cost of revenue
    # (some filers, e.g. STX, tag cost but not gross profit).
    gp_series = series.get("gross_profit", [])
    if not gp_series and series.get("cost_of_revenue"):
        cost = _by_end(series["cost_of_revenue"])
        gp_series = [{"end": e, "value": v - cost[e]} for e, v in _by_end(rev).items() if e in cost]
    gm_series = _aligned_ratio(gp_series, rev)
    om_series = _aligned_ratio(series.get("operating_income", []), rev)
    ocf, capex = _by_end(series.get("operating_cash_flow", [])), _by_end(series.get("capex", []))
    sbc_by_end = _by_end(series.get("share_based_compensation", []))
    rev_by_end = _by_end(rev)
    fcf_raw_by_end = {e: ocf[e] - capex[e] for e in ocf if e in capex}
    # Owner FCF (v3): subtract stock-based compensation year by year.
    # Adjusted FCF exists ONLY for years where SBC is reported; if no
    # year reports SBC the FCF margin sub-score goes missing (fail
    # closed) instead of falling back to the unadjusted figure.
    fcf_by_end = {e: fcf_raw_by_end[e] - sbc_by_end[e]
                  for e in sorted(fcf_raw_by_end) if e in sbc_by_end}
    out["sbc_latest"] = sbc_by_end[max(sbc_by_end)] if sbc_by_end else None
    # The fail-closed flag is specific: it means SBC was never reported,
    # so no adjusted year exists and the unadjusted figure is NOT used
    # as a fallback. (A filer can also lack adjusted years because it
    # files no OCF/capex series at all — that shows in fcf_adjusted_years
    # and the missing-metrics list, not in this flag.)
    out["sbc_not_reported_fcf_unadjusted_not_used"] = not sbc_by_end
    out["fcf_adjusted_years"] = sorted(fcf_by_end)
    out["fcf_raw_latest"] = fcf_raw_by_end[max(fcf_raw_by_end)] if fcf_raw_by_end else None
    out["fcf_raw_series_by_year"] = {e: fcf_raw_by_end[e] for e in sorted(fcf_raw_by_end)}
    fcfm_series = [(e, fcf_by_end[e] / rev_by_end[e] * 100) for e in sorted(fcf_by_end) if rev_by_end.get(e)]
    out["fcf_latest"] = fcf_by_end[max(fcf_by_end)] if fcf_by_end else None
    out["fcf_adjusted_latest"] = out["fcf_latest"]
    out["fcf_series_by_year"] = {e: fcf_by_end[e] for e in sorted(fcf_by_end)}

    # Capex intensity history (v5.2): annual capex / annual revenue
    # for the last five fiscal years (by period end) where both are
    # filed. The median is the company's own mid-cycle capex intensity
    # — the input the DCF base normalisation is built on. Fewer than 3
    # observations in the window leaves the median None: normalisation
    # unavailable, fail closed in the DCF base selection.
    intensity_years = []
    for end in sorted(rev_by_end)[-5:]:
        if capex.get(end) and rev_by_end.get(end):
            intensity_years.append((end, capex[end] / rev_by_end[end] * 100))
    out["capex_intensity_series_pct"] = {e: round(v, 4) for e, v in intensity_years}
    out["capex_intensity_observations"] = len(intensity_years)
    out["capex_intensity_median_5y_pct"] = (
        round(statistics.median([v for _, v in intensity_years]), 4)
        if len(intensity_years) >= 3 else None)
    # Normalised capex (v5.2): median-intensity capex on TTM revenue,
    # capped at the actual TTM spend — the rule only trims expansion
    # spikes, it never raises capex above what was actually spent.
    # Normalised owner FCF exists only when every TTM input and the
    # median exist; otherwise it stays None (fail closed).
    med_intensity = out["capex_intensity_median_5y_pct"]
    rev_ttm_val = out.get("revenue_ttm")
    if med_intensity is not None and capex_t is not None and rev_ttm_val:
        out["normalized_capex_ttm"] = min(capex_t, med_intensity / 100 * rev_ttm_val)
    else:
        out["normalized_capex_ttm"] = None
    out["capex_normalized_binds"] = bool(
        out["normalized_capex_ttm"] is not None and capex_t is not None
        and out["normalized_capex_ttm"] < capex_t)
    if out["normalized_capex_ttm"] is None:
        out["capex_trimmed_usd"] = None
    elif out["capex_normalized_binds"]:
        out["capex_trimmed_usd"] = capex_t - out["normalized_capex_ttm"]
    else:
        out["capex_trimmed_usd"] = 0.0
    out["fcf_normalized_ttm"] = (
        ocf_t - out["normalized_capex_ttm"] - sbc_t) if (
        ocf_t is not None and out["normalized_capex_ttm"] is not None
        and sbc_t is not None) else None

    gm_vals = [v for _, v in gm_series]
    out["gross_margin_latest_pct"] = gm_vals[-1] if gm_vals else None
    out["gross_margin_median_5y_pct"] = _median_last(gm_vals)
    out["gross_margin_stdev_5y_pts"] = statistics.pstdev(gm_vals[-5:]) if len(gm_vals) >= 3 else None
    # Relative gross-margin volatility (v5.5): the coefficient of
    # variation of the 5-year gross-margin series — stdev normalised
    # by the margin LEVEL (floored at 5 pts so a near-zero median
    # cannot explode the ratio). Raw point stdev treats a 2.71 pt
    # swing on SMCI's ~13.8% hardware margin (CV 0.20) as far safer
    # than the same swing on an 80% software margin (CV 0.03); the
    # Business score now reads the relative figure. The raw stdev
    # above stays as the disclosed display value (and still feeds the
    # Moat leg and the fungibility flag, unchanged).
    if out["gross_margin_stdev_5y_pts"] is not None \
            and out["gross_margin_median_5y_pct"] is not None:
        out["gross_margin_relative_volatility"] = (
            out["gross_margin_stdev_5y_pts"]
            / max(out["gross_margin_median_5y_pct"], 5.0))
    else:
        out["gross_margin_relative_volatility"] = None
    om_vals = [v for _, v in om_series]
    out["operating_margin_median_5y_pct"] = _median_last(om_vals)
    out["fcf_margin_median_5y_pct"] = _median_last([v for _, v in fcfm_series])
    out["fcf_margin_latest_pct"] = fcfm_series[-1][1] if fcfm_series else None
    # Current-regime margins (v5): latest filed FY and TTM, the inputs
    # the inflection blend below is tested against.
    out["operating_margin_latest_pct"] = om_vals[-1] if om_vals else None
    oi_ttm, rev_ttm = out.get("operating_income_ttm"), out.get("revenue_ttm")
    out["operating_margin_ttm_pct"] = (oi_ttm / rev_ttm * 100) if (
        oi_ttm is not None and rev_ttm) else None
    out["fcf_margin_ttm_pct"] = (out["fcf_ttm"] / rev_ttm * 100) if (
        out.get("fcf_ttm") is not None and rev_ttm) else None
    # Inflection blend (v5, thresholds in config/scoring.yaml's
    # inflection block): when a margin's 5-year median is negative but
    # BOTH the latest FY and the TTM are solidly positive, at scale,
    # and in agreement with each other, the median describes a company
    # that no longer exists — score the metric on the average of the
    # median and the more conservative current reading instead. The
    # unblended medians are kept under *_raw and every blend is
    # disclosed in margin_inflection_blend. A positive median (a
    # cyclical at a peak, e.g. MU) can never trigger this.
    out["operating_margin_median_5y_pct_raw"] = out["operating_margin_median_5y_pct"]
    out["fcf_margin_median_5y_pct_raw"] = out["fcf_margin_median_5y_pct"]
    blend = {}
    if inflection_cfg:
        cagr = out.get("revenue_cagr_5y_pct")
        for metric, latest_key, ttm_key in (
                ("operating_margin_median_5y_pct", "operating_margin_latest_pct",
                 "operating_margin_ttm_pct"),
                ("fcf_margin_median_5y_pct", "fcf_margin_latest_pct",
                 "fcf_margin_ttm_pct")):
            med, lat, ttm = out.get(metric), out.get(latest_key), out.get(ttm_key)
            if med is None or lat is None or ttm is None or cagr is None:
                continue
            current = min(lat, ttm)
            if (med < 0 < current
                    and current - med >= inflection_cfg["min_distance_pts"]
                    and cagr >= inflection_cfg["min_revenue_cagr_pct"]
                    and abs(lat - ttm) <= inflection_cfg["max_latest_ttm_gap_pts"]):
                blended = (med + current) / 2
                blend[metric] = {"median": round(med, 2), "latest_fy": round(lat, 2),
                                 "ttm": round(ttm, 2), "current_used": round(current, 2),
                                 "blended": round(blended, 2)}
                out[metric] = blended
    out["margin_inflection_blend"] = blend

    def lat(name):
        row = _latest(series.get(name, []))
        return row["value"] if row else None

    def blat(name):
        # Balance-sheet "latest" = latest filed period end (10-K or 10-Q),
        # falling back to the annual series.
        row = balance_latest.get(name)
        return row["value"] if row else lat(name)

    cash = blat("cash") or 0
    inv = (blat("investments_current") or 0) + (blat("investments_noncurrent") or 0)
    # Total debt: resolved across ALL debt concepts at the latest balance
    # date. FAIL CLOSED — if it cannot be determined, debt stays None and
    # every debt-derived figure below is None (sub-scores go missing and
    # renormalise) instead of flowing through as zero debt.
    debt_fallback = balance_latest.get("total_debt")
    if debt_fallback is None and lat("total_debt") is not None:
        debt_fallback = {"end": _latest(series.get("total_debt", []))["end"],
                         "value": lat("total_debt")}
    debt_res = resolve_total_debt(series.get("_debt_instants"), debt_fallback)
    debt = debt_res["value"]
    # Verified-zero debt (v5): only when the resolver found nothing AND
    # edgar's positive-evidence test on the raw companyfacts passed
    # (no debt balance rows ever, no recent interest expense, no debt
    # cash flows in the latest FY, complete balance sheet) may debt be
    # read as exactly zero. Everything else stays fail-closed.
    verified_zero = False
    if debt is None:
        vz = series.get("_verified_zero_debt") or {}
        if vz.get("eligible"):
            verified_zero = True
            debt = 0
            debt_res = {"value": 0,
                        "as_of": max((r["end"] for r in balance_latest.values()),
                                     default=None),
                        "method": "verified_zero",
                        "components": {},
                        "evidence": vz.get("checks")}
    out["cash_latest"] = blat("cash")
    out["investments_latest"] = inv if (blat("investments_current") is not None or blat("investments_noncurrent") is not None) else None
    out["total_debt_latest"] = debt
    out["total_debt_resolution"] = debt_res
    out["debt_verified_zero"] = verified_zero
    out["debt_not_determinable_fail_closed"] = debt is None
    out["balance_as_of"] = max((r["end"] for r in balance_latest.values()), default=None)
    # Snapshot balance-sheet strength vs trailing revenue (TTM when the
    # TTM build succeeded, else the latest annual).
    rev_snapshot = out.get("revenue_ttm") or latest_rev["value"]
    if debt is None:
        out["net_cash_latest"] = None
        out["net_cash_to_revenue"] = None
        out["leverage_debt_to_revenue_ttm"] = None
    else:
        out["net_cash_latest"] = cash + inv - debt
        out["net_cash_to_revenue"] = out["net_cash_latest"] / rev_snapshot if rev_snapshot else None
        out["leverage_debt_to_revenue_ttm"] = debt / rev_snapshot if rev_snapshot else None
    # Interest coverage on the same TTM bridge as the other trailing
    # figures. The denominator is GROSS interest expense TTM — the
    # interest_expense concept list in config/sources.yaml contains
    # gross tags only, and interest income is never netted against it
    # (v5.5, SMCI fix: a net denominator collapses toward zero when
    # interest income on cash nearly offsets the gross expense, and
    # manufactured a 235.59x coverage print). The raw ratio is capped
    # at 100.0 for scoring/display, with the uncapped figure kept in
    # interest_coverage_ttm_uncapped for audit. Missing (renormalised)
    # when interest expense is absent or non-positive — a filer with
    # no interest-bearing debt is not awarded a free perfect score
    # (V5.4 Exception 1).
    oi_t, ie_t = out.get("operating_income_ttm"), out.get("interest_expense_ttm")
    if oi_t is not None and ie_t is not None and ie_t > 0:
        uncapped = oi_t / ie_t
        out["interest_coverage_ttm_uncapped"] = uncapped
        out["interest_coverage_ttm"] = min(uncapped, 100.0)
        out["interest_coverage_missing_reason"] = None
    else:
        out["interest_coverage_ttm"] = None
        out["interest_coverage_ttm_uncapped"] = None
        out["interest_coverage_missing_reason"] = (
            "operating_income_ttm_missing" if oi_t is None else
            "interest_expense_ttm_missing_or_nonpositive")
    out["capex_latest"] = lat("capex")
    out["capex_intensity_latest_pct"] = (lat("capex") / latest_rev["value"] * 100) if lat("capex") else None
    out["operating_cash_flow_latest"] = lat("operating_cash_flow")
    out["net_income_latest"] = lat("net_income")
    out["rd_expense_latest"] = lat("rd_expense")
    out["eps_diluted_latest"] = lat("eps_diluted")
    out["diluted_shares_latest"] = lat("diluted_shares")
    # SBC burden on the latest adjusted year, for transparency.
    if out.get("sbc_latest") is not None and fcf_by_end:
        sbc_end = max(fcf_by_end)
        out["sbc_pct_of_revenue_latest"] = (
            sbc_by_end[sbc_end] / rev_by_end[sbc_end] * 100) if rev_by_end.get(sbc_end) else None
    else:
        out["sbc_pct_of_revenue_latest"] = None

    # Dilution: CAGR of the annual diluted weighted-average share count
    # over the available span, up to 5 years back from the latest annual
    # point. Needs >= 2 annual points; with exactly 2 it is the 1-year
    # growth rate. Fallback chain (v5.5): (1) the annual CAGR on the
    # SPLIT-ADJUSTED series — a clean integer split step (SMCI's
    # 10-for-1) is restated onto post-split units instead of failing
    # the leg closed, while a non-split break (units error, ~1000x)
    # still fails closed; (2) when the annual path yields nothing, the
    # YoY growth of the latest standalone quarter vs the year-ago
    # quarter (Q_t vs Q_{t-4}) from the period rows. Missing below
    # that — never guessed — and a data-quality warning is recorded
    # whenever the leg stays missing (under V5.4 it then zero-scores
    # at full weight in Business; it is never silently renormalised
    # away without the warning).
    share_rows = [r for r in series.get("diluted_shares", []) if r.get("value")]
    dil_pct, dil_span, dil_points = None, None, len(share_rows)
    dil_discontinuity = False
    dil_method, split_events = None, []
    if len(share_rows) >= 2:
        # Split adjustment runs on the scoring window only (the last
        # ~5 years), matching the old continuity guard's scope: an
        # ancient break outside the window never blocked the leg.
        raw_latest = max(share_rows, key=lambda r: r["end"])
        cutoff = _day(raw_latest["end"]).toordinal() - int(5 * 365.25)
        window_raw = [r for r in share_rows
                      if _day(r["end"]).toordinal() >= cutoff]
        window, split_events, unresolved = _split_adjusted_series(window_raw)
        latest_row = window[-1] if window else raw_latest
        dil_discontinuity = unresolved
        if not unresolved and len(window) >= 2:
            earliest_row = window[0]
            span_days = (_day(latest_row["end"]) - _day(earliest_row["end"])).days
            if span_days > 0 and earliest_row["value"] > 0:
                dil_span = span_days / 365.25
                dil_pct = ((latest_row["value"] / earliest_row["value"]) ** (1 / dil_span) - 1) * 100
                dil_points = len(window)
                dil_method = "annual_cagr_split_adjusted" if split_events else "annual_cagr"
    q_yoy = _quarterly_share_yoy(periods.get("diluted_shares"))
    if dil_pct is None and q_yoy is not None:
        dil_pct, dil_span, dil_points = q_yoy, 1.0, 2
        dil_method = "quarterly_yoy_fallback"
        dil_discontinuity = False
    out["dilution_share_count_growth_pct"] = dil_pct
    out["dilution_span_years"] = round(dil_span, 2) if dil_span is not None else None
    out["dilution_annual_points"] = dil_points if dil_pct is not None else None
    out["dilution_share_series_discontinuity"] = dil_discontinuity
    out["dilution_method"] = dil_method
    out["dilution_split_adjustments"] = split_events
    out["dilution_quarterly_yoy_pct"] = q_yoy
    if dil_pct is None:
        reason = ("share_series_discontinuity_not_a_split" if dil_discontinuity
                  else "fewer_than_two_share_points_and_no_quarterly_pair"
                  if share_rows else "no_diluted_share_series_any_fallback")
        out["dilution_data_quality_warning"] = (
            f"dilution_share_count_growth_pct missing ({reason}); all "
            "share-count fallbacks exhausted — Business leg zero-scores "
            "at full weight under the V5.4 missing policy")
    else:
        out["dilution_data_quality_warning"] = None

    # Through-cycle ROIC: operating income after config tax, over invested
    # capital (equity + debt - cash - investments), aligned per year, median.
    eq, db, ca = _by_end(series.get("equity", [])), _by_end(series.get("total_debt", [])), _by_end(series.get("cash", []))
    invc = _by_end(series.get("investments_current", []))
    invn = _by_end(series.get("investments_noncurrent", []))
    oi = _by_end(series.get("operating_income", []))
    roics = []
    for e in sorted(oi):
        if e in eq and e in db and e in ca:
            capital = eq[e] + db[e] - ca[e] - invc.get(e, 0) - invn.get(e, 0)
            if capital > 0:
                roics.append(oi[e] * (1 - tax_rate_pct / 100) / capital * 100)
    out["roic_median_5y_pct"] = _median_last(roics)
    out["roic_series_pct"] = roics
    return out


def add_price_metrics(metrics, price):
    """Trailing multiples on a TTM basis: revenue / EPS / FCF come from
    the TTM build when available (falling back to the latest annual, with
    the basis recorded), and market cap uses the most recent period's
    diluted share count.

    Market cap and the EPS / FCF-yield / 52-week legs need only shares
    and price. Only the revenue-ratio legs (P/S, EV/S) need a revenue
    base — and a filer that FILES zero TTM revenue (a wound-down
    collaboration, a pre-revenue period) has data, not missing data:
    zero revenue must not suppress the revenue-independent legs (the
    old truthiness gate did exactly that — TNGX lost even its
    discount-from-52-week-high, a pure price leg). P/S and EV/S stay
    missing on a zero base: a multiple of a zero denominator is not a
    number, and missing is the honest state."""
    shares = metrics.get("diluted_shares_latest_period") or metrics.get("diluted_shares_latest")
    if not shares:
        return metrics
    metrics["shares_used_for_market_cap"] = shares
    mktcap = price["last_close"] * shares
    metrics["market_cap"] = mktcap
    rev = metrics.get("revenue_ttm")
    basis = "ttm"
    if rev is None:
        rev = metrics.get("revenue_latest")
        basis = "annual_fallback"
    if rev:
        metrics["revenue_used_for_multiples"] = rev
        metrics["multiples_basis"] = basis
        metrics["price_to_sales"] = mktcap / rev
        net_cash = metrics.get("net_cash_latest") or 0
        metrics["ev_to_sales"] = (mktcap - net_cash) / rev
    eps = metrics.get("eps_diluted_ttm")
    eps_derived = False
    # Derived-EPS fallback: some filers never tag the standard EPS
    # concepts in companyfacts (ABNB: EarningsPerShareDiluted/Basic
    # are entirely absent — dual-class two-class EPS lives only in
    # filing text), while net income and diluted shares ARE filed.
    # Trailing EPS = filed TTM net income / diluted shares is the
    # same filed-data arithmetic, disclosed via eps_derived flag —
    # without it a profitable filer's P/E goes missing for a tagging
    # reason, not an economic one. Filed EPS always wins when present.
    if eps is None and metrics.get("net_income_ttm") is not None and shares:
        eps = metrics["net_income_ttm"] / shares
        eps_derived = True
    if eps is None:
        eps = metrics.get("eps_diluted_latest")
    if eps is None and metrics.get("net_income_latest") is not None \
            and metrics.get("diluted_shares_latest"):
        eps = metrics["net_income_latest"] / metrics["diluted_shares_latest"]
        eps_derived = True
    metrics["eps_derived_from_net_income"] = eps_derived
    metrics["eps_used_for_trailing_pe"] = eps
    # Negative-P/E guard (v4): a loss-maker's negative P/E is not a
    # valuation signal — on the "lower is cheaper" anchors it used to
    # clamp to a perfect 10. When trailing EPS <= 0 the P/E is MISSING
    # (excluded, weights renormalised) and flagged, never scored.
    if eps is not None and eps <= 0:
        metrics["trailing_pe"] = None
        metrics["negative_trailing_pe_not_scored"] = True
    else:
        metrics["trailing_pe"] = price["last_close"] / eps if eps else None
        metrics["negative_trailing_pe_not_scored"] = False
    fcf = metrics.get("fcf_ttm")
    if fcf is None:
        fcf = metrics.get("fcf_latest")
    metrics["fcf_used_for_yield"] = fcf
    metrics["fcf_yield_latest_pct"] = fcf / mktcap * 100 if fcf is not None else None
    metrics["discount_from_52w_high_pct"] = (price["last_close"] / price["high_52w"] - 1) * 100 if price.get("high_52w") else None
    metrics["price_vs_52w_low_pct"] = (price["last_close"] / price["low_52w"] - 1) * 100 if price.get("low_52w") else None
    return metrics
