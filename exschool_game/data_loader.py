from __future__ import annotations

import logging
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


LOGGER = logging.getLogger(__name__)
ROOT_DIR = Path(__file__).resolve().parents[1]
OBOS_DIR = ROOT_DIR / "obos"
EXSCHOOL_DIR = ROOT_DIR / "exschool"
EXSCHOOL_EXPORT_DIR = ROOT_DIR / "outputs" / "exschool_market_report_exports"
EXSCHOOL_STRUCTURED_DIR = EXSCHOOL_EXPORT_DIR / "structured_xlsx"
FIXED_DECISIONS_XLSX = ROOT_DIR / "outputs" / "exschool_inferred_decisions" / "all_companies_numeric_decisions.xlsx"
SMART_FIXED_DECISIONS_XLSX = ROOT_DIR / "outputs" / "exschool_inferred_decisions" / "all_companies_numeric_decisions_smart.xlsx"
REAL_ORIGINAL_FIXED_DECISIONS_XLSX = ROOT_DIR / "outputs" / "exschool_inferred_decisions" / "all_companies_numeric_decisions_real_original_fixed.xlsx"
REAL_ORIGINAL_ROUND_SUMMARY_XLSX = ROOT_DIR / "outputs" / "exschool_inferred_decisions" / "all_round_reconstruction_summary.xlsx"
GENERATE_SMART_FIXED_OPPONENTS_SCRIPT = ROOT_DIR / "scripts" / "generate_smart_fixed_opponents.py"
FIXED_DECISION_MODE_ALIASES = {
    "": "real-original",
    "single": "real-original",
    "fixed": "real-original",
    "fixed-opponent": "real-original",
    "fixed_opponent": "real-original",
    "high-intensity": "high-intensity",
    "high_intensity": "high-intensity",
    "smart": "high-intensity",
    "practice": "high-intensity",
    "real": "real-original",
    "real-opponent": "real-original",
    "real_opponent": "real-original",
    "real-original": "real-original",
    "real_original": "real-original",
    "challenge": "real-original",
}
EXPORT_MARKET_REPORT_SCRIPT = ROOT_DIR / "skills" / "image-report-html-table" / "scripts" / "export_market_report_tables.py"
RECONSTRUCT_DECISIONS_SCRIPT = ROOT_DIR / "obos" / "reconstruct_exschool_decisions.py"
REPORT_ROUND_MAP = {
    "report4_market_reports.xlsx": "r1",
    "report4_market_reports_fixed.xlsx": "r1",
    "report3_market_reports.xlsx": "r2",
    "report3_market_reports_fixed.xlsx": "r2",
    "report2_market_reports.xlsx": "r3",
    "report2_market_reports_fixed.xlsx": "r3",
    "report1_market_reports.xlsx": "r4",
    "report1_market_reports_fixed.xlsx": "r4",
}
ROUND_WORKBOOK_MAP = {
    "r1": "round_1_team13.xlsx",
    "r2": "round_2_team13.xlsx",
    "r3": "round_3_team13.xlsx",
    "r4": "round_4_team13.xlsx",
}

if str(OBOS_DIR) not in sys.path:
    sys.path.insert(0, str(OBOS_DIR))

from analyze_team24_competitiveness import parse_numeric, round_sort_key  # type: ignore  # noqa: E402


def normalize_fixed_decision_mode(mode: str | None) -> str:
    raw = str(mode or "").strip().lower()
    return FIXED_DECISION_MODE_ALIASES.get(raw, "real-original")


def _parse_signed_int(value: Any) -> int:
    if pd.isna(value):
        return 0
    text = str(value).strip()
    if not text:
        return 0
    sign = -1 if text.startswith("-") else 1
    digits = "".join(ch for ch in text if ch.isdigit())
    return sign * int(digits or "0")


def _available_market_report_files(base_dir: Path) -> list[Path]:
    seen: dict[str, Path] = {}
    for path in sorted(base_dir.glob("report*_market_reports.xlsx")) + sorted(base_dir.glob("report*_market_reports_fixed.xlsx")):
        seen[path.name] = path
    return list(seen.values())


