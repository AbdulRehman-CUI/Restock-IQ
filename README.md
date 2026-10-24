# RestockIQ — Forecasting and Inventory Decision Engine

An offline data foundation for a fictional Pakistani electronics-accessories retailer. **All generated business records, customer demand, supplier quotes, events, and FX rates are synthetic.** Business timezone: `Asia/Karachi`; reporting currency: `PKR`; prices are tax-exclusive. The default history is **2025-10-16 through 2026-10-15**, with 20 SKUs and 365 days each.

Implemented: six validated CSV contracts, deterministic synthetic data, atomic SQLite imports, quality summaries, chronological RF/baseline evaluation, recursive forecasts, replacement costs, capped price suggestions, stock projections, replenishment quantities, immutable scenarios, persistent recommendations, safe exports, FastAPI, CLI, and a five-section Streamlit/Plotly dashboard with local review history. `ARCHITECTURE.md` remains the full-product specification.

## Setup (Windows PowerShell)

Use a normal Python **3.11–3.13** installation; Python 3.12 is the verified environment. From this repository:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock
.\.venv\Scripts\python.exe -m pip install --no-build-isolation --no-deps -e .
Copy-Item .env.example .env
.\.venv\Scripts\python.exe -m app.cli init-db
.\.venv\Scripts\python.exe -m app.cli generate-demo --import
.\.venv\Scripts\python.exe -m app.cli serve
```

Open [API documentation](http://127.0.0.1:8000/docs) after starting the service. Run one API process, with one local worker; do not run several Uvicorn workers or a second API against the same database. Stop with Ctrl+C; normal shutdown waits for the current job. No external service or network access is used after dependency installation. Update a Phase 1 environment using the same two pip install commands above. `init-db` creates model/forecast tables additively without dropping or recreating business tables.

On macOS/Linux, replace `py -3.12` with `python3.12` and `.\.venv\Scripts\python.exe` with `.venv/bin/python`. Activation is optional; explicit interpreter paths avoid PowerShell execution-policy changes. Installed console commands also work as `.\.venv\Scripts\restockiq.exe ...`.

`requirements.lock` pins direct and transitive runtime/test dependencies and the build backend. `pyproject.toml` records project dependencies and the console entry point. Windows-only packages use environment markers. The lock targets Python 3.11–3.13; verification used Windows x64/Python 3.12.

## Dashboard startup (separate PowerShell terminals)

After installing the locked dependencies above, optionally prepare a complete synthetic demonstration once:

```powershell
Set-Location 'C:\Users\J A\Desktop\MarginShift'
.\.venv\Scripts\python.exe -m app.cli prepare-demo
```

This prints selected IDs, reuses compatible saved data/models/runs, and creates only missing prerequisites. It never clears the database or runs automatically when opening the UI. Run it with the API stopped if another heavy job is active.

Terminal 1 — API:

```powershell
Set-Location 'C:\Users\J A\Desktop\MarginShift'
.\.venv\Scripts\python.exe -m app.cli serve --port 8000
```

Terminal 2 — dashboard:

```powershell
Set-Location 'C:\Users\J A\Desktop\MarginShift'
.\.venv\Scripts\python.exe -m app.cli ui --port 8501 --api-url http://127.0.0.1:8000
```

Open [RestockIQ](http://127.0.0.1:8501), [API health](http://127.0.0.1:8000/api/v1/health), or [API documentation](http://127.0.0.1:8000/docs). Stop each terminal with Ctrl+C. If port 8000 is occupied, stop your previous API or use `serve --port 8001` and `ui --api-url http://127.0.0.1:8001`. Do not use multiple workers against one database. No public hosting is configured.

Without prepared data, select **Data & Settings**. Generate a new synthetic dataset or upload all six CSVs, inspect quality, explicitly train, wait for completion, generate a forecast at the snapshot origin, then generate base recommendations. Dependent steps stay disabled until their prerequisites exist. Job polling runs every two seconds only while the tracked job is active; failed jobs require explicit resubmission.

The five pages are **Overview**, **Products**, **Scenarios**, **Model Performance**, and **Data & Settings**. Choose a dataset in the sidebar; **Advanced analysis selection** contains completed model, forecast, and exact saved analysis selectors. Dataset/model/forecast changes clear dependent selections. A compact context caption shows provenance, analysis date, horizon, and scenario name. Full IDs, saved settings, and assumptions are in **Technical details · selected analysis**. Editable drafts apply only to a new run. Scenarios can reuse saved assumptions or submit a named hypothetical scenario, preserving the chosen base pricing settings. **FX +10% / lead +7** leaves product target margins unchanged; **Phase 3 demonstration** additionally sets a 20% margin override.

