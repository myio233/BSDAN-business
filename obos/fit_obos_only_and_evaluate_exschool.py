#!/usr/bin/env python3
import re
from itertools import product
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingRegressor

from analyze_team24_competitiveness import round_sort_key
from evaluate_obos_r1_r4_against_models import load_obos_validation_table
from fit_weighted_theoretical_cpi_model import (
    BASE_DIR,
    EPS,
    base_features,
    build_context,
    build_tree_feature_matrix,
    clean_market_table,
    metrics,
)


EXSCHOOL_DIR = Path("/mnt/c/Users/david/documents/ASDAN/表格/exschool")
OUTPUT_XLSX = BASE_DIR / "exschool_obos_model_comparison.xlsx"
TEAM_ID = "13"
REPORT_ROUND_MAP = {
    "report4_market_reports.xlsx": "r1",
    "report4_market_reports_fixed.xlsx": "r1",
    "report3_market_reports.xlsx": "r2",
    "report3_market_reports_fixed.xlsx": "r2",
    "report2_market_reports.xlsx": "r3",
    "report2_market_reports_fixed.xlsx": "r3",
    "report1_market_reports.xlsx": "r4",
    "report1_market_reports_fixed.xlsx": "r4",
}


def prepare_obos_only_table():
    df = load_obos_validation_table().copy()
    df["competition"] = "OBOS"
    df["fit_target"] = np.where(
        df["is_real_cpi_row"].fillna(False),
        df["actual_cpi"],
        df["marketshare_clean"],
    )
    df["target_source"] = np.where(df["is_real_cpi_row"].fillna(False), "team9_real_cpi", "marketshare_clean_proxy")
    df["is_labeled"] = df["fit_target"].notna()
    df["is_anchor_real"] = df["is_real_cpi_row"].fillna(False)
    return df.sort_values(["round_order", "market", "team"]).reset_index(drop=True)


def build_eval_context(df):
    real_mask = df["is_real_cpi_row"].fillna(False).to_numpy(dtype=bool)
    others_mask = (~real_mask) & df["marketshare_clean"].notna().to_numpy(dtype=bool)
    overall_mask = df["fit_target"].notna().to_numpy(dtype=bool)
    return {
        "real_mask": real_mask,
        "real_actual": df.loc[real_mask, "actual_cpi"].to_numpy(dtype=float),
        "others_mask": others_mask,
        "others_actual": df.loc[others_mask, "marketshare_clean"].to_numpy(dtype=float),
        "overall_mask": overall_mask,
        "overall_actual": df.loc[overall_mask, "fit_target"].to_numpy(dtype=float),
    }


def evaluate_prediction(pred, eval_ctx):
    pred = np.asarray(pred, dtype=float)
    return {
        "real": metrics(eval_ctx["real_actual"], pred[eval_ctx["real_mask"]]),
        "others": metrics(eval_ctx["others_actual"], pred[eval_ctx["others_mask"]]),
        "overall": metrics(eval_ctx["overall_actual"], pred[eval_ctx["overall_mask"]]),
        "max_pred": float(np.max(pred)) if len(pred) else 0.0,
    }


def score_result(result):
    pen_real = max(0.95 - result["real"]["r2"], 0.0)
    pen_others = max(0.85 - result["others"]["r2"], 0.0)
    pen_max = max(result["max_pred"] - 0.40, 0.0)
    return (
        6.0 * result["real"]["rmse"]
        + 1.5 * result["others"]["rmse"]
        + 0.5 * result["overall"]["rmse"]
        + 12.0 * pen_real ** 2
        + 4.0 * pen_others ** 2
        + 4.0 * pen_max ** 2
    )


