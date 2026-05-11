#!/usr/bin/env python3
import pandas as pd
import numpy as np

# 只读取r3的数据
xl = pd.ExcelFile("r3_summary.xlsx")
df = pd.read_excel("r3_summary.xlsx", sheet_name=xl.sheet_names[0])

markets = ["Chengdu", "Hangzhou", "Shanghai"]
avg_prices = {"Chengdu": 15993, "Hangzhou": 18351, "Shanghai": 17418}

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

    print(f"\n{'='*100}")
    print(f"r3 {market}")
    print(f"{'='*100}")

    # 读取所有队伍数据
    teams = []
    for r in range(header_row + 1, len(df)):
        team_num = df.iloc[r, 0]
        if pd.isna(team_num) or str(team_num).strip() == "":
            break

        team_data = {
            "team": str(team_num),
            "management_index": pd.to_numeric(df.iloc[r, 1], errors="coerce"),
            "agents": pd.to_numeric(df.iloc[r, 2], errors="coerce"),
            "marketing_investment": pd.to_numeric(df.iloc[r, 3], errors="coerce"),
            "quality_index": pd.to_numeric(df.iloc[r, 4], errors="coerce"),
            "price": pd.to_numeric(df.iloc[r, 5], errors="coerce"),
        }

        # 读取竞争力列
        if df.shape[1] > 8:
            comp_val = df.iloc[r, 8]
            if pd.notna(comp_val):
                team_data["actual_competitiveness"] = pd.to_numeric(comp_val, errors="coerce")

        teams.append(team_data)

    # 计算
    teams_df = pd.DataFrame(teams)
    teams_df["market_index"] = (1 + 0.1 * teams_df["agents"]) * teams_df["marketing_investment"]
    teams_df["m_factor"] = np.sqrt(teams_df["management_index"] + 1)
    teams_df["q_factor"] = np.sqrt(teams_df["quality_index"] + 1)
    teams_df["p_factor"] = avg_prices[market] / teams_df["price"]
    teams_df["mi_factor"] = np.sqrt(teams_df["market_index"])
    teams_df["raw_score"] = teams_df["m_factor"] * teams_df["q_factor"] * teams_df["p_factor"] * teams_df["mi_factor"]
    teams_df["raw_score"] = teams_df["raw_score"].fillna(0)

    total_raw = teams_df["raw_score"].sum()
    teams_df["theoretical_competitiveness"] = teams_df["raw_score"] / total_raw

    pd.options.display.float_format = '{:.6f}'.format
    print(teams_df[["team", "management_index", "quality_index", "agents",
                     "marketing_investment", "price", "raw_score",
                     "theoretical_competitiveness", "actual_competitiveness"]].to_string(index=False))

    print(f"\n总原始分数: {total_raw:.0f}")

    # 显示Team9
    team9 = teams_df[teams_df["team"] == "9"].iloc[0]
    if pd.notna(team9.get("actual_competitiveness")):
        print(f"\nTeam9:")
        print(f"  原始分数: {team9['raw_score']:.0f}")
        print(f"  理论竞争力: {team9['theoretical_competitiveness']:.6f}")
        print(f"  实际竞争力: {team9['actual_competitiveness']:.6f}")
        print(f"  误差: {team9['theoretical_competitiveness'] - team9['actual_competitiveness']:.6f}")
