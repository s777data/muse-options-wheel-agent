# Step 0 Inventory — Options Wheel Agent

Source baseline (per PRD): `s777data/options-wheel-agent` @
`0b0d64a7e2f90e41ffa406acac66bf42045f1c75` — **READ-ONLY. Session 1
could not access it (not in the GitHub App installation); BIG SANDY
fixed the installation and session 2 (2026-11-09) fetched everything
below via the connector.** Every fetched file was verified by
recomputing its git blob SHA-1 against the contents-API listing —
26/26 OK:

| Source path | Bytes | Blob SHA-1 |
|---|---|---|
| `scripts/full_wheel.py` | 4,961 | 1cd2946551d1f7d9fefe2e730ced071771561f53 |
| `scripts/csp_screening.py` | 111,618 | 88961458f4a5a177d1053070b5cd43dd8cfb24a8 |
| `scripts/profit_taking.py` | 6,997 | 645a10b7488d45859da7dcdaf2f52bb87e5692d6 |
| `scripts/covered_calls.py` | 9,300 | 2ef8fa16dddf8f3f506fb11e8eda0a5013eeb65f |
| `scripts/valuation_scorer.py` | 9,826 | c2d7639a9945ca576a60deeba6de58a0280e9d31 |
| `skills/trading/options-wheel-agent/SKILL.md` | 5,026 | 44bf07f298a3bc754c874916f53c1d4a751ec6c8 |
| `skills/trading-account-session-management/SKILL.md` | 14,839 | e90a0b181d1a9d57c1dd317dde55d4117b0b9108 |
| `skills/trading-account-session-management/references/csp-candidate-evaluation.md` | 3,749 | a29dacdd7a069986ebb8b8e581690aeeee86c548 |
| `skills/profit-taking-decider/SKILL.md` | 8,444 | 0cea5cce646699c13dbeb5abee6e2c2f37af38be |
| `skills/trading-account-csp-process/SKILL.md` | 11,551 | 45abf08fde09848fca69e73a991ef1f56d55e1b5 |
| `skills/trading-account-covered-call-process/SKILL.md` | 1,235 | 5bcffba5a9cf0c04061c55e9528cc80d4e6a1da3 |
| `skills/trading-account-covered-call-process/references/lot-eligibility-and-basis.md` | 3,032 | 5ef22fdf6ce69e2553f1f5ad2c4e0e5628f08bfd |
| `skills/trading-account-covered-call-process/references/screening.md` | 1,933 | e239498bd18398cb1788ecd50ed0a602269346f1 |
| `skills/trading-account-covered-call-process/references/selection-and-risk.md` | 9,429 | 27dc847ecf4b5689f7b32f8d28811c1ae46b2c60 |
| `skills/trading-account-order-lifecycle/SKILL.md` | 14,555 | 27e816d0359fe616b3c66be2bc52818b8fafa8c9 |
| `skills/trading-account-order-lifecycle/references/confirmation.md` | 1,279 | 35d6bb1a2ab90daa62004a56079a5cab05c40d60 |
| `skills/trading-account-order-lifecycle/references/execution-and-reconciliation.md` | 6,750 | bce97fa108eeceeb5d356dc5d22f887b9ac77ff0 |
| `skills/trading-account-order-lifecycle/references/order-changes-and-cancellation.md` | 1,177 | f66f24209042d2065ccbad3b084faa504eac21fd |
| `skills/trading-account-order-lifecycle/references/preview.md` | 6,363 | 9131e2859d33f2927bf1ba4719717695978a9ec5 |
| `skills/trading-account-order-lifecycle/references/reporting-style.md` | 1,567 | d141369ded01075abd878ccd3c98408e582a2485 |
| `skills/trading-account-order-lifecycle/scripts/order_lifecycle.py` | 15,456 | a118ecf9e7d4a58c3e1130d64cc30027bb810baa |
| `skills/trading-account-order-lifecycle/scripts/build_social_media_input.py` | 12,304 | 7db999fdb9ec74f22d55192b1d17958e93c99668 |
| `skills/trading-account-order-lifecycle/scripts/social_media_telegram_message.py` | 10,154 | 3c3d7e61d9670bde020240410b263cfb071e96e8 |
| `skills/trading-account-order-lifecycle/scripts/test_build_social_media_input.py` | 4,936 | 1d6b26cf4be4e7e166a3cf8d0ceed20f79d24c86 |
| `skills/trading-account-order-lifecycle/scripts/test_order_lifecycle.py` | 7,098 | 4a21dc56ee9eecdb14229e322d8c429f7857b74c |
| `skills/trading-account-order-lifecycle/scripts/test_social_media_telegram_message.py` | 7,007 | ecc9b681da53b5403a0cf728355b5584a989a209 |

