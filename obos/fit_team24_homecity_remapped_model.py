#!/usr/bin/env python3
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import minimize

from analyze_team24_competitiveness import (
    build_sample_table,
    compute_metrics,
    load_market_reports,
    load_summary_samples,
    round_sort_key,
)
from fit_team24_semidynamic_model import attach_lagged_features


OUTPUT_XLSX = Path("/mnt/c/Users/david/documents/ASDAN/表格/WYEF_results/team24_homecity_remapped_model.xlsx")
OUTPUT_MD = Path("/mnt/c/Users/david/documents/ASDAN/表格/WYEF_results/team24_homecity_remapped_model.md")
TEAM_ID = "24"
HOME_CITY = "Shanghai"
ROUND_REMAP = {"r6": "r7", "r7": "r6"}


def remap_round(round_name):
    return ROUND_REMAP.get(round_name, round_name)


def remap_dataset(all_teams, summary_samples):
    teams = all_teams.copy()
    samples = summary_samples.copy()
    teams["round_original"] = teams["round"]
    samples["round_original"] = samples["round"]
    teams["round"] = teams["round"].map(remap_round)
    samples["round"] = samples["round"].map(remap_round)
    return teams, samples


def softmax_share(score, team_mask):
    shifted = score - np.max(score)
    raw = np.exp(np.clip(shifted, -60, 60))
    total = raw.sum()
    if total <= 0:
        return 0.0
    return float(raw[team_mask][0] / total)


def prediction(group, params):
    w_m, w_q, w_mi, w_p, w_mq, w_mmi, w_miq, w_brand, rho_m, rho_q, w_home = params

    m_cur = np.log1p(group["management_index"].fillna(0).to_numpy(dtype=float))
    q_cur = np.log1p(group["quality_index"].fillna(0).to_numpy(dtype=float))
    m_prev = np.log1p(group["prev_team_management_index"].fillna(0).to_numpy(dtype=float))
    q_prev = np.log1p(group["prev_team_quality_index"].fillna(0).to_numpy(dtype=float))
    brand = np.log1p(np.maximum(group["prev_market_share"].fillna(0).to_numpy(dtype=float), 0) * 1000.0)
    mi = np.log1p(group["market_index"].fillna(0).to_numpy(dtype=float))
    price_ratio = (
        group["avg_price"].fillna(1) / group["price"].replace(0, np.nan)
    ).fillna(1e-9)
    p = np.log(np.maximum(price_ratio.to_numpy(dtype=float), 1e-9))

    m_eff = m_cur + rho_m * m_prev
    q_eff = q_cur + rho_q * q_prev

    home_indicator = (
        ((group["team"] == TEAM_ID) & (group["market"] == HOME_CITY))
        .astype(float)
        .to_numpy(dtype=float)
    )

    score = (
        w_m * m_eff
        + w_q * q_eff
        + w_mi * mi
        + w_p * p
        + w_mq * m_eff * q_eff
        + w_mmi * m_eff * mi
        + w_miq * mi * q_eff
        + w_brand * brand
        + w_home * home_indicator
    )

    team_mask = (group["team"] == TEAM_ID).to_numpy()
    return softmax_share(score, team_mask)


def build_groups():
    teams = attach_lagged_features(load_market_reports())
    samples = load_summary_samples()
    teams, samples = remap_dataset(teams, samples)

    sample_df = build_sample_table(teams, samples)
    sample_df["round_order"] = sample_df["round"].map(round_sort_key)
    sample_df = sample_df[sample_df["team24_present_in_report"]].copy()
    sample_df = sample_df.sort_values(["round_order", "market"]).reset_index(drop=True)

    groups = []
    for _, sample in sample_df.iterrows():
        group = teams[(teams["round"] == sample["round"]) & (teams["market"] == sample["market"])].copy()
        groups.append(group)

    return teams, sample_df, groups


