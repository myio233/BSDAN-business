#!/usr/bin/env python3
import math
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import minimize

from analyze_team24_competitiveness import (
    BASE_DIR,
    build_sample_table,
    compute_metrics,
    load_market_reports,
    load_summary_samples,
    round_sort_key,
)


OUTPUT_XLSX = BASE_DIR / "team24_semidynamic_model.xlsx"
OUTPUT_MD = BASE_DIR / "team24_semidynamic_model.md"
TEAM_ID = "24"


def attach_lagged_features(all_teams):
    df = all_teams.copy()
    df["round_order"] = df["round"].map(round_sort_key)

    team_state = (
        df.groupby(["team", "round", "round_order"], as_index=False)
        .agg(
            team_management_index=("management_index", "mean"),
            team_quality_index=("quality_index", "mean"),
        )
        .sort_values(["team", "round_order"])
        .reset_index(drop=True)
    )
    team_state["prev_team_management_index"] = team_state.groupby("team")["team_management_index"].shift(1)
    team_state["prev_team_quality_index"] = team_state.groupby("team")["team_quality_index"].shift(1)

    df = df.merge(
        team_state[
            [
                "team",
                "round",
                "prev_team_management_index",
                "prev_team_quality_index",
            ]
        ],
        on=["team", "round"],
        how="left",
    )

    df = df.sort_values(["team", "market", "round_order"]).reset_index(drop=True)
    df["prev_market_share"] = df.groupby(["team", "market"])["market_share"].shift(1)
    df["prev_sales_volume"] = df.groupby(["team", "market"])["sales_volume"].shift(1)
    return df


def softmax_share(score, team_mask):
    score = np.asarray(score, dtype=float)
    score = np.where(np.isfinite(score), score, -1e9)
    shifted = score - np.max(score)
    raw = np.exp(np.clip(shifted, -60, 60))
    total = raw.sum()
    if total <= 0:
        return 0.0
    return float(raw[team_mask][0] / total)


def static_interaction_prediction(group, params):
    w_m, w_q, w_mi, w_p, w_mq, w_mmi, w_qmi = params

    m = np.log1p(group["management_index"].fillna(0).to_numpy(dtype=float))
    q = np.log1p(group["quality_index"].fillna(0).to_numpy(dtype=float))
    mi = np.log1p(group["market_index"].fillna(0).to_numpy(dtype=float))
    price_ratio = (
        group["avg_price"].fillna(1) / group["price"].replace(0, np.nan)
    ).fillna(1e-9)
    p = np.log(np.maximum(price_ratio.to_numpy(dtype=float), 1e-9))

    score = w_m * m + w_q * q + w_mi * mi + w_p * p + w_mq * m * q + w_mmi * m * mi + w_qmi * q * mi
    team_mask = (group["team"] == TEAM_ID).to_numpy()
    return softmax_share(score, team_mask)


def semidynamic_prediction(group, params):
    w_m, w_q, w_mi, w_p, w_mq, w_mmi, w_miq, w_brand, rho_m, rho_q = params

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

    score = (
        w_m * m_eff
        + w_q * q_eff
        + w_mi * mi
        + w_p * p
        + w_mq * m_eff * q_eff
        + w_mmi * m_eff * mi
        + w_miq * mi * q_eff
        + w_brand * brand
    )

    team_mask = (group["team"] == TEAM_ID).to_numpy()
    return softmax_share(score, team_mask)


