# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

ASDAN business competition analysis toolkit. The repository has two main modes of work:

1. `exschool_game/` is a FastAPI app that runs a single-round simulator for the Exschool competition.
2. `obos/`, `WYEF/`, `WYEF_results/`, `training1/`, and `结果/` contain data-processing, OCR, evaluation, and model-fitting scripts for historical competition datasets.

This is primarily a script-first research repository, not a packaged Python library. There is no repo-level `pyproject.toml`, `requirements.txt`, or lint/test configuration at the root, so verify commands from the specific script or subdirectory you are editing.

## Common Commands

### Run the Exschool simulator

From the repository root:

```bash
uvicorn exschool_game.app:app --reload --app-dir . --port 8010
```

Alternative entrypoint:

```bash
python -m exschool_game.app
```

Then open `http://127.0.0.1:8010`.

### Run a standalone analysis script

Most analysis/modeling files in `obos/` and `结果/` are meant to be run directly:

```bash
python obos/<script_name>.py
python 结果/<script_name>.py
```

Example:

```bash
python obos/fit_obos_exschool_joint_model.py
```

### Run tests

The only discovered automated test file is:

```bash
python -m pytest obos/test_team24_threshold_semidynamic.py
```

Run a single test node with:

```bash
python -m pytest obos/test_team24_threshold_semidynamic.py -k <pattern>
```

## High-Level Architecture

### 1. Exschool simulator web app

The web app is split between a thin FastAPI layer and a heavy simulation engine:

- `exschool_game/app.py` exposes the HTTP interface.
  - `GET /` renders the decision form.
  - `GET /api/rounds/{round_id}/defaults` returns the default payload for a round.
  - `POST /simulate` parses form data, runs the simulator, and renders the report.
- `exschool_game/engine.py` contains almost all business logic.
  - Loads Excel workbooks from `exschool/`.
  - Imports feature-engineering helpers from `obos/` by inserting `obos/` onto `sys.path`.
  - Trains Gradient Boosting models in-process when the simulator is first constructed.
  - Converts a decision payload into projected market share, theoretical CPI, sales, finance, and report tables.
- `exschool_game/templates/` contains the UI/report rendering.
  - `index.html` is a form-driven single-round decision editor with JSON import/export in the browser.
  - `report.html` renders the simulated finance, HR, production, sales, and peer market comparison tables.

Important implication: changes to `obos/` feature engineering can affect simulator behavior even if the FastAPI app code is untouched.

### 2. Data-driven simulation pipeline

The simulator is tightly coupled to historical Excel data under `exschool/`:

- `report*_market_reports.xlsx` and optional `*_fixed.xlsx` variants provide per-market, per-team market report data.
- `round_*_team13.xlsx` provides the Team 13 round workbooks used for defaults and finance structure.
- `asdan_key_data_sheet.xlsx` provides market constants and equation text.

Inside `engine.py`, the flow is:

1. Parse historical market reports into a normalized dataframe.
2. Parse Team 13 actual round outcomes.
3. Add lagged/team-history features.
4. Reuse feature builders from `obos/fit_weighted_theoretical_cpi_model.py`.
5. Train share/CPI regressors.
6. For a user decision, keep other teams fixed at historical values and replace Team 13 inputs only.
7. Cap demand by planned production, then derive finance/report outputs from the historical workbook structure.

### 3. Research and model-fitting scripts

The `obos/` directory is the modeling workbench. Files are mostly standalone scripts rather than a cohesive package.

Common script roles:

- `analyze_*` scripts parse competition data and build baseline analytical tables.
- `fit_*` scripts train or search for CPI / market-share model variants.
- `evaluate_*` scripts score fitted models against held-out or cross-dataset scenarios.
- `check_*` scripts are spot-check/debug utilities for specific rounds or assumptions.
- `process_*` / `azure_ocr*.py` scripts turn screenshots or OCR output into structured Excel inputs.

Several scripts assume absolute local paths such as `/mnt/c/Users/david/documents/ASDAN/...` rather than repository-relative paths. Before editing one of these scripts, check whether it is intended for the current machine only or should be generalized.

### 4. OCR and workbook-generation workflow

There are two OCR pipelines in the repo:

- `obos/azure_ocr.py` and related scripts use Azure Document Intelligence and require credentials from environment variables such as `DOCUMENTINTELLIGENCE_ENDPOINT` and `DOCUMENTINTELLIGENCE_API_KEY`.
- `obos/azure_ocr.py` also writes Excel outputs that downstream market-analysis scripts consume.
- `obos/process_sales_azure.py`, `obos/process_r7.py`, `obos/process_r7_split.py`, and files in `结果/` continue the conversion pipeline from screenshots to structured workbooks.
- `obos/create_excel.py` and similar scripts generate consolidated Excel artifacts used for later fitting/evaluation.

When debugging data issues, inspect whether the problem originates in OCR extraction, workbook normalization, or model fitting before changing prediction logic.

## Repository Structure

- `exschool_game/` — FastAPI simulator app and HTML templates.
- `exschool/` — historical Exschool Excel inputs used directly by the simulator.
- `obos/` — main analysis/model-fitting/OCR script collection.
- `WYEF/` and `WYEF_results/` — historical WYEF round data and derived outputs.
- `training1/`, `southtraining/`, `结果/` — additional datasets and experiments.

## Working Notes

- This repo mixes application code and one-off research scripts; do not assume every Python file is reusable library code.
- The simulator currently models a single round only; there is no persistent multi-round campaign state.
- The simulator treats non-Team-13 competitors as fixed historical opponents for the selected round.
- If changing simulator behavior, read both `exschool_game/engine.py` and the imported feature code in `obos/fit_weighted_theoretical_cpi_model.py`.
- If changing parsing or normalization logic, also inspect the corresponding workbook/OCR scripts because many downstream files depend on shared column conventions rather than explicit schemas.
