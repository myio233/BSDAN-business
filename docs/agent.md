# Agent Rules

This file stores reusable long-task execution rules for this repo.

## Core Execution Rules

- Do not leave key project state only in chat; persist it in repo files.
- Keep the repo runnable after each meaningful change.
- Prefer small validated steps over broad speculative refactors.
- If no material progress is made across two consecutive loops, reread `PLAN.md` and narrow the next step.
- Prefer direct implementation over long discussion when the next action is discoverable from the repo.
- Do not stop at partial analysis when a fix, validation, or artifact refresh can be completed in the same loop.
- Do not claim completion for a pipeline just because artifacts already exist; reproduction and validation must pass in the current environment.
- Treat findings from other agents as leads, not truth; verify locally before accepting or rejecting them.
- If privileged local commands are required and the user has provided sudo credentials, they may be used for machine setup during the session, but raw secrets must not be written into tracked repo files.
- Treat `real-original` as the current source-faithful 23-team mode, not as perfect full-roster ground truth.
- Do not synthesize missing teams or silently relabel one mode as another.
- When a metric looks bad, record whether the bad number is still current or came from an older evaluator/model baseline before explaining it.

## Required Loop Status Format

Each loop must answer four things in plain language:

1. Current phase goal
2. What just changed
3. How it was verified
4. What is next

## Subagent Rules

- Each subagent owns one clear subproblem.
- Multiple subagents must not edit the same file.
- Main agent merges, compares, and decides.
- When two candidate solutions exist, the main agent must record which one was chosen and why.
- Supervisor agent audits progress and final quality; it should avoid product-code edits.

## Repo-Specific Task Protocol

- Preserve the fair-settlement semantics:
  - player team uses its own submitted decisions
  - fixed opponents use fixed inputs
  - all teams are settled by the same engine rules
- For real-data tasks, first establish validated source tables before changing the model.
- For extracted-report pipelines, require both:
  - script reproducibility in the current repo path and environment
  - workbook-level validation outputs without silent failures
- For model work, keep the conceptual chain unchanged:
  - CPI
  - CPI -> unconstrained share
  - unconstrained share -> final share
- For model tuning, prefer stage-local improvements with measured before/after metrics over broad rewrites.
- For UI work, preserve current flows unless the plan requires a visible product change.
- For engine work, preserve fair settlement semantics across player and fixed opponents.

## Validation Expectations

- Run the narrowest relevant tests first.
- After integration, run:
  - script-based validation
  - model evaluation
  - Playwright browser validation
  - final review pass
- For any substantial user-facing change, include a real browser pass, not just static reasoning.
- In this environment, prefer split-scenario Playwright validation over one giant all-in-one run:
  - `--scenario high-intensity`
  - `--scenario real-original`
  - `--scenario multi-save`
- If host CPU / memory pressure is already high and the browser process itself crashes, record it as an environment-validation failure and fall back to narrower/lighter browser checks before treating it as an app regression.
- For data-pipeline work, distinguish clearly between:
  - script executed
  - artifacts generated
  - validation passed

## Persistence

- Update `RUNLOG.md` after meaningful loops.
- Update `model.md` whenever engine/model assumptions change.
- Keep a compact repo-level status summary when the workstream gets long enough that chat context may drift.
