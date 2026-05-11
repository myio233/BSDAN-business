#!/usr/bin/env python3
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import least_squares

from analyze_team24_competitiveness import (
    build_sample_table,
    load_market_reports,
    load_summary_samples,
    round_sort_key,
)
from fit_team24_semidynamic_model import attach_lagged_features


BASE_DIR = Path("/mnt/c/Users/david/documents/ASDAN/表格/WYEF_results")
OUTPUT_XLSX = BASE_DIR / "team24_high_accuracy_absolute_cpi.xlsx"
OUTPUT_MD = BASE_DIR / "team24_high_accuracy_absolute_cpi.md"
PEER_XLSX = BASE_DIR / "r1_r6_peer_high_accuracy_absolute_cpi.xlsx"
TEAM_ID = "24"
ROUND_REMAP = {"r7": "r6"}


def remap_round(round_name):
    return ROUND_REMAP.get(round_name, round_name)


def softplus(x):
    x = np.asarray(x, dtype=float)
    return np.log1p(np.exp(-np.abs(x))) + np.maximum(x, 0)


def sigmoid(x):
    return 1.0 / (1.0 + np.exp(-np.clip(x, -60, 60)))


def relu(x):
    return np.maximum(x, 0.0)


def prepare():
    teams = attach_lagged_features(load_market_reports()).copy()
    summaries = load_summary_samples().copy()
    teams["round_original"] = teams["round"]
    summaries["round_original"] = summaries["round"]
    teams["round"] = teams["round"].map(remap_round)
    summaries["round"] = summaries["round"].map(remap_round)
    teams = teams[teams["round"].isin(["r1", "r2", "r3", "r4", "r5", "r6"])].copy()
    samples = build_sample_table(teams, summaries)
    samples = samples[samples["team24_present_in_report"]].copy()
    samples = samples[samples["round"].isin(["r1", "r2", "r3", "r4", "r5", "r6"])].copy()
    samples["round_order"] = samples["round"].map(round_sort_key)
    samples = samples.sort_values(["round_order", "market"]).reset_index(drop=True)
    return teams, samples


def feature_frame(df):
    out = df.copy()
    out["m_log"] = np.log1p(out["management_index"].fillna(0))
    out["q_log"] = np.log1p(out["quality_index"].fillna(0))
    out["mi_log"] = np.log1p(out["market_index"].fillna(0))
    price_ratio = (out["avg_price"].fillna(1) / out["price"].replace(0, np.nan)).fillna(1e-9)
    out["p_log"] = np.log(np.maximum(price_ratio, 1e-9))
    out["share"] = out["market_share"].fillna(0)
    return out


def get_categories(samples):
    rounds = sorted(samples["round"].unique(), key=round_sort_key)
    cities = sorted(samples["market"].unique())
    return rounds, cities


def unpack_params(vec, rounds, cities):
    i = 0
    raw = {}
    raw["base"] = vec[i]; i += 1
    raw["share_w"] = vec[i]; i += 1
    raw["share_b"] = vec[i]; i += 1

    raw["m_w"] = vec[i]; i += 1
    raw["m_tau"] = vec[i]; i += 1
    raw["m_pow"] = vec[i]; i += 1

    raw["q_w"] = vec[i]; i += 1
    raw["q_tau"] = vec[i]; i += 1
    raw["q_pow"] = vec[i]; i += 1

    raw["mi_w"] = vec[i]; i += 1
    raw["mi_tau"] = vec[i]; i += 1
    raw["mi_pow"] = vec[i]; i += 1

    raw["p_w"] = vec[i]; i += 1
    raw["p_tau"] = vec[i]; i += 1
    raw["p_pow"] = vec[i]; i += 1

    raw["unlock_w"] = vec[i]; i += 1
    raw["unlock_tau"] = vec[i]; i += 1
    raw["unlock_k"] = vec[i]; i += 1

    round_biases = {"r1": 0.0}
    for r in rounds:
        if r == "r1":
            continue
        round_biases[r] = vec[i]
        i += 1

    city_biases = {"Shanghai": 0.0}
    for c in cities:
        if c == "Shanghai":
            continue
        city_biases[c] = vec[i]
        i += 1

    return raw, round_biases, city_biases


