# Architecture

BSDAN Business Simulator is a FastAPI application with server-rendered pages and a Python simulation engine. The core design goal is to keep user flow, state normalization, business settlement, and report rendering separable enough that each layer can be tested.

## Runtime Layers

```text
Browser
  -> FastAPI routes in exschool_game/app.py
  -> Jinja templates in exschool_game/templates/
  -> static JS/CSS in exschool_game/static/
  -> campaign_support.py
  -> engine.py
  -> rule modules
  -> report_payload.py
  -> optional report image export
```

## Important Modules

| Path | Responsibility |
| --- | --- |
| `exschool_game/app.py` | FastAPI app, routing, sessions, single-player flow, multiplayer flow, report download endpoints. |
| `exschool_game/auth_client.py` | Auth use cases that connect routes to email verification and user storage. |
| `exschool_game/auth_store.py` | JSON-backed local account store for small deployments. |
| `exschool_game/email_code_service.py` | SMTP-backed verification-code sender. |
| `exschool_game/user_store.py` | JSON-backed saved-game store. |
| `exschool_game/multiplayer_store.py` | JSON-backed multiplayer room state and submission store. |
| `exschool_game/campaign_support.py` | Form normalization, default decisions, previous-round state, campaign state assembly. |
| `exschool_game/data_loader.py` | Loads bundled workbooks, structured market reports, inferred decisions, and provenance metadata. |
| `exschool_game/engine.py` | Main round settlement orchestration and simulator facade. |
| `exschool_game/modeling.py` | CPI/share feature engineering and model fitting/prediction helpers. |
| `exschool_game/market_allocation.py` | Integer allocation and market demand/stock distribution helpers. |
| `exschool_game/inventory.py` | Component/product inventory, production caps, and storage handling. |
| `exschool_game/workforce.py` | Worker/engineer changes, salary effects, attrition, and capacity. |
| `exschool_game/finance.py` | Financing and cash-flow helpers. |
| `exschool_game/research.py` | Patent and material-cost effects. |
| `exschool_game/report_payload.py` | Converts settlement state into page/report tables. |
| `exschool_game/export_report_html.py` | Builds report HTML for image export. |

## Data Flow for One Round

1. The browser submits a decision form.
2. `app.py` validates flow state, auth/session context, CSRF, and mode.
3. `campaign_support.py` normalizes the form into a decision payload and merges previous campaign state.
4. `engine.py` builds the round context from fixed opponents, market data, previous state, and current decisions.
5. The rule modules settle workforce, production, inventory, research, market allocation, and finance.
6. `report_payload.py` builds report tables and notes.
7. The app stores the updated campaign/room state under `storage/`.
8. The user sees a report page and can export an image-style report.

## Persistence

The current persistence layer is intentionally simple:

- `storage/session_secret.txt` for a generated local session secret when `EXSCHOOL_SESSION_SECRET` is not set.
- `storage/users.json` and related files for small local account storage.
- `storage/` room/game files for multiplayer and saved campaigns.
- `storage/report_png_cache/` for exported report images.

This is appropriate for a small demo, classroom server, or portfolio deployment. Larger public deployments should move accounts, rooms, and game state to a real database and use Redis or another shared store for multi-process coordination.

## Public Data Artifacts

The simulator depends on bundled workbook artifacts:

- `exschool/*.xlsx`
- `outputs/exschool_inferred_decisions/*.xlsx`
- `outputs/exschool_market_report_exports/**/*.xlsx`
- `WYEF_results/*.xlsx`
- `generated_reports/model_pipeline_current_baseline/metrics.csv`

Those files are committed because they are required for normal operation and validation. Runtime secrets and user data are not committed.

## Deployment Shape

A typical server deployment is:

```text
nginx :443
  -> 127.0.0.1:8010
  -> uvicorn exschool_game.app:app
  -> writable ./storage
```

The included deployment files are examples, not secrets:

- `deploy/exschool-game.env.example`
- `deploy/exschool-game.service`
- `deploy/nginx-exschool-game.conf`
