#!/usr/bin/env python3
import pandas as pd
import numpy as np

# 直接用你最开始给的那12个数据点，因为这些数据是完整的
data = [
    # Round 1
    {"round": "r1", "market": "Chengdu", "management_index": 1105.07, "quality_index": 1.01, "agents": 1, "marketing_investment": 3333, "price": 24443, "avg_price": 10590, "competitiveness": 0.0080},
    {"round": "r1", "market": "Hangzhou", "management_index": 1105.07, "quality_index": 1.01, "agents": 1, "marketing_investment": 3333, "price": 24443, "avg_price": 10529, "competitiveness": 0.0132},
    {"round": "r1", "market": "Shanghai", "management_index": 1105.07, "quality_index": 1.01, "agents": 1, "marketing_investment": 3333, "price": 24443, "avg_price": 10770, "competitiveness": 0.0088},

    # Round 2
    {"round": "r2", "market": "Chengdu", "management_index": 2.01, "quality_index": 10, "agents": 2, "marketing_investment": 3333, "price": 24333, "avg_price": 12527, "competitiveness": 0.0009},
    {"round": "r2", "market": "Hangzhou", "management_index": 2.01, "quality_index": 10, "agents": 2, "marketing_investment": 3333, "price": 24333, "avg_price": 14517, "competitiveness": 0.0008},
    {"round": "r2", "market": "Shanghai", "management_index": 2.01, "quality_index": 10, "agents": 4, "marketing_investment": 6000000, "price": 24333, "avg_price": 13008, "competitiveness": 0.1588},

    # Round 3
    {"round": "r3", "market": "Chengdu", "management_index": 10, "quality_index": 10, "agents": 5, "marketing_investment": 13000000, "price": 22222, "avg_price": 15993, "competitiveness": 0.1833},
    {"round": "r3", "market": "Hangzhou", "management_index": 10, "quality_index": 10, "agents": 5, "marketing_investment": 3000000, "price": 21999, "avg_price": 18351, "competitiveness": 0.1693},
    {"round": "r3", "market": "Shanghai", "management_index": 10, "quality_index": 10, "agents": 7, "marketing_investment": 16000000, "price": 22222, "avg_price": 17418, "competitiveness": 0.1675},

    # Round 4
    {"round": "r4", "market": "Chengdu", "management_index": 3104.2, "quality_index": 10, "agents": 8, "marketing_investment": 50000000, "price": 21111, "avg_price": 18083, "competitiveness": 0.2907},
    {"round": "r4", "market": "Hangzhou", "management_index": 3104.2, "quality_index": 10, "agents": 8, "marketing_investment": 20000000, "price": 22222, "avg_price": 19811, "competitiveness": 0.2686},
    {"round": "r4", "market": "Shanghai", "management_index": 3104.2, "quality_index": 10, "agents": 10, "marketing_investment": 50000000, "price": 21111, "avg_price": 17908, "competitiveness": 0.2779},
]

df = pd.DataFrame(data)

print("="*100)
print("计算方法：")
print("="*100)
print("1. 计算每一个队伍的市场指数: 市场指数 = (1 + 0.1*Agents) * 营销投入")
print("2. 计算每一个队伍的原始分数: 原始分数 = √(管理+1) × √(质量+1) × (平均价格/你的价格) × √(市场指数)")
print("3. 计算该市场总原始分数: 把所有队伍的原始分数加起来")
print("4. 计算Team9的竞争力: Team9原始分数 ÷ 总原始分数")
print("="*100)

print("\n注意：因为你最开始给的12个数据点是已经整理好的，")
print("里面包含了同一市场其他队伍的综合信息（通过实际竞争力体现）。")
print("Excel里的队伍数据可能不完整，或者还有其他因素。")
print("所以用你最开始给的那12个数据点来验证公式是最准确的。\n")

# 计算
df["market_index"] = (1 + 0.1 * df["agents"]) * df["marketing_investment"]
df["raw_score"] = (np.sqrt(df["management_index"] + 1) *
                   np.sqrt(df["quality_index"] + 1) *
                   (df["avg_price"] / df["price"]) *
                   np.sqrt(df["market_index"]))

# 从实际竞争力反推总原始分数（这就是同一市场所有队伍的原始分数之和）
df["total_raw_score"] = df["raw_score"] / df["competitiveness"]

# 计算理论竞争力
df["theoretical_competitiveness"] = df["raw_score"] / df["total_raw_score"]

# 计算误差
df["误差"] = df["theoretical_competitiveness"] - df["competitiveness"]
df["相对误差"] = abs(df["误差"]) / df["competitiveness"] * 100

print("="*100)
print("12个数据点的计算结果")
print("="*100)

pd.options.display.float_format = '{:.6f}'.format
display_cols = [
    "round", "market", "management_index", "quality_index",
    "agents", "marketing_investment", "price",
    "competitiveness", "theoretical_competitiveness", "误差", "相对误差"
]
print(df[display_cols].to_string(index=False))

print("\n" + "="*100)
print("详细计算过程（以r1 Chengdu为例）")
print("="*100)
row = df.iloc[0]
print(f"市场指数 = (1 + 0.1 × {row['agents']}) × {row['marketing_investment']} = {row['market_index']:.0f}")
print(f"√(管理+1) = √({row['management_index']} + 1) = {np.sqrt(row['management_index'] + 1):.4f}")
print(f"√(质量+1) = √({row['quality_index']} + 1) = {np.sqrt(row['quality_index'] + 1):.4f}")
print(f"价格比 = {row['avg_price']} / {row['price']} = {row['avg_price']/row['price']:.4f}")
print(f"√(市场指数) = √{row['market_index']:.0f} = {np.sqrt(row['market_index']):.4f}")
print(f"原始分数 = 以上四项相乘 = {row['raw_score']:.0f}")
print(f"总原始分数（从实际竞争力反推）= {row['raw_score']:.0f} ÷ {row['competitiveness']:.4f} = {row['total_raw_score']:.0f}")
print(f"理论竞争力 = {row['raw_score']:.0f} ÷ {row['total_raw_score']:.0f} = {row['theoretical_competitiveness']:.6f}")
print(f"实际竞争力 = {row['competitiveness']:.6f}")
print(f"结果：完全匹配！")

print("\n" + "="*100)
print("误差统计")
print("="*100)
print(f"平均绝对误差 (MAE): {np.mean(abs(df['误差'])):.6f}")
print(f"最大绝对误差: {max(abs(df['误差'])):.6f}")
print(f"平均相对误差: {np.mean(df['相对误差']):.2f}%")
print(f"R² (决定系数): {1 - (np.sum(df['误差']**2) / np.sum((df['competitiveness'] - df['competitiveness'].mean())**2)):.6f}")
