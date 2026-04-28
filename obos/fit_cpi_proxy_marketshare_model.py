#!/usr/bin/env python3
from pathlib import Path

import numpy as np
import pandas as pd

from fit_constrained_global_cpi_model import prepare_data
from analyze_team24_competitiveness import round_sort_key


BASE_DIR = Path("/mnt/c/Users/david/documents/ASDAN/表格/WYEF_results")
OUTPUT_XLSX = BASE_DIR / "r1_r6_peer_global_constrained_cpi.xlsx"
MODEL_XLSX = BASE_DIR / "global_constrained_cpi_model.xlsx"
OUTPUT_MD = BASE_DIR / "global_constrained_cpi_model.md"
EPS = 1e-12


def writable_path(path):
    try:
        with open(path, "ab"):
            pass
        return path
    except OSError:
        return path.with_name(f"{path.stem}_updated{path.suffix}")


def fit_power_mapping(df):
    fit_df = df[(df["estimated_cpi"] > 0) & (df["market_share_clean"] > 0)].copy()
    x = np.log(fit_df["estimated_cpi"].to_numpy(dtype=float))
    y = np.log(fit_df["market_share_clean"].to_numpy(dtype=float))

    X = np.column_stack([np.ones_like(x), x])
    beta = np.linalg.lstsq(X, y, rcond=None)[0]
    a = float(np.exp(beta[0]))
    b = float(beta[1])

    return a, b


def compute_metrics(actual, pred):
    err = pred - actual
    mae = float(np.mean(np.abs(err)))
    rmse = float(np.sqrt(np.mean(err ** 2)))
    ss_tot = float(np.sum((actual - actual.mean()) ** 2))
    r2 = float(1 - np.sum(err ** 2) / ss_tot) if ss_tot > 0 else np.nan
    corr = float(np.corrcoef(actual, pred)[0, 1]) if len(actual) > 1 else np.nan
    return {
        "mae": mae,
        "rmse": rmse,
        "r2": r2,
        "corr": corr,
    }


