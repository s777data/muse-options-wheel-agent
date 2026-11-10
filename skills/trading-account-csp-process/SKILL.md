---
name: trading-account-csp-process
description: Pull the options-wheel-agent repository and run scripts/csp_screening.py end to end, monitoring the active run and reporting progress, rows written, filtered counts, issues, and output paths every five minutes without cron.
---

# Trading account CSP process

## Objective and execution boundary

Execute the existing CSP screening code end to end. Resolve routine execution issues promptly, monitor the process until it exits, and report verified outputs. Run the screening once per invocation; five-minute updates are monitoring intervals, not repeated screening runs.

Treat these instructions as the complete runbook once loaded by Hermes. During execution, strictly do not open, reread, search the contents of, or follow references from any SKILL.md/skill.md file, including this one. Do not invoke other skills. Loading this skill initially is the prerequisite for using it; the prohibition applies after that initial load. If even initial skill loading is forbidden, supply this body directly as the task instructions instead.

Run existing code and operational shell commands only. Do not substitute manual screening, web research, invented results, or discretionary trading decisions. Do not change screening logic, thresholds, valuation logic, or source code to force success. This workflow does not authorize placing orders.

## 1. Establish the repository and protect existing work

Use this repository:
`/home/s777data/.hermes/profiles/options-wheel-agent`

Never run Git commands from `/home/s777data` merely because it is the shell's starting directory. Resolve and verify the root before pulling:

```bash
CSP_REPO=/home/s777data/.hermes/profiles/options-wheel-agent
CSP_ROOT="$(git -C "$CSP_REPO" rev-parse --show-toplevel)" || exit 1
test -f "$CSP_ROOT/scripts/csp_screening.py" || exit 1
CSP_RUN_DIR="$(mktemp -d /home/s777data/csp-run-XXXXXXXX)" || exit 1
git -C "$CSP_ROOT" status --porcelain=v1 --untracked-files=all > "$CSP_RUN_DIR/git-status-before.txt"
git -C "$CSP_ROOT" rev-parse HEAD > "$CSP_RUN_DIR/commit-before.txt"
```

Use the resolved root as the working directory for the entire pipeline. Record the branch, upstream, start time, and run directory. Check for an existing active screening process for this repository; attach to its monitoring rather than launch a duplicate. Do not kill an unrelated process.

Record dirty tracked and untracked paths before changing anything. Keep logs, status snapshots, and monitoring artifacts in the external run directory. Do not reset, clean, delete, overwrite, stash, or commit pre-existing work to make pulling easier.

Run:
`git -C "$CSP_ROOT" pull --ff-only`

Capture the actual exit status and log. Existing dirty files alone are not a reason to stop if the pull succeeds safely. If local edits would be overwritten, branches diverge, authentication fails, or an upstream is missing, diagnose the precise failure. Retry a transient network failure at most twice. Do not guess a branch, force a merge, silently switch checkouts, or run old code while claiming that the pull succeeded. Report a genuine unresolved blocker promptly.

After a successful pull, record the resulting commit and a second Git status snapshot. This is the baseline for identifying execution-generated changes separately from changes brought in by the pull.

## 2. Verify execution and freshness

Use the repository's existing Python environment and normal script invocation. Check executable paths and installed dependencies without reading skills or their references. When necessary, inspect only executable source, machine-readable configuration, or dependency manifests to determine supported arguments, output locations, and cache behavior. Never dump credentials.

The target command is:
`python -u scripts/csp_screening.py`

Replace `python` with the verified environment interpreter if needed. Keep the repository root as the working directory. Do not invent CLI options. Use only supported output-directory settings to place generated results outside the repository when possible. If output paths are fixed, record their pre-run existence, size, modification time, and row counts; preserve existing files before any known overwrite. Keep the code's required paths intact.

Freshness requirements are strict:
- Do not use prior-run cached screening inputs, cached intermediate results, or old outputs as this run's data.
- Use only code-supported ways of obtaining fresh data or isolating non-valuation caches; never delete shared caches.
- Never pass `--no-cache` or `--refresh` to company valuation scoring, directly or through a parent command, environment setting, or subprocess.
- Do not emulate those prohibited valuation flags by deleting valuation caches or forcing a valuation refresh through another mechanism.
- Do not assume that omitting flags proves freshness. Check actual source timestamps, run provenance, or code/log evidence.
- If normal valuation execution supplies fresh data without those flags, proceed. If it would reuse prior-run valuation data and no permitted path satisfies both rules, report that exact conflict as a blocker. Never silently relax either instruction.
- Treat fresh retrieval of historical financial statements as fresh acquisition; do not confuse a statement's fiscal date with its retrieval time.

Do not use a cached result as a fallback for unavailable credentials, provider failures, or rate limits.

## 3. Run once and monitor without cron

