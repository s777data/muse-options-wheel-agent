---
name: options-wheel-agent
version: 1.0
description: Coordinate the daily options-wheel account snapshot, profit-taking evaluation, CSP screening, and covered-call evaluation workflow with Telegram delivery and explicit order confirmations.
category: trading
metadata:
  short-description: Coordinate daily options-wheel workflows
---

# Options Wheel Agent

**Timezone:** America/Los_Angeles (PST/PDT)  
**Schedule:** Daily at 10:30 AM local time on NYSE trading days  
**Communication:** Telegram bot configured in this profile (all notifications, confirmations, and reports)

## Daily Workflow

### 1. Account Snapshot (10:30 AM PST)

Run **`Trading-account-session-management`** on the **Robinhood agentic account** (cash account, option_level_2, approximately $10.8K).

- Retrieve the complete snapshot: balances, equity positions, option positions, and coverage status.
- Use read-only MCP tools; do not take order actions.
- Record the snapshot timestamp and completeness.

### 2. Profit-Taking Evaluation (Short Options >50% Return)

From the snapshot, identify **every short put and short call leg** where `position_return_pct > 50%` (premium-basis return). The downstream profit-taking decision has an additional strict gate: `premium captured > 50.00%`. A leg must satisfy both gates before it can produce `TAKE PROFIT`; equality at 50.00% is not sufficient.

For each qualifying leg:

- Run **`profit-taking-decider`** with broker-verified inputs: entry credit, adjusted mark price, opening date, expiration, multiplier, and remaining quantity. The analytical signal uses adjusted mark; any later order preview must independently use execution-aware pricing.
- If the outcome is **`TAKE PROFIT`**:
  - Run **`trading-account-order-lifecycle`** for that specific leg (buy-to-close).
  - Process legs sequentially: complete preview → confirmation → submission → reconciliation before the next leg.
  - Deliver each preview and result via Telegram.

### 3. Cash-Secured Put Screening (If Eligible Cash Exists)

**Execution order requirement:** Complete the entire Profit-Taking stage first. Start CSP screening only after Profit-Taking has finished; these stages must never run in parallel.

If the snapshot shows **cash available for option collateral** (settled, unencumbered cash > $0):

- Run **`trading-account-csp-process`** with this override:
  - `${MAX_UNDERLYING_PRICE_USD}` = **`current cash balance / 100`** (dollar amount per 100-share contract).
  - All other policy variables from the skill catalog apply.
- The skill will:
  1. Execute `screening.md` and produce the screening CSV.
  2. Run `master-value-screen-csp-recommend.md` on that CSV and produce recommendation CSV and JSON outputs.
  3. Present recommendations via Telegram as a numbered list containing PUT, STRIKE, PRICE, and ROC.
  4. Wait for user selection; if the user confirms one or more, pass each to `trading-account-order-lifecycle`.

### 4. Covered-Call Evaluation (Uncovered Share Lots)

From the snapshot, identify **every equity lot with ≥100 shares not already committed to an open or pending short call**.

For each eligible lot:

- Run **`trading-account-covered-call-process`**, loading `lot-eligibility-and-basis.md`, `screening.md`, and `selection-and-risk.md`.
- If a contract qualifies, pass it to `trading-account-order-lifecycle` for preview and user confirmation.
- Process lots sequentially via Telegram.

## Execution Rules

| Rule | Detail |
|---|---|
| **Order of operations** | Snapshot → Profit-taking → CSP (if cash) → Covered calls |
| **Sequential processing** | Each order lifecycle completes preview → confirm → submit → reconcile before the next begins |
| **User confirmation** | Every actionable preview requires explicit user `confirm` via Telegram before submission |
| **Telegram delivery** | Deliver all previews, confirmations, results, CSVs, and reports to the configured Telegram chat |
| **No cross-account pooling** | Use the agentic account only |
| **Trading days only** | Skip non-NYSE trading days; no more than one scheduled run per trading day |

## Error Handling

- **Snapshot incomplete** → Report `POSITION SNAPSHOT INCOMPLETE` via Telegram and stop the workflow.
- **Profit-taking INSUFFICIENT DATA** → Log and skip that leg; continue with the remaining legs.
- **CSP screening yields no candidates** → Report `No CSP candidates today`; continue to covered calls.
- **Covered-call lot ineligible** → Log the reason and continue to the next lot.
- **MCP/auth failure** → Report via Telegram; do not retry automatically.

## Files & Outputs

| Output | Location |
|---|---|
| Screening CSV | `${SCREENING_EXPORT_DIRECTORY}/${SCREENING_EXPORT_FILENAME_PREFIX}{timestamp}.csv` |
| CSP recommendations CSV/JSON | Per `master-value-screen-csp-recommend.md` |
| Session logs | Profile cron output directory |
| Audit trail | Per-skill audit logs for valuation, CSP, and related workflows |

## Version

`2026-08-26` — Initial daily workflow codification
