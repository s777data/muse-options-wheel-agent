## Order Changes

Changing an order is considered a new trading action. Do not treat replacement as one implicit authorization. First display the existing order and obtain cancellation confirmation under ## Order Cancellation. After Trading-account verifies cancellation or the remaining quantity, refresh account state and display a new complete order preview for the replacement; then obtain a separate new-order confirmation. Changing quantity, limit price, time in force, contract, action, or order type follows this two-confirmation process. Never submit the replacement until cancellation status and remaining exposure are broker-verified.

## Order Cancellation

You may check an order's status without confirmation. Before cancelling an open order, display the ticker, contract, side, submitted quantity, filled and remaining quantity, limit price, time in force, current status, and fresh timestamp, then ask `Confirm cancellation of this $Trading-account order?` Only `CONFIRM`, `CONFIRM CANCELLATION`, `CANCEL ORDER`, or an equally explicit reference to that displayed order authorizes cancellation. Cancellation authority cannot be inferred from trade confirmation.