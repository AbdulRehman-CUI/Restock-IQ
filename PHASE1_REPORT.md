# Phase 1 completion report

Implemented only **Foundation and Data**, following the fully read `ARCHITECTURE.md`. The original architecture file was preserved. No substantive architecture contradiction required a change; routine interpretations and defaults are documented in `README.md`.

## Delivered

- A Python package with pinned dependencies, `.env.example`, repository-relative configuration, and Windows instructions.
- Six Pydantic CSV contracts with record, calendar, reference, supplier, date, status, uniqueness, precision, and inventory checks.
- A seeded synthetic retailer with chronological stock movements, receipts, delayed/cancelled orders, observed sales, separate hidden simulation truth, manifests, and content hashes.
- SQLite datasets, six business entities, persisted jobs, exact scaled-integer Decimal storage, composite keys, foreign keys, and atomic imports.
- Data-quality reports separating historical schema validity from future cost-input readiness.
- The five requested API routes, one local background worker, duplicate-job rejection, progress, sanitized failures, restart recovery, multipart size bounds, and temporary-file cleanup.
- CLI commands reusing the same services, a small checked-in fixture, and focused tests.

Important implementation files:

| Files | Responsibility |
| --- | --- |
| `app/data/contracts.py`, `app/data/synthetic.py` | Typed data contracts and deterministic simulation |
| `app/services/validation.py`, `quality.py`, `ingestion.py` | Validation, quality/readiness, and atomic imports |
| `app/db/models.py`, `session.py`, `repositories.py` | Exact storage, constraints, sessions, and repositories |
| `app/services/jobs.py`, `app/api/` | Persistent worker and typed API |
| `app/core/config.py`, `app/cli.py` | Configuration and executable commands |
| `tests/`, `tests/fixtures/small/` | Verification and a small reproducible import batch |

## Exact commands

For a clean checkout with Python 3.12 installed, run in PowerShell from the project directory:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock
.\.venv\Scripts\python.exe -m pip install --no-build-isolation --no-deps -e .
Copy-Item .env.example .env
.\.venv\Scripts\python.exe -m app.cli init-db
.\.venv\Scripts\python.exe -m app.cli generate-demo --seed 42 --start-date 2025-10-16 --days 365 --sku-count 20 --import
.\.venv\Scripts\python.exe -m app.cli serve
```

The current workspace already has `.venv`, the database, and the generated dataset. Its Python is the bundled Python 3.12.14 because the system `python` command points to a Windows Store alias. Start it directly with the final command above. API documentation is available at `http://127.0.0.1:8000/docs` while running.

```powershell
.\.venv\Scripts\python.exe -m app.cli summary 229e8694-5e15-4809-b7c9-a59e21c26dad
.\.venv\Scripts\python.exe -m app.cli import tests/fixtures/small
.\.venv\Scripts\python.exe -m pytest -q --basetemp=runtime/final-tests
```

Every regeneration creates new UUID directories. Existing demos and uploaded business data are preserved. See `README.md` for configurable paths, API requests, defaults, and platform differences.

## Executed default workflow

Dataset ID: `229e8694-5e15-4809-b7c9-a59e21c26dad`.

- Operational CSVs and manifest: `data/demo/229e8694-5e15-4809-b7c9-a59e21c26dad/`.
- Hidden demand/reconciliation and delivery plans: `data/evaluation_only/229e8694-5e15-4809-b7c9-a59e21c26dad/`.
- Database: `runtime/restockiq.sqlite3`.
- CLI output: `runtime/default-demo-result.json`.
- Parameters: seed 42, 20 SKUs, 365 days, 2025-10-16 through 2026-10-15, Asia/Karachi, PKR.

| Operational table/CSV | Rows |
| --- | ---: |
| Products | 20 |
| Daily sales | 7,300 |
| Inventory snapshots | 20 |
| Supplier quotes | 540 |
| Purchase orders | 770 |
| FX rates | 730 |

Quality findings: **100% product/day coverage**, zero blocking validation errors, 772 stockout-affected open SKU-days, six closed calendar days (120 closed SKU-days), and 1,430 final on-hand/available units with zero reservations. Orders: 728 received, 22 open, 20 cancelled. Four open orders are overdue. Final eligible quotes and required FX rates are present and fresh; no expired final quotes. Censoring and overdue supply are explicit warnings. All generated data is labeled synthetic in its manifest.

## Actual verification results

- Final full suite: **52 passed in 5.44 seconds**, one upstream deprecation warning. Command: `python -m pytest -q --basetemp=runtime/final-tests` using `.venv` Python.
- Tests include full-default deterministic CSV/hash equality, product/day coverage, nonnegative quantities, daily stock reconciliation, stock-constrained sales, exact PO receipt matching, cancellations, final snapshot reconciliation, future-arrival hiding, hidden-data separation, historical policy estimates, invalid references/duplicates/rates/margins/dates, rollback after injected write failure, repeated-import isolation, exact money storage, summary counts/readiness, and API/job/upload success/failure/restart behavior.
- Full default CLI generation and database import succeeded.
- Actual Uvicorn HTTP startup through the CLI succeeded on temporary port 8765: health 200, persisted default summary 200 with 7,300 sales rows, unknown job 404. The temporary server was stopped afterward. Diagnostic output is in `runtime/http-smoke.log`.
- Dependency check returned `No broken requirements found`.

An initial test expectation was corrected: cancelled orders with actual receipt dates fail status validation, while a separate received-order test checks future actual-date rejection. The final suite has no failures. FastAPI tests required running outside the tool sandbox because Windows blocked asyncio's local socket pair inside it; this is an execution-environment constraint.

## Limits and Phase 2 handoff

No unresolved functional failures remain. Starlette's TestClient emits an upstream AnyIO `BlockingPortal` deprecation warning; runtime checks and API tests pass. This is a single-process local application without authentication, migrations, or a production job system. Imported operational CSVs cannot prove intraday stock reconciliation without additional records; the generated simulation is fully reconciled against separate truth. Missing cost inputs block future cost readiness, not historical data storage. Failed generation can leave an unimported UUID folder for diagnosis.

Phase 2 can consume immutable dataset IDs, observed daily sales and availability, product settings, origin inventory, quotes/rates known by origin, purchase histories, and quality findings. It must build chronological features, exclude censored/closed target days, and preserve the evaluation-only boundary. Model training, forecasts, pricing/reorder recommendations, scenarios, and dashboards were intentionally not started.
