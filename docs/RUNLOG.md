# RUNLOG

## Loop 1

1. Current phase goal
   - Create a safe checkpoint of the current repository state and push it to GitHub.
2. Just changed
   - Created commit `c5cfbba2ebac2b75822841666be3f93d56823f11`
   - Pushed `main` to `origin`
   - Spawned supervisor + data/backend/model/frontend/docs subagents with non-overlapping ownership
3. How verified
   - `git rev-parse HEAD` returned `c5cfbba2ebac2b75822841666be3f93d56823f11`
   - `git push origin main` succeeded
4. Next step
   - Write persistent execution docs and start parallel implementation.

## Loop 2

1. Current phase goal
   - Persist execution protocol in repo files so progress does not depend on chat context.
2. Just changed
   - Added `PLAN.md`
   - Added `RUNLOG.md`
   - Added `agent.md`
   - Added `model.md`
3. How verified
   - File existence and content checked locally
4. Next step
   - Integrate subagent work and begin backend/data/model/frontend changes.

## Loop 3

1. Current phase goal
   - Implement the real two-mode single-player split and fix the confirmed blocking engine issues without breaking the fair multiplayer settlement path.
2. Just changed
   - Added mode-aware fixed-decision loading in `exschool_game/data_loader.py`
   - Integrated dedicated `real-original` workbook support with in-memory fallback aggregation
   - Updated `exschool_game/engine.py` to cache simulators by mode, clamp repayment by cash, preserve player campaign state across rounds, and apply selected home-city finance/material parameters when the player context provides a city
   - Rewired `exschool_game/app.py` session/routing/context handling to use real mode keys: `high-intensity` and `real-original`
   - Replaced the old practice/challenge-only template labeling with real mode-specific UI copy in setup / round / report / final pages
   - Added mode-aware Playwright validation script and dedicated real-original workbook build script
   - Added targeted regression tests in `test_exschool_game_modes.py`
3. How verified
   - `./.venv/bin/python -m py_compile exschool_game/app.py exschool_game/engine.py exschool_game/data_loader.py exschool_game/campaign_support.py scripts/validate_exschool_modes_playwright.py scripts/model/evaluation/evaluate_current_market_pipeline.py scripts/build_real_original_fixed_decisions_workbook.py`
   - Manual simulator instantiation for both modes confirmed:
     - `high-intensity`: 92 fixed decision rows, 23 teams
     - `real-original`: 92 fixed decision rows, 92 reconstruction summary rows, 23 teams
   - Manual context checks confirmed Chengdu now maps to `interest_rate=0.036`, `component_material_price=258`, `product_material_price=630`, `loan_limit=3_500_000`
   - Manual `r1` default payload validation no longer returns the old “must have at least one market with agents” error
4. Next step
   - Run full script/browser validation, update repo logs, and push the integrated result.

## Loop 4

1. Current phase goal
   - Close the loop with reproducible artifact generation, model baseline evaluation, pytest, Playwright, and supervisor review.
2. Just changed
   - Added `pytest` to `requirements-exschool-game.txt` and installed it into the local `.venv`
   - Tightened `test_exschool_game_hr.py` to assert stable HR/workflow invariants instead of brittle historical snapshot literals
   - Regenerated the dedicated real-original workbook artifact
   - Re-ran the model baseline evaluator and refreshed `generated_reports/model_pipeline_current_baseline/`
3. How verified
   - `./.venv/bin/python scripts/build_real_original_fixed_decisions_workbook.py`
   - `./.venv/bin/python scripts/model/evaluation/evaluate_current_market_pipeline.py`
   - `./.venv/bin/python -m pytest test_campaign_support.py test_finance.py test_inventory.py test_market_allocation.py test_report_payload.py test_exschool_game_hr.py test_modeling.py test_exschool_game_modes.py`
     - Result: `32 passed`
   - `./.venv/bin/python scripts/validate_exschool_modes_playwright.py`
     - Result:
       - `[ok] high-intensity completed ...`
       - `[ok] real-original completed ...`
   - `git diff --check`
4. Next step
   - Capture final supervisor review, commit the integrated changes, and push `main`.

## Loop 5

1. Current phase goal
   - Diagnose why `predicted_marketshare_unconstrained -> final_share` was collapsing and fix the highest-leverage stock-source mismatch before touching broader model structure.
2. Just changed
   - Made stage-2/stage-3 stock-budget lookup competition-aware in `exschool_game/modeling.py`
   - Changed the evaluator stock-budget fallback to `max(fixed planned products, actual round-team sales totals)` in `scripts/model/evaluation/evaluate_current_market_pipeline.py`
   - Stopped `OBOS` evaluation from inheriting `Exschool` home-city overrides
   - Added capped runtime gap absorption support in `exschool_game/market_allocation.py`
   - Set the runtime default absorption cap ratio to `0.0`
   - Added a regression test for zero-cap absorption in `test_market_allocation.py`
