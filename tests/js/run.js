// Runs the browser-script checks for app/static/app.js against a simulated page.
// Usage: node tests/js/run.js    (Node 18 or newer; no packages needed)
const { execFileSync } = require("child_process");
const fs = require("fs");
const path = require("path");

const app = path.join(__dirname, "..", "..", "app", "static", "app.js");
let failed = 0;
for (const file of fs.readdirSync(__dirname).filter(f => f.endsWith(".test.js")).sort()) {
  try {
    const out = execFileSync(process.execPath, [path.join(__dirname, file), app], { encoding: "utf8" });
    console.log(`${file}: ${out.trim().split("\n").pop()}`);
  } catch (err) {
    failed++;
    console.log(`${file}: FAILED`);
    console.log(String(err.stdout || "").trim());
  }
}
process.exit(failed ? 1 : 0);
