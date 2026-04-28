# Model Notes

This file documents the current simulator/model understanding and will be expanded as implementation proceeds.

## Current Conceptual Chain

The market pipeline is intentionally kept in three stages:

1. `features -> CPI`
2. `CPI -> predicted_marketshare_unconstrained`
3. `predicted_marketshare_unconstrained -> final realized market allocation`

The implementation goal is to improve the three stages separately without changing the overall structure.

## Current Acceptance Targets

- Stage 1 (`CPI`) >= 99%
- Stage 2 (`CPI -> unconstrained share`) >= 99%
- Stage 3 (`unconstrained share -> final share`) >= 99%
- Combined chain >= 93%

The evaluation scope is restricted to verified real data from:

- Exschool
- OBOS

## Current Known System Facts

- Opponent settlement is fair only when all teams are run through the same engine.
- Player team is `C13`.
- Current repo already has a smart fixed-opponent mode.
- A new real-original mode must use reconstructed real opponent decisions from real reports.

## Confirmed Engine/Data Risks To Track

- Invalid default `r1` payload due to zero default agents.
- Repayment exceeding available cash.
- City-sensitive campaign parameters not fully wired in campaign context.
- Opponent later-round starting-state fallback quality.
- Storage capacity continuity mismatch.
- Duplicate workforce planning paths.
- Salary-anchor drift from campaign reality.
- Export/report-image fragility.

## Documentation To Fill In During Implementation

- Real-source datasets and lineage
- Reconstruction rules for opponent decisions
- Exact stage metrics and formulas
- Chosen absorption cap design
- Residual mismatch cases after tuning

## Current Modeling Findings

- The largest earlier Exschool failure was not CPI itself. After the stock-budget/key fix, Exschool `CPI -> unconstrained share` was already strong and end-to-end improved sharply.
- The old stage-2 share model was structurally one-sided:
  - it learned `max(actual_share - CPI, 0)`
  - it could only add positive uplift
  - it could not learn negative corrections when CPI overshot realized share
- Stage-2 has now been changed to a signed delta model:
  - target: `delta_share = actual_share - predicted_cpi`
  - prediction: `predicted_marketshare_unconstrained = clip(predicted_cpi + predicted_delta, 0, 1)`
  - runtime and evaluator now use the same helper path
- Under the current runtime-default allocator (`absorption cap = 0`), the signed stage-2 change materially improved both datasets:
  - Exschool `CPI -> unconstrained share` `R² ≈ 0.9610`
  - Exschool end-to-end final share `R² ≈ 0.9550`
  - OBOS `CPI -> unconstrained share` `R² ≈ 0.1098`
  - OBOS end-to-end final share `R² ≈ 0.1281`

## Current Runtime Baseline

- Do not flatten the current repo into a single metric surface. Four different verified surfaces exist right now:
  - last explicit checkpoint artifact at `2050c83`:
    - candidate shape: smaller stage-1 `GBR + RF` blend without the newer market-context features
    - Exschool `cpi R² ≈ 0.99174`
    - Exschool `CPI -> unconstrained share R² ≈ 0.96228`
    - Exschool end-to-end final share `R² ≈ 0.94835`
    - OBOS `cpi R² ≈ 0.15951`
    - OBOS `CPI -> unconstrained share R² ≈ 0.34226`
    - OBOS end-to-end final share `R² ≈ 0.29571`
  - RUNLOG loop 16 local rerun of that older checkpointed candidate on this machine:
    - Exschool `cpi R² ≈ 0.99174`
    - Exschool `CPI -> unconstrained share R² ≈ 0.96386`
    - Exschool end-to-end final share `R² ≈ 0.94962`
    - OBOS `cpi R² ≈ 0.15951`
    - OBOS `CPI -> unconstrained share R² ≈ 0.34254`
    - OBOS end-to-end final share `R² ≈ 0.29333`
    - this rerun was verified in the runlog, but it did not get its own refreshed artifact folder before the next local candidate was tried
  - earlier accepted market-context baseline:
    - candidate shape: small `GBR + RF` blend plus market-context features from `population` / `penetration`
    - Exschool `cpi R² ≈ 0.992186`
    - Exschool `CPI -> unconstrained share R² ≈ 0.962133`
    - Exschool end-to-end final share `R² ≈ 0.948824`
    - OBOS `cpi R² ≈ 0.228234`
    - OBOS `CPI -> unconstrained share R² ≈ 0.360615`
    - OBOS end-to-end final share `R² ≈ 0.327263`
    - unless a newer candidate is explicitly accepted, treat this as the current accepted model candidate
  - current working-tree `generated_reports/model_pipeline_current_baseline/` refresh:
    - candidate shape: refreshed artifact surface for the same market-context candidate family
    - Exschool `cpi R² ≈ 0.992186`
    - Exschool `CPI -> unconstrained share R² ≈ 0.962950`
    - Exschool end-to-end final share `R² ≈ 0.948436`
    - OBOS `cpi R² ≈ 0.228234`
    - OBOS `CPI -> unconstrained share R² ≈ 0.356986`
    - OBOS end-to-end final share `R² ≈ 0.324523`
    - these refreshed artifacts are verified, but they do not silently supersede the accepted market-context candidate