def search_obos_model(df):
    feats = base_features(df)
    round_levels = sorted(df["round"].dropna().unique(), key=round_sort_key)
    city_levels = sorted(df["market"].dropna().unique())
    context = build_context(feats, round_levels, city_levels)
    X = build_tree_feature_matrix(feats, context)

    labeled_mask = df["is_labeled"].to_numpy(dtype=bool)
    y_log = np.log(np.maximum(df.loc[labeled_mask, "fit_target"].to_numpy(dtype=float), EPS))
    anchor_mask = df.loc[labeled_mask, "is_anchor_real"].to_numpy(dtype=bool)
    eval_ctx = build_eval_context(df)

    rows = []
    best = None

    grid = product(
        [100.0, 200.0, 500.0],
        [500],
        [0.03, 0.05],
        [4, 5],
        [1],
        [0.7, 1.0],
    )
    for idx, (tw, n_estimators, lr, depth, leaf, subsample) in enumerate(grid, start=1):
        print(f"[obos-only] candidate {idx}: tw={tw}, n_estimators={n_estimators}, lr={lr}, depth={depth}, leaf={leaf}, subsample={subsample}")
        sample_weights = np.where(anchor_mask, tw, 1.0)
        est = GradientBoostingRegressor(
            loss="squared_error",
            n_estimators=n_estimators,
            learning_rate=lr,
            max_depth=depth,
            min_samples_leaf=leaf,
            subsample=subsample,
            random_state=42,
        )
        est.fit(X.loc[labeled_mask], y_log, sample_weight=sample_weights)
        pred = np.maximum(np.exp(est.predict(X)) - EPS, 0.0)
        eval_result = evaluate_prediction(pred, eval_ctx)
        score = score_result(eval_result)
        row = {
            "model_family": "gradient_boosting_obos_only_tree",
            "params": f"anchor_w={tw}, n_estimators={n_estimators}, lr={lr}, max_depth={depth}, leaf={leaf}, subsample={subsample}",
            "score": score,
            "real_r2": eval_result["real"]["r2"],
            "real_rmse": eval_result["real"]["rmse"],
            "others_proxy_r2": eval_result["others"]["r2"],
            "others_proxy_rmse": eval_result["others"]["rmse"],
            "overall_r2": eval_result["overall"]["r2"],
            "overall_rmse": eval_result["overall"]["rmse"],
            "max_pred": eval_result["max_pred"],
        }
        rows.append(row)
        candidate = {
            "name": row["model_family"],
            "params": row["params"],
            "score": score,
            "pred": pred,
            "estimator": est,
            "columns": X.columns.tolist(),
            "round_levels": round_levels,
            "city_levels": city_levels,
            "feature_importance": getattr(est, "feature_importances_", None),
            **eval_result,
        }
        if best is None or score < best["score"]:
            best = candidate

    results_df = pd.DataFrame(rows).sort_values(
        ["real_r2", "real_rmse", "others_proxy_r2", "overall_r2", "score"],
        ascending=[False, True, False, False, True],
    ).reset_index(drop=True)
    return best, results_df


def parse_team13_actual():
    rows = []
    for path in sorted(EXSCHOOL_DIR.glob("round_*_team13.xlsx")):
        round_num = int(re.search(r"(\d+)", path.stem).group(1))
        round_name = f"r{round_num}"
        df = pd.read_excel(path, sheet_name="Sales Result")
        for _, row in df.iterrows():
            market = str(row["Market"]).strip()
            cpi = str(row["Competitive Power"]).strip()
            cpi_val = float(cpi.replace("%", "").replace(",", "")) / 100.0 if cpi not in {"", "nan"} else np.nan
            rows.append(
                {
                    "round": round_name,
                    "market": market,
                    "team": TEAM_ID,
                    "actual_real_cpi": cpi_val,
                    "actual_real_cpi_source": path.name,
                }
            )
    return pd.DataFrame(rows)


