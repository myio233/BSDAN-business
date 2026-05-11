#!/usr/bin/env python3
from itertools import product

import numpy as np
import pandas as pd
from sklearn.ensemble import ExtraTreesRegressor, GradientBoostingRegressor
from sklearn.tree import DecisionTreeRegressor, export_text

from analyze_team24_competitiveness import round_sort_key
from evaluate_training1_with_no_management_model import TEAM_ID, attach_lags, load_training1_table
from fit_universal_no_management_model import build_no_management_feature_matrix
from fit_weighted_theoretical_cpi_model import BASE_DIR, EPS, base_features, build_context, clean_market_table, metrics


OUTPUT_XLSX = BASE_DIR / "training1_no_management_model.xlsx"


def prepare_training1_model_table():
    df = load_training1_table().copy()
    df["competition"] = "training1"
    df["management_index"] = 0.0
    df = attach_lags(df)
    df = clean_market_table(df)
    df["actual_real_cpi_source"] = np.where(df["team"] == TEAM_ID, "Sales sheet Competitive Power", "")
    df["fit_target"] = np.where(
        df["team"] == TEAM_ID,
        df["actual_real_cpi"],
        df["marketshare_clean"],
    )
    df["target_source"] = np.where(df["team"] == TEAM_ID, "team16_real_cpi", "marketshare_clean_proxy")
    df["is_labeled"] = df["fit_target"].notna()
    df["is_team16"] = df["team"] == TEAM_ID
    return df.sort_values(["round_order", "market", "team"]).reset_index(drop=True)


def build_eval_context(df):
    team16_mask = df["is_team16"].to_numpy(dtype=bool)
    others_mask = (~team16_mask) & df["marketshare_clean"].notna().to_numpy(dtype=bool)
    overall_mask = df["fit_target"].notna().to_numpy(dtype=bool)
    return {
        "team16_mask": team16_mask,
        "team16_actual": df.loc[team16_mask, "actual_real_cpi"].to_numpy(dtype=float),
        "others_mask": others_mask,
        "others_actual": df.loc[others_mask, "marketshare_clean"].to_numpy(dtype=float),
        "overall_mask": overall_mask,
        "overall_actual": df.loc[overall_mask, "fit_target"].to_numpy(dtype=float),
    }


def evaluate_prediction(pred, eval_ctx):
    pred = np.asarray(pred, dtype=float)
    return {
        "team16": metrics(eval_ctx["team16_actual"], pred[eval_ctx["team16_mask"]]),
        "others": metrics(eval_ctx["others_actual"], pred[eval_ctx["others_mask"]]),
        "overall": metrics(eval_ctx["overall_actual"], pred[eval_ctx["overall_mask"]]),
        "max_pred": float(np.max(pred)) if len(pred) else 0.0,
    }


def score_result(result):
    pen_team16 = max(0.95 - result["team16"]["r2"], 0.0)
    pen_others = max(0.80 - result["others"]["r2"], 0.0)
    pen_max = max(result["max_pred"] - 0.40, 0.0)
    return (
        6.0 * result["team16"]["rmse"]
        + 1.0 * result["others"]["rmse"]
        + 0.5 * result["overall"]["rmse"]
        + 12.0 * pen_team16 ** 2
        + 3.0 * pen_others ** 2
        + 4.0 * pen_max ** 2
    )


def build_training_matrix(df):
    feats = base_features(df)
    round_levels = sorted(df["round"].dropna().unique(), key=round_sort_key)
    city_levels = sorted(df["market"].dropna().unique())
    context = build_context(feats, round_levels, city_levels)
    X = build_no_management_feature_matrix(feats, context)
    return feats, X, round_levels, city_levels