- The current committed baseline before the newest local change was:
  - Exschool `cpi R² ≈ 0.99917`
  - Exschool end-to-end final share `R² ≈ 0.95041`
  - OBOS `cpi R² ≈ 0.14369`
  - OBOS `CPI -> unconstrained share R² ≈ 0.32746`
  - OBOS end-to-end final share `R² ≈ 0.27594`
- The current local in-progress change adds a small `GBR + RF` blend in stage-1 CPI:
  - `CPI_RF_BLEND_WEIGHT = 0.15`
  - latest verified runtime-baseline metrics before the newest market-context patch:
    - Exschool `cpi R² ≈ 0.99174`
    - Exschool `CPI -> unconstrained share R² ≈ 0.96228`
    - Exschool end-to-end final share `R² ≈ 0.94835`
    - OBOS `cpi R² ≈ 0.15951`
    - OBOS `CPI -> unconstrained share R² ≈ 0.34226`
    - OBOS end-to-end final share `R² ≈ 0.29571`
- The earlier accepted market-context baseline keeps the same `GBR + RF` blend and adds market-context features to stage-1:
  - `population_log`
  - `penetration_raw`
  - `penetration_logit`
  - `population_x_penetration`
  - `penetration_x_m_rank`
  - `penetration_x_q_rank`
  - `penetration_x_mi_rank`
  - `market_size_log`
  - `market_size_x_price_rank`
  - accepted-candidate metrics:
    - Exschool `cpi R² ≈ 0.99219`
    - Exschool `CPI -> unconstrained share R² ≈ 0.962133`
    - Exschool end-to-end final share `R² ≈ 0.948824`
    - OBOS `cpi R² ≈ 0.22823`
    - OBOS `CPI -> unconstrained share R² ≈ 0.360615`
    - OBOS end-to-end final share `R² ≈ 0.327263`
- The current working-tree artifact refresh for that same candidate family reads:
  - Exschool `CPI -> unconstrained share R² ≈ 0.962950`
  - Exschool end-to-end final share `R² ≈ 0.948436`
  - OBOS `CPI -> unconstrained share R² ≈ 0.356986`
  - OBOS end-to-end final share `R² ≈ 0.324523`
- Interpretation:
  - the market-context patch materially improves the weak cross-domain `OBOS` slice, especially stage-1 and end-to-end
  - `Exschool` end-to-end moved slightly down versus the prior local baseline, but not by enough to reject the patch outright
  - no newer candidate was explicitly accepted after that market-context baseline, so docs should keep that acceptance state separate from later artifact refreshes
  - this is still far from the user-requested near-99% cross-dataset fit, so it is an incremental gain, not a finish line

## Why Stage 3 Still Looks Bad In Isolation

- The isolated stage-3 metric currently compares:
  - actual adjustment: `actual_share - predicted_marketshare_unconstrained`
  - predicted adjustment: `final_share - predicted_marketshare_unconstrained`
- After stage-2 got better, those residual adjustments became smaller and noisier.
- Under the current runtime default, stage-3 also operates with `absorption cap = 0`, so the allocator is intentionally conservative and often leaves `final_share` close to `predicted_marketshare_unconstrained`.
- Current evidence shows the allocator is under-adjusting magnitude rather than fully collapsing:
  - Exschool actual adjustment std is about `0.00895`, but predicted adjustment std is only about `0.00328`
  - OBOS actual adjustment std is about `0.06347`, but predicted adjustment std is only about `0.01344`
