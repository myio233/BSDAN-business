# ASDAN Compact Status

This page is the compact handoff/status view for the current long task.

## Canonical Read

- Last explicit git checkpoint in history is `2050c83` (`Harden CSRF and checkpoint model diagnostics`). Treat that as the last clearly accepted repo state.
- The current accepted model candidate still remains the earlier market-context baseline unless a newer candidate is explicitly accepted:
  - accepted-candidate highlights: Exschool end-to-end final share `R² ≈ 0.948824`, OBOS end-to-end final share `R² ≈ 0.327263`
- `generated_reports/model_pipeline_current_baseline/` in the current working tree is a refreshed verified artifact surface for that same candidate family, not an implicitly newer accepted candidate.
- Metric drift currently exists across four surfaces and should be read with provenance attached:
  - `2050c83` checkpoint artifact (`generated_reports/model_pipeline_current_baseline/` at `HEAD`): Exschool end-to-end final share `R² ≈ 0.94835`, OBOS end-to-end final share `R² ≈ 0.29571`
  - RUNLOG loop 16 local rerun of that older candidate on this machine: Exschool end-to-end final share `R² ≈ 0.94962`, OBOS end-to-end final share `R² ≈ 0.29333`
  - earlier accepted market-context baseline: Exschool end-to-end final share `R² ≈ 0.948824`, OBOS end-to-end final share `R² ≈ 0.327263`
  - current working-tree refreshed artifact (`generated_reports/model_pipeline_current_baseline/` now): Exschool end-to-end final share `R² ≈ 0.94844`, OBOS end-to-end final share `R² ≈ 0.32452`
- Accepted backend/engine/report hardening is the committed web/security/report work through `2050c83`. The latest loop-16/17 sequential browser reruns still exercised those paths successfully, but this doc does not promote newer unlogged worktree code as accepted.
- Newer verified local lane results beyond `2050c83` now include:
  - session/setup/final-flow lane landed
  - market-allocation bias lane landed
  - modeling provenance lane landed
  - broad regression across `test_exschool_game_modes.py + test_engine_data_fidelity.py + test_engine_report_correctness.py + test_report_payload.py + test_modeling.py + test_market_allocation.py + test_reconstruct_exschool_decisions.py + test_data_loader_provenance.py` reached `72 passed`
  - `scripts/validate_exschool_modes_playwright.py --scenario high-intensity --browser chromium` is green again

## Done

- Two selectable single-player modes exist in the frontend and backend:
  - `high-intensity`
  - `real-original`
- `real-original` mode loads the dedicated reconstructed fixed-decision workbook.
- Key engine fixes already landed for:
  - invalid default `r1` submission
  - loan repayment clamped by available cash
  - player home-city finance/material overrides
  - campaign player-state continuity
- Targeted pytest coverage around session/setup/final-flow and report-image behavior is green:
  - `pytest -q test_exschool_game_modes.py` reached `41 passed`
  - broad regression across modes + engine/report + provenance / fidelity lanes reached `72 passed`
- High-intensity Chromium browser validation is green again:
  - `./.venv/bin/python scripts/validate_exschool_modes_playwright.py --scenario high-intensity --browser chromium`
- Website-flow regressions around mode routing, restart, submitted-round resume, and report download have been hardened:
  - form and JSON mutation paths now require CSRF
  - session secret no longer depends on a tracked hardcoded fallback
  - active continue links now carry explicit `?game_id=...`
  - `/game/submit` now enforces round deadlines server-side
  - `/game/report-image` no longer accepts arbitrary client HTML
  - `/auth/email-code` invalid JSON now returns `400`
  - `/api/rounds/{round_id}/defaults` invalid round ids now return `404`
  - auth/user stores now write atomically with backup recovery
  - report-image cache now prunes expired/excess files
  - `GET /single/setup` no longer clears the active saved run
  - `real-original` now starts through explicit mode-specific setup/start routes
  - `GET /game` reopens the submitted report page using persisted full report detail
  - report download prefers a cached PNG route and no longer prefetches/generates PNGs on every report-page load
  - `/favicon.ico` now returns `204`, removing browser-console `404` noise during validation
- The save system is now visible and usable in the homepage flow:
  - multiple in-progress runs are auto-saved independently
  - the homepage can switch between in-progress runs and delete them
  - the most recent 10 completed runs can be reopened as read-only final summaries
