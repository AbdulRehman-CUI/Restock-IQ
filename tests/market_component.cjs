// Lightweight component/handler checks in Node's VM. No browser or screenshots.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const { test } = require("node:test");
const { webcrypto } = require("node:crypto");

function component() {
  const nodes = new Map();
  const element = (id) => {
    if (!nodes.has(id)) nodes.set(id, {value: "", checked: false, disabled: false, textContent: "", style: {}});
    return nodes.get(id);
  };
  const context = vm.createContext({
    document: {querySelector: element, querySelectorAll: () => []},
    localStorage: {getItem: () => null}, window: {addEventListener: () => {}},
    fetch: () => new Promise(() => {}), crypto: webcrypto,
    Intl, Date, console, setTimeout, clearTimeout, assert,
  });
  for (const path of ["app/web/app.js", "app/web/market.js"])
    vm.runInContext(fs.readFileSync(path, "utf8"), context, {filename: path});
  return {context, element};
}

test("quote forms escape values, use existing suppliers and require explicit costs", () => {
  const {context} = component();
  vm.runInContext(`
    const html = quoteForm([{sku:'A',name:'<script>alert(1)</script>',supplier_id:'SUP-1'}], 'A', null);
    assert.ok(html.includes('&lt;script&gt;'));
    assert.ok(!html.includes('<script>'));
    assert.ok(html.includes('SUP-1'));
    assert.ok(html.includes('id="freight_unit_pkr"'));
    assert.ok(html.includes('enter 0 if none'));
    assert.ok(html.includes('type="datetime-local"'));
    assert.ok(marketTime('2026-11-06T10:00:00Z').includes('PKT (UTC+05:00)'));
    assert.ok(assessmentTable(null).includes('No current-pricing assessment'));
  `, context);
});

test("bank mode disables reference spread and submits no second spread", async () => {
  const {context, element} = component();
  element("#rate-mode").value = "manual_bank";
  context.captured = null;
  vm.runInContext(`
    wireForm = (id, handler) => { captured = handler; };
    api = async (path, options) => { capturedBody = JSON.parse(options.body); return {created_at:'2026-11-06T10:00:00Z'}; };
    notify = () => {};
    wireRates(false);
  `, context);
  assert.equal(element("#spread_percent").disabled, true);
  assert.equal(element("#manual_rate").disabled, false);
  await vm.runInContext(`captured({manual_rate:'310',manual_source:'Bank counter',manual_observed_at:'2026-11-06T10:00',
    rate_note:'',target_margin:'0.20',order_quantity:'',payment_fee_total_pkr:'0',price_step_pkr:'5'})`, context);
  const body = vm.runInContext("capturedBody", context);
  assert.equal(body.spread_percent, "0");
  assert.equal(body.manual_rate, "310");
  assert.equal(body.mode, "manual_bank");
});

test("quote retries retain the same idempotency key and original draft", async () => {
  const {context, element} = component();
  element("#quote-sku").value = "A";
  element("#quote-currency").value = "USD";
  element("#unit-basis").value = "unit";
  context.calls = [];
  vm.runInContext(`
    wireForm = (id, handler) => { captured = handler; };
    notify = () => {};
    api = async (path, options) => { calls.push(options); if(calls.length === 1) throw Error('recoverable timeout'); return {}; };
    wireQuote([{sku:'A',supplier_id:'SUP-1'}]);
    draft = {unit_price:'10',quote_observed:'2026-11-06T10:00',quote_expiry:'',pack_units:'1',min_quantity:'1',
      max_quantity:'',freight_unit_pkr:'200',other_unit_pkr:'0',source_reference:'',quote_note:'confirmed'};
  `, context);
  await assert.rejects(vm.runInContext("captured(draft)", context), /timeout/);
  await vm.runInContext("captured(draft)", context);
  const calls = context.calls;
  assert.equal(calls[0].headers["Idempotency-Key"], calls[1].headers["Idempotency-Key"]);
  assert.equal(calls[0].body, calls[1].body);
  assert.equal(JSON.parse(calls[1].body).supplier_id, "SUP-1");
});
