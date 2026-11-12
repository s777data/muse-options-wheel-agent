# Build log — muse-options-wheel-agent

## 2026-11-09 (build session 1)

- Read PRD in full (`~/workspace/user/files/OPTIONS_WHEEL_AGENT_PRD.md`),
  snapshot-push procedure, and Robinhood connector doc.
- STEP 0 INVENTORY — **SOURCE ACCESS BLOCKED (genuine blocker):**
  - `git clone https://github.com/s777data/options-wheel-agent.git` →
    `fatal: could not read Username` (repo is not public).
  - `browser.open https://github.com/s777data/options-wheel-agent` → HTTP 404.
  - GitHub connector `get_file_contents` / `list_commits` on
    s777data/options-wheel-agent @ 0b0d64a → 404. The Muse GitHub App
    installation for s777data does **not** include `options-wheel-agent`
    (repo search via the connector finds only `muse-options-wheel-agent`).
  - No local copy of the source exists anywhere under `~/workspace`
    (searched for csp_screening.py / full_wheel.py / valuation_scorer.py /
    covered_calls.py / profit_taking.py — zero hits outside this repo).
  - **Consequence:** verbatim copy of Phases 0–4, 6–8 and the
    order_lifecycle.py copy for Phases 9–11 cannot be performed until
    BIG SANDY adds `options-wheel-agent` to the Muse GitHub App
    installation (or otherwise makes it readable). Modules that depend on
    the copy are staged as fail-closed placeholders, clearly marked —
    nothing was rewritten from memory and passed off as a copy.
  - Target repo verified EMPTY via connector: `get_file_contents` on
    s777data/muse-options-wheel-agent → `409 Git Repository is empty`.
- Robinhood tool parity inventoried (see INVENTORY.md): server offers 91
  tools (incl. create_scan/run_scan, get_trade_approvals,
  get_equity_tax_lots), but the local client.py ALLOWED_TOOLS contains only
  15 read tools — a second, independent gap.
- Vendored dashboard engine (src/evaluator + config, unmodified) from
  ~/workspace/muse-business-evaluation-dashboard under
  vendor/muse-business-evaluation-dashboard/ (remote head ac8deaf,
  V5.3 + GAV-Code). See vendor VENDOR.md.
- Created .venv and installed requirements (pyyaml, yfinance, pandas,
  requests) — all installed cleanly; imports verified (pandas 3.1.1).
- Built: Robinhood transport shim, Phase 5 valuation_scorer + contract,
  Phase 5 integration functions in csp_screening.py, hard-disabled
  order_lifecycle placeholder, Phase 0 manifest scaffold in full_wheel.py.
- Phase 5 control test on MU/NVDA/V/BRK.B: see TEST_RESULTS in this log
  (appended after the run).

## Test results (2026-11-09 session 1)

- `compileall` on scripts/skills/vendor: OK. Import smoke tests: OK.
- Transport shim: place_option_order -> WriteToolDisabled; create_scan ->
  WriteToolDisabled; get_trade_approvals -> ToolNotAllowlisted. OK.
- Order lifecycle: place() -> OrderLifecycleDisabled; send_telegram ->
  telegram_not_configured, second identical message -> duplicate_deduped. OK.
- Vendor integrity: `diff -r` vendor src and config vs the dashboard
  working copy: IDENTICAL. Dashboard engine test suite run in its own
  repo with this repo's venv: **107/107 OK**.
- **Phase 5 controls (live engine runs through the adapter):**
  | Ticker | Status | Business | Value | GAV-Code | Final-Code |
  |---|---|---|---|---|---|
  | MU | ok | 68.3 | 68.8 | **81.9** | 71.8 |
  | NVDA | ok | 88.6 | 22.1 | **58.5** | 64.4 |
  | V | ok (CIK injected) | 77.8 | 20.0 | **35.0** | 52.6 |
  | BRK.B | insufficient_data (CIK injected) | — | — | None | None (reason: value_score_missing) |
  GAV-Code matches the verified Nov 6 expectations exactly (81.9 / 58.5 /
  35.0 / None). V Value 20.0 vs 18.7 on the Nov 6 basis is live-price
  movement, not a structural mismatch. BRK.B stays fail-closed as
  required. Final-Code arithmetic verified by hand for MU and NVDA.
  Row ordering: scored (Final-Code desc, ticker asc) before unscored;
  BRK.B phase6_eligible=False.
