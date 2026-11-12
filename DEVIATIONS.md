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
- **D-17 (DECLINED by BIG SANDY 2026-11-09 — 'ignore GENB'; REVIVED and
  BUILT 2026-11-11 at his instruction after LEGN killed a live run):**
  adapter exception path should emit the structured insufficient_data
  envelope instead of `result: null` when the dashboard engine raises
  before producing a result (e.g. GENB: no 10-K on EDGAR). Today the
  source's merge step hard-fails the whole run on a null result; with
  the envelope, such tickers are excluded per-ticker like KEEL/BRK.B.
  AS BUILT (2026-11-11): the envelope is realized at the merge —
  `build_phase5_valuation_rows` (scripts/csp_screening.py) carries a
  terminal `insufficient_data` record with a missing result as a
  structured unscored row (identical schema, all score fields None,
  engine failure_reason preserved, Phase 4 price/market-cap fields
  kept, sorts last, counted in summary status_counts/unscored_count).
  Any other terminal status with a missing result (error, timeout,
  unknown) still raises — retrieval failures stay fail-closed. Trigger:
  live run 20261111T183812Z-05fa98b4 failed at the Phase 5 merge on
  LEGN (Legend Biotech, CIK 0001801198), a foreign private issuer that
  files Form 20-F, not 10-K — the same no-10-K class as GENB. Tests:
  tests/test_phase5_d17.py 15/15 (mixed cohort reconciles 1:1, LEGN
  row unscored with reason preserved, error/timeout/unknown still
  raise, scored passthrough); live controls MU 68.8 / NVDA 65.8 /
  V 52.6 / BRK.B unscored unchanged.
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

### D-23 — Phase 8 (covered calls) live-shape repair, five fixes (2026-11-11)

Audit at BIG SANDY's request found Phase 8 could never produce a real
recommendation against the live MCP shapes. All five fixed in
scripts/covered_calls.py; no criteria or thresholds changed.

1. **Instrument type by `ids` (fatal).** `_instrument_type` used
   `get_option_instruments(ids=[...])`, which the MCP rejects (JSON-RPC
   error) — this failed every full run in the D-07 pre-check. Now
   chain-scoped: `chain_symbol` + `expiration_dates`, matched on
   instrument id locally (same technique as D-21), fail-closed raise on
   an unresolvable type, per D-07's read-failure rule.
2. **Chains shape.** Live chains arrive under `data.chains`, each with
   an `expiration_dates` list and `trade_value_multiplier` as the string
   `"100.0000"`. Code expected `data.results`, a single
   `expiration_date`, and int `100`. Now reads `data.chains`, iterates
   the expiration list with the 30–45 DTE filter per date, and compares
   the multiplier as a float.
3. **Tax lots shape.** Live lots arrive under `data.tax_lots`; code read
   `data.results` OUTSIDE the per-symbol try, so any account holding
   >=100 shares would have crashed the phase. Now reads `data.tax_lots`
   (falling back to `_payload_results`), with string-safe quantity/cost
   coercion and a total-`cost_basis` fallback.
4. **Quote nesting.** Live quotes nest under `"quote"` (D-14 shape);
   code read flat `bid_price`/`ask_price` -> zeros -> the `bid > 0` gate
   rejected every contract. Now unwraps `row["quote"]` before reading
   bid/ask/delta, with a guarded delta coercion.
5. **Dead RSI gate.** `timedelta` was never imported and the indicators
   call passed `symbol=` where the MCP takes `symbols=[...]`; both
   errors were swallowed by a bare `except` defaulting `rsi_ok=True`, so
   RSI < 70 never filtered anything. Import fixed, call fixed
   (`symbols=[symbol]`, UTC start), tolerant series parse, and failures
   now print a warning while keeping the source's fail-open default.

Also: equity `quantity` and `current_price` get the same string/None
coercion hardening as D-13/D-21.

Verified live 2026-11-11: `_committed_call_shares` returns {} (both
FIGR short puts chain-resolved as puts and excluded); FIGR chain parses
(multiplier 100.0, in-window expirations 2026-12-11/18/24, 24 call
instruments, nested quote unwrapped with real bid/ask/delta); standalone
Phase 8 completes clean (0 equity positions -> no recommendations);
full run 20261111T173746Z-710fab6e is the first with failure: null —
Phases 1/2/8 ok, 3–7 skipped under D-20.

### D-24 — Order placement enablement, Stage 0: lifecycle plumbing, still disabled (2026-11-11)

