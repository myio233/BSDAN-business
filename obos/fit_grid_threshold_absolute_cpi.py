#!/usr/bin/env python3
from pathlib import Path
from itertools import product

import numpy as np
import pandas as pd

from analyze_team24_competitiveness import (
    build_sample_table,
    load_market_reports,
    load_summary_samples,
    round_sort_key,
)
from fit_team24_semidynamic_model import attach_lagged_features


BASE_DIR = Path("/mnt/c/Users/david/documents/ASDAN/表格/WYEF_results")
OUTPUT_XLSX = BASE_DIR / "team24_grid_threshold_absolute_cpi.xlsx"
OUTPUT_MD = BASE_DIR / "team24_grid_threshold_absolute_cpi.md"
PEER_XLSX = BASE_DIR / "r1_r6_peer_grid_threshold_absolute_cpi.xlsx"
TEAM_ID = "24"
ROUND_REMAP = {"r6": "r7", "r7": "r6"}
RIDGE = 1e-6
EPS = 1e-6


def remap_round(round_name):
    return ROUND_REMAP.get(round_name, round_name)


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


def base_features(df):
    out = df.copy()
    out["m_log"] = np.log1p(out["management_index"].fillna(0))
    out["q_log"] = np.log1p(out["quality_index"].fillna(0))
    out["mi_log"] = np.log1p(out["market_index"].fillna(0))
    price_ratio = (out["avg_price"].fillna(1) / out["price"].replace(0, np.nan)).fillna(1e-9)
    out["p_log"] = np.log(np.maximum(price_ratio, 1e-9))
    out["share"] = out["market_share"].fillna(0)
    return out


def build_design_matrix(df, taus, round_levels=None, city_levels=None):
    tau_m, tau_q, tau_mi, tau_p = taus
    feats = base_features(df)

    X = pd.DataFrame(index=feats.index)
    X["const"] = 1.0
    X["share"] = feats["share"]
    X["share_sqrt"] = np.sqrt(np.maximum(feats["share"], 0))
    X["share_sq"] = feats["share"] ** 2

    X["m_relu"] = np.maximum(feats["m_log"] - tau_m, 0)
    X["q_relu"] = np.maximum(feats["q_log"] - tau_q, 0)
    X["mi_relu"] = np.maximum(feats["mi_log"] - tau_mi, 0)
    X["p_relu"] = np.maximum(feats["p_log"] - tau_p, 0)

    X["q_x_mi"] = X["q_relu"] * X["mi_relu"]
    X["m_x_q"] = X["m_relu"] * X["q_relu"]
    X["share_x_q"] = X["share"] * X["q_relu"]
    X["share_x_mi"] = X["share"] * X["mi_relu"]

    if round_levels is not None:
        round_series = pd.Series(
            pd.Categorical(feats["round"], categories=round_levels, ordered=True),
            index=feats.index,
        )
    else:
        round_series = feats["round"]
    if city_levels is not None:
        city_series = pd.Series(
            pd.Categorical(feats["market"], categories=city_levels, ordered=True),
            index=feats.index,
        )
    else:
        city_series = feats["market"]

    round_dummies = pd.get_dummies(round_series, prefix="round", drop_first=True, dtype=float)
    city_dummies = pd.get_dummies(city_series, prefix="city", drop_first=True, dtype=float)

    X = pd.concat([X, round_dummies, city_dummies], axis=1)
    return X


def ridge_fit_predict(X, y):
    X_mat = X.to_numpy(dtype=float)
    y_vec = np.asarray(y, dtype=float)
    XtX = X_mat.T @ X_mat
    beta = np.linalg.solve(XtX + RIDGE * np.eye(XtX.shape[0]), X_mat.T @ y_vec)
    pred = X_mat @ beta
    return beta, pred


def metrics(y, pred):
    err = pred - y
    mae = float(np.mean(np.abs(err)))
    rmse = float(np.sqrt(np.mean(err ** 2)))
    ss_tot = float(np.sum((y - y.mean()) ** 2))
    r2 = float(1 - np.sum(err ** 2) / ss_tot) if ss_tot > 0 else np.nan
    return mae, rmse, r2


def fit_best_model(samples):
    team24 = samples.rename(
        columns={
            "team24_management_index": "management_index",
            "team24_quality_index": "quality_index",
            "team24_market_index_report": "market_index",
            "team24_price_report": "price",
            "team24_market_share_report": "market_share",
        }
    ).copy()

    feats = base_features(team24)
    grids = {
        "m": np.quantile(feats["m_log"], [0.2, 0.4, 0.6, 0.8]),
        "q": np.quantile(feats["q_log"], [0.2, 0.4, 0.6, 0.8]),
        "mi": np.quantile(feats["mi_log"], [0.2, 0.4, 0.6, 0.8]),
        "p": np.quantile(feats["p_log"], [0.2, 0.4, 0.6, 0.8]),
    }
    round_levels = sorted(team24["round"].dropna().unique(), key=round_sort_key)
    city_levels = sorted(team24["market"].dropna().unique())

    y = team24["actual_competitiveness"].to_numpy(dtype=float)
    y_log = np.log(y + EPS)
    best = None

    for taus in product(grids["m"], grids["q"], grids["mi"], grids["p"]):
        X = build_design_matrix(team24, taus, round_levels=round_levels, city_levels=city_levels)
        beta, pred_log = ridge_fit_predict(X, y_log)
        pred = np.exp(pred_log) - EPS
        mae, rmse, r2 = metrics(y, pred)
        if best is None or rmse < best["rmse"]:
            best = {
                "taus": taus,
                "columns": X.columns.tolist(),
                "beta": beta,
                "round_levels": round_levels,
                "city_levels": city_levels,
                "pred": pred,
                "mae": mae,
                "rmse": rmse,
                "r2": r2,
            }

    return best, team24


