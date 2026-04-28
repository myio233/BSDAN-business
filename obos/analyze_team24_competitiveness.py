#!/usr/bin/env python3
import math
import re
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import minimize


BASE_DIR = Path("/mnt/c/Users/david/documents/ASDAN/表格/WYEF_results")
TEAM_ID = "24"
OUTPUT_XLSX = BASE_DIR / "team24_competitiveness_analysis.xlsx"
OUTPUT_MD = BASE_DIR / "team24_competitiveness_report.md"


def round_sort_key(round_name):
    if round_name == "r-1":
        return -1
    match = re.match(r"r(-?\d+)", round_name)
    return int(match.group(1)) if match else 999


def parse_numeric(value, percent=False):
    if pd.isna(value):
        return np.nan

    text = str(value).strip()
    if not text:
        return np.nan

    replacements = {
        ":unselected:": "",
        "¥": "",
        ",": "",
        "%": "",
        "YO": "0",
        "@": "0",
        "®": "0",
        " ": "",
    }
    for src, dst in replacements.items():
        text = text.replace(src, dst)

    filtered = "".join(ch for ch in text if ch.isdigit() or ch in ".-")
    if filtered in {"", "-", ".", "-."}:
        return np.nan

    try:
        number = float(filtered)
    except ValueError:
        return np.nan

    if percent:
        return number / 100.0
    return number


def canonical_market_name(value):
    return str(value).strip().replace("_", " ")


def load_market_reports():
    rows = []

    for path in sorted(BASE_DIR.glob("*_market_*_azure.xlsx")):
        match = re.match(r"^(r-?\d+)_market_(.+?)_azure\.xlsx$", path.name)
        if not match:
            continue

        round_name = match.group(1)
        market_name = canonical_market_name(match.group(2))
        df = pd.read_excel(path, header=None)

        population = parse_numeric(df.iloc[3, 0])
        penetration = parse_numeric(df.iloc[3, 1], percent=True)
        market_size = parse_numeric(df.iloc[3, 2])
        total_sales_volume = parse_numeric(df.iloc[3, 3])
        avg_price = parse_numeric(df.iloc[3, 4])

        for row_idx in range(6, len(df)):
            team_value = df.iloc[row_idx, 0]
            if pd.isna(team_value):
                continue

            team = str(team_value).strip()
            if not re.fullmatch(r"\d+", team):
                continue

            agents = parse_numeric(df.iloc[row_idx, 2])
            marketing_investment = parse_numeric(df.iloc[row_idx, 3])

            rows.append(
                {
                    "round": round_name,
                    "market": market_name,
                    "team": team,
                    "management_index": parse_numeric(df.iloc[row_idx, 1]),
                    "agents": agents,
                    "marketing_investment": marketing_investment,
                    "quality_index": parse_numeric(df.iloc[row_idx, 4]),
                    "price": parse_numeric(df.iloc[row_idx, 5]),
                    "sales_volume": parse_numeric(df.iloc[row_idx, 6]),
                    "market_share": parse_numeric(df.iloc[row_idx, 7], percent=True),
                    "population": population,
                    "penetration": penetration,
                    "market_size": market_size,
                    "total_sales_volume": total_sales_volume,
                    "avg_price": avg_price,
                    "source_file": path.name,
                }
            )

    all_teams = pd.DataFrame(rows)
    all_teams["market_index"] = (
        (1 + 0.1 * all_teams["agents"].fillna(0)) * all_teams["marketing_investment"].fillna(0)
    )
    all_teams["is_team24"] = all_teams["team"] == TEAM_ID
    return all_teams