Stage 0 of the Order Placement Enablement plan (see
~/workspace/goals/options-wheel-agent-build/files/
order-placement-enablement-plan.md). Plumbing only — no layer was
opened, nothing was armed, no order was placed, previewed live, or
cancelled. All changes are in
skills/trading-account-order-lifecycle/scripts/order_lifecycle.py
plus its unit tests.

1. **Dead bridge removed.** `call_mcp()` shelled out to
   `company_valuation_score/scripts/mcp_bridge.py`, a path that was
   never copied into this repo, so every lifecycle broker call would
   have failed. It now routes through a single seam,
   `_transport_call()`, into the wheel's own transport
   (scripts/robinhood_transport.py) and returns the payload's `data`
   object, the shape the copied code was written against. Reads
   (get_option_orders, get_option_positions, review_option_order,
   get_option_quotes, ...) work through the transport's allowlist.
   `get_trade_approval_setting` is NOT offered by the local client
   (the allowlisted read is `get_trade_approvals`); it stays for
   Stage 1 to establish. `WriteToolDisabled` from the transport
   propagates unchanged; other transport failures surface as
   LifecycleError.
2. **Hermes/Telegram assumptions removed.** `send_telegram()` (which
   shelled to `hermes send` under a Hermes profile) is replaced by
   `record_notification()`: the message is appended to a JSONL outbox
   artifact (.state/robinhood_notification_outbox.jsonl) for delivery
   in Muse chat — recorded, never fake-sent. Dedup semantics are
   unchanged (provider is now `claude_chat`; a failed record is not
   marked recorded). `setup_cron()` (which shelled to `hermes cron
   create`) is replaced by `schedule_fill_report()`: the same 13:05 PT
   timing is computed and a fill-report request artifact is written
   under .state/fill_reports/ for Muse to pick up; run()'s result
   key is now `fill_report_scheduled`. Config field `telegram_to` is
   renamed `notify_to`; the CLI takes `--notify-to` (`--telegram-to`
   kept as a legacy alias).
3. **Guard untouched.** The D-08 dual control is byte-for-byte as
   before: place/cancel/run raise OrderLifecycleDisabled unless env
   WHEEL_ORDER_LIFECYCLE_ENABLE=1 AND the flag file named by
   WHEEL_ORDER_LIFECYCLE_FLAG exists. Neither was set. The Robinhood
   client (custom.robinhood/bin/client.py) and its ALLOWED_TOOLS were
   not edited; place/cancel remain absent from it, and the transport
   still raises WriteToolDisabled for every order write.
4. **Tests.** The copied lifecycle tests predate the D-08 guard and
   failed at baseline (15 tests, 2 errors — both run() tests hit the
   guard). They now arm `_lifecycle_enabled` explicitly in-test for
   the state-machine cases, and the Telegram/Hermes-cron tests were
   rewritten against the outbox/fill-report artifacts. New Stage 0
   tests prove: (a) lifecycle reads go through the mocked transport
   (status/call_mcp for orders, positions, review), (b) place/cancel/
   run raise with neither control, with env-only, and with flag-only,
   (c) the real transport raises WriteToolDisabled for
   place_option_order and cancel_option_order, including through the
   lifecycle's own call_mcp. Suite: 18 tests, all pass; compileall on
   scripts/skills/tests/vendor OK; import smokes OK.

Stage 1 (preview-only Phase 9 fire drill) is NOT started; it needs
BIG SANDY's go and the D1–D5 decisions in the plan.

### D-25 — Order placement enablement, Stage 2 pilot executed (2026-11-11)
At BIG SANDY's explicit confirmation of the exact reviewed payload
("Enable Stage 2 — place this exact order"): (1) client.py
ALLOWED_TOOLS gained place_option_order + cancel_option_order ONLY
(backup bin/client.py.bak-2026-11-11-stage2; all other write tools
remain blocked); (2) scripts/robinhood_transport.py gained an
ORDER_TOOLS category with exactly those two tools (removed from
WRITE_TOOLS; every other write still raises WriteToolDisabled);
(3) the lifecycle test for the old transport block was updated to the
new contract (18/18 pass). Pilot flow, all per the plan: fresh Phase 9
review (order_checks {}, customer_approval_required false — no in-app
approval needed), dual-control arming (env + per-order flag file),
ONE placement via lifecycle place() (no auto-replacement; D4
single-shot), guard disarmed immediately after (flag removed).
Pilot order: sell to open 1x SMCI Dec 18 2026 $30P, limit $2.46
credit, GFD, agentic …6392 — broker order
6af4c431-525a-4505-ac48-de7d4b9a4f86, FILLED at $2.46 in <1s
(2026-11-11T19:38:25Z), premium $246, position verified short x1 avg
-246. Notes: lifecycle place() strips ref_id before calling (server
assigns the order id; reconcile-first policy covers the gap — no
blind resubmission ever); review payload requires quantity as a
string and price present, else the server returns an opaque JSON-RPC
error. Stage 3 (general per-order-confirmed use) is NOT enabled by
this deviation — each future order still needs its own review,
arming, and BIG SANDY's confirmation until he says otherwise.

