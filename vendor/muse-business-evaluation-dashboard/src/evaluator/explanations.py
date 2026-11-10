"""Bottom-of-dashboard explanations: TEMPLATE sentences filled with the
actual sub-scores and values the formula produced. No model, no free text
generation — if a template slot has no data, the sentence says so."""


def _top_bottom(subs, n=2):
    items = sorted(subs.items(), key=lambda kv: kv[1]["score"], reverse=True)
    return items[:n], list(reversed(items[-n:])) if len(items) > n else []


def _fmt(metric, entry):
    return f"{metric.replace('_', ' ')} scored {entry['score']}/10 (value {entry['value']})"


def leverage_note(scored):
    """Generic leverage sentence for the Business explanation: names the
    debt-to-revenue and interest-coverage sub-scores when they were
    scored, or states plainly that they were excluded."""
    subs = scored.get("subscores", {})
    parts = []
    for key, label in (("leverage_debt_to_revenue_ttm", "Debt-to-TTM-revenue leverage"),
                       ("interest_coverage_ttm", "Interest coverage")):
        entry = subs.get(key)
        if entry:
            parts.append(f"{label} scored {entry['score']}/10 (value {entry['value']})")
    if parts:
        return " " + " and ".join(parts) + "."
    return (" Leverage checks (debt-to-TTM-revenue and interest coverage) were "
            "excluded: filed debt or interest data could not be determined.")


def dilution_note(scored, metrics):
    """Generic dilution / SBC sentence for the Business explanation:
    names the dilution sub-score and the SBC adjustment when present,
    or states plainly that they were excluded."""
    subs = scored.get("subscores", {})
    entry = subs.get("dilution_share_count_growth_pct")
    parts = []
    if entry:
        span = metrics.get("dilution_span_years")
        parts.append(f"Diluted share count changed {entry['value']:+}%/yr"
                     + (f" over {span} years" if span else "")
                     + f", scored {entry['score']}/10")
    if metrics.get("sbc_not_reported_fcf_unadjusted_not_used"):
        parts.append("no stock-based compensation was reported, so the FCF margin "
                     "sub-score was excluded rather than computed on unadjusted FCF")
    elif metrics.get("sbc_pct_of_revenue_latest") is not None:
        parts.append(f"FCF is SBC-adjusted owner FCF (stock comp was "
                     f"{metrics['sbc_pct_of_revenue_latest']:.1f}% of revenue in the latest year)")
    return (" " + "; ".join(parts) + ".") if parts else ""


def inflection_note(metrics):
    """v5 inflection-blend disclosure for the Business explanation:
    names each margin metric that was blended, with the median, the
    current filed readings and the blended value actually scored."""
    blend = metrics.get("margin_inflection_blend") or {}
    if not blend:
        return ""
    parts = []
    for metric, d in blend.items():
        label = metric.replace("_median_5y_pct", "").replace("_", " ")
        parts.append(f"{label} inflection blend applied: 5-yr median {d['median']:+.1f}% "
                     f"vs current filed regime {d['current_used']:+.1f}% (latest FY "
                     f"{d['latest_fy']:+.1f}%, TTM {d['ttm']:+.1f}%) — scored on the "
                     f"blend {d['blended']:+.1f}%")
    return " " + "; ".join(parts) + "."


def buyback_note(scored, buybacks):
    """V5.3 forward-buyback sentence for the Value explanation: names
    the leg, the remaining authorization, its yield and score, with
    the source filing; states plainly when it was excluded, and
    discloses the overvaluation cap when it bound."""
    subs = scored.get("subscores", {})
    entry = subs.get("forward_buyback_yield_pct")
    if not buybacks or buybacks.get("remaining_usd") is None:
        return (" Forward buyback yield was excluded: no verifiable remaining "
                "repurchase authorization in the latest periodic filing.")
    src = buybacks.get("source") or {}
    src_txt = f" per the {src.get('form')} filed {src.get('filed')}" if src.get("form") else ""
    txt = (f" Forward buyback yield scored {entry['score']}/10: "
           f"${buybacks['remaining_usd']:,.0f} of repurchase authorization remaining "
           f"({entry['value']:.2f}% of market cap){src_txt}.") if entry else ""
    if entry and entry.get("capped_from") is not None:
        txt += (f" The sub-score was capped at {entry['score']}/10 (from "
                f"{entry['capped_from']}) because the DCF upside is below -50%: "
                f"buying back deeply overvalued stock destroys per-share value.")
    if buybacks.get("effective_from"):
        txt += f" Purchases under the current authorization run from {buybacks['effective_from']}."
    return txt


def moat_coverage_note(moat):
    """v5 moat-coverage disclosure: when part of the moat weight could
    not be scored from filed data, say which metrics were missing and
    that the rest renormalised."""
    coverage = moat.get("coverage")
    missing = moat.get("missing_metrics") or []
    if coverage is None or coverage >= 1 or not missing:
        return ""
    names = ", ".join(m.replace("_", " ") for m in missing)
    return (f" Moat coverage is {coverage:.0%} of the moat weight: missing "
            f"metrics ({names}) were excluded and the remaining weights "
            f"renormalised.")


def explain(section_name, scored, extra=""):
    subs = scored.get("subscores", {})
    total = scored.get("total")
    if total is None or not subs:
        return f"{section_name} score unavailable: required filed metrics were missing."
    top, bottom = _top_bottom(subs)
    s1 = (f"{section_name} score is {total}/10, driven mainly by "
          + " and ".join(_fmt(m, e) for m, e in top) + ".")
    s2 = ""
    if bottom:
        s2 = (" The main drags were "
              + " and ".join(_fmt(m, e) for m, e in bottom) + ".")
    return (s1 + s2 + extra).strip()
