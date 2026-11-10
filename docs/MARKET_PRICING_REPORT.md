# Market & Pricing completion report

Date: 7 November 2026. Branch: `codex/live-fx-replacement-pricing`, based on remote main `64ee2ff`. Working checkout: `D:\MarginShift` (the chat's original C: path was stale). Changes are local and reviewable; nothing was merged, pushed or deployed.

## Delivered

| Area | Result |
| --- | --- |
| Daily FX | ExchangeRate-API provider protocol/adapter, official endpoint and PKR documentation checked, exact parsing, bounded transient retries, Retry-After, persistent immutable observations, independent fetch timestamps, cross-instance lease/cooldown and visible failure/staleness |
| Conversion | Versioned reference-plus-spread and manual bank selling modes; no double spread; source/identity/observation/expiry/notes; fixed fees allocated only across an explicitly selected SKU batch |
| Supplier quotes | Existing SKU/supplier/pack relationship validation, USD/PKR quotes, tiers, explicit acquisition charges, version history and linked unchanged-price reconfirmation |
| Price-list import | Blank downloadable CSV template; CSV and one-sheet XLSX; bounded chunk upload, preview/findings, explicit hash-bound confirmation, atomic rollback and retry receipts; formulas/macros/external links rejected; expiry/cleanup policy |
| Pricing engine | Shared landed-cost and existing rounding/cap functions; exact replacement costs, historical comparison, gross margin, shortfall, minimum funding price, target/suggested prices, achievable margin, changes and input assumptions |
| Historical isolation | Separate current-pricing tables/snapshots; historical dataset FX/quotes, recommendation results, forecasts and inventory dates remain unchanged |
| Alerts/comparison | Missing/expired/stale/unconfirmed inputs, refresh failure, funding/margin/cap warnings, material FX-only movements, combined-input explanations and source-linked comparisons |
| Hosted UX | Market & Pricing navigation; quote/rate maintenance, import preview, assessment table/detail/history, exact exports, error/loading/empty states, disabled duplicate submissions and draft-preserving retry handlers |
| API/security | Typed contracts; existing authenticated workspace and origin protection; server-side fixed provider URL; independent bearer-protected scheduled endpoint; bounded histories |
| Persistence | Additive migration 1 with version ledger, indexes and SQLite/PostgreSQL immutability triggers; existing records preserved; rollback limitations documented |
| Local workflows | Original Streamlit, six-file import, synthetic generation, demand modeling, inventory, scenarios, reviews and model storage retained and regression-tested |

See [feature architecture and operating guide](MARKET_PRICING.md), [template](templates/supplier-price-list-template.csv) and [deployment setup](../DEPLOYMENT.md).

## Executed verification

- **175 pytest tests passed**, including the entire existing suite and the new pricing tests, with both optional real PostgreSQL tests enabled. Final full run: 79.54 seconds. One pre-existing Starlette/AnyIO deprecation warning; no test failures/skips in that run.
- **43 focused pricing/PostgreSQL tests passed** after the final fee and deduplication changes. Provider cases use deterministic recorded/mocked HTTP responses, including missing PKR, malformed timestamps, timeouts and rate limiting.
- **3 Node component/handler checks passed**: HTML escaping/required quote inputs, manual mode disabling/removing spread, and stable idempotency keys across recoverable quote retries. `node --check` passed for both frontend scripts.
- A final targeted authenticated API test passed after strengthening the assessment response contract; it verifies response/export equality and protected scheduled execution.
- Real **PostgreSQL 16.8** ran on an isolated local port, with generated disposable schemas. Tests verified an upgrade of populated legacy tables, repeated migration initialization, immutable rows, concurrent refresh deduplication and concurrent import confirmation. No hosted database was accessed or mutated. SQLite tests used temporary databases and independent engines for concurrency/cache checks.
- The full controlled workflow loaded a dataset, saved a USD quote, selected FX/settings, assessed current pricing, introduced a later FX observation, checked the price-change explanation, verified the original historical recommendation and dataset FX/quotes were unchanged, and exported provenance through the authenticated API.
- The regression example passes: USD 10 plus freight PKR 200, zero other costs, 20% gross margin: cost **3,000 → 3,300**, target **3,750 → 4,125**, increase **375** as effective FX moves **280 → 310**. Supplier USD price and other charges are held unchanged.
- Git whitespace checks passed. No browser automation, Playwright or screenshots were used. Visual layout/touch/cross-browser behavior has not been visually verified. The separate optional live-provider smoke command is supplied but was not run.

## Automation and remaining setup

Implemented automation is request-driven: persistent refresh locking/caching, idempotent upload confirmation, and reassessment of explicitly opted-in datasets on authenticated scheduled invocation. No demand retraining, price publishing, orders or messaging is automated.

**Scheduled refresh is not active.** To activate after review: verify the current Vercel plan/Container Services schedule support in the dashboard, create header-safe `CRON_SECRET`, add the daily route schedule documented in DEPLOYMENT.md, deploy through the normal approval process and verify its invocation logs. The connector exposed project/team identity but did not disclose the plan; no billing or schedule setting was changed. Provider keys and paid plans are unnecessary. Existing database/workspace configuration remains required. Dataset automatic-reassessment and synthetic real-FX choices are separate explicit opt-ins.

The seller still supplies/reconfirms supplier prices, bank rates, quantities, freight and other acquisition charges. Missing expiry means unknown validity. Mixed-SKU fee allocation, supplier monitoring, future-price prediction, Alibaba/OCR/LLM extraction and external price publication are outside this release. Current pricing retains the historical dataset's selling-price/inventory context and prominently reports its dates.
