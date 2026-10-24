# Phase 4 — Dashboard, integration and presentation readiness

Implemented and verified locally on Windows x64 / Python 3.12.14 on 16 October 2026. This is a local prototype. Visual browser verification remains unavailable as described below.

## 1. Implemented pages and capabilities

- **Overview:** saved-run decision cards; order-now landed cash with inclusion/exclusion counts and partial status; no-additional-action shortage and overdue-supply indicators; category/currency/urgency/missing-data filters; deterministic risk ordering; product navigation.
- **Product Explorer:** searchable products; origin-filtered observed sales, censored/closed markers and future demand; forecast-origin divider; stock, receipts and unmet demand; cost, FX, freight, price and margin details; backend explanations, complete inputs and receipt IDs; local reviewed/rejected history.
- **Scenario Lab:** explicit forms, bounds matching the backend, optional margin percentage conversion, two distinct presets, reset without deletion, saved scenarios, compatible base pricing, backend comparisons and unchanged-demand notice. Newly submitted scenarios remain recoverable if a later calculation fails.
- **Model Evaluation:** persisted selection evidence, split dates, separate validation/test results, 1/7/30-day horizons, baseline and Random Forest MAE/WAPE, counts/exclusions, per-product results, and insufficient cumulative coverage. No invented confidence bands.
- **Data & Settings:** generation and six-file atomic import, required/optional field descriptions, structured errors, quality/provenance, training jobs with two-second polling, forecasting, draft pricing settings, new immutable recommendation runs, and explicit Open Overview.
- Shared compatible selectors, provenance/origin/range/scenario context, dependent-state reset, selected-ID CSV/metadata downloads, explicit refresh, cached immutable reads, uncached jobs/history/discovery, structured connection failures and no automatic write retries.
- Additive SQLite review table with update/delete prevention, run/SKU foreign key, server timestamp and request UUID idempotency. Actions do not change business calculations.

## 2. Important files

| File | Purpose |
| --- | --- |
| `app/ui/streamlit_app.py` | App shell, context, navigation and downloads |
| `app/ui/api_client.py` | HTTP-only client, URL validation, timeouts and structured errors |
| `app/ui/{overview,product,scenario,evaluation,data_settings}.py` | Five page implementations |
| `app/ui/common.py`, `app/ui/state.py` | Formatting, charts, immutable read cache and state/request helpers |
| `app/api/routes/dashboard.py` | Typed discovery, history, overview, metadata, contracts and review endpoints |
| `app/services/dashboard.py` | Read projections and append-only review persistence |
| `app/db/models.py` | Additive review table and immutability triggers |
| `app/services/demo.py`, `app/cli.py` | Explicit `prepare-demo` and `ui` commands |
| `.streamlit/config.toml`, `.env.example` | Local theme and configuration |
| `pyproject.toml`, `requirements.lock` | Pinned Streamlit/Plotly/HTTP dependencies and complete installed lock |
| `tests/test_dashboard.py` | API, financial-scope, state and supported Streamlit interaction tests |
| `scripts/verify_live_ui.py` | Explicit AppTest acceptance run using real HTTP; appends a scenario/run and review |

The UI imports no database, training, pricing, inventory or hidden-evaluation modules. Existing forecasting and decision formulas were reused.

## 3. Exact setup and startup commands

From PowerShell, for a new installation:

```powershell
Set-Location 'C:\Users\J A\Desktop\MarginShift'
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock
.\.venv\Scripts\python.exe -m pip install --no-build-isolation --no-deps -e .
Copy-Item .env.example .env
.\.venv\Scripts\python.exe -m app.cli init-db
```

For the existing environment, use the two pip commands only; preserve an existing `.env`. If Windows sandbox TEMP permissions block pip, use a workspace temporary directory first:

```powershell
New-Item -ItemType Directory -Force runtime\tmp | Out-Null
$env:TEMP = (Resolve-Path runtime\tmp).Path
$env:TMP = $env:TEMP
```

Optional one-time preparation, separate from UI rendering:

```powershell
.\.venv\Scripts\python.exe -m app.cli prepare-demo
```

