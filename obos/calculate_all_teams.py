#!/usr/bin/env python3
import pandas as pd
import numpy as np

def parse_market_report(df, round_name):
    """从Excel表格中解析市场报告数据"""
    markets = ["Chengdu", "Hangzhou", "Shanghai"]
    market_data = {}

    for market in markets:
        # 找到市场起始位置
        start_row = None
        for idx, row in df.iterrows():
            for col in df.columns:
                val = str(row[col]) if pd.notna(row[col]) else ""
                if f"Market Report - {market}" in val:
                    start_row = idx
                    break
            if start_row is not None:
                break

        if start_row is None:
            continue

        # 找到表头行（包含"Team"的行）
        header_row = None
        for r in range(start_row, min(start_row + 20, len(df))):
            row_vals = [str(df.iloc[r, c]) if pd.notna(df.iloc[r, c]) else "" for c in range(df.shape[1])]
            if "Team" in "".join(row_vals):
                header_row = r
                break

        if header_row is None:
            continue

        # 读取队伍数据
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

            teams.append(team_data)

        market_data[market] = teams

    return market_data

# 平均价格数据
avg_prices = {
    "r1": {"Chengdu": 10590, "Hangzhou": 10529, "Shanghai": 10770},
    "r2": {"Chengdu": 12527, "Hangzhou": 14517, "Shanghai": 13008},
    "r3": {"Chengdu": 15993, "Hangzhou": 18351, "Shanghai": 17418},
    "r4": {"Chengdu": 18083, "Hangzhou": 19811, "Shanghai": 17908},
}

# 用户提供的实际竞争力数据
actual_comp_data = {
    ("r1", "Chengdu"): 0.0080,
    ("r1", "Hangzhou"): 0.0132,
    ("r1", "Shanghai"): 0.0088,
    ("r2", "Chengdu"): 0.0009,
    ("r2", "Hangzhou"): 0.0008,
    ("r2", "Shanghai"): 0.1588,
    ("r3", "Chengdu"): 0.1833,
    ("r3", "Hangzhou"): 0.1693,
    ("r3", "Shanghai"): 0.1675,
    ("r4", "Chengdu"): 0.2907,
    ("r4", "Hangzhou"): 0.2686,
    ("r4", "Shanghai"): 0.2779,
}

# 读取所有轮次的数据
all_results = []

round_files = {
    "r1": "r1_summary.xlsx",
    "r2": "r2_summary.xlsx",
    "r3": "r3_summary.xlsx",
    "r4": "r4_summary.xlsx",
}

for round_name, filename in round_files.items():
    xl = pd.ExcelFile(filename)
    df = pd.read_excel(filename, sheet_name=xl.sheet_names[0])
    market_data = parse_market_report(df, round_name)

    for market, teams in market_data.items():
        teams_df = pd.DataFrame(teams)

        # 计算每一个队伍的原始分数
        teams_df["market_index"] = (1 + 0.1 * teams_df["agents"]) * teams_df["marketing_investment"]
        teams_df["m_factor"] = np.sqrt(teams_df["management_index"] + 1)
        teams_df["q_factor"] = np.sqrt(teams_df["quality_index"] + 1)
        teams_df["p_factor"] = avg_prices[round_name][market] / teams_df["price"]
        teams_df["mi_factor"] = np.sqrt(teams_df["market_index"])
        teams_df["raw_score"] = teams_df["m_factor"] * teams_df["q_factor"] * teams_df["p_factor"] * teams_df["mi_factor"]
        teams_df["raw_score"] = teams_df["raw_score"].fillna(0)

        # 计算总原始分数
        total_raw = teams_df["raw_score"].sum()

        # 计算每一个队伍的理论竞争力
        teams_df["theoretical_competitiveness"] = teams_df["raw_score"] / total_raw

        # 找到Team9
        team9 = teams_df[teams_df["team"] == "9"]
        if len(team9) > 0:
            team9 = team9.iloc[0]

            # 使用用户提供的实际竞争力
            actual_comp = actual_comp_data.get((round_name, market), np.nan)

            print(f"\n{'='*100}")
            print(f"{round_name} {market}")
            print(f"{'='*100}")
            print(f"所有队伍的原始分数：")
            print(teams_df[["team", "management_index", "quality_index", "agents",
                           "marketing_investment", "price", "raw_score",
                           "theoretical_competitiveness"]].to_string(index=False))
            print(f"\n总原始分数: {total_raw:.0f}")

            print(f"\nTeam9:")
            print(f"  原始分数: {team9['raw_score']:.0f}")
            print(f"  理论竞争力: {team9['theoretical_competitiveness']:.6f}")
            print(f"  实际竞争力: {actual_comp:.6f}")

            if pd.notna(actual_comp):
                error = team9['theoretical_competitiveness'] - actual_comp
                rel_error = abs(error) / actual_comp * 100
                print(f"  误差: {error:.6f} ({rel_error:.2f}%)")

                all_results.append({
                    "round": round_name,
                    "market": market,
                    "theoretical": team9['theoretical_competitiveness'],
                    "actual": actual_comp,
                    "error": error,
                    "rel_error": rel_error,
                })

print(f"\n{'='*100}")
print(f"总结")
print(f"{'='*100}")

if all_results:
    result_df = pd.DataFrame(all_results)

    round_order = {"r1": 1, "r2": 2, "r3": 3, "r4": 4}
    market_order = {"Chengdu": 1, "Hangzhou": 2, "Shanghai": 3}
    result_df["round_order"] = result_df["round"].map(round_order)
    result_df["market_order"] = result_df["market"].map(market_order)
    result_df = result_df.sort_values(["round_order", "market_order"]).drop(["round_order", "market_order"], axis=1).reset_index(drop=True)

    pd.options.display.float_format = '{:.6f}'.format
    print(result_df.to_string(index=False))

    print(f"\n平均绝对误差: {np.mean(abs(result_df['error'])):.6f}")
    print(f"平均相对误差: {np.mean(result_df['rel_error']):.2f}%")