def _team_ids_from_frame(df: pd.DataFrame) -> list[str]:
    if "team" not in df.columns:
        return []
    team_ids = [str(value).strip() for value in df["team"].dropna().tolist()]
    return sorted(set(team_ids), key=lambda team_id: (not team_id.isdigit(), int(team_id) if team_id.isdigit() else team_id))


def _missing_team_ids(team_ids: list[str]) -> list[str]:
    numeric_team_ids = sorted({int(team_id) for team_id in team_ids if team_id.isdigit()})
    if len(numeric_team_ids) < 10 or not numeric_team_ids or numeric_team_ids[0] != 1:
        return []
    present = set(numeric_team_ids)
    return [str(team_id) for team_id in range(1, numeric_team_ids[-1] + 1) if team_id not in present]


def _team_roster_details(df: pd.DataFrame) -> tuple[list[str], list[str]]:
    team_ids = _team_ids_from_frame(df)
    return team_ids, _missing_team_ids(team_ids)


def _validate_loaded_frame(df: pd.DataFrame, *, artifact_label: str, artifact_path: Path) -> pd.DataFrame:
    missing_columns = [column for column in ("team", "round_id") if column not in df.columns]
    if missing_columns:
        raise ValueError(f"{artifact_label} at {artifact_path} missing required columns: {', '.join(missing_columns)}")
    if df.empty:
        raise ValueError(f"{artifact_label} at {artifact_path} is empty")
    validated = df.copy()
    validated["team"] = validated["team"].astype(str)
    validated["round_id"] = validated["round_id"].astype(str)
    return validated


def _log_fixed_decision_provenance(mode: str | None, *, source_dir: Path, df: pd.DataFrame) -> None:
    team_ids, missing_team_ids = _team_roster_details(df)
    LOGGER.info(
        "Loaded %s fixed decisions from source_dir=%s roster_size=%d missing_team_ids=%s",
        normalize_fixed_decision_mode(mode),
        source_dir,
        len(team_ids),
        ",".join(missing_team_ids) if missing_team_ids else "none",
    )


def _real_original_summary_missing_error(summary_path: Path, df: pd.DataFrame, *, source_dir: Path) -> FileNotFoundError:
    team_ids, missing_team_ids = _team_roster_details(df)
    missing = ",".join(missing_team_ids) if missing_team_ids else "none"
    return FileNotFoundError(
        f"Missing real-original round summary workbook at {summary_path} "
        f"(source_dir={source_dir}, roster_size={len(team_ids)}, missing_team_ids={missing})"
    )


def ensure_structured_market_reports() -> Path | None:
    if _available_market_report_files(EXSCHOOL_STRUCTURED_DIR):
        return EXSCHOOL_STRUCTURED_DIR
    if EXPORT_MARKET_REPORT_SCRIPT.exists():
        subprocess.run([sys.executable, str(EXPORT_MARKET_REPORT_SCRIPT), "--preset", "exschool"], check=True, cwd=str(ROOT_DIR))
    if _available_market_report_files(EXSCHOOL_STRUCTURED_DIR):
        return EXSCHOOL_STRUCTURED_DIR
    return None


def resolve_market_report_base_dir(base_dir: Path = EXSCHOOL_DIR) -> Path:
    env_override = os.environ.get("EXSCHOOL_MARKET_REPORT_DIR")
    if env_override:
        override_dir = Path(env_override)
        if _available_market_report_files(override_dir):
            return override_dir
    if _available_market_report_files(base_dir):
        return base_dir
    if _available_market_report_files(EXSCHOOL_STRUCTURED_DIR):
        return EXSCHOOL_STRUCTURED_DIR
    structured_dir = ensure_structured_market_reports()
    if structured_dir is not None:
        return structured_dir
    return base_dir