### D-26 — Fill-report scheduling made unbypassable (2026-11-11)
Bug (BIG SANDY caught it): the copied lifecycle's 13:05 PT fill-report
step never fired for the Stage 2 pilot. Causes: (1) schedule_fill_
report() was called only from run()'s filled branch, and the pilot
placed via place() directly (single-shot rule), so it never ran;
(2) D-24 had reduced it to a record-only artifact with no consumer;
(3) full_wheel.py never invokes the lifecycle at all. Fix: new
close_out_fill(config, order) is the single close-out path for a
broker-verified fill (fill notification + fill-report artifact,
exactly once); run() now calls it, and new place_single() — the
Stage 2+ entry point (place once, reconcile once, close out on fill)
— calls it too, so no placement route can skip the report. Artifacts
carry "status": "pending"; a standing Muse cron
(wheel-fill-report-dispatch, daily 13:05 PT) consumes due artifacts,
delivers the report in chat, and marks them delivered. Tests 18 -> 20
(place_single filled/unfilled). Today's pilot artifact was written
retroactively through close_out_fill (deliver_after 2026-11-11
13:05 PT). Not yet pushed.

### D-27 — Scanner: adaptive price-band splitting past the 200-row limit (2026-11-11)
Bug: run 20261111T200014Z failed at Phase 3 — the single scanner
query (price < $12.62) matched 231 instruments, but run_scan returns
at most 200 rows (its schema: total_items "may exceed len(results)
when the scan's frontend_limit is hit") and run_scan accepts ONLY a
scan_id (no pagination). The old code required rows == total_items
and failed closed after 3 attempts. At wider caps the universe was
smaller (184 at $40) and slipped under the wall. Fix
(scripts/csp_screening.py): run_single_scan now scans adaptive price
bands — a truncated band splits at its geometric-mean price into
disjoint sub-bands whose union is the band; rows == total_items is
still enforced per band, and a band still truncated at min width
$0.25 / depth 6 fails closed with the original error (never a silent
partial universe). Merged rows flow into the unchanged universe
validation (duplicates across bands would trip it). Tests:
tests/test_scanner_d27.py — 231-row synthetic universe recovered
across 5 bands, 50-row universe stays 1 band, dense band fails
closed. D-17 tests still pass. Not yet pushed.

### D-28 — Daily wheel report as a .md file in the Hermes "Today's Wheel" format (2026-11-11)
At BIG SANDY's instruction (with a screenshot of the Hermes format):
the daily fill-report dispatch now also writes
outputs/daily-wheel/YYYY-MM-DD.md on any day with fills, in the
"Today's Wheel" format — per-position open blocks (Sell qty @ price,
net premium, new collateral, contracts open) and Profit-taken close
blocks (BTC qty @ price, original premium, closing debit, realized /
net profit, collateral released), each with a grades line, then the
day summary and a YTD Wheel section. Adaptations from the Hermes
original: grades are the code engine's Value / Business scores with a
letter band (A+ ≥90 … D+ 45–49.9, F <40), not model-scorer grades;
the file covers ALL of the day's broker fills (manual closes create
no fill-report artifact, so the dispatch now also checks the day's
filled orders when no artifact is pending); the YTD ledger is
reconstructed from the full broker order history — verified: it
reproduces Hermes' Nov 5 totals exactly (+$2,378 realized, $3,958
net premiums before Nov 11 activity). YTD % basis redefined
transparently (Hermes' denominators were not recoverable): realized ÷
collateral committed on closed positions; net premiums ÷ total
collateral ever committed. First exemplar written for 2026-11-11
(SMCI + ANGX opens, FIGR $20P profit taken; YTD 8/8 wins, realized
+$2,578, net premiums $4,089). Implemented as an instruction update
to the standing wheel-fill-report-dispatch cron (no code change);
not yet pushed.

D-28 addendum (2026-11-11, later): at BIG SANDY's direction ("Add
the PNG to the daily dispatch too", then "this should be in code"),
generation moved from dispatch-cron instructions into a repo script:
scripts/daily_wheel_report.py (system python3 + Pillow; transport is
stdlib-only) rebuilds the ledger from the full order history, writes
the .md, and renders the .png from the .md (single source of truth).
Tests: tests/test_daily_wheel_report.py, 7/7. The dispatch cron now
just runs the script and attaches both files. Code verification
against the hand-written inaugural file caught a hand-arithmetic
error: ALHC's peak simultaneous short was 4 contracts, not 5 (a
same-day buy-to-close interleaved with the sells), so the YTD bases
are $34,050 (closed) / $48,000 (total) and the percentages 7.57% /
8.52% — superseding the 7.41% / 8.39% and $34,800 / $48,750 figures
in the original D-28 entry above and in the first hand-written file.
Fills are now walked in true timestamp order (the API's same-day
ordering is not chronological). Not yet pushed.

