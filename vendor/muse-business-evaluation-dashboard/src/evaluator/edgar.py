"""SEC EDGAR XBRL companyfacts extraction.

Annual series (used for through-cycle medians, growth history and the DCF
base) are taken from 10-K / FY rows with an annual duration (~300-400
days); duplicates across filings (restatements) are resolved by keeping,
per fiscal year-end, the row from the most recently filed document.

For TRAILING-TWELVE-MONTH multiples the annual figure alone is stale
whenever interim 10-Qs have been filed since the fiscal year-end, so this
module also returns, per flow metric, every filed duration row of the
chosen concept ("_periods") and, per balance metric, the latest instant
row across 10-K and 10-Q ("_balance_latest"). metrics.py builds true TTM
values from those rows: latest annual + current-FY year-to-date - same
period a year earlier. (Summing the last four quarters is NOT robust: a
fiscal Q4 is often never filed as a standalone quarter.)
"""
from datetime import date, timedelta

from .util import fetch_json

BALANCE_METRICS = {"cash", "investments_current", "investments_noncurrent",
                   "total_debt", "equity", "total_liabilities"}
UNIT_PREFERENCE = {
    "diluted_shares": ["shares"],
    "eps_diluted": ["USD/shares"],
}
PERIOD_FORMS = {"10-K", "10-Q", "10-K/A", "10-Q/A"}


def _parse_day(s):
    y, m, d = (int(p) for p in s.split("-"))
    return date(y, m, d)


def _unit_rows(facts, concept, metric):
    """Raw XBRL rows for one concept in the preferred unit, or []."""
    node = facts.get("facts", {}).get("us-gaap", {}).get(concept)
    if not node:
        return []
    units = node.get("units", {})
    pref = UNIT_PREFERENCE.get(metric, ["USD"])
    unit_key = next((u for u in pref if u in units), None) or (list(units) or [None])[0]
    if unit_key is None:
        return []
    return units[unit_key]


def _annual_from_rows(rows, metric):
    """Annual series from raw rows: 10-K / FY only, annual duration for
    flow metrics, instant rows for balance metrics."""
    by_end = {}
    for row in rows:
        if row.get("form") != "10-K" or row.get("fp") != "FY":
            continue
        if metric in BALANCE_METRICS:
            if row.get("start"):
                continue
        else:
            if not row.get("start"):
                continue
            dur = (_parse_day(row["end"]) - _parse_day(row["start"])).days
            if not (300 <= dur <= 400):
                continue
        end = row["end"]
        if end not in by_end or row.get("filed", "") > by_end[end].get("filed", ""):
            by_end[end] = row
    return [{"end": e, "value": by_end[e]["val"], "filed": by_end[e].get("filed")}
            for e in sorted(by_end)]


def _period_rows(rows):
    """Every duration row (10-K/10-Q, incl. amendments), deduplicated by
    (start, end) keeping the most recently filed value."""
    by_key = {}
    for row in rows:
        if row.get("form") not in PERIOD_FORMS or not row.get("start"):
            continue
        key = (row["start"], row["end"])
        if key not in by_key or row.get("filed", "") > by_key[key].get("filed", ""):
            by_key[key] = row
    return [{"start": s, "end": e, "value": by_key[(s, e)]["val"],
             "filed": by_key[(s, e)].get("filed")} for (s, e) in sorted(by_key)]


def _latest_instant(rows):
    """Latest balance-sheet instant row across 10-K and 10-Q: max period
    end, then most recently filed."""
    best = None
    for row in rows:
        if row.get("form") not in PERIOD_FORMS or row.get("start"):
            continue
        if best is None or (row["end"], row.get("filed", "")) > (best["end"], best.get("filed", "")):
            best = row
    return {"end": best["end"], "value": best["val"], "filed": best.get("filed")} if best else None