def ensure_fixed_decision_workbook() -> Path | None:
    if FIXED_DECISIONS_XLSX.exists():
        return FIXED_DECISIONS_XLSX
    if os.environ.get("EXSCHOOL_SKIP_FIXED_DECISION_BOOTSTRAP") == "1":
        return None
    structured_dir = ensure_structured_market_reports()
    if structured_dir is not None and RECONSTRUCT_DECISIONS_SCRIPT.exists():
        env = {**os.environ, "EXSCHOOL_MARKET_REPORT_DIR": str(structured_dir)}
        subprocess.run([sys.executable, str(RECONSTRUCT_DECISIONS_SCRIPT)], check=True, cwd=str(ROOT_DIR), env=env)
    if FIXED_DECISIONS_XLSX.exists():
        return FIXED_DECISIONS_XLSX
    return None


def ensure_smart_fixed_decision_workbook() -> Path | None:
    if SMART_FIXED_DECISIONS_XLSX.exists():
        return SMART_FIXED_DECISIONS_XLSX
    if os.environ.get("EXSCHOOL_SKIP_SMART_FIXED_OPPONENT_BOOTSTRAP") == "1":
        return None
    if GENERATE_SMART_FIXED_OPPONENTS_SCRIPT.exists():
        env = {
            **os.environ,
            "EXSCHOOL_SKIP_SMART_FIXED_OPPONENT_BOOTSTRAP": "1",
        }
        subprocess.run(
            [sys.executable, str(GENERATE_SMART_FIXED_OPPONENTS_SCRIPT)],
            check=True,
            cwd=str(ROOT_DIR),
            env=env,
        )
    if SMART_FIXED_DECISIONS_XLSX.exists():
        return SMART_FIXED_DECISIONS_XLSX
    return None


def fixed_decision_workbook_for_mode(mode: str | None) -> Path | None:
    normalized_mode = normalize_fixed_decision_mode(mode)
    if normalized_mode == "real-original":
        if REAL_ORIGINAL_FIXED_DECISIONS_XLSX.exists():
            return REAL_ORIGINAL_FIXED_DECISIONS_XLSX
        return None
    smart_workbook = ensure_smart_fixed_decision_workbook()
    if smart_workbook is not None:
        return smart_workbook
    if SMART_FIXED_DECISIONS_XLSX.exists():
        return SMART_FIXED_DECISIONS_XLSX
    return ensure_fixed_decision_workbook()


def describe_fixed_decision_source(mode: str | None = None) -> dict[str, Any]:
    normalized_mode = normalize_fixed_decision_mode(mode)
    expected_team_count = 23 if normalized_mode == "real-original" else 0
    workbook = fixed_decision_workbook_for_mode(normalized_mode)
    source_path = workbook or (ROOT_DIR / "outputs" / "exschool_inferred_decisions")

    if workbook is not None and workbook.exists():
        df = pd.read_excel(workbook)
    elif normalized_mode == "real-original":
        df = assemble_real_original_fixed_decisions_frame()
    else:
        df = pd.DataFrame()

    team_ids = _team_ids_from_frame(df) if not df.empty else []
    missing_team_ids = _missing_team_ids(team_ids) if team_ids else []
    observed_team_count = len(team_ids)
    coverage_complete = expected_team_count > 0 and observed_team_count >= expected_team_count and not missing_team_ids
    coverage_ratio = f"{observed_team_count}/{expected_team_count}" if expected_team_count > 0 else str(observed_team_count)

    return {
        "mode": normalized_mode,
        "source_path": str(source_path),
        "expected_team_count": expected_team_count,
        "observed_team_count": observed_team_count,
        "coverage_ratio": coverage_ratio,
        "coverage_complete": coverage_complete,
        "missing_team_ids": missing_team_ids,
    }


def _real_original_team_workbooks(base_dir: Path | None = None) -> list[Path]:
    resolved_base = base_dir or (ROOT_DIR / "outputs" / "exschool_inferred_decisions")
    return sorted(resolved_base.glob("C*_纯决策数值.xlsx"))