def load_summary_samples():
    rows = []

    for path in sorted(BASE_DIR.glob("*_compeptiveindex_summary.xlsx")):
        match = re.match(r"^(r-?\d+)_compeptiveindex_summary\.xlsx$", path.name)
        if not match:
            continue

        round_name = match.group(1)
        agents_df = pd.read_excel(path, sheet_name="Agents")
        sales_df = pd.read_excel(path, sheet_name="Market Sales")
        merged = agents_df.merge(sales_df, on="Market", how="outer")

        for _, row in merged.iterrows():
            rows.append(
                {
                    "round": round_name,
                    "market": canonical_market_name(row["Market"]),
                    "actual_competitiveness": float(row["Competitive Power"]),
                    "team24_agents_summary": float(row["After"]) if pd.notna(row["After"]) else 0.0,
                    "team24_marketing_summary": float(row["Marketing Investment"]) if pd.notna(row["Marketing Investment"]) else 0.0,
                    "team24_price_summary": float(row["Price"]) if pd.notna(row["Price"]) else 0.0,
                    "team24_sales_volume_summary": float(row["Sales Volume"]) if pd.notna(row["Sales Volume"]) else 0.0,
                    "team24_sales_summary": float(row["Sales"]) if pd.notna(row["Sales"]) else 0.0,
                }
            )

    return pd.DataFrame(rows)


def build_sample_table(all_teams, summary_samples):
    market_counts = all_teams.groupby(["round", "market"]).size().reset_index(name="num_teams")
    team24_rows = all_teams[all_teams["team"] == TEAM_ID].copy()
    team24_rows = team24_rows.rename(
        columns={
            "management_index": "team24_management_index",
            "agents": "team24_agents_report",
            "marketing_investment": "team24_marketing_report",
            "quality_index": "team24_quality_index",
            "price": "team24_price_report",
            "sales_volume": "team24_sales_volume_report",
            "market_share": "team24_market_share_report",
            "market_index": "team24_market_index_report",
            "source_file": "team24_source_file",
        }
    )

    sample_df = summary_samples.merge(market_counts, on=["round", "market"], how="inner")
    sample_df = sample_df.merge(
        team24_rows[
            [
                "round",
                "market",
                "team",
                "team24_management_index",
                "team24_agents_report",
                "team24_marketing_report",
                "team24_quality_index",
                "team24_price_report",
                "team24_sales_volume_report",
                "team24_market_share_report",
                "team24_market_index_report",
                "team24_source_file",
                "avg_price",
                "population",
                "penetration",
                "market_size",
                "total_sales_volume",
            ]
        ],
        on=["round", "market"],
        how="left",
    )

    sample_df["team24_present_in_report"] = sample_df["team"].notna()
    sample_df["team24_team"] = sample_df["team"].fillna("")
    sample_df = sample_df.drop(columns=["team"])

    sample_df["team24_market_index_summary"] = (
        (1 + 0.1 * sample_df["team24_agents_summary"].fillna(0))
        * sample_df["team24_marketing_summary"].fillna(0)
    )

    return sample_df


def predict_team24_competitiveness(all_teams, sample_df, params):
    m_exp, q_exp, mi_exp, price_exp = params
    predictions = []

    for _, sample in sample_df.iterrows():
        market_rows = all_teams[
            (all_teams["round"] == sample["round"]) & (all_teams["market"] == sample["market"])
        ].copy()

        if market_rows.empty or not sample["team24_present_in_report"]:
            predictions.append(0.0)
            continue

        price_factor = (
            market_rows["avg_price"].fillna(1) / market_rows["price"].replace(0, np.nan)
        ).fillna(0)

        market_rows["raw_score"] = (
            (1 + market_rows["management_index"].fillna(0)) ** m_exp
            * (1 + market_rows["quality_index"].fillna(0)) ** q_exp
            * (1 + market_rows["market_index"].fillna(0)) ** mi_exp
            * (price_factor**price_exp)
        )

        total_raw = market_rows["raw_score"].sum()
        if total_raw <= 0:
            predictions.append(0.0)
            continue

        team24_raw = market_rows.loc[market_rows["team"] == TEAM_ID, "raw_score"].iloc[0]
        predictions.append(float(team24_raw / total_raw))

    return np.array(predictions)


def fit_model(all_teams, sample_df):
    y = sample_df["actual_competitiveness"].to_numpy(dtype=float)

    baseline_params = np.array([0.5, 0.5, 0.5, 1.0], dtype=float)
    baseline_pred = predict_team24_competitiveness(all_teams, sample_df, baseline_params)

    def objective(params):
        pred = predict_team24_competitiveness(all_teams, sample_df, params)
        return np.mean((pred - y) ** 2)

    result = minimize(
        objective,
        x0=np.array([0.3, 1.0, 0.05, 1.0], dtype=float),
        bounds=[(0, 5), (0, 5), (0, 5), (0, 5)],
        method="L-BFGS-B",
    )

    fitted_params = result.x
    fitted_pred = predict_team24_competitiveness(all_teams, sample_df, fitted_params)
    return baseline_params, baseline_pred, result, fitted_params, fitted_pred


