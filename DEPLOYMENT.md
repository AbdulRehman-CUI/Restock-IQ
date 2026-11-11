# Vercel deployment

## Configure USD/PKR rates

1. Create a free ExchangeRate-API account, confirm your email, and copy the API key from the provider dashboard. ExchangeRate-API restricts programmatic redistribution. The public demo exposes exact observations through its API and exports, so obtain written clearance for that use before enabling the feed on a public deployment. [Account signup](https://app.exchangerate-api.com/sign-up) · [Standard API](https://www.exchangerate-api.com/docs/standard-requests) · [Provider terms](https://www.exchangerate-api.com/terms)
2. Locally, create a gitignored `.env` from `.env.example` if needed and set the following values. Restart the API process after editing `.env`.

   | Variable | Value |
   | --- | --- |
   | `EXCHANGE_RATE_API_PROVIDER` | `exchange-rate-api` for the authenticated Standard API. This is the default. |
   | `EXCHANGE_RATE_API_KEY` | Your provider key. Keep it server-side. Leave it blank to disable Standard requests. |
   | `EXCHANGE_RATE_API_BASE_URL` | Optional. Leave unset to use `https://v6.exchangerate-api.com/v6`. An override must be a trusted HTTPS base including the API version, with no credentials, query, or fragment. |

3. In Vercel, open **RestockIQ → Settings → Environment Variables**. Add the provider and key to **Production** for the live site. Add them to **Preview** only where you want provider access and to **Development** only if you use Vercel to supply local variables. Mark the key **Sensitive** where available. Add the base URL only if overriding it. A new deployment loads the saved values, so redeploy the intended environment after any change. [Vercel environment variables](https://vercel.com/docs/environment-variables/managing-environment-variables)
4. To verify the key locally without touching the database, run `python -m scripts.check_fx_provider --live` with the project's virtual environment. After redeploying, call `GET /api/v1/market/fx` or open **Market & Pricing**. Look for a positive USD/PKR rate, the ExchangeRate-API Standard source, and a recent provider update. A successful eligible refresh returns `data_status: live`. Later calls within the cooldown return `cached`. If Preview and Production share a database, their cooldown and saved observations are shared. Check `last_success_at` to distinguish a new fetch from a cache hit.

The previous keyless feed can be selected explicitly with `EXCHANGE_RATE_API_PROVIDER=exchange-rate-api-open`. Its attribution and provider terms still apply. With the default provider and a blank key, no upstream requests are sent. Existing observations can be used as stale fallback. If there is no observation, the FX panel can show the selected synthetic dataset’s labeled demo rate. Uploaded data without a usable reference still supports an explicitly entered bank rate. Synthetic pricing uses its own FX assumptions until real FX is explicitly enabled.

## Optional Market & Pricing scheduling

The dashboard and new current-pricing assessments refresh eligible FX cache entries on demand. The dashboard also provides manual refresh under the same database cooldown. Scheduled refresh needs a separate server-side secret. [Feature and provider policy](docs/MARKET_PRICING.md)

1. Review the branch and back up the intended database through the existing process. Additive migration 1 runs through `python -m app.cli init-db` or API startup, preserving existing tables/records. Rollback application code if needed while leaving new tables/ledger intact; no destructive down migration.
2. Configure the FX provider above. Keep `RESTOCKIQ_FX_STALE_DAYS=3` and `RESTOCKIQ_QUOTE_STALE_DAYS=30` or adjust the thresholds for your use.
3. For scheduling, create a strong random, header-safe **`CRON_SECRET`** (at least 16 characters) in the intended Vercel environment. The endpoint `GET /api/v1/market/scheduled-refresh` requires `Authorization: Bearer <CRON_SECRET>`. It returns 503 when unconfigured and 401 for a missing or incorrect bearer.
4. Verify the current team's plan and Container Services scheduling support in the dashboard before adding a schedule. Read-only connector checks located the RestockIQ project/team, but returned team metadata did not expose the billing plan. Official [Cron limits](https://vercel.com/docs/schedules/cron-jobs/usage-and-pricing) permit all plans, with daily execution/hour-level timing on Hobby. [Cron management](https://vercel.com/docs/schedules/cron-jobs/manage-cron-jobs) documents automatic bearer injection. Cron is currently documented as a legacy feature. This branch leaves `vercel.json` schedules unchanged pending that verification.
5. Once verified, add this entry alongside existing `services`/`rewrites`, then deploy through the approved release process:

   ```json
   "crons": [{ "path": "/api/v1/market/scheduled-refresh", "schedule": "0 2 * * *" }]
   ```

   This is 02:00 UTC / 07:00 Asia/Karachi daily. Hobby may invoke within that hour. The database applies the same cooldown to scheduled, duplicate, and manual requests. If a prior successful fetch is less than 24 hours old, a scheduled invocation reuses the cache. If your Container Services configuration cannot register legacy Cron routes, use an HTTP scheduler with the same daily GET and bearer token.
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