The redesign uses a light slate/white theme, restrained teal accents, system fonts, native keyboard-accessible controls, numeric sortable columns, and a shared Plotly theme. Overview puts urgent saved explanations before searchable inventory decisions. Products groups demand, costs, and recommendation details into tabs. Scenarios places assumptions alongside saved comparisons with percentage-point margin differences and explicit unapplied-form notices. Model Performance separates validation from held-out test results. Data & Settings presents a five-step setup workflow with collapsed advanced controls. The sidebar stays light to preserve native control contrast in Streamlit 1.45.1. See [redesign report and manual review checklist](docs/UI_REDESIGN.md); visual browser inspection remains a manual handoff.

The Overview cash card includes only saved `order_required=true` products with available cash. It reports excluded counts, marks partial totals, and displays unavailable when no eligible values exist. Table order is expedite, order-now, earliest shortage, then SKU. Coverage quantities are candidates; no suggested orders are inserted into stock projections. Review/dismiss actions append a local audit record and have no effect on calculations.

Downloads are fetched from the API using the visible run IDs. Immutable GET results are cached by API address and full path; discovery, job status, and audit history are uncached. Mutations never auto-retry. After a timeout, refresh discovery to check whether the operation committed before resubmitting. An action request UUID makes a retried identical local review idempotent; reusing it with changed content returns 409.

Presentation handoff: [demo script](docs/DEMO_SCRIPT.md), [six-slide outline](docs/PRESENTATION_OUTLINE.md), [limitations](docs/LIMITATIONS.md), and [Phase 4 verification report](PHASE4_REPORT.md).

Additional API reads: `GET /datasets`, `/models?dataset_id=…`, `/forecasts?dataset_id=…&model_id=…`, `/recommendations?dataset_id=…&forecast_run_id=…`, `/scenarios?dataset_id=…&forecast_run_id=…`, `/datasets/{id}/products/{sku}/sales?through=YYYY-MM-DD`, `/data-contracts`, `/recommendations/{id}/overview`, and forecast/recommendation `/{id}/metadata`. Review history uses GET/POST `/recommendations/{id}/actions` with `request_id`, `sku`, `action` (`reviewed` or `rejected`), and optional `note`.

## CLI reference

```powershell
# Export a NEW demo; this alone does not import it.
.\.venv\Scripts\python.exe -m app.cli generate-demo --seed 42 --start-date 2025-10-16 --days 365 --sku-count 20

# Export and import together. Save the printed dataset_id and operational_dir.
.\.venv\Scripts\python.exe -m app.cli generate-demo --seed 43 --import

# Import a six-CSV directory and inspect the printed dataset_id.
.\.venv\Scripts\python.exe -m app.cli import 'data/demo/<directory-id>'
.\.venv\Scripts\python.exe -m app.cli summary '<dataset-id>'

# Full tests; runtime is ignored by source control.
.\.venv\Scripts\python.exe -m pytest -q --basetemp=runtime/pytest-temp
# Equivalent CLI shortcut:
.\.venv\Scripts\python.exe -m app.cli test
```

Default paths (all relative configuration paths resolve against the repository, independent of the working directory):

| Path | Contents |
| --- | --- |
| `data/demo/<UUID>/` | Six operational CSVs plus `manifest.json` |
| `data/evaluation_only/<UUID>/simulation.csv` | Hidden demand, stock movement reconciliation, fictional event labels, historical policy estimate |
| `data/evaluation_only/<UUID>/order_truth.csv` | Simulated delivery plans, including future actual arrivals |
| `data/uploads/tmp/batch-*/` | Temporary fixed-name multipart files; removed after success or failure |
| `runtime/restockiq.sqlite3` | Persistent datasets, business records, job state (SQLite WAL files may sit alongside it) |
| `artifacts/models/<model-UUID>/` | Application-owned `pipeline.joblib` plus `metadata.json`; distinct validation, test, and final fits |
| `tests/fixtures/small/` | Small checked-in synthetic batch for a quick import |

`RESTOCKIQ_DATA_DIR`, `RESTOCKIQ_RUNTIME_DIR`, and `RESTOCKIQ_ARTIFACT_DIR` override these roots. `.env` is loaded from the repository. Freshness limits and total multipart upload size are configurable in `.env.example`. CLI import directories also resolve relative to the repository. Database files, generated data, uploads, secrets, and large artifacts are ignored. Demo regeneration always creates new UUID directories; it never replaces another dataset or uploaded business data. A failed generation may leave an unimported UUID directory for diagnosis; no partial dataset is committed.

