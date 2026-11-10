---
name: Trading-account-order-lifecycle
description: "Control Trading-account option order previews, limit pricing, same-task confirmations, covered-call acknowledgment, cancellation and replacement, submission, unknown-status handling, and broker-verified execution reconciliation."
---

# Trading-account Order Lifecycle

Input: Manual: Trading-account and option order request
The Input generally comes from profit-taking-decider here but in general you automatically need those two parameters for this skill to execute.

Use this skill before displaying any complete actionable Trading-account order preview and for every cancellation, submission, replacement, status reconciliation, or post-fill update. 
<!--
The repository `AGENTS.md` controls authorization and account scope.
!-->

Read the stage-specific references before displaying a preview, accepting confirmation, changing an order, or reconciling execution. After the user confirms a complete preview, use the implementation below; do not duplicate its broker, replacement, or notification behavior in prompt text. Rules not enforced by the runner remain policy requirements and must be applied before invoking it.

## Python implementation

The executable source of truth is:

```bash
python3 /home/s777data/.hermes/profiles/options-wheel-agent/skills/trading-account-order-lifecycle/scripts/order_lifecycle.py \
  --reviewed-order /path/to/reviewed-order.json \
  --account-number <agentic-account-number> \
  --telegram-to telegram:<configured-destination> \
  --max-attempts 3 \
  --wait-seconds 60
```

The reviewed JSON must be the unchanged, user-confirmed order payload and must include option legs, quantity, limit price, time-in-force, market-hours, and contract tick. The runner uses the Hermes MCP bridge, reconciles broker state after each placement, sends deduplicated Telegram notifications, verifies cancellations, and performs bounded quote-refresh/replacement attempts. It stops on partial, unknown, unconfirmed, rejected, invalid-quote, minimum-credit, notification, or attempt-limit conditions. Broker-alert handling remains a pre-run policy control. It never treats an unverified response as a fill.

Notification deduplication is implemented in the runner with SHA-256 over provider, destination, and message. Successful sends are recorded atomically in `${NOTIFICATION_STATE_FILE}`; failed sends are not recorded. The current implementation supports the configured Telegram destination. A one-shot 1:05 PM America/Los_Angeles fill-report cron is mandatory after broker-verified fill and successful fill notification. If the fill occurs after 1:05 PM Pacific, the report is scheduled for 1:05 PM the following day. It never creates a recurring or recursive order job.

The runner does not replace the pre-run preview, explicit user confirmation, account/collateral checks, quote-freshness checks, or strategy-specific policy validation. Those remain required text-based controls because they are not implemented by this script.

Unit tests for the implementation are:

```bash
python3 -m unittest discover \
  -s /home/s777data/.hermes/profiles/options-wheel-agent/skills/trading-account-order-lifecycle/scripts \
  -p 'test_*.py'
```

## CSP collateral and premium rules

For every cash-secured put (CSP), separate broker opening capacity from economic net exposure.

### Opening-capacity gate

1. Calculate gross full-strike collateral for the new order:

   `gross_collateral = strike_price * ${STANDARD_OPTION_CONTRACT_SHARES} * new_contracts`

2. Use the broker's current **overnight buying power** as the execution gate.
3. Do not subtract expected premium or fees from gross collateral for this gate.
4. Submit only when:

   `overnight_buying_power >= gross_collateral`

5. If the broker preview or placement returns an overnight-buying-power rejection, do not force, retry, or reinterpret the order as affordable.
6. For portfolio capacity, calculate the conservative total obligation across all open short puts:

   `total_full_strike_obligation = sum(strike_price * ${STANDARD_OPTION_CONTRACT_SHARES} * open_contracts)`

7. Compare total obligation with settled, unencumbered cash. If total obligation exceeds settled cash, label the excess as limited-margin/settlement exposure, even when no margin debit is reported.
8. Working orders reserve capacity until cancellation is broker-verified. Previews do not reserve capacity. Cancelled unfilled orders do not consume capacity after final cancellation verification. Filled short puts continue to consume capacity until closed, expired, or assigned.

### Premium and assignment accounting

1. Expected premium reduces the eventual economic basis; it does not reliably reduce the broker's initial collateral gate.
2. Calculate economic net collateral separately:

   `net_economic_collateral = gross_collateral - gross_premium + estimated_fees`