def parse_market_report_workbooks():
    rows = []
    base_paths = sorted(EXSCHOOL_DIR.glob("report*_market_reports.xlsx"))
    chosen_paths = []
    for path in base_paths:
        fixed = path.with_name(path.stem + "_fixed" + path.suffix)
        chosen_paths.append(fixed if fixed.exists() else path)

    for path in chosen_paths:
        round_name = REPORT_ROUND_MAP[path.name]
        xl = pd.ExcelFile(path)
        for market in xl.sheet_names:
            df = pd.read_excel(path, sheet_name=market, header=None)
            summary_header_idx = None
            team_header_idx = None
            for i in range(len(df)):
                first = str(df.iloc[i, 0]).strip() if pd.notna(df.iloc[i, 0]) else ""
                if first == "Population":
                    summary_header_idx = i
                if first == "Team":
                    team_header_idx = i
                    break

            if summary_header_idx is None or team_header_idx is None:
                raise ValueError(f"Cannot locate summary/team headers in {path.name} / {market}")

            summary_row = summary_header_idx + 1
            market_size = float(str(df.iloc[summary_row, 2]).replace(",", ""))
            total_sales_volume = float(str(df.iloc[summary_row, 3]).replace(",", ""))
            avg_price = float(str(df.iloc[summary_row, 4]).replace("¥", "").replace(",", ""))
            row_idx = team_header_idx + 1
            while row_idx < len(df):
                team = df.iloc[row_idx, 0]
                if pd.isna(team) or not str(team).strip().isdigit():
                    break
                team = str(team).strip()
                management = float(str(df.iloc[row_idx, 1]).replace(",", ""))
                agents = float(df.iloc[row_idx, 2])
                marketing = float(str(df.iloc[row_idx, 3]).replace("¥", "").replace(",", ""))
                quality = float(str(df.iloc[row_idx, 4]).replace(",", ""))
                price = float(str(df.iloc[row_idx, 5]).replace("¥", "").replace(",", ""))
                sales_volume = float(str(df.iloc[row_idx, 6]).replace(",", ""))
                market_share = float(str(df.iloc[row_idx, 7]).replace("%", "").replace(",", "")) / 100.0
                rows.append(
                    {
                        "round": round_name,
                        "market": market,
                        "team": team,
                        "management_index": management,
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
                        "source_file": path.name,
                    }
                )
                row_idx += 1
    return pd.DataFrame(rows)


def attach_lags(df):
    out = df.copy()
    out["round_order"] = out["round"].map(round_sort_key)
    out = out.sort_values(["team", "market", "round_order"]).reset_index(drop=True)

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
        team_state[["team", "round", "prev_team_management_index", "prev_team_quality_index"]],
        on=["team", "round"],
        how="left",
    )
    return out


def build_exschool_scored(estimator, columns, round_levels, city_levels):
    actual_df = parse_team13_actual()
    market_df = parse_market_report_workbooks()
    merged = market_df.merge(actual_df, on=["round", "market", "team"], how="left")

    # Add missing actual rows where current OCR market reports are absent.
    available_keys = set(zip(merged["round"], merged["market"], merged["team"]))
    missing_rows = []
    for _, row in actual_df.iterrows():
        key = (row["round"], row["market"], row["team"])
        if key not in available_keys:
            missing_rows.append(
                {
                    "round": row["round"],
                    "market": row["market"],
                    "team": row["team"],
                    "actual_real_cpi": row["actual_real_cpi"],
                    "actual_real_cpi_source": row["actual_real_cpi_source"],
                    "missing_market_report": True,
                }
            )

    merged["missing_market_report"] = False
    merged["competition"] = "exschool"
    merged = attach_lags(merged)
    merged = clean_market_table(merged)

    feats = base_features(merged)
    context = build_context(feats, round_levels, city_levels)
    X = build_tree_feature_matrix(feats, context).reindex(columns=columns, fill_value=0.0)
    pred = np.maximum(np.exp(estimator.predict(X)) - EPS, 0.0)

    scored = merged.copy()
    scored["predicted_theoretical_cpi"] = pred
    scored["prediction_minus_actual_real_cpi"] = scored["predicted_theoretical_cpi"] - scored["actual_real_cpi"]
    scored["prediction_minus_marketshare_clean"] = scored["predicted_theoretical_cpi"] - scored["marketshare_clean"]
    scored["theoretical_cpi_rank"] = scored.groupby(["round", "market"])["predicted_theoretical_cpi"].rank(method="min", ascending=False)
    scored["marketshare_clean_rank"] = scored.groupby(["round", "market"])["marketshare_clean"].rank(method="min", ascending=False)
    scored["rank_gap_vs_marketshare_clean"] = scored["theoretical_cpi_rank"] - scored["marketshare_clean_rank"]

    missing_df = pd.DataFrame(missing_rows)
    if not missing_df.empty:
        for col in scored.columns:
            if col not in missing_df.columns:
                missing_df[col] = np.nan
        missing_df = missing_df[scored.columns.tolist()]
        scored = pd.concat([scored, missing_df], ignore_index=True, sort=False)

    scored["round_order"] = scored["round"].map(round_sort_key)
    scored = scored.sort_values(["round_order", "market", "team"]).reset_index(drop=True)
    return scored


