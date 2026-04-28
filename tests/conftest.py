from __future__ import annotations

from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
PRIVATE_DATA_SENTINEL = ROOT / "exschool" / "asdan_key_data_sheet.xlsx"

PRIVATE_DATA_TEST_MODULES = {
    "test_engine_data_fidelity.py",
    "test_engine_report_correctness.py",
    "test_exschool_game_hr.py",
    "test_exschool_game_modes.py",
    "test_modeling_stage2_late_hangzhou_residual.py",
    "test_modeling_stage2_market_context.py",
    "test_multiplayer_mode.py",
    "test_reconstruct_exschool_decisions.py",
    "test_report_notes_visibility.py",
    "test_report_payload.py",
}


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--run-private-data",
        action="store_true",
        default=False,
        help="Run tests that require private source workbooks under exschool/.",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if config.getoption("--run-private-data") or PRIVATE_DATA_SENTINEL.exists():
        return

    skip_private = pytest.mark.skip(
        reason="requires private source workbooks; rerun with --run-private-data after restoring local exschool/ inputs"
    )
    for item in items:
        if Path(str(item.fspath)).name in PRIVATE_DATA_TEST_MODULES:
            item.add_marker(skip_private)
