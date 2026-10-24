# Scope and interpretation

- Local prototype, offline after dependencies are installed; no authentication or public deployment. Use one API process and one worker. Review history is local, append-only, and does not identify authenticated users.
- Demonstration dataset `229e8694-5e15-4809-b7c9-a59e21c26dad` is synthetic. Its costs, demand, rates and receipts do not establish real savings or market conditions. “Uploaded” provenance describes the import path, not independent verification of business data.
- Forecast `9535e13b-04eb-4d1f-b740-13d0a35a4873` has origin 2026-10-15, 600 rows, 20 SKUs and a 30-day horizon. A historical origin is not today. No calibrated intervals are supplied.
- Demand uses observed sales and stock/closure flags. Censored sales are not latent unconstrained demand. Forecast assumptions include future open days and inactive promotions. No new models or price elasticity were added.
- Model `48f53d36-a35d-4c4d-a4de-e4ed97794553` was selected on validation. Test 7-day MAE: baseline 4.1963 / Random Forest 3.9139 units/product-day; WAPE 39.18% / 36.55%. WAPE is error, not accuracy. Individual products may favor baseline. Thirty-day cumulative validation has only 2 complete windows against the minimum 3; test has none. Stored insufficient status must remain visible.
- Stock projections include eligible existing receipts and no additional recommended orders. Unmet demand is lost rather than backlogged; receipts precede trading. Overdue supply is uncertain. Safety stock is a heuristic, not a service-level guarantee.
- Finite horizons can leave quantities, decision dates and cash unavailable. Null is not zero. Partial totals report included/excluded products. The Overview cash card includes only `order_required=true` coverage candidates; diagnostic all-candidate cash is a different scope.
- Replacement quotes/rates have freshness warnings. Costs are held constant through the horizon. Suggested margins are gross replacement margins, not net profit; landed cash is not an invoice. No taxes, operating costs, financing or customer response are modeled.
- Scenarios are hypothetical cost/delivery assumptions. The Phase 3 demonstration changes FX +10%, lead +7 and margin to 20%; the margin override can affect suggested prices independently of FX. Saved base results remain unchanged.
- UI mutations are explicit and never auto-retried. Runs can still duplicate if a user deliberately submits a new calculation again. After a timeout, inspect discovery before retrying. Local review retries are idempotent by request UUID; identical accidental double clicks are suppressed in-session.
- CSV exports retain backend precision and spreadsheet-formula protection. The UI is an API client; it does not inspect hidden evaluation truth, model artifacts or SQLite.

These values were retrieved from persisted operational API responses during Phase 4 verification. See PHASE4_REPORT.md for actual checks and any browser verification limits.