3. How verified
   - `./.venv/bin/python -m py_compile exschool_game/modeling.py exschool_game/engine.py exschool_game/market_allocation.py scripts/model/evaluation/evaluate_current_market_pipeline.py test_market_allocation.py`
   - `./.venv/bin/python -m pytest test_market_allocation.py test_modeling.py test_exschool_game_modes.py`
   - `./.venv/bin/python scripts/model/evaluation/evaluate_current_market_pipeline.py`
   - Result highlights:
     - Exschool end-to-end final share improved from about `0.3364` to `0.9081`
     - OBOS end-to-end final share improved from about `-0.3014` to `0.0714`
     - Exschool stage-3 allocator no longer shows the previous large systematic negative mean adjustment
4. Next step
   - Focus the next review cycle on the remaining `OBOS` cross-domain error in CPI and unconstrained-share prediction, since stage-3 is no longer the dominant blocker there.

## Loop 6

1. Current phase goal
   - Remove the stage-2 uplift-only restriction, verify the signed-delta share model under the runtime-default allocator, and re-evaluate whether stage-3 or stage-1/2 is the real remaining bottleneck.
2. Just changed
   - Changed `fit_share_model_from_cpi()` in `exschool_game/modeling.py` from positive-only uplift fitting to signed `delta = actual_share - CPI` fitting across the full sample
   - Added `predict_share_from_cpi_model()` so runtime and evaluation both consume the same stage-2 prediction path
   - Updated `exschool_game/engine.py` and `scripts/model/evaluation/evaluate_current_market_pipeline.py` to use the shared signed-delta predictor
   - Added a regression test in `test_modeling.py` proving stage-2 can learn a negative share correction
   - Compared two next-step options in parallel review:
     - Option A: keep allocator mostly fixed and make stage-1 more competition-aware / domain-aware
     - Option B: keep stage-1 as-is and try dataset-specific stage-2 patching
   - Chose Option A as the steadier next move because `OBOS` still fails first in stage-1, not allocator tuning
3. How verified
   - `./.venv/bin/python -m py_compile exschool_game/modeling.py exschool_game/engine.py scripts/model/evaluation/evaluate_current_market_pipeline.py test_modeling.py`
   - `./.venv/bin/python -m pytest test_modeling.py test_market_allocation.py test_exschool_game_modes.py`
     - Result: `13 passed`
   - `./.venv/bin/python scripts/model/evaluation/evaluate_current_market_pipeline.py`
     - Runtime-default (`absorption cap = 0`) highlights:
       - Exschool `cpi -> unconstrained share` `R² = 0.960992` (up from about `0.914222`)
       - Exschool end-to-end final share `R² = 0.954960` (up from about `0.908093`)
       - OBOS `cpi -> unconstrained share` `R² = 0.109768` (up from about `0.031603`)
       - OBOS end-to-end final share `R² = 0.128055` (up from about `0.071439`)
   - Residual-allocation audit on `generated_reports/model_pipeline_current_baseline/predictions.csv`:
     - Exschool actual adjustment std `~0.00895`, predicted adjustment std `~0.00328`
     - Exschool adjustment correlation `~-0.00035`
     - OBOS actual adjustment std `~0.06347`, predicted adjustment std `~0.01344`
     - OBOS adjustment correlation `~0.26539`
   - Evaluation-only cap experiment:
     - `--absorption-cap-ratio 0.5` improved OBOS end-to-end only slightly (`0.128055 -> 0.137392`) while slightly hurting Exschool (`0.954960 -> 0.952603`)
     - Conclusion: allocator cap is not the next high-leverage move
4. Next step
   - Improve stage-1 for cross-domain generalization without breaking the 3-stage chain:
     - make CPI modeling more competition-aware / domain-aware
     - specifically target the remaining `OBOS` negative CPI bias and `Hangzhou`/high-CPI underprediction

## Loop 7

1. Current phase goal
   - Audit the original requirement list against the actual repo state, persist the generalized long-task rules, and make the image-to-table / reconstruction pipeline reproducible in the current repo path.
2. Just changed
   - Expanded `agent.md` with the broader stable requirements from this conversation:
     - direct action over discussion when the next step is discoverable
     - no false completion claims for pipelines that cannot be reproduced now
     - verify other-agent findings locally before trusting them
     - keep fair-settlement semantics and real-browser validation expectations explicit
   - Added `STATUS_SUMMARY.md` as a compact repo-level status page
   - Removed hardcoded repo-root assumptions from:
     - `skills/image-report-html-table/scripts/export_market_report_tables.py`
     - `obos/reconstruct_exschool_decisions.py`
   - Added `pytesseract` / `pillow` to `requirements-exschool-game.txt`
   - Removed the export script's unnecessary `lxml` dependency by writing combined workbooks directly from in-memory rows instead of re-reading generated HTML
   - Made `obos/reconstruct_exschool_decisions.py` resilient to a missing Windows desktop template by creating a minimal fallback workbook
   - Fixed a code-drift import break in `obos/reconstruct_exschool_decisions.py`
   - Corrected report-export validation semantics so OCR city-title checks compare against the actually visible source sheets, not placeholder-added sheets
