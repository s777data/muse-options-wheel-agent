"""Announced / remaining share-repurchase authorization extraction (v5.3,
extractor revision 2 — rules R1–R8).

System of record (SPEC_V5.3 §2.1): the latest periodic filing — 10-Q
Part II Item 2 / 10-K Part II Item 5 (Reg S-K Item 703) and the narrative
around it (equity note, MD&A, risk factors). Sentences are matched by
rule and stored VERBATIM as evidence, exactly like filings.py does for
customer concentration. Numbers are parsed from those sentences only.

Revision 3 (2026-11-09) — field fixes from the first full-universe
run of revision 2: colspan expansion so grid columns align; the
availability column also answers to 'Amount Available Under ...
Program'; a cell carrying its own unit word states its own scale,
and a table with no unit statement falls back (flagged, ledger-
bound-checked) to the document's unit convention; a Total-row cell
that contradicts the column's own progression yields to it;
securities-issuance shelves ('remained available to be sold under
the ATM Program') are not buyback remainders; '$A of the $B
authorization remained' reads $A; a program the filing says was
completed is never summed as undrawn; the floor test measures
activity by executions only.

Revision 2 (2026-11-09) — why: the first table fallback took an
unbounded forward character window after the Item 703 header phrase,
harvested every $ amount in it (average prices and post-table prose
included) and kept the LAST one, with a two-entry unit table that had
no "billions"; the narrative path required the literal string
"repurchas", discarding availability sentences phrased against "the
Authorization" or "the program". Every nominal extraction in the field
(NVDA $209, AAPL $256, CAT $7.20, ...) traces to those two places. The
rules below are stated over filing language and table structure only —
no filer names, CIKs, or per-filer values anywhere:

- R1: the Item 703 table is located by a normalised header matcher and
  bounded to the table itself (raw-HTML grid when available; a bounded,
  prose-truncated region of the flattened text otherwise).
- R2: remaining = the LAST POPULATED cell of the may-yet-be-purchased
  column, read row by row from the grid — never "the last $ in a
  window". Column semantics come from the header, not amount order.
- R3: the unit scale comes from the table's own unit statement
  wherever it lives (header cell, intro sentence, caption): billions /
  millions / thousands are all handled. A narrative figure for the same
  quantity with an explicit unit word governs on conflict, via the
  unit-consistency test in R8.
- R4: the narrative availability grammar is widened ("remaining
  availability", "remained under the ... authorization", "there
  remained $X", ...) and the hard "repurchas" gate is replaced by a
  program-context test (repurchas* / authorization / program /
  buyback). Table-header and unit-statement sentences are excluded
  from the narrative pool.
- R5: an as-of current-authorization sentence ("As of <date>, we were
  authorized ... to repurchase up to $X") counts as REMAINING only
  when validated — against the Item 703 final cell, or against a
  buildable authorization ledger. Event-dated approvals stay
  originations; without validation the sentence fails closed.
- R6: when the filing decomposes remaining per program, the Item 703
  total governs and the decomposition must reconcile with it;
  without a table, a decomposition sum is accepted only inside the
  authorization ledger bound. Disagreement fails closed.
- R7: post-period board additions are recognised in their common
  phrasings ("approved an additional $X in share repurchase
  authorization", "increased its authorization by $X", ...) and are
  accepted only as (i) the filer's own stated new remaining/total,
  (ii) period-end remaining + a stated addition, or (iii) the
  remained-available construction sentence. In-period additions are
  ledger events, never supersessions.
- R8: narrative and table figures cross-check each other (agreement
  within ~2%, unit-consistency scale inference, ledger preference on
  disagreement). Guards: a candidate above the buildable ledger bound
  is rejected; an extracted remaining under $1M on a filer with
  > $100M of repurchase activity in the filing is an extraction
  failure — the leg goes missing (fail closed), it is never scored
  at the anchor floor.

Fail-closed rules (SPEC_V5.3 §2.3), unchanged:
- An "increase / total authorization" figure is accepted as the
  REMAINING amount only when the filing's own construction sentence is
  located nearby (the leftover it is built from, "plus ...") — filers
  use "total authorization" both for a cumulative cap and for a new
  available total (the MU trap, SPEC §3.2), so without the construction
  sentence the figure is ambiguous and is never scored.
- Share-denominated authorizations are stored as shares and never
  converted at market price in v1.
- Employee tax-withholding repurchases are never program repurchases.
"""
import re
from html.parser import HTMLParser

from .filings import split_sentences

MONEY_RE = re.compile(r"\$\s*([\d,]+(?:\.\d+)?)\s*(billion|million|thousand)?", re.I)
DATE_RE = re.compile(r"(January|February|March|April|May|June|July|August|"
                     r"September|October|November|December)\s+\d{1,2},\s+\d{4}")
SCALE = {"billion": 1e9, "million": 1e6, "thousand": 1e3, None: 1.0}
UNIT_WORD = {"billion": 1e9, "million": 1e6, "thousand": 1e3}
MONTHS = ["January", "February", "March", "April", "May", "June", "July",
          "August", "September", "October", "November", "December"]
_AGREE_TOL = 0.02        # narrative/table agreement tolerance (R8)
_DECOMP_TOL = 0.01       # decomposition vs table tolerance (R6)


def _money(sentence, start=0):
    """First $ amount in the sentence at/after `start`, in USD, or None."""
    m = MONEY_RE.search(sentence, start)
    if not m:
        return None
    return float(m.group(1).replace(",", "")) * SCALE[(m.group(2) or "").lower() or None]


def _money_nearest(sentence, idx):
    """The $ amount closest to character offset `idx` in the sentence.
    Remaining-figure sentences often carry TWO amounts — the program
    size ("up to $10 billion ... of which $2.16 billion remains") —
    and the one adjacent to the availability keyword is the remaining
    figure, never the first amount in the sentence."""
    best, best_dist = None, None
    for m in MONEY_RE.finditer(sentence):
        dist = min(abs(m.start() - idx), abs(m.end() - idx))
        if best_dist is None or dist < best_dist:
            best, best_dist = m, dist
    if best is None:
        return None
    return float(best.group(1).replace(",", "")) * SCALE[(best.group(2) or "").lower() or None]


