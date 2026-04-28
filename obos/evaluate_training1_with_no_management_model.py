#!/usr/bin/env python3
import re
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingRegressor

from analyze_team24_competitiveness import round_sort_key
from fit_universal_cpi_model import prepare_universal_table
from fit_universal_no_management_model import build_no_management_feature_matrix
from fit_weighted_theoretical_cpi_model import BASE_DIR, EPS, base_features, build_context, clean_market_table, metrics


TRAINING1_DIR = Path("/mnt/c/Users/david/documents/ASDAN/表格/training1")
OUTPUT_XLSX = BASE_DIR / "training1_no_management_universal_cpi_comparison.xlsx"
TEAM_ID = "16"
MARKET_MAP = {
    "GZ Report": "Guangzhou",
    "SZ Report": "Suzhou",
    "Guangzhou": "Guangzhou",
    "Suzhou": "Suzhou",
}
MODEL_PARAMS = {
    "anchor_w": 500.0,
    "n_estimators": 500,
    "learning_rate": 0.05,
    "max_depth": 5,
    "min_samples_leaf": 1,
    "subsample": 1.0,
}


def parse_training1_round(path):
    round_num = int(re.search(r"(\d+)", path.stem).group(1))
    round_name = f"r{round_num}"

    sales_df = pd.read_excel(path, sheet_name="Sales", header=None)
    own_actual = {}
    for row_idx in range(7, len(sales_df)):
        market = sales_df.iloc[row_idx, 0]
        if pd.isna(market):
            continue
        own_actual[MARKET_MAP.get(str(market).strip(), str(market).strip())] = float(sales_df.iloc[row_idx, 1])

    rows = []
    for sheet in ["GZ Report", "SZ Report"]:
        df = pd.read_excel(path, sheet_name=sheet, header=None)
        market = MARKET_MAP[sheet]
        market_size = float(df.iloc[2, 2])
        total_sales_volume = float(df.iloc[2, 3])
        avg_price = float(df.iloc[2, 4])

        row_idx = 5
        while row_idx < len(df):
            team = df.iloc[row_idx, 0]
            if pd.isna(team) or not str(team).strip().isdigit():
                break

            team = str(team).strip()
            agents = float(df.iloc[row_idx, 1])
            marketing = float(df.iloc[row_idx, 2])
            quality = float(df.iloc[row_idx, 3])
            price = float(df.iloc[row_idx, 4])
            sales_volume = float(df.iloc[row_idx, 5])
            market_share = float(df.iloc[row_idx, 6]) if pd.notna(df.iloc[row_idx, 6]) else np.nan

            rows.append(
                {
                    "competition": "training1",
                    "round": round_name,
                    "market": market,
                    "team": team,
                    "agents": agents,
                    "marketing_investment": marketing,
                    "quality_index": quality,
                    "price": price,
                    "sales_volume": sales_volume,
                    "market_share": market_share,
                    "market_size": market_size,
                    "total_sales_volume": total_sales_volume,
                    "avg_price": avg_price,
                    "market_index": (1 + 0.1 * agents) * marketing,
                    "management_index": 0.0,
                    "actual_real_cpi": own_actual.get(market) if team == TEAM_ID else np.nan,
                    "actual_real_cpi_source": "Sales sheet Competitive Power" if team == TEAM_ID else "",
                    "source_file": path.name,
                }
            )
            row_idx += 1

    return rows


def load_training1_table():
    rows = []
    for path in sorted(TRAINING1_DIR.glob("round_*.xlsx")):
        rows.extend(parse_training1_round(path))
    df = pd.DataFrame(rows)
    df["round_order"] = df["round"].map(round_sort_key)
    return df.sort_values(["round_order", "market", "team"]).reset_index(drop=True)


def attach_lags(df):
    out = df.copy().sort_values(["team", "round_order", "market"]).reset_index(drop=True)
    out["prev_team_management_index"] = 0.0

    team_state = (
        out.groupby(["team", "round", "round_order"], as_index=False)
        .agg(team_quality_index=("quality_index", "mean"))
        .sort_values(["team", "round_order"])
        .reset_index(drop=True)
    )
    team_state["prev_team_quality_index"] = team_state.groupby("team")["team_quality_index"].shift(1)
    out = out.merge(team_state[["team", "round", "prev_team_quality_index"]], on=["team", "round"], how="left")
    return out