- Dry run `python -m scripts.full_wheel --force-rerun`: FAILED at Phase 1
  with `SourceCopyPending: csp_screening.run_account_preflight` —
  the expected fail-closed outcome while the verbatim copy is blocked.
  Manifest artifact written correctly. A full read-only wheel run is NOT
  claimed.

## Push attempt (2026-11-09 session 1) — BLOCKED on GitHub App write access

- Target re-confirmed empty immediately before the push (list_commits ->
  409 Git Repository is empty).
- Snapshot push (38 files, ~309 KB, NUL manifest at /tmp/wheel_paths.bin)
  FAILED during provider initialization:
  `failed to create initial file: PUT .../contents/README.md: 403
  Resource not accessible by integration`.
- Diagnostic retry via create_or_update_file for README.md alone:
  same 403 `Resource not accessible by integration`.
- Post-failure read-back: repo still completely empty (409) — nothing
  landed, no initial commit was created, and the snapshot sync record
  was NOT advanced.
- Conclusion: the Muse GitHub App can READ (search/empty-state) the new
  repo but has no write (Contents) access to it. Unblock: BIG SANDY adds
  `muse-options-wheel-agent` to the Muse GitHub App installation with
  write access, then re-run the same push-files command; all 38 files
  are staged locally and unchanged.

## 2026-11-09 (build session 2) — source access restored, verbatim copy

- BIG SANDY fixed the GitHub App installation; parent verified source
  readable (list_commits head = 0b0d64a, get_file_contents works).
- Fetched the source tree at the pinned commit via the connector.
  Directory listings recorded path/size/git-blob-SHA for every needed
  file; each fetched file's bytes were verified by recomputing its git
  blob SHA-1 — **26/26 files OK**, including scripts/csp_screening.py
  (111,618 bytes, blob 88961458f4a5a177d1053070b5cd43dd8cfb24a8).
  Mirror kept at /tmp/source_mirror (ephemeral) for the merge.
- Source Phase 5 composite formula now extracted exactly (was pending):
  confidence "medium" -> 0.30*valuation + 0.70*business_quality;
  "high" -> 0.40*valuation + 0.60*business_quality; low/unavailable ->
  None (unscored). Source valuation_scorer fresh_retry retries failed
  tickers with --no-cache (the PRD gap-5 behavior our adapter disables).
- Merge in progress (see DEVIATIONS.md D-05..D-11 updates).

## Test results (2026-11-09 session 2)

- New dependency from copied code: `httpx` (csp_screening SEC client) —
  added to requirements.txt, installed (0.28.1). All other imports are
  stdlib / already-present (yfinance, pandas, requests, pyyaml).
- Environment quirk found & fixed (D-12): this sandbox's `no_proxy`
  contains bracketed-IPv6 entries ("[::1]") that httpx 0.28's
  URLPattern parser rejects at module import; csp_screening now drops
  only the bracketed duplicates before constructing its SEC client.
- `compileall` on scripts/skills/tests/vendor: OK. Import smokes: OK
  (all phase entry points present). Lifecycle guards: place/cancel/run
  all raise OrderLifecycleDisabled. Shim: create_scan ->
  WriteToolDisabled, get_trade_approvals -> ToolNotAllowlisted.
  Function sanity: profit_taking_signal TAKE case OK, linear_boundary
  OK, calculate_cash_secured_capacity OK,
  prepare_valuation_mcp_adapter returns the adapter path.
- **Phase 5 controls re-run after the merge (live):**
  | Ticker | Status | Business | Value | GAV-Code | Final-Code |
  |---|---|---|---|---|---|
  | MU | ok | 68.3 | 68.8 | **81.9** | 71.8 |
  | NVDA | ok | 88.6 | 22.1 | **58.5** | 64.4 |
  | V | ok (CIK injected) | 77.8 | 20.0 | **35.0** | 52.6 |
  | BRK.B | insufficient_data | — | — | None | None (value_score_missing) |
  Identical to session 1; BRK.B remains fail-closed. Rows now built by
  the source-signature build_phase5_valuation_rows (Final-Code
  composite); ordering MU > NVDA > V > BRK.B(unscored).
