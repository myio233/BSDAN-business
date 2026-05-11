#!/usr/bin/env python3
import pandas as pd

# 读取r4_summary.xlsx
xl = pd.ExcelFile("r4_summary.xlsx")
df = pd.read_excel("r4_summary.xlsx", sheet_name=xl.sheet_names[0])

markets = ["Chengdu", "Hangzhou", "Shanghai"]

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

    print(f"\n{'='*80}")
    print(f"r4 {market}")
    print(f"{'='*80}")
    print(f"表头: {df.iloc[header_row].tolist()}")

    # 读取所有队伍数据
    for r in range(header_row + 1, len(df)):
        team_num = df.iloc[r, 0]
        if pd.isna(team_num) or str(team_num).strip() == "":
            break

        row_data = []
        for c in range(df.shape[1]):
            val = df.iloc[r, c]
            row_data.append(val)

        if str(team_num) == "9":
            print(f"Team9: {row_data}")