3. Use net premium for breakeven, effective assignment basis, ROC, and profit calculations only after the gross-collateral gate passes.
4. For a standard equity CSP assigned at the strike, the gross purchase obligation remains:

   `strike_price * ${STANDARD_OPTION_CONTRACT_SHARES} * contracts`

   Premium and fees determine the effective net share basis; they do not eliminate the need to support the assignment obligation.
5. Never describe a CSP as fully cash-secured solely because its premium exceeds a collateral shortfall. Use settled cash, broker collateral, and overnight buying power evidence.

## Communication

The runner supports the configured Telegram destination. Invocation must provide an explicitly configured destination; provider setup and any additional notification policy remain outside the runner.
<!-- BEGIN POLICY VARIABLES -->
### Repository and source variables

| Variable | Value | Unit or meaning |
|---|---:|---|
| `${PYTHON_INDENT_SPACES}` | `4` | spaces |
| `${JAVASCRIPT_INDENT_SPACES}` | `2` | spaces |
| `${SEC_ANNUAL_REPORT_FORM}` | `10-K` | SEC form |
| `${SEC_QUARTERLY_REPORT_FORM}` | `10-Q` | SEC form |
| `${SEC_CURRENT_REPORT_FORM}` | `8-K` | SEC form |
| `${TEXT_ENCODING}` | `UTF-8` | encoding |
| `${NOTIFICATION_DEDUP_HASH_ALGORITHM}` | `SHA-256` | provider-neutral notification deduplication hash algorithm |
| `${NOTIFICATION_STATE_FILE}` | `.state/robinhood_notification_dedup.json` | repository-relative private notification-delivery state file |
| `${NOTIFICATION_UNCONFIGURED_VALUE}` | `UNCONFIGURED` | sentinel meaning that a provider has no explicit destination and must not be used |
| `${MIN_CONFIGURED_NOTIFICATION_PROVIDERS}` | `1` | minimum policy-configured notification providers |
| `${SLACK_NOTIFICATION_PROVIDER_ID}` | `slack` | stable notification-provider identifier |
| `${SLACK_NOTIFICATION_DESTINATION}` | `robinhood-wheel-agent` | exact Slack channel name |
| `${TELEGRAM_NOTIFICATION_PROVIDER_ID}` | `telegram` | stable notification-provider identifier |
| `${TELEGRAM_NOTIFICATION_DESTINATION}` | `UNCONFIGURED` | exact Telegram chat ID, group/channel username, or connector destination; requires user configuration |
| `${TIMESTAMP_STANDARD}` | `ISO 8601` | timestamp format |
| `${FILENAME_TIMESTAMP_FORMAT}` | `yyyy-MM-dd'T'HH-mm-ss±HH-mm` | filename-safe local timestamp pattern that preserves the active numeric UTC offset |
| `${MONTHLY_LOG_MONTH_FORMAT}` | `MMMM` | full local month name in the monthly log filename |
| `${MONTHLY_LOG_YEAR_FORMAT}` | `yyyy` | four-digit local year in the monthly log filename |
| `${SCREENING_EXPORT_DIRECTORY}` | `$HOME/options-wheel-agent/outputs` | repository-relative generated screening-export directory |
| `${SCREENING_EXPORT_FILENAME_PREFIX}` | `Robinhood_Screening_Data_` | screening CSV filename prefix |
| `${NEAR_MISS_REASONING_LOG_DIRECTORY}` | `logs` | repository-relative private final near-miss reasoning-log directory |
| `${NEAR_MISS_REASONING_LOG_FILENAME_PREFIX}` | `final_near_miss_shortlist_reasoning_` | final near-miss reasoning-log filename prefix |
| `${NEAR_MISS_REASONING_FILENAME_TIMESTAMP_FORMAT}` | `yyyy-MM-dd_HH-mm-ss±HH-mm` | filename-safe local date-and-time pattern for final near-miss reasoning logs |

### Scheduling, freshness, and time variables

