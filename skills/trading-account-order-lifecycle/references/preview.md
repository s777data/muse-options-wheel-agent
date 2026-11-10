## Required Trade Preview
------------------------------------------------------------------------------------------------------------------
Before submitting ANY order, show me a trade preview using a Robinhood quote no more than `${QUOTE_MAX_AGE_SECONDS}` seconds old. Every field in the preview template is required unless the template or strategy rule explicitly permits `N/A`; `UNAVAILABLE` is not a complete preview. If a required account, quote, contract, event, fee, probability, size, tick, collateral, or position field cannot be retrieved and verified, stop without displaying `READY FOR CONFIRMATION - NOT YET AUTHORIZED`. Use limit orders only for options; never use a market order. Default time in force to `DAY` unless I explicitly request another supported value. A fill is never guaranteed.

Use `k = ${DEFAULT_PRICING_FRACTION}` as the initial spread fraction for opening and closing orders. `k` must remain from `${MIN_PRICING_FRACTION}` through `${MAX_PRICING_FRACTION}` inclusive; no exception may place it outside that range. Any value other than `${DEFAULT_PRICING_FRACTION}` requires an explanation in a new preview. Never reprice automatically.

For a sell order, interpret the permitted pricing-fraction range as movement through the bid/ask spread, not as a percentage discount from the option price:

```text
spread = ask - bid
sell_limit = ask - (spread * k), where k is from ${MIN_PRICING_FRACTION} through ${MAX_PRICING_FRACTION} inclusive
```

For a buy-to-close order:

```text
spread = ask - bid
buy_limit = bid + (spread * k), where k is from ${MIN_PRICING_FRACTION} through ${MAX_PRICING_FRACTION} inclusive
```

Round the proposed limit to the contract's valid minimum tick while keeping it within the current bid/ask spread. For a `SELL TO OPEN` limit, round toward the bid; for a `BUY TO CLOSE` limit, round toward the ask. If the unrounded value is exactly on a valid tick, do not change it. Report both the unrounded formula result and final rounded limit. If the valid tick size is unavailable or no valid tick exists within the current spread after directional rounding, exclude the contract and report `NO TRADE`; never move a limit outside the spread. If an order does not fill, do not automatically reprice it; every revised limit requires a new preview and confirmation.

For every preview and qualification calculation, use the proposed rounded limit—not the mark, bid, ask, or midpoint—as the estimated execution price. For `Estimated dollar amount`, report `rounded limit price * ${STANDARD_OPTION_CONTRACT_SHARES} * number of contracts` and label it as a credit for `SELL TO OPEN` or a debit for `BUY TO CLOSE`. Report estimated fees and estimated net premium or debit separately. After execution, replace estimates with Robinhood's verified filled quantity and average fill price; never rewrite the original decision-time snapshot.

Require `ask >= bid >= ${ZERO_VALUE}` and midpoint `> ${ZERO_VALUE}` before calculating a preview. A crossed, negative, zero-midpoint, or otherwise invalid market is not previewable. Calculate bid/ask spread ratio as `(ask - bid) / ((ask + bid) / ${MIDPOINT_DIVISOR})`. Use Robinhood's option mark when provided; otherwise use the bid/ask midpoint and label it as a calculated midpoint rather than a broker-provided mark. The mark is reporting data only and never replaces the rounded limit in a qualification or preview calculation.

Calculate preview risk fields by strategy:

- CSP `SELL TO OPEN`: `breakeven = strike - estimated net premium per share`; `maximum theoretical loss = (strike * ${STANDARD_OPTION_CONTRACT_SHARES} * contracts) - estimated net premium`, assuming shares fall to `${ZERO_VALUE}`. Include estimated fees in net premium.
- Covered-call `SELL TO OPEN`: `breakeven = premium-adjusted cost basis per share - estimated net call premium per share`; `maximum theoretical loss on the combined covered position = (premium-adjusted cost basis per share * ${STANDARD_OPTION_CONTRACT_SHARES} * contracts) - estimated net call premium`, assuming shares fall to `${ZERO_VALUE}`. Separately disclose that upside is capped and assignment sells `${STANDARD_OPTION_CONTRACT_SHARES}` shares per contract at the strike.
- `BUY TO CLOSE`: report breakeven and future maximum theoretical loss as `N/A - short obligation closed`; separately report the estimated realized strategy profit or loss using broker-confirmed entry and closing values plus fees.

```text
Ticker:
Action: BUY TO CLOSE or SELL TO OPEN
Security:
Quantity:
Estimated dollar amount:
Current underlying market price:
Option mark price:
Order type:
BID price:
ASK price:
Bid/ask sizes:
Bid/ask spread and spread percentage:
Quote timestamp and age:
Limit price, if applicable:
Pricing fraction k:
Time in force:
Current buying power:
Unleveraged buying power:
Cash collateral required or released:
No-margin check: PASS or FAIL
Position after trade:
Estimated remaining buying power:
Estimated fees:
Estimated net premium or debit:
Holding-period net ROC, if applicable:
Normalized covered-call net ROC, if applicable:
Expiration and days to expiration:
Breakeven at expiration:
Maximum theoretical loss:
Implied volatility:
Delta:
Robinhood short-side probability of profit:
Robinhood-provided probability of expiring out-of-the-money, if available (optional reporting data):
CSP short-side probability-of-profit qualification, if applicable:
Robinhood-provided probability of touching, if available for a covered call:
Covered-call delta-based high-share-retention qualification, if applicable:
Covered-call empirical calibration status, if applicable:
Option volume and open interest:
Next earnings date:
Next ex-dividend date, if applicable:
Early-assignment or call-away warning, if applicable:

READY FOR CONFIRMATION - NOT YET AUTHORIZED
```

Do not submit the order yet.

A preview generated by a schedule or automation is analysis only. A confirmation authorizes submission only when it is given in the same Codex task as the complete preview and refers unambiguously to that one order. If quote age exceeds `${QUOTE_MAX_AGE_SECONDS}` seconds, or buying power, collateral, position, contract, quantity, price, or another preview field changes before submission, refresh the complete preview and request a new confirmation. A confirmation in another task or for an earlier preview is invalid.