Identical imports deliberately create **separate immutable datasets**, each with a fresh UUID and content hashes. Within a dataset, composite primary keys prohibit duplicate products, sales, snapshots, quote IDs, PO IDs, and FX date/currency pairs. The six CSVs are the only import inputs: an uploaded manifest is never trusted to assert synthetic provenance. Thus manually importing even a generated batch records `uploaded`; `generate-demo --import` and the demo API preserve synthetic provenance and generator configuration.

## CSV conventions and validation

See architecture section 6 for every field and its meaning. CSVs use UTF-8 (BOM accepted), a header, ISO dates, timezone-aware ISO timestamps, and `true`/`false` or `1`/`0` booleans. Quantities are whole units. Required fields cannot be blank. Unknown columns, duplicate headers, malformed rows, and unknown product references are blocking errors. An empty quotes/orders/rates table still needs its header.

Optional product defaults: `target_margin=0.20`, `safety_days=2`, `review_days=7`, `moq=1`, `pack_size=1`. Inventory defaults to `reserved_units=0`. Quote `valid_until` and PO `actual_arrival_date` default to null. Missing optional columns and blank optional cells use these defaults. All other fields are required.

Money accepts at most two decimal places and stores exact integer minor units; excess precision is rejected rather than rounded differently during import. Generated money uses Decimal `ROUND_HALF_UP` to cents. FX rates retain up to six decimal places and are **PKR per one currency unit**; PKR must equal 1. Margin fractions retain up to four places, within `[0, 0.80]`. Database columns scale money ×100, FX ×1,000,000, and margins ×10,000. Decimal values are never stored as binary floating-point. The manifest hashes exact CSV bytes, not normalized records.

Coverage requires one row for every product on every date from the earliest through latest sales date. Zero sales must be explicitly recorded; missing rows are never filled. Each product needs one inventory snapshot at the final sales date. Reserved stock cannot exceed on-hand stock. All supplier references must match the primary supplier in products. Observed quotes, rates, order dates, and actual receipts cannot reveal information after the origin. Expected future delivery dates are allowed. Open/cancelled POs must have no actual arrival; received POs must have an actual arrival on/before origin. Arrival/expected dates cannot precede ordering.

The generator treats `fully_in_stock=true` as an open trading day with stock remaining throughout: exact stock depletion is conservatively marked false, even if no unmet demand was recorded. Closed days have `units_sold=0`, `store_open=false`, `fully_in_stock=false`; quality counts them separately from open stockout-affected SKU-days. Imported availability flags are retailer observations; the six input contracts cannot reconstruct missing intraday inventory histories.

Validation errors identify file, CSV row (header is row 1), field, code, and explanation. Cross-file missing-record errors have no physical row. At most 100 errors are displayed with the full error count and a truncation flag. Imports parse and validate before starting one SQL transaction. Any write failure rolls back the dataset and all business entities. SQLite foreign keys and checks are enabled on every connection.

## Simulation and evaluation boundary

The seeded NumPy generator creates category/SKU demand levels, weekday patterns, modest trends, promotions, fictional demand events, product-specific shifts, and Gamma-Poisson count noise. FX follows a positive noisy path with occasional shocks. Quotes arrive every 14 days; USD quote changes and local PKR responses differ by SKU. Some PKR suppliers respond partially and with a delay to FX; others move independently. There are no real holiday labels or external data sources.

For each SKU/day: receive due orders before trading, record available stock, generate demand, fulfill up to available units on open days, record observed sales, close inventory, then apply a simple historical stocking policy. The policy uses the last 28 observed uncensored open days, with the assigned initial demand baseline as a prior until observations exist. It uses known lead/review/safety settings and current on-hand plus open orders. Quantities respect MOQ and pack sizes. Delivery delays, occasional cancellations, and a few initially scarce products produce stockouts without chronic retailer-wide unavailability. Reservations are always zero. This historical policy is solely a data generator, not a recommendation engine.

**Operational `daily_sales.csv` contains fulfilled sales only.** Latent customer demand and future delivery plans live exclusively in `data/evaluation_only/`. Ordinary ingestion reads a fixed allowlist of six CSV filenames; business tables and standard endpoints have no hidden-demand columns or evaluation file access. Phase 2 must preserve this boundary: censor stockout targets and closed days instead of learning hidden simulation demand. Optional future evaluation against truth must be explicitly labeled simulation-only.

