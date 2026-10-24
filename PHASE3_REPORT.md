# Phase 3 completion — replacement costs, prices, inventory and scenarios

Implemented the requested decision engine after reading the architecture, README, Phase 3 request and existing services. No new dependency, model, dashboard, live integration, purchase submission or price publication was added. Existing datasets, model files and forecasts were preserved.

## Implementation

| File | Responsibility |
| --- | --- |
| `app/services/costs.py` | Origin-aware quote/FX selection, deterministic ties, Decimal landed costs and freshness |
| `app/services/pricing.py` | Target-margin prices, upward grid rounding, percentage caps and truthful margin warnings |
| `app/services/inventory.py` | Supplier median/fallback leads, uncertain supply, daily lost-demand stock projections, coverage/MOQ/pack quantities, urgency and future decision timing |
| `app/services/scenarios.py` | Validated immutable assumptions tied to a fixed dataset and forecast |
| `app/services/recommendations.py` | Reference/calendar compatibility, component composition, deterministic explanations, atomic persistence and paired comparisons |
| `app/services/exports.py` | Formula-safe flat CSV, missing-value statuses, metadata sidecars and exclusive/cleanup-safe writes |
| `app/db/models.py`, `repositories.py` | Three additive tables, immutable scenario triggers, operational-only input loading |
| `app/api/schemas/decisions.py`, `routes/decisions.py`, `app/cli.py` | Typed routes and shared-service CLI commands |
| `tests/test_costs_pricing.py`, `test_inventory.py`, `test_scenarios.py` | Financial, timing, scenario, persistence, export and API acceptance checks |

Calculation version: **`decisions-v1`**. Money uses decimal strings. Unrounded supplier/FX components are retained; landed PKR cost rounds once to cents with `ROUND_HALF_UP`, then that same cost drives prices and cash. Forecast quantities remain fractional. Daily stock projections are validated structured payloads saved with each product result.

## Verified references

The existing services confirmed these original Phase 2 references, without regeneration or retraining:

- Dataset: **`229e8694-5e15-4809-b7c9-a59e21c26dad`**.
- Completed final model: **`48f53d36-a35d-4c4d-a4de-e4ed97794553`**, selected method Random Forest.
- Forecast: **`9535e13b-04eb-4d1f-b740-13d0a35a4873`**.
- Origin: **2026-10-15**, end of business day, Asia/Karachi.
- Forecast: 600 rows, 20 products, 2026-10-16 through 2026-11-14.

New demonstration references:

- Base recommendation run: **`edd2f0b6-0568-447c-ad64-8242af89a820`**.
- Scenario: **`c03f48fb-65de-4c84-ac5c-f847c7800d13`**.
- Scenario recommendation run: **`68bd9829-89ed-4c7e-8cdb-f5fca5001017`**.

The scenario applies FX +10%, prospective/eligible receipt delay +7 days, supplier price +0%, additional freight PKR 0, and target-margin override 0.20. Supplier and FX percentages convert once; FX affects USD quotes only. Overdue orders stay uncertain. Assumptions and base records are immutable through the application; SQLite also rejects updates/deletes of scenarios.

## Actual demonstration results

| Metric | Base | Scenario |
| --- | ---: | ---: |
| Products analyzed | 20 | 20 |
| Cost and inventory components available | 20 | 20 |
| Products requiring an order now | 7 | 19 |
| Products requiring expedited supply | 5 | 18 |
| Products with projected shortage within 30 days | 19 | 20 |
| Products with uncertain overdue supply | 4 | 4 |
| Quantity calculations with insufficient horizon | 0 | 0 |
| Expected unmet demand over the horizon | 2,620.5105 units | 2,706.7306 units |
| Sum of coverage candidate quantities | 1,174 units | 2,413 units |
| Estimated landed cash for all coverage candidates | PKR 1,825,978.24 | PKR 3,961,785.51 |
| Sum of per-SKU unit replacement costs | PKR 39,722.30 | PKR 40,923.26 |
| Sum of per-SKU suggested unit prices | PKR 50,715.00 | PKR 51,205.00 |
| Unweighted current-price replacement margin | 27.2001% | 24.8870% |

All these totals include 20 products and exclude none; cash is not partial in this demonstration. The cost/price sums are diagnostic sums of unit values, not invoice totals or revenue. **Cash and quantity totals describe all calculable coverage candidates**, including products that do not yet trigger an order. Use the separate `order_required` flag and timing before acting; the persisted summary includes this scope explicitly.

