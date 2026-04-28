# BSDAN Business Simulator

BSDAN Business Simulator is a FastAPI web app for running a multi-round business simulation inspired by classroom competition workflows. Players make operating decisions for production, hiring, salary, financing, marketing, pricing, quality, management, and research. The app settles those decisions against shared market rules and produces browser reports for single-player and multiplayer sessions.

This public repository contains the application code, modeling code, tests, deployment examples, and developer documentation. Private source workbooks, screenshots, generated reports, runtime storage, and credentials are intentionally excluded.

## What Is Included

- `exschool_game/` - FastAPI app, templates, static assets, simulation engine, auth, multiplayer room state, finance, inventory, workforce, research, market allocation, and report rendering.
- `scripts/` - operational scripts for launching the app, validating browser flows, generating fixed opponents, and rebuilding derived workbooks when private inputs are available locally.
- `obos/` - analysis and modeling utilities used to fit or inspect market-allocation behavior from locally supplied data.
- `tests/` - pytest coverage for the engine, finance, inventory, modeling, report payloads, auth-related flows, and multiplayer behavior.
- `deploy/` - generic systemd, nginx, and environment examples. Replace example values before using them on a server.
- `docs/` - design notes, run logs, model notes, planning notes, and operational lessons.
- `skills/` - local workflow helpers used during data extraction and governance work.

## What Is Not Included

The public GitHub repo deliberately excludes these files, but the local checkout may keep them as ignored private inputs for development and testing:

- `.env` files and real service credentials.
- `storage/` runtime state, session secrets, user game data, and report caches.
- Raw competition workbooks, OCR screenshots, and private images.
- Generated output directories such as `outputs/` and `generated_reports/`.
- Browser validation screenshots and Playwright MCP traces.
- Virtual environments and local cache folders.

The `.gitignore` blocks these classes of files so they are not accidentally recommitted.

## Requirements

- Python 3.12 or compatible Python 3.x runtime.
- `pip` and `venv`.
- Optional: Playwright browser dependencies for browser validation scripts.
- Optional: Tesseract and Azure Document Intelligence credentials for OCR/data extraction scripts. These are not needed to run the core app.

Install dependencies:

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements-exschool-game.txt
```

## Run Locally

Start the web app:

```bash
. .venv/bin/activate
uvicorn exschool_game.app:app --reload --host 127.0.0.1 --port 8010
```

Then open:

```text
http://127.0.0.1:8010
```

The production-style helper script expects `.venv/bin/python` to exist:

```bash
EXSCHOOL_HOST=127.0.0.1 EXSCHOOL_PORT=8010 ./scripts/start_exschool_game.sh
```

## Configuration

Most runtime configuration is via environment variables. Start from:

```text
deploy/exschool-game.env.example
```

Important variables:

- `EXSCHOOL_SESSION_SECRET` - set this to a long random value in production.
- `EXSCHOOL_AUTH_SITE_NAME` - display name/domain used by auth email copy.
- `SMTP_*` - SMTP settings for email verification codes.
- `EXSCHOOL_HOST` and `EXSCHOOL_PORT` - bind address used by the launch script.
- `EXSCHOOL_ROOT_PATH` - optional URL prefix when reverse-proxying under a path.

Do not commit real `.env` files.

## Tests

Run unit and integration tests:

```bash
. .venv/bin/activate
pytest -q
```

By default, tests that require private source workbooks under `exschool/` are skipped when those files are absent. To run the full private-data suite on a local machine that has those ignored inputs restored:

```bash
pytest -q --run-private-data
```

Run selected browser validation scripts after installing Playwright:

```bash
playwright install chromium
python scripts/validate_exschool_modes_playwright.py --base-url http://127.0.0.1:8010
python scripts/validate_multiplayer_room_playwright.py --base-url http://127.0.0.1:8010
```

Browser scripts may create screenshots and summaries under ignored output folders.

## Repository Layout

```text
.
├── deploy/                 # Generic deployment examples
├── docs/                   # Design notes, plans, run logs, model notes
├── exschool_game/          # FastAPI app and simulation package
├── obos/                   # Data-analysis and model-fitting utilities
├── scripts/                # Launch, validation, and data rebuild scripts
├── skills/                 # Local workflow helpers
├── tests/                  # Pytest suite
├── requirements-exschool-game.txt
└── README.md
```

## Data Rebuild Workflow

Some scripts can rebuild derived decision workbooks or market-report exports, but they require private local inputs that are not part of this repository. Place private inputs in the ignored local directories expected by the scripts, run the rebuild, and keep generated files out of Git unless they have been explicitly sanitized and reviewed.

Useful scripts:

- `scripts/build_real_original_fixed_decisions_workbook.py`
- `scripts/generate_smart_fixed_opponents.py`
- `skills/image-report-html-table/scripts/export_market_report_tables.py`
- `obos/reconstruct_exschool_decisions.py`

## Deployment Notes

Deployment examples are intentionally generic:

- `deploy/exschool-game.service` assumes the app lives at `/opt/exschool-game`.
- `deploy/nginx-location.example.conf` shows reverse proxy settings for serving the app under `/asdan/`.
- `deploy/exschool-game.env.example` contains placeholder environment variables only.

Before deploying, set a real session secret, configure SMTP, choose your hostname, and confirm that runtime storage is writable by the service user.

## Public-Repo Hygiene

Before pushing changes, run local privacy checks for:

- provider tokens and API credentials
- private key blocks
- machine-specific absolute paths
- environment files
- personal identifiers
- raw workbook, image, PDF, and generated report artifacts

Expected matches should be code variable names, test fixtures, or documentation examples only. Raw workbooks, screenshots, keys, and runtime state should not appear.