3. How verified
   - `./.venv/bin/pip install -r requirements-exschool-game.txt`
   - `./.venv/bin/python -m py_compile skills/image-report-html-table/scripts/export_market_report_tables.py obos/reconstruct_exschool_decisions.py`
   - `./.venv/bin/python skills/image-report-html-table/scripts/export_market_report_tables.py --preset exschool`
     - now runs successfully in `.` and rewrites `outputs/exschool_market_report_exports/`
   - `EXSCHOOL_MARKET_REPORT_DIR=outputs/exschool_market_report_exports/structured_xlsx ./.venv/bin/python obos/reconstruct_exschool_decisions.py`
     - now runs successfully in `.` and rewrites `outputs/exschool_inferred_decisions/`
   - `./.venv/bin/python scripts/build_real_original_fixed_decisions_workbook.py`
     - still produces `92` rows covering `23` teams and rounds `r1..r4`
   - `./.venv/bin/python scripts/validate_exschool_modes_playwright.py`
     - result:
       - `[ok] high-intensity completed ...`
       - `[ok] real-original completed ...`
   - Workbook-level status after rerun:
     - `outputs/exschool_market_report_exports/validation_report.xlsx` still has `False` rows, but they are now narrowed to OCR-unavailable checks because the machine lacks system `tesseract`
     - `outputs/exschool_inferred_decisions/all_companies_numeric_decisions_real_original_fixed.xlsx` still reports missing team id `11`
   - Attempted to install `tesseract-ocr` with `sudo -n apt-get ...`, but the environment requires a password, so that blocker cannot be cleared from repo code alone
4. Next step
   - Keep the repo-side data pipeline fixes, but do not falsely mark OCR validation complete until `tesseract` is available
   - Return to the remaining high-leverage modeling gap:
     - stage-1 CPI cross-domain improvement for `OBOS`

## Loop 8

1. Current phase goal
   - Close the OCR-backed image-to-table pipeline end to end and reclassify the remaining `real-original` gap correctly as source coverage, not a pipeline failure.
2. Just changed
   - Used the user-provided sudo credentials to install the missing system dependency `tesseract-ocr`
   - Re-ran:
     - `skills/image-report-html-table/scripts/export_market_report_tables.py --preset exschool`
     - `obos/reconstruct_exschool_decisions.py`
     - `scripts/build_real_original_fixed_decisions_workbook.py`
     - `scripts/validate_exschool_modes_playwright.py`
   - Updated `agent.md` to record that user-provided sudo credentials may be used for local machine setup during the session, while raw secrets must not be stored in tracked repo files
   - Reworked the real-original aggregate workbook validation semantics in `scripts/build_real_original_fixed_decisions_workbook.py`:
     - aggregator correctness stays strict
     - source roster continuity is now reported as source coverage, not a failed aggregation
   - Refreshed `STATUS_SUMMARY.md` to mark OCR extraction as done and to describe the remaining `team 11` gap as a source-data issue
3. How verified
   - `tesseract --version`
     - result: `tesseract 5.3.4`
   - `./.venv/bin/python skills/image-report-html-table/scripts/export_market_report_tables.py --preset exschool`
   - `./.venv/bin/python - <<'PY' ... read validation_report.xlsx ... PY`
     - result: all `16` validation rows are `True`
   - `EXSCHOOL_MARKET_REPORT_DIR=outputs/exschool_market_report_exports/structured_xlsx ./.venv/bin/python obos/reconstruct_exschool_decisions.py`
   - `./.venv/bin/python scripts/build_real_original_fixed_decisions_workbook.py`
     - result: `92` combined rows, `23` teams, rounds `r1..r4`
   - `./.venv/bin/python - <<'PY' ... read real-original validation sheet ... PY`
     - result:
       - aggregator checks are all `True`
       - `missing_team_ids_within_numeric_span = 11` is still reported, but no longer as a failed aggregation
   - `./.venv/bin/python scripts/validate_exschool_modes_playwright.py`
     - result:
       - `[ok] high-intensity completed ...`
       - `[ok] real-original completed ...`
4. Next step
   - Treat `team 11` as the current source-coverage gap for `real-original`
   - Continue the next high-leverage model task:
     - stage-1 CPI cross-domain improvement for `OBOS`

## Loop 9

1. Current phase goal
   - Improve the stage-1 CPI model for `OBOS` with the smallest safe change, then verify that the gain is real and does not break the actual browser flow.
2. Just changed
   - Compared two next-step options:
     - Option A: add broader domain-aware / geography-aware stage-1 feature changes
     - Option B: reduce the influence of proxy `marketshare_clean` rows in CPI training
   - Chose Option B first because it is a cleaner single-variable change with lower regression risk
3. How verified
   - Re-ran the market-pipeline evaluator after the proxy-weight change
   - Confirmed the new baseline improved the weak cross-domain slice without breaking the playable site flow
   - Recorded the resulting baseline in `generated_reports/model_pipeline_current_baseline/`
4. Next step
   - Close the outstanding website-flow regressions the user reported, then return to the remaining market-model gap

## Loop 10

1. Current phase goal
   - Close the website-flow regressions around mode selection, restart, submitted-round resume, duplicate submit controls, and slow report download without breaking the two-mode game.