- **Full-wheel dry run** (`python -m scripts.full_wheel --force-rerun`,
  run 20261110T020725Z-ce377b48): **Phase 1 PASSED live** — 3 accounts
  seen, exactly 1 eligible (agentic …6392, limited_margin,
  option_level_2, active), unleveraged buying power $206.25
  (max underlying price $2.06). **Stopped at Phase 2** with
  `TypeError: '>' not supported between instances of 'str' and 'int'`:
  the live MCP returns option-position `quantity` as a string and the
  source's short-position filter compares it to int 0
  (profit_taking.py). Workflow correctly recorded FAILED (never
  PARTIAL); manifest artifact is the evidence. This is a source/live
  payload-shape incompatibility, NOT patched in session 2 (outside the
  approved deviation list) — a one-line float() coercion in the filter
  is the obvious fix, pending approval. Next expected stops after it:
  Phase 4A create_scan (WriteToolDisabled in the shim / not in the
  local client allowlist) inside run_csp_screening. Note the source
  Phase 1 does not call get_trade_approvals at all (PRD lists it; the
  code doesn't), so that anticipated stop does not occur.

## Push (2026-11-09 session 2) — SUCCESS

- Target re-confirmed empty immediately before the push (list_commits
  -> 409). No prior sync record existed (session 1 never advanced one).
- Snapshot push: **58 files, 591,450 bytes** (excludes .venv, outputs/,
  __pycache__, and the vendor engine's runtime output/cache), branch
  main. Provider initialized the repo (initial commit 003dd6f3) and
  the snapshot landed as head **98239034936679a783e9313184fbf2992ebd85fc**.
- Read-back verification: list_commits head == 98239034…; six key
  files fetched back and byte-compared to local — csp_screening.py
  (105,576 bytes; contains the source's qualify_options_hardened /
  MIN_CSP_SHORT_POP and the D-05 Final-Code composite),
  valuation_scorer.py, README.md, order_lifecycle.py (guard present),
  covered_calls.py (D-07 helpers present), profit_taking.py — **all
  identical**. Sync record saved at
  ~/workspace/muse-options-wheel-agent.muse-github-snapshot-sync.json.

## Session 3 (2026-11-09) — D-13 applied, dry run re-run

- D-13 applied with BIG SANDY's approval: `scripts/profit_taking.py`
  short-position filter now `float(pos.get("quantity", 0) or 0) > 0`
  (one line; DEVIATIONS.md updated from OBSERVED to D-13).
- Dry run `20261110T021954Z-8b21b60e`: Phase 1 PASS again (agentic
  …6392, BP $206.25). Phase 2 got PAST the quantity filter — the fix
  works — and found the account's 2 open FIGR short option positions.
  It then FAILED with `KeyError: 'instrument_id'` at the quote-map
  line. Root cause diagnosed against the live MCP (read-only calls):
  `get_option_quotes` returns `results[]` items shaped
  `{"quote": {instrument_id, bid_price, ask_price, ...}, "close": {...}}`
  — the quote is nested — while the source expects flat quote objects.
  A second latent instance of the D-13 bug sits downstream of it:
  `quantity = pos.get("quantity", 0)` (string) is used in float
  arithmetic for unrealized P&L.
- NOT patched (outside the approved one-line scope). Proposed
  Phase 2 live-shape patch, 3 lines in profit_taking.py: unwrap
  `q.get("quote", q)` when building quote_map, use the unwrapped
  quote for bid/ask, and `quantity = float(pos.get("quantity", 0) or 0)`.
  Recorded in DEVIATIONS.md as OBSERVED-2 pending approval.

## Session 4 (2026-11-09) — D-14 applied, dry run re-run

- D-14 applied with BIG SANDY's approval (3 lines in
  `scripts/profit_taking.py`): quote results unwrapped via
  `q.get("quote", q)` when building the quote map, and
  `quantity = float(pos.get("quantity", 0) or 0)` at the P&L read.
  DEVIATIONS.md OBSERVED-2 converted to D-14.
- Dry run result appended below after the run.
- Dry run `20261110T022223Z-3dc41770`: **Phase 1 PASS, Phase 2 PASS**
  (first fully green Phase 2: both FIGR short positions on …6392
  evaluated and signal rows produced; note the source's Phase 2
  output is placeholder-grade by its own comments — strike shows 0
  because live positions carry no strike_price, and the signed
  average_price makes its P&L arithmetic non-decision-grade; the
  phase executes, the numbers are the source's, flagged here).
  Run then FAILED in Phase 4A as predicted: scanner query
  (price < $2.06) → `create_scan` hard-disabled in the transport
  shim (not in the Robinhood client allowlist), 3 bounded retries,
  `RuntimeError: Single scanner query failed after 3 attempts`,
  workflow FAILED (fail-closed working). Unblocking Phase 4A is the
  client-allowlist decision (create_scan/run_scan), not a code fix.

## Session 5 (2026-11-09) — allowlist extended (approved), deepest run yet

- BIG SANDY approved extending the Robinhood client allowlist with 5
  non-order tools. `client.py` ALLOWED_TOOLS += create_scan, run_scan,
  get_equity_tax_lots, get_trade_approvals, review_option_order
  (backup: client.py.bak-2026-11-09). Shim aligned (D-15): the two
  reads joined READ_TOOLS; create_scan/run_scan/review_option_order
  moved to a new NON_ORDER_ACTION_TOOLS set — they create scanner
  runs / order previews only. Order tools verified still
  WriteToolDisabled (place/cancel/exercise/decline). Live smoke:
  get_trade_approvals (0 pending) and get_equity_tax_lots both work.
- Dry run `20261110T022548Z-765de2a3` (~3 min): Phase 1 PASS,
  Phase 2 PASS, **Phase 4A scanner WORKED** (16 rows under the $2.06
  cap), Phase 4B revenue gate passed 3 (KEEL, EOSE, GERN; one
  yfinance timeout warning on CIG, gate completed), Phase 4C
  technicals passed all 3, **Phase 5 scored live in-pipeline**:
  GERN Final-Code 37.5 (Biz 20.5 / Val 50.9 / GAV-Code 58.0),
  EOSE 33.8 (16.7 / 36.2 / 65.6), KEEL unscored
  (ttm_revenue_growth_missing → fail-closed, excluded) — exactly the
  designed behavior on a real cohort.
- Run FAILED in Phase 6 on the quote-freshness gate:
  `stale underlying quote for GERN: 19727.0s` (~5.5 h — the run was
  at 18:25 PT, after the 13:00 PT close). This is the PRD safety
  gate working, not a bug; nothing patched. A full end-to-end PASS
  requires a market-hours run. Remaining phases (6 chain
  qualification, 7 ranking, 8 covered calls) are untested live for
  this reason only; Phase 8's tax-lots dependency is now unblocked.

## Session 6 (2026-11-09) — $40 scan-cap dry run (D-16) + new Phase 5 failure found

- D-16 applied at BIG SANDY's request: env-gated dry-run scan-cap
  override `WHEEL_SCAN_PRICE_CAP_USD` in run_account_preflight
  (default behavior unchanged; collateral checks keep real buying
  power; orders stay disabled).
- Dry run `20261110T034515Z-beb17c40` with cap $40, total 17m47s.
  Phase timings: P1 ~5s, P2 ~5s, 4A ~7s (182 rows; min-mcap tier
  stepped to $1B per the source's >$25 rule), 4B 3m29s (182 -> 68;
  top rejection: annual YoY <= 15%, 78 names), 4C 5m53s (68 -> 67),
  Phase 5 ~8m (all 67 evaluated sequentially).
- Run FAILED at the Phase 5 merge: GENB (Generate Biomedicines,
  recent IPO, CIK 0002100782, no 10-K on EDGAR) makes the dashboard
  engine raise `No 10-K found`; the D-05 adapter records
  `result: null` for engine exceptions, and the source's
  build_phase5_valuation_rows treats a null result as fatal.
  Contrast KEEL/BRK.B: engine returns a structured insufficient_data
  envelope -> ticker excluded, run continues. Reproduced standalone
  via tests/run_phase5_controls.py GENB.
- Proposed fix D-17 (NOT applied, awaiting approval): in the
  adapter's exception path, emit the standard structured
  insufficient_data envelope (scores null, failure_reason = the
  engine error) instead of result: null — per-ticker fail-closed,
  consistent with every other unscored path. Adapter file only;
  source code untouched.

- D-17 decision (2026-11-09): BIG SANDY declined the fix — GENB is to be ignored. Known accepted behavior: a cohort containing a no-10-K filer (engine raises -> result null) will stop the run at the Phase 5 merge until/unless D-17 is revived.

## Session 7 (2026-11-09) — D-18 engine fixes + D-19 adapter fallback

- BIG SANDY approved "engine fixes plus the flagged yfinance
  fallback". Built in the dashboard working copy, synced to vendor,
  independently re-verified by Muse in the main thread (controls
  harness + spot tickers URGN/TNGX/KEEL/GENB).
- Note: the pre-build diagnosis was wrong in three places; the build
  re-derived causes from companyfacts: URGN = basic-shares-only
  filer (Fix C-2), TNGX = filed revenue of 0 suppressing all price
  legs (Fix C-1), PGEN = bridge failure with clean FY annuals
  (Fix B fy_annual rung). Fix A was narrowed mid-build after a
  BRK.B baseline diff exposed cross-tag splicing risk.
- Final cohort state: 63 scored / 4 unscored (GENB, AADX, QNT,
  SECZ — no 10-K / shell, by design). New scores include LGN 67.7,
  FIGR 59.4, GLXY 57.9, EQPT 54.5, URGN 48.2, PGEN 36.6, APLD 32.6,
  KEEL 30.2, TNGX 27.8, ZBIO 24.1, BMNR 21.6 (Final-Code).

## Session 8 (2026-11-09) — Phase 6+7 rehearsal on closing quotes

- At BIG SANDY's instruction ("run phase 6, just use last closed
  values"), a harness (outputs/run_phase6_rehearsal.py, no repo code
  changed) fed the pipeline's own qualify_options_hardened +
  build_phase7_ranking the $40 cohort's Phase 5 rows (63 scored;
  4 null-result names excluded — Phase 6 consumes scored rows only),
  with two in-process-only relaxations: quote max age raised
  (closing quotes ~30,890s old) and buying power premised at $4,000
  to match the $40 cap. Production gates untouched.