Projections contain only already-recorded eligible receipts, with no insertion of the suggested orders. Thus the shortage counts describe estimated risk if no additional action is taken, not the outcome after implementing recommendations. Unmet demand is lost, not a backlog. All 600 expected-demand values are unchanged between the base and scenario.

## Inspected products and explanations

| Product | Currency | Base/scenario unit cost | Base/scenario suggested price | Effective lead | Coverage quantity |
| --- | --- | --- | --- | --- | --- |
| CHG-001 | USD | PKR 1,587.16 → 1,741.07 | PKR 1,985 → 2,180 | 11 → 18 days | 180 → 252 |
| CBL-002 | PKR | PKR 260.61 → 260.61 | PKR 330 → 330 | 9 → 16 days | 57 → 192 |
| CBL-007 | USD | PKR 297.96 → 319.36 | PKR 385 → 400 | 10 → 17 days | 166 → 268 |

- **CHG-001:** USD acquisition cost responds to FX while fixed PKR charges remain unchanged. Its 70 overdue units are excluded from reliable planning in both runs. First projected shortage remains 2026-10-16; a normal new order cannot prevent that early unmet demand, so expedite warnings remain explicit.
- **CBL-002:** FX alone does not change its PKR quote or landed cost. Delayed eligible supply and longer prospective lead time increase the coverage quantity and move its estimated first shortage from 2026-11-02 to 2026-10-23. Demand is identical. The global 20% margin override can affect PKR price suggestions independently of FX; this product's price remains PKR 330 after step rounding.
- **CBL-007:** Its 124 overdue units remain uncertain rather than becoming reliable after adding seven scenario days. First projected shortage is 2026-10-16 in both runs. Recorded and reliable inventory positions differ visibly; the result identifies missing near-term supply and expediting urgency.

Product JSON inspections: `runtime/phase3-product-usd.json`, `phase3-product-pkr.json`, `phase3-product-overdue.json`. Each saved product includes quote ID/time, FX source/date/components, product/inventory inputs, forecast references/method/fallbacks, lead source/count, daily projections, quantity/cash statuses, reason codes and deterministic plain-language explanations.

## Missing-input and horizon behavior

The default data has no missing/stale selected quotes or FX rates and no insufficient quantity horizon under this scenario. Isolated fixtures verify the absent cases without changing the demo:

- Missing quote or FX: cost and pricing unavailable, with no historical-cost substitution; valid stock projections and calculable quantities remain available.
- Expired/stale inputs: indicative costs with explicit freshness warnings at origin.
- Missing forecast: unavailable inventory/quantity rather than zero demand.
- Five-day forecast with seven-day lead: daily projection remains valid; lead/quantity/timing report insufficient horizon. Known pre-arrival unmet demand is labeled `partial_horizon` with its through-date instead of implying complete coverage.
- Coverage exceeding horizon: quantity/cash remain unavailable, without extrapolation. Cost/pricing can still be available.
- Partial costs: cash totals explicitly identify included/excluded products and are partial; no available contributions produce null rather than fabricated zero.
- Distant reliable receipts: do not conceal an earlier shortage; if later receipts already cover the coverage need and new quantity is zero, expose receipt IDs to expedite rather than suggest a duplicate order.

## Commands executed / reproduction

From this project directory in PowerShell:

```powershell
$datasetId = '229e8694-5e15-4809-b7c9-a59e21c26dad'
$forecastId = '9535e13b-04eb-4d1f-b740-13d0a35a4873'
$base = .\.venv\Scripts\python.exe -m app.cli recommend $datasetId $forecastId | ConvertFrom-Json
$scenario = .\.venv\Scripts\python.exe -m app.cli create-scenario $datasetId $forecastId --name 'FX +10%, lead +7 days' --fx-change-pct 10 --extra-lead-days 7 --target-margin 0.20 | ConvertFrom-Json
$changed = .\.venv\Scripts\python.exe -m app.cli recommend $datasetId $forecastId --scenario-id $scenario.id | ConvertFrom-Json
.\.venv\Scripts\python.exe -m app.cli compare-runs $base.recommendation_run_id $changed.recommendation_run_id
.\.venv\Scripts\python.exe -m app.cli recommendation $base.recommendation_run_id --sku CHG-001
.\.venv\Scripts\python.exe -m app.cli recommendation $base.recommendation_run_id --sku CBL-002
.\.venv\Scripts\python.exe -m app.cli recommendation $base.recommendation_run_id --sku CBL-007
.\.venv\Scripts\python.exe -m app.cli export-recommendations $base.recommendation_run_id "artifacts/exports/recommendations-$($base.recommendation_run_id).csv"
.\.venv\Scripts\python.exe -m app.cli export-recommendations $changed.recommendation_run_id "artifacts/exports/recommendations-$($changed.recommendation_run_id).csv"
.\.venv\Scripts\python.exe -m pytest -q --basetemp=runtime/phase3-acceptance-tests
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m app.cli serve
```