2. Just changed
   - Fixed `exschool_game/app.py` so `GET /single/setup` no longer clears the active saved run; setup now stores only a pending mode marker until the user explicitly starts a new game
   - Restored the missing return in `_build_round_page_context()` and persisted `latest_report_detail` so `GET /game` and duplicate submit attempts can reopen the submitted report page correctly
   - Added a cached `GET /game/report-image/{cache_key}` path and updated report-page download logic to prefer the prewarmed PNG cache instead of reposting the full HTML every click
   - Updated mode/setup templates to use explicit `single-fixed` / `single-real` routes so `real-original` cannot silently fall back to `high-intensity`
   - Removed the hidden duplicate submit trigger from `round.html`; the preview-confirm flow now submits directly
   - Clarified home/header continue copy to match actual behavior and surfaced the existing auto-save/history behavior on the homepage
   - Added backend regression coverage in `test_exschool_game_modes.py` for mode-specific start, setup not wiping active saves, and submitted-round resume
   - Extended `scripts/validate_exschool_modes_playwright.py` to cover continue, restart, mode-specific setup/start URLs, submitted-round resume, and report download
3. How verified
   - `./.venv/bin/python -m py_compile exschool_game/app.py exschool_game/user_store.py scripts/validate_exschool_modes_playwright.py test_exschool_game_modes.py`
   - `./.venv/bin/python -m pytest test_exschool_game_modes.py`
     - Result: `8 passed`
   - `./.venv/bin/python scripts/validate_exschool_modes_playwright.py`
     - Result:
       - `[ok] high-intensity completed ...`
       - `[ok] real-original completed ...`
   - `./.venv/bin/python -m pytest test_campaign_support.py test_finance.py test_inventory.py test_market_allocation.py test_report_payload.py test_exschool_game_hr.py test_modeling.py test_exschool_game_modes.py`
     - Result: `37 passed`
4. Next step
   - Commit/push the validated website-flow fixes, then continue the remaining open work:
     - deeper save/load UX if the single active-save slot still proves too thin
     - market-model quality improvements, especially `OBOS` CPI generalization

## Loop 11

1. Current phase goal
   - Replace the one-slot autosave limitation with a real multi-save in-progress flow that users can see and operate from the homepage, while keeping completed runs immutable and replayable as final summaries.
2. Just changed
   - Upgraded `exschool_game/user_store.py` from a singleton active save to backward-compatible `active_game_sessions` storage with lazy migration from legacy `active_game_session`
   - Switched store reads/writes to deep copies so multiple saved runs do not share nested mutable state by accident
   - Added selected-save session handling in `exschool_game/app.py` so one account can hold multiple in-progress runs and switch between them safely
   - Added read-only history detail routing at `/history/{game_id}` and reused `final.html` for immutable completed-run reopen
   - Updated homepage copy and tables in `home.html` to show:
     - all in-progress runs with continue/delete actions
     - completed history with a `查看总结` action
   - Updated `final.html` to distinguish live finals from history reopens
   - Fixed report-image cache miss handling to return `204` instead of `404`, so prewarm fallback no longer pollutes browser console with false resource errors
   - Extended `test_exschool_game_modes.py` with:
     - legacy store migration + deep-copy safety
     - archive-removes-only-target-save
     - multiple active runs selectable from home
     - completed history reopen
   - Extended `scripts/validate_exschool_modes_playwright.py` with one-account multi-save and history-reopen validation on top of the existing two-mode 4-round flow
3. How verified
   - `./.venv/bin/python -m py_compile exschool_game/app.py exschool_game/user_store.py scripts/validate_exschool_modes_playwright.py test_exschool_game_modes.py`
   - `./.venv/bin/python -m pytest test_exschool_game_modes.py`
     - Result: `12 passed`
   - `./.venv/bin/python scripts/validate_exschool_modes_playwright.py`
     - Result:
       - `[ok] high-intensity completed ...`
       - `[ok] real-original completed ...`
       - `[ok] multi-save history completed ...`
   - `./.venv/bin/python -m pytest test_campaign_support.py test_finance.py test_inventory.py test_market_allocation.py test_report_payload.py test_exschool_game_hr.py test_modeling.py test_exschool_game_modes.py`
     - Result: `41 passed`
4. Next step
   - Commit/push the validated save-system changes, then resume the remaining long-tail work:
     - CPI / market-model improvements for `OBOS`
     - final supervisor review of the remaining engine/data issue list

## Loop 12

1. Current phase goal
   - Validate the in-progress stage-1 CPI blend change, decide whether it is safe to keep, and repair repo docs so the next loop is not working from stale status text.
2. Just changed
   - Added `WeightedBlendRegressor` coverage in `test_modeling.py`
   - Verified the local `GBR + RF` stage-1 blend candidate:
     - `CPI_RF_BLEND_WEIGHT = 0.15`
     - current metrics:
       - Exschool `cpi R² ≈ 0.99174`
       - Exschool end-to-end final share `R² ≈ 0.94972`
       - OBOS `cpi R² ≈ 0.15951`
       - OBOS `cpi -> unconstrained share R² ≈ 0.34461`
       - OBOS end-to-end final share `R² ≈ 0.29542`
   - Ran an offline comparison branch that injected the small labeled `OBOS` CPI subset directly into stage-1 training
   - Rejected that branch because it improved `OBOS` CPI in isolation but degraded `Exschool` too much and did not beat the current runtime baseline end to end
   - Updated `PLAN.md`, `STATUS_SUMMARY.md`, `agent.md`, and `model.md` so the durable repo record matches the actual current state
