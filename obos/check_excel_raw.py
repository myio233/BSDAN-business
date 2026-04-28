#!/usr/bin/env python3
import pandas as pd

# 读取所有Excel文件，看看它们的完整内容
round_files = {
    "r1": "r1_summary.xlsx",
    "r2": "r2_summary.xlsx",
    "r3": "r3_summary.xlsx",
    "r4": "r4_summary.xlsx",
}

for round_name, filename in round_files.items():
    print(f"\n{'='*100}")
    print(f"{filename}")
    print(f"{'='*100}")

    xl = pd.ExcelFile(filename)
    df = pd.read_excel(filename, sheet_name=xl.sheet_names[0])

    # 打印前50行，看看内容
    print(df.head(60).to_string())