def estimate_cpi(df, vec, rounds, cities):
    raw, round_biases, city_biases = unpack_params(vec, rounds, cities)
    feats = feature_frame(df)

    base = softplus(raw["base"])
    share_w = softplus(raw["share_w"])
    share_b = softplus(raw["share_b"]) + 0.2

    m_w = softplus(raw["m_w"])
    m_tau = raw["m_tau"]
    m_pow = softplus(raw["m_pow"]) + 0.2

    q_w = softplus(raw["q_w"])
    q_tau = raw["q_tau"]
    q_pow = softplus(raw["q_pow"]) + 0.2

    mi_w = softplus(raw["mi_w"])
    mi_tau = raw["mi_tau"]
    mi_pow = softplus(raw["mi_pow"]) + 0.2

    p_w = softplus(raw["p_w"])
    p_tau = raw["p_tau"]
    p_pow = softplus(raw["p_pow"]) + 0.2

    unlock_w = softplus(raw["unlock_w"])
    unlock_tau = raw["unlock_tau"]
    unlock_k = softplus(raw["unlock_k"]) + 0.1

    m_term = m_w * relu(feats["m_log"] - m_tau) ** m_pow
    q_term = q_w * relu(feats["q_log"] - q_tau) ** q_pow
    mi_gate = 0.1 + 0.9 * sigmoid(unlock_k * (feats["q_log"] - unlock_tau))
    mi_term = mi_w * relu(feats["mi_log"] - mi_tau) ** mi_pow * mi_gate
    p_term = p_w * relu(feats["p_log"] - p_tau) ** p_pow
    share_term = share_w * np.maximum(feats["share"], 1e-9) ** share_b

    latent = base + m_term + q_term + mi_term + p_term + share_term

    round_scale = feats["round"].map(round_biases).fillna(0).to_numpy(dtype=float)
    city_scale = feats["market"].map(city_biases).fillna(0).to_numpy(dtype=float)
    context = np.exp(round_scale + city_scale)

    out = feats.copy()
    out["estimated_cpi"] = context * latent
    out["base_component"] = context * base
    out["share_component"] = context * share_term
    out["management_component"] = context * m_term
    out["quality_component"] = context * q_term
    out["market_component"] = context * mi_term
    out["price_component"] = context * p_term
    out["market_unlock_gate"] = mi_gate
    return out


def build_initial_vector(samples):
    rounds, cities = get_categories(samples)
    n = 18 + (len(rounds) - 1) + (len(cities) - 1)
    x0 = np.zeros(n, dtype=float)
    x0[:18] = np.array([
        -5.0,   # base
        -2.0,   # share_w
        0.0,    # share_b
        -2.0,   # m_w
        6.5,    # m_tau
        0.0,    # m_pow
        -1.0,   # q_w
        3.0,    # q_tau
        0.0,    # q_pow
        -1.0,   # mi_w
        10.0,   # mi_tau
        0.0,    # mi_pow
        -1.0,   # p_w
        -0.5,   # p_tau
        0.0,    # p_pow
        -1.0,   # unlock_w
        3.0,    # unlock_tau
        0.0,    # unlock_k
    ])
    return x0


def build_bounds(samples):
    feats = feature_frame(samples.rename(columns={
        "team24_management_index": "management_index",
        "team24_quality_index": "quality_index",
        "team24_market_index_report": "market_index",
        "team24_price_report": "price",
        "team24_market_share_report": "market_share",
    }))
    rounds, cities = get_categories(samples)
    n = 18 + (len(rounds) - 1) + (len(cities) - 1)
    lb = np.full(n, -5.0, dtype=float)
    ub = np.full(n, 5.0, dtype=float)
    lb[4] = float(feats["m_log"].min()); ub[4] = float(feats["m_log"].max())
    lb[7] = float(feats["q_log"].min()); ub[7] = float(feats["q_log"].max())
    lb[10] = float(feats["mi_log"].min()); ub[10] = float(feats["mi_log"].max())
    lb[13] = float(feats["p_log"].min()); ub[13] = float(feats["p_log"].max())
    lb[16] = float(feats["q_log"].min()); ub[16] = float(feats["q_log"].max())
    return lb, ub


def fit_model(teams, samples):
    rounds, cities = get_categories(samples)
    x0 = build_initial_vector(samples)
    lb, ub = build_bounds(samples)

    team24_rows = feature_frame(
        samples.rename(columns={
            "team24_management_index": "management_index",
            "team24_quality_index": "quality_index",
            "team24_market_index_report": "market_index",
            "team24_price_report": "price",
            "team24_market_share_report": "market_share",
        })
    )
    team24_rows["round"] = samples["round"]
    team24_rows["market"] = samples["market"]
    y = samples["actual_competitiveness"].to_numpy(dtype=float)

    def residuals(vec):
        pred = estimate_cpi(team24_rows, vec, rounds, cities)["estimated_cpi"].to_numpy(dtype=float)
        return pred - y

    starts = [x0]

    best = None
    best_cost = None
    for start in starts:
        start = np.clip(start, lb, ub)
        res = least_squares(residuals, x0=start, bounds=(lb, ub), max_nfev=1000, verbose=0)
        cost = float(np.mean(res.fun ** 2))
        if best is None or cost < best_cost:
            best = res
            best_cost = cost

    pred_df = estimate_cpi(team24_rows, best.x, rounds, cities)
    pred = pred_df["estimated_cpi"].to_numpy(dtype=float)
    err = pred - y
    mae = float(np.mean(np.abs(err)))
    rmse = float(np.sqrt(np.mean(err ** 2)))
    ss_tot = float(np.sum((y - y.mean()) ** 2))
    r2 = float(1 - np.sum(err ** 2) / ss_tot) if ss_tot > 0 else np.nan
    return best, pred_df, {"mae": mae, "rmse": rmse, "r2": r2, "objective": best_cost}