| Variable | Value | Unit or meaning |
|---|---:|---|
| `${SCHEDULED_RUN_TIME_LOCAL}` | `10:00` | `America/Los_Angeles` local time |
| `${SCHEDULED_RUN_WINDOW_START_LOCAL}` | `06:30:00` | inclusive local time |
| `${SCHEDULED_RUN_WINDOW_END_LOCAL}` | `13:00:00` | inclusive local time |
| `${MAX_SCHEDULED_RUNS_PER_TRADING_DAY}` | `1` | maximum scheduled workflow runs for an NYSE trading date |
| `${QUOTE_MAX_AGE_SECONDS}` | `300` | seconds |
| `${EARLY_PREMIUM_CAPTURE_WINDOW_DAYS}` | `14` | calendar days |
| `${DAYS_PER_YEAR}` | `365` | days |
| `${AVERAGE_VOLUME_LOOKBACK_DAYS}` | `30` | calendar days |
| `${TARGET_OPTION_DTE}` | `30` | calendar days |
| `${MIN_OPTION_DTE}` | `30` | calendar days, inclusive |
| `${MAX_OPTION_DTE}` | `45` | calendar days, inclusive |

### Instrument, account, and portfolio variables

| Variable | Value | Unit or meaning |
|---|---:|---|
| `${STANDARD_OPTION_CONTRACT_SHARES}` | `100` | common shares per standard equity-option contract |
| `${CSP_SCREENING_CASH_THRESHOLD_USD}` | `500` | dollars, exclusive trigger threshold |
| `${ALL_CASH_PERCENT}` | `100%` | all-cash portfolio state |
| `${ZERO_VALUE}` | `0` | generic zero boundary |
| `${RATIO_BASELINE}` | `1` | unit baseline used in growth and ratio formulas |
| `${FULL_UTILIZATION_RATIO}` | `1.00` | maximum collateral utilization |
| `${CONCENTRATION_DISCLOSURE_RATIO}` | `0.50` | exclusive single-ticker disclosure threshold |
| `${SINGLE_TICKER_CONCENTRATION_RATIO}` | `1.00` | complete single-ticker concentration |
| `${ALLOCATION_DOLLAR_ROUNDING_INCREMENT}` | `1` | dollar, half-up rounding |

### Short-option management variables

| Variable | Value | Unit or meaning |
|---|---:|---|
| `${PREMIUM_CAPTURE_ANYTIME_THRESHOLD}` | `0.80` | ratio, exclusive |
| `${PREMIUM_CAPTURE_EARLY_THRESHOLD}` | `0.50` | ratio, exclusive |

### CSP market and technical screening variables

| Variable | Value | Unit or meaning |
|---|---:|---|
| `${MIN_AVERAGE_VOLUME_SHARES}` | `500000` | shares, exclusive |
| `${MIN_MARKET_CAP_USD}` | `2000000000` | dollars, inclusive |
| `${MAX_MARKET_CAP_USD}` | `500000000000` | dollars, inclusive |
| `${MIN_TRAILING_PE}` | `10` | ratio, exclusive |
| `${MIN_ATM_PUT_IV}` | `0.75` | decimal IV, inclusive |
| `${MAX_ATM_PUT_IV}` | `10.0` | decimal IV, inclusive |
| `${MAX_UNDERLYING_PRICE_USD}` | `100` | dollars, exclusive |
| `${RSI_WILDER_PERIOD_SESSIONS}` | `14` | completed sessions |
| `${CSP_MAX_RSI}` | `55` | RSI points, exclusive |
| `${SMA_MEDIUM_PERIOD_SESSIONS}` | `50` | completed sessions |
| `${SMA_LONG_PERIOD_SESSIONS}` | `200` | completed sessions |
| `${BOLLINGER_PERIOD_SESSIONS}` | `20` | completed sessions |
| `${BOLLINGER_STDDEV_MULTIPLIER}` | `2` | population standard deviations |
| `${CSP_MAX_MEDIUM_SMA_DISTANCE}` | `0.10` | ratio, inclusive |
| `${CSP_MAX_LONG_SMA_DISTANCE}` | `0.15` | ratio, inclusive |
| `${CSP_MAX_BOLLINGER_PERCENT_B}` | `0.35` | ratio, inclusive |
| `${RSI_MIN_VALUE}` | `0` | RSI points |
| `${RSI_MAX_VALUE}` | `100` | RSI points |

### CSP contract, return, liquidity, and ranking variables

