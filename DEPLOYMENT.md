# Vercel deployment

## Optional Market & Pricing scheduling

Manual FX refresh is available in the public dashboard. Scheduled refresh needs a separate server-side secret. [Feature and provider policy](docs/MARKET_PRICING.md).

1. Review the branch and back up the intended database through the existing process. Additive migration 1 runs through `python -m app.cli init-db` or API startup, preserving existing tables/records. Rollback application code if needed while leaving new tables/ledger intact; no destructive down migration.
2. Keep `RESTOCKIQ_FX_STALE_DAYS=3` and `RESTOCKIQ_QUOTE_STALE_DAYS=30` or explicitly adjust them. Manual FX refresh needs no additional configuration.
3. For scheduling, create a strong random, header-safe **`CRON_SECRET`** (at least 16 characters) in the intended Vercel environment. The endpoint `GET /api/v1/market/scheduled-refresh` requires `Authorization: Bearer <CRON_SECRET>`. It returns 503 when unconfigured and 401 for a missing or incorrect bearer.
4. Verify the current team's plan and Container Services scheduling support in the dashboard before adding a schedule. Read-only connector checks located the RestockIQ project/team, but returned team metadata did not expose the billing plan. Official [Cron limits](https://vercel.com/docs/schedules/cron-jobs/usage-and-pricing) permit all plans, with daily execution/hour-level timing on Hobby. [Cron management](https://vercel.com/docs/schedules/cron-jobs/manage-cron-jobs) documents automatic bearer injection. Cron is currently documented as a legacy feature. This branch leaves `vercel.json` schedules unchanged pending that verification.
5. Once verified, add this entry alongside existing `services`/`rewrites`, then deploy through the approved release process:

   ```json
   "crons": [{ "path": "/api/v1/market/scheduled-refresh", "schedule": "0 2 * * *" }]
   ```

   This is 02:00 UTC / 07:00 Asia/Karachi daily; Hobby may invoke within that hour. Database cooldown prevents more-than-daily fetching, including duplicate/manual requests. If a prior delayed invocation is less than 24 hours old, a scheduled invocation can reuse cache. If your Container Services configuration cannot register legacy Cron routes, use an HTTP scheduler with the same daily GET/bearer contract. No permanent process is required.
6. Redeploy with the secret and verify 2xx scheduled logs before claiming automation is active. Setting an environment variable alone does not deploy code or activate a schedule. Secret rotation requires redeployment; preview and production are separate.
7. Configure automatic reassessment per dataset from a trusted local API. The pricing settings endpoint accepts `auto_assess` in local mode, and public updates preserve its saved value. Scheduled calls check the FX cache, clean expired quote uploads, and save assessments when financial or freshness inputs change.

## Existing hosted deployment

RestockIQ serves the browser dashboard and the FastAPI/ML engine from one Python container. The root URL opens the Overview, and bookmarked dashboard views use URL fragments such as `/#products` and `/#market`. The browser uses relative `/api/v1/` URLs on the same host.

## Configuration

- `DATABASE_URL`: pooled Neon PostgreSQL connection, provided by the Vercel integration. Never commit it.
- `RESTOCKIQ_ADMIN_PASSWORD`: obsolete after deploying this version. Remove it from the hosting environment once older password-gated deployments are retired.
- `RESTOCKIQ_CLOUD=1`: set by the Dockerfile; enables hosted routes and temporary working directories.
- `BLOB_READ_WRITE_TOKEN`: provisioned for future object-storage use; the current migration keeps application-written, hash-checked models in PostgreSQL along with business records. No uploaded file can become a loadable model artifact.

Visitors share the database configured for this deployment. Before releasing the public demo, verify that it contains only records intended for public display, including previously imported datasets, saved quotes, and review notes. Keep connection strings and scheduler secrets on the server. The hosted API sends `Cache-Control: no-store` and rejects cross-origin browser mutations.

The pricing API and exports still expose exact provider FX observations. Obtain provider clearance for public access or address FX exposure before releasing this version. ExchangeRate-API restricts programmatic redistribution. [Provider terms](https://www.exchangerate-api.com/terms)

## Persistence and jobs

PostgreSQL stores datasets, jobs, forecasts, recommendations, immutable scenarios/reviews, and model artifacts. `/tmp` holds disposable generation and import working files only. Local SQLite remains the default when DATABASE_URL is absent. Existing local datasets are not automatically uploaded.

The browser submits a job, holds an execution request open, and polls its saved status. A database row lock prevents duplicate execution across instances. Jobs are not dispatched to orphaned process threads in hosted mode. Stale queued/running jobs become explicitly failed after ten minutes, so they do not block later submissions. Completed steps remain saved. Keep the tab open while preparing data or training; this is request-bound execution, not a durable background queue. Vercel's execution limits still apply (300 seconds on Hobby); unusually large training datasets may require a dedicated worker in a later release. No algorithm, estimator count, or data validation rule has been reduced for deployment.

The public demo offers synthetic data generation and blank CSV templates. Bulk dataset and supplier price-list imports are available through the trusted local API with `RESTOCKIQ_CLOUD=0`. Use a local database for private business files.

Currency columns use scaled BIGINT values on PostgreSQL, preserving Decimal precision. Initialization uses a transaction-scoped advisory lock. PostgreSQL triggers enforce the same immutable scenario/review guarantees as SQLite.

## Local checks

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.lock
.venv/bin/pip install --no-deps -e .
.venv/bin/python -m pytest -q
node --check app/web/app.js
```

To test the browser dashboard locally, point `RESTOCKIQ_DATA_DIR`, `RESTOCKIQ_RUNTIME_DIR`, and `RESTOCKIQ_ARTIFACT_DIR` to local demo directories. With `DATABASE_URL` unset and `RESTOCKIQ_CLOUD=0`, run `python -m app.cli prepare-demo` to create or reuse a saved synthetic analysis. Then set `RESTOCKIQ_CLOUD=1`, run `python -m app.cli serve`, and open [the local dashboard](http://127.0.0.1:8000/).

## Feature map

- Overview: order/expedite/margin/cash KPIs, priority table, assumptions and exports.
- Products: search/action filters, product calculations, stock and sales charts, daily forecasts, review/reject and append-only history.
- Scenarios: FX, supplier cost, freight, lead-time and margin changes; saved base/scenario comparisons.
- Model performance: validation and test results, method selection, partitions, coverage and provenance.
- Data: CSV templates, configurable seeded simulation, quality review, training/model reuse, forecasts and saved recommendations.

Preview and production currently use the database connection supplied by the integration. Use separate Neon branches for isolated preview data if desired. All test imports create fresh dataset IDs; no existing data is deleted.
