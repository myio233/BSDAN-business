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


BASE_DIR = Path("/mnt/c/Users/david/documents/ASDAN/表格/WYEF_results")
OUTPUT_XLSX = BASE_DIR / "team24_absolute_cpi_threshold_model.xlsx"
OUTPUT_MD = BASE_DIR / "team24_absolute_cpi_threshold_model.md"
PEER_OUTPUT_XLSX = BASE_DIR / "r1_r6_peer_absolute_cpi.xlsx"
PEER_OUTPUT_MD = BASE_DIR / "r1_r6_peer_absolute_cpi.md"

TEAM_ID = "24"
ROUND_REMAP = {"r7": "r6"}
BASE_FRACTION = 0.10
SHARPNESS = 4.0


def remap_round(round_name):
    return ROUND_REMAP.get(round_name, round_name)


def sigmoid(x):
    return 1.0 / (1.0 + np.exp(-np.clip(x, -60, 60)))


def softplus(x):
    x = np.asarray(x, dtype=float)
    return np.log1p(np.exp(-np.abs(x))) + np.maximum(x, 0)


def prepare_data():
    teams = attach_lagged_features(load_market_reports()).copy()
    samples = load_summary_samples().copy()

    teams["round_original"] = teams["round"]
    samples["round_original"] = samples["round"]
    teams["round"] = teams["round"].map(remap_round)
    samples["round"] = samples["round"].map(remap_round)

    sample_df = build_sample_table(teams, samples)
    sample_df["round_order"] = sample_df["round"].map(round_sort_key)
    sample_df = sample_df[sample_df["team24_present_in_report"]].copy()
    sample_df = sample_df[sample_df["round"].isin(["r1", "r2", "r3", "r4", "r5", "r6"])].copy()
    sample_df = sample_df.sort_values(["round_order", "market"]).reset_index(drop=True)

    teams = teams[teams["round"].isin(["r1", "r2", "r3", "r4", "r5", "r6"])].copy()
    return teams, sample_df


def feature_frame(group):
    df = group.copy()
    df["m_log"] = np.log1p(df["management_index"].fillna(0))
    df["q_log"] = np.log1p(df["quality_index"].fillna(0))
    df["mi_log"] = np.log1p(df["market_index"].fillna(0))
    price_ratio = (df["avg_price"].fillna(1) / df["price"].replace(0, np.nan)).fillna(1e-9)
    df["p_log"] = np.log(np.maximum(price_ratio, 1e-9))
    df["brand_log"] = np.log1p(np.maximum(df["prev_market_share"].fillna(0), 0) * 1000.0)
    return df


def apply_absolute_cpi_model(group, params):
    (
        scale_latent,
        scale_share,
        w_m,
        w_q,
        w_mi,
        w_p,
        tau_m,
        tau_q,
        tau_mi,
        tau_p,
    ) = params

    df = feature_frame(group)

    gate_m = BASE_FRACTION + (1.0 - BASE_FRACTION) * sigmoid(SHARPNESS * (df["m_log"] - tau_m))
    gate_q = BASE_FRACTION + (1.0 - BASE_FRACTION) * sigmoid(SHARPNESS * (df["q_log"] - tau_q))
    gate_mi = BASE_FRACTION + (1.0 - BASE_FRACTION) * sigmoid(SHARPNESS * (df["mi_log"] - tau_mi))
    gate_p = BASE_FRACTION + (1.0 - BASE_FRACTION) * sigmoid(SHARPNESS * (df["p_log"] - tau_p))

    df["management_component"] = w_m * df["m_log"] * gate_m
    df["quality_component"] = w_q * df["q_log"] * gate_q
    df["market_component"] = w_mi * df["mi_log"] * gate_mi
    df["price_component"] = w_p * df["p_log"] * gate_p
    df["brand_component"] = 0.0

    latent = (
        df["management_component"]
        + df["quality_component"]
        + df["market_component"]
        + df["price_component"]
    )

    df["latent_absolute_strength"] = softplus(latent)
    df["estimated_cpi"] = scale_latent * df["latent_absolute_strength"] + scale_share * df["market_share"].fillna(0)

    df["gate_m"] = gate_m
    df["gate_q"] = gate_q
    df["gate_mi"] = gate_mi
    df["gate_p"] = gate_p
    return df


