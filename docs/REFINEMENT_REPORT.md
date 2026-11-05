# MarginShift refinement — 2–3 November 2026

## Assessment and resulting workflow

The existing application already had sound analytical boundaries, native UI components, local review idempotency, and broad calculation tests. The largest user-facing costs were the dataset → model → forecast → recommendation selection chain, no direct demo entry, setup controls displayed together, product tabs executing all views, unconditional report downloads, and full saved-run loading for narrow reads.

The implementation now provides:

- **Explore demo** resolves a compatible saved synthetic base and its dependencies without training or writes. Missing preparation opens explicit guided setup. No database IDs are hardcoded.
- **Saved analysis** presents origin, date, outlook, base/scenario, and a readable saved ordinal. Advanced dataset/model/forecast selection and full technical references remain accessible. Compatibility is resolved server-side with matching dataset/model/forecast/date/horizon and scenario relationships.
- Five consistent destinations, compact shared styling, four decision cards, prioritized backend explanations, relevant next actions/dates/quantities, searchable products, and optional table columns.
- Product identity/status and a recommendation before metrics; conditional demand/stock, cost/pricing, and explanation views; practical missing-input guidance; opt-in local review controls with fresh history.
- Explicit scenario forms, FX-only/delay-only/combined presets without silent margin overrides, assumption summaries, affected-product filtering (including availability/urgency changes), same-forecast/settings comparisons, unchanged-demand disclosure, and saved-assumption recovery after calculation failure.
- One setup step at a time, six contract-derived blank CSV templates, atomic validation with correction guidance, tracked jobs without estimated percentages, forecast reuse, and exact completed-step recovery. Identical successful scenario/analysis submissions reuse their result within the same UI session.
- Report preparation fetches only the requested report. Dataset transitions clear dependent results/drafts. Temporary API failures retain the selected context; changing API address clears it. Mutable discovery/status/history stay fresh.

## Performance evidence

The initial baseline was recorded before optimization. Matched comparisons then used the original source from commit `fa7dc402ef28ffaaab69ceb7ce4edfbeaaba1273` and the refined source, each with a separate SQLite backup of the same existing saved synthetic dataset/base analysis. No tests or training ran concurrently with these matched measurements. Each batch timed a first request after discovery and nine repeat requests through FastAPI TestClient. Discovery had already warmed SQLite; “first” is not a cold operating-system disk benchmark. AppTest request counts separately compare empty versus populated Streamlit data caches. These are API/test-harness results, **not browser rendering measurements**.

| Operation, ms | Before batch 1 | After batch 1 | Before batch 2 | After batch 2 |
| --- | ---: | ---: | ---: | ---: |
| Overview summary, first request | 8.38 | 5.86 | 18.55 | 8.15 |
| Overview summary, repeat median | 16.41 | 3.63 | 17.44 | 10.32 |
| Product detail, first request | 14.17 | 3.22 | 19.24 | 8.16 |
| Product detail, repeat median | 12.97 | 2.33 | 16.87 | 6.49 |
| Scenario calculation, first request | 132.16 | 76.70 | 134.30 | 190.80 |
| Scenario calculation, repeat median | 219.11 | 84.18 | 216.21 | 272.94 |

Overview summary and product detail improved in both batches. Overview now projects only summary inputs from saved JSON; product detail uses the existing `(run_id, sku)` primary key instead of loading every product. Metadata skips forecast rows/product results. Scenario timing did not show a reliable improvement, so no scenario-calculation speedup is claimed. No financial/forecasting algorithm, validation, model, horizon, or precision was weakened.

| AppTest interaction on the same saved base | Before | After |
| --- | ---: | ---: |
| First Overview API requests | 11 | 5 |
| First Overview response bytes, total | 545,725 | 84,384 |
| Warm Overview API requests | 4 | 2 |
| Warm Overview response bytes, total | 5,136 | 7,627 |
| First Products navigation requests | 6 | 6 |
| First Model Performance navigation requests | 5 | 3 |
| Unrequested export/download requests on first Overview | 4 | 0 |

Warm discovery transfers slightly more data because the joined analysis catalog carries readable provenance and dependency information. First product navigation remains six requests, but fetches only the selected product/forecast/history and defers audit history. Switching an already-loaded product to Pricing & Costs makes only the two fresh discovery calls, with no history request or chart construction. Model Performance does not load product histories, chart rows, or exports. No pagination or speculative indexes were added for the small demo; existing primary keys cover the targeted detail/history queries.

Raw measurements and request paths: [REFINEMENT_BENCHMARKS.json](REFINEMENT_BENCHMARKS.json). Reproduce current measurements with a fresh output label:

```powershell
.\.venv\Scripts\python.exe scripts/benchmark_refinement.py another-check
```

The benchmark refuses to replace a prior output database. It uses an isolated backup and creates scenarios/recommendations only in that copy. `--source` can select a separate original-source snapshot for comparison. SHA-256 fingerprints match across all four batches for **73 existing database/data/artifact files**. Base and scenario product-output hashes also match across versions. Forecast rows, prices, money, inventory calculations, and unavailable states are preserved.

## Changed files