- In the latest blended baseline, the isolated stage-3 metric is still weak:
  - Exschool stage-3 `R² ≈ -0.40087`
  - OBOS stage-3 `R² ≈ -0.09639`
- So a negative stage-3 `R²` does not mean final share is unusable by itself. The stronger indicator right now is end-to-end final share, which is much higher than before.
- In practice, the biggest remaining end-to-end error is still coming from stage-1 / stage-2 underprediction on `OBOS`, especially `Hangzhou` and other high-CPI rows, not from allocator instability alone.

## Current Best Interpretation

- Exschool remaining error is mostly residual allocator-amplitude mismatch, not total chain failure.
- OBOS remaining error is mostly stage-1 / stage-2 cross-domain weakness:
  - strong negative CPI bias
  - underprediction in `Hangzhou` and high-CPI cases
  - sparse CPI labels make naive cross-domain fitting unstable
- The latest refreshed local artifact set suggests that some of the stage-1 miss was a missing market-context signal rather than purely a proxy-weight problem:
  - feeding `population` / `penetration` / derived market-context terms into the stage-1 tree matrix improved `OBOS` without rewriting stage-2
  - a follow-up utilization-based stage-1 experiment was checked offline and rejected because it did not improve the tradeoff

## Latest Stage-1 Adjustment

- Two candidate next moves were compared:
  - broader domain-aware feature changes in stage-1
  - a narrower change that reduces how much proxy `marketshare_clean` rows can pull the CPI scale away from true CPI rows
- The narrower change was selected first because it isolates one training-signal issue without rewriting the model structure.
- Current stage-1 training weights are now:
  - true CPI rows: `100.0`
  - proxy market-share rows: `0.5`
- This materially improved the cross-domain metrics while keeping Exschool CPI nearly unchanged:
  - Exschool `cpi R² ≈ 0.99917`
  - OBOS `cpi R² ≈ 0.14369`
  - OBOS `CPI -> unconstrained share R² ≈ 0.32746`
  - OBOS end-to-end final share `R² ≈ 0.27594`
- Tradeoff:
  - Exschool end-to-end final share slipped slightly from the previous signed-delta baseline, but remained high at about `0.95041`
- The newest follow-up then tested a different narrow stage-1 move:
  - keep the current training weights and `GBR + RF` blend
  - add market-context features that already exist in the report data, especially `population` / `penetration` and their interactions with rank signals
- Result:
  - Exschool `cpi R²` improved slightly (`≈ 0.99174 -> ≈ 0.992186`)
  - Exschool end-to-end final share moved slightly down (`≈ 0.94962 -> ≈ 0.948436`)
  - OBOS `cpi R²` improved materially (`≈ 0.15951 -> ≈ 0.228234`)
  - OBOS end-to-end final share improved materially (`≈ 0.29333 -> ≈ 0.324523`)
- Acceptance status:
  - refreshed artifacts exist for this market-context patch
  - the accepted model candidate still remains the earlier market-context baseline unless a newer candidate is explicitly accepted
  - do not describe a later artifact refresh as the already accepted repo/model baseline unless that acceptance happens explicitly
  - do not add utilization-derived stage-1 features on top for now, because the offline probe worsened the stage-1 tradeoff

## Current Rejected Branches

- Injecting the small set of real `OBOS` CPI labels directly into stage-1 training was tested as the next safe-looking option.
- Result:
  - `OBOS` CPI `R²` could be raised sharply in the offline experiment
  - but `Exschool` CPI and end-to-end quality degraded too much
  - net result was worse than the current runtime baseline for the actual game model
- Decision:
  - reject this branch for now
  - keep the runtime training frame source-faithful instead of hard-biasing it toward `OBOS`

## Next Modeling Direction

- Preserve the 3-stage chain:
  1. `features -> CPI`
  2. `CPI -> predicted_marketshare_unconstrained`
  3. `predicted_marketshare_unconstrained -> final_share`
- Do not go back to allocator-only tuning as the main lever.
- Next high-leverage change should still be surgical and comparison-driven:
  - keep the accepted market-context stage-1 feature set as the base candidate
  - inspect whether `team 9` / `Hangzhou` / `r3-r4` still need a tighter stage-1 or stage-2 correction
  - specifically try to recover the small `Exschool` end-to-end dip without giving back the new `OBOS` gains
  - avoid broad rewrites unless they clearly beat the current candidate in both `Exschool` and `OBOS`