Every demo manifest records schema version, synthetic provenance/notice, seed/configuration, dates, timezone, currency, row counts, SHA-256 CSV hashes, creation timestamp, and quality findings. UUIDs and timestamps vary; generated business and evaluation CSV bytes are deterministic for identical parameters under the locked environment.

## API and quality report

| Method | Route | Result |
| --- | --- | --- |
| GET | `/api/v1/health` | Database readiness and conventions |
| POST | `/api/v1/datasets/demo` | 202 with `job_id` and polling URL; JSON fields: `seed`, `start_date`, `days`, `sku_count` (defaults above) |
| POST | `/api/v1/datasets/import` | 201 dataset summary; six multipart fields named `products`, `daily_sales`, `inventory`, `supplier_quotes`, `purchase_orders`, `fx_rates` |
| GET | `/api/v1/datasets/{id}/summary` | Manifest, quality, counts, and cost-input readiness |
| GET | `/api/v1/jobs/{id}` | Persistent queued/running/completed/failed state, stage, progress, dataset reference, safe error |

Generation is limited to 1–200 SKUs and 1–3,650 days. One worker handles demo jobs; a database uniqueness constraint rejects concurrent duplicate starts with 409. Database writes use a shared local lock plus SQLite transaction locking. Completed job state and dataset creation commit together. On restart, queued/running jobs become failed with a retry instruction. A hard stop can leave unimported generated files; they are never treated as datasets automatically. Unexpected API/job errors return a safe error UUID and log technical details locally, without returning stack traces. Bad inputs return 422, unknown UUIDs 404, and oversized multipart bodies 413. The 12 MiB limit covers the **entire request**, including multipart overhead; bodies without Content-Length are also bounded. Uploaded filenames are ignored, and temporary files are cleaned up.

```powershell
$job = Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/v1/datasets/demo -ContentType application/json -Body '{"seed":42,"start_date":"2025-10-16","days":365,"sku_count":20}'
Invoke-RestMethod "http://127.0.0.1:8000/api/v1/jobs/$($job.job_id)"
# Once completed, use the returned dataset_id:
Invoke-RestMethod 'http://127.0.0.1:8000/api/v1/datasets/<dataset-id>/summary'
```

The summary reports product/table counts, coverage, stockout and closure counts (SKU-days versus calendar days explicitly named), quote/FX freshness, PO status totals, overdue orders, and final inventory totals. Unknown product references are blocking schema errors. Missing eligible quotes/rates allow a valid historical dataset to be stored but block affected future cost calculations via `readiness.cost_blockers`. Old/expired quotes and FX are warnings; no historical-cost substitution or future-rate backfill occurs. Freshness is measured at the dataset origin, never the current clock. The immutable dataset summary does not assess model sufficiency; Phase 2 model records separately report eligibility and usable observations at each cutoff.

## Implementation map and handoff

| Files | Responsibility |
| --- | --- |
| `app/data/contracts.py`, `synthetic.py` | Pydantic CSV contracts and seeded historical simulation/export |
| `app/services/validation.py`, `quality.py`, `ingestion.py` | Parsing, cross-file validation, quality/readiness, atomic dataset creation |
| `app/db/models.py`, `session.py`, `repositories.py` | Scaled-integer entities, composite keys, foreign keys, transactions, reads/writes |
| `app/services/jobs.py` | Local worker, persisted progress/failure/recovery |
| `app/api/main.py`, `routes/`, `schemas/` | App lifecycle, request bounds, structured errors, five typed routes |
| `app/core/`, `app/cli.py`, `scripts/generate_demo.py` | Configuration, conventions, errors, shared-service CLI |
| `tests/` | Generator, validation, rollback, persistence, quality, API checks |
| `app/ml/features.py`, `baseline.py`, `predict.py` | Shared chronological proxies/features, observed-sales baseline, recursive forecasts |
| `app/ml/train.py`, `evaluate.py`, `artifacts.py` | Three-fit lifecycle, paired evaluation/selection, registered artifact safety |
| `app/services/forecasting.py`, `app/api/routes/ml.py` | Persisted model metrics/forecasts, CSV export, ML API routes |

The data foundation supports Phases 2 and 3 below. This is a single-business local demo: no authentication, public hosting, live providers, general schema migration framework, or multi-process job orchestration are supplied. No purchase order is sent and no selling price is changed by a recommendation.

## Phase 2 commands and API

The dataset ID is always supplied; no demo ID is hardcoded in application logic. From PowerShell, using a dataset already imported:

