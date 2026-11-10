# Current replacement-cost pricing

The hosted **Market & Pricing** screen estimates replacement cost from the seller's latest known supplier quote and daily reference or manual executable USD/PKR rate. It does not predict future prices, forecast FX, monitor suppliers or connect to Alibaba. Random Forest and baseline demand forecasting remain independent.

## Workflow

1. Load a dataset using the existing six-file import or synthetic generator. No trained model or saved forecast is required for current pricing.
2. Open **Market & Pricing**, then a product, or follow **Update supplier quote / current pricing** from historical product detail.
3. Enter a confirmed supplier quote with its existing SKU/supplier relationship, USD or PKR price, observation/expiry, quantity tier and inventory-unit/pack basis. Enter freight and other costs per inventory unit, including explicit zero values. Missing costs block pricing. Pack quotes must match the product's pack size.
4. Select **Refresh FX**, then reference plus spread or a manual bank selling rate. This is the rate at which the bank sells USD to the retailer. Manual rates receive no additional reference spread. Expired bank rates cannot be used; blank expiry is unknown validity.
5. Save settings, then **Recalculate current pricing**. Inspect itemized costs, shortfalls, gross margin, price changes and constraints. Historical inventory, selling prices and demand retain their dataset dates, displayed prominently. They are not fresh POS/inventory feeds.
6. Compare saved assessments or export exact CSV / full-provenance JSON. FX-only attribution requires the same quote version and settings. Multiple changes are labeled combined. An FX-only suggested-price change of at least 1% is an explicit material-movement alert.

Identical submissions reuse the original assessment and timestamp. Ages in saved assessments mean age **at assessment time**. Normal age ticking does not create new histories; freshness-state changes do. Automatic comparison considers the latest 20 records with the same SKU scope; explicit comparison supports any saved IDs.

Synthetic datasets retain their own fictional FX by default. Real reference/manual FX requires explicit opt-in and the label **Synthetic products with real FX**. Fictional supplier history remains unconfirmed, and fetching FX never reconfirms quotes. The default synthetic analysis remains offline.

## Quote versions and imports

Manual quotes append immutable versions; **Reconfirm unchanged** links a new version to the previous confirmation, using the observation/expiry/note fields in the form. An absent expiry means unknown validity. Quote age and expiry are distinct. The newest current quote observation and latest confirmation supersede dataset quotes. Dataset quotes are visibly unconfirmed. Older backdated versions do not displace newer observations. For quantity tiers at the latest observation, an explicit order quantity selects the applicable tier with the highest minimum (up to 100 tiers); incompatible quantities block pricing. No supplier remapping occurs.

The quote-only import supplements the six-file dataset import. Download [the blank CSV template](templates/supplier-price-list-template.csv), fill it, or save the same header/table as a single-sheet non-macro `.xlsx`:

```text
sku,supplier_id,currency,unit_price,observed_at,valid_until,min_quantity,max_quantity,unit_basis,pack_units,freight_unit_pkr,other_unit_pkr,source_reference,note
```

| Field | Rule |
| --- | --- |
| SKU / supplier | Existing dataset relationship |
| Currency / unit price | USD or PKR; positive money, 2 decimals maximum |
| Observed / valid until | ISO timestamp **with offset**, e.g. `2026-11-06T09:00:00+05:00`; observation cannot be future-dated; expiry must follow observation. Excel numeric dates are unsupported |
| Minimum / maximum quantity | Positive whole **inventory units**; maximum optional; minimum `1` for no higher minimum |
| Unit basis / pack units | `unit,1` or `pack,<product pack_size>` |
| Freight / other costs | Nonnegative PKR per inventory unit, 2 decimals; blanks mark costs missing |
| Source reference / note | Optional; HTTP(S) reference is stored as text and never fetched |

Source type is assigned `CSV`, `XLSX` or `manual`. Each upload is parsed and previewed with up to 100 row findings. Invalid rows block the whole import. Confirmation requires an explicit checkbox and the exact preview hash. All quote inserts and the retry receipt share one transaction. A failed insert rolls the entire confirmation back. Retrying that upload returns the original result; a separate new upload is a new confirmation event. Duplicate SKU/observation/tier rows within one file fail validation. Manual quote APIs accept an optional UUID `Idempotency-Key`; the browser preserves it across recoverable retries of the same draft.

