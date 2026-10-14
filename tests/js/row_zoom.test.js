// Browser-script check for app/static/app.js. Run all with: node tests/js/run.js
const fs = require("fs");
const src = fs.readFileSync(process.argv[2], "utf8");
const handlers = {};
global.document = {
  documentElement: { classList: { add() {}, remove() {}, contains: () => false, toggle() {} } },
  body: { appendChild() {} },
  createElement: () => ({ classList: { add() {}, remove() {} }, setAttribute() {}, addEventListener() {}, appendChild() {} }),
  addEventListener: (ev, fn) => { (handlers[ev] = handlers[ev] || []).push(fn); },
  querySelectorAll: () => [],
};
let selection = "";
global.window = { addEventListener() {}, getSelection: () => selection };
global.navigator = {};
global.location = { search: "", pathname: "/vlan/3", hash: "", href: "/vlan/3" };
global.history = { replaceState() {} };
eval(src);
handlers.DOMContentLoaded.forEach(f => f());

const row = { getAttribute: k => (k === "data-href" ? "/vlan/3?zoom=7" : null) };
// target that reports what it is inside of
function target(insideRow, insideControl) {
  return { closest: sel => (sel === "tr[data-href]" ? (insideRow ? row : null) : sel.indexOf("a, button") === 0 && insideControl ? {} : null) };
}
function click(t) { location.href = "/vlan/3"; handlers.click.forEach(f => f({ target: t })); return location.href; }

let fails = 0;
const eq = (l, g, w) => { const p = g === w; if (!p) fails++; console.log((p ? "ok   " : "FAIL ") + l + ": " + JSON.stringify(g)); };
eq("click on a plain cell navigates", click(target(true, false)), "/vlan/3?zoom=7");
eq("click on Edit/Delete/copy is left alone", click(target(true, true)), "/vlan/3");
eq("click outside range rows does nothing", click(target(false, false)), "/vlan/3");
selection = "10.1.20.16";
eq("selecting text does not navigate", click(target(true, false)), "/vlan/3");
console.log(fails ? fails + " failed" : "all passed");
process.exit(fails ? 1 : 0);
