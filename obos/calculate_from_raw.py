#!/usr/bin/env python3
import pandas as pd
import numpy as np

def parse_market_report(df, round_name):
    """从Excel表格中解析市场报告数据"""
    markets = ["Chengdu", "Hangzhou", "Shanghai"]
    market_data = {}

    # 平均价格数据
    avg_prices = {
        "r1": {"Chengdu": 10590, "Hangzhou": 10529, "Shanghai": 10770},
        "r2": {"Chengdu": 12527, "Hangzhou": 14517, "Shanghai": 13008},
        "r3": {"Chengdu": 15993, "Hangzhou": 18351, "Shanghai": 17418},
        "r4": {"Chengdu": 18083, "Hangzhou": 19811, "Shanghai": 17908},
    }

    # 查找每个市场的起始位置
    market_rows = {}
    for idx, row in df.iterrows():
        for col in df.columns:
            val = str(row[col]) if pd.notna(row[col]) else ""
            for market in markets:
                if f"Market Report - {market}" in val:
                    market_rows[market] = idx

    # 解析每个市场的数据
    for market, start_row in market_rows.items():
        # 找到表头行（包含"Team"的行）
        header_row = None
        for r in range(start_row, min(start_row + 20, len(df))):
            row_vals = [str(df.iloc[r, c]) if pd.notna(df.iloc[r, c]) else "" for c in range(df.shape[1])]
            if "Team" in "".join(row_vals):
                header_row = r
                break

        if header_row is None:
            continue

        # 读取队伍数据，直到遇到空行
        teams = []
        for r in range(header_row + 1, len(df)):
            team_num = df.iloc[r, 0]
            if pd.isna(team_num) or str(team_num).strip() == "":
                break

            team_data = {
                "round": round_name,
                "market": market,
                "team": str(team_num),
                "management_index": pd.to_numeric(df.iloc[r, 1], errors="coerce"),
                "agents": pd.to_numeric(df.iloc[r, 2], errors="coerce"),
                "marketing_investment": pd.to_numeric(df.iloc[r, 3], errors="coerce"),
                "quality_index": pd.to_numeric(df.iloc[r, 4], errors="coerce"),
                "price": pd.to_numeric(df.iloc[r, 5], errors="coerce"),
            }

            # 从Excel中读取竞争力列（第8列，索引7）
            if df.shape[1] > 8:
                comp_val = df.iloc[r, 8]
                if pd.notna(comp_val):
                    team_data["actual_competitiveness"] = pd.to_numeric(comp_val, errors="coerce")

            teams.append(team_data)

        market_data[market] = {
            "teams": teams,
            "avg_price": avg_prices.get(round_name, {}).get(market)
        }

    return market_data

# 读取所有轮次的数据
all_data = []
round_files = {
    "r1": "r1_summary.xlsx",
    "r2": "r2_summary.xlsx",
    "r3": "r3_summary.xlsx",
    "r4": "r4_summary.xlsx"
}

for round_name, filename in round_files.items():
    xl = pd.ExcelFile(filename)
    df = pd.read_excel(filename, sheet_name=xl.sheet_names[0])
    market_data = parse_market_report(df, round_name)

    for market, data in market_data.items():
        for team in data["teams"]:
            team["avg_price"] = data["avg_price"]
            all_data.append(team)

df_all = pd.DataFrame(all_data)

# 手动补充r1和r2的实际竞争力（从之前的验证数据）
manual_competitiveness = {
    ("r1", "Chengdu"): 0.0080,
    ("r1", "Hangzhou"): 0.0132,
    ("r1", "Shanghai"): 0.0088,
    ("r2", "Chengdu"): 0.0009,
    ("r2", "Hangzhou"): 0.0008,
    ("r2", "Shanghai"): 0.1588,
    ("r4", "Chengdu"): 0.2907,
}

for idx, row in df_all.iterrows():
    key = (row["round"], row["market"])
    if key in manual_competitiveness and row["team"] == "9":
        if pd.isna(df_all.at[idx, "actual_competitiveness"]):
            df_all.at[idx, "actual_competitiveness"] = manual_competitiveness[key]

print("="*100)
print("用所有队伍的数据计算竞争力")
print("="*100)
print("原始分数 = √(管理指数 + 1) × √(质量指数 + 1) × (平均价格/你的价格) × √(市场指数)")
print("市场指数 = (1 + 0.1 × Agent数量) × 营销投入")
print("竞争力 = 你的原始分数 ÷ 该市场所有队伍原始分数之和")
print("="*100)

results = []