def build_groups(teams, sample_df):
    groups = []
    for _, sample in sample_df.iterrows():
        group = teams[(teams["round"] == sample["round"]) & (teams["market"] == sample["market"])].copy()
        groups.append(group)
    return groups


def initial_params(teams):
    feats = feature_frame(teams)
    return np.array(
        [
            0.005,
            0.5,
            0.10,
            0.20,
            0.10,
            0.20,
            float(feats["m_log"].median()),
            float(feats["q_log"].median()),
            float(feats["mi_log"].median()),
            float(feats["p_log"].median()),
        ],
        dtype=float,
    )


def param_bounds(teams):
    feats = feature_frame(teams)
    return [
        (0, 1),   # scale_latent
        (0, 5),   # scale_share
        (-5, 5),  # w_m
        (-5, 5),  # w_q
        (-5, 5),  # w_mi
        (-5, 5),  # w_p
        (float(feats["m_log"].min()), float(feats["m_log"].max())),
        (float(feats["q_log"].min()), float(feats["q_log"].max())),
        (float(feats["mi_log"].min()), float(feats["mi_log"].max())),
        (float(feats["p_log"].min()), float(feats["p_log"].max())),
    ]


def fit_model(teams, sample_df):
    groups = build_groups(teams, sample_df)
    actual = sample_df["actual_competitiveness"].to_numpy(dtype=float)
    x0 = initial_params(teams)
    bounds = param_bounds(teams)

    def predict(params):
        pred = []
        for group in groups:
            scored = apply_absolute_cpi_model(group, params)
            team24_val = scored.loc[scored["team"] == TEAM_ID, "estimated_cpi"].iloc[0]
            pred.append(float(team24_val))
        return np.array(pred, dtype=float)

    def objective(params):
        pred = predict(params)
        return float(np.mean((pred - actual) ** 2))

    starts = [
        x0,
        np.array([0.01, 1.0, 0.05, 0.10, 0.05, 0.30, *x0[6:]], dtype=float),
    ]

    best_result = None
    best_value = None
    for start in starts:
        result = minimize(
            objective,
            x0=start,
            bounds=bounds,
            method="L-BFGS-B",
            options={"maxiter": 100},
        )
        value = objective(result.x)
        if best_result is None or value < best_value:
            best_result = result
            best_value = value

    pred = predict(best_result.x)
    metrics = compute_metrics(actual, pred)
    return best_result, pred, metrics


def export_peer_table(teams, params):
    frames = []
    for (_, _), group in teams.groupby(["round", "market"], sort=False):
        scored = apply_absolute_cpi_model(group, params)
        scored = scored.sort_values("estimated_cpi", ascending=False).reset_index(drop=True)
        scored["estimated_rank_in_city"] = np.arange(1, len(scored) + 1)
        scored["actual_market_share_rank"] = scored["market_share"].rank(method="min", ascending=False)
        frames.append(scored)

    result = pd.concat(frames, ignore_index=True)
    result["is_team24"] = result["team"] == TEAM_ID
    result = result.sort_values(
        ["round", "market", "estimated_rank_in_city", "team"],
        key=lambda s: s.map(round_sort_key) if s.name == "round" else s,
    ).reset_index(drop=True)

    cols = [
        "round",
        "market",
        "team",
        "estimated_rank_in_city",
        "estimated_cpi",
        "market_share",
        "actual_market_share_rank",
        "sales_volume",
        "management_index",
        "quality_index",
        "agents",
        "marketing_investment",
        "market_index",
        "price",
        "avg_price",
        "prev_market_share",
        "latent_absolute_strength",
        "management_component",
        "quality_component",
        "market_component",
        "price_component",
        "brand_component",
        "gate_m",
        "gate_q",
        "gate_mi",
        "gate_p",
        "round_original",
        "source_file",
        "is_team24",
    ]

    with pd.ExcelWriter(PEER_OUTPUT_XLSX, engine="openpyxl") as writer:
        result[cols].to_excel(writer, sheet_name="all_teams_r1_r6", index=False)
        result[result["team"] != TEAM_ID][cols].to_excel(writer, sheet_name="other_teams_only", index=False)
        for round_name in ["r1", "r2", "r3", "r4", "r5", "r6"]:
            result[result["round"] == round_name][cols].to_excel(writer, sheet_name=round_name, index=False)

    lines = [
        "# R1-R6 Peer Absolute CPI Export",
        "",
        "这是非归一化的绝对 CPI 估计，不再强制同城所有公司加总为 1。",
        "",
        f"- 总行数: {len(result)}",
        f"- 其它公司行数: {len(result[result['team'] != TEAM_ID])}",
    ]
    PEER_OUTPUT_MD.write_text("\n".join(lines), encoding="utf-8")

    return result