Terminal 1:

```powershell
Set-Location 'C:\Users\J A\Desktop\MarginShift'
.\.venv\Scripts\python.exe -m app.cli serve --port 8000
```

Terminal 2:

```powershell
Set-Location 'C:\Users\J A\Desktop\MarginShift'
.\.venv\Scripts\python.exe -m app.cli ui --port 8501 --api-url http://127.0.0.1:8000
```

Stop each with Ctrl+C. Use a single API worker/process against a database. For another port, change both `serve --port` and `ui --api-url`.

## 4. Local addresses and current process context

Standard startup uses API **http://127.0.0.1:8000**, docs **http://127.0.0.1:8000/docs**, UI **http://127.0.0.1:8501**.

Port 8000 already held an older repository API process during verification. The updated verification API was started on **http://127.0.0.1:8001**, and the running UI at **http://127.0.0.1:8501** points there. After identifying the old process and confirming zero queued/running jobs, the old API was stopped; only the updated API remains. To reproduce the current configuration, use `serve --port 8001` and `ui --api-url http://127.0.0.1:8001`. Do not point the new UI at an older API lacking discovery endpoints.

## 5. Verified demonstration references and outputs

All six supplied references were read and cross-checked through the local API; none are application constants.

| Entity | Preserved ID |
| --- | --- |
| Dataset | `229e8694-5e15-4809-b7c9-a59e21c26dad` |
| Final model | `48f53d36-a35d-4c4d-a4de-e4ed97794553` |
| Forecast | `9535e13b-04eb-4d1f-b740-13d0a35a4873` |
| Base recommendation | `edd2f0b6-0568-447c-ad64-8242af89a820` |
| Scenario | `c03f48fb-65de-4c84-ac5c-f847c7800d13` |
| Scenario recommendation | `68bd9829-89ed-4c7e-8cdb-f5fca5001017` |

Synthetic provenance; origin **2026-10-15**; horizon **2026-10-16–2026-11-14**, **600 rows / 20 products**. Saved scenario: FX +10%, supplier-price change 0%, additional freight PKR 0, extra lead 7 days, target-margin override 20%.

| Saved output | Base | Scenario |
| --- | ---: | ---: |
| Order-now products | 7 | 19 |
| Expedite products | 5 | 18 |
| Below target replacement margin | 1 | 0 |
| No-additional-action shortages | 19 | 20 |
| Uncertain overdue supply products | 4 | 4 |
| **Order-now landed cash (PKR)** | **1,178,851.36** | **3,867,941.79** |
| Cash included / excluded products | 7 / 0 | 19 / 0 |
| All-coverage-candidate cash, different scope (PKR) | 1,825,978.24 | 3,961,785.51 |

These are hypothetical software outputs on synthetic records, not measured business impact.

Seven-day validation MAE: baseline **3.964541**, RF **3.784623**, **838** paired targets. Test MAE: **4.196346 / 3.913922**, **860** targets. Test WAPE: **39.1841% / 36.5469%**. Validation chose RF; test assessed it afterward. Thirty-day cumulative coverage remains insufficient.

Explicit demo preparation reused a more recent existing final model `3aac863c-2f6c-4bfa-bcc1-21c0f1cb0f0f` and created missing compatible analysis records once: forecast `8b8866ad-a84b-4820-ae18-23ec6e3757a2`, base `2e5a0160-26f1-41c7-8129-70fd11e2ec38`, scenario `1eeb55e2-b581-4322-b778-3380d6001b6f`, scenario run `fed38655-c6cf-40ae-958b-54a292a4e05b`. A repeated preparation returned the same IDs without retraining. The preserved Phase 3 references remain selectable.

## 6. Actual verification

