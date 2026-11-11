# Deviations from a pure verbatim copy

Every deviation from "copy the source at 0b0d64a unchanged" is listed
here. Nothing else differs intentionally.

- **D-00 (RESOLVED in session 2, 2026-11-09):** ~~Phases 0–4, 6–8 module
  bodies and the Phases 9–11 `order_lifecycle.py` are **not yet copied**~~
  — BIG SANDY added `options-wheel-agent` to the GitHub App
  installation; all 26 needed source files were fetched at the pinned
  commit and verified by git blob SHA-1 (see INVENTORY.md). The
  verbatim copies are now in place with only the deviations below.
  The session-1 fail-closed `SourceCopyPending` placeholders are gone.
- **D-01 (Phase 0, PRD gap 10):** `full_wheel.py` scaffold adds a run
  manifest (`00_run_manifest.json`: run_id, started_at, workflow
  version, schedule window) and a duplicate-run guard (one successful
  run per window unless `force_rerun=True`).
- **D-02 (Phases order, PRD gap 1):** Orchestrator order is PRD order —
  snapshot → profit taking → CSP → covered calls. The source
  `full_wheel.py` runs CSP first.
- **D-03 (PRD gap 3):** A required-phase failure sets the workflow
  `FAILED` and stops; the source catches phase exceptions and continues.
  Final status is PASS/FAILED (or AWAITING_USER_ACTION), never PARTIAL.
- **D-04 (PRD gap 8):** Telegram dedup uses the
  `_notification_hash` / load / write state pattern from the source's
  `order_lifecycle.py`. No Telegram credentials exist in this repo:
  `send_telegram()` persists the payload into the run artifacts, records
  the hash, and returns `telegram_not_configured` — it never sends and
  never invents a token.