def fit(groups, actual):
    starts = [
        np.array([0.166767, 1.130079, -0.040338, 1.045803, 0.025702, 0.002783, 0.00121, 0.157779, 0.0, 0.0, 0.0], dtype=float),
        np.array([0.166767, 1.130079, -0.040338, 1.045803, 0.025702, 0.002783, 0.00121, 0.157779, 0.0, 0.0, 0.2], dtype=float),
        np.array([0.2, 1.0, 0.0, 1.0, 0.02, 0.0, 0.0, 0.1, 0.0, 0.0, 0.5], dtype=float),
    ]
    bounds = [(-5, 5), (-5, 5), (-5, 5), (-5, 5), (-2, 2), (-2, 2), (-2, 2), (-5, 5), (0, 1), (0, 1), (-5, 5)]

    def predict(params):
        return np.array([prediction(group, params) for group in groups], dtype=float)

    def objective(params):
        pred = predict(params)
        return float(np.mean((pred - actual) ** 2))

    best_result = None
    best_value = None
    for start in starts:
        result = minimize(objective, x0=start, bounds=bounds, method="L-BFGS-B")
        value = objective(result.x)
        if best_result is None or value < best_value:
            best_result = result
            best_value = value

    pred = predict(best_result.x)
    return best_result, pred


def main():
    all_teams, sample_df, groups = build_groups()
    actual = sample_df["actual_competitiveness"].to_numpy(dtype=float)

    result, pred = fit(groups, actual)
    metrics = compute_metrics(actual, pred)

    metrics_df = pd.DataFrame(
        [
            {
                "model": "semidynamic_logit_homecity_r6r7_remapped",
                "success": bool(result.success),
                "objective": float(np.mean((pred - actual) ** 2)),
                **metrics,
                "w_m": result.x[0],
                "w_q": result.x[1],
                "w_mi": result.x[2],
                "w_p": result.x[3],
                "w_mq": result.x[4],
                "w_mmi": result.x[5],
                "w_miq": result.x[6],
                "w_brand": result.x[7],
                "rho_m": result.x[8],
                "rho_q": result.x[9],
                "w_home": result.x[10],
            }
        ]
    )

    pred_df = sample_df.copy()
    pred_df["prediction"] = pred
    pred_df["error"] = pred_df["prediction"] - pred_df["actual_competitiveness"]
    pred_df["abs_error"] = pred_df["error"].abs()

    with pd.ExcelWriter(OUTPUT_XLSX, engine="openpyxl") as writer:
        metrics_df.to_excel(writer, sheet_name="metrics", index=False)
        pred_df.to_excel(writer, sheet_name="predictions", index=False)
        all_teams.to_excel(writer, sheet_name="all_team_features", index=False)

    report_lines = [
        "# Team24 Home-City + Remapped Semi-Dynamic Model",
        "",
        "Assumptions applied:",
        "- Home city effect exists for Team24 in Shanghai.",
        "- Round labels are remapped with `r6 <-> r7` before fitting.",
        "",
        "## Formula",
        "",
        "```text",
        "Score_i,t,c = static/semidynamic score",
        "            + w_home * I(team_i = 24 and city_c = Shanghai)",
        "",
        "Competitiveness_i,t,c = exp(Score_i,t,c) / Σ_j exp(Score_j,t,c)",
        "```",
        "",
        "## Metrics",
        "",
        metrics_df.to_string(index=False),
        "",
        "## Parameters",
        "",
        f"- w_m = {result.x[0]:.6f}",
        f"- w_q = {result.x[1]:.6f}",
        f"- w_mi = {result.x[2]:.6f}",
        f"- w_p = {result.x[3]:.6f}",
        f"- w_mq = {result.x[4]:.6f}",
        f"- w_mmi = {result.x[5]:.6f}",
        f"- w_miq = {result.x[6]:.6f}",
        f"- w_brand = {result.x[7]:.6f}",
        f"- rho_m = {result.x[8]:.6f}",
        f"- rho_q = {result.x[9]:.6f}",
        f"- w_home = {result.x[10]:.6f}",
        "",
        "## Largest Errors",
        "",
        pred_df.sort_values("abs_error", ascending=False)[
            ["round", "market", "actual_competitiveness", "prediction", "error"]
        ].head(12).to_string(index=False),
    ]
    OUTPUT_MD.write_text("\n".join(report_lines), encoding="utf-8")

    print(f"Saved: {OUTPUT_XLSX}")
    print(f"Saved: {OUTPUT_MD}")
    print(metrics_df.to_string(index=False))


if __name__ == "__main__":
    main()
