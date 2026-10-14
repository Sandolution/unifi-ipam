// Browser-script check for app/static/app.js. Run all with: node tests/js/run.js
// Refresh-on-return with check-ins: fake page, fake clock, fake /presence.
const fs = require("fs");
const src = fs.readFileSync(process.argv[2], "utf8");

let fails = 0;
const eq = (l, g, w) => { const p = g === w; if (!p) fails++; console.log((p ? "ok   " : "FAIL ") + l + ": " + JSON.stringify(g)); };
const flush = () => new Promise(r => setTimeout(r, 0));

function boot(opts) {
  const o = Object.assign({ resumed: false, visible: true }, opts || {});
  let now = 0; Date.now = () => now;
  const docH = {}, winH = {};
  const state = { editing: false, colsOpen: false, busy: false, noRenew: false, selection: "", inputs: [] };
  const counters = { reloads: 0, checkIns: 0 };
  let tick = null;
  global.setInterval = fn => { tick = fn; return 1; };
  global.fetch = () => { counters.checkIns++; const r = o.resumed; o.resumed = false;
    return Promise.resolve({ ok: true, json: () => Promise.resolve({ resumed: r }) }); };
  global.window = { addEventListener: (ev, fn) => { (winH[ev] = winH[ev] || []).push(fn); }, getSelection: () => state.selection };
  global.navigator = {};
  global.history = { replaceState() {} };
  global.location = { search: "", pathname: "/vlan/3", hash: "", reload: () => { counters.reloads++; } };
  global.document = {
    visibilityState: o.visible ? "visible" : "hidden",
    documentElement: { classList: { contains: c => c === "busy" && state.busy, add() {}, remove() {}, toggle() {} } },
    body: { hasAttribute: a => a === "data-no-renew" && state.noRenew, appendChild() {} },
    createElement: () => ({ classList: { add() {}, remove() {} }, setAttribute() {}, addEventListener() {}, appendChild() {} }),
    getElementById: () => null,
    addEventListener: (ev, fn) => { (docH[ev] = docH[ev] || []).push(fn); },
    querySelector: sel => (sel === "tr.editing, details.cols[open]" && (state.editing || state.colsOpen) ? {} : null),
    querySelectorAll: sel => (sel === "input, textarea" ? state.inputs : []),
  };
  eval(src);
  docH.DOMContentLoaded.forEach(f => f());
  return {
    state, counters, o,
    async away(ms, resumedOnReturn) {
      document.visibilityState = "hidden"; docH.visibilitychange.forEach(f => f());
      now += ms; o.resumed = !!resumedOnReturn;
      document.visibilityState = "visible"; await Promise.all(docH.visibilitychange.map(f => f()));
      await flush();
    },
    async tick(visible) { document.visibilityState = visible ? "visible" : "hidden"; tick(); await flush(); },
    async restored() { winH.pageshow.forEach(f => f({ persisted: true })); await flush(); },
  };
}

(async () => {
  let p;
  p = boot(); await flush();
  eq("page open checks in once", p.counters.checkIns, 1);
  eq("no pause: no reload on open", p.counters.reloads, 0);

  p = boot({ resumed: true }); await flush();
  eq("opened while sync was paused: reloads with fresh data", p.counters.reloads, 1);

  p = boot({ visible: false }); await flush();
  eq("opened hidden: no check-in", p.counters.checkIns, 0);

  p = boot(); await flush(); await p.away(60000);
  eq("back after 60 s: reloads", p.counters.reloads, 1);
  eq("  and checked in first", p.counters.checkIns, 2);

  p = boot(); await flush(); await p.away(5000);
  eq("back after 5 s, not paused: no reload", p.counters.reloads, 0);

  p = boot(); await flush(); await p.away(5000, true);
  eq("back after 5 s but sync was paused: reloads", p.counters.reloads, 1);

  p = boot(); await flush(); p.state.editing = true; await p.away(60000, true);
  eq("paused but a row is in edit mode: no reload", p.counters.reloads, 0);

  p = boot(); await flush(); p.state.inputs = [{ type: "text", value: "Cams", defaultValue: "" }]; await p.away(60000);
  eq("unsaved typing: no reload", p.counters.reloads, 0);

  p = boot(); await flush(); p.state.noRenew = true; await p.away(60000);
  eq("form post with errors: no reload", p.counters.reloads, 0);

  p = boot(); await flush(); await p.tick(true); await p.tick(false);
  eq("once-a-minute check-in only while visible", p.counters.checkIns, 2);

  p = boot(); await flush(); await p.restored();
  eq("restored from back/forward cache: checks in and reloads", [p.counters.checkIns, p.counters.reloads].join("/"), "2/1");

  console.log(fails ? fails + " failed" : "all passed");
  process.exit(fails ? 1 : 0);
})();
