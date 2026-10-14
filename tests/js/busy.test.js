// Browser-script check for app/static/app.js. Run all with: node tests/js/run.js
const fs = require("fs");
const src = fs.readFileSync(process.argv[2], "utf8");

function classes() {
  const s = new Set();
  return { add: c => s.add(c), remove: c => s.delete(c), contains: c => s.has(c), toggle: (c, on) => (on ? s.add(c) : s.delete(c)) };
}
const handlers = {};
const html = { classList: classes() };
const toastEl = { className: "", textContent: "", classList: classes(), setAttribute() {} };
global.document = {
  documentElement: html,
  body: { appendChild() {} },
  createElement: () => toastEl,
  addEventListener: (ev, fn) => { (handlers[ev] = handlers[ev] || []).push(fn); },
  querySelectorAll: () => [],
};
global.window = { addEventListener() {} };
global.navigator = {};
global.location = { search: "", pathname: "/", hash: "" };
global.history = { replaceState() {} };
eval(src);
handlers.DOMContentLoaded.forEach(f => f());

function makeForm(action) {
  const attrs = { "data-busy": "Forgetting…" };
  const button = { textContent: "Forget", classList: classes(), attrs: {}, getAttribute: k => null, setAttribute(k, v) { this.attrs[k] = v; } };
  const row = { classList: classes() };
  return {
    action, button, row,
    getAttribute: k => attrs[k] || null,
    setAttribute: (k, v) => { attrs[k] = v; },
    querySelector: () => button,
    closest: () => row,
  };
}
function submit(form, cancelled) {
  let prevented = cancelled;
  const ev = { target: form, submitter: form.button, get defaultPrevented() { return prevented; }, preventDefault() { prevented = true; } };
  handlers.submit.forEach(f => f(ev));
  return prevented;
}

let fails = 0;
const eq = (label, got, want) => { const ok = got === want; if (!ok) fails++; console.log((ok ? "ok   " : "FAIL ") + label + ": " + JSON.stringify(got)); };

const a = makeForm("http://x/device/aa/forget");
submit(a, true);
eq("cancelled confirm leaves button alone", a.button.textContent, "Forget");
eq("cancelled confirm: no busy bar", html.classList.contains("busy"), false);

const b = makeForm("http://x/device/bb/forget");
const blocked1 = submit(b, false);
eq("confirmed: submit goes through", blocked1, false);
eq("confirmed: button says Forgetting…", b.button.textContent, "Forgetting…");
eq("confirmed: spinner class", b.button.classList.contains("is-busy"), true);
eq("confirmed: row dimmed", b.row.classList.contains("is-busy"), true);
eq("confirmed: busy bar on", html.classList.contains("busy"), true);
eq("confirmed: status text", toastEl.textContent, "Forgetting in UniFi…");
const blocked2 = submit(b, false);
eq("second click is blocked", blocked2, true);

console.log(fails ? fails + " failed" : "all passed");
process.exit(fails ? 1 : 0);
