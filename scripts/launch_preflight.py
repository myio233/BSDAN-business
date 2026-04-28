#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import os
import socket
import sys
from dataclasses import dataclass
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))
VENV_PYTHON = ROOT_DIR / ".venv" / "bin" / "python"
START_SCRIPT = ROOT_DIR / "scripts" / "start_exschool_game.sh"
MULTIPLAYER_BROWSER_SCRIPT = ROOT_DIR / "scripts" / "validate_multiplayer_room_playwright.py"
LOCAL_ENV_FILES = [ROOT_DIR / ".env.local", ROOT_DIR / ".smtp.env.local"]
SMTP_KEYS = ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD")
EXSCHOOL_DIR = ROOT_DIR / "exschool"
INFERRED_DECISIONS_DIR = ROOT_DIR / "outputs" / "exschool_inferred_decisions"
REQUIRED_PRIVATE_FILES = [
    EXSCHOOL_DIR / "asdan_key_data_sheet.xlsx",
    EXSCHOOL_DIR / "round_1_team13.xlsx",
    EXSCHOOL_DIR / "round_2_team13.xlsx",
    EXSCHOOL_DIR / "round_3_team13.xlsx",
    EXSCHOOL_DIR / "round_4_team13.xlsx",
    INFERRED_DECISIONS_DIR / "all_companies_numeric_decisions_smart.xlsx",
    INFERRED_DECISIONS_DIR / "all_companies_numeric_decisions_real_original_fixed.xlsx",
    INFERRED_DECISIONS_DIR / "all_round_reconstruction_summary.xlsx",
]
MARKET_REPORT_FILES = [
    EXSCHOOL_DIR / "report1_market_reports.xlsx",
    EXSCHOOL_DIR / "report2_market_reports.xlsx",
    EXSCHOOL_DIR / "report3_market_reports.xlsx",
    EXSCHOOL_DIR / "report4_market_reports.xlsx",
]
FIXED_MARKET_REPORT_FILES = [
    EXSCHOOL_DIR / "report1_market_reports_fixed.xlsx",
    EXSCHOOL_DIR / "report2_market_reports_fixed.xlsx",
    EXSCHOOL_DIR / "report3_market_reports_fixed.xlsx",
    EXSCHOOL_DIR / "report4_market_reports_fixed.xlsx",
]


@dataclass(slots=True)
class CheckResult:
    level: str
    name: str
    detail: str


def load_local_env() -> dict[str, str]:
    env: dict[str, str] = {}
    for path in LOCAL_ENV_FILES:
        if not path.exists():
            continue
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip("'").strip('"')
            if key:
                env[key] = value
    return env


def check_python_surface() -> CheckResult:
    if not VENV_PYTHON.exists():
        return CheckResult("FAIL", "venv", f"missing virtualenv python at {VENV_PYTHON}")
    return CheckResult("PASS", "venv", f"found {VENV_PYTHON}")


def check_scripts() -> list[CheckResult]:
    results: list[CheckResult] = []
    if START_SCRIPT.exists():
        results.append(CheckResult("PASS", "start-script", f"found {START_SCRIPT.name}"))
    else:
        results.append(CheckResult("FAIL", "start-script", f"missing {START_SCRIPT}"))
    if MULTIPLAYER_BROWSER_SCRIPT.exists():
        results.append(CheckResult("PASS", "multiplayer-browser-script", f"found {MULTIPLAYER_BROWSER_SCRIPT.name}"))
    else:
        results.append(CheckResult("FAIL", "multiplayer-browser-script", f"missing {MULTIPLAYER_BROWSER_SCRIPT}"))
    return results


def check_playwright_import() -> CheckResult:
    if importlib.util.find_spec("playwright") is None:
        return CheckResult("FAIL", "playwright", "python package not importable in current interpreter")
    return CheckResult("PASS", "playwright", "python package importable")


