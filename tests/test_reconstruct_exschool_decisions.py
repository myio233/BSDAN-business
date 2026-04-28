from __future__ import annotations

from obos.reconstruct_exschool_decisions import (
    SOURCE_ESTIMATED,
    SOURCE_ROUND_CONTEXT,
    SOURCE_SIMULATOR_ACTUAL,
    ExschoolSimulator,
    add_agent_deltas,
    build_numeric_export,
    build_round_proxies,
    ensure_full_grid,
    override_team13_markets,
    override_team13_round_summary,
    parse_market_reports,
    round_summary_frame,
)


def _build_reconstruction_exports():
    simulator = ExschoolSimulator()
    proxies = build_round_proxies(simulator)
    visible = parse_market_reports()
    full = ensure_full_grid(visible)
    full = add_agent_deltas(full)
    full = override_team13_markets(full, simulator)
    full = add_agent_deltas(full)
    summary = override_team13_round_summary(round_summary_frame(full, proxies), full, simulator)
    numeric = build_numeric_export(full, summary)
    return simulator, summary, numeric


def test_build_round_proxies_uses_round_context_storage_units() -> None:
    simulator = ExschoolSimulator()
    proxies = build_round_proxies(simulator)

    for round_id, proxy in proxies.items():
        ctx = simulator.round_contexts[round_id]
        assert proxy.component_storage_unit == float(ctx["component_storage_unit_cost"])
        assert proxy.product_storage_unit == float(ctx["product_storage_unit_cost"])


def test_reconstruction_exports_include_hidden_field_provenance() -> None:
    _simulator, summary, numeric = _build_reconstruction_exports()

    team1_r1_summary = summary[(summary["team"] == "1") & (summary["round_id"] == "r1")].iloc[0]
    assert team1_r1_summary["loan_delta_est_provenance"] == SOURCE_ESTIMATED
    assert team1_r1_summary["worker_salary_est_provenance"] == SOURCE_ROUND_CONTEXT
    assert team1_r1_summary["component_storage_unit_est_provenance"] == SOURCE_ROUND_CONTEXT
    assert team1_r1_summary["product_storage_unit_est_provenance"] == SOURCE_ROUND_CONTEXT

    team13_r1_summary = summary[(summary["team"] == "13") & (summary["round_id"] == "r1")].iloc[0]
    assert team13_r1_summary["starting_cash_est_provenance"] == SOURCE_SIMULATOR_ACTUAL
    assert team13_r1_summary["workers_est_provenance"] == SOURCE_SIMULATOR_ACTUAL
    assert team13_r1_summary["products_planned_est_provenance"] == SOURCE_SIMULATOR_ACTUAL
    assert team13_r1_summary["market_report_cost_est_provenance"] == SOURCE_SIMULATOR_ACTUAL

    team1_r1_numeric = numeric[(numeric["team"] == "1") & (numeric["round_id"] == "r1")].iloc[0]
    assert team1_r1_numeric["loan_delta_provenance"] == SOURCE_ESTIMATED
    assert team1_r1_numeric["worker_salary_provenance"] == SOURCE_ROUND_CONTEXT
    assert team1_r1_numeric["engineers_provenance"] == SOURCE_ESTIMATED