- **D-05 (Phase 5, by BIG SANDY's instruction — the one real build):**
  `scripts/valuation_scorer.py` is a new adapter, not a copy. It keeps
  the source's public signatures and batch-envelope shape, but calls the
  dashboard engine in-process instead of shelling to the placeholder
  `company_valuation_score` CLI, whose code is **not copied at all**.
  The composite `score` in `build_phase5_valuation_rows()` is
  **Final-Code** = `round(0.5·Business·10 + 0.25·Value·10 + 0.25·GAV-Code, 1)`,
  replacing the placeholder `final_score`. Ordering unchanged:
  Final-Code desc, ticker asc; unscored rows retained, excluded from
  Phase 6. Contract: `contract_version 1.0-wheel.1`,
  `methodology_version dashboard-v5.3+gav-code` (resolves PRD gap 6
  drift by naming the actual engine). `fresh_retry` never triggers a
  `--no-cache` retry (PRD gap 5). Tickers missing from the vendored
  engine config get an in-memory config entry with a CIK resolved from
  EDGAR `company_tickers.json` and no beta (engine computes beta on the
  fly); vendored files are never modified on disk.
- **D-06 (Transport):** One shim, `scripts/robinhood_transport.py`,
  provides the source's expected binding names
  (`call_robinhood_tool` / `_robinhood_tool` / `call_mcp`) over the local
  read-only client.py. Write tools raise `WriteToolDisabled`; tools the
  server offers but the local client doesn't allowlist raise
  `ToolNotAllowlisted` (see INVENTORY.md parity gaps — notably
  `create_scan`/`run_scan`, `get_trade_approvals`, `get_equity_tax_lots`).
  Session 2: the shim gained `hermes_envelope()`, which returns the
  Hermes envelope `{"result": "<json>"}` the copied modules parse,
  with transport-level normalization only (wrap structuredContent as
  `{data: ...}` if needed; alias a missing `results` list from the
  endpoint's named list, e.g. get_accounts' `accounts`). In
  `csp_screening.py` the Hermes-runtime block (hermes_cli registry,
  ~175 lines) is replaced by this binding block; `initialize_mcp_runtime`
  / `shutdown_mcp_runtime` are no-ops there. In `profit_taking.py` /
  `covered_calls.py` the `hermes_tools` import blocks are replaced by
  equivalent `_bind()` bindings. Everything else is verbatim.
- **D-07 (Phase 8, PRD gap 7 — APPLIED in session 2):**
  `covered_calls.py` is the verbatim source PLUS: a
  `_committed_call_shares(account)` helper that sums, per symbol,
  shares committed to (a) existing short call positions
  (`get_option_positions(nonzero=true)`, type resolved per instrument
  via `get_option_instruments`) and (b) open/pending sell-to-open call
  orders (`get_option_orders`, states queued/confirmed/unconfirmed/
  partially_filled/pending, legs with position_effect open + side
  sell). In the evaluation loop the source line
  `contracts_available = int(quantity // 100)` becomes
  `available = quantity - committed[symbol]` (skip if < 100) then
  `contracts_available = int(available // 100)`, and each
  recommendation gains `shares_committed_to_short_calls`. The helper
  is fail-closed: any broker read failure propagates. Two additional
  bindings (`get_option_positions`, `get_option_orders`) were added to
  the module's transport block for this. Observed source quirk kept
  verbatim: the RSI branch uses `timedelta` without importing it
  (NameError is caught by its own try/except, defaulting rsi_ok=True).
- **D-08 (Phases 9–11 — applied to the verbatim copy in session 2):**
  `order_lifecycle.py` is the source file with ONLY a guard block
  added after the imports (plus three one-line `_guard(...)` calls and
  the disabled error added to main()'s except tuple): placement/
  cancel/replace raise `OrderLifecycleDisabled` unless env
  `WHEEL_ORDER_LIFECYCLE_ENABLE=1` **and** the flag file named by
  `WHEEL_ORDER_LIFECYCLE_FLAG` exists (both user-controlled, default
  absent). Defense in depth on top of the client's write block.
  Note: the source's `call_mcp()` shells to
  `skills/company_valuation_score/scripts/mcp_bridge.py` (BRIDGE path),
  which is NOT copied in this repo (Phase 5 exception) — unreachable
  while the lifecycle is disabled, and `status()`/`call_mcp` are not
  wired to any caller here.
- **D-09 (Vendoring):** The dashboard engine is vendored as a pinned
  copy (`vendor/muse-business-evaluation-dashboard/`, commit ac8deaf,
  src/evaluator + config only) instead of a git submodule, because the
  GitHub snapshot-push route cannot carry submodules. See VENDOR.md.
- **D-10 (Local paths, session 2):** The source hardcodes Hermes
  profile paths (`/home/s777data/.hermes/profiles/options-wheel-agent`,
  `/home/s777data/outputs/...`). In the copies these become repo-local:
  `sys.path` inserts use the repo root; CSP artifacts default to
  `outputs/robinhood-wheel-agent/<RUN_ID>/` (override `WHEEL_OUTPUT_DIR`),
  caches to `outputs/cache/` (override `WHEEL_CACHE_DIR`). No other
  path semantics changed.
- **D-11 (Artifact hygiene, session 2):** The orchestrator's manifest
  stores only the Phase 1 artifact payload (masked account `••••6392`),
  not the runtime context tuple element that carries the full account
  number (PRD §5 rule 10).
- **D-12 (Environment hygiene, session 2):** `csp_screening.py` drops
  bracketed-IPv6 entries (`[::1]` form) from `no_proxy`/`NO_PROXY` at
  import, because httpx 0.28's URLPattern parser rejects them when the
  module-level SEC client is built (this sandbox exports them; the same
  hosts are present unbracketed, so bypass semantics are unchanged).
- **D-13 (Phase 2 live-data fix, applied 2026-11-09 with BIG SANDY's
  approval):** the live Robinhood MCP returns option-position
  `quantity` as a string; the source profit-taking filter
  `pos.get("quantity", 0) > 0` raised TypeError against it (session 2
  dry run). One line changed in `scripts/profit_taking.py`:
  `float(pos.get("quantity", 0) or 0) > 0`. Nothing else touched.
  Also observed: the source's `full_wheel.py` orchestrator was not
  used at all — the session-1 scaffold (D-01..D-04) drives the copied
  phase bodies, and phase `telegram_message`s are delivered through
  the scaffold's deduped `send_telegram()`.
- **D-14 (Phase 2 live-shape patch, applied 2026-11-09 with BIG SANDY's approval; was OBSERVED-2 in session 3):** live
  `get_option_quotes` results nest the quote under a `quote` key
  (`{"quote": {instrument_id, bid_price, ...}, "close": {...}}`);
  the source builds `quote_map[q["instrument_id"]]` from flat
  objects → KeyError. Downstream, `quantity = pos.get("quantity", 0)`
  (string) feeds float P&L arithmetic — same class as D-13.
  Proposed 3-line patch in `scripts/profit_taking.py` (unwrap
  `q.get("quote", q)`; float-coerce quantity), pending approval.
- **D-15 (Transport allowlist alignment, 2026-11-09, BIG SANDY
  approved):** the local Robinhood client's ALLOWED_TOOLS gained 5
  non-order tools (create_scan, run_scan, get_equity_tax_lots,
  get_trade_approvals, review_option_order). The shim mirrors this:
  the two reads are in READ_TOOLS; create_scan/run_scan/
  review_option_order sit in NON_ORDER_ACTION_TOOLS (scanner runs and
  order previews only — no placement surface). All order write tools
  remain in WRITE_TOOLS and raise WriteToolDisabled (re-verified).
- **D-16 (Dry-run scan-cap override, 2026-11-09, BIG SANDY
  requested):** `run_account_preflight` honors env
  `WHEEL_SCAN_PRICE_CAP_USD`: when set, the scanner price cap uses it
  instead of buying_power/100, rehearsing a funded account in dry
  runs. The Phase 1 artifact records both `broker_derived_cap_usd`
  and `scan_cap_override_usd`. Collateral capacity checks still use
  real buying power and order placement remains hard-disabled, so
  the override widens analysis scope only. Unset = original behavior.
- **D-17 (DECLINED by BIG SANDY 2026-11-09 — 'ignore GENB'; not applied):** adapter exception path should
  emit the structured insufficient_data envelope instead of
  `result: null` when the dashboard engine raises before producing
  a result (e.g. GENB: no 10-K on EDGAR). Today the source's merge
  step hard-fails the whole run on a null result; with the envelope,
  such tickers are excluded per-ticker like KEEL/BRK.B.
- **D-18 (Engine update, 2026-11-09, BIG SANDY approved):** vendored
  dashboard engine advanced past ac8deaf with three generic fixes,
  built in the dashboard working copy and synced here (diff-verified;
  suite 107 -> 123, all passing; MU/NVDA/V/CRWD/BRK.B controls
  byte-identical). Fix A: annual revenue may gap-fill individual
  periods from a SUPERSEDED candidate tag only (never splices
  coexisting live tags — a BRK.B-shaped regression test locks this).
  Fix B: GAV-Code growth fallback chain — ttm_bridge ->
  quarterly_sum -> fy_annual — with `growth_source` provenance on
  every score. Fix C-1: a filed revenue of exactly 0 no longer
  suppresses price-only value legs (TNGX). Fix C-2: basic-shares
  fallback when no diluted series exists and the latest annual net
  income is negative (ASC 260; URGN). Result: 11 of the 15 previously
  unscored cohort names now score on EDGAR data alone; GENB/AADX/
  QNT/SECZ remain fail-closed (no 10-K / shell), as designed.
- **D-19 (Adapter yfinance GAV fallback, 2026-11-09, BIG SANDY
  approved):** when a ticker has Business+Value but GAV-Code is None
  solely for ttm_revenue_growth_missing (after the D-18 EDGAR chain),
  valuation_scorer may fill the growth band from yfinance annual
  revenue, stamped `gav_code.source = yfinance_annual_fallback` and
  `result.fallback = gav_growth_yfinance_annual`; rows carry
  `gav_code_source` and `fallback`. Implemented and unit-verified;
  did not fire on the current cohort (no stragglers remained).
- **D-20 (CSP buying-power gate, 2026-11-11, BIG SANDY's rule):**
  the orchestrator (`scripts/full_wheel.py`) does not run the CSP
  screening block (Phases 3-7, `run_csp_screening`) when effective
  buying power is below $700 — below that, no realistic cash-secured
  put can be funded and the scan/gates/valuation/chain work is wasted.
  Effective buying power = the Phase 1 artifact's unleveraged buying
  power, or, when the D-16 dry-run scan-cap override is set, the
  override-implied premise (cap x 100), so $40-cap rehearsals still
  run the block. The skip is a manifest entry with status "skipped"
  and reason `buying_power_below_csp_minimum` (with the BP numbers),
  never a silent omission and never a failure. Phases 1, 2 and 8 are
  unaffected — they manage existing positions, not new exposure.
  Threshold is a config value: module constant
  `MIN_CSP_BUYING_POWER_USD`, env-overridable via
  `WHEEL_MIN_CSP_BUYING_POWER_USD` (default 700). First live
  verification 2026-11-11: BP $206.25 -> block skipped in <1s, run
  proceeded to Phase 8.
- **D-21 (Phase 2 profit-taking made decision-grade, 2026-11-11,
  BIG SANDY's instruction — "that's a bug, fix it"):**
  `scripts/profit_taking.py` no longer emits the placeholder
  `CALCULATE` signal. Changes, all in that file: (1) two new transport
  bindings (`get_option_instruments`, `get_equity_quotes`); (2) a
  `_resolve_option_instrument()` helper that resolves a position's
  strike and put/call type by chain_id + expiration_dates + type
  (the Phase 6 call shape, paginated, matching on option_id) —
  positions carry expiration/opened_at but no strike, and the MCP
  errors on instruments-by-ids; (3) the main loop now computes the
  real sqrt-boundary signal via the source's own
  `profit_taking_signal()`: days from the position's `opened_at` to
  its expiration, underlying from a live equity quote; (4) units fix —
  `average_price` is signed per-contract dollars (e.g. -396.6667 ==
  $3.9667/share), so premium/share = abs(average_price) /
  trade_value_multiplier and unrealized P&L uses the per-share
  premium (the source's raw arithmetic produced nonsense like
  -$121,565). Signal rows and the telegram message now carry strike,
  underlying, trigger, boundary, and day counts; a position whose
  instrument cannot be resolved stays `CALCULATE` with an explicit
  `signal_error` (fail-closed per position). Verified live
  2026-11-11: FIGR Dec 18 $20P x2 -> TAKE (premium $1.95, mid $1.00,
  P&L +$190, trigger $19.27 vs underlying $24.10, day 6/43); FIGR
  Nov 20 $32.50P x3 -> HOLD (premium $3.97, mid $8.55, P&L -$1,375,
  trigger $28.86, day 48/57). Note: the same chain-based resolution
  is the technique the pending Phase 8 fix needs.
- **D-22 (Phase 2 signal aligned to the profit-taking-decider skill
  spec, 2026-11-11, BIG SANDY caught the discrepancy):** the copied
  `profit_taking_signal()` claimed "per skill spec" but implemented an
  underlying-price trigger (TAKE when the underlying crosses
  strike -/+ premium x sqrt(elapsed/total)) and never computed profit
  percent — so it emitted TAKE for the FIGR Dec 18 $20P at only 48.7%
  captured. The skill spec is explicit and strict: TAKE only when
  `profit_percent > 50.00` (fixed minimum premium-capture gate, "not
  permitted below this value") AND `profit_percent >
  100 * sqrt(elapsed_fraction)`; equality at either boundary is HOLD.
  D-22 rewrites the signal to the spec: the caller now passes the
  current mark, profit percent is computed from premium vs mark, both
  strict gates are applied, and the result/rows/telegram carry
  `profit_percent`, `required_profit_percent` and
  `min_profit_capture_percent`. `boundary`/`trigger_price` are kept as
  informational fields only and no longer drive the signal. Unit
  checks: live Dec 18 numbers (48.72% vs 37.35% required) -> HOLD;
  mark $0.97 (50.26%) -> TAKE; exactly 50.00% -> HOLD; 60% captured
  but 96.45% required late in life -> HOLD; missing mark -> HOLD with
  error. Supersedes the signal semantics D-21 verified (D-21's data
  plumbing — chain-resolved strike/type, real dates, unit fixes — is
  unchanged and still correct).
