"""Generic fallback for diluted share counts that EDGAR companyfacts
cannot expose: multi-class filers (e.g. Visa) tag their weighted-average
diluted shares ONLY dimensioned by share class, and companyfacts strips
dimensions, so the company-level WeightedAverageNumberOfDilutedShares-
Outstanding concept is simply absent and every market-cap / per-share
metric fails closed.

This module recovers the combined diluted share count from the filer's
own periodic filing (inline XBRL in the latest 10-K / 10-Q — documents
the engine already knows how to fetch), but ONLY under a self-validating
rule, never by assumption:

For each duration period the filing presents, gather the total net
income for the period (undimensioned fact in the same document), the
per-class diluted share counts, and the per-class reported diluted EPS.
A combined share count is accepted only if it REPRODUCES a reported
diluted EPS from that total net income, within EPS_TOLERANCE:

  * "direct"            — an undimensioned share count whose implied
                          EPS (net income / shares) matches the
                          undimensioned reported diluted EPS;
  * "member_as_converted" — ONE class member's count whose implied EPS
                          matches THAT class's reported diluted EPS
                          (the Visa pattern: the primary class is
                          already presented on a combined as-converted
                          basis — summing the classes would double
                          count, and the sum is rejected because it
                          reproduces no reported EPS);
  * "sum_of_classes"    — the classes sum to the combined count, valid
                          only when every class reports (within
                          tolerance) the SAME diluted EPS and the sum
                          reproduces it (equal-participation two-class
                          filers).

If several hypotheses validate but imply DIFFERENT counts, or none
validates, the period stays missing — fail closed, exactly as today.
Nothing here is ticker-specific: any filer whose filings carry the same
tagged evidence gets the same treatment, and filers with a company-level
concept in companyfacts never reach this code (the caller fires it only
when the companyfacts diluted-shares series is empty).

Berkshire Hathaway is the known non-match: its filings contain no
WeightedAverageNumberOfDilutedSharesOutstanding facts at all, even
dimensioned (it reports through custom extension concepts), so the
fallback finds nothing and BRK stays fail-closed.
"""
import re

from .util import fetch_json, fetch_text

SHARES_CONCEPT = "WeightedAverageNumberOfDilutedSharesOutstanding"
EPS_CONCEPT = "EarningsPerShareDiluted"
NI_CONCEPTS = ("NetIncomeLoss", "ProfitLoss")

# Reported EPS is rounded to the cent and share counts are tagged
# rounded to thousands/millions, so an exact match is not expected;
# 2.5% is far inside the gap between competing hypotheses (for Visa
# the winning member validates at 0.05% while the naive sum is off by
# ~5.6% against every class EPS).
EPS_TOLERANCE = 0.025

_CONTEXT_RE = re.compile(
    r"<(?:[A-Za-z0-9]+:)?context id=\"([^\"]+)\"[^>]*>(.*?)</(?:[A-Za-z0-9]+:)?context>",
    re.S)
_PERIOD_TAG_RE = re.compile(
    r"<(?:[A-Za-z0-9]+:)?(startDate|endDate|instant)>([^<]+)</(?:[A-Za-z0-9]+:)?\1>")
_DIM_RE = re.compile(r"dimension=\"([^\"]+)\"[^>]*>([^<]+)<")
_FACT_RE = re.compile(r"<ix:nonFraction\b([^>]*)>(.*?)</ix:nonFraction>", re.S)
_ATTR_RE = re.compile(r"(\w+)=\"([^\"]+)\"")


def _local(name):
    return name.rsplit(":", 1)[-1] if name else name


def _is_class_axis(axis_local):
    return "ClassOfStock" in axis_local or (
        "Class" in axis_local and "Stock" in axis_local)


def _parse_contexts(text):
    """context id -> {"start", "end", "dims": {axis_local: member_local}}.
    Instant contexts get start=None."""
    contexts = {}
    for m in _CONTEXT_RE.finditer(text):
        cid, body = m.group(1), m.group(2)
        tags = dict(_PERIOD_TAG_RE.findall(body))
        dims = {}
        for axis, member in _DIM_RE.findall(body):
            dims[_local(axis)] = _local(member)
        if "instant" in tags:
            contexts[cid] = {"start": None, "end": tags["instant"], "dims": dims}
        elif "startDate" in tags and "endDate" in tags:
            contexts[cid] = {"start": tags["startDate"], "end": tags["endDate"],
                             "dims": dims}
    return contexts


