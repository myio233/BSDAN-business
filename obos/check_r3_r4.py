#!/usr/bin/env python3
import pandas as pd

def check_file(filename, round_name):
    print(f"\n{'='*100}")
    print(f"检查 {filename}")
    print(f"{'='*100}")

    xl = pd.ExcelFile(filename)
    df = pd.read_excel(filename, sheet_name=xl.sheet_names[0])

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

        print(f"\n--- {round_name} {market} ---")
        print(f"表头: {df.iloc[header_row].tolist()}")

        # 找到Team9
        for r in range(header_row + 1, len(df)):
            team_num = df.iloc[r, 0]
            if pd.isna(team_num) or str(team_num).strip() == "":
                break
            if str(team_num) == "9":
                row_data = []
                for c in range(df.shape[1]):
                    val = df.iloc[r, c]
                    row_data.append(val)
                print(f"Team9数据: {row_data}")
                break

check_file("r2_summary.xlsx", "r2")
check_file("r3_summary.xlsx", "r3")
check_file("r4_summary.xlsx", "r4")

print("\n" + "="*100)
print("用户之前提供的竞争力值：")
print("="*100)
print("r1: Chengdu=0.0080, Hangzhou=0.0132, Shanghai=0.0088")
print("r2: Chengdu=0.0009, Hangzhou=0.0008, Shanghai=0.1588")
print("r3: Chengdu=0.1833, Hangzhou=0.1693, Shanghai=0.1675")
print("r4: Chengdu=0.2907, Hangzhou=0.2686, Shanghai=0.2779")
