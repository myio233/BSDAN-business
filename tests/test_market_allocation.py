import numpy as np
import pandas as pd

from exschool_game.market_allocation import (
    allocate_sales_with_gap_absorption,
    integer_allocate_by_weights,
    redistribute_market_gaps,
)


def test_integer_allocate_by_weights_preserves_total_and_biases_larger_weights() -> None:
    allocated = integer_allocate_by_weights(10, np.array([1.0, 2.0, 7.0]))
    assert allocated.sum() == 10
    assert list(allocated) == [1.0, 2.0, 7.0]


def test_integer_allocate_by_weights_spreads_zero_weights_instead_of_dumping_to_first() -> None:
    allocated = integer_allocate_by_weights(5, np.array([0.0, 0.0, 0.0]))
    assert allocated.sum() == 5
    assert list(allocated) == [2.0, 1.0, 2.0]


def test_integer_allocate_by_weights_rotates_equal_weight_remainders() -> None:
    allocated = integer_allocate_by_weights(2, np.array([1.0, 1.0, 1.0]))
    assert allocated.sum() == 2
    assert list(allocated) == [1.0, 0.0, 1.0]


def test_redistribute_market_gaps_moves_sales_to_stronger_absorber() -> None:
    rows = pd.DataFrame(
        [
            {
                "team": "A",
                "management_index": 10.0,
                "market_index": 10.0,
                "quality_index": 10.0,
                "predicted_theoretical_cpi": 0.3,
                "leftover_stock": 4.0,
                "unmet_demand": 0.0,
                "final_sales": 6.0,
            },
            {
                "team": "B",
                "management_index": 1.0,
                "market_index": 1.0,
                "quality_index": 1.0,
                "predicted_theoretical_cpi": 0.1,
                "leftover_stock": 0.0,
                "unmet_demand": 3.0,
                "final_sales": 2.0,
            },
        ],
        index=[0, 1],
    )
    updated = redistribute_market_gaps(rows)
    assert updated.loc[0, "final_sales"] == 9.0
    assert updated.loc[0, "leftover_stock"] == 1.0
    assert updated.loc[1, "unmet_demand"] == 0.0


def test_redistribute_market_gaps_respects_zero_absorption_cap() -> None:
    rows = pd.DataFrame(
        [
            {
                "team": "A",
                "management_index": 10.0,
                "market_index": 10.0,
                "quality_index": 10.0,
                "predicted_theoretical_cpi": 0.3,
                "leftover_stock": 4.0,
                "unmet_demand": 0.0,
                "final_sales": 6.0,
                "cpi_demand_units_int": 6.0,
            },
            {
                "team": "B",
                "management_index": 1.0,
                "market_index": 1.0,
                "quality_index": 1.0,
                "predicted_theoretical_cpi": 0.1,
                "leftover_stock": 0.0,
                "unmet_demand": 3.0,
                "final_sales": 2.0,
                "cpi_demand_units_int": 2.0,
            },
        ],
        index=[0, 1],
    )
    updated = redistribute_market_gaps(rows, cap_ratio=0.0)
    assert updated.loc[0, "final_sales"] == 6.0
    assert updated.loc[0, "leftover_stock"] == 4.0
    assert updated.loc[1, "unmet_demand"] == 3.0


def test_allocate_sales_with_gap_absorption_boosts_home_city_when_stock_is_short() -> None:
    scored = pd.DataFrame(
        [
            {
                "team": "13",
                "market": "Shanghai",
                "home_city": "Shanghai",
                "predicted_marketshare_unconstrained": 0.5,
                "market_size": 100.0,
                "sales_volume": 0.0,
                "active_market": True,
                "management_index": 1.0,
                "market_index": 1.0,
                "quality_index": 1.0,
                "predicted_theoretical_cpi": 0.5,
            },
            {
                "team": "13",
                "market": "Chengdu",
                "home_city": "Shanghai",
                "predicted_marketshare_unconstrained": 0.5,
                "market_size": 100.0,
                "sales_volume": 0.0,
                "active_market": True,
                "management_index": 1.0,
                "market_index": 1.0,
                "quality_index": 1.0,
                "predicted_theoretical_cpi": 0.5,
            },
        ]
    )
    allocated = allocate_sales_with_gap_absorption(scored, {"13": 30.0})
    shanghai_stock = float(allocated.loc[allocated["market"] == "Shanghai", "stock_in_market"].iloc[0])
    chengdu_stock = float(allocated.loc[allocated["market"] == "Chengdu", "stock_in_market"].iloc[0])
    assert shanghai_stock > chengdu_stock
    assert shanghai_stock + chengdu_stock == 30.0