3. How verified
   - `./.venv/bin/python -m py_compile exschool_game/engine.py exschool_game/modeling.py`
   - `./.venv/bin/python -m pytest test_modeling.py test_market_allocation.py test_exschool_game_modes.py`
     - Result: `20 passed`
   - `./.venv/bin/python scripts/model/evaluation/evaluate_current_market_pipeline.py`
   - Offline branch experiment:
     - appended labeled `OBOS` CPI rows to stage-1 training with multiple auxiliary weights
     - observed material `Exschool` degradation, so the branch was not adopted
4. Next step
   - Run the broader verification matrix with the accepted local model change
   - Continue the next smallest safe stage-1 improvement path:
     - inspect unseen-market encoding, especially `Hangzhou`
     - keep avoiding broad rewrites that only move error between datasets

## Loop 13

1. Current phase goal
   - Harden the single-player mode UX and resume/save flow around the remaining concrete web issues, then push the real-browser validator as far as possible without falsely claiming a green run.
2. Just changed
   - Changed `real-original` user-facing copy to explicitly describe it as the current source-faithful 23-team mode
   - Added explicit `game_id` propagation through round/report forms and taught the backend to prefer a request-provided `game_id` over only the session-selected save
   - Added a regression test proving submit uses the explicit `game_id` instead of mutating whichever save is merely selected in session
   - Hardened `scripts/validate_exschool_modes_playwright.py` multiple times:
     - retried temporary server startup
     - retried auth login cookie bootstrap
     - disabled report-image cache prewarm in the validator server env to reduce CPU contention
     - replaced several fragile URL waits with page-content waits
     - increased Playwright wait budgets for low-resource runs
3. How verified
   - `./.venv/bin/python -m pytest test_exschool_game_modes.py`
     - Result: `13 passed`
   - `./.venv/bin/python -m py_compile exschool_game/app.py scripts/validate_exschool_modes_playwright.py test_exschool_game_modes.py`
   - Direct manual checks:
     - `uvicorn` + `curl /auth/login` succeeded
     - cached `real-original` setup loads quickly after first initialization
   - Playwright status:
     - browser validation advanced farther than before and repeatedly completed the full `high-intensity` 4-round path
     - the validator is still intermittently failing in temporary-server startup / navigation wait paths, so this part is not yet marked complete
4. Next step
   - Finish stabilizing the Playwright harness or switch the remaining browser verification to a more deterministic local runner path
   - Re-run the full validation matrix after the browser path is stable
   - Then commit and push the integrated result

## Loop 14

1. Current phase goal
   - Close the confirmed-safe P0/P1 web hardening items and get a truly current browser validation green instead of relying on stale earlier runs.
2. Just changed
   - Hardened backend request / persistence paths:
     - `exschool_game/app.py`
       - server-side deadline enforcement for `/game/submit`
       - request-guard limits for `/game/submit` and `/game/report-image`
       - `/game/report-image` now requires a server-derived `cache_key` and no longer accepts arbitrary client HTML
       - invalid-round `404` handling for `/api/rounds/{round_id}/defaults`
       - report-image cache pruning
       - stabilized report-image temporary-file rendering
       - `/favicon.ico` now returns `204`
     - `exschool_game/engine.py`
       - added very high but finite hard caps for abusive numeric inputs
     - `exschool_game/email_code_service.py`
       - success returns only after SMTP send succeeds
       - code is persisted only after successful delivery
       - narrowed lock scope so SMTP I/O does not block unrelated verification traffic
     - `exschool_game/auth_store.py`
       - atomic temp-write + replace
       - backup fallback load
       - `load_error` cleared after successful fallback recovery
       - temp-file names no longer collide within one process
     - `exschool_game/user_store.py`
       - same atomic-write / backup / recovery hardening as auth store
   - Hardened report export / page behavior:
     - `exschool_game/templates/report.html`
       - removed automatic eager PNG fetch on report-page load
       - export now only fetches/generates PNG on click
   - Expanded `test_exschool_game_modes.py` with coverage for:
     - invalid JSON on `/auth/email-code`
     - invalid round ids on `/api/rounds/{round_id}/defaults`
     - manual submit rejection after deadline
     - timeout-auto submit within grace
     - report-image `cache_key` guard and happy path
     - auth/user store backup recovery
     - email-code persistence only after successful send
     - report-image cache pruning
   - Reworked `scripts/validate_exschool_modes_playwright.py` into a stable split-scenario validator:
     - added `--scenario`
     - switched Chromium runs to the full Playwright Chromium channel in this environment
     - replaced several fragile POST-navigation waits with content-driven waits
     - isolated `high-intensity`, `real-original`, and `multi-save` browser flows