```powershell
$datasetId = '<dataset-id>'
.\.venv\Scripts\python.exe -m app.cli summary $datasetId
# Training includes validation, selection, held-out testing, and final fitting.
$training = .\.venv\Scripts\python.exe -m app.cli train $datasetId --seed 42 | ConvertFrom-Json
$modelId = $training.model.id
.\.venv\Scripts\python.exe -m app.cli metrics $modelId
$forecast = .\.venv\Scripts\python.exe -m app.cli forecast $datasetId $modelId --origin 2026-10-15 --horizon-days 30 | ConvertFrom-Json
.\.venv\Scripts\python.exe -m app.cli export-forecast $forecast.forecast_run_id "artifacts/exports/$($forecast.forecast_run_id).csv"
```

Use the dataset's actual origin, not the example date, for other histories. CLI `train` waits for the existing local job worker and returns the persisted job plus the final model record. The worker rejects conflicting heavy starts across demo generation and training with 409; the CLI exits 2 on a failed job. Ordinary imports use the shared database transaction lock. Only one API process should own recovery; CLI training does not interrupt another process's jobs. After a hard interruption, starting the API marks running jobs/models failed with retry instructions. Files from an interrupted run can remain unregistered for diagnosis and are never loaded automatically.

| Method | Route | Result |
| --- | --- | --- |
| POST | `/api/v1/models/train` | JSON `dataset_id`, optional `seed`; 202 with job ID |
| GET | `/api/v1/jobs/{id}` | Training progress/stage; `model_id` when a model run exists, including failed runs |
| GET | `/api/v1/models/{id}/metrics` | Run state, role, cutoff, selection, coverage, per-product metrics, artifact hashes and provenance |
| POST | `/api/v1/forecasts` | JSON `dataset_id`, `model_id`, `origin`, `horizon_days` (1–30); 201 with persisted forecast |
| GET | `/api/v1/forecasts/{id}` | Read the persisted result without recalculating |
| GET | `/api/v1/forecasts/{id}/export` | CSV with units, methods, reasons and provenance |

```powershell
$body = @{dataset_id=$datasetId; seed=42} | ConvertTo-Json
$job = Invoke-RestMethod -Method Post http://127.0.0.1:8000/api/v1/models/train -ContentType application/json -Body $body
$status = Invoke-RestMethod "http://127.0.0.1:8000/api/v1/jobs/$($job.job_id)"
# Poll until completed; model_id identifies the final inference run.
$request = @{dataset_id=$datasetId; model_id=$status.model_id; origin='2026-10-15'; horizon_days=30} | ConvertTo-Json
Invoke-RestMethod -Method Post http://127.0.0.1:8000/api/v1/forecasts -ContentType application/json -Body $request
```

Unknown IDs return 404. Invalid parameters return 422. Dataset/model mismatch, unfinished or damaged artifacts, origins outside the observed date range, or a model trained after the requested origin return 409. A final artifact cannot be used retrospectively before its training cutoff. The metrics record also identifies validation/test evaluation artifacts; these serve the fixed RF comparator (with documented fallbacks), only at or after their own cutoffs. They do not apply a later validation-based selection to earlier origins. One insufficient-history SKU does not block other SKUs: `product_status` explains omissions; `rows` includes only successfully forecasted SKU-days. An all-insufficient result has explicit per-product failures and zero rows. CSV export keeps fractional units and escapes spreadsheet formula-like text; CLI exports never overwrite an existing CSV and include a metadata sidecar with omitted-product statuses and assumptions.

## Features, censoring and recursive semantics

The operational repository reads only SKU/category and observed sales/availability/promotion fields. No ordinary ML operation opens `data/evaluation_only/`, and neither selling prices, inventory, delivery outcomes nor hidden demand are features. Forecasts estimate unconstrained expected demand and are never capped by stock.

For each SKU, prepare proxy values forward through time. An uncensored open day uses observed fulfilled sales. A censored or closed day uses the mean of **earlier uncensored open observations in the preceding 28 calendar days**, excluding the imputed day. If none exist, retain NaN and missingness flags; never backward-fill. Closed and censored days are excluded as target examples. Features for target date t use only dates before t:

| Feature | Definition |
| --- | --- |
| `sku`, `category` | One-hot encoded; unknown categories ignored safely by fitted preprocessing |
| `day_of_week`, `month` | Target-date calendar, Monday=0 |
| `lag_1`, `lag_7`, `lag_14` | Proxy at exact preceding calendar lags |
| `mean_7`, `mean_28` | Proxy means over preceding 7/28 days; require complete nonmissing windows |
| `std_7` | Population standard deviation of preceding seven proxies |
| `availability_28` | Fraction of preceding up-to-28 days that were uncensored/open |
| `promo_active` | Observed historical target-day promotion during fitting; future always false |
| `lag_*_missing`, `missing_fraction_28` | Explicit historical missingness indicators |