def main():
    obos_df = prepare_obos_only_table()
    model, results_df = search_obos_model(obos_df)

    scored = build_exschool_scored(
        model["estimator"],
        model["columns"],
        model["round_levels"],
        model["city_levels"],
    )

    team13_avail = scored[(scored["team"] == TEAM_ID) & (~scored["missing_market_report"].fillna(False))].copy()
    team13_missing = scored[(scored["team"] == TEAM_ID) & (scored["missing_market_report"].fillna(False))].copy()
    team13_metrics = metrics(team13_avail["actual_real_cpi"], team13_avail["predicted_theoretical_cpi"])

    summary_df = pd.DataFrame(
        [
            {
                "selected_model_family": model["name"],
                "selected_params": model["params"],
                "available_team13_points": len(team13_avail),
                "missing_team13_points": len(team13_missing),
                "team13_r2_on_available": team13_metrics["r2"],
                "team13_rmse_on_available": team13_metrics["rmse"],
                "team13_mae_on_available": team13_metrics["mae"],
                "obos_real_r2": model["real"]["r2"],
                "obos_real_rmse": model["real"]["rmse"],
                "obos_proxy_r2": model["others"]["r2"],
                "note": "exschool currently has 3 team13 city-rounds without parsed market reports; those rows are kept but not predicted",
            }
        ]
    )

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
        "quality_index",
        "market_index",
        "price",
        "agents",
        "marketing_investment",
        "sales_volume",
        "market_size",
        "avg_price",
        "missing_market_report",
        "source_file",
    ]
    remaining = [c for c in scored.columns if c not in preferred]
    scored = scored[[c for c in preferred if c in scored.columns] + remaining]

    feature_importance_df = pd.DataFrame(
        {"feature": model["columns"], "importance": model["feature_importance"]}
    ).sort_values("importance", ascending=False).reset_index(drop=True)

    with pd.ExcelWriter(OUTPUT_XLSX, engine="openpyxl") as writer:
        summary_df.to_excel(writer, sheet_name="summary", index=False)
        results_df.to_excel(writer, sheet_name="obos_model_search", index=False)
        feature_importance_df.to_excel(writer, sheet_name="feature_importance", index=False)
        scored.to_excel(writer, sheet_name="all_predictions", index=False)
        scored[(scored["team"] == TEAM_ID) & (~scored["missing_market_report"].fillna(False))].to_excel(writer, sheet_name="team13_available", index=False)
        scored[(scored["team"] == TEAM_ID) & (scored["missing_market_report"].fillna(False))].to_excel(writer, sheet_name="team13_missing", index=False)

    print(f"Saved: {OUTPUT_XLSX}")
    print(summary_df.to_string(index=False))


if __name__ == "__main__":
    main()