Use Hermes's available long-running terminal/process session capability. Start one supervised process, capture stdout and stderr continuously in the run directory, retain its session identifier/PID, and obtain the actual Python exit code. If using `tee`, use Bash `pipefail` and record the Python status rather than only the logger's status.

Do not create cron jobs, scheduled tasks, or detached fire-and-forget runs. Keep the active agent session alive and monitor within the same invocation:

1. Send a startup update with repository, commit, command, run directory, and freshness verification status.
2. Record a monotonic start time and set the next reporting deadline to start + 300 seconds.
3. Poll the process/session and new log output every 15–30 seconds using bounded waits. Check process state, stage, output changes, counters, and errors.
4. At each 300-second deadline, send an actual progress message to the requesting conversation, even if nothing changed. Then advance to the next future 300-second boundary.
5. Immediately report new output files and serious errors rather than waiting for the next interval.
6. Continue until the process exits, then verify artifacts and send the final report.

The loop is a monitoring workaround, not an independent scheduler. Log entries alone are not delivered progress messages. Do not claim that updates will continue after the agent session ends. If the runtime cannot remain active or deliver messages during execution, explicitly report that limitation and the last verified process status; do not claim an unattended notification service exists.

## 4. Report evidence-based counts and paths

Use structured counters or current-run logs first. Supplement with safe, read-only parsing of current-run outputs.

Every five-minute update must contain:
- Run identifier, timestamp, elapsed time, and RUNNING / RETRYING / BLOCKED status.
- Current stage and most recent verified progress.
- Candidates processed and total, when exposed by the code.
- Rows written per output file, with change since the previous update.
- Filtered-out count and reason breakdown, when exposed by the code.
- Errors/retries separately from filtering.
- Absolute paths of newly created or updated outputs and the log.
- Current issue, recovery action, and its observed result.

Count CSV records with a CSV parser, excluding headers; do not use raw line counts for multiline CSV. Count JSON records according to the actual schema. Distinguish partial files from finalized outputs and label interim counts as provisional. For append-only files, distinguish this run's appended records from total file rows. Do not attribute unchanged pre-existing files to this run.

Use `not yet reported` or `unavailable from code` for unknown counters. Use zero only when verified. Do not infer filtered = input minus written unless both counts use the same unit and every missing record is demonstrably a filter rejection. Do not double-count repeated log summaries or overlapping rejection reasons.

Suggested progress message:

```text
CSP [run-id] | RUNNING | elapsed [duration]
Stage: [stage] | processed: [count/total or unavailable]
Rows written: [file: current-run count, delta, provisional/final]
Filtered out: [count or unavailable] | reasons: [verified breakdown]
Errors/retries: [count and issue]
Output: [absolute path, or none written yet]
Log: [absolute path]
Issue/action: [observed issue and recovery result, or none]
```

## 5. Recover without hiding failures

Investigate stalled progress using process state, logs, output timestamps, and documented-in-code timeout/retry behavior. Silence alone does not prove a hang. Report suspected stalls at the next update; keep monitoring while the process remains legitimately active.

Resolve routine working-directory, interpreter, and existing-environment activation problems. Respect provider backoff. Use bounded retries for transient failures; avoid retry loops that repeatedly restart the entire pipeline. Before retrying screening, verify the previous process exited and determine whether rerunning can duplicate or overwrite outputs. Preserve prior-attempt evidence and distinguish attempts in reports.

Do not disable checks, fabricate credentials, change permissions broadly, alter business logic, or overwrite user work. When no permitted recovery remains, report BLOCKED or FAILED with the exact failing command, sanitized error, completed stages, partial output paths, and required next action. Do not claim that a blocker is resolved until a check proves it.

## 6. Verify completion and report

After exit:
1. Record the actual process exit code, end time, duration, and final stage.
2. Verify every expected output indicated by the code or logs exists, belongs to this run, parses correctly, and has a verified row count. A zero-row result can be valid if the pipeline completed and the filtering evidence explains it.
3. Report final filtering totals only where the code provides enough evidence.
4. Record post-run Git status and review the diff for relevant code/configuration and generated-output changes, excluding skill files and their references. Distinguish pre-existing dirty files, pulled changes, and execution-generated changes.
5. Do not stage, commit, push, or clean files as part of this workflow. If a separate instruction later authorizes a commit, review the exact diff first and exclude pre-existing unrelated changes.
6. Send SUCCESS only when the process exited successfully and required artifacts passed verification. Otherwise use PARTIAL, FAILED, or BLOCKED and explain why.

Final report: status, exit code, duration, commit, freshness evidence or limitation, rows written per file, filtered counts/reasons, absolute output and log paths, issues resolved, and remaining issues. If no output was produced, say so explicitly.

Never copy old issue statuses such as "addressed for this run" into a new report without rechecking them. Pre-existing dirty files remain preserved and may remain uncleaned; report that accurately.
