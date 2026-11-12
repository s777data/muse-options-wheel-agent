"""Config-driven piecewise scoring. The ONLY math here is interpolation
between the anchor points in config/scoring.yaml and a weighted mean.
Missing metrics are excluded and the remaining weights renormalised;
every exclusion is reported by the caller in data_quality.

Missing-policy (V5.4, config/scoring.yaml `missing_policy`, set by
BIG SANDY 2026-11-11 from the ANGX check): a section or an individual
metric can instead be scored 0 at its full weight when missing. The
Business section uses this for every leg — no Business leg is
optional — with exactly two exceptions he approved: interest
coverage stays renormalised when it is missing because the filer
reports no interest expense (there is nothing to cover), and the
net-cash / leverage legs stay renormalised when debt cannot be
determined from the filings (fail-closed stays fail-closed; the
zero is a policy score for a missing leg, never an invented debt
figure). In Value the zero treatment is optional and applies only
to the trailing P/E and FCF-yield legs when they are missing
because the filer is not profitable; the DCF and buyback legs
still renormalise. A zero-scored leg stays in `missing_metrics`
(it WAS missing) and is additionally listed in
`zero_scored_missing_metrics`, with its sub-score entry carrying
`zero_scored_missing: True` and value None — the missing state is
disclosed, never disguised as filed data."""


def interpolate(value, anchors):
    pts = sorted((float(x), float(y)) for x, y in anchors)
    if value <= pts[0][0]:
        return pts[0][1]
    if value >= pts[-1][0]:
        return pts[-1][1]
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        if x0 <= value <= x1:
            if x1 == x0:
                return y1
            return y0 + (y1 - y0) * (value - x0) / (x1 - x0)
    return pts[-1][1]


def score_section(section_cfg, metric_values, missing_as_zero=False, zero_metrics=None,
                  zero_exclude_metrics=None):
    """Weighted mean of a section's sub-scores.

    Default (pre-V5.4) behaviour: a missing metric is excluded and the
    remaining weights renormalise. With `missing_as_zero` every missing
    metric scores 0 at its full weight; with `zero_metrics` (a set of
    metric names) only those missing metrics score 0 and the rest still
    renormalise. `zero_exclude_metrics` names exceptions: those missing
    metrics renormalise even under `missing_as_zero` (the caller decides
    eligibility from the missing reason — see run.py's Business
    policy). Zero-scored metrics keep their weight in the denominator,
    so `weights_renormalised_over` then counts them too.
    """
    zero_metrics = set(zero_metrics or ())
    zero_exclude_metrics = set(zero_exclude_metrics or ())
    subs, missing, zeroed, weight_sum = {}, [], [], 0.0
    for metric, spec in section_cfg.items():
        value = metric_values.get(metric)
        if value is None:
            missing.append(metric)
            if (missing_as_zero or metric in zero_metrics) \
                    and metric not in zero_exclude_metrics:
                subs[metric] = {"value": None, "score": 0.0,
                                "weight": spec["weight"],
                                "zero_scored_missing": True}
                zeroed.append(metric)
                weight_sum += spec["weight"]
            continue
        score = interpolate(float(value), spec["anchors"])
        subs[metric] = {"value": round(float(value), 4), "score": round(score, 3),
                        "weight": spec["weight"]}
        weight_sum += spec["weight"]
    total = None
    if weight_sum:
        total = sum(s["score"] * s["weight"] for s in subs.values()) / weight_sum
    return {"subscores": subs, "total": round(total, 2) if total is not None else None,
            "missing_metrics": missing, "zero_scored_missing_metrics": zeroed,
            "weights_renormalised_over": round(weight_sum, 4)}