Reproduction creates fresh scenario/run IDs. Existing saved runs remain inspectable with `recommendation <run-id>`. Exports never overwrite existing filenames. README documents every new route, request shape, input bounds, price-cap/rounding conventions, calendar conventions, and effective settings.

## Export paths

Both files contain 20 product rows plus the header, with matching metadata sidecars:

- `artifacts/exports/recommendations-edd2f0b6-0568-447c-ad64-8242af89a820.csv`.
- `artifacts/exports/recommendations-68bd9829-89ed-4c7e-8cdb-f5fca5001017.csv`.
- Each sidecar is the CSV path followed by `.metadata.json`, retaining references, assumptions, summary and effective settings.

Flat CSVs include provenance/scenario, current/suggested prices, margins, lead time, shortage/depletion/reorder dates, quantity/cash, component statuses, warning codes and all dataset/model/forecast/recommendation references. Missing money is blank, not zero. Text reuses the same formula-injection protection as Phase 2; valid negative numerical margins remain numerical.

Captured command outputs: `runtime/phase3-base.json`, `phase3-scenario.json`, `phase3-scenario-run.json`, `phase3-comparison.json`, `phase3-base-export.json`, `phase3-scenario-export.json`.

## Verification

**109 tests passed in 15.96 seconds**, including all 73 prior tests, with one upstream Starlette/AnyIO TestClient deprecation warning and no failures. Checks cover:

- Architecture financial examples (1,300/1,380 cost, 1,725 target price, FX 280×1.10=308), PKR isolation, compounding, deterministic/future quote/rate selection and stale/missing inputs.
- Price caps in both directions, grid alignment, impossible grids, zero cost, margin warnings and invalid scenario assumptions.
- Reorder point 90, depletion day 10/shortage day 11, receipts before trading, reservations once, order-status exclusions, overdue uncertainty, median lead rules, fractional conservation, zero demand, MOQ/packs, lost demand and finite forecast validation.
- Future reorder dates without duplicate receipts, horizon insufficiency, zero-change equivalence, scenario isolation, repeated business-output equality, partial summaries, atomic rollback and immutable scenarios.
- Retrieval/export roundtrips, formula-safe names, overwrite/failure cleanup, compatibility errors, invalid assumptions, sanitized unexpected errors and original Phase 1/2 behavior.

Actual temporary Uvicorn HTTP checks passed on port 8767: scenario POST 201/GET 200, recommendation POST 201/GET 200, three product GETs 200, comparison 200, 20-row CSV export 200, invalid assumptions 422, unknown reference 404 and incompatible comparison 409. A real HTTP zero-change scenario returned exactly the base product calculations. The temporary server was stopped. Results/logs: `runtime/phase3-http-result.json`, `phase3-http-smoke.log`.

Preservation checks independently verified the original dataset's six row counts, both original model file integrity hashes, and equality of the complete saved Phase 2 forecast against its original JSON output. `pip check` returned `No broken requirements found`. No dependency installation or model retraining was needed for Phase 3.

## Limitations and Phase 4 handoff

No unresolved functional failures remain. This is still a local single-process application without authentication or live suppliers. Lead medians, safety stock, and future timing are heuristics. Forecasts are point estimates, scenario FX/cost/delay inputs are assumptions, and the earlier thirty-day cumulative evaluation had insufficient complete observed windows. Suggested prices do not establish customer willingness to pay; there is no price elasticity or demand change. Synthetic results are not evidence of real business savings, preserved margins, revenue improvement or guaranteed stock availability.

Phase 4 can build the dashboard against saved typed recommendations, product projections/explanations, immutable scenario details, paired comparisons and safe CSV exports. Show synthetic/hypothetical labels, origin/horizon limits, unavailable/indicative/partial states, demand independence, urgency, and the difference between coverage quantities and orders required now. Acknowledgements remain an optional future local audit action; no external purchase or price update is implied. Dashboard and presentation work were not started.