Uploads use 512 KiB chunks with immutable positions, exact declared length, a 12 MiB file limit (or smaller configured limit), 1,000 quote rows, 14 columns and 4,096 characters/cell. XLSX is limited to 100 ZIP parts and 8 MiB expanded data. Unsupported formats, multiple sheets, macros, encryption, formulas, XML entities and external relationships are rejected. No spreadsheet formula, external link, OCR or LLM runs. Parsing is in memory; uploaded ZIP contents are never extracted. Use literal ISO text timestamps.

Chunks are removed immediately after confirmation. Incomplete/preview uploads expire after 24 hours; cleanup runs at the next quote-upload start or scheduled refresh. Completed retry receipts remain seven days. Confirmed quote/assessment records persist independently. With no subsequent request/schedule, expired raw rows await cleanup, but expiry remains enforced on access.

## Calculation and precision

Current and historical pricing share `landed_cost` and the existing price-rounding/cap engine. Historical callers retain their previous rounded-cost semantics; current assessments pass the unrounded exact cost into pricing.

```text
effective USD rate = reference × (1 + spread / 100), OR manual bank rate
replacement cost = supplier price per inventory unit × effective rate
                 + freight + other acquisition costs per unit
                 + fixed payment fee / explicit batch quantity
PKR supplier quotes use rate 1.
target price = cost / (1 - target gross margin)
replacement margin = (selling price - cost) / selling price
shortfall = max(0, cost - selling price)
```

Fixed fees are equally allocated across an explicit **single-SKU** batch and entered quantity. The selected fee SKU is required and validated; other SKUs receive zero allocation, so the same fee is never charged to every product. Mixed-SKU shipment allocation is outside this release. No shipment quantity is invented. Pack prices divide by the stated matching pack size, while acquisition charges remain per inventory unit. Other acquisition costs can include seller-entered nonrecoverable taxes/duties/handling. Nothing looks up current taxes automatically. Minimum replacement-funding price excludes profit, operating expenses and costs not entered; **it is not full business break-even**. Historical gross difference is not net profit. No guaranteed purchasing power is claimed for incomplete, stale, expired or constrained inputs, or against future price changes.

Decimal calculations use 38 significant digits, including repeating fee/pack divisions. Provider rates parse directly to Decimal and half-even quantize to 6 decimals for scaled BIGINT persistence. Seller money accepts 2 decimals, percentages 4, FX 6. Display costs round half-up to 2 decimals; minimum funding price rounds **up** to the next paisa. JSON preserves unrounded costs/shortfalls/components. CSV retains exact Decimal strings and guards free text against spreadsheet formula injection. Existing upward price steps and optional symmetric price-change caps remain authoritative; caps that prevent funding/target margin produce explicit warnings.

Regression example with unchanged USD quote/other costs: USD 10 + PKR 200 freight + zero other costs; 20% gross margin → FX 280 gives cost 3,000 / target 3,750; FX 310 gives cost 3,300 / target 4,125; increase 375. This is gross margin, not markup.

## Provider and freshness

