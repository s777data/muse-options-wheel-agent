# muse-options-wheel-agent

Options Wheel Agent (per `OPTIONS_WHEEL_AGENT_PRD.md`): a scheduled,
read-first, human-authorized workflow for an options-wheel portfolio on
the Robinhood **agentic account (…6392)**. Copy-by-phase implementation
of `s777data/options-wheel-agent` @ `0b0d64a` — with one deliberate
exception: Phase 5 valuation is **not** the source's placeholder
`company_valuation_score` engine. It calls the Muse Business Evaluation
Dashboard engine (V5.3 + GAV-Code) in-process and ranks CSP candidates
by **Final-Code = 0.5·Business×10 + 0.25·Value×10 + 0.25·GAV-Code**.

**Status (2026-11-09, build session 2): copies complete.** All 26
needed source files were fetched at the pinned commit and verified by
git blob SHA-1 (see `INVENTORY.md`); the modules are in place with only
the deviations logged in `DEVIATIONS.md`. Phase 5 controls pass
(MU 71.8 / NVDA 64.4 / V 52.6 Final-Code; BRK.B fail-closed). A live
read-only dry run passes Phase 1 (exactly one eligible agentic
account, …6392) and currently stops in Phase 2 on a source/live
payload-shape mismatch (option-position `quantity` arrives as a
string; see BUILD_LOG.md session 2 — fix pending approval). Order
placement is disabled at three layers (this repo's lifecycle guard,
the transport shim, and the Robinhood client's own write block). No
order is ever placed by a scheduled run.

## Per-phase status

| PRD phase | Status |
|---|---|
| 0 Schedule / manifest / idempotency | Implemented (scaffold: manifest, duplicate-run guard, deduped notification state) |
| 1 Account preflight + snapshot | Verbatim copy; **live-verified PASS** on agentic …6392 |
| 2 Profit taking | Verbatim copy; live run stops on the quantity-as-string TypeError (see above) |
| 3 CSP eligibility / capacity | Verbatim copy |
| 4A Market scanner | Verbatim copy; **cannot run** — `create_scan`/`run_scan` not in the local client allowlist |
| 4B Fundamentals / revenue gate | Verbatim copy (yfinance + SEC helpers) |
| 4C Technical validation | Verbatim copy |
| 5 Valuation (placeholder replaced) | **Dashboard adapter** — Final-Code composite; controls pass |
| 6 Option-chain qualification | Verbatim copy |
| 7 CSP ranking + recommendation | Verbatim copy |
| 8 Covered calls | Verbatim copy **+ D-07 reserved-share fix**; needs `get_equity_tax_lots` (not allowlisted) to run end-to-end |
| 9 Review / confirmation | Verbatim lifecycle copy, hard-disabled (D-08) |
| 10 Submission / reconciliation | Verbatim lifecycle copy, hard-disabled (D-08) |
| 11 Notifications / post-fill | Lifecycle copy hard-disabled; orchestrator notifications write payloads to run artifacts (`telegram_not_configured`) until Telegram credentials exist |

## Run it (code-only, no model)

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

# Full daily run (read-only; fails closed on any required-phase failure):
.venv/bin/python -m scripts.full_wheel

# Phase 5 standalone control test (dashboard-engine scoring, sequential):
.venv/bin/python tests/run_phase5_controls.py MU NVDA V BRK.B
```

Daily scheduling is a plain cron/scheduler invocation of
`.venv/bin/python -m scripts.full_wheel` — no model call is involved in
a run, so a daily run burns no tokens.

## Layout

- `scripts/` — orchestrator (`full_wheel.py`), CSP/profit-taking/
  covered-call modules (verbatim copies + logged deviations), Phase 5
  adapter (`valuation_scorer.py`), contract (`valuation_contract.py`),
  Robinhood transport shim (`robinhood_transport.py`)
- `skills/` — the source's normative skill docs (copied) and
  `trading-account-order-lifecycle/` (Phases 9–11, hard-disabled)
- `vendor/muse-business-evaluation-dashboard/` — pinned dashboard
  engine (commit ac8deaf; see its VENDOR.md — do not edit)
- `outputs/` — private run artifacts (gitignored; never commit
  account data)
- `INVENTORY.md` — Step 0 inventory (sizes/SHAs) + Robinhood
  tool-parity gaps
- `DEVIATIONS.md` — every intentional difference from a verbatim copy
- `BUILD_LOG.md` — running build record

## Known gaps / decisions still open

- Robinhood client allowlist extension (BIG SANDY's connector):
  `create_scan`/`run_scan` (Phase 4A), `get_trade_approvals` (PRD
  Phase 1 completeness — the source code doesn't call it),
  `get_equity_tax_lots` (Phase 8), `review_option_order` (Phase 9).
- Telegram bot token / chat ID (via Secure Vault) — until then,
  notifications are artifact payloads, not sends.
- One-line profit-taking quantity coercion fix (dry-run finding,
  recorded in DEVIATIONS.md as OBSERVED).
- Order-lifecycle enablement: deliberately off; enabling requires
  `WHEEL_ORDER_LIFECYCLE_ENABLE=1` + a user-controlled flag file, and
  a separate explicit decision.
