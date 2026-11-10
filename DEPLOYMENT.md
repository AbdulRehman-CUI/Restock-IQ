# Vercel deployment

## Market & Pricing release setup (7 November 2026)

Implemented on `codex/live-fx-replacement-pricing`; **not merged, deployed or scheduled**. [Feature/provider/import policy](docs/MARKET_PRICING.md). No provider key or payment is required. Existing `DATABASE_URL`, `RESTOCKIQ_ADMIN_PASSWORD`, and `RESTOCKIQ_CLOUD` retain their roles.

1. Review the branch and back up the intended database through the existing process. Additive migration 1 runs through `python -m app.cli init-db` or API startup, preserving existing tables/records. Rollback application code if needed while leaving new tables/ledger intact; no destructive down migration.
2. Keep `RESTOCKIQ_FX_STALE_DAYS=3` and `RESTOCKIQ_QUOTE_STALE_DAYS=30` or explicitly adjust them. Manual FX refresh needs no additional configuration.
3. For scheduling, create a strong random, header-safe **`CRON_SECRET`** (at least 16 characters) in the intended Vercel environment. Endpoint: `GET /api/v1/market/scheduled-refresh`, requiring `Authorization: Bearer <CRON_SECRET>`. A workspace session is insufficient. Unconfigured returns 503; incorrect bearer returns 401. No raw provider rate is returned.
4. Verify the current team's plan and Container Services scheduling support in the dashboard before adding a schedule. Read-only connector checks located the RestockIQ project/team, but returned team metadata did not expose the billing plan. Official [Cron limits](https://vercel.com/docs/schedules/cron-jobs/usage-and-pricing) permit all plans, with daily execution/hour-level timing on Hobby. [Cron management](https://vercel.com/docs/schedules/cron-jobs/manage-cron-jobs) documents automatic bearer injection. Cron is currently documented as a legacy feature. This branch leaves `vercel.json` schedules unchanged pending that verification.
5. Once verified, add this entry alongside existing `services`/`rewrites`, then deploy through the approved release process:

   ```json
   "crons": [{ "path": "/api/v1/market/scheduled-refresh", "schedule": "0 2 * * *" }]
   ```

   This is 02:00 UTC / 07:00 Asia/Karachi daily; Hobby may invoke within that hour. Database cooldown prevents more-than-daily fetching, including duplicate/manual requests. If a prior delayed invocation is less than 24 hours old, a scheduled invocation can reuse cache. If your Container Services configuration cannot register legacy Cron routes, use an HTTP scheduler with the same daily GET/bearer contract. No permanent process is required.
6. Redeploy with the secret and verify 2xx scheduled logs before claiming automation is active. Setting an environment variable alone does not deploy code or activate a schedule. Secret rotation requires redeployment; preview and production are separate.
7. Explicitly enable **automatic reassessment** per dataset in Market & Pricing. Scheduled calls refresh/cache-check FX, clean expired quote uploads, and save only changed financial/freshness inputs. Seller quotes, bank rates and acquisition costs still require seller input. Synthetic products have an independent real-FX opt-in. No emails, orders, storefront updates, demand retraining or background loops run.

For the new browser screen locally, set `RESTOCKIQ_CLOUD=1` and a workspace password, then use the existing API server. Without `DATABASE_URL`, SQLite remains available. The original five Streamlit screens remain supported; Market & Pricing is a browser-only screen. [Executed verification](docs/MARKET_PRICING_REPORT.md) includes real localhost PostgreSQL tests and Node component checks. No browser automation/screenshots were used.

## Existing hosted deployment

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