def check_private_data_files() -> CheckResult:
    missing = [path.relative_to(ROOT_DIR).as_posix() for path in REQUIRED_PRIVATE_FILES if not path.exists()]
    market_reports_present = all(path.exists() for path in MARKET_REPORT_FILES) or all(path.exists() for path in FIXED_MARKET_REPORT_FILES)
    if not market_reports_present:
        missing.append("exschool/report[1-4]_market_reports(.xlsx or _fixed.xlsx)")
    if missing:
        return CheckResult(
            "FAIL",
            "private-data",
            "missing required ignored runtime inputs: " + ", ".join(missing),
        )
    return CheckResult("PASS", "private-data", "required ignored workbooks are present locally")


def check_simulator_runtime() -> CheckResult:
    try:
        from exschool_game.engine import get_simulator

        simulator = get_simulator("high-intensity")
        if simulator.market_df.empty:
            return CheckResult("FAIL", "simulator", "market reports loaded as an empty dataframe")
        if simulator.fixed_decisions_df.empty:
            return CheckResult("FAIL", "simulator", "fixed opponent decisions loaded as an empty dataframe")
        if len(simulator.round_contexts) != 4:
            return CheckResult("FAIL", "simulator", f"expected 4 round contexts, got {len(simulator.round_contexts)}")
        if not simulator.key_data.get("markets"):
            return CheckResult("FAIL", "simulator", "key market data is empty")
    except Exception as exc:
        return CheckResult("FAIL", "simulator", f"{type(exc).__name__}: {exc}")
    return CheckResult(
        "PASS",
        "simulator",
        f"loaded {len(simulator.market_df)} market rows, {len(simulator.fixed_decisions_df)} fixed decisions, {len(simulator.round_contexts)} rounds",
    )


def check_real_original_coverage() -> CheckResult:
    from exschool_game.data_loader import describe_fixed_decision_source

    info = describe_fixed_decision_source("real-original")
    coverage_ratio = str(info.get("coverage_ratio", "unknown"))
    if bool(info.get("coverage_complete", False)):
        return CheckResult("PASS", "real-original", f"coverage_complete ratio={coverage_ratio}")
    missing = ",".join(info.get("missing_team_ids", []) or []) or "unknown"
    return CheckResult("WARN", "real-original", f"source-limited coverage ratio={coverage_ratio}, missing_team_ids={missing}")


def check_smtp_prereq(env_from_files: dict[str, str]) -> CheckResult:
    merged = {**env_from_files, **os.environ}
    present = [key for key in SMTP_KEYS if str(merged.get(key, "")).strip()]
    if len(present) == len(SMTP_KEYS):
        host = str(merged.get("SMTP_HOST", "")).strip()
        port = int(str(merged.get("SMTP_PORT", "465")).strip() or "465")
        try:
            socket.getaddrinfo(host, port)
        except Exception as exc:
            return CheckResult("WARN", "smtp", f"configured but hostname resolution failed for {host}:{port} ({type(exc).__name__}: {exc})")
        sock = socket.socket()
        sock.settimeout(10)
        try:
            sock.connect((host, port))
        except Exception as exc:
            return CheckResult("WARN", "smtp", f"configured but connection failed for {host}:{port} ({type(exc).__name__}: {exc})")
        finally:
            sock.close()
        return CheckResult("PASS", "smtp", f"configured and reachable for self-service signup via {host}:{port}")
    missing = [key for key in SMTP_KEYS if key not in present]
    return CheckResult(
        "WARN",
        "smtp",
        "self-service signup requires SMTP config; missing " + ", ".join(missing),
    )


def render_results(results: list[CheckResult]) -> str:
    lines = ["Launch preflight results:"]
    for item in results:
        lines.append(f"- [{item.level}] {item.name}: {item.detail}")
    return "\n".join(lines)


def main() -> int:
    env_from_files = load_local_env()
    results = [
        check_python_surface(),
        *check_scripts(),
        check_playwright_import(),
        check_private_data_files(),
        check_simulator_runtime(),
        check_real_original_coverage(),
        check_smtp_prereq(env_from_files),
    ]
    print(render_results(results))
    return 1 if any(item.level == "FAIL" for item in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