def _parse_facts(text, contexts):
    """Duration facts for the share / EPS / net-income concepts, as
    {(start, end): {"ni": value|None, "direct_shares": value|None,
    "direct_eps": value|None, "members": {member: {"shares": v|None,
    "eps": v|None}}, "conflict": bool}}."""
    periods = {}
    for m in _FACT_RE.finditer(text):
        attrs = dict(_ATTR_RE.findall(m.group(1)))
        name = attrs.get("name", "")
        concept = _local(name)
        if concept not in (SHARES_CONCEPT, EPS_CONCEPT) + NI_CONCEPTS:
            continue
        if not name.startswith("us-gaap:"):
            continue
        ctx = contexts.get(attrs.get("contextRef", ""))
        if ctx is None or ctx["start"] is None:
            continue
        raw = re.sub(r"<[^>]+>", "", m.group(2)).replace(",", "").strip()
        try:
            value = float(raw)
        except ValueError:
            continue
        if attrs.get("sign") == "-":
            value = -value
        scale = attrs.get("scale")
        if scale:
            try:
                value *= 10 ** int(scale)
            except ValueError:
                continue
        key = (ctx["start"], ctx["end"])
        slot = periods.setdefault(key, {"ni": None, "direct_shares": None,
                                        "direct_eps": None, "members": {},
                                        "conflict": False})
        dims = ctx["dims"]
        class_axes = [a for a in dims if _is_class_axis(a)]

        def put(holder, field, val):
            if holder[field] is not None and holder[field] != val:
                slot["conflict"] = True
            else:
                holder[field] = val

        if concept in NI_CONCEPTS:
            if not dims:
                put(slot, "ni", value)
        elif not dims:
            put(slot, "direct_shares" if concept == SHARES_CONCEPT else "direct_eps",
                value)
        elif len(dims) == 1 and len(class_axes) == 1:
            member = dims[class_axes[0]]
            entry = slot["members"].setdefault(member, {"shares": None, "eps": None})
            put(entry, "shares" if concept == SHARES_CONCEPT else "eps", value)
    return periods


def _rel_err(computed, reported):
    return abs(computed - reported) / max(abs(reported), 0.05)


def _validate_period(slot):
    """Return (shares, method, member, computed_eps, reported_eps) or
    None. See the module docstring for the three hypotheses."""
    ni = slot.get("ni")
    if not ni or slot.get("conflict"):
        return None
    accepted = []

    def consider(shares, method, member, reported_eps):
        if not shares or shares <= 0 or not reported_eps:
            return
        computed = ni / shares
        err = _rel_err(computed, reported_eps)
        if err <= EPS_TOLERANCE:
            accepted.append({"shares": shares, "method": method,
                             "member": member, "computed_eps": computed,
                             "reported_eps": reported_eps, "err": err})

    consider(slot.get("direct_shares"), "direct", None, slot.get("direct_eps"))
    members = {m: v for m, v in (slot.get("members") or {}).items()
               if v.get("shares")}
    for member, v in members.items():
        consider(v["shares"], "member_as_converted", member, v.get("eps"))
    if len(members) >= 2 and all(v.get("eps") for v in members.values()):
        epss = [v["eps"] for v in members.values()]
        mean_eps = sum(epss) / len(epss)
        if mean_eps and max(_rel_err(e, mean_eps) for e in epss) <= EPS_TOLERANCE:
            consider(sum(v["shares"] for v in members.values()),
                     "sum_of_classes", None, mean_eps)
    if not accepted:
        return None
    best = min(accepted, key=lambda a: a["err"])
    # Ambiguity between DIFFERENT counts stays fail-closed; hypotheses
    # agreeing on the same count (within 0.1%) are one answer.
    for other in accepted:
        if other is not best and abs(other["shares"] - best["shares"]) / best["shares"] > 0.001:
            return None
    return best


