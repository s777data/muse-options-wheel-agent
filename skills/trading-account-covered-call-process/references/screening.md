### Covered-call screening phase

Use the following criteria for each current `${STANDARD_OPTION_CONTRACT_SHARES}`-share lot before proposing a covered call. Use the same current-price, completed-session daily-bar, RSI, SMA, and Bollinger calculation definitions specified in CSP Screening. For the current trend-quality test, calculate Bollinger `%B` with the fresh live current price and bands based on the most recently completed sessions; calculate RSI and both SMAs only from completed-session closes. For momentum deceleration, calculate both the latest and comparison values as completed-session-end indicators, without a live-price input.

All of the following trend-quality conditions must pass:

- `current price >= SMA ${SMA_MEDIUM_PERIOD_SESSIONS}`
- `RSI(${RSI_WILDER_PERIOD_SESSIONS}) > ${COVERED_CALL_MIN_RSI}`
- Bollinger `%B >= ${COVERED_CALL_MIN_BOLLINGER_PERCENT_B}`

At least one of the following momentum-deceleration conditions must also pass:

- latest completed-session `RSI(${RSI_WILDER_PERIOD_SESSIONS}) <= RSI(${RSI_WILDER_PERIOD_SESSIONS})` calculated as of `${COVERED_CALL_MOMENTUM_LOOKBACK_SESSIONS}` completed session earlier
- latest completed-session Bollinger `%B <=` Bollinger `%B` calculated as of `${COVERED_CALL_MOMENTUM_LOOKBACK_SESSIONS}` completed session earlier

For the comparison values, recalculate the complete indicator using adjusted regular-session closes available through the comparison session; do not compare the current indicator with an intraday approximation. These technical conditions are underlying-quality and entry-timing controls only. Never claim that RSI, SMA, Bollinger Bands, or momentum deceleration independently establishes the probability that a call will expire out-of-the-money. If any required current or comparison indicator is unavailable, either SMA is `<= ${ZERO_VALUE}`, or either Bollinger band width is `${ZERO_VALUE}`, record `NO TRADE` for that lot.