- The image-to-table / report-extraction pipeline is now reproducible in the current repo path and environment:
  - `tesseract` installed on the machine
  - `validation_report.xlsx` rerun and all checks are `True`
- Market-model pipeline was materially improved:
  - competition-aware stock lookup
  - runtime default absorption cap `0.0`
  - signed stage-2 `CPI -> share delta`
  - current accepted model candidate remains the earlier market-context baseline
  - latest refreshed local artifacts in the working tree stay useful for provenance/comparison, but do not implicitly replace the accepted candidate

## Partial

- `real-original` data reconstruction is integrated, but should not yet be treated as “perfect ground truth for every team”.
  - the available source roster currently covers 23 teams and excludes team id `11`
- Market-model quality improved a lot, but still does not meet the user’s requested near-99% target across all stages and datasets.
- Last explicit checkpointed baseline artifacts at `2050c83` still show:
  - Exschool `cpi R² ≈ 0.99174`
  - Exschool `CPI -> unconstrained share R² ≈ 0.96228`
  - Exschool end-to-end final share `R² ≈ 0.94835`
  - OBOS `cpi R² ≈ 0.15951`
  - OBOS `CPI -> unconstrained share R² ≈ 0.34226`
  - OBOS end-to-end final share `R² ≈ 0.29571`
- Current accepted model candidate still remains the earlier market-context baseline:
  - Exschool `cpi R² ≈ 0.992186`
  - Exschool `CPI -> unconstrained share R² ≈ 0.962133`
  - Exschool end-to-end final share `R² ≈ 0.948824`
  - OBOS `cpi R² ≈ 0.228234`
  - OBOS `CPI -> unconstrained share R² ≈ 0.360615`
  - OBOS end-to-end final share `R² ≈ 0.327263`
- Latest refreshed local-artifact metrics in `generated_reports/model_pipeline_current_baseline/` are:
  - Exschool `cpi R² ≈ 0.992186`
  - Exschool `CPI -> unconstrained share R² ≈ 0.962950`
  - Exschool end-to-end final share `R² ≈ 0.948436`
  - OBOS `cpi R² ≈ 0.228234`
  - OBOS `CPI -> unconstrained share R² ≈ 0.356986`
  - OBOS end-to-end final share `R² ≈ 0.324523`
- Those refreshed artifact numbers are real, but they do not by themselves mean a newer accepted model candidate exists.
- RUNLOG loop 16 reran the older accepted candidate on this machine and got slightly different values (`Exschool` end-to-end final share `R² ≈ 0.94962`, `OBOS` end-to-end final share `R² ≈ 0.29333`), but those rerun numbers were not promoted into a dedicated artifact folder before the loop-17 refresh replaced the baseline directory.
- Final supervisor review exists as audit notes, but the whole program of residual engine issues is not fully closed.
- The save system is still homepage-centric rather than a full in-page slot manager; selection happens from home, not every screen.
- Browser validation is still only partially closed:
  - targeted pytest around report-image and final-flow behavior is green
  - `scripts/validate_exschool_modes_playwright.py --scenario high-intensity --browser chromium` is green again
  - `scripts/validate_exschool_modes_playwright.py --scenario multi-save --browser chromium` failed because the verifier followed the wrong continue link and asserted the wrong mode label
  - a fresh single `scripts/validate_exschool_modes_playwright.py --scenario real-original --browser chromium` run hit a mode-card lookup timeout
  - do not describe the full browser matrix as green again until the verifier weaknesses are fixed and the remaining scenarios are rerun cleanly

## Missing Or Still Open

- Cross-domain CPI improvements for `OBOS`, especially negative bias / `Hangzhou` / high-CPI buckets.
- Remaining engine/data issues from the long issue list still need separate verification and either fixes or explicit deferrals.
- The isolated stage-3 allocator metric can still be negative even when end-to-end final share is strong, because the runtime default uses an absorption cap of `0` and the residual adjustment target has low, noisy variance.
- The main remaining blockers are:
  - deeper CPI / share model quality work
  - explicit disposition of the remaining confirmed issue list

## Current Highest-Leverage Next Steps

1. Close the real-original source-coverage gap or explicitly ship it as a 23-team source-faithful mode.
2. Continue stage-1 CPI work for cross-domain generalization, especially `team 9` / `Hangzhou` / `r3-r4`, while trying to recover the small `Exschool` end-to-end dip.
3. Finish the remaining delivery/review cleanup after the next modeling pass.