- Full regression: **117 passed**, one upstream Starlette/AnyIO deprecation warning, **82.90 seconds**. This includes all 109 preserved tests and 8 new tests.
- After final product display and required/optional contract labels, focused dashboard verification: **7 passed**, one intentionally deselected slow empty-workflow test. The empty workflow had already passed after its final state/navigation fix.
- Empty SQLite AppTest workflow actually generated 365 days / 5 SKUs, trained through the worker, polled completion, forecast 30 days, saved base recommendations and opened Overview. Only the four explicit POSTs occurred; rerunning did not duplicate them. This test uses real services through a TestClient transport, not canned financial results.
- API checks covered discovery scoping, operational-history origin filtering, missing products, typed contracts, metadata, review validation/persistence/idempotency, database append-only enforcement, unchanged run contents, order-now cash scope, missing/partial values, client errors and selected export IDs.
- Streamlit checks covered real selector-driven cross-dataset reset, form submissions, product navigation, review double-click suppression, scenario comparison, empty state and connection errors.
- **Live acceptance:** actual FastAPI and Streamlit processes ran together. `scripts/verify_live_ui.py` used supported AppTest with real HTTP against port 8001: selected the preserved model/forecast/base; opened products and both charts; appended a local review; submitted the Phase 3 preset; displayed backend comparison; opened evaluation including stored insufficient 30-day cumulative metrics; opened Data & Settings; fetched all four exports; switched to an unavailable API port and recovered. It confirmed base and forecast payloads unchanged afterward. The final such run created scenario recommendation `47e5312e-dc09-46c5-973f-3ce04715a20e` and reviewed `CBL-007`.
- CSV bytes were parsed and verified against the visible selected IDs. AppTest checked download controls and the actual HTTP export payloads; no browser file-save dialog was exercised.
- Streamlit health returned HTTP 200. Editable package installation, `compileall`, and `pip check` passed. No data/model-artifact deletion or historical-result rewrite was performed. Verification appended named scenario/review records; temporary test datasets are isolated from the working database.

Reproduce the full tests:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

The live check deliberately creates a scenario/run and review, so run it only when those verification records are wanted:

```powershell
.\.venv\Scripts\python.exe scripts\verify_live_ui.py --api-url http://127.0.0.1:8001 --model-id 48f53d36-a35d-4c4d-a4de-e4ed97794553 --forecast-id 9535e13b-04eb-4d1f-b740-13d0a35a4873 --base-run-id edd2f0b6-0568-447c-ad64-8242af89a820
```

## 7. Screenshots and visual verification

**No screenshots captured.** The browser automation runtime failed twice during initialization with `failed to write kernel assets: The system cannot find the path specified. (os error 3)`; the alternate Node automation runtime failed identically. Consequently, normal-laptop viewport layout, chart legibility in a rendered browser, native download behavior and browser interaction are **not visually verified**. AppTest/HTTP checks do not prove browser layout. No mock screenshots were produced.

## 8. Presentation handoff

- `docs/DEMO_SCRIPT.md` — approximately three-minute narration and exact preserved run context.
- `docs/PRESENTATION_OUTLINE.md` — six-slide outline using retrieved measurements.
- `docs/LIMITATIONS.md` — interpretation, synthetic evidence, finite horizons, evaluation coverage and local-operation limits.

No PowerPoint dependency or presentation generator was added.

## 9. Remaining limits

The visual/browser checks above remain outstanding because the automation runtime could not initialize. The implemented workflows pass API and supported Streamlit interaction checks. This does not establish production readiness, calibrated uncertainty, demand elasticity or real business savings. No public deployment, authentication, live integration, automatic purchasing, price publication, new model or further-phase feature was added.

## 10. Short walkthrough

1. Start the two processes, or use the already running UI/API addresses above. With an empty database, open Data & Settings; generate/import, inspect quality, train, forecast, generate base recommendations, then Open Overview.
2. For the preserved demonstration, select synthetic dataset `229e8694`, completed model `48f53d36`, forecast `9535e13b`, and base run `edd2f0b6` in the sidebar.
3. Read the cash inclusion scope and no-additional-action notice. Filter USD quotes and inspect a product, including reasons and receipt uncertainty. Optionally save a local review.
4. Open Scenario Lab, choose the preserved base, select saved scenario `c03f48fb` or explicitly submit the Phase 3 preset. Read the active assumptions and compare decisions.
5. Open Model Evaluation at 7 and 30 days. Download selected CSVs/metadata and use the three presentation documents for the handoff.