def business_zero_exclusions(metric_values, business_policy):
    """The two approved exceptions to Business missing-as-zero (V5.4),
    decided from the recorded missing reasons, never guessed:

    - interest_coverage_ttm, when it is missing because the filer
      reports no (or non-positive) interest expense — there is no
      interest to cover, so the leg renormalises instead of scoring 0.
      Coverage missing for any other reason (e.g. operating income
      missing) still scores 0.
    - net_cash_to_revenue and leverage_debt_to_revenue_ttm, when debt
      could not be determined from the filings
      (`debt_not_determinable_fail_closed`) — fail-closed stays
      fail-closed: the legs renormalise and the failure stays flagged,
      rather than converting an extraction failure into a 0.

    Returns (excluded metric set, applied exception names)."""
    business_policy = business_policy or {}
    excluded, applied = set(), []
    if business_policy.get("exception_interest_coverage_no_interest_expense", True) \
            and metric_values.get("interest_coverage_missing_reason") == \
            "interest_expense_ttm_missing_or_nonpositive":
        excluded.add("interest_coverage_ttm")
        applied.append("interest_coverage_no_interest_expense")
    if business_policy.get("exception_debt_legs_when_debt_not_determinable", True) \
            and metric_values.get("debt_not_determinable_fail_closed"):
        excluded.update(("net_cash_to_revenue", "leverage_debt_to_revenue_ttm"))
        applied.append("debt_legs_debt_not_determinable")
    return excluded, applied


def is_unprofitable(metric_values):
    """Loss-maker test for the Value missing-policy: the negative-P/E
    guard fired (trailing EPS <= 0), or the EPS actually used for the
    trailing P/E is non-positive, or filed TTM / latest net income is
    non-positive. Pure filed-data arithmetic — no estimates."""
    if metric_values.get("negative_trailing_pe_not_scored"):
        return True
    eps = metric_values.get("eps_used_for_trailing_pe")
    if eps is not None and eps <= 0:
        return True
    for key in ("net_income_ttm", "net_income_latest"):
        ni = metric_values.get(key)
        if ni is not None and ni <= 0:
            return True
    return False


def value_zero_metrics(metric_values, policy_metrics):
    """The Value legs eligible to score 0 under the optional V5.4
    policy: the configured legs (trailing P/E, FCF yield) that are
    actually missing, and only when the filer is not profitable.
    A profitable filer whose leg is missing for a data reason keeps
    the renormalise treatment — the zero is for unprofitability, not
    for missing paperwork."""
    if not policy_metrics or not is_unprofitable(metric_values):
        return set()
    return {m for m in policy_metrics if metric_values.get(m) is None}


def apply_buyback_overvaluation_cap(value_result, dcf_upside_pct, cap=4.0, threshold_pct=-50.0):
    """V5.3 overvaluation cap: buying back deeply overvalued stock
    destroys per-share value, so when the DCF upside is worse than
    `threshold_pct`, the forward-buyback sub-score is capped at `cap`
    and the Value total recomputed from the capped sub-scores.
    Returns True when the cap bound."""
    subs = value_result.get("subscores", {})
    entry = subs.get("forward_buyback_yield_pct")
    if entry is None or dcf_upside_pct is None:
        return False
    if dcf_upside_pct >= threshold_pct or entry["score"] <= cap:
        return False
    entry["capped_from"] = entry["score"]
    entry["score"] = cap
    weight_sum = sum(s["weight"] for s in subs.values())
    if weight_sum:
        value_result["total"] = round(
            sum(s["score"] * s["weight"] for s in subs.values()) / weight_sum, 2)
    return True


def concentration_score(concentration, rules):
    """Rule table from config/scoring.yaml applied to filing facts."""
    if not concentration or not concentration.get("single_customer"):
        return None
    single = concentration["single_customer"]
    top = concentration.get("top_ten") or {}
    if single.get("no_customer_at_or_above_threshold"):
        score = rules["base_if_no_single_customer_at_threshold"]
    else:
        score = rules["base_if_single_customer_present"]
        pct = single.get("max_single_customer_pct") or 0
        if pct > 20:
            score -= (pct - 20) * rules["single_customer_extra_penalty_per_pct_over_20"]
    top_pct = top.get("top_ten_share_pct")
    if top_pct is not None and top_pct > rules["top10_free_allowance_pct"]:
        score -= (top_pct - rules["top10_free_allowance_pct"]) * rules["top10_penalty_per_pct_over_allowance"]
    return max(rules["score_floor"], min(rules["score_ceiling"], round(score, 3)))


def percentile_rank(value, peer_values):
    vals = [v for v in peer_values if v is not None]
    if value is None or not vals:
        return None
    below = sum(1 for v in vals if v < value)
    equal = sum(1 for v in vals if v == value)
    return round((below + 0.5 * equal) / len(vals) * 100, 1)


