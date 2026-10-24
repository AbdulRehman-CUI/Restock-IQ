# Phase 2 completion report — ML Demand Forecasting

Phase 2 is implemented and verified. `ARCHITECTURE.md`, the Phase 2 request, README, and Phase 1 services were inspected before editing. The architecture and synthetic generator were preserved. Existing business tables were extended additively; Phase 1 tests still pass. No pricing, stock decision rules, scenarios, or dashboard were implemented.

## Delivered files

| Files | Responsibility |
| --- | --- |
| `app/ml/features.py` | Forward-only observed-demand proxies, historical lag/rolling features, missingness, cutoff eligibility, recursive feature state |
| `app/ml/baseline.py` | Constant last-seven-uncensored-observations average within 28 calendar days, sample counts and insufficiency |
| `app/ml/train.py` | Separate validation/test/final fits, frozen validation selection, run state and provenance |
| `app/ml/evaluate.py` | Shared chronological split, rolling origins, paired daily/cumulative metrics, exclusion counts, selection criterion |
| `app/ml/predict.py` | Recursive 1–30-day fractional demand forecasts, sparse/unseen fallback and per-product insufficiency |
| `app/ml/artifacts.py` | Atomic artifact directory publication, hashes, controlled-path and dependency checks before trusted joblib loading |
| `app/db/models.py`, `repositories.py` | Three new tables (`model_runs`, `forecast_runs`, `forecast_rows`), operational-only loading and persisted references |
| `app/services/jobs.py`, `forecasting.py` | Shared persistent training worker, restart recovery, forecast persistence and safe CSV/metadata export |
| `app/api/routes/ml.py`, `schemas/ml.py`, `app/cli.py` | Typed training/metrics/forecast endpoints, retrieval/export, executable commands |
| `tests/test_features.py`, `test_forecasting.py`, `test_ml_integration.py` | Leakage, recursion, eligibility, artifacts, metrics, persistence, API and lifecycle acceptance checks |
| `pyproject.toml`, `requirements.lock`, `.env.example`, `README.md` | Pinned dependencies, artifact-root configuration, reproducible instructions and semantics |

Pinned additions: scikit-learn 1.6.1, joblib 1.4.2, SciPy 1.15.3 and threadpoolctl 3.6.0. Existing dependency versions were preserved. Application operation remains offline after installation.

## Verified dataset and run references

The existing summary service confirmed dataset **`229e8694-5e15-4809-b7c9-a59e21c26dad`** exists, with 20 products, 7,300 sales rows, 365 days (2025-10-16–2026-10-15), 772 censored open SKU-days, and 120 closed SKU-days. No replacement demo was needed. The normal pipeline loads only product IDs/categories and observed sales/flags from SQLite; tests forbid reads under `evaluation_only` during training and inference.

Final inference model: **`48f53d36-a35d-4c4d-a4de-e4ed97794553`**.

Training job: `3a5930f5-38bb-4b90-900e-ac40d2781bce`.

Evaluation models:

- Validation candidate: `9cb773f6-83e4-47da-aecc-8f673e8dd411`, fitted through 2026-06-27.
- Test candidate: `b591f396-1640-4e1d-8ebf-7ee761bdcc34`, fitted through 2026-08-21.

Each has a separate registered UUID directory under `artifacts/models/`, containing `pipeline.joblib` and `metadata.json`. The final model is fitted through 2026-10-15 and cannot serve earlier origins. Evaluation artifacts serve the fixed RF candidate, so a later validation selection never determines earlier evaluation-origin predictions.

## Splits and usable observations

| Partition | Dates inclusive | SKU-days | Uncensored/open | Censored/open | Closed |
| --- | --- | ---: | ---: | ---: | ---: |
| Training | 2025-10-16–2026-06-27 | 5,100 | 4,494 | 526 | 80 |
| Validation | 2026-06-28–2026-08-21 | 1,100 | 950 | 130 | 20 |
| Test | 2026-08-22–2026-10-15 | 1,100 | 964 | 116 | 20 |