def compute_metrics(actual, pred):
    actual = np.asarray(actual, dtype=float)
    pred = np.asarray(pred, dtype=float)
    errors = pred - actual
    mae = np.mean(np.abs(errors))
    rmse = math.sqrt(np.mean(errors**2))
    ss_tot = np.sum((actual - actual.mean()) ** 2)
    r2 = 1 - np.sum(errors**2) / ss_tot if ss_tot > 0 else np.nan
    return {"mae": mae, "rmse": rmse, "r2": r2}


def build_predictions_table(sample_df, baseline_pred, fitted_pred):
    result = sample_df.copy()
    result["baseline_prediction"] = baseline_pred
    result["baseline_error"] = result["baseline_prediction"] - result["actual_competitiveness"]
    result["fitted_prediction"] = fitted_pred
    result["fitted_error"] = result["fitted_prediction"] - result["actual_competitiveness"]
    result["abs_fitted_error"] = result["fitted_error"].abs()
    return result


def write_outputs(all_teams, sample_df, fit_result_df, metrics_df):
    with pd.ExcelWriter(OUTPUT_XLSX, engine="openpyxl") as writer:
        all_teams.sort_values(["round", "market", "team"], key=lambda s: s.map(round_sort_key) if s.name == "round" else s).to_excel(
            writer, sheet_name="all_team_decisions", index=False
        )
        sample_df.to_excel(writer, sheet_name="team24_samples", index=False)
        fit_result_df.to_excel(writer, sheet_name="team24_predictions", index=False)
        metrics_df.to_excel(writer, sheet_name="fit_metrics", index=False)


def write_report(all_teams, sample_df, fit_result_df, metrics_df, fitted_params):
    covered_rounds = ", ".join(sorted(sample_df["round"].unique(), key=round_sort_key))
    missing_rounds = []
    for round_name in sorted(
        {re.match(r"^(r-?\d+)_compeptiveindex_summary\.xlsx$", p.name).group(1) for p in BASE_DIR.glob("*_compeptiveindex_summary.xlsx")},
        key=round_sort_key,
    ):
        if round_name not in set(sample_df["round"].unique()):
            missing_rounds.append(round_name)

    top_errors = fit_result_df.sort_values("abs_fitted_error", ascending=False).head(8)

    lines = [
        "# Team24 Competitiveness Report",
        "",
        f"- Team ID: `{TEAM_ID}`",
        f"- Covered rounds with usable market reports: {covered_rounds}",
        f"- Missing rounds with summary but no market-report Excel in `WYEF_results`: {', '.join(missing_rounds) if missing_rounds else 'None'}",
        f"- Total market reports parsed: {sample_df.shape[0]}",
        f"- Total company-decision rows parsed: {all_teams.shape[0]}",
        f"- Markets where Team24 appears in report: {int(sample_df['team24_present_in_report'].sum())}",
        "",
        "## Fitted Formula",
        "",
        "For market `(r, c)` and company `i`, define:",
        "",
        "```text",
        "MarketIndex_i = MarketingInvestment_i * (1 + 0.1 * Agents_i)",
        "RawScore_i = (1 + ManagementIndex_i)^a",
        "            * (1 + QualityIndex_i)^b",
        "            * (1 + MarketIndex_i)^c",
        "            * (AvgPrice_(r,c) / Price_i)^d",
        "PredictedCompetitiveness_24 = RawScore_24 / Σ RawScore_i",
        "```",
        "",
        f"Fitted coefficients: `a={fitted_params[0]:.6f}`, `b={fitted_params[1]:.6f}`, `c={fitted_params[2]:.6f}`, `d={fitted_params[3]:.6f}`",
        "",
        "Interpretation:",
        f"- Management elasticity is moderate: `{fitted_params[0]:.3f}`",
        f"- Quality elasticity is strongest: `{fitted_params[1]:.3f}`",
        f"- Market-index elasticity is numerically small: `{fitted_params[2]:.3f}`",
        "- But market index itself spans several orders of magnitude, so this term still materially changes the score.",
        f"- Price effect is close to inverse-price proportionality: `{fitted_params[3]:.3f}`",
        "",
        "## Metrics",
        "",
        metrics_df.to_string(index=False),
        "",
        "## Largest Fitted Errors",
        "",
        top_errors[
            [
                "round",
                "market",
                "actual_competitiveness",
                "fitted_prediction",
                "fitted_error",
                "team24_management_index",
                "team24_quality_index",
                "team24_agents_report",
                "team24_marketing_report",
                "team24_price_report",
            ]
        ].to_string(index=False),
        "",
        "## Notes",
        "",
        "- This report only uses Excel files already present in `WYEF_results`.",
        "- `r6` summary exists, but no city-level market-report Excel was available there, so it could not be included in the all-company fitting step.",
        "- The largest residuals likely reflect latent factors not visible in the current Excel fields, such as round carry-over, hidden simulation rules, OCR noise in competitor rows, or additional game mechanics outside management/quality/marketing/price.",
    ]

    OUTPUT_MD.write_text("\n".join(lines), encoding="utf-8")