Official references checked 7 November 2026: [open endpoint](https://www.exchangerate-api.com/docs/free), [PKR support](https://www.exchangerate-api.com/docs/supported-currencies), [terms](https://www.exchangerate-api.com/terms). Fixed HTTPS URL: `https://open.er-api.com/v6/latest/USD`. No key/payment; **daily**, not real-time. Commercial end-use and persistent caching are permitted; raw redistribution is prohibited. This application offers private workspace use, not a public FX feed. The provider describes rates as indicative; they are not executable bank rates.

Pages using provider rates show linked **Rates By Exchange Rate API** attribution; exports include attribution/provenance. Redirects are not followed. Connect timeout is 4 seconds, other HTTP timeouts 8 seconds, response maximum 128 KiB, at most three transient attempts with short backoff. HTTP 429 defers at least 20 minutes or longer `Retry-After`. Transient responses with that header also defer; numeric and HTTP-date headers are supported.

Validation requires success, USD base/identity rate, positive finite PKR, positive integer timestamps, observation before next update, next update within three days, and no observation more than five minutes in the future. UTC text timestamps must agree if present. Observation/source/next-update/first-fetched time are immutable. Regressing observations or altered contents for an existing observation are rejected. Latest usable reference is the newest validated observation not later than assessment time. Its age comes from **provider time**, never retrieval time.

A database compare-and-set lease lasts 90 seconds. Success permits another fetch no sooner than 24 hours later **and** no sooner than provider next-update time. Manual refresh respects this cooldown. Failures preserve the last observation and display a separate failure state, deferring further attempts 20 minutes or longer provider backoff. No valid observation means unavailable with manual entry offered; real FX never silently becomes synthetic.

Default stale thresholds: more than 3 days for FX (`RESTOCKIQ_FX_STALE_DAYS`), more than 30 days for quotes (`RESTOCKIQ_QUOTE_STALE_DAYS`). Expired, unconfirmed, unknown-validity or stale quotes yield indicative estimates with warnings. Missing costs/FX and unit/quantity mismatches block pricing.

## API, storage and migration

Prefix `/api/v1/market`; hosted private routes inherit workspace cookie and same-origin protection. Existing identity has one owner: records say `workspace-owner`, or `local-operator` locally, not invented individual usernames.

| Suffix | Purpose |
| --- | --- |
| `/fx`, `/fx/history`, `/fx/refresh` | GET status/history, POST refresh |
| `/datasets/{id}/settings` | GET / POST versioned settings |
| `/datasets/{id}/catalog` | GET product/supplier/pack references |
| `/datasets/{id}/quotes` | GET history / POST quote |
| `/datasets/{id}/quotes/{quote}/reconfirm` | POST linked confirmation |
| `/quote-template.csv` | GET blank template |
| `/datasets/{id}/quote-imports` | POST metadata |
| `/quote-imports/{id}/chunks/{position}` | PUT bounded chunk |
| `/quote-imports/{id}/preview`, `/confirm` | POST preview / atomic confirmation |
| `/datasets/{id}/assessments` | GET history / POST calculation |
| `/assessments/{id}`, `/compare/{other}`, `/alerts`, `/export?format=json\|csv` | GET snapshot, comparison, alerts, export |
| `/scheduled-refresh` | GET with independent `CRON_SECRET` bearer |

Pydantic defines request/response contracts. Histories default to 50, maximum 100, offset up to 10,000; assessment SKU filtering applies within the bounded page. Database uniqueness, conditional leases, PostgreSQL row locks and SQLite `BEGIN IMMEDIATE` protect concurrency. Assessments have unique input fingerprints. Fetch clock and ordinary age ticking do not create duplicate histories; meaningful freshness transitions do. Reference changes do not trigger new exclusively-PKR/manual-bank financial assessments.

Migration **1**, `app/db/migrations.py`, creates only new tables/indexes/immutability triggers and `schema_versions`. PostgreSQL initialization holds an advisory transaction lock; SQLite holds an immediate transaction. Existing business records/structures are retained. The existing `create_all` bootstrap is restricted to legacy tables; market additions are applied by the versioned migration, not implicit alteration. Run `python -m app.cli init-db` or normal API startup. Rollback application code while retaining additive tables/ledger; no destructive down migration is provided. Never drop/recreate populated tables or delete the ledger.

Market & Pricing is a browser/hosted screen; the original local Streamlit screens remain unchanged. Enable existing browser mode locally with `RESTOCKIQ_CLOUD=1` and a workspace password; SQLite remains available without `DATABASE_URL`. See [deployment](../DEPLOYMENT.md) for scheduling and environment steps.

## Verification

```powershell
.\.venv\Scripts\python.exe -m pytest -q
node --check app/web/app.js
node --check app/web/market.js
node --test tests/market_component.cjs
```

Optional real PostgreSQL tests require a disposable **localhost** server, never production `DATABASE_URL`:

```powershell
$env:RESTOCKIQ_TEST_POSTGRES_URL='postgresql+psycopg://postgres@127.0.0.1:5433/postgres'
.\.venv\Scripts\python.exe -m pytest tests/test_market_postgres.py -q
```

Each test creates/drops its generated schema and refuses remote hosts. Without that explicit variable, two PostgreSQL tests skip. Provider responses are deterministic/mocked. Optional separate provider smoke: `python -m scripts.check_fx_provider --live`, with no database writes. No browser automation/screenshots were used; visual layout, touch behavior and cross-browser appearance remain unverified.
