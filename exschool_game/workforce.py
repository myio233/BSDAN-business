from __future__ import annotations

from typing import Any

import numpy as np

PRODUCTIVITY_SALARY_ELASTICITY = 0.85
MIN_PRODUCTIVITY_MULTIPLIER = 0.72
MAX_PRODUCTIVITY_MULTIPLIER = 1.28


def smoothed_average_salary(current_average: float, previous_average: float) -> float:
    return (float(current_average) - float(previous_average)) * 0.4 + float(previous_average)


def salary_ratio(requested_salary: float, benchmark_average_salary: float) -> float:
    benchmark = max(float(benchmark_average_salary), 1.0)
    return max(float(requested_salary), 0.0) / benchmark


def productivity_multiplier_from_ratio(ratio: float) -> float:
    bounded_ratio = min(max(float(ratio), 0.35), 1.65)
    multiplier = bounded_ratio**PRODUCTIVITY_SALARY_ELASTICITY
    return float(min(max(multiplier, MIN_PRODUCTIVITY_MULTIPLIER), MAX_PRODUCTIVITY_MULTIPLIER))


def productivity_multiplier(requested_salary: float, benchmark_average_salary: float) -> float:
    return productivity_multiplier_from_ratio(salary_ratio(requested_salary, benchmark_average_salary))


def cut_headcount(recent: int, mature: int, experienced: int, count: int) -> tuple[int, int, int, int, int, int]:
    remaining = int(max(count, 0))
    recent_cut = min(recent, remaining)
    recent -= recent_cut
    remaining -= recent_cut
    mature_cut = min(mature, remaining)
    mature -= mature_cut
    remaining -= mature_cut
    exp_cut = min(experienced, remaining)
    experienced -= exp_cut
    return recent, mature, experienced, recent_cut, mature_cut, exp_cut


def workforce_plan(
    *,
    requested_total: int,
    requested_salary: float,
    benchmark_average_salary: float,
    previous_recent: int,
    previous_mature: int,
    previous_experienced: int,
) -> dict[str, Any]:
    previous_total = int(previous_recent + previous_mature + previous_experienced)
    salary_ratio = (
        min(max(float(requested_salary) / max(float(benchmark_average_salary), 1.0), 0.0), 1.25)
        if previous_total > 0
        else 1.0
    )
    desired_added = max(int(requested_total) - previous_total, 0)
    layoffs = max(previous_total - int(requested_total), 0)
    quits = 0
    allowed_added = desired_added
    if salary_ratio < 1.0 and previous_total > 0:
        quits = int(np.ceil(previous_total * (1.0 - salary_ratio) * 0.12))
        allowed_added = int(np.floor(desired_added * salary_ratio))
    recent, mature, experienced, laid_recent, laid_mature, laid_experienced = cut_headcount(
        int(previous_recent),
        int(previous_mature),
        int(previous_experienced),
        layoffs,
    )
    recent, mature, experienced, quit_recent, quit_mature, quit_experienced = cut_headcount(
        recent,
        mature,
        experienced,
        quits,
    )
    promoted_this_round = mature
    current_recent = recent + allowed_added
    current_mature = 0
    current_experienced = experienced + promoted_this_round
    promoted_next = recent
    next_recent = allowed_added
    next_mature = recent
    next_experienced = current_experienced
    return {
        "working": current_recent + current_mature + current_experienced,
        "added": allowed_added,
        "laid_off": layoffs,
        "quits": quits,
        "recent": current_recent,
        "mature": current_mature,
        "experienced": current_experienced,
        "next_recent": next_recent,
        "next_mature": next_mature,
        "next_experienced": next_experienced,
        "promotion_ready": promoted_next,
        "promoted_this_round": promoted_this_round,
        "previous_inexperienced": int(previous_recent + previous_mature),
        "previous_experienced": int(previous_experienced),
        "working_inexperienced": current_recent + current_mature,
        "working_experienced": current_experienced,
        "laid_off_inexperienced": int(laid_recent + laid_mature),
        "laid_off_experienced": int(laid_experienced),
        "quits_inexperienced": int(quit_recent + quit_mature),
        "quits_experienced": int(quit_experienced),
        "salary_ratio": salary_ratio,
        "average_salary": float(benchmark_average_salary),
    }
