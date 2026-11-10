"""CLI: python -m src.evaluator.run MU

Pipeline (all code, free sources only):
  EDGAR companyfacts -> KPI metrics -> config-anchor sub-scores
  EDGAR 10-K text    -> customer concentration evidence
  Free price feed    -> latest close / 52-week range (fail closed if none)
  Config DCF         -> fair value + distance from current price
  EDGAR peers        -> percentile ranks
Writes output/scores.json with sources, data-quality flags and the
template explanations. No LLM is called at any step.
"""
import json
import sys
from datetime import date

from . import config as cfgmod
from . import dcf as dcfmod
from . import edgar, explanations, filings, metrics as metmod, prices, scoring


def _moat_checks(metrics, concentration, peer_count, checks_cfg):
    checks = []
    capex = metrics.get("capex_latest")
    checks.append({
        "check": "new_entrant_capex_barrier",
        "status": "pass" if (capex or 0) >= checks_cfg["entrant_capex_barrier_min_capex_usd"] else "fail",
        "evidence": f"latest FY capex ${capex:,.0f} vs config barrier ${checks_cfg['entrant_capex_barrier_min_capex_usd']:,.0f}" if capex else "capex missing"})
    stdev = metrics.get("gross_margin_stdev_5y_pts")
    checks.append({
        "check": "product_fungibility_risk (high margin volatility = commodity-like pricing)",
        "status": "risk_flagged" if (stdev or 0) > checks_cfg["margin_fungibility_stdev_pts_over"] else "not_flagged",
        "evidence": f"gross-margin stdev over last 5 FYs = {stdev and round(stdev, 2)} pts (config flag threshold {checks_cfg['margin_fungibility_stdev_pts_over']} pts)"})
    checks.append({
        "check": "concentrated_industry_structure (small EDGAR peer set as oligopoly proxy)",
        "status": "pass" if peer_count <= checks_cfg["oligopoly_peer_count_max_for_concentrated"] else "fail",
        "evidence": f"{peer_count} US-listed EDGAR peers in config peer set"})
    single = (concentration or {}).get("single_customer") or {}
    checks.append({
        "check": "no_single_customer_dependency",
        "status": "pass" if single.get("no_customer_at_or_above_threshold") else "fail_or_unknown",
        "evidence": single.get("quote", "no single-customer sentence found in latest 10-K")})
    checks.append({
        "check": "switching_costs / contract lock-in",
        "status": "not_evaluated_v1",
        "evidence": "No structured free source in v1 quantifies switching costs; flagged, not scored by proxy."})
    return checks


def _evaluate_peers(peer_defs, cfg, tax):
    """Evaluate a list of peer defs ({symbol, cik, name, rationale})
    into metric rows. A peer that fails to load is reported, not
    fatal (fail closed per peer, disclosed by the caller)."""
    rows, failures = [], []
    for peer in peer_defs:
        try:
            _, pseries = edgar.get_company_metrics(cfg["sources"], peer["cik"])
            pm = metmod.compute_metrics(pseries, tax)
            try:
                pprice = prices.get_price(cfg["sources"], peer["symbol"])
                pm = metmod.add_price_metrics(pm, pprice)
            except Exception:
                pprice = None
            rows.append({"symbol": peer["symbol"], "name": peer["name"],
                         "rationale": peer["rationale"],
                         "revenue": pm.get("revenue_ttm") or pm.get("revenue_latest"),
                         "revenue_annual": pm.get("revenue_latest"),
                         "revenue_ttm": pm.get("revenue_ttm"),
                         "gross_margin_latest_pct": pm.get("gross_margin_latest_pct"),
                         "gross_margin_median_5y_pct": pm.get("gross_margin_median_5y_pct"),
                         "roic_median_5y_pct": pm.get("roic_median_5y_pct"),
                         "fcf_margin_latest_pct": pm.get("fcf_margin_latest_pct"),
                         "fcf_margin_median_5y_pct": pm.get("fcf_margin_median_5y_pct"),
                         "price_to_sales": pm.get("price_to_sales"),
                         "ev_to_sales": pm.get("ev_to_sales"),
                         "price": pprice})
        except Exception as exc:
            failures.append({"symbol": peer["symbol"], "error": str(exc)})
    return rows, failures


