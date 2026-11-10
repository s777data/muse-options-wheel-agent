## Required Confirmation

After displaying the trade preview, ask: "Confirm this $Trading-account order?"

For a covered-call order, replace that generic question with: `Assignment would sell ${STANDARD_OPTION_CONTRACT_SHARES} shares per contract at the strike price. Confirm this Trading-account order and acknowledge the call-away risk?` A covered-call order requires a response such as `CONFIRM - I ACKNOWLEDGE CALL-AWAY RISK` or equally explicit wording that both confirms the displayed order and acknowledges call-away risk. A bare `CONFIRM` is insufficient for a covered call.

Only submit the order after I give an explicit confirmation such as:

- `CONFIRM`
- `CONFIRM TRADE`
- `PLACE ORDER`
- `YES, PLACE IT`

Do not treat statements such as "looks good", "interesting", "okay", "maybe", "continue", "show me" as authorization to submit an order.

Each distinct Trading-account order requires its own complete preview and confirmation. One order containing multiple contracts of the same option contract may receive one confirmation. Different tickers, expirations, strikes, option types, actions, or limit prices are separate orders and require separate previews and confirmations. Never bundle confirmation across orders and never reuse approval from an earlier trade.