for (round_name, market), group in df_all.groupby(["round", "market"]):
    # 计算该市场每个队伍的市场指数
    group = group.copy()
    group["market_index"] = (1 + 0.1 * group["agents"]) * group["marketing_investment"]

    # 计算原始分数
    avg_price = group["avg_price"].iloc[0]
    group["m_factor"] = np.sqrt(group["management_index"] + 1)
    group["q_factor"] = np.sqrt(group["quality_index"] + 1)
    group["p_factor"] = avg_price / group["price"]
    group["mi_factor"] = np.sqrt(group["market_index"])
    group["raw_score"] = group["m_factor"] * group["q_factor"] * group["p_factor"] * group["mi_factor"]

    # 处理可能的NaN值（填充为0）
    group["raw_score"] = group["raw_score"].fillna(0)

    # 计算总原始分数
    total_raw = group["raw_score"].sum()

    # 计算理论竞争力
    group["theoretical_competitiveness"] = group["raw_score"] / total_raw

    # 获取Team 9的数据
    team9 = group[group["team"] == "9"]
    if len(team9) > 0:
        team9 = team9.iloc[0]
        actual_comp = team9.get("actual_competitiveness", np.nan)

        if pd.notna(actual_comp):
            results.append({
                "round": round_name,
                "market": market,
                "management_index": team9["management_index"],
                "quality_index": team9["quality_index"],
                "agents": team9["agents"],
                "marketing_investment": team9["marketing_investment"],
                "price": team9["price"],
                "avg_price": avg_price,
                "market_index": team9["market_index"],
                "raw_score": team9["raw_score"],
                "total_raw_score": total_raw,
                "num_teams": len(group),
                "theoretical_competitiveness": team9["theoretical_competitiveness"],
                "actual_competitiveness": actual_comp
            })

# 按正确的顺序排序
round_order = {"r1": 1, "r2": 2, "r3": 3, "r4": 4}
market_order = {"Chengdu": 1, "Hangzhou": 2, "Shanghai": 3}
result_df = pd.DataFrame(results)
result_df["round_order"] = result_df["round"].map(round_order)
result_df["market_order"] = result_df["market"].map(market_order)
result_df = result_df.sort_values(["round_order", "market_order"]).drop(["round_order", "market_order"], axis=1).reset_index(drop=True)

# 计算误差
result_df["误差"] = result_df["theoretical_competitiveness"] - result_df["actual_competitiveness"]
result_df["相对误差"] = abs(result_df["误差"]) / result_df["actual_competitiveness"] * 100

print("\n" + "="*100)
print("用所有队伍数据计算的结果：理论值 vs 实际值对比")
print("="*100)

pd.options.display.float_format = '{:.6f}'.format
display_cols = [
    "round", "market", "num_teams", "management_index", "quality_index",
    "agents", "marketing_investment", "price",
    "actual_competitiveness", "theoretical_competitiveness", "误差", "相对误差"
]
print(result_df[display_cols].to_string(index=False))

print("\n" + "="*100)
print("每个市场所有队伍的原始分数（前3轮）")
print("="*100)

for (round_name, market), group in df_all.groupby(["round", "market"]):
    if round_name not in ["r1", "r2", "r3"]:
        continue

    group = group.copy()
    avg_price = group["avg_price"].iloc[0]
    group["market_index"] = (1 + 0.1 * group["agents"]) * group["marketing_investment"]
    group["m_factor"] = np.sqrt(group["management_index"] + 1)
    group["q_factor"] = np.sqrt(group["quality_index"] + 1)
    group["p_factor"] = avg_price / group["price"]
    group["mi_factor"] = np.sqrt(group["market_index"])
    group["raw_score"] = group["m_factor"] * group["q_factor"] * group["p_factor"] * group["mi_factor"]
    group["raw_score"] = group["raw_score"].fillna(0)
    total_raw = group["raw_score"].sum()
    group["theoretical_competitiveness"] = group["raw_score"] / total_raw

    print(f"\n--- {round_name} {market} (平均价格: {avg_price}) ---")
    print(group[["team", "management_index", "quality_index", "agents",
                 "marketing_investment", "price", "raw_score", "theoretical_competitiveness"]].to_string(index=False))
    print(f"总原始分数: {total_raw:.0f}")

print("\n" + "="*100)
print("误差统计")
print("="*100)

mae = np.mean(abs(result_df["误差"]))
max_abs_error = max(abs(result_df["误差"]))
mean_relative_error = np.mean(result_df["相对误差"])
r_squared = 1 - (np.sum(result_df["误差"]**2) /
                 np.sum((result_df["actual_competitiveness"] - result_df["actual_competitiveness"].mean())**2))

print(f"平均绝对误差 (MAE): {mae:.6f}")
print(f"最大绝对误差: {max_abs_error:.6f}")
print(f"平均相对误差: {mean_relative_error:.2f}%")
print(f"R² (决定系数): {r_squared:.6f}")

print("\n" + "="*100)
print("Team 9 详细数据对比")
print("="*100)

for idx, row in result_df.iterrows():
    print(f"\n{idx+1}. {row['round']} {row['market']}:")
    print(f"   管理指数={row['management_index']}, 质量指数={row['quality_index']}")
    print(f"   Agents={row['agents']}, 营销投入={row['marketing_investment']}, 价格={row['price']}, 平均价格={row['avg_price']}")
    print(f"   市场指数={row['market_index']:.0f}, 原始分数={row['raw_score']:.0f}, 市场总原始分数={row['total_raw_score']:.0f}, 队伍数量={row['num_teams']}")
    print(f"   实际竞争力: {row['actual_competitiveness']:.6f}")
    print(f"   理论竞争力: {row['theoretical_competitiveness']:.6f}")
    print(f"   误差: {row['误差']:.6f} ({row['相对误差']:.2f}%)")
