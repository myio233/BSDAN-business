#!/usr/bin/env python3
import re
from itertools import product
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingRegressor

from analyze_team24_competitiveness import round_sort_key
from fit_universal_cpi_model import prepare_universal_table
from fit_weighted_theoretical_cpi_model import (
    BASE_DIR,
    EPS,
    base_features,
    build_context,
    build_tree_feature_matrix,
    clean_market_table,
    metrics,
)


TRAINING1_DIR = Path("/mnt/c/Users/david/documents/ASDAN/表格/training1")
OUTPUT_XLSX = BASE_DIR / "training1_universal_cpi_comparison.xlsx"
TEAM_ID = "16"
MARKET_MAP = {
    "GZ Report": "Guangzhou",
    "SZ Report": "Suzhou",
    "Guangzhou": "Guangzhou",
    "Suzhou": "Suzhou",
}

# Fixed universal model selected from universal_cpi_model.xlsx
UNIVERSAL_MODEL_PARAMS = {
    "anchor_w": 500.0,
    "n_estimators": 500,
    "learning_rate": 0.05,
    "max_depth": 5,
    "min_samples_leaf": 1,
    "subsample": 0.7,
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


def attach_training1_lags(df):
    out = df.copy().sort_values(["team", "round_order", "market"]).reset_index(drop=True)
    team_state = (
        out.groupby(["team", "round", "round_order"], as_index=False)
        .agg(
            team_management_index=("management_index", "mean"),
            team_quality_index=("quality_index", "mean"),
        )
        .sort_values(["team", "round_order"])
        .reset_index(drop=True)
    )
    team_state["prev_team_management_index"] = team_state.groupby("team")["team_management_index"].shift(1)
    team_state["prev_team_quality_index"] = team_state.groupby("team")["team_quality_index"].shift(1)

    out = out.merge(
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
    return out


def train_universal_estimator():
    train_df = prepare_universal_table()
    feats = base_features(train_df)
    round_levels = sorted(train_df["round"].dropna().unique(), key=round_sort_key)
    city_levels = sorted(train_df["market"].dropna().unique())
    context = build_context(feats, round_levels, city_levels)
    X = build_tree_feature_matrix(feats, context)

    labeled_mask = train_df["fit_target"].notna().to_numpy(dtype=bool)
    y_log = np.log(np.maximum(train_df.loc[labeled_mask, "fit_target"].to_numpy(dtype=float), EPS))
    sample_weights = np.where(train_df.loc[labeled_mask, "is_anchor_real"].to_numpy(dtype=bool), UNIVERSAL_MODEL_PARAMS["anchor_w"], 1.0)

    est = GradientBoostingRegressor(
        loss="squared_error",
        n_estimators=UNIVERSAL_MODEL_PARAMS["n_estimators"],
        learning_rate=UNIVERSAL_MODEL_PARAMS["learning_rate"],
        max_depth=UNIVERSAL_MODEL_PARAMS["max_depth"],
        min_samples_leaf=UNIVERSAL_MODEL_PARAMS["min_samples_leaf"],
        subsample=UNIVERSAL_MODEL_PARAMS["subsample"],
        random_state=42,
    )
    est.fit(X.loc[labeled_mask], y_log, sample_weight=sample_weights)
    return est, X.columns.tolist(), round_levels, city_levels


def evaluate_management_formula(raw_df, estimator, train_columns, round_levels, city_levels, aq, bagents, cmkt):
    df = raw_df.copy()
    df["management_index"] = (
        aq * df["quality_index"].to_numpy(dtype=float)
        + bagents * df["agents"].to_numpy(dtype=float)
        + cmkt * df["marketing_investment"].to_numpy(dtype=float)
    )
    df["management_formula"] = f"{aq}*quality_index + {bagents}*agents + {cmkt}*marketing_investment"
    df = attach_training1_lags(df)
    df = clean_market_table(df)

    feats = base_features(df)
    context = build_context(feats, round_levels, city_levels)
    X = build_tree_feature_matrix(feats, context).reindex(columns=train_columns, fill_value=0.0)
    pred = np.maximum(np.exp(estimator.predict(X)) - EPS, 0.0)

    team16_mask = df["team"].eq(TEAM_ID).to_numpy(dtype=bool)
    team16_metrics = metrics(df.loc[team16_mask, "actual_real_cpi"], pred[team16_mask])
    others_metrics = metrics(df.loc[~team16_mask, "marketshare_clean"], pred[~team16_mask])

    out = df.copy()
    out["predicted_theoretical_cpi"] = pred
    out["prediction_minus_actual_real_cpi"] = out["predicted_theoretical_cpi"] - out["actual_real_cpi"]
    out["prediction_minus_marketshare_clean"] = out["predicted_theoretical_cpi"] - out["marketshare_clean"]
    out["theoretical_cpi_rank"] = out.groupby(["round", "market"])["predicted_theoretical_cpi"].rank(method="min", ascending=False)
    out["marketshare_clean_rank"] = out.groupby(["round", "market"])["marketshare_clean"].rank(method="min", ascending=False)
    out["rank_gap_vs_marketshare_clean"] = out["theoretical_cpi_rank"] - out["marketshare_clean_rank"]

    return out, team16_metrics, others_metrics


def search_best_management_formula(raw_df, estimator, train_columns, round_levels, city_levels):
    rows = []
    best = None

    coarse_grid = product(
        [0, 0.5, 1, 2, 4, 8, 12],
        [0, 100, 300, 500, 800, 1200],
        [0, 0.0002, 0.0005, 0.001, 0.002],
    )
    for aq, bagents, cmkt in coarse_grid:
        scored, team16_metrics, others_metrics = evaluate_management_formula(
            raw_df,
            estimator,
            train_columns,
            round_levels,
            city_levels,
            aq,
            bagents,
            cmkt,
        )
        row = {
            "stage": "coarse",
            "a_quality": aq,
            "b_agents": bagents,
            "c_marketing": cmkt,
            "team16_r2": team16_metrics["r2"],
            "team16_rmse": team16_metrics["rmse"],
            "team16_mae": team16_metrics["mae"],
            "others_proxy_r2": others_metrics["r2"],
            "others_proxy_rmse": others_metrics["rmse"],
        }
        rows.append(row)
        key = (team16_metrics["r2"], -team16_metrics["rmse"], others_metrics["r2"])
        if best is None or key > best["key"]:
            best = {
                "aq": aq,
                "bagents": bagents,
                "cmkt": cmkt,
                "key": key,
                "scored": scored,
                "team16_metrics": team16_metrics,
                "others_metrics": others_metrics,
            }

    fine_a = sorted({max(best["aq"] - 0.5, 0.0), max(best["aq"] - 0.25, 0.0), best["aq"], best["aq"] + 0.25, best["aq"] + 0.5})
    fine_b = sorted({max(best["bagents"] - 100, 0.0), max(best["bagents"] - 50, 0.0), best["bagents"], best["bagents"] + 25, best["bagents"] + 50, best["bagents"] + 100})
    fine_c = sorted({max(best["cmkt"] - 0.0005, 0.0), max(best["cmkt"] - 0.0002, 0.0), best["cmkt"], best["cmkt"] + 0.0001, best["cmkt"] + 0.0002, best["cmkt"] + 0.0005})

    for aq, bagents, cmkt in product(fine_a, fine_b, fine_c):
        scored, team16_metrics, others_metrics = evaluate_management_formula(
            raw_df,
            estimator,
            train_columns,
            round_levels,
            city_levels,
            aq,
            bagents,
            cmkt,
        )
        row = {
            "stage": "fine",
            "a_quality": aq,
            "b_agents": bagents,
            "c_marketing": cmkt,
            "team16_r2": team16_metrics["r2"],
            "team16_rmse": team16_metrics["rmse"],
            "team16_mae": team16_metrics["mae"],
            "others_proxy_r2": others_metrics["r2"],
            "others_proxy_rmse": others_metrics["rmse"],
        }
        rows.append(row)
        key = (team16_metrics["r2"], -team16_metrics["rmse"], others_metrics["r2"])
        if key > best["key"]:
            best = {
                "aq": aq,
                "bagents": bagents,
                "cmkt": cmkt,
                "key": key,
                "scored": scored,
                "team16_metrics": team16_metrics,
                "others_metrics": others_metrics,
            }

    return best, pd.DataFrame(rows).sort_values(["team16_r2", "team16_rmse", "others_proxy_r2"], ascending=[False, True, False]).reset_index(drop=True)


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
        "management_index",
        "management_formula",
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
    raw_df = load_training1_table()
    estimator, train_columns, round_levels, city_levels = train_universal_estimator()
    strict_scored, strict_team16_metrics, strict_others_metrics = evaluate_management_formula(
        raw_df,
        estimator,
        train_columns,
        round_levels,
        city_levels,
        0.0,
        0.0,
        0.0,
    )
    best, search_df = search_best_management_formula(raw_df, estimator, train_columns, round_levels, city_levels)

    scored = reorder_columns(strict_scored)
    team16_only = scored[scored["team"] == TEAM_ID].copy()
    others_only = scored[scored["team"] != TEAM_ID].copy()
    proxy_scored = reorder_columns(best["scored"])

    summary_df = pd.DataFrame(
        [
            {
                "scenario": "strict_no_management",
                "selected_management_formula": "0",
                "team16_r2": strict_team16_metrics["r2"],
                "team16_rmse": strict_team16_metrics["rmse"],
                "team16_mae": strict_team16_metrics["mae"],
                "others_proxy_r2": strict_others_metrics["r2"],
                "others_proxy_rmse": strict_others_metrics["rmse"],
                "universal_model": "gradient_boosting_threshold_tree",
                "universal_params": "anchor_w=500.0, n_estimators=500, lr=0.05, max_depth=5, leaf=1, subsample=0.7",
                "note": "official comparison under the user-confirmed rule that this competition has no management index",
            },
            {
                "scenario": "diagnostic_best_proxy",
                "selected_management_formula": f"{best['aq']}*quality_index + {best['bagents']}*agents + {best['cmkt']}*marketing_investment",
                "team16_r2": best["team16_metrics"]["r2"],
                "team16_rmse": best["team16_metrics"]["rmse"],
                "team16_mae": best["team16_metrics"]["mae"],
                "others_proxy_r2": best["others_metrics"]["r2"],
                "others_proxy_rmse": best["others_metrics"]["rmse"],
                "universal_model": "gradient_boosting_threshold_tree",
                "universal_params": "anchor_w=500.0, n_estimators=500, lr=0.05, max_depth=5, leaf=1, subsample=0.7",
                "note": "diagnostic only; not rule-consistent for this competition",
            },
        ]
    )

    with pd.ExcelWriter(OUTPUT_XLSX, engine="openpyxl") as writer:
        summary_df.to_excel(writer, sheet_name="summary", index=False)
        search_df.to_excel(writer, sheet_name="management_search", index=False)
        scored.to_excel(writer, sheet_name="all_predictions", index=False)
        team16_only.to_excel(writer, sheet_name="team16_real_only", index=False)
        others_only.to_excel(writer, sheet_name="other_teams_only", index=False)
        proxy_scored.to_excel(writer, sheet_name="proxy_diagnostic_predictions", index=False)

    print(f"Saved: {OUTPUT_XLSX}")
    print(summary_df.to_string(index=False))


if __name__ == "__main__":
    main()