def assemble_real_original_fixed_decisions_frame(base_dir: Path | None = None) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for path in _real_original_team_workbooks(base_dir):
        df = pd.read_excel(path)
        if df.empty:
            continue
        frames.append(df)
    if not frames:
        return pd.DataFrame()
    merged = pd.concat(frames, ignore_index=True)
    if "team" in merged.columns:
        merged["team"] = merged["team"].astype(str)
    if "round_id" in merged.columns:
        merged["round_id"] = merged["round_id"].astype(str)
    return merged


def parse_market_report_workbooks(base_dir: Path = EXSCHOOL_DIR) -> pd.DataFrame:
    base_dir = resolve_market_report_base_dir(base_dir)
    rows: list[dict[str, Any]] = []
    base_paths = _available_market_report_files(base_dir)
    chosen_paths = []
    for path in base_paths:
        if path.name.endswith("_fixed.xlsx"):
            chosen_paths.append(path)
            continue
        fixed = path.with_name(path.stem + "_fixed" + path.suffix)
        chosen_paths.append(fixed if fixed.exists() else path)

    for path in chosen_paths:
        round_name = REPORT_ROUND_MAP[path.name]
        xl = pd.ExcelFile(path)
        for market in xl.sheet_names:
            df = pd.read_excel(path, sheet_name=market, header=None)
            summary_header_idx = None
            team_header_idx = None
            for i in range(len(df)):
                first = str(df.iloc[i, 0]).strip() if pd.notna(df.iloc[i, 0]) else ""
                if first == "Population":
                    summary_header_idx = i
                if first == "Team":
                    team_header_idx = i
                    break

            if summary_header_idx is None or team_header_idx is None:
                continue

            summary_row = summary_header_idx + 1
            market_size = float(str(df.iloc[summary_row, 2]).replace(",", ""))
            total_sales_volume = float(str(df.iloc[summary_row, 3]).replace(",", ""))
            avg_price = float(str(df.iloc[summary_row, 4]).replace("¥", "").replace(",", ""))
            population = parse_numeric(df.iloc[summary_row, 0])
            penetration = parse_numeric(df.iloc[summary_row, 1], percent=True)

            row_idx = team_header_idx + 1
            while row_idx < len(df):
                team = df.iloc[row_idx, 0]
                if pd.isna(team) or not str(team).strip().isdigit():
                    break
                team = str(team).strip()
                management = float(str(df.iloc[row_idx, 1]).replace(",", ""))
                agents = float(df.iloc[row_idx, 2])
                marketing = float(str(df.iloc[row_idx, 3]).replace("¥", "").replace(",", ""))
                quality = float(str(df.iloc[row_idx, 4]).replace(",", ""))
                price = float(str(df.iloc[row_idx, 5]).replace("¥", "").replace(",", ""))
                sales_volume = float(str(df.iloc[row_idx, 6]).replace(",", ""))
                market_share = float(str(df.iloc[row_idx, 7]).replace("%", "").replace(",", "")) / 100.0
                rows.append(
                    {
                        "round": round_name,
                        "market": market,
                        "team": team,
                        "management_index": management,
                        "agents": agents,
                        "marketing_investment": marketing,
                        "quality_index": quality,
                        "price": price,
                        "sales_volume": sales_volume,
                        "market_share": market_share,
                        "market_size": market_size,
                        "total_sales_volume": total_sales_volume,
                        "avg_price": avg_price,
                        "population": population,
                        "penetration": penetration,
                        "market_index": (1 + 0.1 * agents) * marketing,
                        "source_file": path.name,
                    }
                )
                row_idx += 1
    return pd.DataFrame(rows)


def parse_team13_actual(base_dir: Path = EXSCHOOL_DIR, team_id: str = "13") -> pd.DataFrame:
    rows = []
    for path in sorted(base_dir.glob("round_*_team13.xlsx")):
        round_num = int(re.search(r"(\d+)", path.stem).group(1))
        round_name = f"r{round_num}"
        df = pd.read_excel(path, sheet_name="Sales Result")
        for _, row in df.iterrows():
            market = str(row["Market"]).strip()
            cpi = str(row["Competitive Power"]).strip()
            cpi_val = float(cpi.replace("%", "").replace(",", "")) / 100.0 if cpi not in {"", "nan"} else np.nan
            rows.append(
                {
                    "round": round_name,
                    "market": market,
                    "team": team_id,
                    "actual_real_cpi": cpi_val,
                }
            )
    return pd.DataFrame(rows)


