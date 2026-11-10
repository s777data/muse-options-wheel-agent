# Vendored dashboard engine — DO NOT EDIT

Pinned copy of ONLY `src/evaluator/` + `config/` from
`s777data/muse-business-evaluation-dashboard` (V5.3 + GAV-Code),
remote head commit `ac8deaf` (pushed 2026-11-09, verified by read-back
in the parent session). Copied 2026-11-09 from the local working copy at
~/workspace/muse-business-evaluation-dashboard, which is byte-identical
to that remote head for these paths. A true git submodule was not used
because the GitHub snapshot-push route cannot carry submodules.

Phase 5 (scripts/valuation_scorer.py) imports `src.evaluator.run.evaluate`
from this vendor tree in-process. The engine's runtime cache/output land
in `vendor/muse-business-evaluation-dashboard/output/` (gitignored).
To update: re-copy from the dashboard repo at a new pinned commit and
update this file + INVENTORY/DEVIATIONS in the same commit.
