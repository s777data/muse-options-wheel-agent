#!/usr/bin/env python3
"""Daily wheel report generator (D-28 — code version).

Rebuilds the agentic account's wheel ledger from the broker's filled
option orders (read-only, via scripts/robinhood_transport.py), then
writes the day's "Today's Wheel" report in the Hermes format:

    outputs/daily-wheel/YYYY-MM-DD.md    (markdown)
    outputs/daily-wheel/YYYY-MM-DD.png   (phone-style screenshot render)

The .md is the single source of truth; the .png is rendered from it.
Run with the system python3 (needs Pillow; the repo .venv is not
required — robinhood_transport is stdlib-only):

    python3 scripts/daily_wheel_report.py [--date YYYY-MM-DD]
                                          [--account 858226392]

Exit code 0 always on success; prints a one-line JSON summary. When no
order filled on the report date, nothing is written and the summary
reports {"fills_today": 0}.

Ledger rules (v1):
- A position is one contract (symbol, type, strike, expiry). Realized
  P&L is recognized when the position returns to zero, as total sell
  premiums minus total buy premiums over its life.
- Collateral for a contract = strike x 100 x max simultaneous short
  quantity observed across its fills.
- YTD percentages (D-33, set by BIG SANDY 2026-11-11): realized and
  net premiums are both divided by STARTING_CAPITAL_USD — the agentic
  account's $10,000 seed (cash reconciliation: $14,086.98 cash at the
  2026-11-11 close = $10,000 + $4,089 net premiums - $2.02 fees).
- Contracts still open past expiry (assignment/expiry residuals) are
  counted as open; assignments are reported as 0 until an equity
  delivery is observable in the order history (none so far).
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

PT = ZoneInfo("America/Los_Angeles")
DEFAULT_ACCOUNT = "858226392"
STARTING_CAPITAL_USD = 10_000.0  # D-33: YTD % basis — the account's seed capital


# ---------------------------------------------------------------- ledger

@dataclass
class Fill:
    day: date
    ts: datetime         # full fill timestamp (walk order within a day matters)
    side: str            # buy | sell
    effect: str          # open | close
    qty: float
    price: float
    premium: float       # processed_premium, unsigned


@dataclass
class Contract:
    symbol: str
    option_type: str     # put | call
    strike: float
    expiry: date
    fills: list = field(default_factory=list)

    @property
    def key(self):
        return (self.symbol, self.option_type, self.strike, self.expiry)

    def _walk(self):
        """Yield (fill, signed_qty_after) chronologically; short = negative."""
        pos = 0.0
        for f in sorted(self.fills, key=lambda x: x.ts):
            if (f.side, f.effect) in (("sell", "open"), ("buy", "close")):
                pos += -f.qty if f.side == "sell" else f.qty
            else:  # buy open / sell close (long side; not used by the wheel yet)
                pos += f.qty if f.side == "buy" else -f.qty
            yield f, pos

    @property
    def credits(self):
        return sum(f.premium for f in self.fills if f.side == "sell")

    @property
    def debits(self):
        return sum(f.premium for f in self.fills if f.side == "buy")

    @property
    def net_qty(self):
        return list(self._walk())[-1][1] if self.fills else 0.0

    @property
    def max_short(self):
        return max((-pos for _, pos in self._walk()), default=0.0)

    @property
    def collateral(self):
        return self.strike * 100 * self.max_short

    @property
    def is_closed(self):
        return self.net_qty == 0 and bool(self.fills)

    @property
    def realized(self):
        return self.credits - self.debits if self.is_closed else 0.0

    @property
    def close_day(self):
        last_zero = None
        for f, pos in self._walk():
            if pos == 0:
                last_zero = f.day
        return last_zero if self.is_closed else None

    def first_ts_on(self, day, effect):
        tss = [f.ts for f in self.fills if f.day == day and f.effect == effect]
        return min(tss) if tss else None

    def opens_on(self, day):
        fs = [f for f in self.fills if f.day == day and f.effect == "open" and f.side == "sell"]
        return sum(f.qty for f in fs), sum(f.premium for f in fs)

    def closes_on(self, day):
        fs = [f for f in self.fills if f.day == day and f.effect == "close"]
        return sum(f.qty for f in fs), sum(f.premium for f in fs)


def build_contracts(orders):
    """Group raw get_option_orders results into Contracts (filled only)."""
    contracts = {}
    for od in orders:
        if od.get("state") != "filled":
            continue
        legs = od.get("legs") or []
        if len(legs) != 1:
            continue  # v1: single-leg only (the wheel trades single-leg)
        leg = legs[0]
        created = datetime.fromisoformat(od["created_at"].replace("Z", "+00:00"))
        fill = Fill(
            day=created.astimezone(PT).date(),
            ts=created,
            side=leg.get("side"),
            effect=leg.get("position_effect"),
            qty=float(od.get("quantity", 0)),
            price=float(od.get("price", 0)),
            premium=float(od.get("processed_premium", 0) or 0),
        )
        key = (od.get("chain_symbol"), leg.get("option_type"),
               float(leg.get("strike_price")), date.fromisoformat(leg.get("expiration_date")))
        c = contracts.setdefault(key, Contract(*key))
        c.fills.append(fill)
    return list(contracts.values())


def letter(score):
    for band, lab in ((90, "A+"), (85, "A"), (80, "A-"), (75, "B+"), (70, "B"),
                      (65, "B-"), (60, "C+"), (55, "C"), (50, "C-"), (45, "D+"), (40, "D")):
        if score >= band:
            return lab
    return "F"


def load_scores():
    """Latest scored (business, value) per ticker across all Phase 5 runs."""
    path = REPO / "outputs" / "phase5-heatmap-runs.json"
    scores = {}
    if not path.exists():
        return scores
    data = json.loads(path.read_text())
    for run in data.get("runs", []):
        for row in run.get("rows", []):
            if row.get("status") == "ok" and row.get("business") is not None:
                scores[row["ticker"]] = (row["business"], row["value"])
    return scores


# ---------------------------------------------------------------- report

def money(x, signed=False):
    s = f"${abs(x):,.0f}"
    if signed:
        return ("+" if x >= 0 else "-") + s
    return ("-" if x < 0 else "") + s


def build_report(contracts, day, scores, total_orders, first_fill, last_fill):
    opens, closes = [], []
    for c in contracts:
        oq, op = c.opens_on(day)
        if oq:
            opens.append((c, oq, op))
        if c.is_closed and c.close_day == day:
            cq, cp = c.closes_on(day)
            closes.append((c, cq, cp))
    if not opens and not closes:
        return None

    def grades(c):
        if c.symbol not in scores:
            return None
        biz, val = scores[c.symbol]
        return f"🏷️ Value {letter(val)} ({val:.2f}) · 🏢 Quality {letter(biz)} ({biz:.2f})"

    def header(c):
        dte = (c.expiry - day).days
        suffix = "P" if c.option_type == "put" else "C"
        return f"🔹 **{c.symbol} ${c.strike:.2f}{suffix} · {dte} DTE**"

    lines = [f"📊 **Today's Wheel | {day.month}/{day.day}**", ""]
    new_premium = new_collateral = 0.0
    for c, qty, prem in sorted(opens, key=lambda t: t[0].first_ts_on(day, "open")):
        avg = prem / (qty * 100)
        coll = c.strike * 100 * qty
        new_premium += prem
        new_collateral += coll
        lines += [header(c), f"Sell {qty:g} @ ${avg:.2f}",
                  f"💵 +{money(prem)} net · 🔒 {money(coll)} new collateral · {abs(c.net_qty):g} open"]
        g = grades(c)
        if g:
            lines.append(g)
        lines.append("")
    profit_taken = collateral_released = 0.0
    for c, qty, prem in sorted(closes, key=lambda t: t[0].first_ts_on(day, "close")):
        avg = prem / (qty * 100) if qty else 0.0
        profit_taken += c.realized
        released = c.strike * 100 * qty
        collateral_released += released
        lines += [header(c), "✅ **Profit taken**", f"BTC {qty:g} @ ${avg:.2f}",
                  f"💵 Original premium: **{money(c.credits, signed=True)}**",
                  f"💳 Closing debit: **-{money(c.debits)}**",
                  f"📈 Realized profit: **{money(c.realized, signed=True)} gross**",
                  f"💰 Net profit: **{money(c.realized, signed=True)}**",
                  f"🔓 Collateral released: **{money(released)}** · 0 open"]
        g = grades(c)
        if g:
            lines.append(g)
        lines.append("")

    pct = (new_premium / new_collateral * 100) if new_collateral else 0.0
    lines += [f"💵 **New premium: {money(new_premium, signed=True)} ({pct:.2f}% of new collateral)**",
              f"🔒 **New collateral: {money(new_collateral)}**",
              f"📈 **Profit taken: {money(profit_taken, signed=True)}**",
              f"🔓 **Collateral released: {money(collateral_released)}**", "", "📈 **YTD Wheel**", ""]

    upto = [c for c in contracts if any(f.day <= day for f in c.fills)]
    closed = [c for c in upto if c.is_closed]
    open_ = [c for c in upto if not c.is_closed]
    wins = sum(1 for c in closed if c.realized > 0)
    realized_total = sum(c.realized for c in closed)
    net_premiums = sum(c.credits - c.debits for c in upto)
    rpct = realized_total / STARTING_CAPITAL_USD * 100
    npct = net_premiums / STARTING_CAPITAL_USD * 100
    lines += [f"✅ Closed wins: **{wins}/{len(closed)} · {wins / len(closed) * 100:.0f}%**" if closed else "✅ Closed wins: **0/0**",
              f"🗂️ **{len(upto)} positions · {len(open_)} open · {len(closed)} closed · 0 assignments**",
              f"💰 Realized: **{money(realized_total, signed=True)} ({rpct:.2f}%)**",
              f"💵 Net premiums: **{money(net_premiums)} ({npct:.2f}%) before fees**", "",
              f"*Grades are the wheel's code-engine Value / Business scores (0–100, letter band "
              f"A+ ≥90, A- 80–84.9, B 70–74.9, B- 65–69.9, C+ 60–64.9, C 55–59.9, D+ 45–49.9). "
              f"Ledger reconstructed from the full broker order history of the agentic account "
              f"({total_orders} orders, {first_fill:%b} {first_fill.day} – {last_fill:%b} {last_fill.day}, {last_fill.year}). "
              f"YTD % basis: realized ÷ starting capital ({money(STARTING_CAPITAL_USD)} seed); "
              f"net premiums ÷ starting capital.*"]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- png render

def _is_emoji(ch):
    o = ord(ch)
    return o >= 0x1F000 or 0x2600 <= o <= 0x27BF or o == 0x2B50


def parse_segments(line):
    """Split an md line into ('e', emoji) | ('t', text) | ('b', bold) segments."""
    segs, bold, buf = [], False, ""
    def flush():
        nonlocal buf
        if buf:
            segs.append(("b" if bold else "t", buf))
            buf = ""
    i = 0
    while i < len(line):
        if line.startswith("**", i):
            flush()
            bold = not bold
            i += 2
            continue
        ch = line[i]
        if _is_emoji(ch):
            flush()
            emo = ch
            if i + 1 < len(line) and line[i + 1] == "\uFE0F":
                emo += "\uFE0F"
                i += 1
            segs.append(("e", emo))
            i += 1
            continue
        buf += ch
        i += 1
    flush()
    return segs


def render_png(md_text, out_path):
    from PIL import Image, ImageDraw, ImageFont

    FS, LH, GAP = 33, 47, 30
    PADX, PADTOP, PADBOT = 34, 36, 42
    EMOJI_H = 40
    fonts_dir = Path("/usr/share/fonts/truetype/noto")
    f_reg = ImageFont.truetype(str(fonts_dir / "NotoSans-Regular.ttf"), FS)
    f_bold = ImageFont.truetype(str(fonts_dir / "NotoSans-Bold.ttf"), FS)
    # Footnote font: DejaVu Sans — Noto Sans Italic renders ≥ as tofu on this system.
    try:
        f_foot = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 21)
    except Exception:
        f_foot = ImageFont.truetype(str(fonts_dir / "NotoSans-Regular.ttf"), 21)
    f_emoji = ImageFont.truetype(str(fonts_dir / "NotoColorEmoji.ttf"), 109)

    cache = {}
    def emoji_tile(ch):
        if ch not in cache:
            tile = Image.new("RGBA", (240, 160), (0, 0, 0, 0))
            d = ImageDraw.Draw(tile)
            d.text((10, 10), ch, font=f_emoji, embedded_color=True)
            bbox = tile.getbbox()
            if bbox is None:
                if ch.endswith("\uFE0F"):
                    return emoji_tile(ch[:-1])
                tile = Image.new("RGBA", (EMOJI_H, EMOJI_H), (0, 0, 0, 0))
            else:
                tile = tile.crop(bbox)
                w, h = tile.size
                tile = tile.resize((max(1, round(w * EMOJI_H / h)), EMOJI_H), Image.LANCZOS)
            cache[ch] = tile
        return cache[ch]

    items = []  # ('line', segs) | ('gap',) | ('foot', text)
    for raw in md_text.splitlines():
        if not raw.strip():
            items.append(("gap", None))
        elif raw.startswith("*") and raw.endswith("*") and not raw.startswith("**"):
            items.append(("foot", raw.strip("*")))
        else:
            items.append(("line", parse_segments(raw)))

    probe = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    def seg_w(seg):
        if seg[0] == "e":
            return emoji_tile(seg[1]).size[0]
        return probe.textlength(seg[1], font=f_bold if seg[0] == "b" else f_reg)

    content_w = max((sum(seg_w(s) for s in segs) for kind, segs in items if kind == "line"), default=600)
    W = int(content_w + 2 * PADX)

    foot_lines = []
    for kind, val in items:
        if kind == "foot":
            words, cur = val.split(), ""
            for w_ in words:
                t = (cur + " " + w_).strip()
                if probe.textlength(t, font=f_foot) <= W - 2 * PADX:
                    cur = t
                else:
                    foot_lines.append(cur)
                    cur = w_
            if cur:
                foot_lines.append(cur)
    FOOT_LH = 30
    H = PADTOP + sum(LH if k == "line" else GAP for k, _ in items if k != "foot") \
        + (26 + len(foot_lines) * FOOT_LH if foot_lines else 0) + PADBOT

    im = Image.new("RGB", (W, H), (0, 0, 0))
    draw = ImageDraw.Draw(im)
    y = PADTOP
    for kind, val in items:
        if kind == "gap":
            y += GAP
            continue
        if kind == "foot":
            y += 26
            for fl in foot_lines:
                draw.text((PADX, y), fl, font=f_foot, fill=(142, 142, 147))
                y += FOOT_LH
            continue
        x = PADX
        for seg in val:
            if seg[0] == "e":
                tile = emoji_tile(seg[1])
                im.paste(tile, (int(x), int(y + (LH - EMOJI_H) // 2 - 2)), tile)
                x += tile.size[0]
            else:
                font = f_bold if seg[0] == "b" else f_reg
                draw.text((x, y), seg[1], font=font, fill=(255, 255, 255))
                x += draw.textlength(seg[1], font=font)
        y += LH
    im.save(out_path)
    return out_path


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=None, help="report date YYYY-MM-DD (default: today, PT)")
    ap.add_argument("--account", default=DEFAULT_ACCOUNT)
    ap.add_argument("--out-dir", default=str(REPO / "outputs" / "daily-wheel"))
    args = ap.parse_args()

    day = date.fromisoformat(args.date) if args.date else datetime.now(PT).date()

    import robinhood_transport as rt
    resp = rt.call_mcp("get_option_orders", {"account_number": args.account})
    data = resp.get("data", {})
    orders = data.get("results") or data.get("orders") or []
    contracts = build_contracts(orders)
    fill_days = sorted({f.day for c in contracts for f in c.fills})
    report = build_report(contracts, day, load_scores(), len(orders),
                          fill_days[0] if fill_days else day, fill_days[-1] if fill_days else day)
    if report is None:
        print(json.dumps({"date": str(day), "fills_today": 0}))
        return 0
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    md_path = out_dir / f"{day.isoformat()}.md"
    md_path.write_text(report)
    png_path = render_png(report, out_dir / f"{day.isoformat()}.png")
    print(json.dumps({"date": str(day), "fills_today": 1, "md": str(md_path), "png": str(png_path)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