- Result: primary cohort (HRMY, BRBR, LGN, UNIT, NRIX) strict
  window: 0 qualified. Secondary strict: 2 qualified — HIMS
  Dec 18 '26 $22P (bid $2.08, net ROC 10.44%, POP 0.638, OI 2,311)
  ranked #1, SMCI Dec 18 '26 $28P (bid $2.42, net ROC 9.46%,
  POP 0.634, OI 6,103) ranked #2; INOD/ADEA/NUVB no contract passed.
  First end-to-end Phase 6/7 completion. Phase 8 (covered calls)
  and a true market-hours full run remain for the Nov 10 cron.

## Session 9 (2026-11-11) — D-20 CSP buying-power gate + first market-hours run

- First true market-hours full run (20261111T163640Z-09a63f6b, 2m01s):
  P1 PASS (BP $206.25), P2 PASS (both FIGR shorts CALCULATE), scanner
  19 -> 2 (EOSE, GERN), Phase 5 GERN 37.3 / EOSE 33.2, Phase 6 strict
  + fallback 0 qualified (0 moneyness-eligible of 38 raw), Phase 7 no
  recommendation. Phase 8 FAILED the run: the Robinhood MCP errors on
  get_option_instruments queried by `ids` (by chain_symbol works;
  direct call + standalone Phase 8 retry both reproduced it), and
  covered_calls' D-07 pre-check resolves positions by ids. Agentic
  account holds 0 equity positions, so no covered call was possible
  regardless. Phase 8 ids-lookup patch proposed to BIG SANDY, not yet
  approved at time of writing.
