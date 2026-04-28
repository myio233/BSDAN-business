#!/usr/bin/env python3
import pandas as pd

# 读取r3_summary.xlsx
xl = pd.ExcelFile("r3_summary.xlsx")
df = pd.read_excel("r3_summary.xlsx", sheet_name=xl.sheet_names[0])

# 找到Shanghai市场
print("="*100)
print("r3 Shanghai 完整数据")
print("="*100)

# 先找到Shanghai市场的起始位置
start_row = None
for idx, row in df.iterrows():
    for col in df.columns:
        val = str(row[col]) if pd.notna(row[col]) else ""
        if "Market Report - Shanghai" in val:
            start_row = idx
            break
    if start_row is not None:
        break

# 找到表头行
header_row = None
for r in range(start_row, min(start_row + 20, len(df))):
    row_vals = [str(df.iloc[r, c]) if pd.notna(df.iloc[r, c]) else "" for c in range(df.shape[1])]
    if "Team" in "".join(row_vals):
        header_row = r
        break

print(f"表头行 {header_row}: {df.iloc[header_row].tolist()}")
print()

# 打印所有队伍数据
for r in range(header_row + 1, len(df)):
    team_num = df.iloc[r, 0]
    if pd.isna(team_num) or str(team_num).strip() == "":
        break

    row_data = []
    for c in range(df.shape[1]):
        val = df.iloc[r, c]
        row_data.append(val)

    print(f"队伍 {team_num}: {row_data}")