Training drops rows without complete lag/rolling history. RF eligibility requires at least 60 calendar days and 30 uncensored open observations through the relevant cutoff. A conservative additional fit guard requires 30 feature-complete uncensored training rows per SKU after warmup; counts are saved. The pooled preprocessing/forest pipeline is fit only on eligible rows at that cutoff. Initial fixed settings: 200 trees, depth 12, leaf minimum 3, seed 42, `n_jobs=-1`; no hyperparameter search is performed.

The baseline averages the last seven uncensored open sales observations in the 28 calendar dates ending at the origin. It never uses proxies, stays constant across the horizon, reports observation count, and warns when fewer than seven observations exist. With no recent usable observations it returns `insufficient_history`. If RF is selected, untrained/unseen SKUs, sparse histories, missing required features, or unavailable RF candidates fall back to this baseline explicitly. Baseline selection itself is not marked a fallback.

At each origin, prepare history only through that date. Recursively predict the next day, append fractional nonnegative expected demand to a private proxy history, then rebuild features for the following day. Future store days are assumed open, promotions inactive, and availability-related features advance as uncensored/open (1). This feature assumption is **not** a stock guarantee; no future closure/stock flags are read. Historical proxy construction is shared across training, evaluation and inference. Outputs are point estimates with no calibrated uncertainty or learned price elasticity.

## Evaluation and artifact lifecycle

Split unique calendar dates once across all SKUs: training ends at index `floor(0.70*N)-1`, validation ends at `floor(0.85*N)-1`, and test is the remainder. Actual dates and usable/excluded target counts are persisted. At least seven dates are required for nonempty partitions; sparse datasets can complete as a clearly labeled baseline-only model run with insufficient RF evaluation coverage.

For each of 1, 7 and 30 days, origins start one day before the evaluation partition and advance every seven days; only complete horizons inside that partition are evaluated. Thirty-day windows overlap, so daily metric counts include repeated target appearances at different origins. Each forecast uses the full recursive horizon without actual intermediate target updates. At a later origin, earlier actual observations may become history. Evaluation uses no future promotions or closures as predictors.

1. Fit the validation candidate on training dates only.
2. Evaluate validation and freeze selection: lower **paired seven-day uncensored SKU-day MAE**, requiring at least 30 paired observations across at least three origins. Baseline wins ties within 1e-9 and insufficient evidence.
3. Fit a separate test candidate on training plus validation; evaluate both methods on untouched test targets. Test scores cannot change selection.
4. Fit a separate final candidate through the dataset origin for inference, even if validation selected baseline. Its fitted forest remains a recorded candidate, but inference follows the selected method.

RF and baseline metrics use identical targets where the RF candidate actually produced a forest prediction and both have an observable uncensored/open target. RF fallback predictions are excluded from this paired comparison, with coverage/reasons reported. All products appear in per-product results, including insufficient-coverage entries. Report MAE, WAPE as a fraction, actual-unit totals, sample counts and exclusions. WAPE is null with `undefined_zero_actual_units` when its denominator is zero. Cumulative horizon-demand MAE is computed only for entirely uncensored/open paired windows; partial windows are excluded and counted. Require at least **three complete paired windows** overall or per product for a cumulative score, otherwise return `insufficient_coverage` with actual counts and null metrics. This is a minimum reporting guard, not a claim of statistical precision. These mechanical results on synthetic data do not establish real-world commercial performance.

Each training operation persists one final model record and two evaluation records, with roles, parent references, cutoffs, feature schema, parameters, seed, split/coverage/metrics, selection rationale, dependencies, duration, provenance and hashes. Controlled UUID artifact directories contain preprocessing together with the estimator and JSON metadata. Complete temporary directories are renamed into place before the success transaction. Failures leave a failed run rather than an apparently ready model. Only registered application-produced files under `RESTOCKIQ_ARTIFACT_DIR` (default `artifacts`) can be loaded, after integrity, feature-version and dependency checks. There is no arbitrary joblib upload endpoint. General model-file exchange and untrusted pickle loading are unsupported.

Phase 3 consumes these persisted demand estimates without retraining or changing the Phase 2 forecasting semantics.

## Phase 3 commands and API