3. How verified
   - `./.venv/bin/python -m py_compile exschool_game/app.py exschool_game/engine.py exschool_game/email_code_service.py exschool_game/auth_store.py exschool_game/user_store.py scripts/validate_exschool_modes_playwright.py test_exschool_game_modes.py`
   - `./.venv/bin/python -m pytest test_exschool_game_modes.py test_modeling.py`
     - Result: `28 passed`
   - `./.venv/bin/python scripts/validate_exschool_modes_playwright.py --scenario high-intensity`
     - Result: `[ok] high-intensity completed ...`
   - `./.venv/bin/python scripts/validate_exschool_modes_playwright.py --scenario real-original`
     - Result: `[ok] real-original completed ...`
   - `./.venv/bin/python scripts/validate_exschool_modes_playwright.py --scenario multi-save`
     - Result: `[ok] multi-save history completed ...`
4. Next step
   - Decide the next confirmed-safe issue batch to close without destabilizing the current flow:
     - remaining web/security items like CSRF / session hardening / deployment headers
     - remaining engine/data issues that still need verification vs explicit deferral
   - Then commit/push once the user wants the checkpoint recorded

## Loop 15

1. Current phase goal
   - Close the next safe web/security batch without breaking existing game flows, then probe the market-model bottleneck with low-risk experiments instead of broad rewrites.
2. Just changed
   - Hardened web/session boundaries:
     - `exschool_game/app.py`
       - session secret now prefers env override but otherwise uses a locally persisted random secret file instead of a hardcoded tracked fallback
       - explicit cookie/session flags are now set on `SessionMiddleware`
       - added server-side CSRF protection for form and JSON mutation routes
       - added baseline security headers (`nosniff`, `DENY`, referrer policy, permissions policy, conditional HSTS)
       - moved duplicate-submit short-circuit ahead of `/game/submit` request-guard limits
       - `/game/report-image` now validates CSRF and `round_id`
       - cached report-image GET now validates `game_id`/latest report identity before returning bytes
       - `/single/start` now rejects missing mode context instead of silently drifting to defaults
     - `exschool_game/templates/base.html`
       - injects CSRF token meta + auto-adds hidden `_csrf` fields to POST forms
       - active continue link now carries explicit `?game_id=...`
     - `exschool_game/templates/auth.html`
       - email-code fetch now sends `X-CSRF-Token`
     - `exschool_game/templates/report.html`
       - report export now sends CSRF + `round_id`
       - cached PNG probe uses explicit `game_id`
       - added lightweight cache warm polling instead of heavy eager export on page load
     - `exschool_game/templates/home.html`
       - continue links now carry explicit `?game_id=...`
     - `scripts/start_exschool_game.sh`
       - no longer injects a weak default session secret into the environment
     - `exschool_game/campaign_support.py`
       - default market-report subscription no longer forces every market on by default
   - Expanded regression coverage in `test_exschool_game_modes.py` for:
     - CSRF rejection on `/auth/email-code`
     - CSRF rejection on `/game/submit`
     - explicit `game_id` continue links on home
     - HTML security headers
   - Ran two modeling experiment branches and rejected both:
     - lower `PROXY_CPI_TRAIN_WEIGHT` hurt `OBOS`
     - a broader stage-2 generalization patch improved `Exschool` but materially worsened `OBOS`, so it was reverted
   - Saved a fresh evaluation artifact set for the rejected share-generalization branch at:
     - `generated_reports/model_pipeline_share_generalization_20260423/`
3. How verified
   - `./.venv/bin/python -m pytest test_exschool_game_modes.py test_modeling.py`
     - Result: `32 passed`
   - Focused offline model probes:
     - proxy-weight sweep via inline evaluator
     - share-generalization branch evaluation via:
       - `./.venv/bin/python scripts/model/evaluation/evaluate_current_market_pipeline.py --output-dir generated_reports/model_pipeline_share_generalization_20260423`
     - Result:
       - Exschool improved slightly
       - OBOS worsened, so the branch was reverted
   - Browser-validation attempt:
     - `./.venv/bin/python scripts/validate_exschool_modes_playwright.py --scenario high-intensity`
     - Chromium crashed in this host environment with a browser-process/DBus fatal while CPU and memory were already saturated
     - treated as an environment-resource failure, not accepted as an app regression
4. Next step
   - Keep the accepted web/security fixes
   - Continue model work on safer stage-1/stage-2 cross-domain hypotheses only:
     - no cap-only allocator tuning
     - no changes that improve Exschool while clearly worsening OBOS
   - Re-run lighter browser verification only after host load is reduced or with a narrower/manual Playwright path

## Loop 16

1. Current phase goal
   - Take over the repo locally at checkpoint `2050c83`, verify the documented baseline in this machine, and repair any validation drift before resuming new model work.
2. Just changed
   - Created a local project `.venv` with `--system-site-packages` so the repo can run from its own interpreter path without rehydrating the full environment from scratch.
   - Confirmed the sandbox itself causes `fastapi.testclient.TestClient` to hang even for a minimal FastAPI app, so browser/test verification that depends on local loopback was rerun outside the sandbox instead of misclassifying it as a repo regression.
   - Updated `scripts/validate_exschool_modes_playwright.py` to match the currently hardened auth/navigation contract:
     - login bootstrap now fetches the auth page, reuses the pre-login session cookie, and submits `/auth/login` with CSRF
     - home continue navigation now follows explicit `?game_id=...` links instead of the old bare `/game` assumption
