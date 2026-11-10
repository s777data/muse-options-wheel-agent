"""Latest 10-K full text + rule-based customer-concentration extraction.

Sentences are selected by the keyword rules in config/sources.yaml and the
matching sentence is stored VERBATIM as evidence. Numbers are parsed from
that sentence only; vague wording ("over half") is kept as text plus a
config-defined approximate value, never silently sharpened.
"""
import html
import re

from .util import fetch_json, fetch_text

WORD_PCT = [
    (r"over half", "over_half"),
    (r"approximately one-half", "one_half"),
    (r"about one-half", "one_half"),
    (r"approximately half", "one_half"),
]


def strip_html(raw):
    text = re.sub(r"<[^>]+>", " ", raw)
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def split_sentences(text):
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]


def extract_concentration(text, search_cfg, rules_cfg, latest_fy_year=None):
    sentences = split_sentences(text)
    out = {"single_customer": None, "top_ten": None, "data_center": None}

    def find(keywords_list, contains_all=()):
        hits = []
        for s in sentences:
            low = s.lower()
            if any(k in low for k in keywords_list) and all(c in low for c in contains_all):
                hits.append(s)
        return hits

    # Single customer >= threshold statement
    for s in find(search_cfg["single_customer_keywords"]):
        m = re.search(r"(\d+(?:\.\d+)?)\s*%", s)
        if not m:
            continue
        entry = {"quote": s, "threshold_pct_in_sentence": float(m.group(1)),
                 "no_customer_at_or_above_threshold": s.lower().startswith("no customer")}
        if entry["no_customer_at_or_above_threshold"]:
            entry["max_single_customer_pct"] = None
        else:
            entry["max_single_customer_pct"] = float(m.group(1))
        out["single_customer"] = entry
        break

    # Top-ten share — prefer a sentence naming the latest fiscal year
    top_hits = find(search_cfg["top_ten_keywords"])
    chosen = None
    if latest_fy_year:
        for s in top_hits:
            if str(latest_fy_year) in s:
                chosen = s
                break
    if chosen is None and top_hits:
        chosen = top_hits[0]
    if chosen:
        entry = {"quote": chosen}
        m = re.search(r"(\d+(?:\.\d+)?)\s*%", chosen)
        if m:
            entry["top_ten_share_pct"] = float(m.group(1))
            entry["top_ten_share_basis"] = "explicit_percent_in_filing"
        else:
            kind = next((k for pat, k in WORD_PCT if re.search(pat, chosen, re.I)), None)
            if kind == "over_half":
                entry["top_ten_share_pct"] = rules_cfg["top10_approx_value_when_over_half_pct"]
                entry["top_ten_share_basis"] = "config_approx_for_wording_over_half_lower_bound_50"
            elif kind == "one_half":
                entry["top_ten_share_pct"] = rules_cfg["top10_approx_value_when_one_half_pct"]
                entry["top_ten_share_basis"] = "config_approx_for_wording_one_half"
            else:
                entry["top_ten_share_pct"] = None
                entry["top_ten_share_basis"] = "unparsed_wording"
        out["top_ten"] = entry

    # Data-center end-market share
    for s in find(search_cfg["data_center_keywords"][:1], contains_all=("revenue", "%")):
        m = re.search(r"(\d+(?:\.\d+)?)\s*%", s)
        if m and "end market" in s.lower():
            out["data_center"] = {"quote": s, "data_center_share_pct": float(m.group(1))}
            break
    return out


def get_periodic_buyback(sources_cfg, cik):
    """Buyback authorization state from the NEWEST periodic filing
    (latest 10-K vs latest 10-Q, whichever was filed most recently) —
    SPEC_V5.3 §2.1: the periodic filing is the system of record, and a
    post-period-end board action disclosed in it supersedes its own
    period-end table. Fail soft: any fetch/parse failure returns
    buyback=None with the error named, and the run proceeds with the
    sub-score missing (fail closed), never with an invented figure."""
    from .buybacks import extract_buyback
    src = sources_cfg["sources"]
    try:
        sub = fetch_json(src["edgar_submissions_url"].format(cik=cik),
                         src["user_agent"], src["timeout_seconds"], src["request_delay_seconds"])
        recent = sub["filings"]["recent"]
        latest = {}
        for form, filed, acc, doc, rep in zip(recent["form"], recent["filingDate"],
                                              recent["accessionNumber"],
                                              recent["primaryDocument"],
                                              recent["reportDate"]):
            base = form.rstrip("/A") if form.endswith("/A") else form
            if base in ("10-K", "10-Q") and base not in latest:
                latest[base] = {"form": form, "filed": filed, "accession": acc,
                                "primary_document": doc, "report_date": rep}
        if not latest:
            return {"filing": None, "buyback": None, "error": "no_periodic_filing_found"}
        target = max(latest.values(), key=lambda t: t["filed"])
        url = src["edgar_archive_url"].format(cik_int=int(cik),
                                              accession_nodash=target["accession"].replace("-", ""),
                                              primary_document=target["primary_document"])
        raw = fetch_text(url, src["user_agent"], src["timeout_seconds"], src["request_delay_seconds"])
        if "Undeclared Automated Tool" in raw[:2000]:
            return {"filing": {**target, "url": url}, "buyback": None,
                    "error": "sec_archives_blocked_undeclared_tool"}
        buyback = extract_buyback(strip_html(raw), raw_html=raw,
                                  period_end=target["report_date"])
        return {"filing": {**target, "url": url}, "buyback": buyback, "error": None}
    except Exception as exc:
        return {"filing": None, "buyback": None, "error": str(exc)}


def get_latest_10k_concentration(sources_cfg, rules_cfg, cik, latest_fy_year=None):
    src = sources_cfg["sources"]
    sub = fetch_json(src["edgar_submissions_url"].format(cik=cik),
                     src["user_agent"], src["timeout_seconds"], src["request_delay_seconds"])
    recent = sub["filings"]["recent"]
    target = None
    for form, filed, acc, doc in zip(recent["form"], recent["filingDate"],
                                     recent["accessionNumber"], recent["primaryDocument"]):
        if form == sources_cfg["filing_search"]["form"]:
            target = {"filed": filed, "accession": acc, "primary_document": doc}
            break
    if not target:
        raise RuntimeError(f"No {sources_cfg['filing_search']['form']} found in EDGAR submissions for CIK {cik}")
    url = src["edgar_archive_url"].format(cik_int=int(cik),
                                          accession_nodash=target["accession"].replace("-", ""),
                                          primary_document=target["primary_document"])
    raw = fetch_text(url, src["user_agent"], src["timeout_seconds"], src["request_delay_seconds"])
    if "Undeclared Automated Tool" in raw[:2000]:
        raise RuntimeError("SEC Archives blocked the filing fetch (undeclared-tool page)")
    text = strip_html(raw)
    concentration = extract_concentration(text, sources_cfg["filing_search"], rules_cfg, latest_fy_year)
    return {"filing": {**target, "url": url}, "concentration": concentration}