def train_no_management_estimator():
    train_df = prepare_universal_table().copy()
    feats = base_features(train_df)
    round_levels = sorted(train_df["round"].dropna().unique(), key=round_sort_key)
    city_levels = sorted(train_df["market"].dropna().unique())
    context = build_context(feats, round_levels, city_levels)
    X = build_no_management_feature_matrix(feats, context)

    labeled_mask = train_df["fit_target"].notna().to_numpy(dtype=bool)
    y_log = np.log(np.maximum(train_df.loc[labeled_mask, "fit_target"].to_numpy(dtype=float), EPS))
    sample_weights = np.where(train_df.loc[labeled_mask, "is_anchor_real"].to_numpy(dtype=bool), MODEL_PARAMS["anchor_w"], 1.0)

    est = GradientBoostingRegressor(
        loss="squared_error",
        n_estimators=MODEL_PARAMS["n_estimators"],
        learning_rate=MODEL_PARAMS["learning_rate"],
        max_depth=MODEL_PARAMS["max_depth"],
        min_samples_leaf=MODEL_PARAMS["min_samples_leaf"],
        subsample=MODEL_PARAMS["subsample"],
        random_state=42,
    )
    est.fit(X.loc[labeled_mask], y_log, sample_weight=sample_weights)
    return est, X.columns.tolist(), round_levels, city_levels


def reorder_columns(df):
    preferred = [
        "round",
        "market",
        "team",
        "marketshare_clean",
        "predicted_theoretical_cpi",
        "actual_real_cpi",
        "prediction_minus_actual_real_cpi",
        "prediction_minus_marketshare_clean",
        "theoretical_cpi_rank",
        "marketshare_clean_rank",
        "rank_gap_vs_marketshare_clean",
        "quality_index",
        "market_index",
        "price",
        "agents",
        "marketing_investment",
        "sales_volume",
        "market_size",
        "avg_price",
        "source_file",
    ]
    remaining = [c for c in df.columns if c not in preferred]
    return df[[c for c in preferred if c in df.columns] + remaining]


def main():
    estimator, train_columns, round_levels, city_levels = train_no_management_estimator()
    df = load_training1_table()
    df = attach_lags(df)
    df = clean_market_table(df)

    feats = base_features(df)
    context = build_context(feats, round_levels, city_levels)
    X = build_no_management_feature_matrix(feats, context).reindex(columns=train_columns, fill_value=0.0)
    pred = np.maximum(np.exp(estimator.predict(X)) - EPS, 0.0)

    scored = df.copy()
    scored["predicted_theoretical_cpi"] = pred
    scored["prediction_minus_actual_real_cpi"] = scored["predicted_theoretical_cpi"] - scored["actual_real_cpi"]
    scored["prediction_minus_marketshare_clean"] = scored["predicted_theoretical_cpi"] - scored["marketshare_clean"]
    scored["theoretical_cpi_rank"] = scored.groupby(["round", "market"])["predicted_theoretical_cpi"].rank(method="min", ascending=False)
    scored["marketshare_clean_rank"] = scored.groupby(["round", "market"])["marketshare_clean"].rank(method="min", ascending=False)
    scored["rank_gap_vs_marketshare_clean"] = scored["theoretical_cpi_rank"] - scored["marketshare_clean_rank"]
    scored = reorder_columns(scored)

    team16_only = scored[scored["team"] == TEAM_ID].copy()
    others_only = scored[scored["team"] != TEAM_ID].copy()

    team16_metrics = metrics(team16_only["actual_real_cpi"], team16_only["predicted_theoretical_cpi"])
    others_metrics = metrics(others_only["marketshare_clean"], others_only["predicted_theoretical_cpi"])

    summary_df = pd.DataFrame(
        [
            {
                "scenario": "strict_no_management_retrained_model",
                "team16_r2": team16_metrics["r2"],
                "team16_rmse": team16_metrics["rmse"],
                "team16_mae": team16_metrics["mae"],
                "others_proxy_r2": others_metrics["r2"],
                "others_proxy_rmse": others_metrics["rmse"],
                "universal_model": "gradient_boosting_no_management_tree",
                "universal_params": "anchor_w=500.0, n_estimators=500, lr=0.05, max_depth=5, leaf=1, subsample=1.0",
                "note": "retrained universal model with management dimension removed from training and scoring",
            }
        ]
    )

    with pd.ExcelWriter(OUTPUT_XLSX, engine="openpyxl") as writer:
        summary_df.to_excel(writer, sheet_name="summary", index=False)
        scored.to_excel(writer, sheet_name="all_predictions", index=False)
        team16_only.to_excel(writer, sheet_name="team16_real_only", index=False)
        others_only.to_excel(writer, sheet_name="other_teams_only", index=False)

    print(f"Saved: {OUTPUT_XLSX}")
    print(summary_df.to_string(index=False))


if __name__ == "__main__":
    main()