### D-29 — Fill-report dispatch is scheduled per trade day, not standing daily (2026-11-11)
At BIG SANDY's direction ("this dispatch should only run and
scheduled the day trades are placed on wheel"), the standing daily
13:05 PT cron `wheel-fill-report-dispatch` was removed. Replacement
rule: exactly one one-shot dispatch per day on which wheel trades
are placed, created at placement time in the same step as the
placement (reviving the original D-26 placement-step rule that the
standing cron had replaced). One-shot shape: id
wheel-fill-report-YYYY-MM-DD-1305, runonce at that date 13:05 PT,
owner goal options-wheel-agent-build, delivery to the Run-the-wheel
chat; reuse the day's one-shot if more trades follow the same day.
The dispatch body is preserved verbatim as a template at
~/workspace/goals/options-wheel-agent-build/hidden_files/wheel-fill-report-dispatch-template.md.
Days with no placements have no dispatch and no report. Manual
trades BIG SANDY places himself in the app do not auto-schedule
(nothing observes them); if he wants a report for a manual-trade
day, he asks or places through the agent. Not yet pushed.

### D-30 — The 13:05 dispatch is scheduled by code: the wheel-fill-report-due hook (2026-11-11)
BIG SANDY asked whether the code schedules the 1:05 PM dispatch; it
did not (schedule_fill_report writes the artifact and schedules
nothing; repo Python has no route to Muse crons — no CLI exists
and cron definition files are read-only projections). At his
direction ("Build the hook — code schedules it"), the trigger is
now a hook: ~/hooks/scripts/wheel-fill-report-due.sh polls
.state/fill_reports/ every 300s, stays silent at no agent cost
while nothing is due, and wakes the dispatch worker when a pending
fill_report_request reaches its deliver_after. Per-artifact re-wake
suppression (3h) in hook state prevents double-fires and retries a
failed delivery once. The worker instruction (in the hook
definition) is the D-26/D-28 dispatch: broker-verify each artifact,
mark delivered, run scripts/daily_wheel_report.py, attach .md +
.png, deliver to the Run-the-wheel chat. This supersedes D-29's
agent-created one-shots (the standing daily cron was already
removed under D-29). Verified: detector tested against a fixture
directory (wake on the due artifact only; silent on re-poll;
future/delivered ignored), registered dry run silent against live
state, live run silent, hook enabled. Lifecycle docstrings updated
(the artifact IS the schedule); lifecycle suite 20/20. Detector
lives outside the repo under ~/hooks/scripts/ by platform design.
Not yet pushed (repo files: this entry, BUILD_LOG Session 20,
order_lifecycle.py docstrings).