def attach_lags(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["round_order"] = out["round"].map(round_sort_key)
    out = out.sort_values(["team", "market", "round_order"]).reset_index(drop=True)

    team_state = (
        out.groupby(["team", "round", "round_order"], as_index=False)
        .agg(
            team_management_index=("management_index", "mean"),
            team_quality_index=("quality_index", "mean"),
        )
        .sort_values(["team", "round_order"])
        .reset_index(drop=True)
    )
    team_state["prev_team_management_index"] = team_state.groupby("team")["team_management_index"].shift(1)
    team_state["prev_team_quality_index"] = team_state.groupby("team")["team_quality_index"].shift(1)
    out = out.merge(
        team_state[["team", "round", "prev_team_management_index", "prev_team_quality_index"]],
        on=["team", "round"],
        how="left",
    )
    return out


def parse_key_data(base_dir: Path = EXSCHOOL_DIR) -> dict[str, Any]:
    path = base_dir / "asdan_key_data_sheet.xlsx"
    if not path.exists():
        fallback_reports = parse_market_report_workbooks(base_dir)
        if fallback_reports.empty:
            raise FileNotFoundError(f"Missing key data sheet: {path}")
        markets: dict[str, dict[str, float]] = {}
        defaults = {
            "Shanghai": {"initial_max_loan": 5_000_000.0, "interest_rate": 0.031, "initial_worker_salary": 3300.0, "initial_engineer_salary": 6400.0, "component_material_unit_cost": 300.0, "product_material_unit_cost": 650.0, "component_storage_unit_cost": 28.0, "product_storage_unit_cost": 110.0},
            "Chengdu": {"initial_max_loan": 3_500_000.0, "interest_rate": 0.036, "initial_worker_salary": 2900.0, "initial_engineer_salary": 5600.0, "component_material_unit_cost": 258.0, "product_material_unit_cost": 630.0, "component_storage_unit_cost": 24.0, "product_storage_unit_cost": 100.0},
            "Wuhan": {"initial_max_loan": 4_200_000.0, "interest_rate": 0.033, "initial_worker_salary": 2600.0, "initial_engineer_salary": 5000.0, "component_material_unit_cost": 215.0, "product_material_unit_cost": 600.0, "component_storage_unit_cost": 22.0, "product_storage_unit_cost": 88.0},
            "Wuxi": {"initial_max_loan": 5_000_000.0, "interest_rate": 0.031, "initial_worker_salary": 2400.0, "initial_engineer_salary": 4600.0, "component_material_unit_cost": 188.0, "product_material_unit_cost": 540.0, "component_storage_unit_cost": 18.0, "product_storage_unit_cost": 78.0},
            "Ningbo": {"initial_max_loan": 5_000_000.0, "interest_rate": 0.031, "initial_worker_salary": 2400.0, "initial_engineer_salary": 4600.0, "component_material_unit_cost": 188.0, "product_material_unit_cost": 540.0, "component_storage_unit_cost": 18.0, "product_storage_unit_cost": 78.0},
        }
        for market, group in fallback_reports.groupby("market"):
            first = group.sort_values("round", key=lambda s: s.map(round_sort_key)).iloc[0]
            if market not in defaults:
                continue
            markets[market] = {
                **defaults[market],
                "population": float(first["population"]),
                "initial_penetration": float(first["penetration"]),
                "initial_avg_price": float(first["avg_price"]),
            }
        return {
            "initial_cash": 15_000_000.0,
            "markets": markets,
            "equations_text": "Fallback key data generated from market reports and baked-in exschool defaults.",
            "equations_rows": [],
        }
    key_df = pd.read_excel(path, sheet_name="Key Data", header=None)
    eq_df = pd.read_excel(path, sheet_name="Equations", header=None)

    flattened = " ".join(str(v) for v in key_df.fillna("").to_numpy().ravel())
    initial_cash_match = re.search(r"Initial Cash:\s*¥([\d,]+)", flattened)
    initial_cash = float(initial_cash_match.group(1).replace(",", "")) if initial_cash_match else 15_000_000.0

    markets: dict[str, dict[str, float]] = {}
    known_markets = {"Shanghai", "Chengdu", "Wuhan", "Wuxi", "Ningbo"}
    for _, row in key_df.iterrows():
        market = str(row.iloc[0]).strip()
        if market in known_markets:
            markets[market] = {
                "initial_max_loan": float(row.iloc[1]),
                "interest_rate": float(row.iloc[2]),
                "initial_worker_salary": float(row.iloc[3]),
                "initial_engineer_salary": float(row.iloc[4]),
                "component_material_unit_cost": float(row.iloc[5]),
                "product_material_unit_cost": float(row.iloc[6]),
                "component_storage_unit_cost": float(row.iloc[7]),
                "product_storage_unit_cost": float(row.iloc[8]),
                "population": float(row.iloc[9]),
                "initial_penetration": float(row.iloc[10]),
                "initial_avg_price": float(row.iloc[11]),
            }

    equations_rows: list[dict[str, str]] = []
    for _, row in eq_df.iterrows():
        item = "" if pd.isna(row.iloc[0]) else str(row.iloc[0]).strip()
        formula = "" if len(row) < 2 or pd.isna(row.iloc[1]) else str(row.iloc[1]).strip()
        if not item or item in {"Equations & Ranges & Prices", "Item"}:
            continue
        if item == "Description / Formula":
            continue
        equations_rows.append({"item": item, "formula": formula})

    equations_text = "\n".join(" ".join(str(v) for v in row if pd.notna(v)) for row in eq_df.to_numpy().tolist())
    return {
        "initial_cash": initial_cash,
        "markets": markets,
        "equations_text": equations_text,
        "equations_rows": equations_rows,
    }


def parse_round_workbook(round_id: str, base_dir: Path = EXSCHOOL_DIR) -> dict[str, Any]:
    path = base_dir / ROUND_WORKBOOK_MAP[round_id]

    finance_df = pd.read_excel(path, sheet_name="Finance")
    hr_df = pd.read_excel(path, sheet_name="HR Summary")
    prod_df = pd.read_excel(path, sheet_name="Production")
    agents_df = pd.read_excel(path, sheet_name="Sales Agents")
    sales_df = pd.read_excel(path, sheet_name="Sales Result")
    summary_df = pd.read_excel(path, sheet_name="Summary", header=None)

    finance_rows = {}
    for _, row in finance_df.iterrows():
        item = str(row.get("Items", "")).strip()
        if item:
            finance_rows[item] = {
                "cash_flow": parse_numeric(row.get("Cash Flow")),
                "cash": parse_numeric(row.get("Cash")),
                "debt_change": parse_numeric(row.get("Debt Change")),
                "debt": parse_numeric(row.get("Debt")),
            }

    production_rows = {}
    for _, row in prod_df.iterrows():
        metric = str(row.get("Metric", "")).strip()
        if metric:
            production_rows[metric] = parse_numeric(row.get("Value"))

    worker_rows = hr_df[hr_df["Category"].astype(str).str.contains("Worker", case=False, na=False)]
    engineer_rows = hr_df[hr_df["Category"].astype(str).str.contains("Engineer", case=False, na=False)]
    workers_actual = int(worker_rows["Working"].map(parse_numeric).fillna(0).sum())
    engineers_actual = int(engineer_rows["Working"].map(parse_numeric).fillna(0).sum())
    worker_salary_actual = float(worker_rows["Salary"].map(parse_numeric).replace(0, np.nan).mean())
    engineer_salary_actual = float(engineer_rows["Salary"].map(parse_numeric).replace(0, np.nan).mean())
    worker_avg_salary_actual = float(worker_rows["Avg"].map(parse_numeric).replace(0, np.nan).mean())
    engineer_avg_salary_actual = float(engineer_rows["Avg"].map(parse_numeric).replace(0, np.nan).mean())
    if not np.isfinite(worker_avg_salary_actual):
        worker_avg_salary_actual = worker_salary_actual
    if not np.isfinite(engineer_avg_salary_actual):
        engineer_avg_salary_actual = engineer_salary_actual

    round_begins = finance_rows.get("Round begins", {})
    bank_loan = finance_rows.get("Bank loan", {})
    debt_interest = finance_rows.get("Debt interest", {})
    market_report_cost = abs(finance_rows.get("Market report cost", {}).get("cash_flow") or 0.0)
    research_investment = abs(finance_rows.get("Research investment", {}).get("cash_flow") or 0.0)
    management_investment = abs(finance_rows.get("Management investment", {}).get("cash_flow") or 0.0)
    quality_investment = abs(finance_rows.get("Quality investment", {}).get("cash_flow") or 0.0)
    components_storage_cost = abs(finance_rows.get("Components storage cost", {}).get("cash_flow") or 0.0)
    products_storage_cost = abs(finance_rows.get("Products storage cost", {}).get("cash_flow") or 0.0)

    starting_cash = float(round_begins.get("cash") or 0.0)
    starting_debt = float(round_begins.get("debt") or 0.0)
    loan_limit = abs(bank_loan.get("cash_flow") or 0.0)
    actual_loan_delta = float(bank_loan.get("cash_flow") or 0.0)

    debt_base = starting_debt + max(actual_loan_delta, 0.0)
    interest_rate = abs(debt_interest.get("debt_change") or 0.0) / debt_base if debt_base > 0 else 0.03

    products_produced = int(production_rows.get("Products Produced") or production_rows.get("Products Plan") or 0)
    components_produced = int(production_rows.get("Components Produced") or production_rows.get("Components Plan") or products_produced * 7)
    key_data = parse_key_data(base_dir)
    component_storage_unit = key_data["markets"].get(agents_df["Market"].iloc[0], {}).get("component_storage_unit_cost", 24.0)
    product_storage_unit = key_data["markets"].get(agents_df["Market"].iloc[0], {}).get("product_storage_unit_cost", 100.0)
    component_storage_factor = components_storage_cost / max(components_produced * component_storage_unit, 1.0)
    product_storage_factor = products_storage_cost / max(products_produced * product_storage_unit, 1.0)

    summary_title = str(summary_df.iloc[0, 0]).strip()
    summary_metrics = {}
    for idx in range(len(summary_df)):
        key = str(summary_df.iloc[idx, 0]).strip()
        if key in {"Total Assets", "Debt", "Net Assets", "Rank", "Sales Revenue", "Cost", "Net Profit"}:
            summary_metrics[key] = summary_df.iloc[idx, 1]

    market_defaults = {}
    for _, row in agents_df.iterrows():
        market = str(row["Market"]).strip()
        sales_row = sales_df[sales_df["Market"] == market]
        actual_price = float(parse_numeric(sales_row["Price"].iloc[0]) or 0.0) if not sales_row.empty else 0.0
        actual_sales_volume = float(parse_numeric(sales_row["Sales Volume"].iloc[0]) or 0.0) if not sales_row.empty else 0.0
        actual_market_share = float(parse_numeric(sales_row["Market Share"].iloc[0], percent=True) or 0.0) if not sales_row.empty else 0.0
        actual_competitive_power = (
            float(parse_numeric(sales_row["Competitive Power"].iloc[0], percent=True) or 0.0) if not sales_row.empty else 0.0
        )
        market_defaults[market] = {
            "previous_agents": int(parse_numeric(row["Previous"]) or 0),
            "actual_change": _parse_signed_int(row["Change"]),
            "actual_after": int(parse_numeric(row["After"]) or 0),
            "actual_marketing_investment": float(parse_numeric(row["Marketing Investment"]) or 0.0),
            "actual_price": actual_price,
            "actual_sales_volume": actual_sales_volume,
            "actual_market_share": actual_market_share,
            "actual_competitive_power": actual_competitive_power,
        }

    accumulated_research_investment_actual = float(parse_numeric(production_rows.get("Accumulated Research Investment")) or 0.0)
    if not np.isfinite(accumulated_research_investment_actual):
        accumulated_research_investment_actual = 0.0

    return {
        "round_id": round_id,
        "title": summary_title,
        "finance_rows": finance_rows,
        "production_rows": production_rows,
        "summary_metrics": summary_metrics,
        "starting_cash": starting_cash,
        "starting_debt": starting_debt,
        "loan_limit": loan_limit,
        "actual_loan_delta": actual_loan_delta,
        "interest_rate": interest_rate if interest_rate > 0 else 0.03,
        "market_report_cost": market_report_cost,
        "research_investment": research_investment,
        "workers_actual": workers_actual,
        "engineers_actual": engineers_actual,
        "worker_salary_actual": worker_salary_actual,
        "engineer_salary_actual": engineer_salary_actual,
        "worker_avg_salary_actual": worker_avg_salary_actual,
        "engineer_avg_salary_actual": engineer_avg_salary_actual,
        "management_investment_actual": management_investment,
        "quality_investment_actual": quality_investment,
        "accumulated_research_investment_actual": accumulated_research_investment_actual,
        "products_produced_actual": products_produced,
        "components_productivity": float(production_rows.get("Components Productivity") or 24.0),
        "products_productivity": float(production_rows.get("Products Productivity") or 9.0),
        "component_material_price": float(parse_numeric(production_rows.get("Components Material Price")) or 188.0),
        "product_material_price": float(parse_numeric(production_rows.get("Products Material Price")) or 540.0),
        "component_storage_factor": component_storage_factor if component_storage_factor > 0 else 0.75,
        "product_storage_factor": product_storage_factor if product_storage_factor > 0 else 0.75,
        "market_defaults": market_defaults,
        "visible_markets": list(market_defaults.keys()),
    }


def parse_fixed_team_decisions(mode: str | None = None) -> pd.DataFrame:
    normalized_mode = normalize_fixed_decision_mode(mode)
    workbook = fixed_decision_workbook_for_mode(normalized_mode)
    if workbook is None:
        if normalized_mode == "real-original":
            df = assemble_real_original_fixed_decisions_frame()
            if not df.empty:
                df = _validate_loaded_frame(df, artifact_label="real-original fixed decision roster", artifact_path=ROOT_DIR / "outputs" / "exschool_inferred_decisions")
                _log_fixed_decision_provenance(normalized_mode, source_dir=ROOT_DIR / "outputs" / "exschool_inferred_decisions", df=df)
            return df
        return pd.DataFrame()
    df = _validate_loaded_frame(pd.read_excel(workbook), artifact_label="fixed decision workbook", artifact_path=workbook)
    _log_fixed_decision_provenance(normalized_mode, source_dir=workbook.parent, df=df)
    return df


def parse_fixed_round_summary(mode: str | None = None) -> pd.DataFrame:
    normalized_mode = normalize_fixed_decision_mode(mode)
    if normalized_mode != "real-original":
        return pd.DataFrame()
    summary_path = REAL_ORIGINAL_ROUND_SUMMARY_XLSX
    if not summary_path.exists():
        workbook = fixed_decision_workbook_for_mode(normalized_mode)
        if workbook is None:
            return pd.DataFrame()
        decisions_df = _validate_loaded_frame(pd.read_excel(workbook), artifact_label="real-original fixed decision workbook", artifact_path=workbook)
        raise _real_original_summary_missing_error(summary_path, decisions_df, source_dir=workbook.parent)
    df = _validate_loaded_frame(pd.read_excel(summary_path), artifact_label="real-original round summary workbook", artifact_path=summary_path)
    return df
