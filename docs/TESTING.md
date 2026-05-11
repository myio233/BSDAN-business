# Testing

The project has three validation levels: Python regression tests, launch preflight checks, and browser flow checks.

## Python Regression Suite

Run:

```bash
python -m pytest -q
```

Important coverage areas:

| Area | Representative tests |
| --- | --- |
| Campaign state and defaults | `test_campaign_support.py`, `test_exschool_game_modes.py` |
| Engine settlement and report correctness | `test_engine_report_correctness.py`, `test_engine_data_fidelity.py`, `test_exschool_game_hr.py` |
| Finance and inventory | `test_finance.py`, `test_inventory.py` |
| Market allocation | `test_market_allocation.py`, `test_market_allocation_home_city_boost.py` |
| Modeling and calibration | `test_modeling.py`, `test_modeling_stage2_market_context.py`, `test_runtime_stage1_calibrator_alignment.py` |
| Multiplayer state | `test_multiplayer_mode.py`, `test_multiplayer_store.py` |
| Report payload and export semantics | `test_report_payload.py`, `test_report_notes_visibility.py`, `test_export_report_html_semantics.py` |
| Data provenance | `test_data_loader_provenance.py`, `test_reconstruct_exschool_decisions.py` |

## Launch Preflight

Run:

```bash
python scripts/launch_preflight.py
```

The preflight checks:

- local virtualenv presence when using the service startup script
- startup/review/browser script presence
- Playwright importability
- baseline model metrics
- real-original fixed-decision coverage
- SMTP configuration reachability when SMTP settings are present

SMTP warnings are acceptable for local development when email verification is not being exercised.

## Browser Checks

Install browser tooling:

```bash
pip install -r requirements-dev.txt
python -m playwright install chromium
```

Single-player/mode validation:

```bash
python scripts/validate_exschool_modes_playwright.py
```

Multiplayer smoke validation:

```bash
python scripts/validate_multiplayer_room_playwright.py --human-seats 2 --bot-count 1 --rounds 1
```

Full-room validation is more expensive but closer to the intended classroom scenario:

```bash
python scripts/validate_multiplayer_room_playwright.py --human-seats 6 --bot-count 6 --rounds 4
```

## Manual Review Checklist

- Home page loads without server errors.
- Mode selection opens single-player and multiplayer paths.
- Single-player setup can start round 1.
- Round report shows KPI, finance, market, production, HR, and notes.
- Multiplayer host can create a room.
- Guest users can join and choose seats/home cities.
- All required players/bots can submit and trigger settlement.
- Report image export works when Playwright is installed.
- `storage/` is writable and not committed.
- `.env`, SMTP secrets, API keys, user records, and session secrets are not tracked.