def _instants_by_end(rows):
    """Every instant row across 10-K and 10-Q as {end: value},
    deduplicated per period end keeping the most recently filed value."""
    by_end = {}
    for row in rows:
        if row.get("form") not in PERIOD_FORMS or row.get("start"):
            continue
        end = row["end"]
        if end not in by_end or row.get("filed", "") > by_end[end].get("filed", ""):
            by_end[end] = row
    return {e: by_end[e]["val"] for e in sorted(by_end)}


# Debt is resolved in metrics.resolve_total_debt from instant rows of ALL
# debt concepts, grouped by role (see config/sources.yaml for the priority
# rules). These config keys are component lists, not standalone metrics,
# so the main per-metric loop below skips them.
# v4: debt lives in FAMILIES that coexist on one balance sheet — the
# bank/debt family (total_debt_current / total_debt_noncurrent) and the
# notes-payable family (total_notes_current / total_notes_noncurrent) —
# plus carrying-amount GRAND TOTALS (total_debt_grand_total) that replace
# all components when present.
DEBT_GROUPS = (("totals", "total_debt"),
               ("grand_totals", "total_debt_grand_total"))
DEBT_FAMILIES = (("bank_debt", "total_debt_current", "total_debt_noncurrent"),
                 ("notes_payable", "total_notes_current", "total_notes_noncurrent"))
DEBT_COMPONENT_KEYS = {"total_debt_current", "total_debt_noncurrent",
                       "total_notes_current", "total_notes_noncurrent",
                       "total_debt_grand_total"}


# v5 verified-zero debt: the debt resolver fails closed when no debt can
# be determined, which is right for a filer whose debt tagging was missed
# (the CRWV bug) but wrong for a genuinely debt-free filer (CRDO), which
# then earns no balance-sheet credit at all. Debt may resolve to exactly
# zero ONLY on positive evidence from the raw companyfacts:
DEBT_CONCEPT_KEYS = ("total_debt", "total_debt_grand_total",
                     "total_debt_current", "total_debt_noncurrent",
                     "total_notes_current", "total_notes_noncurrent")


def verified_zero_debt_evidence(facts, sources_cfg):
    """Positive-evidence test for a debt-free balance sheet. Eligible
    only when ALL hold: (1) zero balance (instant) rows for every
    configured debt concept at every filed date; (2) no interest-expense
    rows in the latest two fiscal years; (3) no debt proceeds/repayment
    rows in the latest fiscal year; (4) the balance sheet is otherwise
    complete (cash and total liabilities both present). Anything
    ambiguous stays fail-closed in metrics.resolve_total_debt."""
    concepts = sources_cfg["concepts"]
    checks = {}
    debt_rows = 0
    for key in DEBT_CONCEPT_KEYS:
        for concept in concepts.get(key, []):
            rows = _unit_rows(facts, concept, "total_debt")
            debt_rows += sum(1 for r in rows if not r.get("start"))
    checks["debt_balance_rows"] = debt_rows

    rev_series = annual_series(facts, concepts.get("revenue", []), "revenue")
    latest_fy_end = rev_series[-1]["end"] if rev_series else None
    checks["latest_fiscal_year_end"] = latest_fy_end

    ie_rows = []
    for concept in concepts.get("interest_expense", []):
        ie_rows += _period_rows(_unit_rows(facts, concept, "interest_expense"))
    if latest_fy_end:
        cutoff = (_parse_day(latest_fy_end) - timedelta(days=731)).isoformat()
        recent_ie = [r for r in ie_rows if r["end"] > cutoff and r["value"]]
    else:
        recent_ie = [r for r in ie_rows if r["value"]]
    checks["interest_expense_rows_latest_2fy"] = len(recent_ie)

    cf_rows = []
    for key in ("debt_proceeds", "debt_repayments"):
        for concept in concepts.get(key, []):
            cf_rows += _period_rows(_unit_rows(facts, concept, key))
    if latest_fy_end:
        fy_start = (_parse_day(latest_fy_end) - timedelta(days=400)).isoformat()
        fy_cf = [r for r in cf_rows if fy_start < r["end"] <= latest_fy_end and r["value"]]
    else:
        fy_cf = [r for r in cf_rows if r["value"]]
    checks["debt_cash_flow_rows_latest_fy"] = len(fy_cf)

    cash_ok = any(_latest_instant(_unit_rows(facts, c, "cash"))
                  for c in concepts.get("cash", []))
    liab_ok = any(_latest_instant(_unit_rows(facts, c, "total_liabilities"))
                  for c in concepts.get("total_liabilities", []))
    checks["cash_present"] = bool(cash_ok)
    checks["total_liabilities_present"] = bool(liab_ok)
    eligible = (debt_rows == 0 and not recent_ie and not fy_cf
                and cash_ok and liab_ok)
    return {"eligible": bool(eligible), "checks": checks}