- D-20 applied at BIG SANDY's request ("not to run P3 when BP is less
  than 700"): orchestrator-level gate in full_wheel.py — see
  DEVIATIONS.md. Verified live the same morning (see the verification
  run appended below).
- D-20 verification run `20261111T164314Z-95b94504` (17s total, vs
  2m01s for the ungated run an hour earlier): P1 PASS (BP $206.25),
  P2 PASS, **03_07_csp_screening status "skipped"** with reason
  `buying_power_below_csp_minimum` (BP $206.25 < $700) recorded in the
  manifest, and the run proceeded to Phase 8 — which failed on the
  same known get_option_instruments-by-ids MCP error (the only
  failure in the run; the gate itself verified clean).

## Session 10 (2026-11-11) — D-21 Phase 2 profit-taking fix

- BIG SANDY, on the voice call: the Phase 2 `CALCULATE` placeholder
  "is a bug, fix it." D-21 applied (see DEVIATIONS.md): chain-based
  instrument resolution, real sqrt-boundary signals, premium/P&L
  units fix. Standalone live run: both FIGR positions now produce
  real signals — Dec 18 $20P TAKE, Nov 20 $32.50P HOLD — matching
  hand-computed boundaries ($0.73 / $3.64) exactly. Phase 2 runtime
  ~11s (instrument-resolution calls) vs ~6s for the placeholder.
