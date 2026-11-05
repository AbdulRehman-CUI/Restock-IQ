# Vercel deployment

RestockIQ's hosted application serves a lightweight browser interface and the existing FastAPI/ML engine from one Python container. The Streamlit interface remains available locally. Container Services is a Vercel beta feature.

## Configuration

- `DATABASE_URL`: pooled Neon PostgreSQL connection, provided by the Vercel integration. Never commit it.
- `RESTOCKIQ_ADMIN_PASSWORD`: workspace password. Reveal it in Vercel project Settings → Environment Variables to sign in. Rotate it and redeploy to revoke all sessions. This is a single-owner workspace, not a multi-user tenant system.
- `RESTOCKIQ_CLOUD=1`: set by the Dockerfile; enables hosted routes and temporary working directories.
- `BLOB_READ_WRITE_TOKEN`: provisioned for future object-storage use; the current migration keeps application-written, hash-checked models in PostgreSQL along with business records. No uploaded file can become a loadable model artifact.

The browser uses an HttpOnly, SameSite session cookie. The password and database credentials are never sent in frontend assets. API responses are not cached. No third-party frontend scripts or fonts are loaded.

## Persistence and jobs

PostgreSQL stores datasets, jobs, forecasts, recommendations, immutable scenarios/reviews, and model artifacts. `/tmp` holds disposable generation and import working files only. Local SQLite remains the default when DATABASE_URL is absent. Existing local datasets are not automatically uploaded.

The browser submits a job, holds an execution request open, and polls its saved status. A database row lock prevents duplicate execution across instances. Jobs are not dispatched to orphaned process threads in hosted mode. Stale queued/running jobs become explicitly failed after ten minutes, so they do not block later submissions. Completed steps remain saved. Keep the tab open while preparing data or training; this is request-bound execution, not a durable background queue. Vercel's execution limits still apply (300 seconds on Hobby); unusually large training datasets may require a dedicated worker in a later release. No algorithm, estimator count, or data validation rule has been reduced for deployment.

CSV import uses 512 KiB chunks with idempotent positions and exact-length checks, retaining the original combined 12 MiB limit. Completion validates and commits atomically. Old upload batches are removed when another upload starts after 24 hours. Uploaded records, not temporary original CSV files, are the durable business representation.

Currency columns use scaled BIGINT values on PostgreSQL, preserving Decimal precision. Initialization uses a transaction-scoped advisory lock. PostgreSQL triggers enforce the same immutable scenario/review guarantees as SQLite.

## Local checks

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.lock
.venv/bin/pip install --no-deps -e .
.venv/bin/python -m pytest -q
node --check app/web/app.js
```

For the hosted frontend locally, set RESTOCKIQ_CLOUD=1 and RESTOCKIQ_ADMIN_PASSWORD in your shell, then run `python -m app.cli serve`. Do not commit these values. Without DATABASE_URL this uses temporary local SQLite for testing only.

## Feature map

- Overview: order/expedite/margin/cash KPIs, priority table, assumptions and exports.
- Products: search/action filters, product calculations, stock and sales charts, daily forecasts, review/reject and append-only history.
- Scenarios: FX, supplier cost, freight, lead-time and margin changes; saved base/scenario comparisons.
- Model performance: validation and test results, method selection, partitions, coverage and provenance.
- Data: six-file import and templates, configurable seeded simulation, quality review, training/model reuse, forecasts and saved recommendations.

Preview and production currently use the database connection supplied by the integration. Use separate Neon branches for isolated preview data if desired. All test imports create fresh dataset IDs; no existing data is deleted.
