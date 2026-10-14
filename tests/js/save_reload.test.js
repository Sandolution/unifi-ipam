// Browser-script check for app/static/app.js. Run all with: node tests/js/run.js
// Save -> reload -> message restored, driven with a fake page.
const fs = require("fs");
const src = fs.readFileSync(process.argv[2], "utf8");

function classes(init) {
  const s = new Set(init);
  return { add: c => s.add(c), remove: c => s.delete(c), contains: c => s.has(c), toggle() {} };
}
function banner(cls, html) {
  const attrs = {};
  const kids = [];
  const el = {
    className: cls, classList: classes(cls.split(" ")), innerHTML: html, children: kids,
    getAttribute: k => (k in attrs ? attrs[k] : null), setAttribute: (k, v) => { attrs[k] = v; },
    addEventListener() {}, appendChild(c) { kids.push(c); c.parentNode = el; },
    removeChild(c) { kids.splice(kids.indexOf(c), 1); },
    cloneNode() { return { innerHTML: html, querySelector: () => null }; },
  };
  return el;
}
const storage = {};
global.sessionStorage = {
  getItem: k => (k in storage ? storage[k] : null), setItem: (k, v) => { storage[k] = v; }, removeItem: k => { delete storage[k]; },
};
let reloads = 0;
global.location = { search: "", pathname: "/vlan/3", hash: "", reload: () => { reloads++; } };
global.history = { replaceState() {} };
global.navigator = {};
global.window = { addEventListener() {}, getSelection: () => "" };

function page(rowEditing, toastList) {
  const handlers = {};
  const box = { children: toastList, appendChild(d) { toastList.push(d); },
    querySelectorAll: sel => toastList.filter(b => sel.indexOf(".banner") === 0 && !b.classList.contains("leaving")) };
  const row = { id: "dev-1", classList: classes(rowEditing ? ["editing"] : []) };
  global.document = {
    visibilityState: "visible",
    documentElement: { classList: classes([]) },
    body: { hasAttribute: () => false, appendChild() {} },
    createElement: () => banner("", ""),
    getElementById: id => (id === "toasts" ? box : id === "dev-1" ? row : null),
    addEventListener: (ev, fn) => { (handlers[ev] = handlers[ev] || []).push(fn); },
    querySelector: () => null,
    querySelectorAll: sel => (sel === ".banner" ? toastList : []),
  };
  eval(src);
  handlers.DOMContentLoaded.forEach(f => f());
  const saveBtn = { matches: s => s === "[data-save]", closest: () => row, textContent: "Save" };
  return {
    save() {
      handlers["htmx:beforeRequest"].forEach(f => f({ detail: { elt: saveBtn } }));
      handlers["htmx:afterSettle"].forEach(f => f({}));
    },
    toasts: toastList,
  };
}

let fails = 0;
const eq = (l, g, w) => { const p = JSON.stringify(g) === JSON.stringify(w); if (!p) fails++; console.log((p ? "ok   " : "FAIL ") + l + ": " + JSON.stringify(g)); };

// 1. successful save: row is back to read-only, message in the stack -> reload + stash
let p1 = page(false, [banner("banner ok", "Saved in UniFi.")]);
p1.save();
eq("successful save reloads", reloads, 1);
eq("message stashed", JSON.parse(storage["ipam.toasts"]).map(t => t.html), ["Saved in UniFi."]);

// 2. the reloaded page shows the stashed message once
let p2 = page(false, []);
eq("message restored after reload", p2.toasts.map(t => t.innerHTML), ["Saved in UniFi."]);
eq("stash cleared", "ipam.toasts" in storage, false);
let p3 = page(false, []);
eq("not shown again on a later load", p3.toasts.length, 0);

// 3. failed save: row still in edit mode -> no reload
reloads = 0;
let p4 = page(true, [banner("banner bad", "UniFi refused: …")]);
p4.save();
eq("failed save does not reload", reloads, 0);

console.log(fails ? fails + " failed" : "all passed");
process.exit(fails ? 1 : 0);
