# ASDAN Execution Plan

## Goal

Implement the approved long-run plan while keeping the repo runnable at every step.

Primary deliverables:

1. Checkpoint the current repo state in Git and GitHub.
2. Add a second single-player mode:
   - `high_intensity_competition`: current smart-fixed-opponent mode
   - `real_original_competition`: opponents use reconstructed real decisions from real reports
3. Build a verified real-data pipeline for Exschool + OBOS:
   - raw images -> validated tables/workbooks
   - structured finance/market truth datasets
4. Fix confirmed engine issues that break realism or fairness.
5. Refit and evaluate the 3-stage market model chain:
   - CPI
   - CPI -> unconstrained share
   - unconstrained share -> final share
6. Add persistent repo skills/docs so future long tasks do not depend on chat context.
7. Run full validation:
   - script tests
   - model/evaluation scripts
   - Playwright full 4-round browser tests
   - final supervisor review

## Acceptance

- Current repo checkpoint committed and pushed.
- `PLAN.md`, `RUNLOG.md`, `agent.md`, `model.md` exist and stay updated.
- Both single-player modes are selectable in UI and persisted in session.
- Real-original mode uses reconstructed real opponent decisions.
- Confirmed engine bugs are fixed or explicitly deferred with evidence.
- Real-data extraction for Exschool + OBOS is reproducible and validated.
- Stage metrics:
  - CPI model >= 99% on chosen stage metric
  - CPI -> unconstrained share >= 99%
  - unconstrained share -> final share >= 99%
  - end-to-end combined chain >= 93%
- Playwright passes real 4-round flow and report sanity checks.
- Final repo state is pushed.

## Current Status

- Done:
  - checkpoint commit/push
  - persistent repo docs
  - two-mode single-player split (`high-intensity`, `real-original`)
  - dedicated real-original fixed-decision workbook generation
  - player-city runtime parameter override for interest/material costs
  - repayment clamp by available cash
  - targeted regression tests
  - model baseline evaluation artifacts
  - double-mode Playwright validation
  - website flow hardening for restart / mode routing / submitted-round resume / report-download cache
  - multi-save in-progress persistence with home-page save picker and read-only completed-history reopen
  - OCR-backed image-to-table pipeline rerun successfully in the current environment
- Remaining:
  - final supervisor review
  - final integration commit and push
  - deeper market-model optimization beyond the current blended baseline
  - CPI cross-domain generalization improvements, especially for `OBOS` / `Hangzhou`
  - close or explicitly defer the remaining engine/data issue list
  - continue the remaining confirmed-safe web/security hardening items in controlled batches

## Resolved Earlier In This Run

- Invalid default `r1` submission because all default agents started at zero.
- Loan repayment exceeding available cash.
- Campaign path not respecting city-sensitive finance/material parameters.
- Real-original mode fallback / routing regressions.
- Submitted-round resume and duplicate submit-flow regressions.
- Report-download path doing slow full repost instead of cached image fetch.
- Server-side round deadline enforcement.
- Invalid JSON / invalid round route failures.
- Report-image arbitrary client-HTML input.
- Atomic auth/user store writes with backup recovery.
- Report page eager PNG generation on load.
- Browser-console favicon `404` noise during validation.

## Still Open Or Needing Explicit Disposition

- Opponent later-round starting state fallback realism.
- Storage capacity continuity mismatch.
- Workforce planning duplication in affordability and finance paths.
- Salary-anchor drift from current campaign state.
- Export/report-image fragility and hardcoded page heights.
- Market-model cross-domain weakness, especially stage-1 CPI on `OBOS`.

## Current Web Validation State

- Current browser validation now passes when run as split scenarios:
  - `./.venv/bin/python scripts/validate_exschool_modes_playwright.py --scenario high-intensity`
  - `./.venv/bin/python scripts/validate_exschool_modes_playwright.py --scenario real-original`
  - `./.venv/bin/python scripts/validate_exschool_modes_playwright.py --scenario multi-save`
- The runner now uses the full Playwright Chromium channel in this environment instead of the unstable headless-shell default.
- Current caution:
  - a fresh rerun in this session was interrupted because the Chromium browser process crashed under host CPU / memory pressure
  - do not reinterpret that crash as an application regression without a lower-load rerun
- Current verified safety/regression test matrix:
  - `./.venv/bin/python -m pytest test_exschool_game_modes.py test_modeling.py`
  - result: `32 passed`

## Working Rules

- Preserve runnable state after each meaningful change.
- Do not overlap file ownership across subagents.
- Every loop updates `RUNLOG.md` with:
  1. current phase goal
  2. what changed
  3. how it was verified
  4. next step
- If two consecutive loops show no substantive progress, stop and reread this file.
- If two options can be explored in parallel, list both and pick the steadier one.
- Do not do unverified large rewrites just to look complete.

## File Ownership

- Main agent:
  - `PLAN.md`
  - `RUNLOG.md`
  - `agent.md`
  - `model.md`
  - integration and final validation
- Worker Data:
  - data extraction scripts / generated validation artifacts / data skills
- Worker Backend:
  - backend engine / data-loader / session plumbing
- Worker Model:
  - model pipeline / evaluation scripts
- Worker Frontend:
  - templates / static / Playwright
- Worker Docs:
  - `README.md` and repo-local skills
- Supervisor:
  - audit only unless explicitly redirected

## Phase Order

1. Checkpoint commit/push
2. Persistent docs
3. Parallel implementation
4. Integration
5. Full test matrix
6. Final review

## Current Modeling Priority

- Keep the 3-stage chain unchanged.
- Treat allocator-only tuning as secondary for now.
- Primary next move:
  - keep the validated small `GBR + RF` CPI blend as the current candidate
  - improve stage-1 CPI generalization across competitions / markets
  - reduce the remaining `OBOS` negative CPI bias
  - specifically inspect `Hangzhou` and high-CPI underprediction buckets
  - avoid heavy retraining branches that improve `OBOS` only by materially degrading `Exschool`
- Explicitly rejected in the latest loop:
  - reducing `PROXY_CPI_TRAIN_WEIGHT`
  - a broader stage-2 share-generalization patch that slightly improved `Exschool` but worsened `OBOS`