def main():
    teams, _ = prepare_data()
    df = teams.copy()
    output_xlsx = writable_path(OUTPUT_XLSX)
    model_xlsx = writable_path(MODEL_XLSX)

    df["estimated_cpi"] = df["abs_share_from_market_size"].fillna(df["market_share_clean"]).fillna(0.0)
    team24_mask = df["team"] == "24"
    df.loc[team24_mask, "estimated_cpi"] = df.loc[team24_mask, "team24_actual_cpi"]
    df["cpi_source"] = np.where(team24_mask, "team24_actual_cpi", "sales_volume_over_market_size")
    a, b = fit_power_mapping(df)
    df["predicted_market_share"] = a * np.power(np.maximum(df["estimated_cpi"], 0.0), b)
    df["predicted_market_share"] = df["predicted_market_share"].fillna(0.0)

    # Backward-compatible aliases in the existing workbook.
    df["share_target"] = df["market_share_clean"]
    df["predicted_share"] = df["predicted_market_share"]
    df["marketshare_fit_error"] = df["predicted_market_share"] - df["market_share_clean"]
    df["marketshare_abs_error"] = df["marketshare_fit_error"].abs()
    df["marketshare_ratio_pred_actual"] = df["predicted_market_share"] / np.maximum(df["market_share_clean"], EPS)

    df["estimated_cpi_rank"] = df.groupby(["round", "market"])["estimated_cpi"].rank(method="min", ascending=False)
    df["market_share_rank"] = df.groupby(["round", "market"])["market_share_clean"].rank(method="min", ascending=False)
    df["predicted_market_share_rank"] = df.groupby(["round", "market"])["predicted_market_share"].rank(method="min", ascending=False)
    df["rank_gap_vs_marketshare"] = df["estimated_cpi_rank"] - df["market_share_rank"]
    df["is_team24"] = team24_mask

    df = df.sort_values(
        ["round", "market", "estimated_cpi_rank", "team"],
        key=lambda s: s.map(round_sort_key) if s.name == "round" else s,
    ).reset_index(drop=True)

    metrics = compute_metrics(
        df["market_share_clean"].to_numpy(dtype=float),
        df["predicted_market_share"].to_numpy(dtype=float),
    )
    team24_metrics = compute_metrics(
        df.loc[df["is_team24"], "market_share_clean"].to_numpy(dtype=float),
        df.loc[df["is_team24"], "predicted_market_share"].to_numpy(dtype=float),
    )

    metrics_df = pd.DataFrame([{
        "model": "cpi_proxy_power_fit_to_marketshare",
        "estimated_cpi_definition": "team24 uses actual CPI; others use sales_volume / market_size",
        "fitted_target": "reported market_share_clean",
        "power_a": a,
        "power_b": b,
        "n_rows": len(df),
        **metrics,
        "team24_mae": team24_metrics["mae"],
        "team24_rmse": team24_metrics["rmse"],
        "team24_r2": team24_metrics["r2"],
        "team24_corr": team24_metrics["corr"],
    }])

    cols = [
        "round", "market", "team", "estimated_cpi_rank", "estimated_cpi", "market_share_clean",
        "predicted_market_share", "share_target", "predicted_share",
        "marketshare_fit_error", "marketshare_abs_error", "marketshare_ratio_pred_actual",
        "market_share_rank", "predicted_market_share_rank", "rank_gap_vs_marketshare",
        "sales_volume", "market_size", "total_sales_volume", "sales_share_raw", "abs_share_from_market_size",
        "team24_actual_cpi", "cpi_source",
        "management_index", "quality_index", "agents", "marketing_investment", "market_index", "price",
        "avg_price", "share_target_source", "round_original", "source_file", "is_team24",
    ]

    largest_errors = df.nlargest(30, "marketshare_abs_error")[
        ["round", "market", "team", "estimated_cpi", "market_share_clean", "predicted_market_share", "marketshare_fit_error"]
    ]

    with pd.ExcelWriter(output_xlsx, engine="openpyxl") as writer:
        df[cols].to_excel(writer, sheet_name="all_teams_r1_r6", index=False)
        df[~df["is_team24"]][cols].to_excel(writer, sheet_name="other_teams_only", index=False)
        metrics_df.to_excel(writer, sheet_name="metrics", index=False)
        largest_errors.to_excel(writer, sheet_name="largest_errors", index=False)
        for round_name in ["r1", "r2", "r3", "r4", "r5", "r6"]:
            df[df["round"] == round_name][cols].to_excel(writer, sheet_name=round_name, index=False)

    with pd.ExcelWriter(model_xlsx, engine="openpyxl") as writer:
        metrics_df.to_excel(writer, sheet_name="metrics", index=False)
        pd.DataFrame(
            [
                {"parameter": "power_a", "value": a},
                {"parameter": "power_b", "value": b},
            ]
        ).to_excel(writer, sheet_name="parameters", index=False)
        df[df["is_team24"]][
            ["round", "market", "estimated_cpi", "market_share_clean", "predicted_market_share", "marketshare_fit_error"]
        ].to_excel(writer, sheet_name="team24_fit", index=False)
        largest_errors.to_excel(writer, sheet_name="largest_errors", index=False)

    report = [
        "# CPI Proxy To Marketshare Fit",
        "",
        "- estimated_cpi: Team 24 uses actual CPI; other teams use sales_volume / market_size",
        "- predicted_market_share = a * estimated_cpi ^ b",
        f"- a = {a:.10f}",
        f"- b = {b:.10f}",
        "",
        "## Metrics",
        f"- MAE = {metrics['mae']:.8f}",
        f"- RMSE = {metrics['rmse']:.8f}",
        f"- R2 = {metrics['r2']:.8f}",
        f"- Corr = {metrics['corr']:.8f}",
        f"- Team24 MAE = {team24_metrics['mae']:.8f}",
        f"- Team24 RMSE = {team24_metrics['rmse']:.8f}",
    ]
    OUTPUT_MD.write_text("\n".join(report), encoding="utf-8")

    print(f"Saved: {output_xlsx}")
    print(f"Saved: {model_xlsx}")
    print(metrics_df.to_string(index=False))
    print(df[["round", "market", "team", "estimated_cpi", "market_share_clean", "predicted_market_share"]].head(20).to_string(index=False))


if __name__ == "__main__":
    main()
    output_xlsx = writable_path(OUTPUT_XLSX)
    model_xlsx = writable_path(MODEL_XLSX)
