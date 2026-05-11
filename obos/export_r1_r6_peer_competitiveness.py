#!/usr/bin/env python3
from pathlib import Path

import numpy as np
import pandas as pd

from analyze_team24_competitiveness import load_market_reports, round_sort_key
from fit_team24_semidynamic_model import attach_lagged_features


OUTPUT_XLSX = Path("/mnt/c/Users/david/documents/ASDAN/表格/WYEF_results/r1_r6_peer_competitiveness.xlsx")
OUTPUT_MD = Path("/mnt/c/Users/david/documents/ASDAN/表格/WYEF_results/r1_r6_peer_competitiveness.md")
TEAM_ID = "24"
ROUND_REMAP = {"r7": "r6"}

# Best-fit semi-dynamic interaction logit parameters
W_M = 0.166767
W_Q = 1.130079
W_MI = -0.040338
W_P = 1.045803
W_MQ = 0.025702
W_MMI = 0.002783
W_MIQ = 0.001210
W_BRAND = 0.157779
RHO_M = 0.0
RHO_Q = 0.0


def remap_round(round_name):
    return ROUND_REMAP.get(round_name, round_name)


def prepare_data():
    df = attach_lagged_features(load_market_reports()).copy()
    df["round_original"] = df["round"]
    df["round"] = df["round"].map(remap_round)
    df = df[df["round"].isin(["r1", "r2", "r3", "r4", "r5", "r6"])].copy()
    return df


def model_scores(group):
    m_cur = np.log1p(group["management_index"].fillna(0).to_numpy(dtype=float))
    q_cur = np.log1p(group["quality_index"].fillna(0).to_numpy(dtype=float))
    m_prev = np.log1p(group["prev_team_management_index"].fillna(0).to_numpy(dtype=float))
    q_prev = np.log1p(group["prev_team_quality_index"].fillna(0).to_numpy(dtype=float))
    brand = np.log1p(np.maximum(group["prev_market_share"].fillna(0).to_numpy(dtype=float), 0) * 1000.0)
    mi = np.log1p(group["market_index"].fillna(0).to_numpy(dtype=float))
    price_ratio = (
        group["avg_price"].fillna(1) / group["price"].replace(0, np.nan)
    ).fillna(1e-9)
    p = np.log(np.maximum(price_ratio.to_numpy(dtype=float), 1e-9))

    m_eff = m_cur + RHO_M * m_prev
    q_eff = q_cur + RHO_Q * q_prev

    score = (
        W_M * m_eff
        + W_Q * q_eff
        + W_MI * mi
        + W_P * p
        + W_MQ * m_eff * q_eff
        + W_MMI * m_eff * mi
        + W_MIQ * mi * q_eff
        + W_BRAND * brand
    )
    shifted = score - np.max(score)
    raw = np.exp(np.clip(shifted, -60, 60))
    share = raw / raw.sum() if raw.sum() > 0 else np.zeros_like(raw)

    result = group.copy()
    result["management_effect"] = m_eff
    result["quality_effect"] = q_eff
    result["market_effect"] = mi
    result["price_effect"] = p
    result["brand_effect"] = brand
    result["model_score"] = score
    result["predicted_competitiveness_index"] = share
    result["model_implied_share"] = share
    return result


def build_output_table(df):
    frames = []
    for (_, _), group in df.groupby(["round", "market"], sort=False):
        scored = model_scores(group)
        scored = scored.sort_values("predicted_competitiveness_index", ascending=False).reset_index(drop=True)
        scored["predicted_rank_in_city"] = np.arange(1, len(scored) + 1)
        scored["actual_market_share_rank"] = scored["market_share"].rank(method="min", ascending=False)
        frames.append(scored)

    result = pd.concat(frames, ignore_index=True)
    result["is_team24"] = result["team"] == TEAM_ID
    result = result.sort_values(
        ["round", "market", "predicted_rank_in_city", "team"],
        key=lambda s: s.map(round_sort_key) if s.name == "round" else s,
    ).reset_index(drop=True)
    return result


def write_outputs(result):
    other_teams = result[result["team"] != TEAM_ID].copy()

    cols = [
        "round",
        "market",
        "team",
        "predicted_rank_in_city",
        "predicted_competitiveness_index",
        "model_implied_share",
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
        "model_score",
        "management_effect",
        "quality_effect",
        "market_effect",
        "price_effect",
        "brand_effect",
        "round_original",
        "source_file",
        "is_team24",
    ]

    with pd.ExcelWriter(OUTPUT_XLSX, engine="openpyxl") as writer:
        result[cols].to_excel(writer, sheet_name="all_teams_r1_r6", index=False)
        other_teams[cols].to_excel(writer, sheet_name="other_teams_only", index=False)
        for round_name in ["r1", "r2", "r3", "r4", "r5", "r6"]:
            round_df = result[result["round"] == round_name][cols]
            round_df.to_excel(writer, sheet_name=round_name, index=False)

    summary_lines = [
        "# R1-R6 Peer Competitiveness Export",
        "",
        "按当前最贴合数据的半动态 Logit 模型计算。",
        "",
        "说明：",
        "- `predicted_competitiveness_index` 和 `model_implied_share` 在这个模型里数值相同，因为模型本身就是相对份额形式。",
        "- `market_share` 是市场报告中读出的实际市场份额。",
        "- 当前文件名中的 `r7_market_*` 已按你的说明重映射为实际 `r6`。",
        "",
        f"- 总行数（含 Team24）: {len(result)}",
        f"- 其它公司行数: {len(other_teams)}",
        "",
        "各轮覆盖城市：",
    ]
    for round_name in ["r1", "r2", "r3", "r4", "r5", "r6"]:
        cities = result[result["round"] == round_name]["market"].drop_duplicates().tolist()
        summary_lines.append(f"- {round_name}: {', '.join(cities)}")

    OUTPUT_MD.write_text("\n".join(summary_lines), encoding="utf-8")


def main():
    df = prepare_data()
    result = build_output_table(df)
    write_outputs(result)
    print(f"Saved: {OUTPUT_XLSX}")
    print(f"Saved: {OUTPUT_MD}")
    print(result[["round", "market", "team", "predicted_competitiveness_index", "market_share"]].head(20).to_string(index=False))


if __name__ == "__main__":
    main()