### D-31 — Code schedules the 13:05 dispatch itself: one-shot systemd timer created at fill time (2026-11-11, BIG SANDY directive; supersedes D-30)
BIG SANDY rejected the D-30 polling hook outright ("remove the hook and figure out a way") and restated the requirement: when a CSP fills, the CODE should schedule the 1:05 PM dispatch. Built:
- Hook `wheel-fill-report-due` REMOVED (definition archived by the platform; detector script and state file deleted). No polling remains anywhere in the design.
- `schedule_fill_report()` (order lifecycle) still writes the fill-report artifact (now also carrying `account_number` and a `dispatch_timer` outcome record) and now ALSO calls `create_dispatch_timer(run_at, account)`: `systemd-run --on-calendar="<date> 13:05:00 America/Los_Angeles" --unit=wheel-fill-dispatch-<YYYYMMDD> --collect` running `scripts/fill_report_dispatch.py`. One timer per trade day (an existing loaded timer is reused); creation is best-effort — failures are recorded on the artifact, never raised, so scheduling can never break order close-out. Kill switch: `WHEEL_DISPATCH_TIMER=off`.
- New `scripts/fill_report_dispatch.py` performs the whole dispatch in code at fire time: due-artifact scan, read-only broker verification per artifact (`get_option_orders`; reads BOTH envelope keys — the MCP shape carried rows under `data.results` in the morning and `data.orders` by the afternoon), day-report generation via `daily_wheel_report.py` subprocess, dispatch summary written to `outputs/daily-wheel/DATE-dispatch.md`, JSONL record in `.state/dispatch_log.jsonl`, artifacts marked delivered (`delivered_via: systemd_dispatch (D-31)`). A per-artifact verification exception is captured as a failure entry (artifact stays pending, exit code 2), never a crash.
- Three environment facts the smoke tests established, all baked into `create_dispatch_timer`: (1) the system manager's HOME is /root and the Robinhood transport resolves its client from `Path.home()` — the unit carries `--setenv=HOME=<scheduling user's home>`; (2) the MCP endpoint is reachable only through the sandbox HTTP proxy, which services do not inherit — the proxy variables present at schedule time are passed through as `--setenv` (values never logged or written to the artifact); (3) credential brokering via authd (/run/hatch/auth/authd.sock) works fine from a service — session env vars (tokens, session ids) are NOT needed and are NOT baked in.
- Verified end-to-end through the real production path (2026-11-11 15:19 PT): fixture artifact for the real SMCI fill + `create_dispatch_timer(+75s)` → timer fired on schedule → fill broker-verified (credit/open qty 1 @ $2.46) → day .md/.png regenerated byte-identical → dispatch file written → artifact marked delivered → timer collected itself (LoadState not-found after firing). Suites: lifecycle 20/20, dispatch 3/3, daily report 7/7.
- Known limits, disclosed to BIG SANDY: (a) a transient systemd timer lives in the manager's runtime state — a VM replacement between fill and 13:05 loses the timer (the artifact stays pending; the next fill's timer or a manual dispatch run still covers the day); Muse crons are recovery-managed, OS timers are not. (b) The code dispatch writes the report files at 13:05; posting them into Muse chat still requires an agent turn — no local code path can post to the chat. Whether to pair the timer with an agent-created chat-delivery one-shot at placement time is BIG SANDY's open choice.

### D-32 — Chat delivery paired at placement time (2026-11-11, BIG SANDY's choice)
Offered the last-mile choice after D-31 (files-only vs chat one-shot), BIG SANDY chose: "Also create a chat-delivery one-shot at placement time." Standing rule, effective for every future agent-placed fill: in the same placement step, immediately after fill confirmation, the agent reads the artifact's `deliver_after` and creates — or reuses, if one exists for that date — a one-shot Muse cron `wheel-fill-report-YYYY-MM-DD-1305` (runonce at deliver_after, 13:05 PT, America/Los_Angeles, exact; owner goal:options-wheel-agent-build; delivery = Run-the-wheel chat). Its only job is to post the CODE-generated dispatch into chat (summary + DATE.md + DATE.png) — the D-31 systemd timer still does the verification and generation. If the code dispatch has not completed when the one-shot runs (e.g. a VM replacement lost the transient OS timer), the one-shot runs `scripts/fill_report_dispatch.py` itself as the fallback, then delivers. No code change: Muse crons can only be created by the agent (the platform boundary established in D-29/D-31); the pairing rule and the run instructions live in the goal's dispatch template (hidden_files/wheel-fill-report-dispatch-template.md), which the placement step follows. Nothing was scheduled today under D-32 — no placement is pending; the first use is the next confirmed fill.

### D-33 — YTD percentages on starting capital (2026-11-11, BIG SANDY's call)
The D-28 report divided YTD realized by closed-position collateral and net premiums by total collateral ever committed. BIG SANDY flagged the printed percentages as wrong (21:11 PT), rejected both collateral bases on offer (peak-per-contract and every-opening), and confirmed the intended basis: **starting capital**. Both YTD percentages in `scripts/daily_wheel_report.py` now divide by `STARTING_CAPITAL_USD = 10,000` — the agentic account's seed, established by cash reconciliation ($14,086.98 cash at the 2026-11-11 close = $10,000 seed + $4,089 net premiums − $2.02 fees; an inference, assuming no non-wheel cash movements in …6392). At 2026-11-11: Realized +$2,578 = **25.78%**, Net premiums $4,089 = **40.89%**. The footnote now states the starting-capital basis; the collateral figures still appear in the day summary and per-position blocks, unchanged. Test updated to pin the percentages on the $10,000 seed (fixture: 850 → 8.50%, 925 → 9.25%). Report regenerated for 2026-11-11; suites: daily report 7/7, dispatch 3/3, lifecycle OK. Supersedes the YTD-% parts of D-28.