Not copied, by decision (recorded): `skills/company_valuation_score/`
entirely — engine code per the Phase 5 exception, and its SKILL.md
(103,063 bytes, blob bdf6ba9e88ef3c73c04eb11d28ea64ce797a3b83) is the
placeholder engine's spec; copying it risks it being mistaken for the
live methodology, which is `dashboard-v5.3+gav-code` here. Also not
copied: source root files unrelated to the PRD phases (AGENTS.md,
SOUL.md, config.yaml, .gitignore) and unrelated skill folders
(devops, mag-50-recommendation, .curator_*).

## Source files named in the PRD

| Source path | Key functions (per PRD) | Target phase |
|---|---|---|
| `scripts/full_wheel.py` | `run_full_wheel()`, `send_telegram()` | 0, 7, 11 |
| `scripts/csp_screening.py` | `run_account_preflight()`, `parse_mcp_payload()`, `finite_nonnegative_number()`, `is_eligible_csp_account()`, `calculate_cash_secured_capacity()`, `run_single_scan()`, `make_filters()`, `run_partition()`, `fetch_fundamentals()`, `_yf_revenue_frame()`, `_yf_revenue_series()`, `_yf_country()`, `compute_yf_annual_yoy()`, `compute_yf_quarterly_qoq()`, `yfinance_annual_yoy_gate()`, `get_company_facts()`, `compute_annual_yoy()`, `compute_quarterly_qoq()`, `fetch_technicals()`, `_latest_indicator()`, `build_technical_conditions()`, `evaluate_post_screen()`, `select_phase5_tickers()`, `prepare_valuation_mcp_adapter()`, `build_phase5_valuation_rows()`, `qualify_options_hardened()`, `_qualify_options_window_hardened()`, `_fetch_option_instruments_complete()`, `_fetch_option_quotes_reconciled()`, `_fetch_underlying_quotes_reconciled()`, `_parse_utc_timestamp()`, `_quote_age_seconds()`, `build_phase7_ranking()`, `build_recommendation_telegram_message()`, `persist_phase_json()`, `run_csp_screening()`, transport: `call_robinhood_tool()` / `_robinhood_tool()` | 1, 3, 4, 5 (adapter prep/rows), 6, 7 |
| `scripts/profit_taking.py` | `run_profit_taking_check()`, `profit_taking_signal()`, `linear_boundary()` | 2 |
| `scripts/covered_calls.py` | `run_covered_call_evaluation()` | 8 |
| `scripts/valuation_scorer.py` | `run_sequential_valuation()`, `run_one_ticker()`, `_cli_command()`, `_extract_cli_json()` | 5 — **REPLACED by dashboard adapter (this repo); signatures preserved** |
| `skills/trading-account-order-lifecycle/scripts/order_lifecycle.py` | `run()`, `place()`, `status()`, `order_row()`, `cancel()`, `replacement_price()`, `_round_toward()`, `call_mcp()`, `send_telegram()`, `notify_placed()`, `notify_fill()`, `notify_status()`, `setup_cron()`, `_notification_hash()`, `_load_notification_state()`, `_write_notification_state()` | 9, 10, 11 — copied verbatim + hard-disable guard (D-08) |
| `skills/company_valuation_score/` (`cli.py`, `contract.py`, SKILL.md) | `company_valuation_score.cli.main()`, `build_engine()`, `validate_response()` | 5 placeholder engine — **NOT copied, by instruction** |
| `skills/**/SKILL.md` (7 skill docs) | normative workflow text | copied with source when access is unblocked |

## Phase 5 composite formula in the source (extracted exactly, session 2)

From the source `build_phase5_valuation_rows()` (blob 88961458…): the
row composite `score` is confidence-weighted over the placeholder
engine's `final_score` (as valuation_score) and `business_quality.score`:

