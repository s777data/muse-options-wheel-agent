---
name: Trading-account-covered-call-process
description: "Evaluate assigned or otherwise eligible Trading-account share lots for covered calls using lot-specific basis, trend and momentum screens, contract risk and liquidity, call-away controls, and empirical outcome calibration."
---

# Trading-account Covered-Call Process

Use this skill for each eligible uncovered standard share lot (no cover call assigned). Never cover shares already committed to an open or pending call order. Use `$Trading-account-order-lifecycle` before displaying a complete actionable preview.

Load references progressively:

1. Read [references/lot-eligibility-and-basis.md](references/lot-eligibility-and-basis.md) for every assigned-share evaluation.
2. Read [references/screening.md](references/screening.md) when the lot is eligible for technical screening.
3. Read [references/selection-and-risk.md](references/selection-and-risk.md) when Screening passes.
4. After a contract qualifies from above step, pass it to trading-account-order-lifecycle skill.

Covered-call analysis never authorizes submission. The complete preview and explicit call-away acknowledgment remain mandatory.
<!-- END OF Trading-account-COVERED-CALL-PROCESS SKILL.md -->