def extract_class_diluted_shares(text):
    """Pure extraction from one filing document's inline XBRL.
    Returns {"annual": [{"end", "value"}], "periods": [{"start", "end",
    "value"}], "validations": [...]} with only EPS-validated periods."""
    contexts = _parse_contexts(text)
    periods = _parse_facts(text, contexts)
    annual, rows, validations = [], [], []
    for (start, end), slot in sorted(periods.items()):
        best = _validate_period(slot)
        if best is None:
            continue
        rows.append({"start": start, "end": end, "value": best["shares"]})
        validations.append({"start": start, "end": end,
                            "method": best["method"], "member": best["member"],
                            "shares": best["shares"],
                            "computed_eps": round(best["computed_eps"], 4),
                            "reported_eps": best["reported_eps"]})
        from datetime import date
        dur = (date.fromisoformat(end) - date.fromisoformat(start)).days
        if 300 <= dur <= 400:
            annual.append({"end": end, "value": best["shares"]})
    return {"annual": annual, "periods": rows, "validations": validations}


def _latest_periodic(recent):
    """Latest 10-K and latest 10-Q entries from a submissions `recent`
    map (first occurrence per base form, as in filings.py)."""
    latest = {}
    for form, filed, acc, doc, rep in zip(recent.get("form") or [],
                                          recent.get("filingDate") or [],
                                          recent.get("accessionNumber") or [],
                                          recent.get("primaryDocument") or [],
                                          recent.get("reportDate") or []):
        base = form.rstrip("/A") if form.endswith("/A") else form
        if base in ("10-K", "10-Q") and base not in latest:
            latest[base] = {"form": form, "filed": filed, "accession": acc,
                            "primary_document": doc, "report_date": rep}
    return latest


def get_class_diluted_shares(sources_cfg, cik):
    """Fetch the latest 10-K (annual share counts) and, when it is the
    newer document, the latest 10-Q (interim periods), and extract
    EPS-validated combined diluted share counts. Fail soft: any fetch
    or parse failure returns empty lists with the error named, and the
    run proceeds fail-closed exactly as before."""
    src = sources_cfg["sources"]
    out = {"annual": [], "periods": [], "validations": [], "filings": [],
           "error": None}
    try:
        sub = fetch_json(src["edgar_submissions_url"].format(cik=cik),
                         src["user_agent"], src["timeout_seconds"],
                         src["request_delay_seconds"])
        latest = _latest_periodic((sub.get("filings") or {}).get("recent") or {})
        if not latest:
            out["error"] = "no_periodic_filing_found"
            return out
        targets = []
        if "10-K" in latest:
            targets.append(latest["10-K"])
        if "10-Q" in latest and ("10-K" not in latest
                                 or latest["10-Q"]["filed"] > latest["10-K"]["filed"]):
            targets.append(latest["10-Q"])
        merged_periods = {}
        for target in targets:
            url = src["edgar_archive_url"].format(
                cik_int=int(cik),
                accession_nodash=target["accession"].replace("-", ""),
                primary_document=target["primary_document"])
            raw = fetch_text(url, src["user_agent"], src["timeout_seconds"],
                             src["request_delay_seconds"])
            if "Undeclared Automated Tool" in raw[:2000]:
                out["error"] = "sec_archives_blocked_undeclared_tool"
                continue
            got = extract_class_diluted_shares(raw)
            out["filings"].append({**target, "url": url})
            for v in got["validations"]:
                v["filed"] = target["filed"]
                v["form"] = target["form"]
            out["validations"] += got["validations"]
            for row in got["periods"]:
                key = (row["start"], row["end"])
                if key not in merged_periods:
                    merged_periods[key] = {**row, "filed": target["filed"]}
            for row in got["annual"]:
                if not any(a["end"] == row["end"] for a in out["annual"]):
                    out["annual"].append({**row, "filed": target["filed"]})
        out["periods"] = [merged_periods[k] for k in sorted(merged_periods)]
        out["annual"].sort(key=lambda a: a["end"])
        if not out["annual"] and not out["periods"] and out["error"] is None:
            out["error"] = "no_eps_validated_class_shares_found"
        return out
    except Exception as exc:
        out["error"] = str(exc)
        return out
