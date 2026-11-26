// Render every Helios page against the mock backend and save screenshots.
// WEBROOT (default /tmp/heliosweb) needs photosphere/, base1/, networks.json and drivers.json (copies of pkgs/*.json).
const puppeteer = require("puppeteer-core");
const http = require("http");
const fs = require("fs");
const path = require("path");

const ROOT = process.env.WEBROOT || "/tmp/heliosweb";
const OUT = process.argv[2] || "/home/claude/shots";
fs.mkdirSync(OUT, { recursive: true });
const types = { ".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".json": "application/json" };
const srv = http.createServer((req, res) => {
  let p;
  try { p = path.resolve(ROOT, "." + decodeURIComponent(req.url.split("?")[0].split("#")[0])); } catch { p = null; }
  if (!p || (p !== path.resolve(ROOT) && !p.startsWith(path.resolve(ROOT) + path.sep))) { res.writeHead(403); res.end(); return; }
  fs.readFile(p, (e, b) => { if (e) { res.writeHead(404); res.end(); return; } res.writeHead(200, { "Content-Type": types[path.extname(p)] || "text/plain" }); res.end(b); });
}).listen(0, "127.0.0.1");

const sleep = ms => new Promise(r => setTimeout(r, ms));

(async () => {
  const port = srv.address().port;
  const browser = await puppeteer.launch({ executablePath: "/usr/bin/google-chrome", headless: "new",
    args: ["--no-sandbox", "--disable-gpu", "--font-render-hinting=none"] });
  const errors = [];
  async function page(dark) {
    const p = await browser.newPage();
    await p.setViewport({ width: 1440, height: 1000, deviceScaleFactor: 1 });
    await p.emulateMediaFeatures([{ name: "prefers-color-scheme", value: dark ? "dark" : "light" }]);
    p.on("pageerror", e => errors.push(`${dark ? "dark" : "light"}: ${e.message}`));
    p.on("console", m => { if (m.type() === "error") errors.push(`console: ${m.text()}`); });
    return p;
  }
  // Pre-fill 3 minutes of live samples so charts look like a page left open
  const prefill = () => {
    const t1 = Date.now() / 1000;
    for (const i of ["wan", "trusted", "family", "iot", "strict", "mgmt", "tailscale0", "ve-heliosphere"]) {
      samples[i] = [];
      for (let k = 89; k >= 0; k--) {
        const t = t1 - k * 2, [rx, tx] = window.__solMock.rates(i, t);
        samples[i].push({ t, down: rx, up: tx });
      }
    }
    document.dispatchEvent(new CustomEvent("helios-samples"));
  };
  async function shot(p, name, opts = {}) {
    await sleep(opts.wait || 900);
    await p.evaluate(prefill);
    await sleep(400);
    await p.screenshot({ path: path.join(OUT, name + ".png"), fullPage: opts.full !== false });
    console.log("saved", name);
  }

  const L = await page(false);
  const base = `http://127.0.0.1:${port}/photosphere/index.html`;
  const TABS = ["overview", "devices", "traffic", "qos", "upnp", "notifications", "history", "networks", "hardware"];
  for (const tab of TABS) {
    await L.goto(`${base}#${tab}`, { waitUntil: "networkidle0" });
    await shot(L, `${String(TABS.indexOf(tab) + 1).padStart(2, "0")}-${tab}`, { wait: 1200 });
  }

  // Name a new device → pending banner
  await L.goto(`${base}#devices`, { waitUntil: "networkidle0" });
  await sleep(800);
  await L.evaluate(() => [...document.querySelectorAll("button")].find(b => b.textContent === "Name it").click());
  await sleep(300);
  await L.evaluate(() => {
    const sel = document.querySelector(".modal select"); sel.value = "iot"; sel.dispatchEvent(new Event("change"));
  });
  await sleep(200);
  await L.screenshot({ path: path.join(OUT, "08-name-device-dialog.png") });
  console.log("saved 08-name-device-dialog");
  await L.evaluate(() => {
    const inp = document.querySelector(".modal input[type=text]"); inp.value = "makemake"; inp.dispatchEvent(new Event("input"));
    [...document.querySelectorAll(".modal button")].find(b => b.textContent === "Save device").click();
  });
  await sleep(1200);
  await L.evaluate(() => refreshPending());
  await sleep(500);
  await L.screenshot({ path: path.join(OUT, "09-pending-banner.png") });
  console.log("saved 09-pending-banner");

  // Apply → confirm countdown (LuCI-style)
  await L.evaluate(() => document.querySelector("#pending-apply").click());
  await sleep(1200);
  await L.screenshot({ path: path.join(OUT, "10-applying.png") });
  await sleep(3000);
  await L.screenshot({ path: path.join(OUT, "11-confirm-or-rollback.png") });
  console.log("saved 09/10 apply flow");

  // Dark mode
  const D = await page(true);
  for (const tab of ["overview", "devices"]) {
    await D.goto(`${base}#${tab}`, { waitUntil: "networkidle0" });
    await shot(D, `12-dark-${tab}`, { wait: 1200 });
  }

  // Narrow (tablet) layout
  const N = await page(false);
  await N.setViewport({ width: 820, height: 1100 });
  await N.goto(`${base}#overview`, { waitUntil: "networkidle0" });
  await shot(N, "13-narrow-overview", { wait: 1200 });

  await browser.close();
  srv.close();
  if (errors.length) { console.log("ERRORS:\n" + errors.join("\n")); process.exitCode = 1; }
})();