PERIODIC_FORMS = {"10-K", "10-Q", "10-K/A", "10-Q/A"}


def parse_reporting(recent, ttm_end=None):
    """Reporting block from an EDGAR submissions `recent` map.

    report_date: filing date of the most recent earnings report — the
    latest 8-K whose items include 2.02; if no such 8-K exists in the
    recent filings list, the filing date of the latest 10-Q/10-K.
    report_form: "8-K earnings release" when the 8-K wins, else the
    periodic form used for the fallback.
    financials_through: period end (reportDate) of the latest
    10-Q/10-K whose data the run uses — the periodic filing whose
    reportDate matches ttm_end when one is supplied and found, else
    the periodic filing with the newest reportDate.
    Pure function of the submissions payload so it is unit-testable
    without the network.
    """
    forms = (recent or {}).get("form") or []
    filed = (recent or {}).get("filingDate") or []
    reports = (recent or {}).get("reportDate") or []
    items_list = (recent or {}).get("items") or []
    rows = []
    for i, form in enumerate(forms):
        rows.append({
            "form": form,
            "filed": filed[i] if i < len(filed) else "",
            "report": reports[i] if i < len(reports) else "",
            "items": items_list[i] if i < len(items_list) else "",
        })
    periodic = [r for r in rows if r["form"] in PERIODIC_FORMS and r["report"]]

    financials_through = None
    if periodic:
        chosen = None
        if ttm_end:
            matching = [r for r in periodic if r["report"] == ttm_end]
            if matching:
                chosen = max(matching, key=lambda r: r["filed"])
        if chosen is None:
            chosen = max(periodic, key=lambda r: (r["report"], r["filed"]))
        financials_through = chosen["report"]

    earnings = [r for r in rows if r["form"] == "8-K"
                and "2.02" in [x.strip() for x in (r["items"] or "").split(",")]]
    if earnings:
        latest_earn = max(earnings, key=lambda r: r["filed"])
        return {"report_date": latest_earn["filed"] or None,
                "report_form": "8-K earnings release",
                "financials_through": financials_through}
    if periodic:
        latest_periodic = max(periodic, key=lambda r: (r["filed"], r["report"]))
        return {"report_date": latest_periodic["filed"] or None,
                "report_form": latest_periodic["form"],
                "financials_through": financials_through}
    return {"report_date": None, "report_form": None,
            "financials_through": financials_through}


def reporting_info(sources_cfg, cik, ttm_end=None):
    """Fetch EDGAR submissions and build the reporting block.
    Fail soft: any fetch or parse failure returns all-None fields
    (financials_through falls back to ttm_end when supplied) and the
    run proceeds without a report date."""
    fallback = {"report_date": None, "report_form": None,
                "financials_through": ttm_end}
    src = sources_cfg["sources"]
    try:
        subs = fetch_json(src["edgar_submissions_url"].format(cik=cik),
                          src["user_agent"], src["timeout_seconds"],
                          src["request_delay_seconds"])
        recent = (subs.get("filings") or {}).get("recent") or {}
        parsed = parse_reporting(recent, ttm_end)
        if parsed.get("financials_through") is None:
            parsed["financials_through"] = ttm_end
        return parsed
    except Exception:
        return fallback