def relative_ev_to_sales(subject_evs, curated_peer_evs, fallback_peer_evs, min_group=4):
    """Relative EV/S cheapness percentile with source precedence:
    the curated sector group first; when that group (subject included)
    is below min_group, a broad-universe fallback basket; else missing.
    Returns (percentile, basis) where basis is "curated_group",
    "broad_universe_fallback" or None. The subject's own EV/S is
    required — without it there is nothing to rank."""
    if subject_evs is None:
        return None, None
    curated = [v for v in (curated_peer_evs or []) if v is not None]
    if 1 + len(curated) >= min_group:
        return cheapness_percentile(subject_evs, curated), "curated_group"
    fallback = [v for v in (fallback_peer_evs or []) if v is not None]
    if 1 + len(fallback) >= min_group:
        return cheapness_percentile(subject_evs, fallback), "broad_universe_fallback"
    return None, None


def cheapness_percentile(value, peer_values):
    """Cheapness percentile of the subject's multiple (e.g. EV/S) within
    its group, subject INCLUDED in the group size:
        (peers with a HIGHER value + 0.5 x ties incl. self) / group x 100
    The cheapest member approaches 100, the most expensive approaches 0.
    Same mid-rank convention as percentile_rank, inverted and with the
    subject counted in the denominator."""
    if value is None:
        return None
    vals = [v for v in peer_values if v is not None]
    group = [value] + vals
    higher = sum(1 for v in vals if v > value)
    ties = sum(1 for v in group if v == value)
    return round((higher + 0.5 * ties) / len(group) * 100, 1)


# --- GAV-Code (2026-11-09): a fully code-derived Growth-Adjusted Value ---
# The heatmap GAV blends Value across four scorers, three of them models
# (Muse DD / Gemini / OpenAI) — not reproducible in a zero-token daily
# run. GAV-Code keeps the identical formula shape and band tables but
# takes Value from this engine alone:
#     GAV-Code = 0.5 x growth band score (TTM revenue growth, filed data)
#              + 0.5 x engine Value (0-10 rescaled to 0-100)
# Fail-closed: a missing TTM growth or a missing Value total leaves the
# score None with the reason recorded — never estimated.
GAV_GROWTH_BANDS = [(60, 95), (35, 80), (20, 65), (10, 50), (5, 35), (0, 20)]
GAV_LABELS = [(85, "🚀💰 Elite — rocket growth, bag of value"),
              (70, "📈💵 Strong — climbing growth, solid cash"),
              (55, "⚖️🪙 Fair — balanced growth, a coin’s worth of value"),
              (40, "📉💸 Thin — sliding growth, money leaking"),
              (0, "🧊🚫 None — frozen growth, no value")]


def growth_band_score(growth_pct):
    """Band score for a TTM revenue growth percentage: strictly above
    +60 -> 95, +35..60 -> 80, +20..35 -> 65, +10..20 -> 50, +5..10 -> 35,
    0..+5 -> 20, zero or negative -> 5. None in, None out."""
    if growth_pct is None:
        return None
    for threshold, score in GAV_GROWTH_BANDS:
        if growth_pct > threshold:
            return score
    return 5


def gav_label(score):
    """Full band label for a 0-100 GAV(-Code) score, matching the
    heatmap wording. None in, None out."""
    if score is None:
        return None
    for threshold, label in GAV_LABELS:
        if score >= threshold:
            return label
    return None


def gav_code(ttm_revenue_growth_pct, value_total, growth_source=None):
    """Compose GAV-Code from the TTM revenue growth percentage and the
    engine Value total (0-10). Returns the block stored in run output:
    score (0-100, 1dp) + label + components + missing_reason.
    growth_source names the metrics fallback chain step that produced
    the growth figure ("ttm_bridge" | "quarterly_sum" | "fy_annual");
    disclosed, never scored."""
    growth_score = growth_band_score(ttm_revenue_growth_pct)
    missing = None
    if growth_score is None:
        missing = "ttm_revenue_growth_missing"
    if value_total is None:
        missing = "value_score_missing" if missing is None \
            else "ttm_revenue_growth_and_value_missing"
    score = None
    if missing is None:
        score = round(0.5 * growth_score + 0.5 * value_total * 10, 1)
    return {"score": score, "label": gav_label(score),
            "growth_score": growth_score,
            "ttm_revenue_growth_pct": ttm_revenue_growth_pct,
            "growth_source": growth_source,
            "value_score": value_total,
            "weights": {"growth": 0.5, "value": 0.5},
            "missing_reason": missing}
