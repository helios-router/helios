// Cross-browser smoke test for the Photosphere demo (Playwright).
//   NODE_PATH=<dir with node_modules/playwright> node tests/browsers.cjs <demo-url> <out-dir> [firefox,webkit,chromium]
// e.g. tests/demo.sh 8765 &  then  node tests/browsers.cjs http://127.0.0.1:8765/photosphere/index.html /tmp/shots firefox
// For each engine, at phone and desktop size: every page loads without JS errors,
// the page dropdown shows (and navigates) on phones, the tab rows show on desktops.
// Screenshots of every page go to <out-dir>/<engine>-<size>-<tab>.png.
"use strict";
const pw = require("playwright");
const fs = require("fs");
const path = require("path");

const [url, outDir, list = "firefox,webkit"] = process.argv.slice(2);
if (!url || !outDir) { console.error("usage: node tests/browsers.cjs <demo-url> <out-dir> [engines]"); process.exit(2); }
fs.mkdirSync(outDir, { recursive: true });
const SIZES = { phone: { width: 390, height: 844 }, desktop: { width: 1366, height: 900 } };

async function run(engine, size) {
  const browser = await pw[engine].launch();
  const ctx = await browser.newContext({ viewport: SIZES[size], deviceScaleFactor: 1 });
  // Advanced mode on, so every tab is reachable
  await ctx.addInitScript(() => { try { localStorage.setItem("photosphere-advanced", "1"); } catch (e) { /* ignore */ } });
  const page = await ctx.newPage();
  const errors = [];
  page.on("pageerror", e => errors.push(`pageerror: ${e.message}`));
  page.on("console", m => { if (m.type() === "error") errors.push(`console: ${m.text()}`); });
  const res = { engine, size, problems: [], pages: 0 };
  const fail = msg => res.problems.push(msg);

  await page.goto(url + "#overview", { waitUntil: "load" });
  await page.waitForSelector("#view .tile, #view .card", { timeout: 10000 }).catch(() => fail("overview never rendered"));
  const menu = await page.$eval("#tab-menu", el => ({ display: getComputedStyle(el).display, options: el.options.length }));
  const tabs = await page.$eval("#tabs", el => getComputedStyle(el).display);
  if (size === "phone") {
    if (menu.display === "none") fail("page dropdown hidden on a phone");
    if (menu.options < 7) fail(`page dropdown has only ${menu.options} entries`);
    if (tabs !== "none") fail("tab rows still shown on a phone");
    await page.selectOption("#tab-menu", "devices").catch(e => fail(`can't pick from the dropdown: ${e.message}`));
    await page.waitForFunction(() => location.hash === "#devices", null, { timeout: 3000 }).catch(() => fail("dropdown didn't navigate"));
  } else {
    if (menu.display !== "none") fail("page dropdown shown on a desktop");
    if (tabs === "none") fail("tab rows hidden on a desktop");
  }
  // Header: the title must not collide with any tab or button
  const overlap = await page.evaluate(() => {
    const b = document.querySelector(".brand").getBoundingClientRect();
    return [...document.querySelectorAll("#tabs a, #tab-menu, .head-tools button")].filter(e => e.offsetParent).some(e => {
      const r = e.getBoundingClientRect(); return r.left < b.right && r.right > b.left && r.top < b.bottom && r.bottom > b.top; });
  });
  if (overlap) fail("header title overlaps the tabs");
  if (await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1)) fail("page scrolls sideways");

  const all = await page.$$eval("#tab-menu option", os => os.map(o => o.value));
  for (const tab of all) {
    const before = errors.length;
    await page.goto(`${url}#${tab}`);
    await page.waitForTimeout(900);
    const filled = await page.$eval("#view", el => el.children.length > 0 && !el.querySelector(".loading"));
    if (!filled) fail(`${tab}: nothing rendered`);
    if (errors.length > before) fail(`${tab}: ${errors.slice(before).join(" | ")}`);
    await page.screenshot({ path: path.join(outDir, `${engine}-${size}-${tab}.png`), fullPage: true });
    res.pages++;
  }
  await browser.close();
  return res;
}

(async () => {
  const results = [];
  for (const engine of list.split(",")) for (const size of Object.keys(SIZES)) {
    try { results.push(await run(engine, size)); }
    catch (e) { results.push({ engine, size, problems: [`couldn't run: ${e.message.split("\n")[0]}`], pages: 0 }); }
  }
  for (const r of results) console.log(`${r.problems.length ? "FAIL" : "ok  "} ${r.engine.padEnd(8)} ${r.size.padEnd(7)} ${r.pages} pages${r.problems.length ? "\n       - " + r.problems.join("\n       - ") : ""}`);
  process.exit(results.some(r => r.problems.length) ? 1 : 0);
})();