def apply_model(df, model):
    X = build_design_matrix(
        df,
        model["taus"],
        round_levels=model.get("round_levels"),
        city_levels=model.get("city_levels"),
    )
    X = X.reindex(columns=model["columns"], fill_value=0.0)
    X_mat = X.to_numpy(dtype=float)
    pred_log = X_mat @ model["beta"]
    pred = np.exp(pred_log) - EPS
    out = base_features(df)
    out["estimated_cpi"] = np.maximum(pred, 0.0)
    out["estimated_log_cpi"] = pred_log

    contribution_df = pd.DataFrame(
        X_mat * model["beta"],
        index=out.index,
        columns=[f"term_{col}" for col in X.columns],
    )
    out = pd.concat([out, contribution_df], axis=1)
    return out


def export_results(teams, samples, model, team24):
    fit_df = apply_model(team24, model)
    fit_export = samples.copy()
    fit_export["predicted_team24_cpi"] = fit_df["estimated_cpi"].to_numpy(dtype=float)
    fit_export["prediction_error"] = fit_export["predicted_team24_cpi"] - fit_export["actual_competitiveness"]
    fit_export["abs_error"] = fit_export["prediction_error"].abs()

    metrics_df = pd.DataFrame([{
        "model": "grid_threshold_absolute_cpi",
        "tau_m": model["taus"][0],
        "tau_q": model["taus"][1],
        "tau_mi": model["taus"][2],
        "tau_p": model["taus"][3],
        "mae": model["mae"],
        "rmse": model["rmse"],
        "r2": model["r2"],
        "n_features": len(model["columns"]),
    }])

    with pd.ExcelWriter(OUTPUT_XLSX, engine="openpyxl") as writer:
        metrics_df.to_excel(writer, sheet_name="metrics", index=False)
        fit_export.to_excel(writer, sheet_name="team24_fit", index=False)
        pd.DataFrame({"feature": model["columns"], "coef": model["beta"]}).to_excel(writer, sheet_name="coefficients", index=False)

    peer = apply_model(teams, model)
    peer["actual_market_share_rank"] = peer.groupby(["round", "market"])["market_share"].rank(method="min", ascending=False)
    peer["estimated_rank_in_city"] = peer.groupby(["round", "market"])["estimated_cpi"].rank(method="first", ascending=False).astype(int)
    peer["is_team24"] = peer["team"] == TEAM_ID
    peer = peer.sort_values(
        ["round", "market", "estimated_rank_in_city", "team"],
        key=lambda s: s.map(round_sort_key) if s.name == "round" else s,
    ).reset_index(drop=True)

    peer_cols = [
        "round", "market", "team", "estimated_rank_in_city", "estimated_cpi", "market_share",
        "actual_market_share_rank", "sales_volume", "management_index", "quality_index", "agents",
        "marketing_investment", "market_index", "price", "avg_price", "m_log", "q_log", "mi_log", "p_log",
        "estimated_log_cpi",
        "round_original", "source_file", "is_team24"
    ]
    term_cols = [c for c in peer.columns if c.startswith("term_")]

    with pd.ExcelWriter(PEER_XLSX, engine="openpyxl") as writer:
        peer[peer_cols].to_excel(writer, sheet_name="all_teams_r1_r6", index=False)
        peer[peer["team"] != TEAM_ID][peer_cols].to_excel(writer, sheet_name="other_teams_only", index=False)
        peer[["round", "market", "team", "estimated_cpi", "estimated_log_cpi", *term_cols]].to_excel(
            writer,
            sheet_name="term_breakdown",
            index=False,
        )
        for round_name in ["r1", "r2", "r3", "r4", "r5", "r6"]:
            peer[peer["round"] == round_name][peer_cols].to_excel(writer, sheet_name=round_name, index=False)

    report = [
        "# Team24 Grid Threshold Absolute CPI",
        "",
        f"- tau_m = {model['taus'][0]:.6f}",
        f"- tau_q = {model['taus'][1]:.6f}",
        f"- tau_mi = {model['taus'][2]:.6f}",
        f"- tau_p = {model['taus'][3]:.6f}",
        f"- MAE = {model['mae']:.8f}",
        f"- RMSE = {model['rmse']:.8f}",
        f"- R2 = {model['r2']:.8f}",
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

    return metrics_df, fit_export, peer


def main():
    teams, samples = prepare()
    model, team24 = fit_best_model(samples)
    metrics_df, fit_export, peer = export_results(teams, samples, model, team24)

    print(f"Saved: {OUTPUT_XLSX}")
    print(f"Saved: {OUTPUT_MD}")
    print(f"Saved: {PEER_XLSX}")
    print(metrics_df.to_string(index=False))
    print(fit_export[["round", "market", "actual_competitiveness", "predicted_team24_cpi", "prediction_error"]].to_string(index=False))
    print(peer[["round", "market", "team", "estimated_cpi", "market_share"]].head(20).to_string(index=False))


if __name__ == "__main__":
    main()
