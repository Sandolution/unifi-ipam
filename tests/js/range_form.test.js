// Browser-script check for app/static/app.js. Run all with: node tests/js/run.js
// Drive app.js's range-form logic with a minimal fake DOM.
const fs = require("fs");
const src = fs.readFileSync(process.argv[2], "utf8");

function input(name, value) {
  const listeners = {};
  const classes = new Set();
  return {
    name, value: value || "",
    classList: { add: (...c) => c.forEach(x => classes.add(x)), remove: (...c) => c.forEach(x => classes.delete(x)), has: c => classes.has(c) },
    addEventListener: (ev, fn) => { listeners[ev] = fn; },
    type(v) { this.value = v; listeners.input && listeners.input(); },
  };
}
function row(prefix, start, end, size, first, last) {
  const f = { start_ip: input("start_ip", start), end_ip: input("end_ip", end), size: input("size", size) };
  return {
    f,
    getAttribute: a => (a === "data-prefix" ? prefix : a === "data-first" ? (first || null) : a === "data-last" ? (last || null) : null),
    querySelector: sel => f[sel.match(/name="([^"]+)"/)[1]],
  };
}
let ready;
global.document = {
  addEventListener: (ev, fn) => { if (ev === "DOMContentLoaded") ready = fn; },
  querySelectorAll: sel => (sel === "tr[data-calc]" ? rows : []),
};
global.navigator = {};
global.location = { search: "", pathname: "/", hash: "" };
global.history = { replaceState() {} };
global.window = { addEventListener() {} };
let rows = [];
eval(src);

let fails = 0;
function eq(label, got, want) {
  const ok = got === want;
  if (!ok) fails++;
  console.log((ok ? "ok   " : "FAIL ") + label + ": " + JSON.stringify(got) + (ok ? "" : " want " + JSON.stringify(want)));
}

// 1. new row, /24: start + end -> size
let a = row("10.1.20.", "", "", ""); rows = [a]; ready();
a.f.start_ip.type("24"); a.f.end_ip.type("31");
eq("start+end -> size", a.f.size.value, "8");
// 2. then change size -> end follows (start, size most recent)
a.f.size.type("10");
eq("then size -> end", a.f.end_ip.value, "33");
// 3. then change start -> end follows (size, start most recent)
a.f.start_ip.type("40");
eq("then start -> end", a.f.end_ip.value, "49");

// 4. end + size -> start
let b = row("10.1.20.", "", "", ""); rows = [b]; ready();
b.f.end_ip.type("60"); b.f.size.type("40");
eq("end+size -> start", b.f.start_ip.value, "21");

// 5. edit row prefilled: start/end set, changing size moves end
let c = row("10.1.40.", "21", "60", "40"); rows = [c]; ready();
c.f.size.type("20");
eq("edit row size -> end", c.f.end_ip.value, "40");

// 6. full addresses typed in a /24 row
let d = row("10.1.20.", "", "", ""); rows = [d]; ready();
d.f.start_ip.type("10.1.20.100"); d.f.size.type("5");
eq("full start + size -> end", d.f.end_ip.value, "104");

// 7. larger subnet, no prefix: full addresses, crossing an octet
let e = row("", "", "", ""); rows = [e]; ready();
e.f.start_ip.type("10.2.0.250"); e.f.size.type("10");
eq("no prefix, crosses octet", e.f.end_ip.value, "10.2.1.3");

// 8. end before start -> size blank + invalid
let g = row("10.1.20.", "", "", ""); rows = [g]; ready();
g.f.start_ip.type("50"); g.f.end_ip.type("40");
eq("end < start -> size blank", g.f.size.value, "");
eq("end < start -> marked invalid", g.f.size.classList.has("invalid"), true);

// 9. garbage input -> no derivation, nothing overwritten
let h = row("10.1.20.", "", "", ""); rows = [h]; ready();
h.f.start_ip.type("abc"); h.f.size.type("5");
eq("garbage start leaves end alone", h.f.end_ip.value, "");

// 10. start + size, then edit end -> size recalculated, start kept
let k = row("10.1.20.", "", "", ""); rows = [k]; ready();
k.f.start_ip.type("24"); k.f.size.type("10");
eq("start+size -> end", k.f.end_ip.value, "33");
k.f.end_ip.type("40");
eq("then end -> size, start kept", k.f.size.value + "/" + k.f.start_ip.value, "17/24");

// 11. re-rendered form after a validation error: start/end present, size empty -> size filled on load
let m = row("10.1.20.", "100", "109", ""); rows = [m]; ready();
eq("size filled on load", m.f.size.value, "10");

// 12. the user's sequence: End first, then Size -> Start
let u = row("10.1.30.", "", "", "", "10.1.30.1", "10.1.30.254"); rows = [u]; ready();
u.f.name = null; u.f.end_ip.type("31"); u.f.size.type("16");
eq("end then size -> start", u.f.start_ip.value + "|" + u.f.end_ip.value, "16|31");

// 13. a result outside the subnet is never written as a foreign address
let w = row("10.1.30.", "", "", "", "10.1.30.1", "10.1.30.254"); rows = [w]; ready();
w.f.start_ip.type("250"); w.f.size.type("16");
eq("start 250 + size 16 -> end left empty", w.f.end_ip.value, "");
eq("  and flagged invalid", w.f.end_ip.classList.has("invalid"), true);
let x = row("10.1.30.", "", "", "", "10.1.30.1", "10.1.30.254"); rows = [x]; ready();
x.f.end_ip.type("5"); x.f.size.type("16");
eq("end 5 + size 16 -> start left empty", x.f.start_ip.value, "");

// 14. a typed address from another subnet is flagged, not used
let y = row("10.1.30.", "", "", "", "10.1.30.1", "10.1.30.254"); rows = [y]; ready();
y.f.start_ip.type("10.1.20.16"); y.f.size.type("8");
eq("foreign start flagged", y.f.start_ip.classList.has("invalid") + "|" + y.f.end_ip.value, "true|");

console.log(fails ? fails + " failed" : "all passed");
process.exit(fails ? 1 : 0);
