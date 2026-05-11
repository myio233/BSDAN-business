import pandas as pd

from exschool_game.market_allocation import (
    DEFAULT_HOME_CITY_DEMAND_BOOST,
    LEGACY_HOME_CITY_DEMAND_BOOST,
    allocate_sales_with_gap_absorption,
)


def test_allocate_sales_with_gap_absorption_exposes_narrower_home_city_boost() -> None:
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

    default_allocated = allocate_sales_with_gap_absorption(scored, {"13": 30.0})
    legacy_allocated = allocate_sales_with_gap_absorption(
        scored,
        {"13": 30.0},
        home_city_demand_boost=LEGACY_HOME_CITY_DEMAND_BOOST,
    )
    neutral_allocated = allocate_sales_with_gap_absorption(
        scored,
        {"13": 30.0},
        home_city_demand_boost=1.0,
    )

    default_home_stock = float(default_allocated.loc[default_allocated["market"] == "Shanghai", "stock_in_market"].iloc[0])
    legacy_home_stock = float(legacy_allocated.loc[legacy_allocated["market"] == "Shanghai", "stock_in_market"].iloc[0])
    neutral_home_stock = float(neutral_allocated.loc[neutral_allocated["market"] == "Shanghai", "stock_in_market"].iloc[0])

    assert DEFAULT_HOME_CITY_DEMAND_BOOST < LEGACY_HOME_CITY_DEMAND_BOOST
    assert neutral_home_stock < default_home_stock < legacy_home_stock
    assert float(default_allocated["stock_in_market"].sum()) == 30.0
    assert float(legacy_allocated["stock_in_market"].sum()) == 30.0
    assert float(neutral_allocated["stock_in_market"].sum()) == 30.0