def evaluate(symbol, cfg):
    ticker = cfg["tickers"]["tickers"][symbol]
    tax = cfg["dcf"]["dcf"]["tax_rate_pct"]

    entity, series = edgar.get_company_metrics(cfg["sources"], ticker["cik"])
    m = metmod.compute_metrics(series, tax, cfg["scoring"].get("inflection"))

    price = prices.get_price(cfg["sources"], symbol)  # raises -> fail closed
    m = metmod.add_price_metrics(m, price)

    fy_year = int(m["fiscal_year_end"][:4]) if m.get("fiscal_year_end") else None
    filing = filings.get_latest_10k_concentration(cfg["sources"], cfg["scoring"]["concentration_rules"],
                                                  ticker["cik"], fy_year)
    concentration = filing["concentration"]
    conc_score = scoring.concentration_score(concentration, cfg["scoring"]["concentration_rules"])

    # Forward buybacks (v5.3, SPEC_V5.3): remaining repurchase
    # authorization from the NEWEST periodic filing (10-K/10-Q text,
    # Item 703 + narrative; post-period board actions included).
    # Fail soft on fetch, fail closed on score: no verifiable
    # remaining figure -> the Value leg stays missing and the other
    # weights renormalise. Execution history (cash actually spent,
    # by fiscal year) comes from XBRL share_repurchases.
    buyback_res = filings.get_periodic_buyback(cfg["sources"], ticker["cik"])
    buyback = buyback_res.get("buyback") or {}
    exec_hist = [{"fy_end": r["end"], "spent_usd": r["value"]}
                 for r in (series.get("share_repurchases") or [])]
    bb_remaining = buyback.get("remaining_usd")
    # R8 guards at the scoring boundary (extractor revision 2): the
    # XBRL execution history is the independent cross-check the filing
    # text alone cannot provide. A sub-$1M "remaining" against >
    # $100M of filed repurchases in any recent fiscal year is an
    # extraction failure — the leg goes missing, never the anchor
    # floor; and a forward yield above the 25% plausibility ceiling
    # is rejected as a figure, not scored.
    if bb_remaining is not None and bb_remaining < 1e6 and \
            any((r["spent_usd"] or 0) > 1e8 for r in exec_hist):
        buyback = dict(buyback)
        buyback["flags"] = list(buyback.get("flags") or []) + \
            ["buyback_remaining_extraction_implausible"]
        buyback["remaining_usd"] = None
        buyback["remaining_basis"] = None
        bb_remaining = None
    if bb_remaining and m.get("market_cap"):
        bb_yield = 100 * bb_remaining / m["market_cap"]
        if bb_yield > 25.0:
            buyback = dict(buyback)
            buyback["flags"] = list(buyback.get("flags") or []) + \
                ["buyback_yield_implausible_ceiling"]
        else:
            m["forward_buyback_yield_pct"] = round(bb_yield, 4)

    # Peers: the subject's sector group from config/peers.yaml (EDGAR +
    # free price feed). The group drives the relative EV/S value
    # sub-score, the moat revenue percentile, and display-only moat
    # percentile ranks.
    peers_cfg = cfg["peers"]
    group_name = (peers_cfg.get("subjects") or {}).get(symbol)
    peer_defs = (peers_cfg.get("groups") or {}).get(group_name, []) if group_name else []
    peer_rows, peer_failures = _evaluate_peers(peer_defs, cfg, tax)

    percentiles = {}
    for key, mkey in (("gross_margin_latest_pct", "gross_margin_latest_pct"),
                      ("fcf_margin_latest_pct", "fcf_margin_latest_pct"), ("price_to_sales", "price_to_sales")):
        percentiles[key] = scoring.percentile_rank(m.get(mkey), [r.get(key) for r in peer_rows])
    # Scale percentile on the same TTM basis as the multiples.
    percentiles["revenue"] = scoring.percentile_rank(
        m.get("revenue_ttm") or m.get("revenue_latest"), [r.get("revenue") for r in peer_rows])
    # Display-only moat percentile ranks vs the subject's own group
    # (NOT scored — the moat score keeps its absolute anchors).
    for key in ("gross_margin_median_5y_pct", "roic_median_5y_pct",
                "fcf_margin_median_5y_pct", "revenue_ttm"):
        # Percentile ranks compare like with like: the subject's FCF
        # margin enters at its RAW median (peers are unblended), even
        # when the v5 inflection blend changed the scored value.
        subject_val = m.get(key) if key != "revenue_ttm" else (
            m.get("revenue_ttm") or m.get("revenue_latest"))
        if key == "fcf_margin_median_5y_pct" and m.get("fcf_margin_median_5y_pct_raw") is not None:
            subject_val = m["fcf_margin_median_5y_pct_raw"]
        percentiles[key] = scoring.percentile_rank(subject_val, [r.get(key) for r in peer_rows])

    # Peer-relative EV/S (v4 value sub-score): cheapness percentile of
    # the subject's EV/S within its curated sector group, subject
    # included in the group size. UNIVERSAL FALLBACK (v5.1): when the
    # curated group cannot rank the subject (no group configured, or
    # the group is below the minimum size), the percentile is computed
    # against the broad scored universe — the config basket in
    # peers.yaml `broad_universe_fallback` — so this leg populates for
    # ANY ticker the engine can score. The fallback is always named
    # "broad_universe_fallback" and disclosed in data_quality; it is
    # never presented as sector peers.
    min_group = peers_cfg.get("relative_ev_to_sales_min_group", 4)
    peer_evs = [r.get("ev_to_sales") for r in peer_rows if r.get("ev_to_sales") is not None]
    evs_group_size = (1 if m.get("ev_to_sales") is not None else 0) + len(peer_evs)
    fallback_rows, fallback_failures = [], []
    fallback_evs = []
    if m.get("ev_to_sales") is not None and evs_group_size < min_group:
        basket_defs = [d for d in (peers_cfg.get("broad_universe_fallback") or [])
                       if d.get("symbol") != symbol]
        if basket_defs:
            fallback_rows, fallback_failures = _evaluate_peers(basket_defs, cfg, tax)
            fallback_evs = [r.get("ev_to_sales") for r in fallback_rows
                            if r.get("ev_to_sales") is not None]
    relative_evs, relative_evs_basis = scoring.relative_ev_to_sales(
        m.get("ev_to_sales"), peer_evs, fallback_evs, min_group)
    m["relative_ev_to_sales_percentile"] = relative_evs

    # Display-only debt/EBITDA (v5, Fix 6): for ipp_power names the
    # industry leverage lens, published as a KPI next to the scored
    # sub-scores. NOT scored; omitted when EBITDA cannot be built from
    # filed TTM operating income + TTM depreciation & amortisation.
    if group_name and group_name.startswith("ipp_power"):
        oi_t, da_t = m.get("operating_income_ttm"), m.get("depreciation_amortization_ttm")
        if oi_t is not None and da_t is not None:
            ebitda_ttm = oi_t + da_t
            m["ebitda_ttm"] = ebitda_ttm
            if m.get("total_debt_latest") is not None and ebitda_ttm:
                m["debt_to_ebitda_ttm"] = m["total_debt_latest"] / ebitda_ttm

    # DCF beta — UNIVERSAL precedence (v5.1): config input first, then
    # a beta computed on the fly by the exact method the config betas
    # were computed with (OLS slope of ~2y weekly returns vs SPY, Yahoo
    # chart API, >=52 weekly return points), then missing. This makes
    # the DCF leg populate for ANY ticker with sufficient price
    # history; a computed beta is disclosed as such, never presented
    # as a config input. Missing remains only when neither source
    # yields a beta (or the FCF base is non-positive — handled below
    # by fair_value never being built on a non-positive base).
    # v5: base is TTM owner FCF/share when available (latest-FY fallback),
    # and the WACC is built on the config's beta adjustment (Blume).
    beta_res = prices.resolve_beta(cfg["sources"], ticker, symbol)
    beta = beta_res["value"]
    wacc = dcfmod.build_wacc(cfg["dcf"]["dcf"], beta, m.get("market_cap"), m.get("total_debt_latest")) if beta is not None else None
    # DCF base (v5.2): the primary base is TTM owner FCF on a
    # NORMALISED capex (median filed capex intensity of the last 5
    # fiscal years, capped at actual spend) when normalisation trims
    # capex; otherwise the actual TTM owner FCF (latest-FY fallback).
    # The actual base is always carried — it anchors the stress floor.
    bases = dcfmod.dcf_bases(m)
    fcf_base, fcf_base_source = bases["primary_per_share"], bases["primary_source"]
    fcf_base_usd = bases["primary_usd"]
    actual_base, actual_base_usd = bases["actual_per_share"], bases["actual_usd"]
    growth_cap_binds = bool(
        (m.get("revenue_cagr_5y_pct") or 0) > cfg["dcf"]["dcf"]["growth"]["cap_pct"])
    path = dcfmod.growth_path(cfg["dcf"]["dcf"], m.get("revenue_cagr_5y_pct"))
    # Honesty guard: a non-positive owner-FCF base cannot anchor a DCF —
    # the leg stays missing (disclosed), never a negative "fair value".
    dcf_missing_reason = None
    if beta is None:
        dcf_missing_reason = "beta_missing_no_config_and_computation_unavailable"
    elif fcf_base is None:
        dcf_missing_reason = "fcf_base_missing"
    elif fcf_base <= 0:
        dcf_missing_reason = "non_positive_fcf_base"
    fv = dcfmod.fair_value(fcf_base, path, wacc["wacc_pct"], cfg["dcf"]["dcf"]["terminal_growth_pct"]) if dcf_missing_reason is None else None
    upside = ((fv["fair_value_per_share"] / price["last_close"] - 1) * 100) if fv else None
    # Stress floor: the same DCF (same path, WACC, terminal growth) on
    # the ACTUAL-capex base. Missing when the actual base cannot anchor
    # one either (no base, or non-positive — e.g. a name whose actual
    # owner FCF is negative but whose normalised base carries the DCF).
    stress_floor, stress_floor_missing_reason = None, None
    if beta is None:
        stress_floor_missing_reason = "beta_missing_no_config_and_computation_unavailable"
    elif actual_base is None:
        stress_floor_missing_reason = "fcf_base_missing"
    elif actual_base <= 0:
        stress_floor_missing_reason = "non_positive_fcf_base"
    else:
        sf = dcfmod.fair_value(actual_base, path, wacc["wacc_pct"],
                               cfg["dcf"]["dcf"]["terminal_growth_pct"])
        stress_floor = {"fair_value_per_share": sf["fair_value_per_share"],
                        "upside_pct": round((sf["fair_value_per_share"] / price["last_close"] - 1) * 100, 2),
                        "fcf_per_share_base": round(actual_base, 4),
                        "fcf_base_usd": actual_base_usd}

    # Scoring inputs assembled from computed metrics only
    m["dcf_upside_pct"] = upside
    m["customer_concentration_score"] = conc_score
    m["revenue_percentile_vs_peers"] = percentiles.get("revenue")
    sc = cfg["scoring"]
    business = scoring.score_section(sc["business"], m)
    value = scoring.score_section(sc["value"], m)
    # V5.3 overvaluation cap on the buyback leg (needs the DCF upside,
    # which is why it applies after scoring, not inside the anchors).
    buyback_capped = scoring.apply_buyback_overvaluation_cap(value, upside)
    moat = scoring.score_section(sc["moat"], m)
    # Moat coverage (v5): share of the moat weight actually scored from
    # filed data. Disclosure only — the score itself is unchanged.
    moat_total_weight = sum(spec["weight"] for spec in sc["moat"].values())
    moat["coverage"] = round(moat["weights_renormalised_over"] / moat_total_weight, 4) \
        if moat_total_weight else None

    # Post-period earnings flag (v5): an Item 2.02 8-K filed after the
    # latest filed period end (and after the latest 10-Q filing) means
    # earnings are out but not yet in filed financials. Disclosure only.
    ppe_date = edgar.post_period_earnings_release(cfg["sources"], ticker["cik"],
                                                  m.get("ttm_end"))

    # Reporting block: when the most recent earnings were reported
    # (latest Item 2.02 8-K, else latest periodic filing) and the
    # period end the filed financials run through. Disclosure only —
    # fail soft to Nones when submissions is unavailable.
    reporting = edgar.reporting_info(cfg["sources"], ticker["cik"],
                                     m.get("ttm_end"))

    data_quality = {
        "trailing_multiples_basis": m.get("multiples_basis"),
        "ttm_end": m.get("ttm_end"),
        "ttm_details": m.get("ttm_details"),
        "consensus_eps": "missing_by_design_v1_no_paid_consensus - forward multiples use filed actuals and the config DCF only",
        "beta": (f"config input - {ticker.get('beta_source')}" if beta_res["origin"] == "config" else
                 (f"Beta computed on the fly (OLS weekly returns vs SPY, {beta_res['points']} points) — the same method used for config betas; DCF populated on that basis."
                  if beta_res["origin"] == "computed_on_the_fly" else
                  "missing - no beta in config/tickers.yaml and on-the-fly computation unavailable (insufficient weekly price history or feed failure); DCF sub-score missing, value weights renormalised")),
        "beta_origin": beta_res["origin"],
        "beta_points": beta_res["points"],
        "beta_missing": beta is None,
        "market_cap_missing": m.get("market_cap") is None,
        "diluted_shares_class_fallback": series.get("_class_share_fallback"),
        "dcf_missing_reason": dcf_missing_reason,
        "capex_normalized": bool(bases["normalized_applied"]),
        "capex_normalization_unavailable": bool(bases["normalization_unavailable"]),
        "capex_intensity_median_5y_pct": m.get("capex_intensity_median_5y_pct"),
        "capex_intensity_observations": m.get("capex_intensity_observations"),
        "normalized_capex_usd": m.get("normalized_capex_ttm"),
        "capex_trimmed_usd": m.get("capex_trimmed_usd"),
        "total_debt_resolution": m.get("total_debt_resolution"),
        "debt_not_determinable_fail_closed": bool(m.get("debt_not_determinable_fail_closed")),
        "debt_verified_zero": bool(m.get("debt_verified_zero")),
        "margin_inflection_blend": m.get("margin_inflection_blend") or {},
        "moat_coverage": moat.get("coverage"),
        "moat_low_coverage": bool(moat.get("coverage") is not None and moat["coverage"] < 0.60),
        "post_period_earnings_release": ppe_date,
        "post_period_earnings_period_end": m.get("ttm_end") if ppe_date else None,
        "finance_lease_liabilities": "excluded_from_total_debt - total debt counts borrowings and debt instruments per the config/sources.yaml resolution priority; operating and finance lease liabilities are not added (several total concepts already embed capital/finance leases)",
        "interest_coverage_missing_reason": m.get("interest_coverage_missing_reason"),
        "sbc_adjusted_fcf": not m.get("sbc_not_reported_fcf_unadjusted_not_used", True),
        "sbc_not_reported_fcf_unadjusted_not_used": bool(m.get("sbc_not_reported_fcf_unadjusted_not_used")),
        "sbc_ttm_unavailable": bool(m.get("sbc_ttm_unavailable")),
        "fcf_basis": "owner_fcf = operating cash flow - capex - stock-based compensation (SBC-adjusted, v3); raw figures kept as fcf_raw_* in kpis",
        "dilution_share_count_growth": {"pct": m.get("dilution_share_count_growth_pct"),
                                        "span_years": m.get("dilution_span_years"),
                                        "annual_points": m.get("dilution_annual_points")},
        "dilution_share_series_discontinuity": bool(m.get("dilution_share_series_discontinuity")),
        "risk_free_and_erp": "config inputs in config/dcf.yaml",
        "sk_hynix_samsung": cfg["peers"].get("excluded_note"),
        "negative_trailing_pe_not_scored": bool(m.get("negative_trailing_pe_not_scored")),
        "eps_derived_from_net_income": bool(m.get("eps_derived_from_net_income")),
        "peer_group": {"name": group_name,
                       "members_evaluated": len(peer_rows),
                       "members_with_ev_to_sales": evs_group_size,
                       "relative_ev_to_sales_min_group": min_group,
                       "relative_ev_to_sales_percentile": relative_evs,
                       "relative_ev_to_sales_basis": relative_evs_basis,
                       "relative_ev_to_sales_group": (
                           group_name if relative_evs_basis == "curated_group" else
                           ("broad_universe_fallback" if relative_evs_basis == "broad_universe_fallback" else None)),
                       "broad_universe_fallback_members_with_ev_to_sales": len(fallback_evs),
                       "relative_ev_to_sales_note": (
                           "No curated sector group covers this ticker (or its group is below the minimum size), so the relative EV/S percentile is computed against the broad scored universe (the broad_universe_fallback basket in config/peers.yaml), not sector peers."
                           if relative_evs_basis == "broad_universe_fallback" else None),
                       "relative_ev_to_sales_missing_reason": (
                           None if relative_evs is not None else
                           ("subject_ev_to_sales_missing" if m.get("ev_to_sales") is None else
                            "group_and_fallback_below_minimum_size"))},
        "peer_failures": peer_failures,
        "fallback_peer_failures": fallback_failures,
        "buybacks": {"remaining_usd": bb_remaining,
                     "remaining_basis": buyback.get("remaining_basis"),
                     "flags": buyback.get("flags") or [],
                     "fetch_error": buyback_res.get("error"),
                     "overvaluation_cap_applied": bool(buyback_capped),
                     "forward_buyback_yield_pct": m.get("forward_buyback_yield_pct")},
        "missing_business_metrics": business["missing_metrics"],
        "missing_value_metrics": value["missing_metrics"],
        "missing_moat_metrics": moat["missing_metrics"],
        "peak_cycle_warning": bool(m.get("gross_margin_latest_pct") and m.get("gross_margin_median_5y_pct")
                                   and m["gross_margin_latest_pct"] - m["gross_margin_median_5y_pct"] > 20),
        "switching_costs": "not_evaluated_v1",
    }

    value_extra = (f" The DCF fair value is ${fv['fair_value_per_share']:,.2f} vs the "
                   f"current ${price['last_close']:,.2f} ({upside:+.1f}%).") if fv else ""
    if fv and bases["normalized_applied"]:
        value_extra += (f" The DCF base is owner FCF on a normalised capex (5-year median "
                        f"filed capex intensity {m.get('capex_intensity_median_5y_pct')}% of "
                        f"revenue; TTM capex trimmed by ${m.get('capex_trimmed_usd'):,.0f}); ")
        value_extra += (f"on actual TTM capex the stress-floor value is "
                        f"${stress_floor['fair_value_per_share']:,.2f}."
                        if stress_floor else
                        "on actual TTM capex the owner FCF base is non-positive, "
                        "so no stress-floor value exists.")
    if dcf_missing_reason == "non_positive_fcf_base":
        value_extra += (" Formula DCF not meaningful — the trailing owner FCF base is "
                        "non-positive, so the DCF leg is excluded (disclosed) rather "
                        "than valued on a negative base.")
    if fv and (growth_cap_binds or m.get("margin_inflection_blend")):
        # v5 DCF disclosure: the formula value rests on trailing owner
        # FCF with filed growth clamped at the cap — for a capped or
        # inflecting profile that is a known limitation, not a verdict.
        value_extra += (" Formula DCF not meaningful for this profile — growth "
                        "capped, valued on trailing owner FCF only.")
    buybacks_block = {
        "remaining_usd": bb_remaining,
        "authorization_total_usd": buyback.get("authorization_total_usd"),
        "cumulative_used_usd": buyback.get("cumulative_used_usd"),
        "remaining_basis": buyback.get("remaining_basis"),
        "remaining_as_of": buyback.get("remaining_as_of"),
        "authorization_date": buyback.get("authorization_date"),
        "effective_from": buyback.get("effective_from"),
        "expiry": buyback.get("expiry"),
        "conditions_quote": buyback.get("conditions_quote"),
        "source_quote": buyback.get("source_quote"),
        "construction_quote": buyback.get("construction_quote"),
        "flags": buyback.get("flags") or [],
        "fetch_error": buyback_res.get("error"),
        "forward_buyback_yield_pct": m.get("forward_buyback_yield_pct"),
        "subscore": (value.get("subscores") or {}).get("forward_buyback_yield_pct"),
        "overvaluation_cap_applied": bool(buyback_capped),
        "source": buyback_res.get("filing"),
        "history": buyback.get("history") or [],
        "execution_history_by_fiscal_year": exec_hist,
    }
    value_extra += explanations.buyback_note(value, buybacks_block)
    business_extra = (explanations.leverage_note(business)
                      + explanations.dilution_note(business, m)
                      + explanations.inflection_note(m))
    if ppe_date:
        business_extra += (f" Scores reflect filed financials through {m.get('ttm_end')}; "
                           f"earnings released {ppe_date} are not yet in a 10-Q.")
    expl = {
        "business": explanations.explain("Business", business, business_extra),
        "value": explanations.explain("Value", value, value_extra),
        "moat": explanations.explain("Moat", moat, explanations.moat_coverage_note(moat)),
    }

    return {
        "symbol": symbol, "entity_name": entity, "snapshot_date": date.today().isoformat(),
        "reporting": reporting,
        "inputs_with_sources": {
            "filing_metrics": {"source": "SEC EDGAR XBRL companyfacts", "cik": ticker["cik"],
                               "fiscal_year_end": m.get("fiscal_year_end")},
            "concentration": {"source": "SEC EDGAR latest 10-K full text", **filing["filing"]},
            "price": price,
            "beta": {"value": beta, "source": beta_res["source"],
                     "origin": beta_res["origin"], "points": beta_res["points"]},
            "dcf_config": "config/dcf.yaml", "scoring_config": "config/scoring.yaml",
        },
        "kpis": {k: v for k, v in m.items() if k not in ("fcf_series_by_year", "fcf_raw_series_by_year")},
        "fcf_series_by_year": m.get("fcf_series_by_year"),
        "fcf_raw_series_by_year": m.get("fcf_raw_series_by_year"),
        "scores": {"business": business, "value": value, "moat": moat},
        "gav_code": scoring.gav_code(m.get("revenue_ttm_growth_pct"),
                                     value.get("total")),
        "dcf": {"fcf_per_share_base": round(fcf_base, 4) if fcf_base else None,
                "fcf_base_source": fcf_base_source,
                "fcf_base_usd": fcf_base_usd,
                "primary_base": "normalized_capex" if bases["normalized_applied"] else "actual",
                "fcf_per_share_base_actual": round(actual_base, 4) if actual_base else None,
                "fcf_base_actual_usd": actual_base_usd,
                "normalized_capex_usd": m.get("normalized_capex_ttm"),
                "actual_capex_ttm_usd": m.get("capex_ttm"),
                "capex_trimmed_usd": m.get("capex_trimmed_usd"),
                "capex_intensity_median_5y_pct": m.get("capex_intensity_median_5y_pct"),
                "capex_intensity_observations": m.get("capex_intensity_observations"),
                "stress_floor": stress_floor,
                "stress_floor_missing_reason": stress_floor_missing_reason,
                "beta_raw": beta,
                "beta_adjusted": wacc["inputs"]["beta_adjusted"] if wacc else None,
                "beta_adjustment": cfg["dcf"]["dcf"].get("beta_adjustment") or "none",
                "growth_cap_binds": growth_cap_binds,
                "growth_path_pct": path, "wacc": wacc, **(fv or {}),
                "current_price": price["last_close"],
                "upside_pct": round(upside, 2) if upside is not None else None,
                "distance_dollars": round(fv["fair_value_per_share"] - price["last_close"], 2) if fv else None},
        "peers": {"group": group_name, "rows": peer_rows,
                  "relative_ev_to_sales_basis": relative_evs_basis,
                  "broad_universe_fallback_rows": fallback_rows,
                  "percentile_ranks_for_subject": percentiles,
                  "excluded_note": cfg["peers"].get("excluded_note")},
        "buybacks": buybacks_block,
        "moat_replicability_checks": _moat_checks(m, concentration, len(peer_rows), sc["moat_checks"]),
        "customer_concentration": concentration,
        "concentration_score": conc_score,
        "data_quality": data_quality,
        "explanations": expl,
    }


def main(argv):
    symbol = (argv[1] if len(argv) > 1 else "MU").upper()
    cfg = cfgmod.load_all()
    if symbol not in cfg["tickers"]["tickers"]:
        print(f"Unknown ticker {symbol}: add it to config/tickers.yaml", file=sys.stderr)
        return 2
    result = evaluate(symbol, cfg)
    out_path = cfgmod.OUTPUT_DIR / "scores.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    s = result["scores"]
    print(f"{symbol} snapshot {result['snapshot_date']}  price ${result['dcf']['current_price']:,.2f} "
          f"({result['inputs_with_sources']['price']['source']})")
    print(f"Business {s['business']['total']}/10 | Value {s['value']['total']}/10 | Moat {s['moat']['total']}/10")
    if result["dcf"].get("fair_value_per_share") is not None:
        print(f"DCF fair value ${result['dcf']['fair_value_per_share']:,.2f} "
              f"({result['dcf']['upside_pct']:+.1f}% vs current, WACC {result['dcf']['wacc']['wacc_pct']}%)")
    else:
        print("DCF fair value unavailable (no beta from config or on-the-fly computation, or missing/non-positive FCF base); DCF sub-score missing, value weights renormalised")
    print(f"Wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