No new dependencies are required. The existing `init-db` command or API startup adds `scenarios`, `recommendation_runs` and `recommendation_products` without replacing old tables or artifacts. Scenarios reject SQL updates/deletes as well as having no edit endpoints; create a new scenario for different assumptions. Recommendation products and their validated daily projections commit atomically with the run.

From PowerShell, use an existing dataset and forecast. For the verified demo the origin is 2026-10-15 and the forecast has 600 rows; actual IDs and results are recorded in `PHASE3_REPORT.md`.

```powershell
$datasetId = '<dataset-id>'
$forecastId = '<forecast-run-id>'
$base = .\.venv\Scripts\python.exe -m app.cli recommend $datasetId $forecastId | ConvertFrom-Json
$scenario = .\.venv\Scripts\python.exe -m app.cli create-scenario $datasetId $forecastId --name 'FX +10%, lead +7 days' --fx-change-pct 10 --extra-lead-days 7 --target-margin 0.20 | ConvertFrom-Json
$changed = .\.venv\Scripts\python.exe -m app.cli recommend $datasetId $forecastId --scenario-id $scenario.id | ConvertFrom-Json
.\.venv\Scripts\python.exe -m app.cli recommendation $base.recommendation_run_id --sku CHG-001
.\.venv\Scripts\python.exe -m app.cli compare-runs $base.recommendation_run_id $changed.recommendation_run_id
.\.venv\Scripts\python.exe -m app.cli export-recommendations $base.recommendation_run_id "artifacts/exports/recommendations-$($base.recommendation_run_id).csv"
.\.venv\Scripts\python.exe -m app.cli export-recommendations $changed.recommendation_run_id "artifacts/exports/recommendations-$($changed.recommendation_run_id).csv"
# Optional cap, disabled by default; price step defaults to PKR 5.
.\.venv\Scripts\python.exe -m app.cli recommend $datasetId $forecastId --price-step-pkr 5 --max-price-change-pct 10
.\.venv\Scripts\python.exe -m pytest -q --basetemp=runtime/phase3-acceptance-tests
```

Additional scenario flags: `--supplier-price-change-pct` (default 0), `--additional-freight-pkr` (default 0). FX/supplier percentages must be greater than -100 and at most 10,000, with up to four decimal places. Freight is nonnegative PKR cents, extra lead time is an integer 0–3,650, and margin override is a fraction in [0, 0.80]. Price-change caps are nonnegative percentages up to 10,000. These are input bounds, not claims that extreme scenarios are commercially plausible.

| Method | Route | Behavior |
| --- | --- | --- |
| POST | `/api/v1/scenarios` | 201; immutable scenario for a fixed dataset and forecast |
| GET | `/api/v1/scenarios/{id}` | Assumptions, references, synthetic/hypothetical notice |
| POST | `/api/v1/recommendations` | 201; atomic base or scenario analysis |
| GET | `/api/v1/recommendations/{run_id}` | Saved summary, effective settings, sources and all product results |
| GET | `/api/v1/recommendations/{run_id}/products/{sku}` | Saved product explanation and daily projection |
| GET | `/api/v1/recommendations/{run_id}/export` | Flat safe CSV; unavailable values remain blank |
| GET | `/api/v1/recommendations/{base_id}/compare/{scenario_run_id}` | Typed comparison on a common forecast and effective settings |

Scenario requests use the architecture's flat assumption fields:

```json
{
  "dataset_id": "<dataset-id>",
  "forecast_run_id": "<forecast-id>",
  "name": "FX and delay",
  "fx_change_pct": 10,
  "supplier_price_change_pct": 0,
  "additional_freight_pkr": 0,
  "extra_lead_days": 7,
  "target_margin_override": 0.20
}
```

A base recommendation request needs `dataset_id` and `forecast_run_id`; optionally supply `scenario_id` and `pricing: {"price_step_pkr": "5.00", "max_price_change_pct": "10"}`. Omit the cap, or set it to null, to disable it. CLI and API use the same services. Unknown references return 404, incompatible dataset/scenario/forecast/origin/model states return 409, invalid assumptions return 422, and unexpected failures return a safe error ID.

## Cost and price conventions

Select the latest relevant-supplier quote known by origin, breaking timestamp ties by greatest `quote_id`. Select the latest same-currency rate on or before origin; never use future observations. PKR uses the identity rate 1 without needing an FX row. Missing quotes or required rates make cost/pricing unavailable, without substituting historical purchase cost or blocking a valid stock projection. Old quotes, expired quotes and FX older than the configured three-day default remain clearly flagged indicative estimates. Freshness is measured at the explicit origin.

