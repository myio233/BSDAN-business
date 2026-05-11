#!/usr/bin/env python3
import pandas as pd
import os

base_path = "/mnt/c/Users/david/Documents/ASDAN/表格/结果"

# Read all summary files
for round_num in range(1, 5):
    filename = os.path.join(base_path, f"r{round_num}_summary.xlsx")
    print(f"\n{'='*60}")
    print(f"Reading r{round_num}_summary.xlsx")
    print(f"{'='*60}")

    try:
        xl = pd.ExcelFile(filename)
        print(f"Sheet names: {xl.sheet_names}")

        for sheet_name in xl.sheet_names:
            df = pd.read_excel(filename, sheet_name=sheet_name)
            print(f"\n--- Sheet: {sheet_name} ---")
            print(df.to_string())
    except Exception as e:
        print(f"Error reading: {e}")
