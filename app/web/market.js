"use strict";
// All monetary calculations and input selection remain on the server.
const marketTime = (value) => value ? new Intl.DateTimeFormat("en-PK", {
  dateStyle: "medium", timeStyle: "short", timeZone: "Asia/Karachi"
}).format(new Date(value)) + " PKT (UTC+05:00)" : "Unavailable";
const utcDraft = () => new Date().toISOString().slice(0, 16);
const marketBase = () => `market/datasets/${state.dataset}`;
const marketSelect = (label, id, items, value) => select(label, id, items, value);
function marketAction(id, handler) {
  const button = $(id);
  if (!button) return;
  button.onclick = async () => {
    button.disabled = true;
    try { await handler(); } catch (e) { notify(e.message, true); }
    finally { if (button.isConnected) button.disabled = false; }
  };
}
function alertLabel(code) {
  const labels = {
    BELOW_REPLACEMENT_COST: "Current price cannot fund estimated replacement. Review selling price.",
    BELOW_TARGET_MARGIN: "Replacement margin is below target. Review the suggested price.",
    EXPIRED_QUOTE: "Quote expired. Ask the supplier to reconfirm it.",
    STALE_QUOTE: "Quote is old. Reconfirm price and costs with the supplier.",
    UNCONFIRMED_QUOTE: "Historical quote has not been reconfirmed for current pricing.",
    QUOTE_VALIDITY_UNKNOWN: "No quote expiry was supplied; validity is unknown.",
    STALE_FX_RATE: "FX observation is old. Refresh or enter a bank selling rate.",
    FX_REFRESH_FAILED: "FX refresh failed. The last valid observation was retained.",
    MISSING_FX_RATE: "FX unavailable. Refresh or explicitly enter a manual bank selling rate.",
    MISSING_QUOTE: "Enter a supplier quote for this product.",
    MISSING_ACQUISITION_COSTS: "Enter freight and other acquisition costs, including explicit zero values.",
    PRICE_CAP_PREVENTS_REPLACEMENT: "Price cap prevents funding replacement. Review the cap or costs.",
    TARGET_MARGIN_UNMET: "Pricing constraints prevent the target margin.",
    PRICE_CHANGE_CAPPED: "Suggested price is limited by the configured price-change cap.",
    QUOTE_QUANTITY_MISMATCH: "Enter an order quantity within the quote's tier.",
    QUOTE_UNIT_MISMATCH: "Quote pack size does not match the product. Correct the quote.",
    ORDER_QUANTITY_MISMATCH: "Batch quantity must respect product MOQ and pack size.",
    MANUAL_RATE_EXPIRED: "Bank rate expired. Enter a new executable rate.",
    MANUAL_RATE_VALIDITY_UNKNOWN: "Bank rate has no expiry; reconfirm before purchasing USD.",
    MATERIAL_FX_PRICE_CHANGE: "FX movement changed the suggested price by at least 1%. Review the saved comparison."
  };
  return labels[code] || code.replaceAll("_", " ").toLowerCase();
}
function rateForm(saved, synthetic, products) {
  const v = saved.values;
  return `<section class="panel"><h2>Conversion & pricing settings</h2><p class="muted">Bank selling rate means the PKR price at which the bank sells USD to you. A manual rate receives no extra reference spread.</p><form id="market-settings"><div class="formgrid">
    ${marketSelect("Rate mode", "rate-mode", [["reference_spread", "Daily reference + conversion spread"], ["manual_bank", "Manual executable / bank selling rate"]], v.mode)}
    ${field("Reference conversion spread (%)", "spread_percent", v.spread_percent, "number", 'min="0" step="0.0001"')}
    ${field("Manual bank selling rate (PKR/USD)", "manual_rate", v.manual_rate || "", "number", 'min="0.000001" step="0.000001"')}
    ${field("Bank rate observed (UTC)", "manual_observed_at", v.manual_observed_at ? new Date(v.manual_observed_at).toISOString().slice(0,16) : utcDraft(), "datetime-local")}
    ${field("Bank rate valid until (UTC, optional)", "manual_valid_until", v.manual_valid_until ? new Date(v.manual_valid_until).toISOString().slice(0,16) : "", "datetime-local")}
    ${field("Bank / manual source", "manual_source", v.manual_source, "text", 'maxlength="200"')}
    ${field("Conversion note", "rate_note", v.note, "text", 'maxlength="1000"')}
    ${field("Target gross margin fraction (optional; 0.20 = 20%)", "target_margin", v.target_margin ?? "", "number", 'min="0" max="0.8" step="0.0001"')}
    ${field("Round price upward to PKR", "price_step_pkr", v.pricing.price_step_pkr, "number", 'min="0.01" step="0.01" required')}
    ${field("Maximum price change (%, optional)", "max_price_change_pct", v.pricing.max_price_change_pct ?? "", "number", 'min="0" step="0.0001"')}
    ${field("Applicable order / fee batch quantity (inventory units, optional)", "order_quantity", v.order_quantity ?? "", "number", 'min="1" step="1"')}
    ${field("Total payment fee for that batch (PKR)", "payment_fee_total_pkr", v.payment_fee_total_pkr, "number", 'min="0" step="0.01" required')}
    ${marketSelect("Fixed-fee batch SKU (fee applied only to this product)", "payment-fee-sku", [["", "No fee batch selected"], ...products.map(p => [p.sku, p.name])], v.payment_fee_sku || "")}
    </div>${synthetic ? `<label class="check-row"><input type="checkbox" id="real-fx" ${v.real_fx_opt_in ? "checked" : ""}> Explicitly apply real FX to synthetic products. Supplier history remains fictional.</label>` : ""}
    <label class="check-row"><input type="checkbox" id="auto-assess" ${v.auto_assess ? "checked" : ""}> Reassess this dataset during configured scheduled refreshes when relevant inputs change.</label>
    <p class="metric-note">Fixed payment fees are divided equally across the selected SKU's explicit batch quantity, once only. Mixed-SKU shipment fee allocation is outside this release. Freight/other costs are per inventory unit, including for pack quotes. No quantity is inferred.</p>
    <button type="submit">Save settings</button><small> ${saved.created_at ? "Saved " + esc(marketTime(saved.created_at)) + " by " + esc(saved.supplied_by) : "Default settings; no real FX applied to synthetic data"}</small></form></section>`;
}
function wireRates(synthetic) {
  const toggle = () => {
    const manual = $("#rate-mode").value === "manual_bank";
    $("#spread_percent").disabled = manual;
    for (const id of ["manual_rate", "manual_observed_at", "manual_valid_until", "manual_source"]) $("#" + id).disabled = !manual;
  };
  $("#rate-mode").onchange = toggle;
  toggle();
  wireForm("#market-settings", async (v) => {
    const manual = $("#rate-mode").value === "manual_bank";
    const result = await post(marketBase() + "/settings", {
      mode: manual ? "manual_bank" : "reference_spread", spread_percent: manual ? "0" : v.spread_percent || "0",
      manual_rate: manual ? v.manual_rate : null,
      manual_observed_at: manual ? v.manual_observed_at + ":00Z" : null,
      manual_valid_until: manual && v.manual_valid_until ? v.manual_valid_until + ":00Z" : null,
      manual_source: manual ? v.manual_source : "", note: v.rate_note,
      target_margin: v.target_margin || null, order_quantity: v.order_quantity ? Number(v.order_quantity) : null,
      payment_fee_total_pkr: v.payment_fee_total_pkr,
      payment_fee_sku: $("#payment-fee-sku").value || null,
      pricing: {price_step_pkr: v.price_step_pkr, max_price_change_pct: v.max_price_change_pct || null},
      real_fx_opt_in: synthetic && $("#real-fx").checked, auto_assess: $("#auto-assess").checked
    });
    notify("Settings saved. Recalculate to save a new pricing assessment.");
    $("#saved-settings").textContent = "Settings version saved at " + marketTime(result.created_at);
  });
}
function quoteForm(products, chosen, quote) {
  const q = quote?.values || {};
  return `<section class="panel" id="quote-maintenance"><h2>Update supplier quote</h2><p class="muted">Enter a supplier-confirmed price or reconfirm an unchanged quote. This creates a new version. Historical CSV and synthetic quotes are not verified market prices.</p>
    <form id="quote-form"><div class="formgrid">
    ${marketSelect("Product · existing supplier", "quote-sku", products.map(p => [p.sku, `${p.name} · ${p.supplier_id}`]), chosen)}
    ${marketSelect("Currency", "quote-currency", [["USD", "USD"], ["PKR", "PKR"]], q.currency || "USD")}
    ${field("Supplier unit / pack price", "unit_price", q.unit_price ?? "", "number", 'min="0.01" step="0.01" required')}
    ${field("Quote observed / confirmed (UTC)", "quote_observed", utcDraft(), "datetime-local", "required")}
    ${field("Valid until (UTC, optional; blank means unknown)", "quote_expiry", "", "datetime-local")}
    ${marketSelect("Price basis", "unit-basis", [["unit", "Per inventory unit"], ["pack", "Per product pack"]], q.unit_basis || "unit")}
    ${field("Units in quoted pack (1 for unit price)", "pack_units", q.pack_units || 1, "number", 'min="1" step="1" required')}
    ${field("Minimum order quantity (inventory units)", "min_quantity", q.min_quantity || 1, "number", 'min="1" step="1" required')}
    ${field("Maximum quantity (inventory units, optional)", "max_quantity", q.max_quantity ?? "", "number", 'min="1" step="1"')}
    ${field("Freight / inventory unit (PKR; enter 0 if none)", "freight_unit_pkr", q.freight_unit_pkr ?? "", "number", 'min="0" step="0.01" required')}
    ${field("Other acquisition costs / inventory unit (PKR)", "other_unit_pkr", q.other_unit_pkr ?? "", "number", 'min="0" step="0.01" required')}
    ${field("Source link (optional)", "source_reference", q.source_reference || "", "url", 'maxlength="1000"')}
    ${field("Quote note / included costs", "quote_note", "", "text", 'maxlength="1000"')}
    </div><p class="metric-note">Other acquisition costs may include seller-entered nonrecoverable taxes/duties and handling. No current tax, customs or operating expense is inferred.</p>
    <button type="submit">Save confirmed quote version</button></form></section>`;
}
function wireQuote(products) {
  let key = crypto.randomUUID(), previous = null;
  $("#quote-sku").onchange = () => { location.hash = "market/" + encodeURIComponent($("#quote-sku").value); };
  wireForm("#quote-form", async (v) => {
    const p = products.find(p => p.sku === $("#quote-sku").value);
    const body = {sku: p.sku, supplier_id: p.supplier_id, currency: $("#quote-currency").value,
      unit_price: v.unit_price, observed_at: v.quote_observed + ":00Z", valid_until: v.quote_expiry ? v.quote_expiry + ":00Z" : null,
      unit_basis: $("#unit-basis").value, pack_units: Number(v.pack_units), min_quantity: Number(v.min_quantity),
      max_quantity: v.max_quantity ? Number(v.max_quantity) : null, freight_unit_pkr: v.freight_unit_pkr,
      other_unit_pkr: v.other_unit_pkr, source_type: "manual", source_reference: v.source_reference || "", note: v.quote_note || ""};
    const serialized = JSON.stringify(body);
    if (previous !== serialized) { key = crypto.randomUUID(); previous = serialized; }
    await api(marketBase() + "/quotes", {method: "POST", headers: {"Idempotency-Key": key}, body: serialized});
    notify("Confirmed quote saved. Recalculate current pricing to use this version.");
  });
}
function importForm() {
  return `<section class="panel"><h2>Import a supplier price list</h2><p class="muted">Separate from the six-file dataset import. Use the template header and ISO timestamps with timezone offsets. XLSX must contain one sheet of literal values; formulas, macros and external links are rejected.</p><a href="/api/v1/market/quote-template.csv" class="button secondary">Download CSV template</a><form id="quote-upload"><div class="field"><label for="quote-file">CSV or XLSX · maximum 12 MiB / 1,000 rows</label><input id="quote-file" type="file" accept=".csv,.xlsx" required></div><button type="submit">Upload & preview</button></form><div id="quote-preview" aria-live="polite"></div></section>`;
}
function wireImport() {
  wireForm("#quote-upload", async () => {
    const file = $("#quote-file").files[0];
    if (file.size > 12 * 1024 * 1024) throw Error("The quote file exceeds 12 MiB.");
    const upload = await post(marketBase() + "/quote-imports", {filename: file.name, size: file.size});
    $("#quote-preview").textContent = "Uploading price list…";
    for (let position=0, offset=0; offset<file.size; position++, offset+=upload.chunk_bytes) {
      const body = await file.slice(offset, offset+upload.chunk_bytes).arrayBuffer();
      await api(`market/quote-imports/${upload.id}/chunks/${position}`, {method: "PUT", body});
    }
    const preview = await post(`market/quote-imports/${upload.id}/preview`);
    $("#quote-preview").innerHTML = `<h3>Preview · ${preview.row_count} valid quote rows</h3>${preview.error_count ? `<p class="error">${preview.error_count} findings. Nothing can be imported until corrected.</p>` : "<p>Review every quote before confirming. Existing versions will be preserved.</p>"}
      ${table(["SKU", "Supplier", "Currency", "Price", "Basis", "Observed", "Valid until"], preview.quotes.map(q => [esc(q.sku),esc(q.supplier_id),esc(q.currency),esc(q.unit_price),esc(q.unit_basis)+" / "+q.pack_units,esc(marketTime(q.observed_at)),esc(marketTime(q.valid_until))]))}
      ${preview.findings.map(f => `<p>Row ${esc(f.row || "file")}: ${esc(f.message)}</p>`).join("")}
      ${preview.can_confirm ? '<label class="check-row"><input type="checkbox" id="confirm-quotes"> I reviewed these quotes and confirm the import.</label><button id="commit-quotes" disabled>Confirm import</button>' : ""}`;
    if (preview.can_confirm) {
      $("#confirm-quotes").onchange = () => { $("#commit-quotes").disabled = !$("#confirm-quotes").checked; };
      marketAction("#commit-quotes", async () => {
        if (!$("#confirm-quotes").checked) return;
        const r = await post(`market/quote-imports/${upload.id}/confirm`, {confirm: true, preview_hash: preview.preview_hash});
        $("#quote-preview").textContent = `${r.count} quote versions imported successfully. Recalculate pricing to use them.`;
        notify("Price list imported atomically.");
      });
    }
  });
}
function assessmentTable(result) {
  if (!result) return '<p class="muted">No current-pricing assessment yet. Enter a quote, review conversion settings, then recalculate.</p>';
  return `<p><span class="badge warning">${esc(result.label)}</span> · Assessed ${esc(marketTime(result.assessed_at))}</p>
    <p>Selected conversion: ${esc(result.rate_selection.mode.replaceAll("_", " "))} · effective USD rate ${number(result.rate_selection.effective_usd_pkr_rate)} PKR/USD. Source: ${esc(result.rate_selection.source)}. PKR quotes use 1:1.</p>
    <p class="metric-note">Inventory / selling-price context: ${esc(result.context.dataset_origin)}. Demand forecasts have not been refreshed.</p>
    ${table(["Product", "Quote age / validity", "Current price", "Replacement cost", "Replacement margin", "Suggested price", "Change", "Status"], result.products.map(p => [
      `<a href="#market/${encodeURIComponent(p.sku)}">${esc(p.name)}</a>`,
      `${p.quote_age_days ?? "—"} days · ${esc(p.quote_validity)}`, money(p.pricing.current_price_pkr), money(p.replacement_cost_pkr),
      pct(p.pricing.current_replacement_margin), money(p.pricing.suggested_price_pkr),
      `${money(p.pricing.price_change_pkr)}<br>${p.pricing.price_change_pct == null ? "—" : number(p.pricing.price_change_pct) + "%"}`,
      `<span class="badge ${p.warnings.length ? "warning" : ""}">${esc(p.status)}</span>${p.warnings.map(w => `<p class="metric-note">${esc(alertLabel(w))}</p>`).join("")}`]))}
    ${result.comparison ? details("Changes since previous comparable assessment", result.comparison) : ""}
    <div class="row"><a class="button secondary" href="/api/v1/market/assessments/${result.id}/export?format=csv">Export exact CSV</a><a class="button secondary" href="/api/v1/market/assessments/${result.id}/export">Export JSON with provenance</a></div>`;
}
async function market() {
  $("#main").classList.add("market-screen");
  if (!state.dataset) {
    $("#main").innerHTML = hero("Market & Pricing", "Load a dataset first, then enter supplier quotes and calculate current replacement pricing.", '<a href="#data" class="button">Load data →</a>');
    return;
  }
  const chosen = decodeURIComponent(location.hash.split("/").slice(1).join("/"));
  const [fx, saved, catalog, history, quotes] = await Promise.all([
    api("market/fx"), api(marketBase()+"/settings"), api(marketBase()+"/catalog"),
    api(marketBase()+"/assessments"), api(marketBase()+"/quotes" + (chosen ? "?sku="+encodeURIComponent(chosen) : ""))
  ]);
  const synthetic = catalog.provenance === "synthetic";
  const selected = catalog.products.find(p => p.sku === chosen) || catalog.products[0];
  if (!selected) throw Error("Dataset has no products.");
  let result = history.length ? await api(`market/assessments/${history[0].id}`) : null;
  const quote = quotes.find(q => q.sku === selected.sku);
  const o = fx.observation;
  $("#main").innerHTML = hero("Market & Pricing", "Estimate replacement cost using daily FX and the latest supplier price you provide. No future-price prediction or supplier monitoring.") +
    `<section class="panel"><div class="row between"><h2>Daily reference USD / PKR</h2><span class="badge ${fx.status === "fresh" ? "" : "warning"}">${esc(fx.status)}</span></div>
    <p class="market-rate">${o ? number(o.rate) + " PKR per USD" : "Reference rate unavailable"}</p><p>Provider update: ${esc(marketTime(o?.observed_at))}<br>Last successful fetch: ${esc(marketTime(fx.last_success_at))}<br>Next provider update: ${esc(marketTime(o?.next_update_at))}</p>
    <p class="metric-note">${esc(fx.notice)} Age: ${fx.age_seconds == null ? "unavailable" : number(fx.age_seconds / 3600)+" hours"}. Next permitted refresh: ${esc(marketTime(fx.next_attempt_at || null))}.</p>
    ${fx.refresh_failed ? `<p role="alert">Refresh failed: ${esc(fx.error)}. Previous observation retained; age is based on provider time.</p>` : ""}
    <button id="refresh-fx">Refresh FX</button> <a href="https://www.exchangerate-api.com" rel="noopener">Rates By Exchange Rate API</a></section>
    ${rateForm(saved, synthetic, catalog.products)}<p id="saved-settings" role="status"></p>
    <section class="panel"><h2>Current price adjustments</h2><button id="recalculate-market">Recalculate current pricing${chosen ? " for " + esc(chosen) : ""}</button><p class="metric-note">Minimum funding price covers the estimated replacement inputs only; it is not full business break-even. Review quote validity and constraints before acting.</p><div id="market-assessment">${assessmentTable(result)}</div></section>
    ${chosen ? '<section class="panel" id="market-product-detail"></section>' : ""}
    ${quoteForm(catalog.products, selected.sku, quote)}${importForm()}
    <section class="panel"><h2>Quote history · ${esc(selected.sku)}</h2>${table(["Confirmed", "Supplier", "Price", "Expiry", "Source", ""], quotes.filter(q => q.sku === selected.sku).map(q => [esc(marketTime(q.observed_at)),esc(q.supplier_id),esc(q.values.currency)+" "+esc(q.values.unit_price),esc(marketTime(q.values.valid_until)),esc(q.values.source_type)+" · "+esc(q.supplied_by),`<button class="secondary reconfirm-quote" data-id="${esc(q.id)}">Reconfirm unchanged</button>`]))}<p class="metric-note">Latest 50 quote versions. Reconfirmation uses the quote observation and expiry fields in the form above; check them before confirming.</p></section>
    <section class="panel"><h2>Pricing assessment history</h2>${marketSelect("Saved assessment", "market-history", history.map(h => [h.id,marketTime(h.assessed_at)+" · "+h.label]), result?.id)}<button id="open-market-assessment" class="secondary">Open saved assessment</button>
    ${marketSelect("Compare from assessment", "market-before", history.map(h => [h.id,marketTime(h.assessed_at)]), history[1]?.id || result?.id)}<button id="compare-market" class="secondary">Compare with displayed assessment</button><div id="market-comparison"></div><p class="metric-note">Latest 50 assessments. Identical input submissions reuse the original assessment and timestamp.</p></section>`;
  const renderDetail = () => {
    if (!chosen || !$("#market-product-detail")) return;
    const p = result?.products.find(p => p.sku === chosen);
    $("#market-product-detail").innerHTML = p ? `<h2>${esc(p.name)} · current pricing detail</h2>
      <p>Effective rate: ${number(p.effective_rate)} PKR/${esc(p.quote?.values.currency || "USD")}. Minimum replacement funding: ${money(p.minimum_replacement_funding_price_pkr)}.</p>
      ${table(["Included component", "Exact value"], Object.entries(p.components).map(([k,v]) => [esc(k.replaceAll("_"," ")),esc(v ?? "not supplied")]))}
      <p>Last confirmed supplier observation: ${p.quote?.confirmed ? esc(marketTime(p.quote.observed_at)) : "Not currently reconfirmed"}. Quote price is assumed unchanged: yes.</p>
      ${p.assumptions.map(a=>`<p>${esc(a)}</p>`).join("")}${details("Quote version, validity and provenance",p.quote)}${details("FX / rate settings",{fx:result.fx,settings:result.settings})}${details("Exact gross margin and price calculation",p.pricing)}${details("Assumptions",result.assumptions)}<a href="#quote-maintenance" id="jump-quote">Update supplier quote ↓</a>` : "No saved assessment for this product yet. Recalculate current pricing above.";
    if ($("#jump-quote")) $("#jump-quote").onclick = e => {e.preventDefault(); $("#unit_price").focus();};
  };
  renderDetail();
  wireRates(synthetic); wireQuote(catalog.products); wireImport();
  marketAction("#refresh-fx", async () => {await post("market/fx/refresh"); await market(); notify("FX status checked. Daily cooldown and provider timestamps apply.");});
  marketAction("#recalculate-market", async () => {
    result = await post(marketBase()+"/assessments", {sku: chosen || null});
    $("#market-assessment").innerHTML = assessmentTable(result); renderDetail(); notify("Current pricing assessment saved. Historical analyses are unchanged.");
    for (const id of ["market-history", "market-before"]) if (![...$("#"+id).options].some(o => o.value === result.id)) $("#"+id).add(new Option(marketTime(result.assessed_at),result.id),0);
  });
  marketAction("#open-market-assessment", async () => {
    if (!$("#market-history").value) throw Error("No saved pricing assessments yet.");
    result = await api(`market/assessments/${$("#market-history").value}`);
    $("#market-assessment").innerHTML = assessmentTable(result); renderDetail();
  });
  marketAction("#compare-market", async () => {
    if (!result || !$("#market-before").value) throw Error("Save two assessments to compare their inputs.");
    const comparison = await api(`market/assessments/${$("#market-before").value}/compare/${result.id}`);
    $("#market-comparison").innerHTML = table(["Product", "Changed inputs", "Suggested-price change", "Explanation"],comparison.products.map(p => [esc(p.sku),esc(p.attribution),money(p.suggested_price_change_pkr),esc(p.explanation)+(p.alerts.length ? " Material FX movement (≥1%)." : "")]));
  });
  document.querySelectorAll(".reconfirm-quote").forEach(button => marketAction(`[data-id="${button.dataset.id}"]`, async () => {
    await post(marketBase()+`/quotes/${button.dataset.id}/reconfirm`, {observed_at:$("#quote_observed").value+":00Z",valid_until:$("#quote_expiry").value ? $("#quote_expiry").value+":00Z" : null,note:$("#quote_note").value});
    notify("Supplier price reconfirmed as a new version. Recalculate pricing to apply it.");
  }));
}