Landed cost = supplier unit price × PKR per quote-currency unit + per-unit freight + other per-unit charges. Scenario supplier-price changes multiply the quote; FX changes multiply foreign-currency rates only. Additional freight adds PKR per unit. Applying supplier and FX changes together compounds them. PKR quote prices are unaffected by an FX-only scenario. The unrounded Decimal cost and individual components are retained; landed cost rounds once to cents with `ROUND_HALF_UP`, then that same cents value drives pricing and cash. JSON money uses decimal strings.

Target price = cost / (1 − margin), rounded upward to the positive price-step grid. If a cap is enabled, find the smallest/largest positive grid prices inside current price ± the permitted percentage and clamp the rounded target to that interval. Rounding never exceeds the cap. If no grid price fits, retain the current price and report `ROUNDING_CONSTRAINT`. Emit `TARGET_MARGIN_UNMET` whenever the resulting price cannot meet the margin; also show achievable margin and signed/absolute price changes. Suggested prices can be below current prices because this rule targets a configured margin, not revenue optimization or customer willingness to pay.

Historical gross difference, current-price replacement margin, and replacement funding shortfall are separate fields. None is net profit. Scenario results are hypothetical, and demand stays unchanged by design.

## Inventory, quantities and timing

Use a rounded-up supplier median from at least five received orders with actual receipts known at origin (minimum one day), otherwise label the product default fallback. Extra scenario lead days affect this prospective lead and shift only eligible future expected receipts. Open orders expected on/before the end-of-day origin remain uncertain under every scenario. Received/cancelled orders never become future receipts. Recorded inventory position includes all open units; reliable planning position excludes uncertain overdue units.

Subtract reservations once. For each forecast day, add scheduled receipts before trading, fulfill up to available stock, record unmet demand, then close at nonnegative stock. Fractional quantities remain fractional. Unmet demand is lost, not backlogged. The projection includes recorded eligible purchase orders only, **not suggested new orders**. Depletion is the first zero closing balance in the supplied window; shortage is the first positive unmet demand. A null shortage date means only `no_shortage_within_horizon`.

Safety stock = ceil(mean demand across the entire supplied horizon × safety_days). Reorder point = ceil(next L forecast days' demand + safety stock). Alert when reliable planning position is at/below the point, or shortage precedes the next eligible receipt. Zero demand creates no positive purchase suggestion. A coverage candidate is computed independently: coverage = L + review_days; raw quantity = max(0, ceil(coverage demand + safety − available − eligible receipts within coverage)); if positive, round max(raw, MOQ) upward to pack size, otherwise use zero. `order_required` gates an immediate order; a positive coverage candidate without an alert is not an instruction to purchase today.

Cash = coverage candidate quantity × selected cents replacement cost, labeled estimated landed replenishment cash. Summary cash/quantity totals intentionally describe **all calculable coverage candidates**, not just products flagged for ordering now; `cash_scope`, included/excluded counts and `partial` make this explicit. Missing cash is not zero. Compare monetary totals on common calculable products, reporting exclusions. Summed unit costs/prices are unweighted diagnostic sums across SKUs, not invoice or revenue totals; margin aggregates are unweighted means.

If L or L+review exceeds the forecast, return `horizon_insufficient` only for the affected lead/quantity/timing calculations; keep valid daily projections and costs. Missing forecasts return unavailable inventory components rather than zero demand. Known unmet demand before a new order's arrival is reported only through the supplied horizon, with `partial_horizon` and a through-date when that interval is truncated. A new end-of-day order arrives at the start of origin+L, so pre-arrival unmet demand excludes that date. If later existing supply covers the coverage need and new quantity is zero, recommend expediting existing receipt IDs rather than inventing another order.

Future decisions occur at each projected day's end. Compare projected closing stock plus still-pending reliable orders with the next complete L-day window plus safety stock; orders received by that candidate date are not counted twice. Evaluate only through origin+H−L; return `order_now`, an estimated future decision, `no_trigger_within_supported_window`, or `horizon_insufficient`, with the last evaluated decision date. These dates and safety settings are planning heuristics, not guarantees.

The calculation version, effective product/settings values, forecast references, scenario assumptions, quote/rate sources, lead method, warning codes and deterministic explanations are persisted with each run. Later settings or forecasts do not rewrite prior results. CLI exports use exclusive filenames, metadata sidecars, formula-safe text, and cleanup after failed writes. The Phase 4 dashboard can read these saved results; no automatic purchasing or price publication is implemented.