def post_period_earnings_release(sources_cfg, cik, period_end):
    """Filing date of the latest Item 2.02 earnings 8-K that post-dates
    the run's latest filed period end AND the most recent 10-Q filing —
    i.e. earnings released but not yet in a 10-Q. Fail soft: any fetch
    or parse failure returns None and the run proceeds unflagged."""
    if not period_end:
        return None
    src = sources_cfg["sources"]
    try:
        subs = fetch_json(src["edgar_submissions_url"].format(cik=cik),
                          src["user_agent"], src["timeout_seconds"],
                          src["request_delay_seconds"])
        recent = (subs.get("filings") or {}).get("recent") or {}
        forms = recent.get("form") or []
        dates = recent.get("filingDate") or []
        items = recent.get("items") or []
        latest_10q_filed = max((d for f, d in zip(forms, dates)
                                if f in ("10-Q", "10-Q/A")), default=None)
        best = None
        for form, filed, item in zip(forms, dates, items):
            if form != "8-K":
                continue
            if "2.02" not in [x.strip() for x in (item or "").split(",")]:
                continue
            if filed <= period_end:
                continue
            if latest_10q_filed and filed <= latest_10q_filed:
                continue
            if best is None or filed > best:
                best = filed
        return best
    except Exception:
        return None


def annual_series(facts, candidates, metric):
    """Try every candidate concept and keep the FRESHEST series (max latest
    fiscal year-end). Companies change tags over time — e.g. NVDA's total
    revenue moved from RevenueFromContract... to Revenues — so first-found
    can be years stale."""
    best = []
    for concept in candidates:
        series = _annual_from_rows(_unit_rows(facts, concept, metric), metric)
        if series and (not best or series[-1]["end"] > best[-1]["end"]):
            best = series
    return best


def _merged_revenue_series(facts, candidates):
    """Merge the revenue series across candidate concepts — safely.

    The candidates are alternative XBRL tags for the same revenue, and
    filers migrate between them — sometimes mid-stream, so the freshest
    ANNUAL rows live under one tag while the interim period rows live
    under another (APLD: FY2026 annual under Revenues, the 10-Q history
    under RevenueFromContractWithCustomerExcludingAssessedTax). Picking
    a single concept by freshest annual end (the old behaviour) then
    starves the TTM bridge of the other tag's period rows.

    But the tags are not always substitutes: Berkshire reports BOTH
    Revenues (total revenues, incl. investment gains) and contract
    revenue, live, in the same filings, at different values. Mixing
    those per-period would splice two different revenue definitions
    into one series. The merge therefore keeps a strict shape:

      * The PRIMARY concept is the long-standing single-concept pick:
        freshest annual period end, config order breaking ties. Its
        annual series is returned EXACTLY as the old code returned it
        — the annual series drives through-cycle medians, CAGR and
        cross-metric alignment for every filer, and splicing annual
        history across tags corrupts that alignment (a superseded
        tag's ancient years can align with another metric's stale
        series and manufacture a "current" median out of decade-old
        data — seen live on BRK, whose operating-income tag exists
        only in 2012-2017 filings).
      * A secondary concept may fill PERIOD-ROW gaps (the (start,
        end) keys the primary lacks) only when it is a SUPERSEDED
        tag — its own annual series ends strictly before the
        primary's, or it has no annual rows at all. A concept whose
        annual series is as fresh as the primary's is a coexisting
        live tag and contributes nothing. Period rows feed only the
        TTM bridge and the quarterly-sum growth fallback, both
        anchored at the recent end, so gap-filling there cannot
        resurrect stale history.

    Periods are selected, never summed: even for a superseded tag the
    candidates are substitutes, not components. A single-tag filer
    gets exactly its own series back."""
    per_concept = []
    for idx, concept in enumerate(candidates):
        rows = _unit_rows(facts, concept, "revenue")
        if not rows:
            continue
        annual = _annual_from_rows(rows, "revenue")
        periods = _period_rows(rows)
        per_concept.append({
            "idx": idx, "annual": annual, "periods": periods,
            "latest_annual": annual[-1]["end"] if annual else "",
            "latest_period": periods[-1]["end"] if periods else ""})
    if not per_concept:
        return [], []
    primary = max(per_concept,
                  key=lambda c: (c["latest_annual"], -c["idx"]))
    periods_by_key = {(r["start"], r["end"]): r for r in primary["periods"]}
    for entry in per_concept:
        if entry is primary:
            continue
        if entry["latest_annual"] and \
                entry["latest_annual"] >= primary["latest_annual"]:
            continue  # coexisting live tag — different scope, hands off
        for row in entry["periods"]:
            periods_by_key.setdefault((row["start"], row["end"]), row)
    periods = [periods_by_key[k] for k in sorted(periods_by_key)]
    return primary["annual"], periods


