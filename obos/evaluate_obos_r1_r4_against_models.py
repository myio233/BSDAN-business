#!/usr/bin/env python3
import re
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingRegressor

from analyze_team24_competitiveness import parse_numeric, round_sort_key
from fit_team24_semidynamic_model import attach_lagged_features
from fit_weighted_theoretical_cpi_model import (
    EPS,
    BASE_DIR,
    build_context,
    build_tree_feature_matrix,
    base_features,
    clean_market_table,
    metrics,
    prepare_model_table,
)


OBOS_DIR = Path("/mnt/c/Users/david/documents/ASDAN/表格/obos")
TOP_MODELS_XLSX = BASE_DIR / "global_constrained_cpi_model.xlsx"
OUTPUT_XLSX = BASE_DIR / "obos_r1_r4_model_validation.xlsx"
TEAM_WITH_REAL_CPI = "9"
MANUAL_CPI = {
    ("r1", "Chengdu", "9"): 0.0080,
    ("r1", "Hangzhou", "9"): 0.0132,
    ("r1", "Shanghai", "9"): 0.0088,
}


def parse_obos_summary(path):
    df = pd.read_excel(path, sheet_name="Market Report", header=None)
    round_name = path.stem.split("_")[0]
    rows = []
    idx = 0

    while idx < len(df):
        header = df.iloc[idx, 0]
        if isinstance(header, str) and header.startswith("Market Report - "):
            market = header.replace("Market Report - ", "").strip()
            market_size = parse_numeric(df.iloc[idx + 3, 2])
            total_sales_volume = parse_numeric(df.iloc[idx + 3, 3])
            avg_price = parse_numeric(df.iloc[idx + 3, 4])

            team_header_idx = idx + 5
            cols = [str(x).strip() if pd.notna(x) else "" for x in df.iloc[team_header_idx].tolist()]
            has_market_share = "Market Share" in cols
            has_cpi = "竞争力" in cols
            market_share_idx = cols.index("Market Share") if has_market_share else None
            cpi_idx = cols.index("竞争力") if has_cpi else None

            row_idx = team_header_idx + 1
            while row_idx < len(df):
                team_val = df.iloc[row_idx, 0]
                if pd.isna(team_val) or not re.fullmatch(r"\d+", str(team_val).strip()):
                    break
                team = str(team_val).strip()
                actual_cpi = parse_numeric(df.iloc[row_idx, cpi_idx]) if cpi_idx is not None else np.nan
                if pd.isna(actual_cpi):
                    actual_cpi = MANUAL_CPI.get((round_name, market, team), np.nan)
                market_share = parse_numeric(df.iloc[row_idx, market_share_idx], percent=True) if market_share_idx is not None else np.nan
                agents = parse_numeric(df.iloc[row_idx, 2])
                marketing = parse_numeric(df.iloc[row_idx, 3])
                rows.append(
                    {
                        "round": round_name,
                        "market": market,
                        "team": team,
                        "management_index": parse_numeric(df.iloc[row_idx, 1]),
                        "agents": agents,
                        "marketing_investment": marketing,
                        "quality_index": parse_numeric(df.iloc[row_idx, 4]),
                        "price": parse_numeric(df.iloc[row_idx, 5]),
                        "sales_volume": parse_numeric(df.iloc[row_idx, 6]),
                        "market_share": market_share,
                        "market_size": market_size,
                        "total_sales_volume": total_sales_volume,
                        "avg_price": avg_price,
                        "market_index": (1 + 0.1 * (agents or 0)) * (marketing or 0),
                        "actual_cpi": actual_cpi,
                        "source_file": path.name,
                    }
                )
                row_idx += 1
            idx = row_idx
        else:
            idx += 1
    return rows


def load_obos_validation_table():
    rows = []
    for round_name in ["r1", "r2", "r3", "r4"]:
        rows.extend(parse_obos_summary(OBOS_DIR / f"{round_name}_summary.xlsx"))
    df = pd.DataFrame(rows)
    df = attach_lagged_features(df)
    df = clean_market_table(df)
    df["actual_cpi"] = df["actual_cpi"].astype(float)
    df["is_real_cpi_row"] = df["actual_cpi"].notna()
    df["round_order"] = df["round"].map(round_sort_key)
    return df.sort_values(["round_order", "market", "team"]).reset_index(drop=True)


def train_tree_estimator(train_df, params):
    feats = base_features(train_df)
    round_levels = sorted(train_df["round"].dropna().unique(), key=round_sort_key)
    city_levels = sorted(train_df["market"].dropna().unique())
    context = build_context(feats, round_levels, city_levels)
    X = build_tree_feature_matrix(feats, context)

    labeled_mask = train_df["is_labeled"].to_numpy(dtype=bool)
    y_log = np.log(np.maximum(train_df.loc[labeled_mask, "fit_target"].to_numpy(dtype=float), EPS))
    team24_mask = train_df.loc[labeled_mask, "is_team24"].to_numpy(dtype=bool)

    tw = float(params["tw"])
    sample_weights = np.where(team24_mask, tw, 1.0)
    estimator = GradientBoostingRegressor(
        loss="squared_error",
        n_estimators=int(params["n_estimators"]),
        learning_rate=float(params["lr"]),
        max_depth=int(params["max_depth"]),
        min_samples_leaf=int(params["leaf"]),
        subsample=float(params["subsample"]),
        random_state=42,
    )
    estimator.fit(X.loc[labeled_mask], y_log, sample_weight=sample_weights)
    return {
        "estimator": estimator,
        "columns": X.columns.tolist(),
        "round_levels": round_levels,
        "city_levels": city_levels,
    }