| Variable | Value | Unit or meaning |
|---|---:|---|
| `${MAX_CSP_CANDIDATES}` | `5` | contracts, with at most one per ticker |
| `${MIN_CSP_SHORT_POP}` | `0.60` | Robinhood short-side probability of profit, inclusive |
| `${MAX_CSP_SHORT_POP}` | `0.70` | Robinhood short-side probability of profit, inclusive |
| `${MIN_CSP_NET_ROC}` | `0.08` | holding-period return ratio, inclusive |
| `${MIN_OPTION_OPEN_INTEREST}` | `100` | contracts, inclusive |
| `${MIN_OPTION_DAILY_VOLUME}` | `20` | contracts, inclusive |
| `${MAX_BID_ASK_SPREAD_RATIO}` | `0.25` | midpoint spread ratio, inclusive |
| `${MAX_CONTRACTS_PER_TICKER_IN_CSP_RANKING}` | `1` | contract |
| `${FINAL_RANK_RUNNER_UP_COUNT}` | `1` | runner-up |

### CSP near-miss variables

| Variable | Value | Unit or meaning |
|---|---:|---|
| `${MAX_CSP_NEAR_MISS_RECOMMENDATIONS}` | `3` | contracts and distinct tickers |
| `${NEAR_MISS_CONTRACT_QUANTITY}` | `1` | contract per standalone alternative |
| `${MAX_NEAR_MISS_CONTRACTS_PER_TICKER}` | `1` | contract |
| `${MIN_NEAR_MISS_REFERENCE_ROC}` | `0.06` | monitoring reference return ratio, inclusive; net when reliable read-only fees are available, otherwise gross and explicitly labeled |

### Ranking stability variables

| Variable | Value | Unit or meaning |
|---|---:|---|
| `${MAX_RANK_REFRESH_ATTEMPTS}` | `3` | maximum quote-refresh-and-rank attempts per ranking phase |

### Covered-call variables

| Variable | Value | Unit or meaning |
|---|---:|---|
| `${MAX_COVERED_CALL_CANDIDATES}` | `3` | contracts per eligible lot |
| `${COVERED_CALL_CONTRACTS_PER_LOT}` | `1` | contract per eligible assigned lot |
| `${COVERED_CALL_RULE_VERSION}` | `CC-HSR-V2` | calibration cohort rule-version identifier |
| `${COVERED_CALL_MIN_RSI}` | `60` | RSI points, exclusive |
| `${COVERED_CALL_MAX_RSI}` | `70` | RSI points, inclusive |
| `${COVERED_CALL_MIN_BOLLINGER_PERCENT_B}` | `0.65` | ratio, inclusive |
| `${COVERED_CALL_MAX_BOLLINGER_PERCENT_B}` | `1.00` | ratio, inclusive |
| `${COVERED_CALL_MOMENTUM_LOOKBACK_SESSIONS}` | `1` | completed-session timing comparison |
| `${MAX_COVERED_CALL_ABS_DELTA}` | `0.25` | absolute call delta, inclusive |
| `${COVERED_CALL_ROC_NORMALIZATION_DAYS}` | `30` | calendar-day normalization period |
| `${MIN_COVERED_CALL_NORMALIZED_NET_ROC}` | `0.01` | normalized net return ratio, inclusive |
| `${COVERED_CALL_CALIBRATION_MIN_OBSERVATIONS}` | `100` | minimum comparable broker-verified terminal observations |
| `${COVERED_CALL_CALIBRATION_CONFIDENCE_LEVEL}` | `0.95` | two-sided Wilson confidence level |
| `${COVERED_CALL_CALIBRATION_Z_SCORE}` | `1.959963984540054` | standard-normal critical value for the calibration confidence level |
| `${TARGET_COVERED_CALL_WORTHLESS_RATE}` | `0.70` | empirical worthless-expiration target, exclusive lower-bound test |

### Order pricing and calculation variables

| Variable | Value | Unit or meaning |
|---|---:|---|
| `${DEFAULT_PRICING_FRACTION}` | `0.50` | spread fraction |
| `${MIN_PRICING_FRACTION}` | `0.35` | spread fraction, inclusive |
| `${MAX_PRICING_FRACTION}` | `0.60` | spread fraction, inclusive |
| `${MIDPOINT_DIVISOR}` | `2` | arithmetic midpoint divisor |

<!-- END POLICY VARIABLES -->


<!-- END OF Trading-account-ORDER-LIFECYCLE SKILL.md -->
