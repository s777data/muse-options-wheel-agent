## SHORT CALL PROCESS
------------------------------------------------------------------------------------------------------------------

We are holding stock in `${STANDARD_OPTION_CONTRACT_SHARES}`-share lots and are now in the SHORT CALL process. Any standard share lot is eligible for covered calls — no CSP-assignment provenance requirement.

Calculate premium-adjusted share cost basis separately for each `${STANDARD_OPTION_CONTRACT_SHARES}`-share lot:

```text
lot_cash_cost = (verified acquisition price * ${STANDARD_OPTION_CONTRACT_SHARES}) + broker-confirmed fees
```

For lots acquired by CSP assignment, use the richer assignment-based basis below; for ordinary share purchases, use the lot's average buy price as the acquisition price. When the exact acquisition details cannot be verified from Trading-account records, fall back to the broker-reported average cost (`average_buy_price`) as the basis input rather than recording `NO TRADE`:

```text
assignment_cash_cost = (CSP assignment strike * ${STANDARD_OPTION_CONTRACT_SHARES}) + broker-confirmed assignment fees
net_assigned_CSP_credit = assigned CSP entry credit - assigned CSP entry fees
net_realized_preassignment_option_income = sum(credits - debits - fees for closed CSP predecessor positions explicitly mapped to the assigned lot)
net_realized_call_income = sum(expired or closed covered-call entry credits - buy-to-close debits - all related fees)
premium_adjusted_cost_basis_per_share = (assignment_cash_cost - net_assigned_CSP_credit - net_realized_preassignment_option_income - net_realized_call_income) / ${STANDARD_OPTION_CONTRACT_SHARES}
```

Treat a realized net debit as negative income so it increases the strategy-management basis. Include a predecessor CSP only when Trading-account records establish an uninterrupted close-and-reopen lineage ending in the assigned CSP. Use `${ZERO_VALUE}` for predecessor income only when Trading-account records show that no such predecessor exists. If Trading-account indicates a predecessor or roll but its credits, debits, fees, or lineage cannot be mapped completely, do not prepare a covered-call order. Include only realized option income explicitly mapped to that lot. Do not subtract premium from an open option, another lot, or another underlying. For a multi-contract transaction, allocate broker-confirmed credits, debits, and fees pro rata by mapped contract count only when the contracts have identical terms and the allocation is arithmetically exact; otherwise require broker-provided lot allocation. If the original assignment strike, assigned CSP credit, relevant debits, fees, assignment timestamp, or lot mapping cannot be verified from Trading-account, do not use the assignment-based basis; fall back to the broker-reported average cost (`average_buy_price`) as the basis input and note the fallback in the analysis.

This premium-adjusted value is a strategy-management basis only, not a tax basis. Never present it as Trading-account's reported tax basis or use it for tax reporting.