def fit_model(groups, actual, fn, x0, bounds):
    starts = [np.array(x0, dtype=float)]
    if len(x0) == 10:
        starts.append(np.array([0.228012, 1.086388, -0.023017, 1.020741, 0.013172, 0.002986, 0.003336, 0.0, 0.0, 0.0], dtype=float))
        starts.append(np.array([0.228012, 1.086388, -0.023017, 1.020741, 0.013172, 0.002986, 0.003336, 0.1, 0.2, 0.2], dtype=float))

    def predict(params):
        return np.array([fn(group, params) for group in groups], dtype=float)

    def objective(params):
        pred = predict(params)
        return float(np.mean((pred - actual) ** 2))

    best_result = None
    best_value = None

    for start in starts:
        result = minimize(
            objective,
            x0=start,
            bounds=bounds,
            method="L-BFGS-B",
        )
        value = objective(result.x)
        if best_result is None or value < best_value:
            best_result = result
            best_value = value

    result = best_result
    pred = predict(result.x)
    metrics = compute_metrics(actual, pred)
    return result, pred, metrics


def main():
    all_teams = attach_lagged_features(load_market_reports())
    summary_samples = load_summary_samples()
    sample_df = build_sample_table(all_teams, summary_samples)
    sample_df = sample_df[sample_df["team24_present_in_report"]].copy()
    sample_df["round_order"] = sample_df["round"].map(round_sort_key)
    sample_df = sample_df.sort_values(["round_order", "market"]).reset_index(drop=True)

    groups = []
    for _, sample in sample_df.iterrows():
        market_rows = all_teams[
            (all_teams["round"] == sample["round"]) & (all_teams["market"] == sample["market"])
        ].copy()
        groups.append(market_rows)

    actual = sample_df["actual_competitiveness"].to_numpy(dtype=float)

    static_result, static_pred, static_metrics = fit_model(
        groups,
        actual,
        static_interaction_prediction,
        x0=[0.3, 1.0, 0.1, 1.0, 0.05, 0.01, 0.05],
        bounds=[(-5, 5), (-5, 5), (-5, 5), (-5, 5), (-2, 2), (-2, 2), (-2, 2)],
    )

    dynamic_result, dynamic_pred, dynamic_metrics = fit_model(
        groups,
        actual,
        semidynamic_prediction,
        x0=[0.25, 1.0, 0.1, 1.0, 0.05, 0.01, 0.05, 0.1, 0.2, 0.2],
        bounds=[(-5, 5), (-5, 5), (-5, 5), (-5, 5), (-2, 2), (-2, 2), (-2, 2), (-5, 5), (0, 1), (0, 1)],
    )

    metrics_df = pd.DataFrame(
        [
            {
                "model": "static_interaction_logit",
                "success": bool(static_result.success),
                "objective": float(np.mean((static_pred - actual) ** 2)),
                **static_metrics,
                "w_m": static_result.x[0],
                "w_q": static_result.x[1],
                "w_mi": static_result.x[2],
                "w_p": static_result.x[3],
                "w_mq": static_result.x[4],
                "w_mmi": static_result.x[5],
                "w_qmi": static_result.x[6],
            },
            {
                "model": "semidynamic_interaction_logit",
                "success": bool(dynamic_result.success),
                "objective": float(np.mean((dynamic_pred - actual) ** 2)),
                **dynamic_metrics,
                "w_m": dynamic_result.x[0],
                "w_q": dynamic_result.x[1],
                "w_mi": dynamic_result.x[2],
                "w_p": dynamic_result.x[3],
                "w_mq": dynamic_result.x[4],
                "w_mmi": dynamic_result.x[5],
                "w_miq": dynamic_result.x[6],
                "w_brand": dynamic_result.x[7],
                "rho_m": dynamic_result.x[8],
                "rho_q": dynamic_result.x[9],
            },
        ]
    )

    prediction_df = sample_df.copy()
    prediction_df["static_prediction"] = static_pred
    prediction_df["static_error"] = prediction_df["static_prediction"] - prediction_df["actual_competitiveness"]
    prediction_df["dynamic_prediction"] = dynamic_pred
    prediction_df["dynamic_error"] = prediction_df["dynamic_prediction"] - prediction_df["actual_competitiveness"]
    prediction_df["dynamic_abs_error"] = prediction_df["dynamic_error"].abs()

    with pd.ExcelWriter(OUTPUT_XLSX, engine="openpyxl") as writer:
        metrics_df.to_excel(writer, sheet_name="metrics", index=False)
        prediction_df.to_excel(writer, sheet_name="predictions", index=False)
        all_teams.to_excel(writer, sheet_name="all_team_features", index=False)

    best_errors = prediction_df[
        [
            "round",
            "market",
            "actual_competitiveness",
            "dynamic_prediction",
            "dynamic_error",
            "team24_management_index",
            "team24_quality_index",
            "team24_agents_report",
            "team24_marketing_report",
            "team24_price_report",
            "avg_price",
        ]
    ].sort_values("dynamic_error", key=lambda s: s.abs(), ascending=False).head(10)

    report_lines = [
        "# Team24 Semi-Dynamic Model",
        "",
        "## Model Structure",
        "",
        "```text",
        "prevMgmt_i,t = previous-round management index of team i",
        "prevQual_i,t = previous-round quality index of team i",
        "prevShare_i,t,c = previous observed market share of team i in city c",
        "",
        "MgmtEff_i,t = log(1 + Mgmt_i,t) + rho_m * log(1 + prevMgmt_i,t)",
        "QualEff_i,t = log(1 + Qual_i,t) + rho_q * log(1 + prevQual_i,t)",
        "BrandEff_i,t,c = log(1 + 1000 * prevShare_i,t,c)",
        "MarketEff_i,t,c = log(1 + MarketIndex_i,t,c)",
        "PriceEff_i,t,c = log(AvgPrice_t,c / Price_i,t,c)",
        "",
        "Score_i,t,c = w_m * MgmtEff",
        "            + w_q * QualEff",
        "            + w_mi * MarketEff",
        "            + w_p * PriceEff",
        "            + w_mq * MgmtEff * QualEff",
        "            + w_miq * MarketEff * QualEff",
        "            + w_brand * BrandEff",
        "",
        "Competitiveness_i,t,c = exp(Score_i,t,c) / Σ_j exp(Score_j,t,c)",
        "```",
        "",
        "## Metrics",
        "",
        metrics_df.to_string(index=False),
        "",
        "## Best-Fit Semi-Dynamic Parameters",
        "",
        f"- w_m = {dynamic_result.x[0]:.6f}",
        f"- w_q = {dynamic_result.x[1]:.6f}",
        f"- w_mi = {dynamic_result.x[2]:.6f}",
        f"- w_p = {dynamic_result.x[3]:.6f}",
        f"- w_mq = {dynamic_result.x[4]:.6f}",
        f"- w_mmi = {dynamic_result.x[5]:.6f}",
        f"- w_miq = {dynamic_result.x[6]:.6f}",
        f"- w_brand = {dynamic_result.x[7]:.6f}",
        f"- rho_m = {dynamic_result.x[8]:.6f}",
        f"- rho_q = {dynamic_result.x[9]:.6f}",
        "",
        "## Interpretation",
        "",
        "- `rho_m` and `rho_q` capture previous-round carry-over. If they are near 0, current-round state dominates. If they are near 1, there is strong persistence.",
        "- `w_brand` captures same-city inertia from previous observed market share.",
        "- `w_mq` and `w_miq` are the interaction terms that let quality amplify management or marketing effectiveness.",
        "",
        "## Largest Semi-Dynamic Errors",
        "",
        best_errors.to_string(index=False),
        "",
        "## Notes",
        "",
        "- This is only semi-dynamic because lagged variables are reconstructed from observed report states, not from explicit investment-flow equations.",
        "- It is still much closer to a realistic business-simulation backend than a flat static weighted sum.",
    ]
    OUTPUT_MD.write_text("\n".join(report_lines), encoding="utf-8")

    print(f"Saved: {OUTPUT_XLSX}")
    print(f"Saved: {OUTPUT_MD}")
    print(metrics_df.to_string(index=False))


if __name__ == "__main__":
    main()
