#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import os
import socket
import sys
from dataclasses import dataclass
from pathlib import Path

import pandas as pd


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))
VENV_PYTHON = ROOT_DIR / ".venv" / "bin" / "python"
START_SCRIPT = ROOT_DIR / "scripts" / "start_exschool_game.sh"
LAUNCH_SCRIPT = ROOT_DIR / "scripts" / "validate_launch_readiness_playwright.sh"
REVIEW_SCRIPT = ROOT_DIR / "scripts" / "review_exschool_browser_experience.sh"
MULTIPLAYER_BROWSER_SCRIPT = ROOT_DIR / "scripts" / "validate_multiplayer_room_playwright.py"
BASELINE_METRICS = ROOT_DIR / "generated_reports" / "model_pipeline_current_baseline" / "metrics.csv"
LOCAL_ENV_FILES = [ROOT_DIR / ".env.local", ROOT_DIR / ".smtp.env.local"]
SMTP_KEYS = ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD")


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
    if LAUNCH_SCRIPT.exists():
        results.append(CheckResult("PASS", "launch-script", f"found {LAUNCH_SCRIPT.name}"))
    else:
        results.append(CheckResult("FAIL", "launch-script", f"missing {LAUNCH_SCRIPT}"))
    if REVIEW_SCRIPT.exists():
        results.append(CheckResult("PASS", "review-script", f"found {REVIEW_SCRIPT.name}"))
    else:
        results.append(CheckResult("FAIL", "review-script", f"missing {REVIEW_SCRIPT}"))
    if MULTIPLAYER_BROWSER_SCRIPT.exists():
        results.append(CheckResult("PASS", "multiplayer-browser-script", f"found {MULTIPLAYER_BROWSER_SCRIPT.name}"))
    else:
        results.append(CheckResult("FAIL", "multiplayer-browser-script", f"missing {MULTIPLAYER_BROWSER_SCRIPT}"))
    return results


def check_playwright_import() -> CheckResult:
    if importlib.util.find_spec("playwright") is None:
        return CheckResult("FAIL", "playwright", "python package not importable in current interpreter")
    return CheckResult("PASS", "playwright", "python package importable")


def check_canonical_baseline() -> CheckResult:
    if not BASELINE_METRICS.exists():
        return CheckResult("FAIL", "baseline", f"missing {BASELINE_METRICS}")
    df = pd.read_csv(BASELINE_METRICS)
    rows = df[
        (df["variant"] == "current_runtime_default")
        & (df["stage"] == "end_to_end_final_share")
        & (df["dataset"].isin(["EXSCHOOL", "OBOS"]))
    ]
    if len(rows) != 2:
        return CheckResult("FAIL", "baseline", "current_runtime_default end_to_end rows are incomplete")
    exschool = float(rows.loc[rows["dataset"] == "EXSCHOOL", "r2"].iloc[0])
    obos = float(rows.loc[rows["dataset"] == "OBOS", "r2"].iloc[0])
    level = "PASS" if exschool > 0.95 and obos > 0.95 else "FAIL"
    return CheckResult(level, "baseline", f"Exschool={exschool:.6f}, OBOS={obos:.6f}")


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
        check_canonical_baseline(),
        check_real_original_coverage(),
        check_smtp_prereq(env_from_files),
    ]
    print(render_results(results))
    return 1 if any(item.level == "FAIL" for item in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
