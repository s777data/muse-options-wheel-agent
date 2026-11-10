# Reporting Style Preferences

## Brief Output Preference
The user prefers concise, minimal output for profit-taking and position analysis. Key preferences observed:

- **Remove formula calculation tables** — show only the decision and key context
- **Condense position summary** to: ticker, strike, expiration, entry credit, current ask, profit %, decision
- **Omit elapsed fraction / required profit % breakdown** unless explicitly requested
- **Lead with the decision** (TAKE PROFIT / HOLD) and the comparison result
- **Keep disclaimer brief**: "Analytical signal only — not a trade authorization"

## Applied Example (from session)
> **Profit-Taking Decider — HOOD $92P 2026-08-28 (Short Put)**
>
> **Position Summary**
> - Opened: 2026-07-28 | Expires: 2026-08-28 (7 days left)
> - Entry credit: $8.10/share ($810 total)
> - Current ask (buy-to-close): $0.39/share ($39 total)
> - Unrealized gain: ~95% of premium captured
>
> **Decision**: **TAKE PROFIT** ✅ — Actual capture (95.2%) exceeds the square-root elapsed-lifetime threshold (88.0%).
>
> **Key Context**
> - Premium decayed from $2.14 → $0.36 (mark) → $0.39 (ask)
> - Theta: -$0.103/day | Delta: -0.07 (deep OTM)
> - 92% chance of profit (short) per Robinhood
>
> ⚠️ **Analytical signal only — not a trade authorization.** Data from Robinhood MCP (quote: 2026-08-21 15:26 UTC).

## General Principle
When displaying analysis results from any skill (profit-taking-decider, screening, order previews, etc.), default to this brief format. Full calculation tables available on request.