def fit_surrogate_formula(X, pred):
    feature_cols = X.columns.tolist()
    y_log = np.log(np.maximum(np.asarray(pred, dtype=float), EPS))
    X_fit = X.to_numpy(dtype=float)

    best = None
    for depth in [2, 3, 4]:
        for min_leaf in [1, 2, 3]:
            reg = DecisionTreeRegressor(max_depth=depth, min_samples_leaf=min_leaf, random_state=42)
            reg.fit(X_fit, y_log)
            pred_log = reg.predict(X_fit)
            pred_sur = np.maximum(np.exp(pred_log) - EPS, 0.0)
            fit_m = metrics(pred, pred_sur)
            objective = (fit_m["r2"], -reg.get_n_leaves(), -fit_m["rmse"])
            if best is None or objective > best["objective"]:
                best = {
                    "pred": pred_sur,
                    "metrics_to_model": fit_m,
                    "rules_text": export_text(reg, feature_names=feature_cols, decimals=4),
                    "feature_importance": pd.DataFrame(
                        {"feature": feature_cols, "importance": reg.feature_importances_}
                    ).sort_values("importance", ascending=False).reset_index(drop=True),
                    "objective": objective,
                }
    return best


def main():
    df = prepare_training1_model_table()
    feats, X, round_levels, city_levels = build_training_matrix(df)
    eval_ctx = build_eval_context(df)

    labeled_mask = df["is_labeled"].to_numpy(dtype=bool)
    y_log = np.log(np.maximum(df.loc[labeled_mask, "fit_target"].to_numpy(dtype=float), EPS))
    team16_mask = df.loc[labeled_mask, "is_team16"].to_numpy(dtype=bool)

    rows = []
    best = None

    gbt_grid = product(
        [100.0, 200.0, 500.0],
        [200, 500],
        [0.03, 0.05],
        [2, 3, 4],
        [1],
        [0.7, 1.0],
    )
    for idx, (tw, n_estimators, lr, depth, leaf, subsample) in enumerate(gbt_grid, start=1):
        print(f"[training1] GBT candidate {idx}: tw={tw}, n_estimators={n_estimators}, lr={lr}, depth={depth}, leaf={leaf}, subsample={subsample}")
        sample_weights = np.where(team16_mask, tw, 1.0)
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
            "model_family": "gradient_boosting_no_management_tree",
            "params": f"team16_w={tw}, n_estimators={n_estimators}, lr={lr}, max_depth={depth}, leaf={leaf}, subsample={subsample}",
            "score": score,
            "team16_r2": eval_result["team16"]["r2"],
            "team16_rmse": eval_result["team16"]["rmse"],
            "team16_mae": eval_result["team16"]["mae"],
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
            "feature_importance": getattr(est, "feature_importances_", None),
            "columns": X.columns.tolist(),
            "round_levels": round_levels,
            "city_levels": city_levels,
            **eval_result,
        }
        if best is None or score < best["score"]:
            best = candidate

    et_grid = product(
        [50.0, 100.0, 200.0],
        [200],
        [4, 6],
        [1],
        ["sqrt", 0.6],
    )
    for idx, (tw, n_estimators, depth, leaf, max_features) in enumerate(et_grid, start=1):
        print(f"[training1] ET candidate {idx}: tw={tw}, n_estimators={n_estimators}, depth={depth}, leaf={leaf}, max_features={max_features}")
        sample_weights = np.where(team16_mask, tw, 1.0)
        est = ExtraTreesRegressor(
            n_estimators=n_estimators,
            max_depth=depth,
            min_samples_leaf=leaf,
            max_features=max_features,
            random_state=42,
            n_jobs=-1,
        )
        est.fit(X.loc[labeled_mask], y_log, sample_weight=sample_weights)
        pred = np.maximum(np.exp(est.predict(X)) - EPS, 0.0)
        eval_result = evaluate_prediction(pred, eval_ctx)
        score = score_result(eval_result)
        row = {
            "model_family": "extra_trees_no_management_tree",
            "params": f"team16_w={tw}, n_estimators={n_estimators}, max_depth={depth}, leaf={leaf}, max_features={max_features}",
            "score": score,
            "team16_r2": eval_result["team16"]["r2"],
            "team16_rmse": eval_result["team16"]["rmse"],
            "team16_mae": eval_result["team16"]["mae"],
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
            "feature_importance": getattr(est, "feature_importances_", None),
            "columns": X.columns.tolist(),
            "round_levels": round_levels,
            "city_levels": city_levels,
            **eval_result,
        }
        if best is None or score < best["score"]:
            best = candidate

    results_df = pd.DataFrame(rows).sort_values(
        ["team16_r2", "team16_rmse", "others_proxy_r2", "overall_r2", "score"],
        ascending=[False, True, False, False, True],
    ).reset_index(drop=True)

    pred = np.asarray(best["pred"], dtype=float)
    scored = df.copy()
    scored["predicted_theoretical_cpi"] = pred
    scored["prediction_minus_actual_real_cpi"] = scored["predicted_theoretical_cpi"] - scored["actual_real_cpi"]
    scored["prediction_minus_marketshare_clean"] = scored["predicted_theoretical_cpi"] - scored["marketshare_clean"]
    scored["theoretical_cpi_rank"] = scored.groupby(["round", "market"])["predicted_theoretical_cpi"].rank(method="min", ascending=False)
    scored["marketshare_clean_rank"] = scored.groupby(["round", "market"])["marketshare_clean"].rank(method="min", ascending=False)
    scored["rank_gap_vs_marketshare_clean"] = scored["theoretical_cpi_rank"] - scored["marketshare_clean_rank"]

    surrogate = fit_surrogate_formula(X, pred)
    formula_df = pd.DataFrame(
        {
            "formula": ["Approximate threshold rules for log(theoretical_cpi):"]
            + surrogate["rules_text"].splitlines()
            + ["", "theoretical_cpi ~= exp(predicted_log_value_from_rules) - 1e-9"]
        }
    )

    importance_df = pd.DataFrame(
        {"feature": X.columns.tolist(), "importance": best["feature_importance"]}
    ).sort_values("importance", ascending=False).reset_index(drop=True)

    best_df = pd.DataFrame(
        [
            {
                "selected_model_family": best["name"],
                "selected_params": best["params"],
                "score": best["score"],
                "team16_r2": best["team16"]["r2"],
                "team16_rmse": best["team16"]["rmse"],
                "team16_mae": best["team16"]["mae"],
                "others_proxy_r2": best["others"]["r2"],
                "others_proxy_rmse": best["others"]["rmse"],
                "overall_r2": best["overall"]["r2"],
                "overall_rmse": best["overall"]["rmse"],
                "max_pred": best["max_pred"],
                "surrogate_to_model_r2": surrogate["metrics_to_model"]["r2"],
                "surrogate_to_model_rmse": surrogate["metrics_to_model"]["rmse"],
                "note": "training1-only no-management model; team16 real CPI is the anchor target, peers use marketshare_clean proxy",
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
        "quality_index",
        "market_index",
        "price",
        "agents",
        "marketing_investment",
        "sales_volume",
        "market_size",
        "avg_price",
        "target_source",
    ]
    remaining = [c for c in scored.columns if c not in preferred]
    scored = scored[[c for c in preferred if c in scored.columns] + remaining]

    with pd.ExcelWriter(OUTPUT_XLSX, engine="openpyxl") as writer:
        best_df.to_excel(writer, sheet_name="best_model", index=False)
        results_df.to_excel(writer, sheet_name="all_results", index=False)
        importance_df.to_excel(writer, sheet_name="feature_importance", index=False)
        formula_df.to_excel(writer, sheet_name="formula", index=False)
        scored.to_excel(writer, sheet_name="all_predictions", index=False)
        scored[scored["is_team16"]].to_excel(writer, sheet_name="team16_real_only", index=False)
        scored[~scored["is_team16"]].to_excel(writer, sheet_name="other_teams_only", index=False)

    print(f"Saved: {OUTPUT_XLSX}")
    print(best_df.to_string(index=False))


if __name__ == "__main__":
    main()