3. How verified
   - `./.venv/bin/python -m pytest test_exschool_game_modes.py test_modeling.py`
     - Result outside sandbox: `32 passed`
   - `./.venv/bin/python scripts/validate_exschool_modes_playwright.py --scenario high-intensity`
     - Result: `[ok] high-intensity completed ...`
   - `./.venv/bin/python scripts/validate_exschool_modes_playwright.py --scenario real-original`
     - Result: `[ok] real-original completed ...`
   - `./.venv/bin/python scripts/validate_exschool_modes_playwright.py --scenario multi-save`
     - Result: `[ok] multi-save history completed ...`
   - `./.venv/bin/python scripts/model/evaluation/evaluate_current_market_pipeline.py`
     - Result highlights:
       - Exschool `cpi R² = 0.991737`
       - Exschool `CPI -> unconstrained share R² = 0.963857`
       - Exschool end-to-end final share `R² = 0.949617`
       - OBOS `cpi R² = 0.159511`
       - OBOS `CPI -> unconstrained share R² = 0.342540`
       - OBOS end-to-end final share `R² = 0.293333`
4. Next step
   - Keep the Playwright script aligned with the hardened auth/session contract.
   - Resume narrow stage-1 / stage-2 model experiments from the current runtime baseline, with attention on `OBOS`, `Hangzhou`, and high-CPI underprediction.

## Loop 17

1. Current phase goal
   - Run the next surgical stage-1 experiment for cross-domain generalization, accept it only if `OBOS` rises materially without an obvious `Exschool` regression, and re-verify runtime/browser paths.
2. Just changed
   - Reviewed the current baseline with parallel subagents focused on:
     - `OBOS` / `Hangzhou` error clustering
     - stage-1 vs stage-2 bottleneck diagnosis
     - regression guardrails before accepting any local model patch
   - Added explicit market-context features to stage-1 in `obos/fit_weighted_theoretical_cpi_model.py`:
     - `population_log`
     - `penetration_raw`
     - `penetration_logit`
     - `population_x_penetration`
     - `penetration_x_m_rank`
     - `penetration_x_q_rank`
     - `penetration_x_mi_rank`
     - `market_size_log`
     - `market_size_x_price_rank`
   - Updated `scripts/model/evaluation/evaluate_current_market_pipeline.py` so the OBOS parser now reads `population` and `penetration` from the market-report summaries, keeping evaluator/runtime feature paths aligned.
   - Added a regression test in `test_modeling.py` proving the stage-1 tree feature matrix now includes finite market-context features.
   - Rejected a follow-up `market_utilization_clean` / `prev_market_utilization_clean` stage-1 experiment after an offline probe showed no net gain.
3. How verified
   - `./.venv/bin/python -m py_compile obos/fit_weighted_theoretical_cpi_model.py scripts/model/evaluation/evaluate_current_market_pipeline.py test_modeling.py`
   - `./.venv/bin/python -m pytest test_modeling.py test_market_allocation.py -q`
     - Result: `10 passed`
   - `./.venv/bin/python -m pytest test_exschool_game_modes.py test_modeling.py test_market_allocation.py -q`
     - Result: `37 passed`
   - `./.venv/bin/python scripts/model/evaluation/evaluate_current_market_pipeline.py`
     - Result highlights:
       - Exschool `cpi R² = 0.992186`
       - Exschool `CPI -> unconstrained share R² = 0.962133`
       - Exschool end-to-end final share `R² = 0.948824`
       - OBOS `cpi R² = 0.228234`
       - OBOS `CPI -> unconstrained share R² = 0.360615`
       - OBOS end-to-end final share `R² = 0.327263`
   - Sequential split-scenario browser verification:
     - `./.venv/bin/python scripts/validate_exschool_modes_playwright.py --scenario high-intensity`
       - Result: `[ok] high-intensity completed ...`
     - `./.venv/bin/python scripts/validate_exschool_modes_playwright.py --scenario real-original`
       - Result: `[ok] real-original completed ...`
     - `./.venv/bin/python scripts/validate_exschool_modes_playwright.py --scenario multi-save`
       - Result: `[ok] multi-save history completed ...`
   - Parallel Playwright reruns of `high-intensity` + `real-original` hit mode-card lookup timeouts while `multi-save` still passed; treated as local concurrent validation noise because the same scenarios passed immediately when rerun in isolation.
4. Next step
   - Keep this market-context patch as the new local candidate because `OBOS` rose materially and `Exschool` only moved slightly.
   - Continue stage-1 / stage-2 narrowing from the new baseline, with the next comparison focused on:
     - `team 9`
     - `Hangzhou`
     - `r3` / `r4`
     - whether `Exschool` end-to-end can be recovered without giving back the `OBOS` gain.

## Loop 18

1. Current phase goal
   - Reconcile the docs/baseline surface so the repo distinguishes accepted checkpoint state from newer local artifact refreshes and does not silently compress conflicting metric surfaces into one story.