Feature-complete RF training rows: 3,981 for the validation fit; 4,931 for the test fit; **5,895 for the final fit**. All 20 products qualified at the relevant default cutoffs.

Eligibility uses at least 60 historical calendar days and 30 uncensored/open observations through the cutoff, with a documented conservative extra fit requirement of 30 feature-complete training targets per SKU. Targets with closed/censored observations are excluded. Missing initial proxy history stays missing; rows lacking complete required lag/rolling history are dropped rather than filled from the future.

## Actual evaluation and selection

Origins advance seven days, starting the day before each partition. Each complete horizon stays inside its evaluation partition; thirty-day windows overlap. Daily sample counts below include repeated target appearances across those overlapping origins. Forest and baseline are evaluated on identical eligible, uncensored/open SKU-days; all per-product results and exclusions are saved in model metadata and `runtime/phase2-metrics.json`.

| Partition / horizon | Paired SKU-days | Baseline MAE | RF MAE | Baseline WAPE | RF WAPE |
| --- | ---: | ---: | ---: | ---: | ---: |
| Validation / 1 day | 136 | 4.250 | 4.349 | 35.92% | 36.76% |
| Validation / 7 days | 838 | 3.965 | 3.785 | 38.04% | 36.32% |
| Validation / 30 days | 2,030 | 4.158 | 3.786 | 39.39% | 35.87% |
| Test / 1 day | 145 | 4.595 | 4.141 | 35.94% | 32.38% |
| Test / 7 days | 860 | 4.196 | 3.914 | 39.18% | 36.55% |
| Test / 30 days | 2,083 | 4.389 | 3.998 | 40.76% | 37.13% |

MAE units are units per SKU-day. Stored WAPE values are fractions; the table displays percentages.

**Selected method: Random Forest**, using only seven-day validation MAE: 3.784623 versus baseline 3.964541 across 838 paired SKU-days and seven origins. The criterion was defined before evaluation: at least 30 paired observations across three origins, lower MAE, baseline on ties within 1e-9 or insufficient evidence. Test metrics did not alter selection. Lifecycle tests explicitly supply opposite validation/test rankings and verify that selection stays frozen.

The forest does not win everywhere: the one-day validation baseline is better, and seven-day test MAE for CBL-012 is approximately 10.206 baseline versus 11.559 RF. Per-product regressions remain visible in the saved metrics.

Cumulative-demand MAE uses only completely uncensored/open paired windows and requires at least three complete windows:

| Partition / horizon | Complete windows | Excluded windows | Baseline cumulative MAE | RF cumulative MAE |
| --- | ---: | ---: | ---: | ---: |
| Validation / 7 days | 76 | 64 | 11.921 | 11.819 |
| Test / 7 days | 88 | 52 | 16.500 | 14.302 |
| Validation / 30 days | 2 | 78 | insufficient coverage | insufficient coverage |
| Test / 30 days | 0 | 80 | insufficient coverage | insufficient coverage |

No partially censored thirty-day window is presented as fully observed demand. WAPE is explicitly undefined when actual-unit totals are zero.

## Final forecast, export and timings

CLI forecast run: **`9535e13b-04eb-4d1f-b740-13d0a35a4873`**.

- Origin: 2026-10-15, end of day in Asia/Karachi.
- Forecast dates: **2026-10-16 through 2026-11-14**, 30 consecutive days.
- **600 persisted rows, 20 successful products**, all using selected RF, with no fallback/omitted products in this default run.
- CSV: `artifacts/exports/9535e13b-04eb-4d1f-b740-13d0a35a4873.csv`.
- Export metadata: the same path followed by `.metadata.json`.
- Measured training pipeline duration: **20.5156 seconds**, from loaded-data feature preparation through three fits/evaluation/artifact publication, excluding final DB completion overhead.
- Measured forecast duration: **2.4049 seconds**, including registered artifact/data loading and recursive inference, excluding final DB persistence overhead.

