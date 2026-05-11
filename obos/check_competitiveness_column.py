#!/usr/bin/env python3
import pandas as pd

# 读取r2_summary.xlsx，特别注意竞争力列
xl = pd.ExcelFile("r2_summary.xlsx")
df = pd.read_excel("r2_summary.xlsx", sheet_name=xl.sheet_names[0])

print("r2_summary.xlsx 的内容（包含竞争力列）：")
print("="*120)

# 找到包含"Team"的行
header_row = None
for r in range(len(df)):
    row_vals = [str(df.iloc[r, c]) if pd.notna(df.iloc[r, c]) else "" for c in range(df.shape[1])]
    if "Team" in "".join(row_vals):
        header_row = r
        break

print(f"\n表头在第 {header_row} 行：")
print(df.iloc[header_row].tolist())

# 读取队伍数据，直到遇到空行
print("\n队伍数据：")
print("-"*120)
for r in range(header_row + 1, len(df)):
    team_num = df.iloc[r, 0]
    if pd.isna(team_num) or str(team_num).strip() == "":
        break
    row_data = []
    for c in range(df.shape[1]):
        val = df.iloc[r, c]
        if pd.notna(val):
            row_data.append(str(val))
    print(f"队伍 {team_num}: {row_data}")
