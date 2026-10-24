# RestockIQ UI redesign

Implemented 24 October 2026 using the existing Streamlit 1.45.1 / Plotly 6.1.2 environment. The UI and README still establish **RestockIQ** as the product name; the workspace directory is named MarginShift. No product rename, frontend migration, new dependency, or backend response change was introduced.

## Design and layout

- Central presentation helpers in `app/ui/common.py`: restrained teal/slate palette, white surfaces, subtle borders, system typography, tabular metric numerals, compact money with exact captions, native numeric table columns, readable dates, status labels, and a shared Plotly theme. The sidebar deliberately remains light so Streamlit 1.45.1 controls retain native contrast without deep CSS overrides. CSS uses component-level selectors and static presentation classes; interactive elements remain native.
- Shell: compact branding, five destinations, restrained API connection caption, compact provenance/date/horizon/scenario context. Dataset remains immediately accessible; model, forecast and exact saved analysis selectors are in Advanced analysis selection. Full IDs and assumptions are in technical details. Selected-run downloads retain their original API paths and precision.
- Overview: four decision cards, urgent products with saved backend explanations and functional product routes, searchable inventory decisions, category/urgency filters, collapsed secondary filters/columns, numeric prices/margins, and explicit coverage-versus-order-now interpretation. Partial and unavailable cash stays explicit.
- Products: searchable name/SKU selector, category/currency/urgency, four stock/pricing metrics, then Demand & Stock, Pricing & Costs, and Recommendation Details tabs. Forecast origin, observed/forecast line semantics, stock/receipt/unmet-demand charts, freshness warnings, timing, MOQ, pack effects, saved explanations and assumptions remain available. Local review actions and history are secondary and preserve request-UUID idempotency.
- Scenarios: narrow assumption form alongside wider saved results; preset selector, reset, saved scenario recovery and exact shared-run selection. Explicit Run scenario is the only draft submission. Results identify their immutable assumptions, compare base/scenario values, show adverse increases using inverse delta semantics, and format margin differences in percentage points. Coverage cash uses the backend common-product comparison; order-now cash is separately labeled with each run's inclusion counts.
- Model Performance: selected method and selection basis first, independent evaluation-context/horizon selectors, MAE/WAPE/count cards, compact comparison charts, numeric per-product results and a baseline-better filter. Split dates, exclusions, repeated appearances in overlapping windows, fallback coverage, and insufficient thirty-day cumulative coverage remain inspectable. No invented accuracy, uncertainty bands or trends.
- Data & Settings: five-step workflow with current prerequisites/completion, readable quality summaries, grouped six-file upload controls, missing-file guidance and detailed contracts. Advanced generation/training/forecast/pricing controls are collapsed. Existing two-second job polling uses actual API stage/progress and failures require explicit retry. Forecast, import, recommendation and scenario submissions show loading feedback.

## Files changed

| File | Change |
| --- | --- |
| `.streamlit/config.toml` | Native light palette and teal primary color |
| `app/ui/common.py` | Central tokens, typography, formatting, chart/table/presentation helpers and compact error details |
| `app/ui/streamlit_app.py` | Navigation, compact context, advanced run selectors, exact-run downloads |
| `app/ui/state.py` | Clear model-dependent state and stale job feedback when datasets change |
| `app/ui/overview.py` | Decision cards, priority routes, filters, sortable numeric decision table |
| `app/ui/product.py` | Product metrics, three detail tabs, charts and secondary review actions |
| `app/ui/scenario.py` | Assumption/result layout, explicit forms, snapshot/dirty labeling, unit-correct comparisons |
| `app/ui/evaluation.py` | Interpretable selection/evaluation summaries, charts, per-product and methodology views |
| `app/ui/data_settings.py` | Five-step setup, quality/upload usability, loading and job feedback |
| `tests/test_dashboard.py` | Updated navigation plus focused behavioral regression coverage |
| `README.md` | Current navigation, visual system and report reference |
| `docs/DEMO_SCRIPT.md` | Current page/control names and cash-scope explanation |
| `docs/UI_REDESIGN.md` | This implementation and verification handoff |

No business services, database models, API contracts, financial formulas or saved artifacts were edited. UI code continues to obtain business data exclusively through the API. Numeric conversions are presentation-only; exports preserve backend precision.

## Verification performed

