"""DCF with a BUILT WACC (CAPM, inputs in config/dcf.yaml + beta in
config/tickers.yaml) and growth derived from filed history, clamped and
faded per config. No consensus input exists in v1."""


def build_wacc(dcf_cfg, beta, market_cap, total_debt):
    rf = dcf_cfg["risk_free_rate_pct"]
    erp = dcf_cfg["equity_risk_premium_pct"]
    # Blume adjustment (v5, config-gated): beta_adj = 0.67*beta + 0.33.
    # "inputs.beta" carries the beta actually used; raw and adjusted are
    # both disclosed alongside.
    adjustment = dcf_cfg.get("beta_adjustment")
    beta_adj = (0.67 * beta + 0.33 * 1.0) if adjustment == "blume" else beta
    cost_equity = rf + beta_adj * erp
    cost_debt_after_tax = dcf_cfg["pre_tax_cost_of_debt_pct"] * (1 - dcf_cfg["tax_rate_pct"] / 100)
    ev = (market_cap or 0) + (total_debt or 0)
    # Fail-closed guard: market_cap is None when the filer reports its
    # share count split by share class (e.g. V, BRK.B), so no
    # company-level diluted share count exists in EDGAR companyfacts
    # and market cap cannot be built. Dividing the raw None by EV
    # crashed the whole run (TypeError) whenever the filer also carried
    # debt. Instead the WACC rests on the cost of equity alone (equity
    # weight 1.0) and the missing market cap is disclosed on the result
    # ("market_cap_missing"); the market-cap-dependent legs (DCF
    # per-share base, P/S, EV/S, FCF yield) stay missing downstream and
    # the value weights renormalise, as with any other missing leg.
    equity_w = (market_cap / ev) if market_cap is not None and ev else 1.0
    debt_w = 1 - equity_w
    wacc = cost_equity * equity_w + cost_debt_after_tax * debt_w
    return {"cost_of_equity_pct": round(cost_equity, 3),
            "cost_of_debt_after_tax_pct": round(cost_debt_after_tax, 3),
            "equity_weight": round(equity_w, 4), "debt_weight": round(debt_w, 4),
            "market_cap_missing": market_cap is None,
            "wacc_pct": round(wacc, 3),
            "inputs": {"risk_free_rate_pct": rf, "equity_risk_premium_pct": erp,
                       "beta": beta_adj, "beta_raw": beta,
                       "beta_adjusted": round(beta_adj, 4),
                       "beta_adjustment": adjustment or "none",
                       "pre_tax_cost_of_debt_pct": dcf_cfg["pre_tax_cost_of_debt_pct"],
                       "tax_rate_pct": dcf_cfg["tax_rate_pct"]}}


def fcf_base_per_share(metrics):
    """DCF base (v5): TTM owner FCF per share when a TTM owner FCF
    exists — the freshest filed twelve months, symmetric for growers
    and decliners — else the latest-FY owner FCF per share. Returns
    (base_per_share, source) or (None, None). Share count is the most
    recent period's diluted count for the TTM base, the latest annual
    diluted count for the FY fallback."""
    shares_ttm = metrics.get("diluted_shares_latest_period") or metrics.get("diluted_shares_latest")
    if metrics.get("fcf_ttm") is not None and shares_ttm:
        return metrics["fcf_ttm"] / shares_ttm, "ttm_owner_fcf"
    if metrics.get("fcf_latest") is not None and metrics.get("diluted_shares_latest"):
        return metrics["fcf_latest"] / metrics["diluted_shares_latest"], \
            "latest_fy_owner_fcf_fallback"
    return None, None


def dcf_bases(metrics):
    """DCF base selection (v5.2). The primary base is the
    normalised-capex TTM owner FCF per share when normalisation
    actually trims capex (metrics carry fcf_normalized_ttm from the
    median filed capex intensity, capped at actual spend); otherwise
    it is the actual base from fcf_base_per_share (TTM owner FCF, or
    the latest-FY fallback). The actual base is always reported
    alongside — it anchors the stress-floor DCF. Returns a dict:
    primary_per_share / primary_source / primary_usd,
    actual_per_share / actual_usd, normalized_applied (the primary
    base is the normalised one), normalization_unavailable (no
    normalised base could be built — median intensity missing or a
    TTM input missing — so the primary base is the actual one)."""
    actual_ps, actual_source = fcf_base_per_share(metrics)
    if actual_source == "ttm_owner_fcf":
        actual_usd = metrics.get("fcf_ttm")
    elif actual_source:
        actual_usd = metrics.get("fcf_latest")
    else:
        actual_usd = None
    res = {"primary_per_share": actual_ps, "primary_source": actual_source,
           "primary_usd": actual_usd, "actual_per_share": actual_ps,
           "actual_usd": actual_usd, "normalized_applied": False,
           "normalization_unavailable":
               metrics.get("fcf_normalized_ttm") is None}
    shares_ttm = metrics.get("diluted_shares_latest_period") or metrics.get("diluted_shares_latest")
    norm_usd = metrics.get("fcf_normalized_ttm")
    if norm_usd is not None and shares_ttm and metrics.get("capex_normalized_binds"):
        res["primary_per_share"] = norm_usd / shares_ttm
        res["primary_source"] = "ttm_owner_fcf_normalized_capex"
        res["primary_usd"] = norm_usd
        res["normalized_applied"] = True
    return res


def growth_path(dcf_cfg, filed_cagr_pct):
    g = dcf_cfg["growth"]
    initial = max(g["floor_pct"], min(g["cap_pct"], filed_cagr_pct or 0.0))
    years = dcf_cfg["projection_years"]
    term = dcf_cfg["terminal_growth_pct"]
    # Fade floor (v5.2): an initial growth above the floor fades only
    # to the floor over the projection — the DCF base is now a
    # mid-cycle (normalised-capex) figure, and fading its growth to
    # zero in five years would double-count the cycle. An initial at
    # or below the floor still fades to terminal growth, as before.
    fade_floor = g.get("fade_floor_pct", 15.0)
    target = fade_floor if initial > fade_floor else term
    if years == 1:
        return [initial]
    step = (initial - target) / years
    return [round(initial - step * i, 4) for i in range(years)]


def fair_value(fcf_per_share_base, growth_pct_path, wacc_pct, terminal_growth_pct):
    """Present value of projected FCF/share plus Gordon terminal value.
    growth_pct_path[i] is the growth applied in projection year i+1."""
    wacc = wacc_pct / 100
    term = terminal_growth_pct / 100
    fcf = fcf_per_share_base
    pv, projected = 0.0, []
    for i, g in enumerate(growth_pct_path, start=1):
        fcf = fcf * (1 + g / 100)
        projected.append(round(fcf, 4))
        pv += fcf / (1 + wacc) ** i
    tv = fcf * (1 + term) / (wacc - term) if wacc > term else 0.0
    pv_tv = tv / (1 + wacc) ** len(growth_pct_path)
    return {"fair_value_per_share": round(pv + pv_tv, 2),
            "pv_of_projected_fcf": round(pv, 2), "pv_of_terminal_value": round(pv_tv, 2),
            "projected_fcf_per_share": projected}