def predict_tree(model, df):
    feats = base_features(df)
    context = build_context(feats, model["round_levels"], model["city_levels"])
    X = build_tree_feature_matrix(feats, context).reindex(columns=model["columns"], fill_value=0.0)
    pred_log = model["estimator"].predict(X)
    return np.maximum(np.exp(pred_log) - EPS, 0.0)


GBT_PATTERN = re.compile(
    r"tw=(?P<tw>[\d.]+), n_estimators=(?P<n_estimators>\d+), lr=(?P<lr>[\d.]+), max_depth=(?P<max_depth>\d+), leaf=(?P<leaf>\d+), subsample=(?P<subsample>[\d.]+)"
)


def parse_gbt_params(text):
    match = GBT_PATTERN.search(text)
    if not match:
        raise ValueError(f"Cannot parse GBT params: {text}")
    return match.groupdict()


def rebuild_candidate(train_df, row):
    family = row["model_family"]
    params = str(row["params"])
    if family == "gradient_boosting_threshold_tree":
        parsed = parse_gbt_params(params)
        fitted = train_tree_estimator(train_df, parsed)
        return {"kind": "gbt", "label": params, "model": fitted}

    if family == "blended_threshold_ensemble":
        parts = re.findall(r"([\d.]+)\*\[gradient_boosting_threshold_tree::(.*?)\](?=\s*(?:\+|$))", params)
        members = []
        for weight_text, inner in parts:
            parsed = parse_gbt_params(inner)
            fitted = train_tree_estimator(train_df, parsed)
            members.append((float(weight_text), fitted, inner))
        return {"kind": "ensemble", "label": params, "members": members}

    raise ValueError(f"Unsupported family: {family}")


def predict_candidate(candidate, df):
    if candidate["kind"] == "gbt":
        return predict_tree(candidate["model"], df)
    total = np.zeros(len(df), dtype=float)
    for weight, member, _ in candidate["members"]:
        total += weight * predict_tree(member, df)
    return total


def main():
    train_df = prepare_model_table()
    obos_df = load_obos_validation_table()

    top_models = pd.read_excel(TOP_MODELS_XLSX, sheet_name="top_models").head(80).copy()
    top_models = top_models.drop_duplicates(subset=["model_family", "params"]).reset_index(drop=True)

    actual_mask = obos_df["is_real_cpi_row"].to_numpy(dtype=bool)
    actual = obos_df.loc[actual_mask, "actual_cpi"].to_numpy(dtype=float)

    metric_rows = []
    best = None
    best_pred = None

    for _, row in top_models.iterrows():
        try:
            candidate = rebuild_candidate(train_df, row)
        except Exception:
            continue
        pred_all = predict_candidate(candidate, obos_df)
        pred = pred_all[actual_mask]
        m = metrics(actual, pred)
        out_row = {
            "model_family": row["model_family"],
            "params": row["params"],
            "obos_mae": m["mae"],
            "obos_rmse": m["rmse"],
            "obos_r2": m["r2"],
            "obos_corr": m["corr"],
            "wyef_overall_r2": row["overall_r2"],
            "wyef_team24_r2": row["team24_r2"],
            "wyef_others_clean_r2": row["others_clean_r2"],
        }
        metric_rows.append(out_row)

        key = (m["r2"], -m["rmse"], row["others_clean_r2"], row["team24_r2"])
        if best is None or key > best["key"]:
            best = {"row": out_row, "candidate": candidate, "key": key}
            best_pred = pred_all

    metrics_df = pd.DataFrame(metric_rows).sort_values(
        ["obos_r2", "obos_rmse", "wyef_others_clean_r2", "wyef_team24_r2"],
        ascending=[False, True, False, False],
    ).reset_index(drop=True)

    out_df = obos_df.copy()
    out_df["predicted_theoretical_cpi"] = best_pred
    out_df["obos_fit_error"] = out_df["predicted_theoretical_cpi"] - out_df["actual_cpi"]
    out_df["obos_abs_error"] = out_df["obos_fit_error"].abs()

    summary_df = pd.DataFrame([best["row"]])

    with pd.ExcelWriter(OUTPUT_XLSX, engine="openpyxl") as writer:
        summary_df.to_excel(writer, sheet_name="best_model", index=False)
        metrics_df.to_excel(writer, sheet_name="candidate_metrics", index=False)
        out_df.to_excel(writer, sheet_name="obos_predictions", index=False)
        out_df[out_df["is_real_cpi_row"]].to_excel(writer, sheet_name="real_cpi_only", index=False)

    print(f"Saved: {OUTPUT_XLSX}")
    print(summary_df.to_string(index=False))
    print(out_df[out_df['is_real_cpi_row']][['round', 'market', 'team', 'actual_cpi', 'predicted_theoretical_cpi', 'obos_fit_error']].to_string(index=False))


if __name__ == "__main__":
    main()
