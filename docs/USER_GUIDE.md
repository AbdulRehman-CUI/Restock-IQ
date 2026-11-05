# Using MarginShift

Start the API and dashboard using the two terminal commands in README, then open http://127.0.0.1:8501.

Choose **Explore demo** for a saved fictional retailer. It reuses a compatible base analysis; visits never retrain the model. If preparation is missing, the app opens guided setup and waits for your explicit action. Synthetic records and FX are always labeled. For your own records, use **Data & Settings**.

## Overview

Start with the four cards: products to order now, products needing faster supply, products below target replacement margin, and estimated cash for orders required now. The cash card shows exclusions and partial totals. Coverage quantities are planning candidates; they are not all orders to place today.

Read **Priority actions** and choose **View details**. Search or filter the inventory table to find other products. A projected shortage before a new delivery can arrive may need expediting an existing delivery, rather than another normal order. Overdue supply remains uncertain. Suggested orders are not added to the projected stock curve.

## Products

Select a product by name or SKU. Its status, short recommendation, and key prices/stock come first. **Demand & Stock** shows observed fulfilled sales, the analysis-date divider, estimated demand, and stock projections. Closed and stock-constrained observations are marked separately.

Choose **Pricing & Costs** for historical versus replacement cost, quote/FX inputs, suggested price, and achievable margin. Replacement margin is not net profit. A suggested price does not establish customer willingness to pay. Missing input notices explain the affected results and the next step.

**Recommendation Details** explains timing, quantity, supplier delivery time, minimum order/pack constraints, assumptions, and provenance. Expand **Local review actions & history** and enable the controls to record a review or dismissal. These are local notes only; they do not order stock or publish prices.

## What-if Scenarios

Choose a saved base for the selected forecast. Pick **FX increase only**, **Delivery delay only**, **FX increase plus delivery delay**, or custom inputs. FX/supplier changes use percent, freight uses PKR per unit, delay uses days, and optional target margin uses percent. Presets keep product target margins unless you explicitly enable an override.

The form shows assumptions and labels edits as unapplied until **Run scenario**. Streamlit sends form edits together, so its summary reflects the last values sent to the server or selected preset. A result always identifies the exact saved assumptions that produced it.

Results compare the same forecast and saved pricing settings. Review affected products, changed costs/prices, shortages, coverage cash, and separate order-now cash. Missing comparisons are unavailable, never zero change. Demand predictions remain unchanged by suggested prices. Scenario costs and delivery shifts are hypothetical assumptions.

**Reset assumptions** changes the draft only. If assumptions save but calculation fails, **Retry saved scenario calculation** reuses those assumptions. After a timeout, refresh and inspect saved results before retrying, because the request may have completed.

## Model Performance

Read which method was selected and why. Random Forest is compared with a recent-demand baseline. **Validation** selects the method; the **held-out test** evaluates it afterward. Change the evaluation period and 1/7/30-day horizon explicitly.

MAE shows how many units the forecast missed by on average. WAPE compares total absolute prediction error with observed demand. Lower is better; neither is an accuracy score. Inspect products where the baseline did better and unavailable/insufficient coverage results. Split dates and excluded observations are available under methodology.

## Data & Settings

Follow **Load data → Review validation → Prepare forecasting model → Generate analysis → Open results**. Use the step selector to revisit earlier work. Completed imports/models/forecasts remain saved if a later step fails.

Upload all six CSVs together: products, daily sales, inventory, supplier quotes, purchase orders, and FX rates. Downloadable blank templates follow the actual contracts and are not usable business history. Sales need every product/date, with explicit zeros. Inventory must use the final sales date. Invalid batches import nothing; correct the file/row/field shown and resubmit the complete batch.

Train explicitly and wait for the actual job status. Duplicate job starts are blocked while a tracked job is active. Generate analysis reuses a matching forecast. Advanced price rounding and caps create new results; old analyses keep their settings. A price cap can leave the target margin unmet.

## Saved analyses, downloads, and recovery

The sidebar **Saved analysis** selector resolves its data, model, and forecast automatically. Its label identifies demo/uploaded origin, analysis date, outlook, base/scenario, and a saved ordinal. The caption shows the forecast range. Use **Advanced analysis details** to switch datasets or choose a completed model and forecast for a new analysis. Full IDs and saved assumptions are under **Technical details · selected analysis**.

Under **Download selected reports**, select a CSV or metadata report, choose **Prepare download**, then download it. It always matches the selected saved analysis. Monetary precision and unavailable values are preserved.

For a temporary connection failure, start the API or correct Connection settings, then **Reconnect / refresh**. Your selected context stays available. An API-address change clears dependent selections. Check saved discovery results after timed-out writes before retrying. Estimates and synthetic results cannot establish actual savings or guaranteed stock availability.
