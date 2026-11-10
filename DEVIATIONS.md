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
- **OBSERVED-2, not patched (session 3 dry run):** live
  `get_option_quotes` results nest the quote under a `quote` key
  (`{"quote": {instrument_id, bid_price, ...}, "close": {...}}`);
  the source builds `quote_map[q["instrument_id"]]` from flat
  objects → KeyError. Downstream, `quantity = pos.get("quantity", 0)`
  (string) feeds float P&L arithmetic — same class as D-13.
  Proposed 3-line patch in `scripts/profit_taking.py` (unwrap
  `q.get("quote", q)`; float-coerce quantity), pending approval.
