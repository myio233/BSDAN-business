#!/usr/bin/env python3
import pandas as pd

def parse_market(df, market_name):
    """解析特定市场的数据"""
    # 找到市场起始位置
    start_row = None
    for idx, row in df.iterrows():
        for col in df.columns:
            val = str(row[col]) if pd.notna(row[col]) else ""
            if f"Market Report - {market_name}" in val:
                start_row = idx
                break
        if start_row is not None:
            break

    if start_row is None:
        return None

    # 找到表头行（包含"Team"的行）
    header_row = None
    for r in range(start_row, min(start_row + 20, len(df))):
        row_vals = [str(df.iloc[r, c]) if pd.notna(df.iloc[r, c]) else "" for c in range(df.shape[1])]
        if "Team" in "".join(row_vals):
            header_row = r
            break

    if header_row is None:
        return None

    print(f"\n{'='*80}")
    print(f"市场: {market_name}")
    print(f"{'='*80}")
    print(f"表头: {df.iloc[header_row].tolist()}")

    # 读取队伍数据
    teams = []
    for r in range(header_row + 1, len(df)):
        team_num = df.iloc[r, 0]
        if pd.isna(team_num) or str(team_num).strip() == "":
            break

        row_data = []
        for c in range(df.shape[1]):
            val = df.iloc[r, c]
            row_data.append(val)
        teams.append(row_data)

        if str(team_num) == "9":
            print(f"\nTeam 9 数据: {row_data}")

    return teams

# 读取r2_summary.xlsx
xl = pd.ExcelFile("r2_summary.xlsx")
df = pd.read_excel("r2_summary.xlsx", sheet_name=xl.sheet_names[0])

for market in ["Chengdu", "Hangzhou", "Shanghai"]:
    parse_market(df, market)

print("\n" + "="*80)
print("对比用户提供的竞争力值：")
print("="*80)
print("r2 Chengdu: 0.0009")
print("r2 Hangzhou: 0.0008")
print("r2 Shanghai: 0.1588")