def get_company_metrics(sources_cfg, cik):
    src = sources_cfg["sources"]
    url = src["edgar_companyfacts_url"].format(cik=cik)
    facts = fetch_json(url, src["user_agent"], src["timeout_seconds"],
                       src["request_delay_seconds"])
    series, periods, balance_latest = {}, {}, {}
    for metric, candidates in sources_cfg["concepts"].items():
        if metric in DEBT_COMPONENT_KEYS:
            continue
        if metric == "revenue":
            # Revenue is built by MERGING every candidate concept (see
            # _merged_revenue_series): tag migration can split the
            # annual history and the interim period rows across tags,
            # and a single-concept pick starves the TTM bridge.
            merged_annual, merged_periods = _merged_revenue_series(facts, candidates)
            series[metric] = merged_annual
            periods[metric] = merged_periods
            continue
        # Choose the concept with the freshest annual series (or, for a
        # metric with no annual rows at all, the first with any rows).
        chosen_rows, best = [], []
        for concept in candidates:
            rows = _unit_rows(facts, concept, metric)
            annual = _annual_from_rows(rows, metric)
            if annual and (not best or annual[-1]["end"] > best[-1]["end"]):
                chosen_rows, best = rows, annual
            elif not best and not chosen_rows and rows:
                chosen_rows = rows
        series[metric] = best
        if metric in BALANCE_METRICS:
            latest = _latest_instant(chosen_rows)
            if latest:
                balance_latest[metric] = latest
        else:
            periods[metric] = _period_rows(chosen_rows)
    # Debt instants for every debt concept (not just the one concept the
    # annual-series selection picked): resolution at the latest balance
    # date happens in metrics.compute_metrics.
    debt_instants = {}
    for group, key in DEBT_GROUPS:
        per_concept = {}
        for concept in sources_cfg["concepts"].get(key, []):
            inst = _instants_by_end(_unit_rows(facts, concept, "total_debt"))
            if inst:
                per_concept[concept] = inst
        debt_instants[group] = per_concept
    families = {}
    for fam_name, cur_key, non_key in DEBT_FAMILIES:
        fam = {}
        for side, key in (("current", cur_key), ("noncurrent", non_key)):
            per_concept = {}
            for concept in sources_cfg["concepts"].get(key, []):
                inst = _instants_by_end(_unit_rows(facts, concept, "total_debt"))
                if inst:
                    per_concept[concept] = inst
            fam[side] = per_concept
        families[fam_name] = fam
    debt_instants["families"] = families
    series["_debt_instants"] = debt_instants
    series["_verified_zero_debt"] = verified_zero_debt_evidence(facts, sources_cfg)
    series["_periods"] = periods
    series["_balance_latest"] = balance_latest
    apply_class_share_fallback(series, periods, sources_cfg, cik)
    apply_basic_shares_fallback(series, periods, facts, sources_cfg)
    return facts.get("entityName"), series