- confidence `"medium"`: `round(0.30 * valuation + 0.70 * business_quality, 2)`
- confidence `"high"`: `round(0.40 * valuation + 0.60 * business_quality, 2)`
- confidence low/unavailable: `None` (row unscored, excluded from Phase 6)

Ordering: score desc, ticker asc, unscored last. The source
`valuation_scorer.run_sequential_valuation(..., fresh_retry=True)`
retries tickers in `FRESH_RETRY_STATUSES` once with `--no-cache`
appended to the CLI command — the PRD §11 gap-5 behavior. **In this
repo the composite is replaced by Final-Code** (DEVIATIONS.md D-05)
and no-cache is never forced.

## Robinhood MCP tool parity

Server catalogue (live `client.py list-tools`, 2026-11-09): **91 tools**,
including every tool the PRD names: `get_accounts`, `get_portfolio`,
`get_equity_positions`, `get_option_positions`, `get_crypto_positions`,
`get_equity_orders`, `get_option_orders`, `get_crypto_orders`,
`get_trade_approvals`, `create_scan`, `run_scan`,
`get_equity_fundamentals`, `get_equity_technical_indicators`,
`get_option_chains`, `get_option_instruments`, `get_option_quotes`,
`get_equity_quotes`, `get_equity_tax_lots`, `review_option_order`,
`place_option_order`, `cancel_option_order`.

Local client gate (`~/workspace/custom-connectors/custom.robinhood/bin/client.py`
`ALLOWED_TOOLS`): only **15** tools are callable —
`get_accounts`, `get_portfolio`, `get_equity_positions`,
`get_option_positions`, `get_equity_orders`, `get_option_orders`,
`get_equity_quotes`, `get_equity_fundamentals`, `get_equity_historicals`,
`get_option_chains`, `get_option_instruments`, `get_option_quotes`,
`get_equity_technical_indicators`, `get_earnings_calendar`,
`get_earnings_results`.

**Parity gaps (server has it, local client blocks it):**

| Needed by phase | Tool | Effect |
|---|---|---|
| 1 | `get_crypto_positions`, `get_crypto_orders`, `get_trade_approvals` | Snapshot incomplete → Phase 1 must fail closed on approvals/crypto sections |
| 4A | `create_scan`, `run_scan` (+ scanner helpers) | **CSP scanner cannot run through this client at all** — Phase 4A blocked until the client allowlist is extended (a connector change BIG SANDY must approve) |
| 8 | `get_equity_tax_lots` | Covered-call lot/basis data unavailable → Phase 8 blocked through this client |
| 9 | `review_option_order` | Review step blocked (also moot while lifecycle is disabled) |
| 10 | `place_option_order`, `cancel_option_order` | Write tools — blocked by design; lifecycle hard-disabled in this repo as well |

The transport shim (`scripts/robinhood_transport.py`) enforces the same
15-tool read allowlist and raises `WriteToolDisabled` for any write tool,
so these gaps surface as explicit errors, never silent fallbacks.

**Update 2026-11-11 (D-15 / D-24 / D-25) — the section above predates
these changes:** the client allowlist was extended with BIG SANDY's
approval in stages: D-15 (2026-11-09) added `create_scan`, `run_scan`,
`get_equity_tax_lots`, `get_trade_approvals`, `review_option_order`
(so the Phase 4A scanner, Phase 8 tax lots, and Phase 9 review all
run); D-25 (2026-11-11, Stage 2 of the order-placement enablement
plan) added exactly `place_option_order` + `cancel_option_order`.
The transport now has three categories — READ_TOOLS,
NON_ORDER_ACTION_TOOLS, and ORDER_TOOLS (D-25, place/cancel only);
every other write tool still raises `WriteToolDisabled`. The order
lifecycle was re-plumbed onto this transport in D-24 (no more dead
bridge; notifications recorded for Muse chat instead of Telegram),
and placement remains gated by its dual-control guard
(`WHEEL_ORDER_LIFECYCLE_ENABLE=1` + user-controlled flag file) plus
BIG SANDY's per-order confirmation of the exact reviewed payload.
First pilot order placed and filled 2026-11-11 (D-25).
