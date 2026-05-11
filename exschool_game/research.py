from __future__ import annotations

import hashlib

import numpy as np


def research_success_probability(total_research_pool: float) -> float:
    if total_research_pool <= 0:
        return 0.0
    invested_million = float(total_research_pool) / 1_000_000.0
    probability = 1.0 / (1.0 + (max(invested_million, 1e-9) / 3.0) ** (-1.585))
    return float(np.clip(probability, 0.0, 1.0))


def deterministic_uniform(*parts: object) -> float:
    seed = "|".join(str(part) for part in parts).encode("utf-8")
    digest = hashlib.sha256(seed).digest()
    integer = int.from_bytes(digest[:8], "big", signed=False)
    return integer / float(2**64)


def patent_cost_multiplier(active_patents: int) -> float:
    return 0.7 ** max(int(active_patents), 0)
