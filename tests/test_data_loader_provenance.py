from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd
import pytest

from exschool_game import data_loader


def _write_fixed_decisions_workbook(path: Path, *, missing_team_id: int = 11) -> None:
    rows = []
    for team_id in range(1, 25):
        if team_id == missing_team_id:
            continue
        rows.append({"team": str(team_id), "round_id": "r1", "loan_delta": team_id * 100})
    pd.DataFrame(rows).to_excel(path, index=False)


def test_parse_fixed_team_decisions_logs_source_dir_roster_and_missing_ids(tmp_path, monkeypatch, caplog) -> None:
    workbook = tmp_path / "all_companies_numeric_decisions_real_original_fixed.xlsx"
    _write_fixed_decisions_workbook(workbook)
    monkeypatch.setattr(data_loader, "REAL_ORIGINAL_FIXED_DECISIONS_XLSX", workbook)

    with caplog.at_level(logging.INFO, logger="exschool_game.data_loader"):
        df = data_loader.parse_fixed_team_decisions("real-original")

    assert len(df) == 23
    assert "source_dir=" + str(tmp_path) in caplog.text
    assert "roster_size=23" in caplog.text
    assert "missing_team_ids=11" in caplog.text


def test_parse_fixed_round_summary_refuses_missing_validation_artifact(tmp_path, monkeypatch) -> None:
    workbook = tmp_path / "all_companies_numeric_decisions_real_original_fixed.xlsx"
    _write_fixed_decisions_workbook(workbook)
    summary_path = tmp_path / "all_round_reconstruction_summary.xlsx"
    monkeypatch.setattr(data_loader, "REAL_ORIGINAL_FIXED_DECISIONS_XLSX", workbook)
    monkeypatch.setattr(data_loader, "REAL_ORIGINAL_ROUND_SUMMARY_XLSX", summary_path)

    with pytest.raises(FileNotFoundError, match="Missing real-original round summary workbook") as exc_info:
        data_loader.parse_fixed_round_summary("real-original")

    message = str(exc_info.value)
    assert f"source_dir={tmp_path}" in message
    assert "roster_size=23" in message
    assert "missing_team_ids=11" in message
