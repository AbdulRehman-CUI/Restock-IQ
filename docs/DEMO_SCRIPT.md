# MarginShift — approximately three minutes

Start the API and UI using README. Select **Explore demo** to reuse a compatible saved synthetic base; no technical IDs or retraining are needed. If no complete demo exists, explicitly prepare the missing steps in Data & Settings before presenting. Read values from the selected run; different saved analyses can have different settings and results.

**0:00–0:25 — the problem.** A retailer can appear profitable against an old purchase cost while the next shipment costs more. MarginShift brings together demand estimates, stock and delivery timing, replacement costs, and suggested inventory/pricing actions. Historical gross difference is not net profit.

**0:25–0:55 — Overview.** Confirm synthetic provenance, analysis date, and forecast range. Read order-now, faster-supply, below-target-margin, and order-now cash cards. Explain partial/excluded cash, then open a priority product with **View details**. Coverage candidates, immediate orders, and expediting existing deliveries have separate meanings.

**0:55–1:30 — Products.** Explain the short recommendation, observed sales versus forecast estimates, analysis-date divider, and scheduled receipts. Newly suggested orders are excluded from the stock curve; overdue supply remains uncertain. Switch to **Pricing & Costs** to compare historical and replacement costs, source inputs, current/suggested price, and achievable margin. Show recommendation details and the optional local review controls; no order or price is published.

**1:30–2:10 — What-if Scenarios.** Select **FX increase plus delivery delay**. Review +10% FX, +7-day delay, supplier change 0%, extra freight 0, and unchanged product target margins. Explicitly submit **Run scenario**, or select an existing saved scenario to avoid a new calculation. Compare affected products and the separate coverage/order-now cash scopes. PKR quotes are unchanged by FX alone. Demand does not respond to suggested prices in this model. Reset the draft and point out that saved results retain their original assumptions.

**2:10–2:40 — Model Performance.** Explain the selected method and validation basis. Compare baseline and Random Forest on the held-out test at seven days. MAE is error in units; WAPE is aggregate relative error, not accuracy. Show products where baseline wins and insufficient thirty-day coverage. Do not promise accuracy or commercial savings from synthetic evaluation.

**2:40–3:00 — handoff.** Select **Recommendation CSV → Prepare download** under Download selected reports, then download it. Explain the own-data setup sequence and the next step: evaluate the pipeline with observed retailer availability, sales, receipts, and landed costs before operational adoption.

For exact historical Phase 3 evidence and saved IDs, use the phase reports. Normal entry does not depend on those IDs.