- D-21 in-pipeline verification run `20261111T165352Z-cc15479a`:
  Phase 2 manifest signals are FIGR $20P TAKE / FIGR $32.50P HOLD;
  Phase 3 skipped per D-20; the run's only failure remains Phase 8
  (known by-ids MCP error, patch still pending BIG SANDY's go).

## Session 11 (2026-11-11) — D-22 Phase 2 signal aligned to skill spec

- BIG SANDY: "Isn't it 50% min in code for TAKE..why at 49%?" — he was
  right. The 50.00% minimum premium-capture gate lives in the
  profit-taking-decider skill spec; the copied signal function never
  implemented it (price-trigger variant instead). D-22 applied at his
  instruction ("make sure this rule is on code"): see DEVIATIONS.md.
  Six unit checks pass, including both strict-equality edges.
- Live verification run appended below.
- D-22 live verification (standalone Phase 2, 2026-11-11 ~09:31 PT):
  FIGR Dec 18 $20P x2 -> HOLD (captured 48.72%, required 37.35%,
  50% gate fails — the D-21 TAKE is corrected); FIGR Nov 20 $32.50P
  x3 -> HOLD (captured -115.55%, required 91.77%). Both signals now
  match the skill spec on live data.

## Session 12 — 2026-11-11 (Phase 8 repair, D-23)

- BIG SANDY asked for a Phase 8 bug audit, then approved fixing all
  five as one deviation ("Yeah, go ahead").
- D-23 applied to scripts/covered_calls.py: chain-scoped instrument
  type (no more `ids` lookups), data.chains + expiration_dates list +
  string multiplier, data.tax_lots, nested-quote unwrap, live RSI gate
  (timedelta import + symbols=[...]); quantity/price coercions hardened.
- Live verification: committed-shares {} (FIGR puts excluded correctly);
  FIGR chain/instruments/quote path exercised end-to-end; standalone
  Phase 8 clean; full run 20261111T173746Z-710fab6e failure: null
  (P1/P2/P8 ok, P3-7 D-20 skip) — first fully green pipeline run.

## Session 13 — 2026-11-11 (Order placement enablement, Stage 0 — D-24)

- BIG SANDY approved starting the Phases 9–11 enablement project;
  Stage 0 (plumbing, still disabled) implemented per the plan file in
  the goal workspace. No layer opened, nothing armed, client
  untouched, no order placed/previewed/cancelled.
- order_lifecycle.py: dead mcp_bridge path removed; call_mcp routes
  through scripts/robinhood_transport.py via a `_transport_call` seam
  and returns the payload `data` object; WriteToolDisabled propagates
  unchanged. send_telegram -> record_notification (JSONL outbox
  artifact for Muse chat delivery, dedup preserved, provider
  claude_chat). setup_cron -> schedule_fill_report (13:05 PT timing
  kept; fill-report request artifact under .state/fill_reports/).
  Config.telegram_to -> notify_to; CLI --notify-to (legacy
  --telegram-to alias kept).
- Tests: baseline was 15 tests / 2 errors (copied run() tests hit the
  D-08 guard). Now 18 tests, all pass — state-machine tests arm the
  guard explicitly in-test; new Stage 0 tests cover the mocked
  transport read path, guard behavior with neither/env-only/flag-only
  controls, and WriteToolDisabled on the real transport for
  place/cancel. compileall OK; guard smoke: place/cancel/run all
  raise OrderLifecycleDisabled with a clean environment.

## Session 14 — 2026-11-11 (D-17 revived: Phase 5 insufficient_data envelope)

- Trigger: live run 20261111T183812Z-05fa98b4 (BIG SANDY "just run the
  wheel", BP $4,016.17, cap $40.16) failed at the Phase 5 merge:
  LEGN terminal_status='insufficient_data', result null. Root cause
  verified standalone: engine raises "No 10-K found in EDGAR
  submissions for CIK 0001801198" — LEGN (Legend Biotech) files 20-F
  as a foreign private issuer. Same class as GENB (D-17 declined
  2026-11-09). BIG SANDY chose "Build the D-17 fix first, then rerun".
- Change: scripts/csp_screening.py `build_phase5_valuation_rows` —
  terminal insufficient_data + missing result now builds a structured
  unscored row (same schema, scores None, failure_reason carried,
  Phase 4 fields kept); other statuses with missing results still
  raise RuntimeError. Row `confidence` falls back to the terminal
  record when the result carries none.
- Tests: tests/test_phase5_d17.py 15/15 pass; live Phase 5 controls
  MU 68.8 / NVDA 65.8 / V 52.6 / BRK.B unscored (structure unchanged);
  order-lifecycle unittest suite OK.

## Session 15 — 2026-11-11 (Stage 2 pilot: first agent-placed order, D-25)

- Stage 1 preview drill (same morning): review_option_order for the
  rerun's Phase 7 recommendation — SMCI Dec 18 '26 $30P sell-to-open
  x1 @ $2.46 — order_checks {}, customer_approval_required false,
  collateral $3,000, fees $0.04. Payload hashed into the run dir.
- BIG SANDY confirmed the exact payload ("Enable Stage 2 — place
  this exact order", 11:36 AM PT). Client + shim opened for
  place/cancel only (D-25); lifecycle tests 18/18; guard verified
  refusing while unarmed; fresh re-review at 11:38 AM still clean
  (bid $2.41 / ask $2.52 / mark $2.47).
- Armed (env + flag), placed once via lifecycle place(): broker
  order 6af4c431-525a-4505-ac48-de7d4b9a4f86 — FILLED at the $2.46
  limit within a second, premium $246 processed. Position verified:
  short SMCI Dec 18 $30P x1, avg -246. Flag removed (disarmed);
  placed/fill notifications recorded to the chat outbox.

## Session 16 — 2026-11-11 (D-26: fill-report fix)

- Root-caused the missing 13:05 PT fill report (see D-26) and fixed
  it in code: close_out_fill() single close-out path; place_single()
  single-shot entry point that reconciles and closes out; run()
  refactored onto close_out_fill with unchanged behavior.
- Lifecycle tests 20/20 (2 new place_single tests).
- Wrote today's pilot fill-report artifact via close_out_fill
  (pending, deliver_after 13:05 PT today); replaced the ad-hoc
  one-shot cron with the standing daily 13:05 PT dispatch cron.

## Session 17 — 2026-11-11 (D-27: scanner banding)

- Diagnosed the 12:00 PM run's Phase 3 failure (231 vs 200-row
  frontend limit; run_scan has no pagination params — schema-checked).
- Built adaptive price-band splitting in run_single_scan with
  per-band fail-closed equality; tests/test_scanner_d27.py green;
  D-17 suite still green. Rerun launched same day with the new
  5-minute progress rule armed.

## Session 18 — 2026-11-11 (D-28: daily wheel .md)

- BIG SANDY supplied the Hermes "Today's Wheel" screenshot format
  and directed that the report create a .md file in that format.
- Reconstructed the full account ledger from get_option_orders
  (34 orders, Jul 28 – Nov 11): 11 contracts, 8 closed (all wins),
  3 open, 0 assignments; cross-checked against the screenshot's
  Nov 5 figures (realized +$2,378, net premiums $3,958) — exact.
- Wrote outputs/daily-wheel/2026-11-11.md (SMCI/ANGX opens, FIGR
  $20P close, day summary, YTD: realized +$2,578, net premiums
  $4,089) with code-engine grades (SMCI B-/C, ANGX A-/C, FIGR D+/C+).
- Updated the wheel-fill-report-dispatch cron body: .md generation
  on any day with fills (incl. artifact-less manual-close days),
  format spec + grade bands + YTD basis embedded; next run Nov 12
  13:05 PT verified.

## Session 18 addendum — 2026-11-11 (D-28 code version)

- BIG SANDY asked for a screenshot of the .md in the Hermes style;
  rendered with PIL (bundled Chromium refuses direct CLI use), then
  at his "this should be in code" the whole generator became
  scripts/daily_wheel_report.py: orders -> ledger -> .md -> .png,
  with tests/test_daily_wheel_report.py (7/7, system python3).
- Regenerated 2026-11-11 files from code; diff vs the hand-written
  file is only the footnote validation sentence (dropped by design)
  and corrected YTD percentages (7.57% / 8.52%; ALHC peak short 4,
  not 5 — see DEVIATIONS D-28 addendum).
- Footnote font switched to DejaVu Sans (Noto Italic renders ≥ as
  tofu here); verified visually.
- Dispatch cron repointed: run the script, attach .md + .png, and
  report script errors instead of hand-writing a substitute.

## Session 19 — 2026-11-11 (D-29: per-trade-day dispatch)

- BIG SANDY: the 1:05 PM dispatch "should only run and scheduled
  the day trades are placed on wheel." Removed the standing daily
  wheel-fill-report-dispatch cron (verified: disabled, no next run).
- Saved the dispatch body as a one-shot template in the goal's
  hidden_files; the scheduling rule (create at placement, same step,
  one per day, reuse if present) is logged as D-29. Tomorrow
  (Nov 12) has no dispatch unless trades are placed.

## Session 20 — 2026-11-11 (D-30: hook-scheduled dispatch)

- Answered BIG SANDY's "the code should schedule the 1:05 dispatch
  — is that the case?" honestly: no; the lifecycle only writes the
  artifact, and D-29's one-shots were agent-created. He chose the
  hook build.
- Built ~/hooks/scripts/wheel-fill-report-due.sh (300s poll of
  .state/fill_reports/, wake on due pending artifacts, 3h per-
  artifact re-wake suppression via hook state). Fixture-tested both
  branches; registered as hook wheel-fill-report-due with the
  dispatch as its worker instruction, delivery to this chat;
  dry run + live run silent against current state; enabled.
- order_lifecycle.py docstrings corrected (removed the stale
  "standing dispatch" claim); lifecycle suite 20/20.

## Session 21 — 2026-11-11 (D-31: hook removed; code schedules the dispatch via systemd timer)
- BIG SANDY rejected the D-30 hook; hook `wheel-fill-report-due` removed, detector script + state deleted.
- Lifecycle: `create_dispatch_timer()` added; `schedule_fill_report()` records account_number + timer outcome on the artifact and creates/reuses the day's one-shot timer for 13:05 PT.
- New `scripts/fill_report_dispatch.py` + `tests/test_fill_report_dispatch.py` (3/3). Env lessons baked in: HOME setenv (transport client resolution), proxy passthrough (MCP reachability), authd works from services (no session secrets baked in). Envelope dual-key read (results/orders) after the afternoon shape drift.
- Full-chain smoke through the production path passed (fixture on the real SMCI fill; day files byte-identical; artifact delivered; timer self-collected). Suites: lifecycle 20/20, dispatch 3/3, daily report 7/7.
- Unpushed: scripts/fill_report_dispatch.py, tests/test_fill_report_dispatch.py, order_lifecycle.py (D-31), DEVIATIONS.md, BUILD_LOG.md.

## Session 22 — 2026-11-11 (D-32: chat-delivery one-shot paired at placement)
- BIG SANDY chose the chat-delivery pairing: every future placement step also creates/reuses the one-shot cron `wheel-fill-report-<date>-1305` for the artifact's deliver_after, which posts the code-generated dispatch into the Run-the-wheel chat (with a fallback run of the dispatch script if the OS timer was lost).
- Dispatch template rewritten to D-31 + D-32 combined mechanism. No code changes (cron creation is agent-side by platform design). No cron created today — no placement pending.

## Session 23 — 2026-11-11 (D-33: YTD % on starting capital)
- BIG SANDY flagged the 11/11 report's YTD percentages, rejected both collateral bases, and confirmed starting capital as the basis (seed $10,000 by cash reconciliation).
- `scripts/daily_wheel_report.py`: added `STARTING_CAPITAL_USD`; realized % and net-premiums % both divide by it; footnote restated; docstring updated. `tests/test_daily_wheel_report.py`: YTD assertions now pin exact percentages on the seed. Regenerated outputs/daily-wheel/2026-11-11.{md,png} — Realized 25.78%, Net premiums 40.89%.
- Suites: daily report 7/7, dispatch 3/3, lifecycle OK. Local-only (outputs never pushed, per standing rule); code/test/log changes unpushed pending BIG SANDY's word.

## Session 24 — 2026-11-11 (D-34: collateral counts every opening — tree made clean)
- At BIG SANDY's word, the working-tree collateral change found during the D-33 push is committed on its own: `collateral = strike x 100 x total_opened` (was peak `max_short`), RCLD fixture + `test_collateral_counts_every_opening` included. Verified the remote-vs-local diff was exactly this change and nothing else before pushing.
- Suites: daily report 7/7. After this push the working tree and remote agree on the daily-report files.