- Compiled all UI modules using `.venv/Scripts/python.exe -m compileall -q app/ui`.
- Ran `.venv/Scripts/python.exe -m pytest -q --basetemp=runtime/redesign-final-tests -o faulthandler_timeout=90` using the existing virtual environment and isolated temporary test data: **119 passed, 1 warning in 36.88 seconds**. A subsequent caption-only change exposes the exact scenario coverage-cash value beside its compact card; compilation was checked afterward.
- Streamlit AppTest exercised all five pages without a browser: empty/disconnected states; setup through explicitly requested test-only generation/training/forecast/analysis; search and numeric table columns; product routing; duplicate local review suppression; preset selection without writes; scenario submission and unchanged reruns; reset/unapplied state; exact scenario selection; selected report paths; unchanged saved base results; dataset-dependent resets; Model Performance context and 30-day horizon changes; missing file validation; and unavailable forecast/cost states.
- AST inspection in a regression test rejects UI imports of services, database, ML, input data modules, SQLAlchemy or SQLite.
- Financial, inventory, scenario, ingestion, model and API tests remain in the full suite. Tests use their own temporary databases/artifacts. Real saved datasets, models and scenario results were not regenerated for styling.
- Executed the existing CLI API and UI startup commands on temporary loopback ports with a separate empty runtime. API `/api/v1/health` returned `status=ok`, timezone Asia/Karachi, reporting currency PKR. Streamlit `/_stcore/health` returned `ok`. Both process trees were stopped after the check. Scratch logs/result are under ignored `runtime/redesign-startup/`.
- Windows sandbox loopback restrictions stalled asyncio before API tests began; tests and startup checks succeeded with approved local socket access outside the sandbox. The final suite emits an existing Starlette/AnyIO deprecation warning.
- No browser automation, browser tools, Playwright, screenshots or browser runtime installation was used.

## Startup with the existing environment

Run each block in a separate PowerShell terminal. No reinstall or data preparation is necessary to inspect existing saved results.

```powershell
Set-Location 'C:\Users\J A\Desktop\MarginShift'
.\.venv\Scripts\python.exe -m app.cli serve --port 8000
```

```powershell
Set-Location 'C:\Users\J A\Desktop\MarginShift'
.\.venv\Scripts\python.exe -m app.cli ui --port 8501 --api-url http://127.0.0.1:8000
```

Open http://127.0.0.1:8501 manually. Use one API process and the existing local worker. Stop both terminals with Ctrl+C. If needed, change the API port in both commands. `prepare-demo` is intentionally not required or invoked by the redesign.

## Remaining limitations and manual review

Visual browser inspection remains for the user to perform. AppTest verifies execution and state, not pixel layout, responsive wrapping, focus visibility or perceived contrast. Charts are responsive through Streamlit containers and columns use the native responsive layout. The light sidebar is intentional. Native numeric columns prioritize sorting; exact grouped financial formatting is shown in detail metrics/captions and exports retain exact precision.

Streamlit forms batch edits in the client until submission. Every displayed scenario result is explicitly labeled with its saved assumptions, and notices beside the form/results state that form edits remain unapplied until Run scenario. A conditional mismatch warning appears when changed values reach Python, such as through presets/reset or another rerun. No unsupported JavaScript was added to detect individual unsent keystrokes. Repeated deliberate Run scenario submissions create new immutable runs, as before; ordinary reruns never write.

Manual five-page checklist:

1. **Overview:** select a known saved base; confirm provenance/date/horizon, compare four cards with saved results, check cash inclusion/partial labels, search and filter, sort optional monetary columns, and open a product from a priority action or Inspect product. Check the shortage interpretation note and report downloads.
2. **Products:** inspect all three tabs, forecast-origin divider, observed versus forecast lines, receipts/unmet demand, missing/stale input states, timing/coverage/MOQ/pack details, and secondary review controls. Check keyboard focus and small-screen wrapping.
3. **Scenarios:** inspect an existing saved scenario first; compare calculated assumptions and base/scenario values, cash scopes, units and percentage-point differences. Change/reset draft values and confirm old results remain labeled as saved/unapplied. Submit only when intentionally saving a new scenario. Check exact saved-run selection through Advanced analysis selection.
4. **Model Performance:** switch test/validation and 1/7/30-day horizons; compare MAE/WAPE/counts, filter baseline wins, inspect exclusions/splits and insufficient cumulative coverage. Confirm no accuracy claim appears.
5. **Data & Settings:** inspect the prerequisite workflow, quality report, six-file upload grouping and collapsed advanced controls. Check disabled prerequisites and missing-file guidance. Run jobs only when intentionally creating new data/models/results; confirm stage/progress and explicit failure recovery.