def main():
    teams, sample_df = prepare_data()
    result, pred, metrics = fit_model(teams, sample_df)

    metrics_df = pd.DataFrame(
        [
            {
                "model": "absolute_threshold_cpi_with_marketshare_anchor",
                "success": bool(result.success),
                "objective": float(np.mean((pred - sample_df["actual_competitiveness"].to_numpy(dtype=float)) ** 2)),
                **metrics,
                "scale_latent": result.x[0],
                "scale_share": result.x[1],
                "w_m": result.x[2],
                "w_q": result.x[3],
                "w_mi": result.x[4],
                "w_p": result.x[5],
                "tau_m": result.x[6],
                "tau_q": result.x[7],
                "tau_mi": result.x[8],
                "tau_p": result.x[9],
            }
        ]
    )

    pred_df = sample_df.copy()
    pred_df["predicted_team24_cpi"] = pred
    pred_df["prediction_error"] = pred_df["predicted_team24_cpi"] - pred_df["actual_competitiveness"]
    pred_df["abs_error"] = pred_df["prediction_error"].abs()

    with pd.ExcelWriter(OUTPUT_XLSX, engine="openpyxl") as writer:
        metrics_df.to_excel(writer, sheet_name="metrics", index=False)
        pred_df.to_excel(writer, sheet_name="team24_fit", index=False)

    peer_df = export_peer_table(teams, result.x)

    lines = [
        "# Team24 Absolute CPI Threshold Model",
        "",
        "核心思想：",
        "- 管理、质量、市场指数、价格分别通过各自门槛函数单独起作用。",
        "- 质量还会额外解锁市场指数的作用。",
        "- 市场份额只作为粗锚点加入，不再把 CPI 当成纯份额。",
        "",
        "公式：",
        "",
        "```text",
        "gate_x = 0.1 + 0.9 * sigmoid(k * (feature_x - tau_x))",
        "latent = bias",
        "       + w_m  * MgmtLog   * gate_m",
        "       + w_q  * QualLog   * gate_q",
        "       + w_mi * MarketLog * gate_mi",
        "       + w_p  * PriceLog  * gate_p",
        "",
        "estimated_cpi = scale_latent * softplus(latent) + scale_share * market_share",
        "```",
        "",
        "## Metrics",
        "",
        metrics_df.to_string(index=False),
        "",
        "## Largest Team24 Errors",
        "",
        pred_df.sort_values("abs_error", ascending=False)[
            ["round", "market", "actual_competitiveness", "predicted_team24_cpi", "prediction_error"]
        ].head(12).to_string(index=False),
        "",
        "## Peer Export",
        "",
        f"- {PEER_OUTPUT_XLSX}",
    ]
    OUTPUT_MD.write_text("\n".join(lines), encoding="utf-8")

    print(f"Saved: {OUTPUT_XLSX}")
    print(f"Saved: {OUTPUT_MD}")
    print(f"Saved: {PEER_OUTPUT_XLSX}")
    print(metrics_df.to_string(index=False))
    print(peer_df[["round", "market", "team", "estimated_cpi", "market_share"]].head(20).to_string(index=False))


if __name__ == "__main__":
    main()