2. Just changed
   - Re-read the committed checkpoint (`2050c83`), the current working-tree baseline artifact, and RUNLOG loops 14-17.
   - Updated `STATUS_SUMMARY.md` and `model.md` to make four facts explicit:
     - the last explicit accepted git checkpoint is still `2050c83`
     - RUNLOG loop 16 reran that older candidate and showed slight metric drift on this machine
     - `generated_reports/model_pipeline_current_baseline/` now reflects the newer loop-17 market-context candidate
     - sequential split-scenario browser validation is green, but concurrent reruns are not fully green and should not be described that way
   - Annotated `generated_reports/model_pipeline_current_baseline/` so the refreshed artifact is described as the latest verified local candidate rather than a silently promoted accepted baseline.
3. How verified
   - `git -C ASDAN-business log --oneline -n 12`
     - confirmed `2050c83` is still `HEAD`
   - `git -C ASDAN-business diff -- STATUS_SUMMARY.md model.md RUNLOG.md generated_reports/model_pipeline_current_baseline/summary.md generated_reports/model_pipeline_current_baseline/metrics.csv`
     - confirmed the current working tree already contained loop-17 metric refreshes not present in `HEAD`
   - `sed -n '1,220p' generated_reports/model_pipeline_current_baseline/summary.md`
   - `sed -n '1,80p' generated_reports/model_pipeline_current_baseline/metrics.csv`
   - `sed -n '470,560p' RUNLOG.md`
     - used the existing in-tree artifacts and verification notes; no code or tests were rerun for this docs-only reconciliation lane
4. Next step
   - If the loop-17 market-context candidate is to become the accepted baseline, stamp it with a new checkpoint/commit so the docs no longer need to carry the acceptance split explicitly.

## Loop 19

1. Current phase goal
   - Fold the newest verified lane outcomes into the docs without overstating browser status or model acceptance.
2. Just changed
   - Updated the docs to record that the session/setup/final-flow lane landed and that `pytest -q test_exschool_game_modes.py` reached `39 passed`.
   - Updated the docs to record that the combined regression across modes + engine/report + modeling reached `59 passed`.
   - Rewrote browser-status text to stay conservative:
     - targeted pytest around report-image/final-flow remains green
     - real browser is still not green because `scripts/validate_exschool_modes_playwright.py --scenario high-intensity` currently fails while waiting for a download event
   - Rewrote modeling-status text so the refreshed market-context artifact set is preserved as verified output, but not described as a newly accepted candidate.
3. How verified
   - Used the latest user-provided verified facts as the current source of truth for this docs-only reconciliation step.
   - Kept the refreshed artifact metrics aligned with the working-tree `generated_reports/model_pipeline_current_baseline/` files:
     - Exschool `cpi R² = 0.992186`
     - Exschool end-to-end final share `R² = 0.948436`
     - OBOS `cpi R² = 0.228234`
     - OBOS end-to-end final share `R² = 0.324523`
4. Next step
   - Keep browser validation red/open until the report download event path passes in a real-browser rerun.
   - Keep the refreshed market-context artifact set separate from accepted-baseline language until the modeling lane explicitly promotes or rejects it.

## Loop 20

1. Current phase goal
   - Refresh the docs lane from the newest verified state without overstating what is accepted versus what is merely refreshed.
2. Just changed
   - Updated `STATUS_SUMMARY.md`, `model.md`, and `generated_reports/model_pipeline_current_baseline/{summary.md,metadata.json}` to record:
     - the market-allocation bias lane landed
     - the modeling provenance lane landed
     - broad regression across `test_exschool_game_modes.py + test_engine_data_fidelity.py + test_engine_report_correctness.py + test_report_payload.py + test_modeling.py + test_market_allocation.py + test_reconstruct_exschool_decisions.py + test_data_loader_provenance.py` is now `72 passed`
     - `scripts/validate_exschool_modes_playwright.py --scenario high-intensity --browser chromium` is green again
   - Narrowed the browser wording so the docs no longer overclaim closure:
     - `multi-save` is still open because the verifier followed the wrong continue link and asserted the wrong mode label
     - a fresh single `real-original` Chromium run still hit a mode-card lookup timeout
   - Clarified the modeling acceptance boundary:
     - the current accepted model candidate still remains the earlier market-context baseline unless a newer candidate is explicitly accepted
     - the current `generated_reports/model_pipeline_current_baseline/` directory is a refreshed verified artifact surface and should not be read as an implicitly newer accepted candidate
3. How verified
   - `./.venv/bin/python -m pytest -q test_exschool_game_modes.py test_engine_data_fidelity.py test_engine_report_correctness.py test_report_payload.py test_modeling.py test_market_allocation.py test_reconstruct_exschool_decisions.py test_data_loader_provenance.py`
     - Result: `72 passed`
   - `./.venv/bin/python scripts/validate_exschool_modes_playwright.py --scenario high-intensity --browser chromium`
     - Result: `[ok] high-intensity completed ...`
   - Browser specifics for `multi-save` / `real-original` were taken from the latest leader-verified state for this docs-only refresh:
     - `multi-save` failed because the verifier picked the wrong continue link and asserted the wrong mode label
     - fresh single `real-original` Chromium rerun hit a mode-card lookup timeout
4. Next step
   - If a newer model candidate should replace the accepted market-context baseline, accept it explicitly and regenerate/checkpoint the artifacts/docs together.
   - If the full browser matrix should be restamped green again, fix the verifier weakness and rerun the remaining Chromium scenarios instead of inferring them from the recovered `high-intensity` pass.
