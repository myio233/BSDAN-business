from exschool_game.inventory import build_production_snapshot, resolve_affordable_production


def test_build_production_snapshot_carries_inventory_and_storage_increment() -> None:
    snapshot = build_production_snapshot(
        target_products=100,
        new_products=80,
        component_units=650.0,
        previous_component_inventory=50.0,
        previous_product_inventory=20.0,
        previous_component_capacity=300.0,
        previous_product_capacity=40.0,
        component_material_price=200.0,
        product_material_price=500.0,
        component_storage_unit_cost=10.0,
        product_storage_unit_cost=20.0,
        patent_multiplier=1.0,
    )
    assert snapshot.component_units == 650.0
    assert snapshot.components_total == 700.0
    assert snapshot.leftover_components == 140.0
    assert snapshot.total_products_available == 100.0
    assert snapshot.component_storage_increment == 400.0
    assert snapshot.product_storage_increment == 60.0
    assert snapshot.total_cost == 650.0 * 200.0 + 80.0 * 500.0 + 400.0 * 10.0 + 60.0 * 20.0


def test_resolve_affordable_production_limits_by_cash_and_engineer_capacity() -> None:
    snapshot = resolve_affordable_production(
        requested_products=100,
        available_cash=80_000.0,
        previous_component_inventory=0.0,
        previous_product_inventory=0.0,
        previous_component_capacity=0.0,
        previous_product_capacity=0.0,
        component_material_price=100.0,
        product_material_price=200.0,
        component_storage_unit_cost=1.0,
        product_storage_unit_cost=1.0,
        patent_multiplier=1.0,
        worker_capacity=700.0,
        engineer_capacity=50.0,
    )
    assert snapshot.target_products <= 100
    assert snapshot.new_products <= 50
    assert snapshot.total_cost <= 80_000.0


def test_resolve_affordable_production_limits_by_worker_capacity_for_components() -> None:
    snapshot = resolve_affordable_production(
        requested_products=10,
        available_cash=1_000_000.0,
        previous_component_inventory=0.0,
        previous_product_inventory=0.0,
        previous_component_capacity=0.0,
        previous_product_capacity=0.0,
        component_material_price=10.0,
        product_material_price=20.0,
        component_storage_unit_cost=0.0,
        product_storage_unit_cost=0.0,
        patent_multiplier=1.0,
        worker_capacity=24.0,
        engineer_capacity=100.0,
    )
    assert snapshot.component_units == 24.0
    assert snapshot.new_products == 3
