from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class ProductionSnapshot:
    target_products: int
    new_products: int
    component_units: float
    components_total: float
    components_used: float
    leftover_components: float
    total_products_available: float
    component_capacity_after: float
    product_capacity_after: float
    component_storage_increment: float
    product_storage_increment: float
    component_material_cost: float
    product_material_cost: float
    component_storage_cost: float
    product_storage_cost: float
    total_cost: float
    quality_denominator: float


def build_production_snapshot(
    *,
    target_products: int,
    new_products: int,
    component_units: float,
    previous_component_inventory: float,
    previous_product_inventory: float,
    previous_component_capacity: float,
    previous_product_capacity: float,
    component_material_price: float,
    product_material_price: float,
    component_storage_unit_cost: float,
    product_storage_unit_cost: float,
    patent_multiplier: float,
) -> ProductionSnapshot:
    target_products = int(max(target_products, 0))
    new_products = int(max(new_products, 0))
    component_units = max(float(component_units), 0.0)
    components_total = previous_component_inventory + component_units
    components_used = float(new_products) * 7.0
    leftover_components = max(components_total - components_used, 0.0)
    total_products_available = previous_product_inventory + float(new_products)
    component_capacity_after = components_total
    product_capacity_after = total_products_available
    component_storage_increment = max(component_capacity_after - previous_component_capacity, 0.0)
    product_storage_increment = max(product_capacity_after - previous_product_capacity, 0.0)
    component_material_cost = component_units * component_material_price * patent_multiplier
    product_material_cost = float(new_products) * product_material_price * patent_multiplier
    component_storage_cost = component_storage_increment * component_storage_unit_cost
    product_storage_cost = product_storage_increment * product_storage_unit_cost
    total_cost = component_material_cost + product_material_cost + component_storage_cost + product_storage_cost
    quality_denominator = previous_product_inventory * 1.2 + float(new_products)
    return ProductionSnapshot(
        target_products=target_products,
        new_products=new_products,
        component_units=component_units,
        components_total=components_total,
        components_used=components_used,
        leftover_components=leftover_components,
        total_products_available=total_products_available,
        component_capacity_after=component_capacity_after,
        product_capacity_after=product_capacity_after,
        component_storage_increment=component_storage_increment,
        product_storage_increment=product_storage_increment,
        component_material_cost=component_material_cost,
        product_material_cost=product_material_cost,
        component_storage_cost=component_storage_cost,
        product_storage_cost=product_storage_cost,
        total_cost=total_cost,
        quality_denominator=quality_denominator,
    )


def resolve_affordable_production(
    *,
    requested_products: int,
    available_cash: float,
    previous_component_inventory: float,
    previous_product_inventory: float,
    previous_component_capacity: float,
    previous_product_capacity: float,
    component_material_price: float,
    product_material_price: float,
    component_storage_unit_cost: float,
    product_storage_unit_cost: float,
    patent_multiplier: float,
    worker_capacity: float,
    engineer_capacity: float,
) -> ProductionSnapshot:
    def snapshot_for_target(target_products: int) -> ProductionSnapshot:
        target_products = int(max(target_products, 0))
        target_component_need = max(float(target_products) * 7.0 - previous_component_inventory, 0.0)
        component_units = min(target_component_need, worker_capacity)
        total_components_available = previous_component_inventory + component_units
        new_products = int(min(float(target_products), engineer_capacity, math.floor(total_components_available / 7.0)))
        return build_production_snapshot(
            target_products=target_products,
            new_products=new_products,
            component_units=component_units,
            previous_component_inventory=previous_component_inventory,
            previous_product_inventory=previous_product_inventory,
            previous_component_capacity=previous_component_capacity,
            previous_product_capacity=previous_product_capacity,
            component_material_price=component_material_price,
            product_material_price=product_material_price,
            component_storage_unit_cost=component_storage_unit_cost,
            product_storage_unit_cost=product_storage_unit_cost,
            patent_multiplier=patent_multiplier,
        )

    requested_products = max(int(requested_products), 0)
    low, high = 0, requested_products
    best_target = 0
    while low <= high:
        mid = (low + high) // 2
        snapshot = snapshot_for_target(mid)
        if snapshot.total_cost <= available_cash + 1e-9:
            best_target = mid
            low = mid + 1
        else:
            high = mid - 1
    return snapshot_for_target(best_target)