def _dkey(dstr):
    """Sortable (year, month, day) for a date string as written in a
    filing ("October 8, 2026" / "November 2026" / "2018" / ISO
    "2026-10-08")."""
    if not dstr:
        return (0, 0, 0)
    months = {m: i + 1 for i, m in enumerate(MONTHS)}
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", dstr)
    if m:
        return (int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = re.search(r"([A-Za-z]+)\s+(\d{1,2}),\s+(\d{4})", dstr)
    if m:
        return (int(m.group(3)), months.get(m.group(1), 0), int(m.group(2)))
    m = re.search(r"([A-Za-z]+)\s+(\d{4})", dstr)
    if m:
        return (int(m.group(2)), months.get(m.group(1), 0), 1)
    m = re.search(r"\b(\d{4})\b", dstr)
    return (int(m.group(1)), 0, 0) if m else (0, 0, 0)


def _date_any(sentence):
    """A date as written in the sentence: full date, else month-year,
    else a bare year, else None."""
    d = _date_in(sentence)
    if d:
        return d
    m = re.search(r"(January|February|March|April|May|June|July|August|"
                  r"September|October|November|December)\s+\d{4}", sentence)
    if m:
        return m.group(0)
    m = re.search(r"\bIn (\d{4})\b", sentence)
    return m.group(1) if m else None


def _date_in(sentence):
    m = DATE_RE.search(sentence)
    return m.group(0) if m else None


def _is_program_sentence(s):
    low = s.lower()
    return "repurchas" in low and ("authoriz" in low or "program" in low)


def _has_program_context(low):
    """R4 program-context test: the sentence is about a repurchase
    program/authorization (replaces the literal 'repurchas' gate, which
    discarded sentences phrased against 'the Authorization' alone)."""
    return ("repurchas" in low or "authoriz" in low
            or "program" in low or "buyback" in low)


def _split_with_spans(text):
    """split_sentences() with (sentence, start, end) offsets."""
    spans, start = [], 0
    for m in re.finditer(r"[.!?]\s+", text):
        end = m.start() + 1
        s = text[start:end].strip()
        if s:
            spans.append((s, start, end))
        start = m.end()
    tail = text[start:].strip()
    if tail:
        spans.append((tail, start, len(text)))
    return spans


# ---------------------------------------------------------------------
# Item 703 table parsing (R1–R3)
# ---------------------------------------------------------------------

_NUM_RE = re.compile(r"[\d,]+(?:\.\d+)?")


def _norm_phrase(s):
    """Lowercase alphanumeric-only form for header matching — XBRL
    header cells routinely lose their spaces ('Numberof Shares')."""
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _cell_events(cell):
    """Ordered ('$', None) / ('n', value) events inside one table cell.
    Parenthesised footnote references — '(1)', '(2)' — are not data."""
    events = []
    for m in re.finditer(r"\$|\(\d+\)|[\d,]+(?:\.\d+)?", cell):
        tok = m.group(0)
        if tok == "$":
            events.append(("$", None))
        elif tok.startswith("("):
            continue
        else:
            try:
                events.append(("n", float(tok.replace(",", ""))))
            except ValueError:
                pass
    return events


def _row_mayyet_value(events):
    """R2 cell semantics on one row's event stream: the may-yet column
    is the LAST column, so a populated cell shows up as a $ amount that
    is also the row's last number (anything after it would be a later
    column — e.g. the shares-under-program column trailing an average
    price means the $ amount was the price, not the remainder).
    Secondary shape: exactly four numbers with the only $ before the
    price (a remainder printed without its own $)."""
    nums = [i for i, e in enumerate(events) if e[0] == "n"]
    if not nums:
        return None
    last_num = nums[-1]
    for i in range(len(events) - 1, -1, -1):
        if events[i][0] == "$":
            nxt = [j for j in nums if j > i]
            if nxt and nxt[0] == last_num:
                return events[last_num][1]
            break
    dollars = [i for i, e in enumerate(events) if e[0] == "$"]
    if len(nums) == 4 and dollars and dollars[-1] < nums[2]:
        return events[nums[3]][1]
    return None


class _TableGrid(HTMLParser):
    """Minimal table-grid collector (stdlib): every <table> becomes a
    list of rows of cell texts. Nested tables are recorded separately.
    Colspans are expanded (cell text in the first spanned column, empty
    continuations after it) so header and data rows share one logical
    column layout — inline-XBRL tables lean on colspans heavily, and
    without expansion the may-yet column index points at the wrong
    cells in half the rows."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables = []
        self._stack = []

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self._stack.append({"rows": [], "row": None, "cell": None,
                                "span": 1})
        elif self._stack:
            top = self._stack[-1]
            if tag == "tr":
                top["row"] = []
            elif tag in ("td", "th") and top["row"] is not None:
                top["cell"] = []
                try:
                    top["span"] = max(1, int(dict(attrs).get("colspan") or 1))
                except (TypeError, ValueError):
                    top["span"] = 1

    def handle_endtag(self, tag):
        if not self._stack:
            return
        top = self._stack[-1]
        if tag in ("td", "th") and top["cell"] is not None:
            top["row"].append(re.sub(r"\s+", " ", "".join(top["cell"])).strip())
            top["row"].extend([""] * (top["span"] - 1))
            top["cell"] = None
            top["span"] = 1
        elif tag == "tr" and top["row"] is not None:
            top["rows"].append(top["row"])
            top["row"] = None
        elif tag == "table":
            self.tables.append(self._stack.pop()["rows"])

    def handle_data(self, data):
        if self._stack and self._stack[-1]["cell"] is not None:
            self._stack[-1]["cell"].append(data)


# Unit statements as filers print them: "(in millions", "($ in
# millions)", a bare "(billions)" hanging off a header cell, or the
# words "dollars/amounts in millions".
_UNIT_PHRASE_RE = re.compile(
    r"\(\s*(?:\$\s*)?(?:in\s+)?(billions|millions|thousands)\b"
    r"|\b(?:dollars|amounts)\s+in\s+(billions|millions|thousands)\b", re.I)


def _unit_word(text):
    m = _UNIT_PHRASE_RE.search(text)
    if not m:
        return None
    return (m.group(1) or m.group(2)).lower().rstrip("s")


def _unit_near_header(text, hdr_idx):
    """R3 fallback scale: the table's unit statement from the flattened
    text around the header phrase — a statement inside the header cell
    continuation (just after the phrase) wins; otherwise the nearest
    statement before it (the intro sentence filers print directly
    above the table). Returns (scale, word) or (None, None)."""
    window = text[max(0, hdr_idx - 5000):hdr_idx + 400]
    matches = list(_UNIT_PHRASE_RE.finditer(window))
    if not matches:
        return None, None
    rel_hdr = min(hdr_idx, 5000)
    after = [m for m in matches if m.start() >= rel_hdr]
    chosen = after[0] if after else matches[-1]
    word = (chosen.group(1) or chosen.group(2)).lower().rstrip("s")
    return UNIT_WORD[word], word


def _unit_document(text, hdr_idx):
    """Last-resort scale (flagged wherever it is used): the nearest
    unit statement anywhere in the document. Filers present a filing
    in one unit convention; when the Item 703 table itself states no
    unit, the document's own convention is the only scale evidence
    available, and the ledger bound in extract_buyback sanity-checks
    the result."""
    matches = list(_UNIT_PHRASE_RE.finditer(text))
    if not matches:
        return None, None
    chosen = min(matches, key=lambda m: abs(m.start() - hdr_idx))
    word = (chosen.group(1) or chosen.group(2)).lower().rstrip("s")
    return UNIT_WORD[word], word


def _is_availability_header(cell):
    """R1 header matcher, normalised: the Item 703 may-yet DOLLAR
    column (a 'Maximum NUMBER of Shares That May Yet Be Purchased'
    column is a share count, not a dollar remainder — share-
    denominated programs disclose no dollar figure to extract), or
    the equivalent availability column some filers title 'Amount
    Available Under [the] Repurchase Program'."""
    norm = _norm_phrase(cell)
    if "mayyetbepurchased" in norm and "dollar" in norm:
        return True
    return "amountavailableunder" in norm and "program" in norm


_CELL_UNIT_RE = re.compile(
    r"[\d,]+(?:\.\d+)?\s*(billions?|millions?|thousands?)\b", re.I)


def _cell_value(cell):
    """(value, unit_word) from one data cell: the first number in the
    cell, plus the unit word the cell itself carries when it carries
    one ('$ 18.60 billion' states its own scale). A cell that is only
    a parenthesised digit or two is a footnote marker, not a value
    ('(3)' sends the reader to the note below the table)."""
    if re.fullmatch(r"\(\d{1,2}\)", cell.strip()):
        return None, None
    m = re.search(r"[\d,]+(?:\.\d+)?", cell)
    if not m:
        return None, None
    try:
        value = float(m.group(0).replace(",", ""))
    except ValueError:
        return None, None
    u = _CELL_UNIT_RE.search(cell)
    return value, (u.group(1).lower().rstrip("s") if u else None)


def _share_scale(header_text, text, hdr_idx):
    """Scale of the shares column (for the period-repurchases estimate
    only): stated in the shares header cell, else in the intro window
    ('number of shares, which are reflected in thousands')."""
    word = _unit_word(header_text)
    if word:
        return UNIT_WORD[word]
    window = text[max(0, hdr_idx - 1200):hdr_idx]
    m = re.search(r"shares[^.]{0,120}?reflected in (thousands|millions)", window, re.I)
    if m:
        return UNIT_WORD[m.group(1).lower().rstrip("s")]
    return 1.0


def _parse_grid_table(raw_html, text):
    """R1/R2/R3 over the raw HTML: find the Item 703 table (the LAST
    table whose header carries the may-yet / availability phrase),
    read the availability column BY COLUMN INDEX (colspan-expanded, so
    header and data rows align), take the Total-row cell when there
    is one and the last populated cell otherwise — a Total cell that
    contradicts the column's own last monthly value is structurally
    corrupt and yields to the progression. Scale comes from the
    table's own unit statement; a cell carrying its own unit word
    ('$ 18.60 billion') states its own scale. Also estimates the
    period's repurchase dollars from the shares × average-price
    columns (guard input only)."""
    if not raw_html:
        return None
    parser = _TableGrid()
    try:
        parser.feed(raw_html)
    except Exception:
        return None
    chosen = None
    for table in parser.tables:
        for ri, row in enumerate(table[:6]):
            if any(_is_availability_header(c) for c in row):
                chosen = (table, ri)
    if chosen is None:
        return None
    table, hdr_row = chosen
    header_cells = list(table[hdr_row])
    header_text = " ".join(header_cells)
    may_col = next(i for i, c in enumerate(header_cells)
                   if _is_availability_header(c))
    mayyet_header = header_cells[may_col]
    shares_col = next((i for i, c in enumerate(header_cells)
                       if "totalnumberofsharespurchased" in _norm_phrase(c)
                       and "aspartof" not in _norm_phrase(c)), None)
    price_col = next((i for i, c in enumerate(header_cells)
                      if "averagepricepaid" in _norm_phrase(c)), None)
    shares_header = header_cells[shares_col] if shares_col is not None else ""
    low_text = text.lower()
    hdr_idx = low_text.rfind("may yet be purchased")
    if hdr_idx < 0:
        hdr_idx = low_text.rfind("amount available under")
    scale, scale_word = None, None
    scale_word = _unit_word(mayyet_header) or _unit_word(header_text)
    if scale_word:
        scale = UNIT_WORD[scale_word]
    elif hdr_idx >= 0:
        scale, scale_word = _unit_near_header(text, hdr_idx)
    share_scale = _share_scale(shares_header, text, hdr_idx) if hdr_idx >= 0 else 1.0

    monthly_value, monthly_label = None, None
    total_value, total_label = None, None
    monthly_unit = total_unit = None
    period_spent = 0.0
    for row in table[hdr_row + 1:]:
        cells = [c for c in row if c.strip()]
        if not cells:
            continue
        if any(_is_availability_header(c) for c in row):
            continue  # repeated header row
        if len(cells) == 1 and len(cells[0]) > 60:
            continue  # footnote paragraph carried as a table row
        label = row[0] if row else ""
        value, unit_word = (None, None)
        if may_col < len(row):
            # The header cell spans several physical columns in
            # XBRL styling ('$' in one, the figure in the next): the
            # availability column group runs from the header's first
            # spanned column to the end of the row (it is the last
            # column group in the Item 703 layout). Column identity
            # only — a blank cell means the row states no remainder
            # (a row-stream guess is how average prices got read as
            # remainders in the first place).
            for ci in range(may_col, len(row)):
                value, unit_word = _cell_value(row[ci])
                if value is not None:
                    if unit_word is None:
                        for cj in range(ci + 1, min(ci + 5, len(row))):
                            u = re.search(r"\b(billions?|millions?|thousands?)\b",
                                          row[cj], re.I)
                            if u:
                                unit_word = u.group(1).lower().rstrip("s")
                                break
                    break
        if value is not None:
            if label.strip().lower().startswith("total"):
                total_value, total_label, total_unit = value, label, unit_word
            else:
                monthly_value, monthly_label, monthly_unit = value, label, unit_word
        # Period repurchases estimate: shares column × price column.
        if shares_col is not None and price_col is not None \
                and not label.strip().lower().startswith("total") \
                and not label.rstrip().endswith(":"):
            sh, _u1 = _cell_value(row[shares_col]) if shares_col < len(row) else (None, None)
            pr, _u2 = _cell_value(row[price_col]) if price_col < len(row) else (None, None)
            if sh and pr:
                period_spent += sh * share_scale * pr
    note = None
    if total_value is not None:
        # R6: the Total-row cell governs. (Sub-rows inside a period
        # block — an ASR tranche's own remainder, a purchase-channel
        # split — can leave a small late 'monthly' value that is not
        # the program remainder; the Total row is the filer's own
        # period-end statement of the column.)
        last_value, last_label, last_unit = total_value, total_label, total_unit
    else:
        last_value, last_label, last_unit = monthly_value, monthly_label, monthly_unit
    if last_value is None:
        return {"remaining_raw": None, "scale": scale, "scale_word": scale_word,
                "period_repurchases_usd": period_spent or None,
                "quote": None}
    if last_unit:
        scale, scale_word = UNIT_WORD[last_unit], last_unit
    quote = (f"Item 703 table, column '{mayyet_header.strip()}', "
             f"row '{last_label}': {last_value:,.4g}"
             + (f" (in {scale_word}s)" if scale_word else ""))
    return {"remaining_raw": last_value, "scale": scale, "scale_word": scale_word,
            "period_repurchases_usd": period_spent or None, "quote": quote,
            "note": note}


_MONTH_ALT = "|".join(MONTHS)
# Row labels: month-range period labels (any case) or the Total row —
# 'Total' is matched case-SENSITIVELY: tables capitalise it, and a
# lowercase 'total' in post-table prose ('for a total value of $...')
# is not a row.
_ROW_LABEL_RE = re.compile(
    r"(?:(?:" + _MONTH_ALT + r")\s+\d{1,2}(?:,\s+\d{4})?\s*(?:-|–|—|\bto\b)\s*"
    r"(?:(?:" + _MONTH_ALT + r")\s+)?\d{1,2}(?:,\s+\d{4})?)"
    r"|(?-i:\bTotal\b)", re.I)


def _truncate_prose(segment):
    """Cut a flattened table-row segment where sentence prose begins
    (four consecutive alphabetic tokens, month names excluded): the
    grid portion of a row is labels and numbers only, and everything
    the old fallback died on lived in the prose after the grid."""
    tokens = segment.split(" ")
    run = 0
    for i, tok in enumerate(tokens):
        word = re.sub(r"[^A-Za-z]", "", tok)
        if len(word) >= 2 and word.capitalize() not in MONTHS and word.lower() != "total":
            run += 1
            if run >= 4:
                return " ".join(tokens[:i - 3])
        else:
            run = 0
    return segment


def _parse_flat_table(text):
    """R1/R2/R3 over flattened text (no raw HTML): the table region is
    bounded at the next Item heading, rows are segmented by their
    period labels and prose-truncated, and the may-yet figure is the
    last $ amount of the last populated row — valid only when that
    amount is also the row's last number (an average price trailed by
    the shares-under-program column is not a remainder)."""
    low = text.lower()
    hdr_idx = low.rfind("may yet be purchased")
    if hdr_idx < 0:
        return None
    region = text[hdr_idx:hdr_idx + 8000]
    cut = re.search(r"\bitem\s+\d+\.", region[50:], re.I)
    if cut:
        region = region[:50 + cut.start()]
    scale, scale_word = _unit_near_header(text, hdr_idx)
    labels = list(_ROW_LABEL_RE.finditer(region))
    last_value, last_label, last_word, period_spent = None, None, None, 0.0
    for li, lab in enumerate(labels):
        end = labels[li + 1].start() if li + 1 < len(labels) else len(region)
        segment = _truncate_prose(region[lab.start():end])
        amounts = [(m.start(), float(m.group(1).replace(",", "")),
                    (m.group(2) or "").lower() or None)
                   for m in MONEY_RE.finditer(segment)]
        nums = [float(x.replace(",", "")) for x in _NUM_RE.findall(segment)]
        if amounts and nums and amounts[-1][1] == nums[-1]:
            last_value, last_label = amounts[-1][1], lab.group(0)
            last_word = amounts[-1][2]
        if nums and not lab.group(0).lower().startswith("total"):
            dollars = [a for a in amounts]
            if dollars and len(nums) >= 2:
                period_spent += nums[0] * dollars[0][1]
    if last_word:
        # The row's figure carries its own unit word ('$ 18.56
        # billion') — that word is the figure's scale.
        scale, scale_word = UNIT_WORD[last_word.rstrip("s")], last_word.rstrip("s")
    return {"remaining_raw": last_value, "scale": scale, "scale_word": scale_word,
            "period_repurchases_usd": period_spent or None,
            "quote": (f"Item 703 table (flattened text), row '{last_label}': "
                      f"{last_value:,.4g}" + (f" (in {scale_word}s)" if scale_word else "")
                      ) if last_value is not None else None,
            "flat": True}


def _parse_item703(text, raw_html):
    """Grid parse first (production path), flattened parse as the
    fallback. Returns a dict with remaining_usd already scaled, or
    None when no table is found at all."""
    parsed = _parse_grid_table(raw_html, text) if raw_html else None
    if parsed is None:
        # No grid table matched at all — flattened-text fallback. (A
        # matched table whose availability cells are all blank or
        # dashed is the filer's own statement that no remainder is
        # given; re-reading the same table as prose only resurrects
        # the average prices the grid correctly refused.)
        parsed = _parse_flat_table(text)
    if parsed is None:
        return None
    out = dict(parsed)
    raw = parsed.get("remaining_raw")
    if raw is not None:
        scale = parsed.get("scale")
        scale_source = "table" if scale else None
        if not scale and raw < 1e7:
            # Tier-3 (R3 last resort): the document's own unit
            # convention, flagged — extract_buyback bound-checks it
            # against the authorization ledger when one exists. Only
            # for compact printed figures: a value printed with eight
            # or more digits and no unit statement is dollars as
            # printed (a filer does not print 11-digit thousands).
            hdr_idx = text.lower().rfind("may yet be purchased")
            if hdr_idx < 0:
                hdr_idx = text.lower().rfind("amount available under")
            scale, scale_word = _unit_document(text, max(hdr_idx, 0))
            if scale:
                out["scale_word"] = scale_word
                scale_source = "document"
        out["remaining_usd"] = raw * scale if scale else raw
        out["scale_applied"] = scale
        out["scale_source"] = scale_source
    else:
        out["remaining_usd"] = None
        out["scale_applied"] = None
        out["scale_source"] = None
    return out


# ---------------------------------------------------------------------
# Narrative availability (R4), as-of authorization (R5)
# ---------------------------------------------------------------------

# Availability constructions filers use for the remaining figure:
# MU: "$2.16 billion ... remained available"; ABNB: "had $2.7 billion
# available to repurchase"; CAT: "$7.192 billion remained available
# under the 2024 Authorization"; AAPL: "remaining availability under
# the May 2025 program was $14.0 billion".
AVAILABILITY_PATTERNS = ("remained available", "remaining availability",
                         "remaining available", "remains available",
                         "available to repurchase", "available for repurchase",
                         "available for future repurchases", "available under",
                         "remained under", "there remained")


def _is_table_sentence(s):
    """Sentences that are table furniture, not narrative: the Item 703
    header itself, and unit-statement lines ('(in millions, except
    ...)'). Excluded from the narrative pool (R4) — a header captured
    as a 'remaining' quote is a known false positive."""
    low = s.lower()
    return ("may yet be purchased" in low or len(s) > 900
            or re.search(r"\(\s*in\s+(billions|millions|thousands)\b", low) is not None)


# Securities-ISSUANCE language: an at-the-market offering shelf also
# 'remains available under the program' — but it is shares to be SOLD,
# the opposite of a repurchase authorization. Excluded unless the
# sentence is itself about repurchasing.
_ISSUANCE_MARKERS = ("to be sold", "offering price", "at-the-market",
                     "shares to be issued", "selling stockholders")


def _availability_candidates(sentences):
    """All narrative remaining-figure candidates: (sentence, amount,
    as_of, approximate, scope_year). scope_year is the program year
    the sentence ties the figure to ('the May 2025 program', 'the
    February 2026 program') — the year nearest the availability
    keyword, so a sentence that also mentions an older program does
    not mis-scope the figure; the R6 decomposition key."""
    cands = []
    for s in sentences:
        if _is_table_sentence(s):
            continue
        low = s.lower()
        if not _has_program_context(low):
            continue
        if "repurchas" not in low and any(m in low for m in _ISSUANCE_MARKERS):
            continue  # issuance shelf, not a buyback remainder
        kw_idx = None
        for pat in AVAILABILITY_PATTERNS:
            p = low.find(pat)
            if p >= 0:
                kw_idx = p
                break
        if kw_idx is None and "remains" in low and "authorization" in low:
            kw_idx = low.find("remains")
        if kw_idx is None:
            continue
        amt = None
        unit_word = None
        # "$A of the $B authorization remained ..." — the remainder is
        # the FIRST figure; $B is the program total it is a part of.
        m = re.search(r"(\$\s*[\d,]+(?:\.\d+)?\s*(?:billion|million|thousand)?)"
                      r"\s+of\s+the\s+\$\s*[\d,]+.{0,70}?remain", s, re.I)
        if m:
            amt = _money(m.group(1))
            um = re.search(r"(billion|million|thousand)", m.group(1), re.I)
            unit_word = um.group(1).lower() if um else None
        if not amt:
            amt = _money_nearest(s, kw_idx)
            best = None
            for mm in MONEY_RE.finditer(s):
                v = float(mm.group(1).replace(",", "")) * SCALE[(mm.group(2) or "").lower() or None]
                if v == amt and (best is None or abs(mm.start() - kw_idx) < best[0]):
                    best = (abs(mm.start() - kw_idx), mm.group(2))
            unit_word = best[1].lower() if best and best[1] else None
        if not amt:
            continue
        asof = None
        m = re.search(r"as of\s+(" + DATE_RE.pattern + r")", s, re.I)
        if m:
            asof = m.group(1)
        scope_year = None
        scoped = [(abs(mm.start() - kw_idx), int(mm.group(2)))
                  for mm in re.finditer(
                      r"\b(" + "|".join(MONTHS) + r")\s+((?:19|20)\d{2})\b", s)]
        scoped += [(abs(mm.start() - kw_idx), int(mm.group(1)))
                   for mm in re.finditer(r"\b((?:19|20)\d{2})\s+(?:authorization|program)\b",
                                         s, re.I)]
        if scoped:
            scope_year = min(scoped)[1]
        cands.append({"sentence": s, "amount": amt, "as_of": asof,
                      "approximate": ("approximately" in low or "approx." in low),
                      "scope_year": scope_year, "unit_word": unit_word})
    return cands


def _select_candidate(cands):
    """The period-end narrative figure: latest as-of date governs;
    among equals, a precise statement beats an 'approximately' one;
    document order breaks remaining ties."""
    if not cands:
        return None
    def rank(c):
        return (_dkey(c["as_of"]), 0 if c["as_of"] else -1,
                0 if not c["approximate"] else -1)
    best = None
    for c in cands:
        if best is None or rank(c) >= rank(best):
            best = c
    return best


_ASOF_AUTH_RE = re.compile(r"authori[sz]ed\b[^$]{0,120}?to repurchase up to\s+", re.I)


def _asof_authorization_candidates(sentences):
    """R5 sentences: 'As of <date>, we were/are authorized ... to
    repurchase up to $X' — a statement of the CURRENT authorization
    state, not a dated board event."""
    out = []
    for s in sentences:
        if _is_table_sentence(s):
            continue
        m = _ASOF_AUTH_RE.search(s)
        if not m:
            continue
        asof = None
        am = re.search(r"as of\s+(" + DATE_RE.pattern + r")", s, re.I)
        if am:
            asof = am.group(1)
        amt = _money(s, m.end() - 1)
        if amt:
            out.append({"sentence": s, "amount": amt, "as_of": asof,
                        "approximate": "approximately" in s.lower(),
                        "scope_year": None, "asof_rule": True})
    return out


# ---------------------------------------------------------------------
# Program events, increases (R7), ledger
# ---------------------------------------------------------------------

_INCREASE_TOTAL_RES = (
    re.compile(r"for a total authorization of\s+", re.I),
    re.compile(r"total authorization (?:of|to)\s+", re.I),
    re.compile(r"increase[^$]{0,60}?\bto\s+\$", re.I),
)
_INCREASE_ADD_RES = (
    re.compile(r"(?:approved|authorized) an additional\s+", re.I),
    re.compile(r"increased (?:its|the) [^$]{0,60}?authorization by\s+", re.I),
    re.compile(r"authorized an increase of\s+", re.I),
    re.compile(r"announced an? (?:additional )?\$", re.I),
)


def _increase_events(sentences):
    """Board increases/additions in document order. Each is classified
    'total' (the $ figure is the new total authorization) or
    'addition' (the $ figure is the increment) from its own wording."""
    events = []
    for i, s in enumerate(sentences):
        low = s.lower()
        if "repurchas" not in low and "authorization" not in low:
            continue
        kind, amt = None, None
        if "authorized an increase" in low:
            kind = "total"
            for rx in _INCREASE_TOTAL_RES:
                m = rx.search(s)
                if m:
                    amt = _money(s, m.start())
                    break
            if amt is None:
                amt = _money(s)
        else:
            for rx in _INCREASE_ADD_RES:
                m = rx.search(s)
                if m and ("increase" in low or "additional" in low):
                    a = _money(s, m.end())
                    if a:
                        kind, amt = "addition", a
                        break
        if kind and amt:
            events.append({"idx": i, "sentence": s, "kind": kind,
                           "amount_usd": amt, "date": _date_any(s)})
    return events


def _date_nearest(sentence, idx):
    """The date nearest character offset `idx`: a glued sentence (an
    Item 703 footnote runs straight out of the flattened table in
    stripped text) can carry several dates, and the one that belongs
    to an 'up to $X' amount is the closest one, not the first."""
    best, best_dist = None, None
    pats = (DATE_RE,
            re.compile(r"(January|February|March|April|May|June|July|August|"
                       r"September|October|November|December)\s+\d{4}"),
            re.compile(r"\bIn (\d{4})\b"))
    for rx in pats:
        for m in rx.finditer(sentence):
            dist = min(abs(m.start() - idx), abs(m.end() - idx))
            if best_dist is None or dist < best_dist:
                best, best_dist = m.group(0), dist
    return best


def _program_events(sentences, asof_sentences):
    """Every dated board approval of a repurchase program with an
    'up to $X' amount (originations and later standalone programs).
    As-of state sentences (R5) and increase sentences are events of
    other kinds and are excluded."""
    events = []
    asof_ids = {id(c["sentence"]) for c in asof_sentences}
    for s in sentences:
        if id(s) in asof_ids:
            continue
        low = s.lower()
        if "repurchas" not in low:
            continue
        if not any(v in low for v in ("authorized", "approved", "announced")):
            continue
        if "authorized an increase" in low:
            continue
        up_to = re.search(r"up to\s+", s)
        if up_to:
            d = _date_nearest(s, up_to.start())
            amt = _money(s, up_to.start())
            if d and amt:
                events.append({"date": d, "amount_usd": amt, "quote": s})
    return events


# ---------------------------------------------------------------------
# Main extraction
# ---------------------------------------------------------------------

def extract_buyback(text, raw_html=None, period_end=None):
    """Extract the repurchase-authorization state from one periodic
    filing. `text` is the filing's stripped full text; `raw_html` (the
    same filing's raw HTML, when the caller has it) enables the R1/R2
    grid parse of the Item 703 table; `period_end` (the filing's
    report date, ISO or as written) anchors the post-period gates when
    the filing never states an as-of date for its remaining figure.
    Pure function — unit-testable."""
    sentences = split_sentences(text)
    out = {
        "authorization_total_usd": None,
        "cumulative_used_usd": None,
        "remaining_usd": None,
        "remaining_basis": None,
        "remaining_as_of": None,
        "authorization_date": None,
        "effective_from": None,
        "expiry": None,
        "conditions_quote": None,
        "source_quote": None,
        "construction_quote": None,
        "share_denominated_remaining": None,
        "flags": [],
        "history": [],
    }
    history = out["history"]
    flags = out["flags"]
    rejected_implausible = [False]

    # --- Share-denominated authorizations (never scored) ---
    for s in sentences:
        low = s.lower()
        d = _date_any(s)
        if (d and "repurchas" in low
                and re.search(r"up to\s+[\d,]+\s+shares", low)
                and not MONEY_RE.search(s)):
            flags.append("buyback_remaining_share_denominated")
            history.append({"type": "program_authorized_shares", "date": d,
                            "quote": s})
            break

    # --- Program events (originations + standalone programs) ---
    asof_cands = _asof_authorization_candidates(sentences)
    prog_events = _program_events(sentences, asof_cands)
    # Filings repeat the same program in several sections, often with
    # the date at different precision ("April 2026" in MD&A, "April
    # 30, 2026" in the note) — same amount within ~a month is one
    # event, counted once (the ledger and the decomposition sum over
    # these; if two genuinely distinct same-size programs were ever
    # merged, the R6 table reconciliation fails closed, not wrong).
    def _ord(dstr):
        y, m, d = _dkey(dstr)
        return y * 372 + m * 31 + d
    _merged = []
    for ev in sorted(prog_events, key=lambda e: _ord(e["date"])):
        if _merged and ev["amount_usd"] == _merged[-1]["amount_usd"] \
                and abs(_ord(ev["date"]) - _ord(_merged[-1]["date"])) <= 45:
            continue
        _merged.append(ev)
    prog_events = _merged
    for ev in prog_events:
        history.append({"type": "program_authorized", "date": ev["date"],
                        "amount_usd": ev["amount_usd"], "quote": ev["quote"]})
    if prog_events:
        d0 = min(prog_events, key=lambda e: _dkey(e["date"]))
        out["authorization_total_usd"] = d0["amount_usd"]

    # --- Cumulative used: "repurchased an aggregate of $X under ..." ---
    for s in sentences:
        if "repurchased an aggregate of" in s.lower():
            amt = _money(s)
            if amt:
                out["cumulative_used_usd"] = amt
                d = _date_in(s)
                history.append({"type": "cumulative_used", "as_of": d,
                                "amount_usd": amt, "quote": s})
                break

    # --- Item 703 table (R1–R3) ---
    table = _parse_item703(text, raw_html)
    table_remaining = table["remaining_usd"] if table else None
    if table and table.get("note") == "total_row_inconsistent":
        history.append({"type": "item_703_total_row_inconsistent",
                        "quote": table.get("quote")})
    if table and table_remaining is not None \
            and table.get("scale_source") == "document":
        # Tier-3 scale: sanity-check against the authorization
        # ledger — step the inferred scale down while the figure
        # exceeds every authorization the filing discloses.
        auth_total = sum(ev["amount_usd"] for ev in prog_events) \
            if prog_events else None
        scale = table.get("scale_applied") or 1.0
        raw = table.get("remaining_raw") or 0.0
        while auth_total and scale > 1.0 \
                and raw * scale > auth_total * 1.02:
            scale /= 1000.0
        if raw * scale != table_remaining:
            table_remaining = raw * scale
            table["remaining_usd"] = table_remaining
            history.append({"type": "table_scale_document_inferred_demoted",
                            "amount_usd": table_remaining,
                            "quote": table.get("quote")})
        else:
            history.append({"type": "table_scale_inferred_from_document",
                            "amount_usd": table_remaining,
                            "quote": table.get("quote")})
        flags.append("buyback_table_scale_inferred_from_document")
    if table and table_remaining is not None:
        history.append({"type": "item_703_table", "amount_usd": table_remaining,
                        "quote": table.get("quote")})

    # --- Narrative remaining at period end (R4) + as-of rule (R5) ---
    cands = _availability_candidates(sentences)
    # Ledger, built from the filing's own events (R5/R6/R8 guards):
    # in-period program authorizations + in-period additions, minus
    # stated cumulative executions. Anchored only when at least one
    # origination-style event was found.
    period_key = _dkey(period_end) if period_end else (0, 0, 0)
    inc_events = _increase_events(sentences)

    def _ledger_bound():
        if not prog_events or out["cumulative_used_usd"] is None:
            return None
        auth = 0.0
        for ev in prog_events:
            if period_key == (0, 0, 0) or _dkey(ev["date"]) <= period_key:
                auth += ev["amount_usd"]
        for ev in inc_events:
            if ev["kind"] == "addition" and (
                    period_key == (0, 0, 0) or _dkey(ev["date"]) <= period_key):
                auth += ev["amount_usd"]
        return (auth - out["cumulative_used_usd"]) * 1.02

    # R5 validation: table consistency, else a buildable ledger bound.
    validated_asof = []
    for c in asof_cands:
        ok = False
        if table_remaining is not None and abs(c["amount"] - table_remaining) \
                <= _AGREE_TOL * max(table_remaining, 1.0):
            ok = True
        else:
            bound = _ledger_bound()
            if bound is not None and c["amount"] <= bound:
                ok = True
        if ok:
            validated_asof.append(c)
        else:
            flags.append("buyback_asof_authorization_unvalidated")
            history.append({"type": "asof_authorization_unvalidated",
                            "amount_usd": c["amount"], "quote": c["sentence"]})
    cands.extend(validated_asof)
    period_cand = _select_candidate(cands)
    period_remaining = period_cand["amount"] if period_cand else None
    period_quote = period_cand["sentence"] if period_cand else None
    if period_cand is not None:
        asof = period_cand["as_of"]
        if asof is None and period_end:
            asof = period_end
        out["remaining_as_of"] = asof
        history.append({"type": "remaining_at_period_end", "as_of": asof,
                        "amount_usd": period_remaining, "quote": period_quote})
    if period_key == (0, 0, 0):
        period_key = _dkey(out["remaining_as_of"])

    # --- R6: multi-program decomposition ---
    decomp_sum, decomp_fail = None, False
    scoped = [c for c in cands if c.get("scope_year") and not c.get("asof_rule")]
    if scoped and period_key != (0, 0, 0):
        scoped_years = {c["scope_year"] for c in scoped}
        scoped_total, seen_sc = 0.0, set()
        for c in scoped:
            key = (c["scope_year"], c["amount"])
            if key not in seen_sc:
                seen_sc.add(key)
                scoped_total += c["amount"]
        # Programs the filing itself says were completed/exhausted
        # contribute nothing, however recently they were announced.
        completed = set()
        for s in sentences:
            for cm in re.finditer(r"completed the ([A-Z][a-z]+) (\d{4}) program",
                                  s):
                completed.add((int(cm.group(2)),
                               MONTHS.index(cm.group(1)) + 1))
        uncovered = []
        for ev in prog_events:
            y = _dkey(ev["date"])[0]
            if (y, _dkey(ev["date"])[1]) in completed:
                continue
            if y in scoped_years or _dkey(ev["date"]) > period_key:
                continue
            # An undrawn program counts only if it was announced
            # recently relative to period end (older programs with no
            # stated remainder are presumed exhausted or replaced).
            pk, ek = period_key, _dkey(ev["date"])
            if (pk[0] * 12 + pk[1]) - (ek[0] * 12 + ek[1]) > 18:
                continue
            if (ev["date"], ev["amount_usd"]) not in \
                    [(u["date"], u["amount_usd"]) for u in uncovered]:
                uncovered.append(ev)
        if uncovered:
            decomp_sum = scoped_total + sum(u["amount_usd"] for u in uncovered)
            history.append({"type": "remaining_multi_program_decomposition",
                            "amount_usd": decomp_sum,
                            "scoped_usd": scoped_total,
                            "undrawn_programs": [
                                {"date": u["date"], "amount_usd": u["amount_usd"]}
                                for u in uncovered]})
            if table_remaining is not None:
                if abs(decomp_sum - table_remaining) > _DECOMP_TOL * table_remaining:
                    decomp_fail = True
            else:
                auth_total = sum(ev["amount_usd"] for ev in prog_events
                                 if _dkey(ev["date"]) <= period_key)
                if not (auth_total and decomp_sum <= auth_total * 1.02):
                    decomp_fail = True
                else:
                    flags.append("buyback_remaining_multi_program_sum")

    # --- Post-period board events (R7 increases + new programs) ---
    increase, unverified_increase = None, []
    for ev in inc_events:
        d = _dkey(ev["date"])
        if period_key != (0, 0, 0) and d and d <= period_key:
            history.append({"type": "authorization_increased_in_period",
                            "date": ev["date"], "kind": ev["kind"],
                            "amount_usd": ev["amount_usd"], "quote": ev["sentence"]})
            continue
        s = ev["sentence"]
        i = ev["idx"]
        if ev["kind"] == "addition":
            if period_remaining is not None:
                increase = {"remaining_usd": period_remaining + ev["amount_usd"],
                            "basis": "post_period_increase_added_to_period_end_remaining",
                            "total_usd": None, "date": ev["date"],
                            "effective_from": None, "quote": s,
                            "construction_quote": period_quote}
                history.append({"type": "authorization_increased",
                                "date": ev["date"],
                                "amount_usd": ev["amount_usd"],
                                "total_remaining_usd": increase["remaining_usd"],
                                "quote": s,
                                "construction_quote": period_quote})
            else:
                unverified_increase.append(ev)
            continue
        # Total-shape: the figure is the new total — it is accepted
        # only with construction evidence (the MU rule): a nearby
        # remained-available + plus/based-on sentence, the filer's own
        # stated post-event remaining, or a stated addition that
        # reconciles period-end remaining to the total.
        construction = None
        for j in range(i, min(i + 7, len(sentences))):
            cj = sentences[j].lower()
            if "remained available" in cj and ("plus" in cj or "based on" in cj):
                construction = sentences[j]
                break
        if construction is None:
            for c in cands:
                if c["sentence"] in sentences[i:i + 9] and \
                        abs(c["amount"] - ev["amount_usd"]) <= _AGREE_TOL * ev["amount_usd"]:
                    construction = c["sentence"]
                    break
        if construction is None and period_remaining is not None:
            for ev2 in inc_events:
                if ev2["kind"] == "addition" and \
                        abs(period_remaining + ev2["amount_usd"] - ev["amount_usd"]) \
                        <= _AGREE_TOL * ev["amount_usd"]:
                    construction = ev2["sentence"]
                    break
        if construction is None:
            unverified_increase.append(ev)
            continue
        eff = None
        for j in range(i, min(i + 4, len(sentences))):
            m = re.search(r"(?:to be made|will be made|be made) from\s+(" + DATE_RE.pattern + r")",
                          sentences[j])
            if m:
                eff = m.group(1)
                break
        increase = {"remaining_usd": ev["amount_usd"],
                    "basis": "post_period_increase_total_with_construction_sentence",
                    "total_usd": ev["amount_usd"], "date": ev["date"],
                    "effective_from": eff, "quote": s,
                    "construction_quote": construction}
        history.append({"type": "authorization_increased",
                        "date": ev["date"], "effective_from": eff,
                        "amount_usd": ev["amount_usd"], "quote": s,
                        "construction_quote": construction})
        break
    if increase is None and unverified_increase:
        ev = unverified_increase[0]
        flags.append("buyback_total_remaining_ambiguous")
        history.append({"type": "authorization_increase_unverified",
                        "amount_usd": ev["amount_usd"], "quote": ev["sentence"]})

    # --- Post-period NEW parallel program: the board approves a NEW
    # program after period end while the prior program "remains in
    # effect with $Y available" — total remaining is the new
    # authorization PLUS the prior remainder, and the remains-in-effect
    # sentence is the construction evidence (the same fail-closed logic
    # as the increase rule). A new program stated to REPLACE the prior
    # counts alone. With neither sentence, the event is recorded
    # unverified and never scored. ---
    new_program = None
    for i, s in enumerate(sentences):
        low = s.lower()
        if "new share repurchase program" not in low and "new repurchase program" not in low:
            continue
        if "approved" not in low and "authorized" not in low:
            continue
        up_to = re.search(r"up to\s+", s)
        if not up_to:
            continue
        amt = _money(s, up_to.start())
        if not amt:
            continue
        # Post-period means dated AFTER the period end the remaining
        # figure is as of — an in-period "new program" announcement is
        # the current program's origination, already reflected in the
        # period-end remaining, never an addition to it.
        if period_key != (0, 0, 0) and _dkey(_date_any(s)) <= period_key:
            continue
        window = sentences[max(0, i - 2):i + 9]
        replaced = any(("replac" in w.lower() or "supersede" in w.lower())
                       and "repurchas" in w.lower() for w in window)
        prior_remaining, construction = None, None
        for w in window:
            wl = w.lower()
            if "remains in effect" in wl:
                p = _money(w, wl.index("in effect"))
                if p:
                    prior_remaining, construction = p, w
                break
        if replaced:
            new_program = {"remaining_usd": amt, "new_amount_usd": amt,
                           "prior_remaining_usd": None, "date": _date_any(s),
                           "basis": "post_period_new_program_replacing_prior",
                           "quote": s, "construction_quote": construction}
        elif prior_remaining is not None:
            new_program = {"remaining_usd": amt + prior_remaining,
                           "new_amount_usd": amt,
                           "prior_remaining_usd": prior_remaining,
                           "date": _date_any(s),
                           "basis": "post_period_new_program_plus_prior_program_remaining",
                           "quote": s, "construction_quote": construction}
        else:
            history.append({"type": "authorization_new_program_unverified",
                            "amount_usd": amt, "quote": s})
            continue
        history.append({"type": "authorization_new_program",
                        "date": new_program["date"],
                        "amount_usd": new_program["new_amount_usd"],
                        "prior_remaining_usd": new_program["prior_remaining_usd"],
                        "total_remaining_usd": new_program["remaining_usd"],
                        "quote": s, "construction_quote": construction})
        break

    # --- Resolve the period-end figure (R6/R8) ---
    period_final, period_basis, period_src_quote = None, None, None
    if decomp_fail:
        flags.append("buyback_remaining_multi_program_unreconciled")
        history.append({"type": "remaining_multi_program_unreconciled",
                        "amount_usd": decomp_sum})
    elif decomp_sum is not None and table_remaining is not None:
        # R6: the Item 703 total governs; the decomposition reconciled.
        period_final, period_basis = table_remaining, "item_703_table_may_yet_be_purchased"
        period_src_quote = table.get("quote")
        history.append({"type": "remaining_multi_program_reconciled",
                        "amount_usd": table_remaining})
    elif decomp_sum is not None:
        period_final, period_basis = decomp_sum, "narrative_multi_program_sum"
        period_src_quote = period_quote
    elif period_remaining is not None and table_remaining is not None:
        narr, tab = period_remaining, table_remaining
        # A narrative figure with NO unit word that equals the table's
        # raw figure is the same figure quoted in the table's own
        # units ('the remaining amount available was $1,115' under a
        # table stated in millions) — agreement, and the scaled table
        # figure governs.
        if table.get("remaining_raw") and not period_cand.get("unit_word") \
                and abs(narr - table["remaining_raw"]) \
                <= _AGREE_TOL * max(table["remaining_raw"], 1.0):
            narr = tab
            history.append({"type": "narrative_in_table_units",
                            "amount_usd": tab, "quote": period_quote})
        # Unit-consistency test: if the figures differ by ~10^3/10^6
        # and a different table scale makes them agree, the narrative's
        # explicit unit word identifies the table's true scale.
        if abs(narr - tab) > _AGREE_TOL * max(tab, 1.0) and table.get("remaining_raw") \
                and period_cand.get("unit_word"):
            raw = table["remaining_raw"]
            for cand_scale in (1e9, 1e6, 1e3, 1.0):
                if cand_scale != table.get("scale_applied") and raw * cand_scale > 0 \
                        and abs(narr - raw * cand_scale) <= _AGREE_TOL * narr:
                    tab = raw * cand_scale
                    table_remaining = tab
                    flags.append("buyback_table_scale_inferred_from_narrative")
                    history.append({"type": "item_703_table_scale_corrected",
                                    "amount_usd": tab, "quote": table.get("quote")})
                    break
        if abs(narr - tab) <= _AGREE_TOL * max(tab, 1.0):
            if period_cand.get("approximate"):
                # An 'approximately $X' narrative yields to the precise
                # table cell it rounds.
                period_final, period_basis = tab, "item_703_table_may_yet_be_purchased"
                period_src_quote = table.get("quote")
            else:
                period_final = narr
                period_basis = ("narrative_asof_current_authorization"
                                if period_cand.get("asof_rule")
                                else "narrative_remaining_at_period_end")
                period_src_quote = period_quote
        else:
            # Disagreement: prefer whichever figure the ledger admits;
            # with no discriminating ledger the regulatory table
            # governs. The disagreement is always flagged.
            flags.append("buyback_remaining_sources_disagree")
            bound = _ledger_bound()
            narr_ok = bound is not None and narr <= bound
            tab_ok = bound is not None and tab <= bound
            if narr_ok and not tab_ok:
                period_final = narr
                period_basis = ("narrative_asof_current_authorization"
                                if period_cand.get("asof_rule")
                                else "narrative_remaining_at_period_end")
                period_src_quote = period_quote
            else:
                period_final, period_basis = tab, "item_703_table_may_yet_be_purchased"
                period_src_quote = table.get("quote")
            history.append({"type": "remaining_sources_disagree",
                            "narrative_usd": narr, "table_usd": tab,
                            "chosen_usd": period_final})
    elif period_remaining is not None:
        period_final = period_remaining
        period_basis = ("narrative_asof_current_authorization"
                        if period_cand.get("asof_rule")
                        else "narrative_remaining_at_period_end")
        period_src_quote = period_quote
    elif table_remaining is not None:
        period_final, period_basis = table_remaining, "item_703_table_may_yet_be_purchased"
        period_src_quote = table.get("quote")

    # Ledger hard bound on the period-end figure (post-period event
    # results carry their own construction evidence and are exempt).
    if period_final is not None:
        bound = _ledger_bound()
        if bound is not None and period_final > bound:
            flags.append("buyback_remaining_exceeds_ledger")
            history.append({"type": "remaining_rejected_exceeds_ledger",
                            "amount_usd": period_final})
            period_final = None

    # Post-period events supersede the period-end figure; when both
    # shapes appear, the later-dated verified event governs.
    post = None
    if increase:
        post = {"remaining_usd": increase["remaining_usd"],
                "basis": increase["basis"],
                "total_usd": increase.get("total_usd"), "date": increase["date"],
                "effective_from": increase["effective_from"],
                "quote": increase["quote"],
                "construction_quote": increase["construction_quote"]}
    if new_program and (post is None or _dkey(new_program["date"]) >= _dkey(post["date"])):
        if out["authorization_total_usd"] and new_program["prior_remaining_usd"]:
            # Live programs stack: prior program's total + the new one.
            total_auth = out["authorization_total_usd"] + new_program["new_amount_usd"]
        else:
            total_auth = new_program["remaining_usd"]
        post = {"remaining_usd": new_program["remaining_usd"],
                "basis": new_program["basis"], "total_usd": total_auth,
                "date": new_program["date"], "effective_from": None,
                "quote": new_program["quote"],
                "construction_quote": new_program["construction_quote"]}
    if post:
        out["remaining_usd"] = post["remaining_usd"]
        out["remaining_basis"] = post["basis"]
        if post["total_usd"]:
            out["authorization_total_usd"] = post["total_usd"]
        out["authorization_date"] = post["date"]
        out["effective_from"] = post["effective_from"]
        out["source_quote"] = post["quote"]
        out["construction_quote"] = post["construction_quote"]
    elif period_final is not None:
        out["remaining_usd"] = period_final
        out["remaining_basis"] = period_basis
        out["source_quote"] = period_src_quote

    # --- Floor test (R8 guard): a sub-$1M 'remaining' on a filer with
    # > $100M of repurchase activity in this filing is an extraction
    # failure, not a figure — the leg goes missing, never the floor. ---
    if out["remaining_usd"] is not None and out["remaining_usd"] < 1e6:
        # Activity means EXECUTIONS: the table's period repurchases,
        # stated cumulative program executions, and narrative period
        # executions ('during the quarter, the Company repurchased
        # $X'). (A free scan of every $ in every 'repurchas' sentence
        # also counts authorizations and the remaining figure itself
        # — it fired on the very figure it was judging.)
        activity = 0.0
        if table and table.get("period_repurchases_usd"):
            activity = max(activity, table["period_repurchases_usd"])
        if out["cumulative_used_usd"]:
            activity = max(activity, out["cumulative_used_usd"])
        for s in sentences:
            low = s.lower()
            if "repurchased" not in low or "remain" in low:
                continue
            if not any(f in low for f in (
                    "during the", "during fiscal", "in the quarter",
                    "for the three months", "for the six months",
                    "for the nine months", "months ended",
                    "aggregate purchase price")):
                continue
            v = _money_nearest(s, low.find("repurchased"))
            if v:
                activity = max(activity, v)
        if activity > 1e8:
            history.append({"type": "remaining_rejected_implausible",
                            "amount_usd": out["remaining_usd"],
                            "activity_usd": activity})
            flags.append("buyback_remaining_extraction_implausible")
            rejected_implausible[0] = True
            out["remaining_usd"] = None
            out["remaining_basis"] = None
            out["source_quote"] = None

    # --- Execution notes in the period (program buys only) ---
    seen_exec = set()
    for s in sentences:
        low = s.lower()
        if ("did not repurchase any common stock under" in low
                or re.search(r"repurchased [\d.]+ million shares", low)):
            key = s[:70]
            if key not in seen_exec:
                seen_exec.add(key)
                history.append({"type": "execution_note", "quote": s})
        if len(seen_exec) >= 3:
            break

    # --- Conditions / expiry ---
    for s in sentences:
        low = s.lower()
        if _is_program_sentence(s) and "no expiration date" in low:
            out["expiry"] = None
            history.append({"type": "conditions", "quote": s})
            if "subject to" in low:
                out["conditions_quote"] = s[:400]
            break
    if out["conditions_quote"] is None:
        for s in sentences:
            low = s.lower()
            if _is_program_sentence(s) and "subject to market conditions" in low:
                out["conditions_quote"] = s[:400]
                break

    _rejected = (rejected_implausible[0]
                 or "buyback_remaining_exceeds_ledger" in flags
                 or "buyback_remaining_multi_program_unreconciled" in flags
                 or "buyback_asof_authorization_unvalidated" in flags)
    if out["remaining_usd"] is None and not _rejected \
            and "buyback_remaining_share_denominated" not in flags:
        flags.append("buyback_remaining_not_disclosed")
    # Reconciliation cross-check (SPEC §2.1): used + remaining should
    # not exceed a stated cumulative program total (non-increase basis).
    if (increase is None and new_program is None and out["cumulative_used_usd"]
            and out["remaining_usd"] and out["authorization_total_usd"]
            and out["cumulative_used_usd"] + out["remaining_usd"]
            > out["authorization_total_usd"] * 1.02):
        flags.append("buyback_remaining_unreconciled")
    return out