def export_peer_tables(teams, vec, samples):
    rounds, cities = get_categories(samples)
    frames = []
    for (_, _), group in teams.groupby(["round", "market"], sort=False):
        scored = estimate_cpi(group, vec, rounds, cities)
        scored = scored.sort_values("estimated_cpi", ascending=False).reset_index(drop=True)
        scored["estimated_rank_in_city"] = np.arange(1, len(scored) + 1)
        scored["actual_market_share_rank"] = scored["market_share"].rank(method="min", ascending=False)
        scored["is_team24"] = scored["team"] == TEAM_ID
        frames.append(scored)
    peer = pd.concat(frames, ignore_index=True)
    peer = peer.sort_values(
        ["round", "market", "estimated_rank_in_city", "team"],
        key=lambda s: s.map(round_sort_key) if s.name == "round" else s,
    ).reset_index(drop=True)

    cols = [
        "round", "market", "team", "estimated_rank_in_city", "estimated_cpi",
        "market_share", "actual_market_share_rank", "sales_volume",
        "management_index", "quality_index", "agents", "marketing_investment",
        "market_index", "price", "avg_price", "base_component", "share_component",
        "management_component", "quality_component", "market_component",
        "price_component", "market_unlock_gate", "round_original", "source_file", "is_team24"
    ]

    with pd.ExcelWriter(PEER_XLSX, engine="openpyxl") as writer:
        peer[cols].to_excel(writer, sheet_name="all_teams_r1_r6", index=False)
        peer[peer["team"] != TEAM_ID][cols].to_excel(writer, sheet_name="other_teams_only", index=False)
        for round_name in ["r1", "r2", "r3", "r4", "r5", "r6"]:
            peer[peer["round"] == round_name][cols].to_excel(writer, sheet_name=round_name, index=False)

    return peer


def main():
    teams, samples = prepare()
    result, fit_df, metrics = fit_model(teams, samples)
    rounds, cities = get_categories(samples)
    raw, round_biases, city_biases = unpack_params(result.x, rounds, cities)

    fit_export = samples.copy()
    fit_export["predicted_team24_cpi"] = fit_df["estimated_cpi"].to_numpy(dtype=float)
    fit_export["prediction_error"] = fit_export["predicted_team24_cpi"] - fit_export["actual_competitiveness"]
    fit_export["abs_error"] = fit_export["prediction_error"].abs()
    fit_export["base_component"] = fit_df["base_component"].to_numpy(dtype=float)
    fit_export["share_component"] = fit_df["share_component"].to_numpy(dtype=float)
    fit_export["management_component"] = fit_df["management_component"].to_numpy(dtype=float)
    fit_export["quality_component"] = fit_df["quality_component"].to_numpy(dtype=float)
    fit_export["market_component"] = fit_df["market_component"].to_numpy(dtype=float)
    fit_export["price_component"] = fit_df["price_component"].to_numpy(dtype=float)

    metrics_df = pd.DataFrame([{
        "model": "high_accuracy_absolute_cpi",
        "success": bool(result.success),
        **metrics,
        "n_params": len(result.x),
    }])

    with pd.ExcelWriter(OUTPUT_XLSX, engine="openpyxl") as writer:
        metrics_df.to_excel(writer, sheet_name="metrics", index=False)
        fit_export.to_excel(writer, sheet_name="team24_fit", index=False)
        pd.DataFrame([raw]).to_excel(writer, sheet_name="raw_params", index=False)
        pd.DataFrame([round_biases]).to_excel(writer, sheet_name="round_biases", index=False)
        pd.DataFrame([city_biases]).to_excel(writer, sheet_name="city_biases", index=False)

    peer = export_peer_tables(teams, result.x, samples)

    report = [
        "# Team24 High Accuracy Absolute CPI Model",
        "",
        f"- objective(MSE): {metrics['objective']:.8f}",
        f"- MAE: {metrics['mae']:.8f}",
        f"- RMSE: {metrics['rmse']:.8f}",
        f"- R2: {metrics['r2']:.8f}",
        f"- parameter_count: {len(result.x)}",
        "",
        "## Largest Errors",
        "",
        fit_export.sort_values("abs_error", ascending=False)[
            ["round", "market", "actual_competitiveness", "predicted_team24_cpi", "prediction_error"]
        ].head(12).to_string(index=False),
        "",
        f"Peer export: {PEER_XLSX}",
    ]
    OUTPUT_MD.write_text("\n".join(report), encoding="utf-8")

    print(f"Saved: {OUTPUT_XLSX}")
    print(f"Saved: {OUTPUT_MD}")
    print(f"Saved: {PEER_XLSX}")
    print(metrics_df.to_string(index=False))
    print(fit_export[['round','market','actual_competitiveness','predicted_team24_cpi','prediction_error']].to_string(index=False))
    print(peer[['round','market','team','estimated_cpi','market_share']].head(20).to_string(index=False))


if __name__ == "__main__":
    main()