BASIC_SHARES_CONCEPT = "WeightedAverageNumberOfSharesOutstandingBasic"


def apply_basic_shares_fallback(series, periods, facts, sources_cfg):
    """Basic-shares fallback for a missing diluted share count.

    Some filers tag ONLY WeightedAverageNumberOfSharesOutstandingBasic
    in companyfacts — no diluted concept at all (URGN: basic filed
    through the latest 10-K, diluted entirely absent) — so every
    market-cap / per-share metric fails closed despite a fully filed
    share count, and the class-share fallback finds nothing because
    the filing carries no diluted share facts to validate either.

    Under ASC 260 a loss-maker's diluted denominator EQUALS its basic
    denominator (potential shares are antidilutive and excluded), so
    for a loss-making filer the filed basic series IS the diluted
    series. The fallback therefore fires only when ALL hold:
      (a) no diluted series exists (the class-share fallback has
          already had its chance — a validated filing-text count is
          the better source and always wins);
      (b) a basic annual series exists in companyfacts;
      (c) the filer's latest annual net income is negative.
    Anything else stays missing — fail closed, exactly as today.
    Provenance is recorded on series["_basic_shares_fallback"]."""
    provenance = {"applied": False}
    if series.get("diluted_shares"):
        series["_basic_shares_fallback"] = provenance
        return provenance
    rows = _unit_rows(facts, BASIC_SHARES_CONCEPT, "diluted_shares")
    annual = _annual_from_rows(rows, "diluted_shares")
    if not annual:
        provenance["reason"] = "no_basic_shares_series"
        series["_basic_shares_fallback"] = provenance
        return provenance
    ni = annual_series(facts, sources_cfg["concepts"].get("net_income", []),
                       "net_income")
    if not ni:
        provenance["reason"] = "net_income_series_missing"
    elif ni[-1]["value"] >= 0:
        provenance["reason"] = "latest_annual_net_income_not_negative"
    else:
        series["diluted_shares"] = annual
        periods["diluted_shares"] = _period_rows(rows)
        provenance = {"applied": True,
                      "concept": BASIC_SHARES_CONCEPT,
                      "latest_annual_net_income": ni[-1]["value"],
                      "latest_annual_end": ni[-1]["end"],
                      "basis": "asc_260_antidilution_loss_maker_diluted_equals_basic"}
    series["_basic_shares_fallback"] = provenance
    return provenance


def apply_class_share_fallback(series, periods, sources_cfg, cik):
    """Multi-class diluted-shares fallback (see class_shares.py): when
    companyfacts exposes NO annual diluted-share series at all — the
    filer tags shares only dimensioned by share class — recover the
    combined count from the filer's own periodic filing, but only
    where it reproduces a reported diluted EPS from filed net income
    (self-validating; anything else stays missing, fail-closed).
    Injected rows use the same shapes as companyfacts-derived rows, so
    market cap, per-share DCF bases and the dilution CAGR downstream
    work unchanged. Provenance is recorded on
    series["_class_share_fallback"] either way. Never fires for a
    filer with a company-level companyfacts series."""
    provenance = {"applied": False}
    if series.get("diluted_shares"):
        series["_class_share_fallback"] = provenance
        return provenance
    from .class_shares import get_class_diluted_shares
    fb = get_class_diluted_shares(sources_cfg, cik)
    provenance = {"applied": bool(fb.get("annual") or fb.get("periods")),
                  "error": fb.get("error"),
                  "filings": fb.get("filings") or [],
                  "validations": fb.get("validations") or []}
    if fb.get("annual"):
        series["diluted_shares"] = fb["annual"]
    if fb.get("periods"):
        periods["diluted_shares"] = fb["periods"]
    series["_class_share_fallback"] = provenance
    return provenance