These are observed local measurements, not performance guarantees. Expected units stay fractional and nonnegative, independent of available inventory. Future store days are assumed open, future promotions inactive, and future availability features advance as uncensored/open; no observed future flags enter recursion. There is no price-response model or calibrated uncertainty interval.

## Commands executed and reproduction

The full default workflow used these CLI commands in PowerShell (with IDs resolved from actual prior output):

```powershell
.\.venv\Scripts\python.exe -m app.cli summary 229e8694-5e15-4809-b7c9-a59e21c26dad
.\.venv\Scripts\python.exe -m app.cli train 229e8694-5e15-4809-b7c9-a59e21c26dad --seed 42
.\.venv\Scripts\python.exe -m app.cli metrics 48f53d36-a35d-4c4d-a4de-e4ed97794553
.\.venv\Scripts\python.exe -m app.cli forecast 229e8694-5e15-4809-b7c9-a59e21c26dad 48f53d36-a35d-4c4d-a4de-e4ed97794553 --origin 2026-10-15 --horizon-days 30
.\.venv\Scripts\python.exe -m app.cli export-forecast 9535e13b-04eb-4d1f-b740-13d0a35a4873 artifacts/exports/9535e13b-04eb-4d1f-b740-13d0a35a4873.csv
.\.venv\Scripts\python.exe -m pytest -q --basetemp=runtime/phase2-verified-tests
.\.venv\Scripts\python.exe -m pip check
```

The export already exists: reproduction must use a new output filename, or create a new forecast and export its new ID. Training creates fresh model IDs rather than rewriting prior artifacts. Exact environment update/setup commands are in README; editable installation and `pip check` succeeded after dependency installation.

Captured workflow outputs: `runtime/phase2-training-result.json`, `phase2-metrics.json`, `phase2-forecast-result.json`, `phase2-export-result.json`. Dataset IDs are supplied to application commands and never hardcoded in application logic.

## Verification outcomes

**73 tests passed in 14.52 seconds**, including all 52 original Phase 1 tests. One upstream Starlette/AnyIO TestClient deprecation warning remains; there are no test failures. The sandbox blocks Windows asyncio loopback sockets, so API tests were run with the required execution permission.

Coverage includes future/target-day leakage, forward proxy construction, censoring exclusions, shared training/inference feature equivalence, baseline rules, fixed-origin recursive invariance, future flags, unseen/sparse/no-history products, deterministic fits, artifact reload equality, tamper/path rejection before deserialization, date/row/nonnegative checks, zero WAPE, censored cumulative windows and minimum sample counts, validation-only method selection, additive schema preservation, job conflicts/failures/restarts, broken artifacts, API responses, and export cleanup/formula escaping.

A real temporary Uvicorn process also passed HTTP checks: health 200, persisted training job 200, metrics 200, thirty-day forecast 201 with 600 rows, CSV export 200, historical-origin rejection 409, invalid horizon 422, and unknown training dataset 404. The API forecast run was `49a01d93-3eab-4a42-90cc-59e5c51d5e11`. The temporary server was stopped. Results/logs: `runtime/phase2-http-result.json`, `runtime/phase2-http-smoke.log`. `pip check` returned `No broken requirements found`.

## Limitations and Phase 3 handoff

There are no unresolved functional failures. These are synthetic-data mechanics, not proof of real retailer accuracy. Metrics exclude unobservable demand targets; the model cannot recover hidden demand from sales alone. Thirty-day cumulative evaluation lacks sufficient complete windows. Global seven-day selection can underperform for individual products or other horizons. Proxies and future availability/open-store assumptions are heuristics. There is no learned price elasticity, inventory cap, uncertainty interval, live integration, production authentication, or multi-process job orchestration. Unregistered artifact directories can remain after a hard interruption and are ignored.

Phase 3 receives stable persisted daily **unconstrained expected demand**, dataset/model/forecast IDs, origin/horizon, methods, fallback reasons, per-SKU insufficiency, provenance, and documented assumptions. It can implement replacement costs and time-phased stock/reorder decisions separately. It must not reinterpret a price scenario as an estimated demand response or treat these point forecasts as stock guarantees.