def main():
    all_teams = load_market_reports()
    summary_samples = load_summary_samples()
    sample_df = build_sample_table(all_teams, summary_samples)
    sample_df = sample_df.sort_values(["round", "market"], key=lambda s: s.map(round_sort_key) if s.name == "round" else s).reset_index(drop=True)

    baseline_params, baseline_pred, result, fitted_params, fitted_pred = fit_model(all_teams, sample_df)

    fit_result_df = build_predictions_table(sample_df, baseline_pred, fitted_pred)

    overall_baseline = compute_metrics(fit_result_df["actual_competitiveness"], fit_result_df["baseline_prediction"])
    overall_fitted = compute_metrics(fit_result_df["actual_competitiveness"], fit_result_df["fitted_prediction"])

    positive_mask = fit_result_df["team24_present_in_report"]
    present_baseline = compute_metrics(
        fit_result_df.loc[positive_mask, "actual_competitiveness"],
        fit_result_df.loc[positive_mask, "baseline_prediction"],
    )
    present_fitted = compute_metrics(
        fit_result_df.loc[positive_mask, "actual_competitiveness"],
        fit_result_df.loc[positive_mask, "fitted_prediction"],
    )

    metrics_df = pd.DataFrame(
        [
            {
                "scope": "all_markets_with_report",
                "model": "baseline_sqrt",
                **overall_baseline,
                "management_exp": baseline_params[0],
                "quality_exp": baseline_params[1],
                "market_index_exp": baseline_params[2],
                "price_exp": baseline_params[3],
            },
            {
                "scope": "all_markets_with_report",
                "model": "fitted",
                **overall_fitted,
                "management_exp": fitted_params[0],
                "quality_exp": fitted_params[1],
                "market_index_exp": fitted_params[2],
                "price_exp": fitted_params[3],
            },
            {
                "scope": "markets_where_team24_present",
                "model": "baseline_sqrt",
                **present_baseline,
                "management_exp": baseline_params[0],
                "quality_exp": baseline_params[1],
                "market_index_exp": baseline_params[2],
                "price_exp": baseline_params[3],
            },
            {
                "scope": "markets_where_team24_present",
                "model": "fitted",
                **present_fitted,
                "management_exp": fitted_params[0],
                "quality_exp": fitted_params[1],
                "market_index_exp": fitted_params[2],
                "price_exp": fitted_params[3],
            },
        ]
    )

    write_outputs(all_teams, sample_df, fit_result_df, metrics_df)
    write_report(all_teams, sample_df, fit_result_df, metrics_df, fitted_params)

    print(f"Saved: {OUTPUT_XLSX}")
    print(f"Saved: {OUTPUT_MD}")
    print(metrics_df.to_string(index=False))


if __name__ == "__main__":
    main()