| Files | Purpose |
| --- | --- |
| `app/ui/streamlit_app.py`, `state.py` | Analysis/demo selection, explicit exports, context/draft persistence and dependency resets |
| `app/ui/data_settings.py` | Guided setup, templates, jobs, forecast/analysis recovery and duplicate suppression |
| `app/ui/overview.py`, `product.py` | Priorities, concise recommendations, targeted product views and missing-input guidance |
| `app/ui/scenario.py`, `evaluation.py` | Explicit assumptions, presets, meaningful comparisons, retry/reuse, plain-language evidence |
| `app/ui/common.py`, `api_client.py` | Shared correction/error guidance and connection recovery |
| `app/services/dashboard.py`, `app/api/routes/dashboard.py` | Joined compatible analysis discovery, compact workspace/catalog, narrow summary/metadata reads and CSV templates |
| `app/services/recommendations.py`, `forecasting.py` | Direct selected-product read and optional metadata/selected-forecast-row reads |
| `app/services/jobs.py` | Correct recovery wording for interrupted generation or training |
| `tests/test_dashboard.py`, `test_refinement.py` | Supported AppTest/API regressions for the changed workflow and recovery boundaries |
| `scripts/benchmark_refinement.py` | Repeatable isolated API/AppTest benchmark and preservation checks |
| `README.md`, `docs/USER_GUIDE.md`, `DEMO_SCRIPT.md`, `UI_REDESIGN.md`, this report and benchmark JSON | Startup, current workflow, evidence, historical-report notice, and review handoff |

The pre-existing `.gitignore` edit was left as found. No existing data/artifact, dependency lock, database schema, calculation formula, model-fitting/evaluation module, or deployment configuration was changed.

## Verification

The original suite passed **119 tests** before changes. The final suite covers the original financial, inventory, scenario, forecasting/leakage, ingestion/atomicity, validation, artifact-integrity, and API behaviors plus eight refinement regressions. Supported Streamlit AppTest exercises all five pages, normal/empty/unavailable states, demo reuse, guided setup, exact scenario selection, dataset resets, selected report paths, no writes on ordinary reruns, identical-form suppression, temporary API failure/reconnect, completed-forecast recovery, saved-scenario retry, cache keys across API/dataset/run/product inputs, fresh mutable reads, and terminal polling. AST checks preserve the API-only UI boundary.

Final full verification on 3 November: **127 passed, 3 warnings in 226.35 seconds**:

```powershell
.\.venv\Scripts\python.exe -m pytest -q --basetemp=runtime/refinement-resumed-verified-tests -o cache_dir=runtime/pytest-cache
```

The final focused checks also passed: **17 passed, 1 deselected in 43.17 seconds**, covering the latest forecast-context reset, saved evaluation-artifact discovery, and availability-aware scenario filtering:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_refinement.py tests/test_dashboard.py -k 'not empty_database_to_overview' --basetemp=runtime/refinement-resumed-focus-tests -o cache_dir=runtime/pytest-cache
```

The warnings comprise one existing Starlette/AnyIO deprecation and two denied pytest-cache writes in this managed environment; test data and assertions completed successfully. Local loopback access was needed because the resumed sandbox stalled Python event-loop initialization. The shared-process AppTest harness waits on the real API job status while training, avoiding concurrent Streamlit `sys.path` changes during package-metadata discovery. It still verifies UI job submission, actual completion, and terminal polling; model/artifact integrity checks remain unchanged. Production continues to run the API and UI in separate processes.

Compilation and `git diff --check` passed. Actual CLI startup on isolated local ports returned API health `status=ok`, timezone `Asia/Karachi`, currency `PKR`, and Streamlit health `ok`. The startup helper's process-tree cleanup timed out; the known temporary API/UI processes were subsequently stopped directly, and their ports were confirmed closed. The existing application database was not used for these startup writes.

The final preservation check again matched all 73 saved-file fingerprints. No browser tools, browser automation, Playwright, screenshots, or model retraining on existing saved data were used.

## Startup and remaining limitations

Use separate PowerShell terminals from the repository:

```powershell
Set-Location 'C:\Users\J A\Desktop\MarginShift'
.\.venv\Scripts\python.exe -m app.cli serve --port 8000
```

```powershell
Set-Location 'C:\Users\J A\Desktop\MarginShift'
.\.venv\Scripts\python.exe -m app.cli ui --port 8501 --api-url http://127.0.0.1:8000
```

Open http://127.0.0.1:8501 and select Explore demo. Existing dependencies and artifacts suffice. Stop both terminals with Ctrl+C. Use one API process per database. Optional `prepare-demo` is only needed when prerequisites are missing.

Visual browser review remains manual. AppTest verifies state and execution, not pixel layout or browser-perceived speed. Native Streamlit forms submit edits together, so assumption summaries reflect the last values sent to Python/preset; persistent unapplied-change notices and saved-result assumptions identify this limit. UI duplicate suppression is session-local; timed-out writes still require checking saved discovery results before retry, and API writes are not generally idempotent. Discovery is intentionally uncached and scans the small saved catalog. Scenario calculation and training are still explicit local computations. No accuracy, confidence intervals, commercial savings, automatic purchasing, or price publication is implied.

## Manual visual review checklist

1. **Entry/selection:** Explore demo opens a base analysis without choosing IDs; check synthetic label, forecast date range, readable saved choices, and advanced exact references.
2. **Overview:** check laptop-width card/table wrapping, priority text/date/quantity, cash exclusions/scope, search/filter/sorting, product links, and explicit report preparation/download.
3. **Products:** inspect all three product views, demand divider/markers, stock/receipt curves, missing-input guidance, pricing precision, explanation details, and opt-in local review history.
4. **Scenarios:** check FX-only/delay-only/combined presets, units and explicit margin override; submit intentionally, compare affected products/cash scopes; reset while preserving saved results and verify unapplied-change notices.
5. **Evidence/setup/recovery:** inspect validation versus test and 30-day coverage, blank templates and validation errors, completed/next setup steps, disabled busy actions, and reconnect after temporarily stopping the API. Check keyboard focus and common laptop widths.
