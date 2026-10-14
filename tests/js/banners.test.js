// Browser-script check for app/static/app.js. Run all with: node tests/js/run.js
const fs = require("fs");
const src = fs.readFileSync(process.argv[2], "utf8");

function classes() {
  const s = new Set();
  return { add: c => s.add(c), remove: c => s.delete(c), contains: c => s.has(c), toggle() {} };
}
function el(cls) {
  const listeners = {};
  const c = classes();
  cls.split(" ").forEach(x => x && c.add(x));
  const attrs = {};
  return { classList: c, children: [], parentNode: null, listeners,
    getAttribute: k => (k in attrs ? attrs[k] : null), setAttribute: (k, v) => { attrs[k] = String(v); },
    appendChild(ch) { ch.parentNode = this; this.children.push(ch); },
    removeChild(ch) { this.children = this.children.filter(x => x !== ch); ch.parentNode = null; },
    addEventListener(ev, fn) { listeners[ev] = fn; } };
}
const page = el("");
const ok = el("banner ok"), bad = el("banner bad");
page.appendChild(ok); page.appendChild(bad);
const handlers = {};
let replaced = null;
const timers = [];
global.setTimeout = (fn, ms) => { timers.push({ fn, ms }); return timers.length; };
global.clearTimeout = id => { if (timers[id - 1]) timers[id - 1].fn = () => {}; };
global.location = { search: "?zoom=3&saved=Printers&forgot=Old%20phone", pathname: "/vlan/3", hash: "#dup" };
global.history = { replaceState: (a, b, url) => { replaced = url; } };
global.window = { addEventListener() {}, history: global.history };
global.navigator = {};
global.document = {
  documentElement: { classList: classes() },
  body: el(""),
  createElement: () => el(""),
  addEventListener: (ev, fn) => { (handlers[ev] = handlers[ev] || []).push(fn); },
  querySelectorAll: sel => (sel === ".banner" ? [ok, bad] : []),
};
eval(src);
handlers.DOMContentLoaded.forEach(f => f());

let fails = 0;
const eq = (l, g, w) => { const p = g === w; if (!p) fails++; console.log((p ? "ok   " : "FAIL ") + l + ": " + JSON.stringify(g)); };

eq("url cleaned, zoom and hash kept", replaced, "/vlan/3?zoom=3#dup");
eq("ok banner has close button", ok.children.length, 1);
eq("error banner has close button", bad.children.length, 1);
eq("only the ok banner schedules a fade", timers.filter(t => t.ms === 6000).length, 1);
timers.filter(t => t.ms === 6000).forEach(t => t.fn());
eq("ok banner fading", ok.classList.contains("leaving"), true);
timers.filter(t => t.ms === 260).forEach(t => t.fn());
eq("ok banner removed", page.children.includes(ok), false);
eq("error banner still there", page.children.includes(bad), true);
bad.children[0].listeners.click();
timers.filter(t => t.ms === 260).forEach(t => t.fn());
eq("error banner closed by its button", page.children.includes(bad), false);

console.log(fails ? fails + " failed" : "all passed");
process.exit(fails ? 1 : 0);
