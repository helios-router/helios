/* Photosphere — HeliOS's pages in Cockpit.
 *
 * Everything privileged goes through `helios-ctl` (JSON in, JSON out), run with
 * Cockpit's superuser channel. Patterns borrowed from router UIs:
 *   - OPNsense: dashboard widgets, services widget, config history + restore,
 *               "pending changes / Apply" banner
 *   - OpenWrt LuCI: apply with automatic rollback unless you confirm,
 *               realtime traffic graphs, SQM settings page
 *   - UniFi:     client list with network filters and "new client" highlighting
 *   - Firewalla: one-tap "pause internet" per device
 */
"use strict";

const CTL = "/run/current-system/sw/bin/helios-ctl";
// Networks and roles come from helios-ctl (state.json + pkgs/networks.json)
let NETWORKS = [];
let ROLES = {};
let ACCESS_HELP = {};
const netInfo = name => NETWORKS.find(n => n.name === name) || { name, label: name, vlan: "?", prefix: "", color: "#888", role: "", access: {} };
const loadNetworks = () => api("networks").then(r => { NETWORKS = r.networks; ROLES = r.roles; ACCESS_HELP = r.access; return r; });
// Filtering profiles (editable on the Filtering tab), filled from helios-ctl
let PROFILES = {};
const setProfiles = list => { if (Array.isArray(list)) PROFILES = Object.fromEntries(list.map(p => [p.name, p])); };
const ifaceLabels = () => ({ wan: "Internet (WAN)", ...Object.fromEntries(NETWORKS.map(n => [n.name, n.label])),
  tailscale0: "Tailscale (admin)", "ve-heliosphere": "Heliosphere (family exit)" });
// Naming theme: planets & moons for people's devices, Kuiper belt for IoT
const NAMES_INNER = ["mercury", "venus", "earth", "mars", "deimos", "saturn", "uranus", "neptune", "europa", "ganymede",
  "callisto", "titan", "enceladus", "mimas", "rhea", "iapetus", "dione", "tethys", "hyperion", "miranda", "ariel",
  "umbriel", "titania", "oberon", "triton", "nereid", "proteus", "amalthea", "himalia", "ceres", "vesta", "pallas"];
const NAMES_KUIPER = ["pluto", "charon", "styx", "nix", "kerberos", "hydra", "eris", "dysnomia", "haumea", "hiiaka",
  "namaka", "makemake", "quaoar", "weywot", "orcus", "vanth", "gonggong", "varuna", "ixion", "salacia", "varda",
  "huya", "arrokoth", "sedna", "chaos", "deucalion"];
let RESERVED = ["heliosphere"];
let DOMAIN = "home.arpa";        // from helios-ctl status   // + router hostname, filled in from helios-ctl status

/* ----------------------------------------------------------------- utils */
const $ = (sel, root = document) => root.querySelector(sel);

function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "style") el.setAttribute("style", v);
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (v === true) el.setAttribute(k, "");
    else el.setAttribute(k, v);
  }
  for (const kid of kids.flat(Infinity)) {
    if (kid === null || kid === undefined || kid === false) continue;
    el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return el;
}
const svgNS = "http://www.w3.org/2000/svg";
function s(tag, attrs, ...kids) {
  const el = document.createElementNS(svgNS, tag);
  for (const [k, v] of Object.entries(attrs || {})) if (v !== null && v !== undefined) el.setAttribute(k, v);
  for (const kid of kids.flat()) if (kid) el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  return el;
}

function fmtBytes(n) {
  if (n === null || n === undefined || isNaN(n)) return "—";
  const u = ["B", "KB", "MB", "GB", "TB", "PB"];
  let i = 0; n = Number(n);
  while (Math.abs(n) >= 1000 && i < u.length - 1) { n /= 1000; i++; }
  return `${n >= 100 || i === 0 ? n.toFixed(0) : n.toFixed(1)} ${u[i]}`;
}
function fmtRate(bps) {
  if (bps === null || bps === undefined || isNaN(bps)) return "—";
  const u = ["b/s", "kb/s", "Mb/s", "Gb/s"];
  let i = 0;
  while (bps >= 1000 && i < u.length - 1) { bps /= 1000; i++; }
  return `${bps >= 100 || i === 0 ? bps.toFixed(0) : bps.toFixed(1)} ${u[i]}`;
}
function rateParts(bps) { const t = fmtRate(bps).split(" "); return [t[0], t[1] || ""]; }
function fmtDur(sec) {
  sec = Math.max(0, Math.round(sec || 0));
  const d = Math.floor(sec / 86400), hh = Math.floor(sec % 86400 / 3600), m = Math.floor(sec % 3600 / 60);
  if (d) return `${d}d ${hh}h`;
  if (hh) return `${hh}h ${m}m`;
  if (m) return `${m}m ${sec % 60}s`;
  return `${sec}s`;
}
function fmtAgo(ts) {
  if (!ts) return "—";
  const d = Date.now() / 1000 - ts;
  if (d < 0) return "in " + fmtDur(-d);
  if (d < 45) return "just now";
  return fmtDur(d) + " ago";
}
function fmtTime(ts, withDate = true) {
  if (!ts) return "—";
  const d = new Date(ts * 1000);
  return withDate ? d.toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })
    : d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}
function parseRate(str) {  // "500mbit" → bits/s
  const m = /^(\d+(?:\.\d+)?)(kbit|mbit|gbit)$/.exec(str || "");
  if (!m) return null;
  return Number(m[1]) * { kbit: 1e3, mbit: 1e6, gbit: 1e9 }[m[2]];
}

function icon(kind) {
  const paths = {
    ok: "M8 1a7 7 0 1 0 0 14A7 7 0 0 0 8 1zm3.3 5.2-3.9 4.2a.8.8 0 0 1-1.1 0L4.7 8.8a.75.75 0 1 1 1.1-1l1.1 1.1 3.3-3.6a.75.75 0 0 1 1.1 1z",
    warn: "M8.9 1.6a1 1 0 0 0-1.8 0L.6 13.3A1 1 0 0 0 1.5 15h13a1 1 0 0 0 .9-1.7zM8 5c.5 0 .8.4.8.8v3.6a.8.8 0 0 1-1.6 0V5.8c0-.4.3-.8.8-.8zm0 7.9a1 1 0 1 1 0-2 1 1 0 0 1 0 2z",
    bad: "M8 1a7 7 0 1 0 0 14A7 7 0 0 0 8 1zm2.8 8.7a.75.75 0 1 1-1.1 1.1L8 9.1l-1.7 1.7a.75.75 0 1 1-1.1-1.1L6.9 8 5.2 6.3a.75.75 0 1 1 1.1-1.1L8 6.9l1.7-1.7a.75.75 0 1 1 1.1 1.1L9.1 8z",
  };
  return s("svg", { class: `icon ${kind}`, viewBox: "0 0 16 16", "aria-hidden": "true" }, s("path", { d: paths[kind], fill: "currentColor" }));
}
function statusEl(kind, text) { return h("span", { class: "status" }, icon(kind), text); }
function vlanPill(v) {
  const info = netInfo(v);
  return h("span", { class: "pill", title: info.desc || (ROLES[info.role] || {}).desc || "" }, h("span", { class: "swatch", style: `background:${info.color}` }), info.label);
}

function toast(msg, bad = false) {
  const t = h("div", { class: "toast" + (bad ? " bad" : "") }, msg);
  $("#toasts").append(t);
  setTimeout(() => t.remove(), bad ? 7000 : 3500);
}

/* ------------------------------------------------------------- theming */
const THEME_KEY = "photosphere-theme";   // "light" | "dark"; absent = follow Cockpit / the OS
function isDark() {
  let choice = null, style = "auto";
  try { choice = localStorage.getItem(THEME_KEY); style = localStorage.getItem("shell:style") || "auto"; } catch (e) { /* storage blocked */ }
  if (choice === "dark" || choice === "light") return choice === "dark";
  return style === "dark" || (style === "auto" && matchMedia("(prefers-color-scheme: dark)").matches);
}
function applyTheme() {
  const dark = isDark();
  document.documentElement.classList.toggle("dark", dark);
  const b = document.getElementById("theme-btn");
  if (b) {
    b.title = b.ariaLabel = dark ? "Switch to light theme" : "Switch to dark theme";
    b.replaceChildren(s("svg", { viewBox: "0 0 24 24", width: 18, height: 18, fill: "none", stroke: "currentColor", "stroke-width": 2, "stroke-linecap": "round", "stroke-linejoin": "round", "aria-hidden": "true" },
      ...(dark ? [s("circle", { cx: 12, cy: 12, r: 4 }), s("path", { d: "M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" })]
        : [s("path", { d: "M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z" })])));
  }
  document.dispatchEvent(new CustomEvent("helios-samples"));   // charts redraw
}
window.addEventListener("storage", applyTheme);
$("#theme-btn").addEventListener("click", () => {
  try { localStorage.setItem(THEME_KEY, isDark() ? "light" : "dark"); } catch (e) { /* storage blocked */ }
  applyTheme();
});
applyTheme();
matchMedia("(prefers-color-scheme: dark)").addEventListener("change", applyTheme);

/* ---------------------------------------------------------------- backend */
function api(...args) { return apiInput(null, ...args); }
// Like api(), but feeds `input` to the command's stdin (keeps secrets out of argv / ps).
function apiInput(input, ...args) {
  const proc = cockpit.spawn([CTL, ...args], { superuser: "require", err: "message" });
  if (input != null) proc.input(input);
  return proc
    .then(out => {
      $("#access").hidden = true;
      const data = JSON.parse(out);
      if (!data.ok) throw new Error(data.error || "helios-ctl failed");
      return data;
    }, ex => {
      if (ex && (ex.problem === "access-denied" || ex.problem === "not-authorized")) $("#access").hidden = false;
      let msg = ex && (ex.message || ex.problem) || String(ex);
      try { const j = JSON.parse(msg); if (j.error) msg = j.error; } catch (e) { /* not JSON */ }
      throw new Error(msg);
    });
}
function systemctl(verb, unit) {
  return cockpit.spawn(["systemctl", verb, unit], { superuser: "require", err: "message" });
}

/* ------------------------------------------------- live traffic sampling */
const samples = {};          // iface → [{t, down, up}] in bits/s, user's perspective
let lastCounters = null;
const HISTORY = 90;          // 3 minutes at 2 s
function dirOf(iface, rx, tx) {
  // WAN: rx is download. LAN/VLAN: the router transmits what devices download.
  return iface === "wan" ? { down: rx, up: tx } : { down: tx, up: rx };
}
function sampleCounters() {
  return api("counters").then(c => {
    if (lastCounters) {
      const dt = c.t - lastCounters.t;
      if (dt > 0) {
        for (const [i, v] of Object.entries(c.ifaces)) {
          const p = lastCounters.ifaces[i];
          if (!p) continue;
          const rx = Math.max(0, (v.rx - p.rx) * 8 / dt), tx = Math.max(0, (v.tx - p.tx) * 8 / dt);
          const arr = samples[i] || (samples[i] = []);
          arr.push({ t: c.t, ...dirOf(i, rx, tx) });
          if (arr.length > HISTORY) arr.shift();
        }
      }
    }
    lastCounters = c;
    document.dispatchEvent(new CustomEvent("helios-samples"));
  }).catch(() => {});
}
setInterval(sampleCounters, 2000);
sampleCounters();

/* ---------------------------------------------------------------- charts */
const tooltip = () => $("#tooltip");
function showTip(ev, head, rows) {
  const tt = tooltip();
  tt.replaceChildren(h("div", { class: "tt-head" }, head),
    ...rows.map(r => h("div", { class: "tt-row" },
      h("span", {}, h("i", { class: "swatch", style: `background:${r.color}` }), r.label), h("strong", {}, r.value))));
  tt.hidden = false;
  const pad = 14, w = tt.offsetWidth, hgt = tt.offsetHeight;
  let x = ev.clientX + pad, y = ev.clientY + pad;
  if (x + w > innerWidth - 8) x = ev.clientX - w - pad;
  if (y + hgt > innerHeight - 8) y = ev.clientY - hgt - pad;
  tt.style.left = x + "px"; tt.style.top = y + "px";
}
function hideTip() { tooltip().hidden = true; }

function niceMax(v) {
  if (!v || v <= 0) return 1;
  const p = Math.pow(10, Math.floor(Math.log10(v)));
  for (const m of [1, 2, 2.5, 5, 10]) if (m * p >= v) return m * p;
  return 10 * p;
}

/* Live line chart: two series (download/upload), crosshair + tooltip, end labels */
function lineChart(host, data, { height = 220, fmt = fmtRate } = {}) {
  const W = Math.max(320, host.clientWidth || 600), H = height;
  const m = { l: 58, r: 96, t: 10, b: 26 };
  const iw = W - m.l - m.r, ih = H - m.t - m.b;
  const series = [
    { key: "down", label: "Download", color: "var(--rx)" },
    { key: "up", label: "Upload", color: "var(--tx)" },
  ];
  const svg = s("svg", { class: "chart", viewBox: `0 0 ${W} ${H}`, width: W, height: H, role: "img",
    "aria-label": "Live throughput chart" });
  if (data.length < 2) {
    svg.append(s("text", { x: W / 2, y: H / 2, "text-anchor": "middle", class: "tick" }, "Collecting samples…"));
    host.replaceChildren(svg);
    return;
  }
  const t0 = data[0].t, t1 = data[data.length - 1].t;
  const span = Math.max(t1 - t0, 1);
  const ymax = niceMax(Math.max(...data.map(d => Math.max(d.down, d.up))) * 1.1);
  const x = t => m.l + (t - t0) / span * iw;
  const y = v => m.t + ih - v / ymax * ih;
  for (let i = 0; i <= 4; i++) {
    const v = ymax * i / 4, yy = y(v);
    svg.append(s("line", { class: i === 0 ? "baseline" : "gridline", x1: m.l, x2: m.l + iw, y1: yy, y2: yy }));
    svg.append(s("text", { class: "tick", x: m.l - 8, y: yy + 4, "text-anchor": "end" }, fmt(v)));
  }
  for (const sec of [0, 30, 60, 90, 120, 150, 180]) {
    const tt = t1 - sec;
    if (tt < t0) break;
    svg.append(s("text", { class: "tick", x: x(tt), y: H - 6, "text-anchor": "middle" }, sec === 0 ? "now" : `-${sec}s`));
  }
  const ends = [];
  for (const se of series) {
    const pts = data.map(d => `${x(d.t).toFixed(1)},${y(d[se.key]).toFixed(1)}`);
    svg.append(s("path", { class: "area", fill: se.color,
      d: `M${x(t0)},${y(0)} L${pts.join(" L")} L${x(t1)},${y(0)} Z` }));
    svg.append(s("path", { class: "line", stroke: se.color, d: "M" + pts.join(" L") }));
    const last = data[data.length - 1][se.key];
    ends.push({ se, yy: y(last), v: last });
  }
  // direct end labels, nudged apart so they never collide
  ends.sort((a, b) => a.yy - b.yy);
  if (ends.length === 2 && ends[1].yy - ends[0].yy < 16) { const mid = (ends[0].yy + ends[1].yy) / 2; ends[0].yy = mid - 8; ends[1].yy = mid + 8; }
  for (const e of ends) {
    svg.append(s("circle", { class: "marker", cx: x(t1), cy: y(e.v), r: 4, fill: e.se.color }));
    svg.append(s("text", { class: "endlabel", x: x(t1) + 10, y: e.yy + 4 }, `${e.se.key === "down" ? "↓" : "↑"} ${fmt(e.v)}`));
  }
  // crosshair
  const cross = s("line", { class: "crosshair", y1: m.t, y2: m.t + ih, visibility: "hidden" });
  const dots = series.map(se => s("circle", { class: "marker", r: 4, fill: se.color, visibility: "hidden" }));
  svg.append(cross, ...dots);
  const hit = s("rect", { x: m.l, y: m.t, width: iw, height: ih, fill: "transparent" });
  hit.addEventListener("pointermove", ev => {
    const r = svg.getBoundingClientRect();
    const px = (ev.clientX - r.left) * (W / r.width);
    const tt = t0 + (px - m.l) / iw * span;
    let best = data[0];
    for (const d of data) if (Math.abs(d.t - tt) < Math.abs(best.t - tt)) best = d;
    const cx = x(best.t);
    cross.setAttribute("x1", cx); cross.setAttribute("x2", cx); cross.setAttribute("visibility", "visible");
    series.forEach((se, i) => { dots[i].setAttribute("cx", cx); dots[i].setAttribute("cy", y(best[se.key])); dots[i].setAttribute("visibility", "visible"); });
    showTip(ev, `${Math.round(t1 - best.t)}s ago`, series.map(se => ({ label: se.label, color: se.color, value: fmt(best[se.key]) })));
  });
  hit.addEventListener("pointerleave", () => { cross.setAttribute("visibility", "hidden"); dots.forEach(d => d.setAttribute("visibility", "hidden")); hideTip(); });
  svg.append(hit);
  host.replaceChildren(svg);
}

/* Grouped bars (download/upload per day) with rounded data-ends and per-bar tooltips */
function barChart(host, days, { height = 220 } = {}) {
  const W = Math.max(320, host.clientWidth || 600), H = height;
  const m = { l: 58, r: 12, t: 10, b: 28 };
  const iw = W - m.l - m.r, ih = H - m.t - m.b;
  const svg = s("svg", { class: "chart", viewBox: `0 0 ${W} ${H}`, width: W, height: H, role: "img", "aria-label": "Daily traffic" });
  if (!days.length) { host.replaceChildren(h("div", { class: "empty" }, "No daily totals yet.")); return; }
  const ymax = niceMax(Math.max(...days.map(d => Math.max(d.down, d.up))) * 1.05);
  const y = v => m.t + ih - v / ymax * ih;
  for (let i = 0; i <= 4; i++) {
    const v = ymax * i / 4;
    svg.append(s("line", { class: i === 0 ? "baseline" : "gridline", x1: m.l, x2: m.l + iw, y1: y(v), y2: y(v) }));
    svg.append(s("text", { class: "tick", x: m.l - 8, y: y(v) + 4, "text-anchor": "end" }, fmtBytes(v)));
  }
  const slot = iw / days.length, gap = 2, bw = Math.min(18, (slot * 0.7 - gap) / 2);
  const barPath = (x0, v) => {
    const top = y(v), base = y(0), r = Math.min(4, bw / 2, base - top);
    if (base - top < 0.5) return "";
    return `M${x0},${base} V${top + r} Q${x0},${top} ${x0 + r},${top} H${x0 + bw - r} Q${x0 + bw},${top} ${x0 + bw},${top + r} V${base} Z`;
  };
  days.forEach((d, i) => {
    const cx = m.l + slot * i + slot / 2;
    const x0 = cx - bw - gap / 2, x1 = cx + gap / 2;
    const rows = [{ label: "Download", color: "var(--rx)", value: fmtBytes(d.down) }, { label: "Upload", color: "var(--tx)", value: fmtBytes(d.up) }];
    for (const [xx, v, c] of [[x0, d.down, "var(--rx)"], [x1, d.up, "var(--tx)"]]) {
      const p = barPath(xx, v);
      if (p) svg.append(s("path", { class: "bar", d: p, fill: c }));
    }
    const hit = s("rect", { x: m.l + slot * i, y: m.t, width: slot, height: ih, fill: "transparent", tabindex: 0 });
    hit.addEventListener("pointermove", ev => showTip(ev, d.label, rows));
    hit.addEventListener("pointerleave", hideTip);
    svg.append(hit);
    if (days.length <= 16 || i % 2 === 0)
      svg.append(s("text", { class: "tick", x: cx, y: H - 8, "text-anchor": "middle" }, d.short));
  });
  host.replaceChildren(svg);
}

function legend(items) {
  return h("div", { class: "legend" }, items.map(([label, color]) => h("span", {}, h("i", { class: "ln", style: `background:${color}` }), label)));
}

/* ------------------------------------------------------------ pending bar */
let pendingState = { dirty: false, changes: [], apply: {} };
function refreshPending() {
  return api("pending").then(p => {
    pendingState = p;
    const bar = $("#pending");
    bar.hidden = !p.dirty;
    $("#pending-title").textContent = p.changes.length === 1 ? "1 pending change" : `${p.changes.length} pending changes`;
    const items = p.changes.slice(0, 6).map(c => h("li", {}, c));
    if (p.changes.length > 6) items.push(h("li", { class: "muted" }, `…and ${p.changes.length - 6} more`));
    $("#pending-list").replaceChildren(...items);
    const st = (p.apply || {}).state;
    if ((st === "running" || st === "confirm") && !$("#apply-modal")) openApplyModal();
  }).catch(() => {});
}
setInterval(refreshPending, 5000);
$("#pending-discard").addEventListener("click", () => {
  confirmDialog("Discard pending changes?", "Your edits since the last apply are thrown away. Nothing on the router changes.",
    "Discard", true).then(ok => ok && api("discard").then(() => { toast("Pending changes discarded"); refreshPending(); rerender(); }));
});
$("#pending-apply").addEventListener("click", () => {
  api("apply-start").then(() => openApplyModal()).catch(e => toast(e.message, true));
});

/* ---------------------------------------------------------------- modals */
function modal({ title, body, foot, wide = false, id = null, onClose = null }) {
  const close = () => { root.remove(); if (onClose) onClose(); };
  const root = h("div", { class: "backdrop", id },
    h("div", { class: "modal" + (wide ? " wide" : ""), role: "dialog", "aria-modal": "true", "aria-label": title },
      h("div", { class: "modal-head" }, h("h2", {}, title)),
      h("div", { class: "modal-body" }, body),
      h("div", { class: "modal-foot" }, foot(close))));
  $("#modal-root").append(root);
  return { root, close };
}
function confirmDialog(title, text, okLabel, danger = false) {
  return new Promise(resolve => {
    modal({
      title, body: h("p", {}, text),
      foot: close => [
        h("button", { class: "btn " + (danger ? "btn-danger" : "btn-primary"), onclick: () => { close(); resolve(true); } }, okLabel),
        h("button", { class: "btn btn-link", onclick: () => { close(); resolve(false); } }, "Cancel"),
      ],
    });
  });
}

/* Apply flow (LuCI-style): run → confirm within the countdown → or auto-rollback */
function openApplyModal() {
  const status = h("div", {});
  const logEl = h("pre", { class: "log" });
  const footBox = h("div", { style: "display:contents" });
  let timer = null;
  const m = modal({
    id: "apply-modal", wide: true, title: "Applying changes",
    body: [status, logEl], foot: () => footBox,
    onClose: () => clearInterval(timer),
  });
  const setFoot = (...btns) => footBox.replaceChildren(...btns);
  const done = () => { clearInterval(timer); m.close(); refreshPending(); rerender(); };
  const poll = () => api("apply-status").then(r => {
    const st = r.apply || {};
    logEl.textContent = (r.log || []).join("\n");
    logEl.scrollTop = logEl.scrollHeight;
    if (st.state === "running") {
      status.replaceChildren(h("div", {}, "Rebuilding the router with your changes. This usually takes under a minute."),
        h("ul", { class: "small" }, (st.changes || []).map(c => h("li", {}, c))), h("div", { class: "progress" }, h("i")));
      setFoot(h("span", { class: "muted small" }, "You can close this page — the rebuild keeps running."));
    } else if (st.state === "confirm") {
      const left = st.remaining ?? Math.max(0, (st.deadline || 0) - Date.now() / 1000);
      status.replaceChildren(
        statusEl("ok", "Changes are live."),
        h("div", { class: "countdown" },
          h("div", { class: "big" }, `${Math.floor(left / 60)}:${String(Math.floor(left % 60)).padStart(2, "0")}`),
          h("div", {}, h("strong", {}, "Keep these changes?"),
            h("div", { class: "small" }, "If you don't confirm, the router rolls back on its own — so a change that cuts you off fixes itself."))));
      setFoot(
        h("button", { class: "btn btn-primary", onclick: () => api("confirm").then(() => { toast("Changes kept and saved to history"); done(); }) }, "Keep changes"),
        h("button", { class: "btn btn-link", onclick: () => api("rollback").then(() => { toast("Rolled back"); }) }, "Revert now"));
    } else if (st.state === "failed") {
      status.replaceChildren(h("div", { class: "alert alert-bad" }, h("strong", {}, "The rebuild failed. "),
        "Nothing on the router changed. Your edits are still pending — fix them or discard them."));
      setFoot(h("button", { class: "btn btn-secondary", onclick: () => api("apply-dismiss").then(done) }, "Close"));
      clearInterval(timer);
    } else if (st.state === "rolledback") {
      status.replaceChildren(h("div", { class: "alert alert-warn" }, h("strong", {}, "Rolled back. "), st.message || ""));
      setFoot(h("button", { class: "btn btn-secondary", onclick: () => api("apply-dismiss").then(done) }, "Close"));
      clearInterval(timer);
    } else if (st.state === "done") {
      done();
    }
  }).catch(() => status.replaceChildren(statusEl("warn", "Lost contact with the router — retrying…")));
  timer = setInterval(poll, 1000);
  poll();
}

/* ------------------------------------------------------------------ views */
let viewTimers = [];
let viewListeners = [];
function every(ms, fn) { viewTimers.push(setInterval(fn, ms)); }
function onSamples(fn) { document.addEventListener("helios-samples", fn); viewListeners.push(fn); }
function cleanupView() {
  viewTimers.forEach(clearInterval); viewTimers = [];
  viewListeners.forEach(fn => document.removeEventListener("helios-samples", fn)); viewListeners = [];
  hideTip();
}
const card = (title, right, ...body) => h("section", { class: "card" },
  h("div", { class: "card-head" }, h("h2", {}, title), right ? h("div", { class: "right" }, right) : null),
  h("div", { class: "card-body" }, body));

/* ----------------------------------------------------------- Overview */
function viewOverview(root) {
  const tiles = h("div", { class: "tiles" });
  const chartHost = h("div");
  const left = h("div", { class: "grid" }), right = h("div", { class: "grid" });
  const rebootBox = h("div");
  root.append(rebootBox, tiles, h("div", { class: "grid g-main" }, left, right));
  const drawReboot = () => api("reboot-status").then(r => rebootBox.replaceChildren(rebootBanner(r))).catch(() => {});
  drawReboot();
  const liveDown = h("span"), liveUp = h("span");
  let lastSt = null, upsInfo = null;      // upsInfo: {device, info} once `hardware` reports a UPS

  const tile = (label, value, foot, sw) => h("div", { class: "tile" },
    h("div", { class: "label" }, sw ? h("i", { class: "swatch", style: `background:${sw}` }) : null, label),
    h("div", { class: "value" }, value), h("div", { class: "foot" }, foot));

  const render = st => {
    lastSt = st;
    $("#head-title").textContent = `HeliOS on ${st.hostname}`; $("#head-sub").textContent = `Up ${fmtDur(st.uptime)}`; document.title = `HeliOS on ${st.hostname}`;
    const tb = $("#tab-new"); tb.hidden = !st.devices.new; tb.textContent = st.devices.new; syncTabMenu();
    const up = st.uplink.state === "up";
    const online = st.vlans.reduce((a, v) => a + v.clients, 0);
    tiles.replaceChildren(
      tile("Internet", statusEl(up ? "ok" : st.uplink.state === "down" ? "bad" : "warn", up ? "Online" : st.uplink.state === "down" ? "Offline" : "Unknown"),
        st.uplink.since ? `${up ? "for" : "since"} ${fmtDur(Date.now() / 1000 - st.uplink.since)}` : ""),
      tile("Devices", h("span", {}, String(online), h("small", {}, `on ${st.vlans.filter(v => v.clients).length} VLANs`)),
        st.devices.new ? h("span", {}, h("span", { class: "badge new" }, `${st.devices.new} new`), " waiting to be named") : `${st.devices.named} named`),
      tile("Download now", liveDown, `QoS cap ${fmtRate(parseRate(st.qos.down))}`, "var(--rx)"),
      tile("Upload now", liveUp, `QoS cap ${fmtRate(parseRate(st.qos.up))}`, "var(--tx)"),
      tile("Outages (24 h)", String(st.uplink.outages_24h),
        st.uplink.last_outage ? `last: ${fmtAgo(st.uplink.last_outage.up_at)}, ${fmtDur(st.uplink.last_outage.duration)}` : "none recorded"),
      upsInfo ? (() => { const s = upsState(upsInfo.info), i = upsInfo.info;
        return h("a", { class: "tile", href: "#hardware", style: "color:inherit;text-decoration:none" },
          h("div", { class: "label" }, "UPS"), h("div", { class: "value", style: "font-size:17px;line-height:2.2;white-space:nowrap" }, statusEl(s.kind, s.text)),
          h("div", { class: "foot" }, i.charge === null || i.charge === undefined ? upsInfo.device.label : `${i.charge}% · ${fmtMin(i.runtime)} left`)); })() : null);
    // replaceChildren() would print a missing UPS tile as the text "null"
    for (const n of [...tiles.childNodes]) if (n.nodeType === 3 && n.textContent === "null") n.remove();
    tiles.classList.toggle("n6", !!upsInfo);

    const wan = st.wan;
    left.replaceChildren(
      card("Internet throughput", legend([["Download", "var(--rx)"], ["Upload", "var(--tx)"]]), chartHost),
      h("div", { class: "grid g-2" },
        card("Internet", null, h("dl", { class: "kv" },
          h("dt", {}, "Status"), h("dd", {}, statusEl(wan.up ? "ok" : "bad", wan.up ? "Link up" : "Link down")),
          h("dt", {}, "IPv4"), h("dd", { class: "mono" }, wan.ipv4 || "—"),
          h("dt", {}, "IPv6"), h("dd", { class: "mono" }, wan.ipv6 || "—"),
          h("dt", {}, "Gateway"), h("dd", { class: "mono" }, wan.gateway || "—"),
          h("dt", {}, "Last outage"), h("dd", {}, st.uplink.last_outage
            ? `${fmtTime(st.uplink.last_outage.down_at)} · ${fmtDur(st.uplink.last_outage.duration)}` : "None recorded"))),
        card("Networks", h("a", { href: "#devices" }, "Devices →"),
          h("ul", { class: "list" }, st.vlans.map(v => h("li", {},
            h("span", { class: "lhs" }, vlanPill(v.name), h("span", { class: "muted mono" }, `VLAN ${v.id} · ${v.gateway}`)),
            h("span", { class: "num" }, `${v.clients} ${v.clients === 1 ? "device" : "devices"}`)))))),
    );
    const svc = st.services.filter(x => x.loaded || x.active !== "inactive");
    const bad = svc.filter(x => x.active === "failed").length;
    right.replaceChildren(
      card("Services", bad ? statusEl("bad", `${bad} failed`) : statusEl("ok", "All running"),
        h("ul", { class: "list" }, svc.map(x => {
          const kind = x.active === "active" ? "ok" : x.active === "failed" ? "bad" : "warn";
          return h("li", {},
            h("span", { class: "lhs" }, h("span", { class: `dot ${kind}` }), h("span", {}, x.label), h("span", { class: "muted small" }, x.sub || x.active)),
            h("button", { class: "btn btn-plain btn-sm", title: `Restart ${x.unit}`,
              onclick: () => systemctl("restart", x.unit).then(() => toast(`Restarted ${x.label}`), e => toast(e.message, true)) }, "Restart"));
        }))),
      card("Tailscale", null, h("dl", { class: "kv" },
        h("dt", {}, "Admin exit"), h("dd", {}, statusEl(st.tailscale.online ? "ok" : "warn", st.tailscale.online ? `${st.tailscale.name} online` : "offline")),
        h("dt", {}, "Address"), h("dd", { class: "mono" }, (st.tailscale.ips || [])[0] || "—"),
        h("dt", {}, "Peers"), h("dd", {}, `${st.tailscale.peers_online} of ${st.tailscale.peers} online`),
        h("dt", {}, "Family exit"), h("dd", {}, (() => { const c = st.services.find(x => x.unit === "container@heliosphere");
          return statusEl(c && c.active === "active" ? "ok" : "warn", c && c.active === "active" ? "heliosphere running" : "heliosphere stopped"); })()))),
      card("System", null,
        h("dl", { class: "kv" },
          h("dt", {}, "Uptime"), h("dd", {}, fmtDur(st.uptime)),
          h("dt", {}, "Load"), h("dd", { class: "num" }, st.load.map(x => x.toFixed(2)).join("  ")),
          h("dt", {}, "Memory"), h("dd", {}, `${fmtBytes(st.memory.used)} of ${fmtBytes(st.memory.total)}`)),
        h("div", { class: "meter", style: "margin-top:10px" }, h("i", { style: `width:${(st.memory.used / st.memory.total * 100).toFixed(0)}%` }))),
      card("QoS & UPnP", h("a", { href: "#qos" }, "Tune →"), h("dl", { class: "kv" },
        h("dt", {}, "Smart queue"), h("dd", {}, statusEl(st.qos.enabled ? "ok" : "warn", st.qos.enabled ? `CAKE · ${fmtRate(parseRate(st.qos.down))} ↓ / ${fmtRate(parseRate(st.qos.up))} ↑` : "Off")),
        h("dt", {}, "UPnP"), h("dd", {}, st.upnp.enabled ? `${st.upnp.mappings} open port${st.upnp.mappings === 1 ? "" : "s"}` : "Off"))),
    );
    drawChart();
  };
  const drawChart = () => {
    const d = samples.wan || [];
    lineChart(chartHost, d, { height: 240 });
    const last = d[d.length - 1];
    const set = (el, v) => { const [n, u] = rateParts(v); el.replaceChildren(n, h("small", {}, u)); };
    set(liveDown, last ? last.down : null); set(liveUp, last ? last.up : null);
  };
  const load = () => api("status").then(render).catch(e => root.replaceChildren(h("div", { class: "alert alert-bad" }, e.message)));
  root.append(h("div", { class: "loading" }, "Loading…"));
  load().then(() => root.querySelector(".loading")?.remove());
  // One extra call on load: show a UPS tile if a UPS is set up. Failures are ignored.
  api("hardware").then(hw => {
    const dev = (hw.devices || []).find(d => d.kind === "ups");
    if (!dev || !root.isConnected) return;
    upsInfo = { device: dev, info: (hw.ups || {})[dev.id] || {} };
    if (lastSt) render(lastSt);
  }).catch(() => {});
  every(10000, load);
  onSamples(drawChart);
}

/* ------------------------------------------------------------ Devices */
const devFilter = { vlan: "all", onlyNew: false, q: "" };
function viewDevices(root) {
  const body = h("tbody");
  const chipsBox = h("div", { class: "chips" });
  const search = h("input", { type: "search", placeholder: "Search name, IP, MAC…", value: devFilter.q,
    oninput: ev => { devFilter.q = ev.target.value.toLowerCase(); draw(); } });
  const newToggle = h("label", { class: "switch" }, h("input", { type: "checkbox", checked: devFilter.onlyNew,
    onchange: ev => { devFilter.onlyNew = ev.target.checked; draw(); } }), "New only");
  const countEl = h("span", { class: "muted small" });
  const restrictBox = h("div");
  root.append(restrictBox, h("section", { class: "card" },
    h("div", { class: "toolbar" }, chipsBox, h("span", { class: "spacer" }), newToggle, search),
    h("div", { class: "table-wrap" }, h("table", {},
      h("thead", {}, h("tr", {}, h("th", { class: "pause-col", "aria-label": "Pause" }), h("th", {}, "Device"), h("th", {}, "Network"), h("th", {}, "IP address"),
        h("th", {}, "MAC"), h("th", {}, "Filtering"), h("th", {}, "Seen"), h("th", { class: "actions" }, ""))),
      body)),
    h("div", { class: "card-foot" }, countEl,
      h("span", { class: "muted small" }, "Name a device to give it a fixed IP, a filtering profile and a ", h("span", { class: "mono" }, `name.${DOMAIN}`), " address."))));
  let devices = [];

  const draw = () => {
    const counts = { all: devices.length };
    for (const d of devices) { const v = d.lease_vlan || d.vlan; counts[v] = (counts[v] || 0) + 1; }
    chipsBox.replaceChildren(...["all", ...NETWORKS.map(n => n.name)].map(v => h("button", {
      class: "chip" + (devFilter.vlan === v ? " on" : ""), onclick: () => { devFilter.vlan = v; draw(); } },
    v === "all" ? null : h("i", { class: "swatch", style: `background:var(--v-${v});border-radius:50%` }),
    v === "all" ? "All" : netInfo(v).label, h("span", { class: "count" }, counts[v] || 0))));
    const rows = devices.filter(d => {
      const v = d.lease_vlan || d.vlan;
      if (devFilter.vlan !== "all" && v !== devFilter.vlan) return false;
      if (devFilter.onlyNew && d.known) return false;
      if (devFilter.q) {
        const hay = [d.name, d.hostname, d.current_ip, d.ip, d.mac, d.notes].join(" ").toLowerCase();
        if (!hay.includes(devFilter.q)) return false;
      }
      return true;
    });
    countEl.textContent = `${rows.length} of ${devices.length} devices · ${devices.filter(d => d.online).length} online`;
    body.replaceChildren(...rows.map(d => {
      const v = d.lease_vlan || d.vlan;
      const mismatch = d.known && d.lease_vlan && d.lease_vlan !== d.vlan;
      return h("tr", {},
        // Pause/resume: its own narrow column right before the name
        // Shows the current state (green ▶ running, red ❚❚ paused); a click switches it
        h("td", { class: "pause-col" }, d.known && netInfo(d.vlan).access.internet ? h("button", { class: "btn btn-icon " + (d.blocked ? "state-paused" : "state-running"),
          title: d.blocked ? `${d.name} is paused: click to give it its internet back` : `${d.name} has internet: click to pause it right now (no apply needed)`,
          "aria-label": d.blocked ? `${d.name} paused. Resume` : `${d.name} running. Pause`, "aria-pressed": d.blocked ? "true" : "false",
          onclick: ev => { ev.stopPropagation(); togglePause(d); } }) : null),
        h("td", {}, h("div", { style: "display:flex;gap:10px;align-items:center" },
          h("span", { class: "dot " + (d.blocked ? "bad" : d.online ? "ok" : "idle"), title: d.blocked ? "Paused" : d.online ? "Online" : "Offline" }),
          h("div", { class: "name-cell" },
            h("div", { class: "primary" }, d.known ? d.name : h("span", { class: "unnamed" }, d.hostname || "Unknown device"),
              !d.known ? h("span", { class: "badge new" }, "NEW") : null,
              d.blocked ? h("span", { class: "badge paused" }, "PAUSED") : null,
              d.restricted ? h("span", { class: "badge restricted", title: "Not named yet: filtered internet only, no other networks or admin pages. Name it and apply to lift this." }, "RESTRICTED") : null,
              d.media ? h("span", { class: "badge media", title: "May reach media servers" }, "Media") : null,
              d.mediaServer ? h("span", { class: "badge media", title: "Serves media: " + describePorts(d.mediaServer) }, "Media server") : null),
            h("div", { class: "secondary" }, d.known ? (d.notes || d.hostname || "") : "Not named yet")))),
        h("td", {}, vlanPill(v), mismatch ? h("div", { class: "small", style: "color:var(--warn)" }, `reserved on ${netInfo(d.vlan).label}`) : null),
        h("td", { class: "mono" }, d.current_ip || "—", d.known && d.ip ? h("div", { class: "muted small" }, d.ip === d.current_ip ? "fixed" : `fixed ${d.ip}`) : h("div", { class: "muted small" }, "DHCP")),
        h("td", { class: "mono muted" }, d.mac),
        h("td", {}, PROFILES[d.profile] ? PROFILES[d.profile].label : d.profile),
        h("td", { class: "muted small" }, d.online ? "Now" : d.lease_since ? fmtAgo(d.lease_since) : "—"),
        h("td", { class: "actions" },
          h("button", { class: "btn btn-secondary btn-sm", onclick: () => editDevice(d, devices, load) }, d.known ? "Edit" : "Name it")));
    }));
    if (!rows.length) body.replaceChildren(h("tr", {}, h("td", { colspan: 8 }, h("div", { class: "empty" }, "No devices match."))));
  };
  const togglePause = d => api("block", d.mac, d.blocked ? "off" : "on").then(r => {
    toast(r.blocked ? `${d.name} paused — internet cut off now` : `${d.name} resumed`);
    if (!r.live) toast(`Saved, but the live firewall update failed: ${r.note}`, true);
    load(); refreshPending();
  }).catch(e => toast(e.message, true));
  const drawRestrict = ru => {
    if (!ru) return restrictBox.replaceChildren();
    const nets = ru.networks.map(n => netInfo(n).label).join(", ");
    const unnamed = devices.filter(d => !d.known && ru.networks.includes(d.lease_vlan || d.vlan));
    const set = on => api("restrict-unnamed", on ? "on" : "off").then(() => { toast(`Restricting unnamed devices ${on ? "on" : "off"} — apply to activate`); refreshPending(); load(); }).catch(e => toast(e.message, true));
    if (!ru.enabled) return restrictBox.replaceChildren(h("div", { class: "alert alert-info small", style: "display:flex;gap:12px;align-items:center;flex-wrap:wrap" },
      h("span", { style: "flex:1;min-width:16em" }, h("strong", {}, "New devices get full access. "), `Anything that joins ${nets} can use it like a named device until you notice. HeliOS can limit unnamed devices to filtered internet until you name them.`),
      h("button", { class: "btn btn-secondary btn-sm", onclick: () => modal({ title: "Restrict unnamed devices?", body: [
          h("p", {}, `Devices you haven't named on ${nets} will only get filtered internet: no other networks, no router admin pages. Naming a device and applying lifts it.`),
          unnamed.length ? [h("p", { style: "margin-bottom:4px" }, h("strong", {}, `${unnamed.length} device${unnamed.length === 1 ? "" : "s"} would be restricted now:`)),
            h("ul", { class: "small", style: "margin-top:0" }, unnamed.map(d => h("li", {}, `${d.hostname || d.mac} on ${netInfo(d.lease_vlan || d.vlan).label}`)))] : h("p", { class: "muted" }, "No unnamed devices on those networks right now."),
          h("div", { class: "alert alert-warn small" }, h("strong", {}, "Name the device you're using first. "), "Otherwise this page stops working for you when you apply; it rolls back by itself after two minutes if you can't confirm. Tailscale and the rescue port are never restricted.")],
        foot: close => [h("button", { class: "btn btn-primary", onclick: () => { close(); set(true); } }, "Restrict unnamed devices"), h("button", { class: "btn btn-link", onclick: close }, "Cancel")] }) }, "Restrict unnamed devices…")));
    if (ru.waitingForAdmin) return restrictBox.replaceChildren(h("div", { class: "alert alert-warn small" }, h("strong", {}, "Unnamed-device restriction is waiting. "),
      "It starts once at least one device on an admin network (like Trusted) is named, so you can't lock yourself out."));
    restrictBox.replaceChildren(h("div", { class: "muted small", style: "margin:0 0 10px" }, `Unnamed devices on ${nets} only get filtered internet until you name them. `,
      h("button", { class: "btn btn-link btn-sm", style: "padding:0", onclick: () => set(false) }, "Turn off")));
  };
  const load = () => api("devices").then(r => { devices = r.devices; setProfiles(r.profiles); draw(); drawRestrict(r.restrictUnnamed); }).catch(e => toast(e.message, true));
  load();
  every(15000, load);
}

// Common media-server ports (defaults; change them if you remapped a port).
// Discovery ports are left out: their broadcasts never cross networks, so
// clients have to be pointed at the server by address anyway.
const MEDIA_PRESETS = [
  ["Video", [
    { label: "Jellyfin", tcp: [8096, 8920], udp: [] },
    { label: "Emby", tcp: [8096, 8920], udp: [] },
    { label: "Plex", tcp: [32400], udp: [] },
  ]],
  ["Music & books", [
    { label: "Navidrome", tcp: [4533], udp: [] },
    { label: "Lyrion (Squeezebox)", tcp: [9000, 3483], udp: [] },
    { label: "Audiobookshelf", tcp: [13378], udp: [] },
    { label: "MPD", tcp: [6600], udp: [] },
  ]],
  ["Live TV", [
    { label: "HDHomeRun", tcp: [80, 5004, 65001], udp: [65001] },
    { label: "Channels DVR", tcp: [8089], udp: [] },
    { label: "Tvheadend", tcp: [9981, 9982], udp: [] },
  ]],
];
const parsePorts = s => [...new Set(s.split(/[\s,]+/).filter(Boolean).map(Number))];
const describePorts = m => [m.tcp.length ? "TCP " + m.tcp.join(", ") : "", m.udp.length ? "UDP " + m.udp.join(", ") : ""]
  .filter(Boolean).join(" · ") + " — for media devices" + ((m.networks || []).length ? " + " + m.networks.map(n => netInfo(n).label).join(", ") : "");

function editDevice(d, all, reload) {
  const vlan0 = d.vlan || d.lease_vlan || (NETWORKS.find(n => !n.access.admin) || NETWORKS[0]).name;
  const netDefault = n => netInfo(n).defaultProfile || (PROFILES.default ? "default" : Object.keys(PROFILES)[0]);
  const model = { mac: d.mac, name: d.name || "", vlan: vlan0, profile: d.known ? d.profile : netDefault(vlan0),
    media: !!d.media, notes: d.notes || "", mediaServers: d.mediaServers ? [...d.mediaServers] : null,
    mediaServer: d.mediaServer ? { tcp: [...d.mediaServer.tcp], udp: [...d.mediaServer.udp], networks: [...(d.mediaServer.networks || [])] } : null };
  const serverDevs = all.filter(x => x.known && x.mediaServer && x.mac !== d.mac);
  const servers = serverDevs.map(x => x.name);
  const used = new Set([...all.filter(x => x.known && x.mac !== d.mac).map(x => x.name), ...RESERVED]);
  const nameIn = h("input", { type: "text", value: model.name, placeholder: "e.g. pluto", autocomplete: "off",
    oninput: ev => { model.name = ev.target.value.trim().toLowerCase(); } });
  const suggestBox = h("div", { class: "suggest" });
  const drawSuggest = () => {
    const kuiper = (ROLES[netInfo(model.vlan).role] || {}).names === "kuiper";
    const pool = (kuiper ? NAMES_KUIPER : NAMES_INNER).filter(n => !used.has(n));
    suggestBox.replaceChildren(h("span", { class: "muted small" }, kuiper ? "Kuiper belt:" : "Planets & moons:"),
      ...pool.slice(0, 7).map(n => h("button", { type: "button", onclick: () => { nameIn.value = n; model.name = n; } }, n)));
  };
  const vlanSel = h("select", { onchange: ev => { const was = model.vlan; model.vlan = ev.target.value;
      if (!d.known && model.profile === netDefault(was)) { model.profile = netDefault(model.vlan); drawProf(); } drawSuggest(); drawMedia(); } },
    NETWORKS.map(n => h("option", { value: n.name, selected: n.name === model.vlan }, `${n.label} (VLAN ${n.vlan})`)));
  const profBox = h("div", { class: "radio-cards" });
  const drawProf = () => profBox.replaceChildren(...Object.entries(PROFILES).map(([k, p]) => h("label", { class: "radio-card" + (model.profile === k ? " on" : "") },
    h("input", { type: "radio", name: "profile", value: k, checked: model.profile === k, onchange: () => { model.profile = k; drawProf(); } }),
    h("div", {}, h("strong", {}, p.label), h("span", {}, p.desc)))));
  // Two different roles: a media DEVICE plays (TV, stick, speaker); a media SERVER serves (Plex, Jellyfin…).
  // Admin networks already reach everything, so "media device" only means something elsewhere.
  const mediaOpt = (checked, label, sub, desc, onchange) => h("div", { class: "opt" },
    h("label", { class: "switch" }, h("input", { type: "checkbox", checked, onchange }), h("span", {}, h("strong", {}, label), h("span", { class: "muted small" }, " · " + sub))),
    h("div", { class: "opt-desc" }, desc));
  const mediaBox = h("div");
  const drawMedia = () => {
    if (netInfo(model.vlan).access.admin) return mediaBox.replaceChildren();
    const pick = model.media && serverDevs.length ? h("div", { class: "opt-more" },
      h("div", { class: "radio-row" },
        h("label", {}, h("input", { type: "radio", name: "ms-scope", checked: model.mediaServers === null, onchange: () => { model.mediaServers = null; drawMedia(); } }), " Every media server, including future ones"),
        h("label", {}, h("input", { type: "radio", name: "ms-scope", checked: model.mediaServers !== null, onchange: () => { model.mediaServers = [...servers]; drawMedia(); } }), " Only these:")),
      model.mediaServers === null ? null : h("div", { class: "net-grid", style: "margin-top:8px" }, ...serverDevs.map(sv => {
        const on = model.mediaServers.includes(sv.name);
        return h("label", { class: "net-chip" + (on ? " on" : ""), title: describePorts(sv.mediaServer) }, h("input", { type: "checkbox", checked: on, onchange: ev => {
          const set = new Set(model.mediaServers); ev.target.checked ? set.add(sv.name) : set.delete(sv.name); model.mediaServers = servers.filter(x => set.has(x)); drawMedia(); } }),
          h("strong", {}, sv.name), h("span", { class: "muted small mono" }, (sv.mediaServer.tcp || []).join(", ")));
      }))) : null;
    mediaBox.replaceChildren(
      mediaOpt(model.media, "Media device", "plays media",
        serverDevs.length ? "A TV, streaming stick, console or speaker. It can stream from your media servers, and phones on networks that allow casting can cast to it."
          : "A TV, streaming stick, console or speaker. Phones on networks that allow casting can cast to it. There's no media server yet: turn on “Media server” on the device that runs Plex, Jellyfin or similar.",
        ev => { model.media = ev.target.checked; drawMedia(); }),
      pick);
  };
  const serverBox = h("div");
  const portsIn = proto => h("input", { type: "text", class: "mono", value: model.mediaServer[proto].join(", "), placeholder: proto === "tcp" ? "e.g. 8096, 8920" : "usually empty",
    oninput: ev => { model.mediaServer[proto] = parsePorts(ev.target.value); } });
  const drawServer = () => serverBox.replaceChildren(
    mediaOpt(!!model.mediaServer, "Media server", "serves media",
      "This device runs Plex, Jellyfin, Emby or a music/TV server. Media devices can reach it on the ports below, and nothing else of theirs can reach this device.",
      ev => { model.mediaServer = ev.target.checked ? { tcp: [8096, 8920], udp: [], networks: [] } : null; drawServer(); }),
    !model.mediaServer ? "" : h("div", { style: "margin:0 0 18px 4px" },
      ...MEDIA_PRESETS.map(([group, list]) => h("div", { class: "suggest", style: "margin-bottom:6px" }, h("span", { class: "muted small", style: "min-width:92px" }, group + ":"),
        ...list.map(p => h("button", { type: "button", title: describePorts({ ...p, networks: [] }).split(" — ")[0], onclick: () => {
          model.mediaServer.tcp = [...p.tcp]; model.mediaServer.udp = [...p.udp]; drawServer(); } }, p.label)))),
      h("label", { class: "field", style: "margin-top:10px" }, h("span", {}, "TCP ports"), portsIn("tcp")),
      h("label", { class: "field" }, h("span", {}, "UDP ports"), portsIn("udp"),
        h("span", { class: "help" }, "Auto-discovery uses broadcasts that don't cross networks, so enter this server's address (",
          h("span", { class: "mono" }, `${model.name || "name"}.${DOMAIN}`), ") in the app by hand.")),
      h("div", { class: "field" }, h("span", { style: "display:block;font-weight:600;margin-bottom:6px" }, "Whole networks that may use it too"),
        h("div", { class: "suggest" }, ...NETWORKS.filter(n => n.access.reach !== "all").map(n => h("label", { class: "switch", style: "margin-right:14px" },
          h("input", { type: "checkbox", checked: model.mediaServer.networks.includes(n.name), onchange: ev => {
            const set = new Set(model.mediaServer.networks); ev.target.checked ? set.add(n.name) : set.delete(n.name); model.mediaServer.networks = [...set]; } }),
          vlanPill(n.name)))),
        h("span", { class: "help" }, "e.g. Family, so phones and laptops can stream. Networks that reach everything aren't listed."))));
  drawSuggest(); drawProf(); drawMedia(); drawServer();
  const err = h("div", { class: "alert alert-bad", hidden: true });
  const body = [
    h("div", { class: "muted", style: "margin-bottom:14px" }, h("span", { class: "mono" }, d.mac),
      d.hostname ? ` · calls itself “${d.hostname}”` : "", d.current_ip ? ` · now at ${d.current_ip}` : ""),
    err,
    h("label", { class: "field" }, h("span", {}, "Name"), nameIn, suggestBox,
      h("span", { class: "help" }, "Lowercase letters, digits and dashes. Reachable as ", h("span", { class: "mono" }, `name.${DOMAIN}`), ".")),
    h("label", { class: "field" }, h("span", {}, "Network"), vlanSel,
      h("span", { class: "help" }, "Where the device connects (switch port or Wi-Fi network). Its fixed IP is reserved on this VLAN.")),
    h("div", { class: "field" }, h("span", { style: "display:block;font-weight:600;margin-bottom:6px" }, "Filtering profile"), profBox),
    mediaBox,
    serverBox,
    h("label", { class: "field" }, h("span", {}, "Notes"), h("input", { type: "text", value: model.notes, placeholder: "Optional",
      oninput: ev => { model.notes = ev.target.value; } })),
  ];
  modal({
    title: d.known ? `Edit ${d.name}` : "Name this device", body,
    foot: close => [
      h("button", { class: "btn btn-primary", onclick: () => api("device-set", JSON.stringify(model)).then(r => {
        close(); toast(`${r.device.name} saved as ${r.device.ip} — apply to activate`); reload(); refreshPending();
      }).catch(e => { err.hidden = false; err.textContent = e.message; }) }, d.known ? "Save" : "Save device"),
      h("button", { class: "btn btn-link", onclick: close }, "Cancel"),
      h("span", { class: "spacer" }),
      d.known ? h("button", { class: "btn btn-link", style: "color:var(--bad)", onclick: () => api("device-remove", d.mac).then(() => {
        close(); toast(`${d.name} removed — apply to activate`); reload(); refreshPending(); }) }, "Forget device") : null,
    ],
  });
  setTimeout(() => nameIn.focus(), 50);
}

/* ------------------------------------------------------------ Traffic */
let trafficIface = "wan";
function viewTraffic(root) {
  const chips = h("div", { class: "chips" });
  const liveHost = h("div"), nowBox = h("div", { class: "legend", style: "gap:24px" });
  const vlanTable = h("tbody");
  const dayHost = h("div"), monthBody = h("tbody");
  const dayTitle = h("h2", {}, "Last 14 days");
  root.append(
    h("div", { class: "toolbar card", style: "margin-bottom:16px" }, h("strong", {}, "Interface"), chips),
    h("div", { class: "grid g-main" },
      card("Live", nowBox, liveHost),
      card("Right now, per network", null, h("table", {}, h("thead", {}, h("tr", {}, h("th", {}, "Network"), h("th", { style: "text-align:right" }, "↓ Download"), h("th", { style: "text-align:right" }, "↑ Upload"))), vlanTable))),
    h("div", { class: "grid g-main", style: "margin-top:16px" },
      h("section", { class: "card" }, h("div", { class: "card-head" }, dayTitle, legend([["Download", "var(--rx)"], ["Upload", "var(--tx)"]])), h("div", { class: "card-body" }, dayHost)),
      card("Monthly totals", null, h("table", {}, h("thead", {}, h("tr", {}, h("th", {}, "Month"), h("th", { style: "text-align:right" }, "Download"), h("th", { style: "text-align:right" }, "Upload"))), monthBody))));
  let vn = null;
  const drawChips = () => chips.replaceChildren(...Object.entries(ifaceLabels()).map(([k, label]) => h("button", {
    class: "chip" + (trafficIface === k ? " on" : ""), onclick: () => { trafficIface = k; drawChips(); drawLive(); drawVn(); } }, label)));
  const drawLive = () => {
    const d = samples[trafficIface] || [];
    lineChart(liveHost, d, { height: 260 });
    const last = d[d.length - 1];
    nowBox.replaceChildren(h("span", {}, h("i", { class: "ln", style: "background:var(--rx)" }), "Download ", h("strong", { class: "num", style: "color:var(--text)" }, last ? fmtRate(last.down) : "—")),
      h("span", {}, h("i", { class: "ln", style: "background:var(--tx)" }), "Upload ", h("strong", { class: "num", style: "color:var(--text)" }, last ? fmtRate(last.up) : "—")));
    vlanTable.replaceChildren(...Object.keys(ifaceLabels()).filter(k => k !== "wan").map(k => {
      const a = samples[k] || [], l = a[a.length - 1];
      return h("tr", {}, h("td", {}, NETWORKS.some(n => n.name === k) ? vlanPill(k) : ifaceLabels()[k]),
        h("td", { class: "num", style: "text-align:right" }, l ? fmtRate(l.down) : "—"),
        h("td", { class: "num", style: "text-align:right" }, l ? fmtRate(l.up) : "—"));
    }));
  };
  const drawVn = () => {
    const it = vn && vn.interfaces.find(i => i.name === trafficIface);
    dayTitle.textContent = `Last 14 days · ${ifaceLabels()[trafficIface]}`;
    if (!it) {
      dayHost.replaceChildren(h("div", { class: "empty" }, vn ? "No history for this interface yet." : "Loading…"));
      monthBody.replaceChildren();
      return;
    }
    const days = it.days.map(dd => {
      const dir = dirOf(trafficIface, dd.rx, dd.tx);
      const date = new Date(dd.date.year, dd.date.month - 1, dd.date.day);
      return { ...dir, label: date.toLocaleDateString([], { weekday: "short", month: "short", day: "numeric" }),
        short: date.toLocaleDateString([], { month: "numeric", day: "numeric" }) };
    });
    barChart(dayHost, days, { height: 240 });
    monthBody.replaceChildren(...it.months.slice().reverse().map(mm => {
      const dir = dirOf(trafficIface, mm.rx, mm.tx);
      return h("tr", {}, h("td", {}, new Date(mm.date.year, mm.date.month - 1, 1).toLocaleDateString([], { month: "long", year: "numeric" })),
        h("td", { class: "num", style: "text-align:right" }, fmtBytes(dir.down)), h("td", { class: "num", style: "text-align:right" }, fmtBytes(dir.up)));
    }));
  };
  drawChips(); drawLive(); drawVn();
  api("vnstat").then(r => { vn = r; drawVn(); }).catch(e => dayHost.replaceChildren(h("div", { class: "empty" }, e.message)));
  onSamples(drawLive);
}

/* ---------------------------------------------------------------- QoS */
function viewQos(root) {
  const form = h("div"), stats = h("div", { class: "grid g-2" });
  root.append(h("div", { class: "grid" }, stats, form));
  let settings = null, loaded = false;
  // Choices with a one-line explanation each (keys match helios-ctl / modules/voyager.nix)
  const LINKS = [["docsis", "Cable", "DOCSIS cable modem (Spectrum, Comcast, Virgin…)."], ["ethernet", "Ethernet / fibre", "Fibre ONT, Ethernet handoff, or unsure."],
    ["vdsl", "VDSL", "Phone-line VDSL/VDSL2 (PTM)."], ["adsl", "ADSL", "Older phone-line ADSL (ATM cells)."], ["custom", "Custom", "Set the overhead yourself."]];
  const FAIRS = [["device", "Per device", "Each home device gets an even share, then its own connections share that. Best for most homes."],
    ["triple", "Balanced", "CAKE's default: shares between devices and destinations together."], ["flow", "Per connection", "Every connection equal, whichever device it's from."]];
  const PRIOS = [["diffserv4", "Four classes", "Bulk, normal, video, voice — when apps mark their traffic."], ["diffserv3", "Three classes", "Bulk, normal, voice. CAKE's default."],
    ["besteffort", "No priorities", "Treat all traffic the same; fairness still applies."]];
  const cards = (list, m, key, name, redraw) => h("div", { class: "radio-cards auto" }, ...list.map(([k, l, d]) => h("label", { class: "radio-card" + (m[key] === k ? " on" : "") },
    h("input", { type: "radio", name, checked: m[key] === k, onchange: () => { m[key] = k; redraw(); } }), h("div", {}, h("strong", {}, l), h("span", {}, d)))));
  const opt = (m, key, label, desc) => h("div", { class: "opt" }, h("label", { class: "switch" }, h("input", { type: "checkbox", checked: !!m[key], onchange: ev => { m[key] = ev.target.checked; } }), h("strong", {}, label)),
    h("div", { class: "opt-desc" }, desc));
  const section = (title, ...kids) => h("div", { class: "card-section" }, h("h3", {}, title), ...kids);
  let m = null;
  const drawForm = () => {
    if (!m) m = { ...settings, down: settings.down ? parseRate(settings.down) / 1e6 : null, up: settings.up ? parseRate(settings.up) / 1e6 : null };
    const err = h("div", { class: "alert alert-bad", hidden: true }), sugBox = h("div");
    const rateIn = key => h("div", { class: "input-unit" }, h("input", { type: "number", min: 1, step: key === "up" ? 0.5 : 1, value: m[key] ?? "", placeholder: "—",
      oninput: ev => { m[key] = ev.target.value === "" ? null : Number(ev.target.value); } }), h("span", {}, "Mb/s"));
    const suggest = measure => { sugBox.replaceChildren(h("div", { class: "muted small", style: "margin-top:10px" }, measure ? "Measuring… QoS is paused for about 20 seconds." : "Checking…"));
      api("qos-suggest", ...(measure ? ["--measure"] : [])).then(r => {
        const rows = Object.entries(r.suggestions).map(([k, v]) => h("tr", {}, h("td", {}, h("strong", {}, k)), h("td", { class: "mono" }, String(v)), h("td", { class: "small muted" }, r.reasons[k] || "")));
        sugBox.replaceChildren(h("div", { class: "suggest-panel" },
          r.measured ? h("div", { style: "margin-bottom:8px" }, `Measured ${r.measured.down} Mb/s down, ${r.measured.up} Mb/s up`,
            r.measured.idleMs !== null ? ` · ${r.measured.idleMs} ms idle, ${r.measured.loadedMs ?? "?"} ms under load` : "",
            r.bufferbloatGrade ? h("span", { class: "badge", style: "margin-left:8px" }, `bufferbloat without QoS: ${r.bufferbloatGrade}`) : null)
            : h("div", { class: "muted small", style: "margin-bottom:8px" }, "Speeds need a measurement: use “Measure my connection”."),
          h("table", { class: "small" }, h("tbody", {}, ...rows)),
          h("div", { class: "card-actions" }, h("button", { class: "btn btn-secondary btn-sm", onclick: () => {
            for (const [k, v] of Object.entries(r.suggestions)) m[k] = (k === "down" || k === "up") ? parseRate(v) / 1e6 : v;
            drawForm(); toast("Suggestions filled in — review and Save"); } }, "Use these"))));
      }).catch(e => sugBox.replaceChildren(h("div", { class: "alert alert-bad small" }, e.message))); };
    const customBox = m.link !== "custom" ? null : h("div", { class: "inline-fields", style: "margin-top:10px" },
      h("label", { class: "field" }, h("span", {}, "Overhead (bytes)"), h("input", { type: "number", class: "mono", style: "width:7em", value: m.overhead, oninput: ev => { m.overhead = Number(ev.target.value); } })),
      h("label", { class: "field" }, h("span", {}, "Minimum packet (MPU)"), h("input", { type: "number", class: "mono", style: "width:7em", value: m.mpu, oninput: ev => { m.mpu = Number(ev.target.value); } })),
      h("label", { class: "switch", style: "align-self:center" }, h("input", { type: "checkbox", checked: m.atm, onchange: ev => { m.atm = ev.target.checked; } }), h("strong", {}, "ATM cells")));
    form.replaceChildren(card("Smart queue (QoS)", null,
      h("p", { class: "muted", style: "margin-top:0" }, "Keeps latency low when the line is busy: calls and games stay smooth while someone downloads. HeliOS shapes traffic to just under your line's speed so the queue lives here, short and fair, instead of in the modem."),
      err, opt(m, "enabled", "Enable QoS", "Needs both speeds below."),
      section("Speeds",
        h("div", { class: "inline-fields" }, h("label", { class: "field" }, h("span", {}, "Download"), rateIn("down")), h("label", { class: "field" }, h("span", {}, "Upload"), rateIn("up"))),
        h("div", { class: "card-actions", style: "margin-top:0" },
          h("button", { class: "btn btn-secondary btn-sm", onclick: () => suggest(false) }, "Suggest settings"),
          h("button", { class: "btn btn-secondary btn-sm", onclick: () => confirmDialog("Measure your connection?", "QoS is paused for about 20 seconds while HeliOS downloads and uploads test data (Cloudflare's speed test) and measures latency under load. Anything busy on the network will make the result lower.", "Measure").then(ok => ok && suggest(true)) }, "Measure my connection"),
          h("a", { href: "https://www.waveform.com/tools/bufferbloat", target: "_blank", rel: "noopener" }, "Bufferbloat test ↗")),
        sugBox),
      section("Link type", h("p", { class: "muted small", style: "margin-top:0" }, "How your modem frames packets, so shaping accounts for the extra bytes."), cards(LINKS, m, "link", "q-link", drawForm), customBox),
      section("Fairness", cards(FAIRS, m, "fairness", "q-fair", drawForm)),
      section("Priority", cards(PRIOS, m, "priority", "q-prio", drawForm)),
      section("Fine-tuning",
        opt(m, "ackFilter", "ACK filter (upload)", "Drops redundant acknowledgements when upload is much smaller than download (cable, DSL). Little effect on symmetric lines."),
        opt(m, "wash", "Clear incoming priority marks", "Ignore DSCP marks set by the internet after classifying them, so outside traffic can't claim priority inside."),
        h("div", { class: "inline-fields" },
          h("label", { class: "field" }, h("span", {}, "Typical distance"), hwSelect([["internet", "Internet-wide (100 ms)"], ["regional", "Regional (30 ms)"], ["metro", "Same city (10 ms)"]], m.rtt, v => { m.rtt = v; }),
            h("span", { class: "help" }, "Leave on Internet-wide unless nearly everything you use is close by.")),
          h("label", { class: "field" }, h("span", {}, "Queue manager"), hwSelect([["cake", "CAKE (recommended)"], ["fq_codel", "fq_codel + HTB (lighter)"]], m.algorithm, v => { m.algorithm = v; drawForm(); }),
            h("span", { class: "help" }, m.algorithm === "fq_codel" ? "fq_codel ignores Fairness and Priority: it shares per connection with one class." : "Uses everything above.")))),
      h("div", { class: "card-actions" },
        h("button", { class: "btn btn-primary", onclick: () => { err.hidden = true;
          const body = { ...m, down: m.down ? `${m.down}mbit` : null, up: m.up ? `${m.up}mbit` : null };
          api("qos-set", JSON.stringify(body)).then(() => { toast("QoS settings saved — apply to activate"); refreshPending(); })
            .catch(e => { err.hidden = false; err.textContent = e.message; err.scrollIntoView({ block: "nearest" }); }); } }, "Save"))));
  };
  const tinNames = ["Bulk", "Best effort", "Voice & video"];
  const panel = (title, q, color) => {
    if (!q) return card(title, null, h("div", { class: "empty" }, settings && settings.enabled ? "Not active — apply QoS or check the helios-sqm service." : "QoS is off."));
    const be = q.tins[1] || q.tins[0] || {};
    return card(title, h("span", { class: "pill" }, h("i", { class: "swatch", style: `background:${color};border-radius:50%` }), `${fmtRate(q.bandwidth * 8)} cap`),
      h("div", { class: "tiles", style: "grid-template-columns:repeat(3,1fr);margin:0 0 14px" },
        h("div", {}, h("div", { class: "muted small" }, "Avg queue delay"), h("div", { style: "font-size:22px" }, `${((be.avg_delay_us || 0) / 1000).toFixed(1)} `, h("small", { class: "muted" }, "ms"))),
        h("div", {}, h("div", { class: "muted small" }, "Peak delay"), h("div", { style: "font-size:22px" }, `${((be.peak_delay_us || 0) / 1000).toFixed(1)} `, h("small", { class: "muted" }, "ms"))),
        h("div", {}, h("div", { class: "muted small" }, "Dropped / marked"), h("div", { style: "font-size:22px" }, `${(q.drops || 0).toLocaleString()}`))),
      h("table", { class: "small" }, h("thead", {}, h("tr", {}, h("th", {}, "Class"), h("th", { style: "text-align:right" }, "Sent"), h("th", { style: "text-align:right" }, "Avg delay"), h("th", { style: "text-align:right" }, "Drops"), h("th", { style: "text-align:right" }, "ECN"))),
        h("tbody", {}, q.tins.map((t, i) => h("tr", {}, h("td", {}, tinNames[i] || `Tin ${i}`),
          h("td", { class: "num", style: "text-align:right" }, fmtBytes(t.sent_bytes)),
          h("td", { class: "num", style: "text-align:right" }, `${((t.avg_delay_us || 0) / 1000).toFixed(2)} ms`),
          h("td", { class: "num", style: "text-align:right" }, (t.drops || 0).toLocaleString()),
          h("td", { class: "num", style: "text-align:right" }, (t.ecn_mark || 0).toLocaleString()))))));
  };
  const load = () => api("qos").then(r => {
    settings = r.settings;
    if (!loaded) { drawForm(); loaded = true; }
    stats.replaceChildren(panel("Download", r.download, "var(--rx)"), panel("Upload", r.upload, "var(--tx)"));
  }).catch(e => toast(e.message, true));
  load();
  every(3000, load);
}

/* --------------------------------------------------------------- UPnP */
function viewUpnp(root) {
  const host = h("div", { class: "grid" });
  root.append(host);
  let devices = [];
  const act = (p, okMsg) => p.then(r => { toast(okMsg(r)); refreshPending(); load(); }).catch(e => toast(e.message, true));
  const block = what => confirmDialog(what.device ? `Block ${what.device}?` : `Block port ${what.port}?`,
    what.device ? `${what.device} can't open ports with UPnP or NAT-PMP any more. Ports it has open now close right away; the block itself takes effect when you apply.`
      : `No device can open public port ${what.port} with UPnP or NAT-PMP. If it's open now it closes right away; the block itself takes effect when you apply.`, "Block", true)
    .then(ok => ok && act(api("upnp-block", JSON.stringify(what)), r => `Blocked${r.closed ? ` · ${r.closed} port${r.closed === 1 ? "" : "s"} closed` : ""} — apply to keep it blocked`));
  const load = () => Promise.all([api("upnp"), api("devices").catch(() => ({ devices: [] })), loadNetworks().catch(() => {})]).then(([r, dv]) => {
    devices = dv.devices.filter(d => d.known && d.ip);
    const enabled = r.enabled, nets = (r.networks || []).map(n => netInfo(n).label);
    const rows = r.mappings.map(m => h("tr", {},
      h("td", {}, h("div", { class: "name-cell" }, h("div", { class: "primary" }, m.device || h("span", { class: "unnamed" }, "Unnamed device"),
        m.blocked ? h("span", { class: "badge paused", title: m.blocked + " — closes when you apply" }, "blocked") : null), h("div", { class: "secondary mono" }, m.ip))),
      h("td", {}, m.proto),
      h("td", { class: "num mono", style: "text-align:right" }, m.ext_port),
      h("td", { class: "num mono", style: "text-align:right" }, m.int_port),
      h("td", {}, m.desc || "—"),
      h("td", { class: "muted" }, m.expires ? fmtAgo(m.expires) : "Until reboot"),
      h("td", { class: "actions" },
        h("button", { class: "btn btn-secondary btn-sm", title: "Close it now. The app may ask for it again.",
          onclick: () => act(api("upnp-close", m.proto, String(m.ext_port)), () => `${m.proto} ${m.ext_port} closed`) }, "Close"),
        m.device ? h("button", { class: "btn btn-link btn-sm", style: "color:var(--bad)", title: `Stop ${m.device} opening ports`, onclick: () => block({ device: m.device }) }, "Block device") : null,
        h("button", { class: "btn btn-link btn-sm", style: "color:var(--bad)", title: `Stop any device opening public port ${m.ext_port}`, onclick: () => block({ port: String(m.ext_port) }) }, "Block port"))));
    const mapCard = h("section", { class: "card" },
      h("div", { class: "toolbar" },
        h("label", { class: "switch" }, h("input", { type: "checkbox", checked: enabled, onchange: ev => api("settings-set", "--upnp", ev.target.checked ? "on" : "off")
          .then(() => { toast("UPnP setting saved — apply to activate"); refreshPending(); }) }), h("strong", {}, "UPnP & NAT-PMP")),
        h("span", { class: "muted small" }, nets.length ? `Devices on ${nets.join(", ")} can open ports to themselves. Other networks never can.` : "No network allows UPnP (Networks → access)."),
        h("span", { class: "spacer" }),
        h("button", { class: "btn btn-secondary btn-sm", disabled: !r.mappings.length,
          onclick: () => confirmDialog("Close all UPnP ports?", "Every open port mapping is removed. Apps that still need one will ask again within a few minutes.", "Close all", true)
            .then(ok => ok && act(api("upnp-clear"), x => `${x.removed} port${x.removed === 1 ? "" : "s"} closed`)) }, "Close all")),
      h("div", { class: "table-wrap" }, h("table", {},
        h("thead", {}, h("tr", {}, h("th", {}, "Device"), h("th", {}, "Protocol"), h("th", { style: "text-align:right" }, "Public port"), h("th", { style: "text-align:right" }, "Device port"),
          h("th", {}, "Requested by"), h("th", {}, "Expires"), h("th", { class: "actions" }, ""))),
        h("tbody", {}, rows.length ? rows : h("tr", {}, h("td", { colspan: 7 }, h("div", { class: "empty" }, enabled ? "No open ports right now." : "UPnP is off.")))))));
    // Block list
    const sel = hwSelect([["", "Pick a device…"], ...devices.filter(d => !r.blockedDevices.includes(d.name)).map(d => [d.name, `${d.name} (${netInfo(d.vlan).label})`])], "", () => {});
    const portIn = h("input", { type: "text", class: "mono", placeholder: "e.g. 3389 or 6000-6100", style: "width:14em" });
    const chip = (label, what) => h("span", { class: "net-chip on" }, label,
      h("button", { class: "btn btn-link btn-sm", style: "padding:0 0 0 6px", title: "Unblock", onclick: () => act(api("upnp-unblock", JSON.stringify(what)), () => "Unblocked — apply to activate") }, "×"));
    const blockCard = card("Blocked", null,
      h("p", { class: "muted small", style: "margin-top:0" }, "For devices that open ports they shouldn't (cameras, smart plugs, misbehaving apps) and ports that should never face the internet. Blocks apply on top of the network's UPnP permission."),
      h("div", { class: "card-section", style: "margin-top:0;border-top:0;padding-top:0" }, h("h3", {}, "Devices"),
        r.blockedDevices.length ? h("div", { class: "net-grid" }, ...r.blockedDevices.map(d => chip(d, { device: d }))) : h("div", { class: "muted small" }, "None."),
        h("div", { class: "card-actions" }, sel, h("button", { class: "btn btn-secondary btn-sm", onclick: () => { const v = sel.querySelector("select") ? sel.querySelector("select").value : sel.value; if (v) block({ device: v }); } }, "Add to block list"))),
      h("div", { class: "card-section" }, h("h3", {}, "Public ports"),
        r.blockedPorts.length ? h("div", { class: "net-grid" }, ...r.blockedPorts.map(p => chip(p, { port: p }))) : h("div", { class: "muted small" }, "None. Ports below 1024 are never allowed anyway."),
        h("div", { class: "card-actions" }, portIn, h("button", { class: "btn btn-secondary btn-sm", onclick: () => { if (portIn.value.trim()) block({ port: portIn.value.trim() }); } }, "Add to block list"))));
    host.replaceChildren(mapCard, blockCard);
  }).catch(e => toast(e.message, true));
  load();
  every(10000, () => document.querySelector(".modal") || document.activeElement === document.querySelector("#view input") ? null : load());
}

/* ------------------------------------------------------------ History */
function viewHistory(root) {
  const host = h("div", { class: "grid g-main" });
  root.append(host);
  const load = () => api("history").then(r => {
    const days = [...Array(7)].map((_, i) => {
      const start = new Date(); start.setHours(0, 0, 0, 0); start.setDate(start.getDate() - (6 - i));
      const t0 = start / 1000, t1 = t0 + 86400;
      const hits = r.outages.filter(o => o.down_at >= t0 && o.down_at < t1);
      return { label: start.toLocaleDateString([], { weekday: "short" }), n: hits.length, dur: hits.reduce((a, o) => a + o.duration, 0) };
    });
    host.replaceChildren(
      h("div", { class: "grid" },
        card("Changes", h("span", { class: "muted small" }, "Every apply, pause and backup is a commit in /etc/nixos"),
          h("ul", { class: "list" }, r.commits.map((c, i) => h("li", {},
            h("div", { class: "name-cell" }, h("div", { class: "primary" }, c.subject),
              h("div", { class: "secondary" }, h("span", { class: "mono" }, c.short), ` · ${fmtTime(c.time)} · ${c.author}`)),
            i === 0 ? h("span", { class: "pill" }, "Current") : h("button", { class: "btn btn-secondary btn-sm",
              onclick: () => confirmDialog(`Restore “${c.subject}”?`, "Devices, QoS and UPnP settings go back to how they were at this point, as pending changes. Review them, then Apply.", "Restore")
                .then(ok => ok && api("restore", c.rev).then(() => { toast("Older settings loaded — review and apply"); refreshPending(); }).catch(e => toast(e.message, true))) }, "Restore")))))),
      h("div", { class: "grid" },
        card("Internet outages", null,
          h("div", { class: "strip", style: "margin-bottom:12px" }, days.map(d => h("div", { class: "day" + (d.n ? " hit" : ""), title: d.n ? `${d.n} outage(s), ${fmtDur(d.dur)} total` : "No outages" },
            d.label, h("strong", {}, d.n ? fmtDur(d.dur) : "—")))),
          r.outages.length ? h("ul", { class: "list" }, r.outages.slice(0, 8).map(o => h("li", {},
            h("span", { class: "lhs" }, icon("bad"), fmtTime(o.down_at)), h("span", { class: "num" }, fmtDur(o.duration)))))
            : h("div", { class: "empty" }, "No outages recorded.")),
        card("Backups", h("button", { class: "btn btn-secondary btn-sm", onclick: () => api("backup").then(b => { toast(b.changed ? "Backup saved" : "Nothing changed since the last backup"); load(); }).catch(e => toast(e.message, true)) }, "Back up now"),
          h("dl", { class: "kv" },
            h("dt", {}, "Last backup"), h("dd", {}, r.backup.time ? `${fmtTime(r.backup.time)} (${fmtAgo(r.backup.time)})` : "Never"),
            h("dt", {}, "Off-site copy"), h("dd", {}, r.backup.pushed === true ? statusEl("ok", "Pushed to git remote") : r.backup.pushed === false ? statusEl("bad", "Push failed") : statusEl("warn", "No git remote set")),
            h("dt", {}, "Includes"), h("dd", {}, "/etc/nixos and AdGuard's settings"))),
        card("System generations", h("span", { class: "muted small" }, "NixOS keeps every build"),
          h("ul", { class: "list" }, r.generations.slice(0, 6).map(g => h("li", {},
            h("span", { class: "lhs" }, h("strong", { class: "num" }, `#${g.gen}`), h("span", { class: "muted" }, g.date)),
            g.current ? h("span", { class: "pill" }, h("i", { class: "swatch", style: "background:var(--ok);border-radius:50%" }), "Running") : null))),
          h("p", { class: "muted small", style: "margin-bottom:0" }, "Older generations are also in the boot menu. ", h("span", { class: "mono" }, "nixos-rebuild switch --rollback"), " returns to the previous one.")),
      ));
  }).catch(e => toast(e.message, true));
  load();
}

/* ---------------------------------------------------------- Solar Wind */
function phoneLoginForm(currentUser, reload) {
  const user = h("input", { type: "text", value: currentUser || "", autocomplete: "off", autocapitalize: "none", spellcheck: false, class: "login-field" });
  const pw = h("input", { type: "password", autocomplete: "new-password", class: "login-field" });
  const pw2 = h("input", { type: "password", autocomplete: "new-password", class: "login-field" });
  const err = h("div", { class: "alert alert-bad", hidden: true });
  const showErr = m => { err.textContent = m; err.hidden = !m; };
  const save = h("button", { class: "btn btn-primary btn-sm", onclick: () => {
    showErr("");
    if (pw.value !== pw2.value) return showErr("The two passwords don't match");
    save.disabled = true;
    apiInput(pw.value, "notify-credentials", "--user", user.value.trim())
      .then(() => { pw.value = pw2.value = ""; toast("Phone login saved"); reload(); })
      .catch(e => showErr(e.message))
      .finally(() => { save.disabled = false; });
  } }, "Save");
  return h("div", {},
    h("p", { class: "muted small", style: "margin-top:0" }, "Choose the login your phone uses. The password is stored hashed for the server and kept here so you can look it up later."),
    h("label", { class: "field" }, h("span", {}, "Username"), user, h("span", { class: "help" }, "3–32 characters: lowercase letters, digits, - or _. Not “helios”.")),
    h("label", { class: "field" }, h("span", {}, "Password"), pw, h("span", { class: "help" }, "12–128 characters.")),
    h("label", { class: "field" }, h("span", {}, "Confirm password"), pw2),
    err, h("div", { style: "margin-top:10px" }, save));
}

function viewSolarWind(root) {
  const host = h("div", { class: "grid g-main" });
  root.append(host);
  let revealed = null;
  const kindPill = k => h("span", { class: "pill" }, h("i", { class: "swatch", style: `border-radius:50%;background:${k === "flare" ? "var(--bad)" : "var(--primary)"}` }), k);
  const load = () => Promise.all([api("notify-info"), api("notify-recent").catch(() => ({ messages: [] }))]).then(([info, recent]) => {
    if (!info.enabled) {
      host.replaceChildren(card("Notifications are off", null, h("p", {}, "Set ", h("span", { class: "mono" }, "helios.solarWind.enable = true"), " in site.nix to get push notifications on your phone.")));
      return;
    }
    const exposureText = { local: "Home network and Tailscale only", proxy: "Public, through your reverse proxy", funnel: "Public, through Tailscale Funnel" }[info.exposure] || info.exposure;
    const copy = text => h("button", { class: "btn btn-plain btn-sm", onclick: () => navigator.clipboard.writeText(text).then(() => toast("Copied")) }, "Copy");
    const pw = revealed || "••••••••••••";
    host.replaceChildren(
      h("div", { class: "grid" },
        card("Recent notifications", h("span", { class: "muted small" }, "Last 72 hours"),
          recent.messages.length ? h("ul", { class: "list" }, recent.messages.map(m => h("li", {},
            h("div", { class: "name-cell" },
              h("div", { class: "primary" }, kindPill(m.kind), m.title || "(no title)"),
              h("div", { class: "secondary", style: "white-space:pre-line" }, m.message.length > 220 ? m.message.slice(0, 220) + "…" : m.message)),
            h("span", { class: "muted small", style: "white-space:nowrap" }, fmtAgo(m.time)))))
            : h("div", { class: "empty" }, "Nothing sent yet. Use “Send test” to try it."))),
      h("div", { class: "grid" },
        card("Notifications", info.configured ? statusEl("ok", "Ready") : statusEl("warn", "Starting up"),
          h("dl", { class: "kv" },
            h("dt", {}, "Reachable"), h("dd", {}, exposureText),
            h("dt", {}, "Server"), h("dd", { class: "mono" }, info.url || "—"),
            h("dt", {}, "Topics"), h("dd", {}, kindPill("flare"), " ", h("span", { class: "muted small" }, "urgent"), "  ", kindPill("aurora"), " ", h("span", { class: "muted small" }, "info"))),
          h("div", { style: "display:flex;gap:8px;margin-top:14px;flex-wrap:wrap" },
            h("button", { class: "btn btn-secondary btn-sm", onclick: () => api("notify-test", "flare").then(() => { toast("Flare sent"); setTimeout(load, 800); }).catch(e => toast(e.message, true)) }, "Send test flare"),
            h("button", { class: "btn btn-secondary btn-sm", onclick: () => api("notify-test", "aurora").then(() => { toast("Aurora sent"); setTimeout(load, 800); }).catch(e => toast(e.message, true)) }, "Send test aurora"))),
        card("Phone login", null, phoneLoginForm(info.user, load)),
        card("Subscribe on your phone", null,
          h("ol", { style: "margin:0;padding-left:18px;line-height:1.9" },
            h("li", {}, "Install the ", h("strong", {}, "ntfy"), " app (Android or iOS)."),
            h("li", {}, "Settings → Manage users → add this server with the login below."),
            h("li", {}, "Subscribe to ", h("span", { class: "mono" }, info.topics.flare), " and ", h("span", { class: "mono" }, info.topics.aurora), " on that server.")),
          h("dl", { class: "kv", style: "margin-top:12px" },
            h("dt", {}, "Server"), h("dd", { class: "mono" }, info.url || "—", info.url ? copy(info.url) : null),
            h("dt", {}, "Username"), h("dd", { class: "mono" }, info.user || "—"),
            h("dt", {}, "Password"), h("dd", { class: "mono" }, pw,
              revealed ? copy(revealed) : h("button", { class: "btn btn-plain btn-sm", onclick: () => api("notify-info", "--reveal").then(r => { revealed = r.password; load(); }) }, "Show"))),
          info.exposure === "local" ? h("p", { class: "muted small", style: "margin-bottom:0" }, "With exposure = \"local\", phones receive notifications only on your network or over Tailscale. Use \"funnel\" (no domain needed) or \"proxy\" to reach them anywhere.") : null)));
  }).catch(e => toast(e.message, true));
  load();
  // Skip the refresh while a login is being typed, so it isn't wiped
  every(20000, () => [...host.querySelectorAll("input[type=password].login-field")].some(i => i.value) || document.activeElement?.classList.contains("login-field") ? null : load());
}

/* ----------------------------------------------------------------- router */
/* ------------------------------------------------------------- Networks */
// Short labels for the access flags, in display order
const ACCESS_UI = [
  ["admin", "Admin"], ["internet", "Internet"], ["filtered", "Filtered DNS"], ["mdns", "mDNS"], ["printing", "Printing"],
  ["upnp", "UPnP"], ["cast", "Cast to TVs"], ["forceNtp", "Forced NTP"], ["tailnet", "On tailnet"], ["quarantine", "Restrict unnamed"],
];
const reachText = n => n.access.reach === "all" ? "Every network"
  : n.access.reach.length ? n.access.reach.map(r => netInfo(r).label).join(", ") : "No other network";

function viewNetworks(root) {
  const load = () => Promise.all([loadNetworks(), api("profiles").then(r => setProfiles(r.profiles)).catch(() => {})]).then(draw)
    .catch(e => root.replaceChildren(h("div", { class: "alert alert-bad" }, e.message)));
  const draw = () => {
    const rows = NETWORKS.map(n => h("tr", {},
      h("td", {}, vlanPill(n.name), h("div", { class: "secondary mono small" }, n.name)),
      h("td", { class: "mono" }, String(n.vlan)),
      h("td", { class: "mono" }, `${n.prefix}.0/24`, h("div", { class: "secondary small" }, n.ipv6 ? `IPv6 ${n.ipv6}` : "IPv4 only")),
      h("td", {}, (ROLES[n.role] || {}).label || n.role, n.overrides.length ? h("div", { class: "secondary small", title: n.overrides.join(", ") }, "customised") : null),
      h("td", {}, h("div", { class: "chips" }, ...ACCESS_UI.filter(([k]) => n.access[k]).map(([k, l]) => h("span", { class: "badge", title: ACCESS_HELP[k] || "" }, l))),
        h("div", { class: "secondary small" }, "Reaches: " + reachText(n))),
      h("td", { class: "num" }, String(n.devices)),
      h("td", { class: "actions" }, h("button", { class: "btn btn-secondary btn-sm", onclick: () => editNetwork(n, load) }, "Edit"))));
    // Who may open connections to whom (internet and casting shown separately)
    const cell = (from, to) => {
      if (from.name === to.name) return h("td", { class: "muted", style: "text-align:center" }, "·");
      const ok = from.access.reach === "all" || from.access.reach.includes(to.name);
      return h("td", { style: "text-align:center", title: `${from.label} → ${to.label}` },
        ok ? h("span", { style: "color:var(--ok)" }, "✓") : from.access.cast ? h("span", { class: "muted small", title: "Only devices marked “Media device”" }, "TVs") : h("span", { class: "muted" }, "—"));
    };
    root.replaceChildren(h("div", { class: "grid" },
      card("Networks", h("button", { class: "btn btn-primary btn-sm", onclick: () => editNetwork(null, load) }, "Add network"),
        h("div", { class: "alert alert-info small" }, "Each network is a VLAN on the trunk. HeliOS sets up the router side; your switch ports and Wi-Fi SSIDs must carry the same VLAN ids."),
        h("table", {}, h("thead", {}, h("tr", {}, h("th", {}, "Network"), h("th", {}, "VLAN"), h("th", {}, "Subnet"), h("th", {}, "Role"),
          h("th", {}, "Access"), h("th", { class: "num" }, "Devices"), h("th", { class: "actions" }, ""))), h("tbody", {}, ...rows))),
      card("Who can reach whom", h("span", { class: "muted small" }, "rows open connections to columns"),
        h("table", { class: "small" }, h("thead", {}, h("tr", {}, h("th", {}, "From ↓  To →"), ...NETWORKS.map(n => h("th", { style: "text-align:center" }, vlanPill(n.name))),
          h("th", { style: "text-align:center" }, "Internet"))),
          h("tbody", {}, ...NETWORKS.map(f => h("tr", {}, h("td", {}, vlanPill(f.name)), ...NETWORKS.map(t => cell(f, t)),
            h("td", { style: "text-align:center" }, f.access.internet ? h("span", { style: "color:var(--ok)" }, "✓") : h("span", { class: "muted" }, "—")))))),
        h("div", { class: "muted small", style: "margin-top:8px" }, "Replies always flow back. Admin networks and Tailscale can also manage the router. Media servers add their own pinholes (Devices → edit → Media server)."))));
  };
  load();
}

function editNetwork(n, reload) {
  const isNew = !n;
  const freeVlan = () => { for (let v = 60; v < 4094; v += 10) if (v !== 60 && !NETWORKS.some(x => x.vlan === v)) return v; return 2; };
  const firstRole = Object.keys(ROLES).includes("guest") ? "guest" : Object.keys(ROLES)[0];
  const model = n ? { from: n.name, name: n.name, label: n.label, vlan: n.vlan, prefix: n.prefix, ipv6: n.ipv6, role: n.role, color: n.color,
      access: JSON.parse(JSON.stringify(n.access)), defaultProfile: n.defaultProfile ?? null }
    : (() => { const v = freeVlan(); return { name: "", label: "", vlan: v, prefix: `10.${v % 256}.0`, ipv6: null, role: firstRole,
      color: ROLES[firstRole].color, access: JSON.parse(JSON.stringify(ROLES[firstRole].access)), defaultProfile: null }; })();
  const err = h("div", { class: "alert alert-bad", hidden: true });
  const text = (key, attrs = {}) => h("input", { type: "text", value: model[key] ?? "", autocomplete: "off", ...attrs,
    oninput: ev => { model[key] = attrs.type === "number" ? Number(ev.target.value) : ev.target.value.trim(); } });
  const roleBox = h("div", { class: "radio-cards" }), accessBox = h("div");
  const drawRoles = () => roleBox.replaceChildren(...Object.entries(ROLES).map(([k, r]) => h("label", { class: "radio-card" + (model.role === k ? " on" : "") },
    h("input", { type: "radio", name: "role", checked: model.role === k, onchange: () => {
      model.role = k; model.access = JSON.parse(JSON.stringify(r.access)); if (isNew || !model.colorTouched) model.color = r.color;
      colorIn.value = model.color; drawRoles(); drawAccess(); } }),
    h("div", {}, h("strong", {}, r.label), h("span", {}, r.desc)))));
  const others = NETWORKS.filter(x => x.name !== (n && n.name));
  const drawAccess = () => {
    const base = ROLES[model.role].access;
    const changed = k => JSON.stringify(model.access[k]) !== JSON.stringify(base[k]);
    accessBox.replaceChildren(
      h("div", { style: "display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px 18px;margin-bottom:14px" },
        ...ACCESS_UI.map(([k, l]) => h("label", { class: "switch", title: ACCESS_HELP[k] || "" },
          h("input", { type: "checkbox", checked: !!model.access[k], onchange: ev => { model.access[k] = ev.target.checked; drawAccess(); } }),
          h("span", {}, h("strong", {}, l), changed(k) ? h("span", { class: "badge", style: "margin-left:6px" }, "changed") : null,
            h("div", { class: "muted small" }, ACCESS_HELP[k] || ""))))),
      h("div", { class: "field" }, h("span", { style: "display:block;font-weight:600;margin-bottom:6px" }, "Can open connections to",
          changed("reach") ? h("span", { class: "badge", style: "margin-left:6px" }, "changed") : null),
        h("label", { class: "switch", style: "margin-bottom:8px" }, h("input", { type: "checkbox", checked: model.access.reach === "all",
          onchange: ev => { model.access.reach = ev.target.checked ? "all" : []; drawAccess(); } }), h("strong", {}, "Every network")),
        model.access.reach === "all" ? "" : h("div", { class: "suggest" }, ...others.map(o => h("label", { class: "switch", style: "margin-right:14px" },
          h("input", { type: "checkbox", checked: model.access.reach.includes(o.name), onchange: ev => {
            const set = new Set(model.access.reach); ev.target.checked ? set.add(o.name) : set.delete(o.name); model.access.reach = [...set]; } }),
          vlanPill(o.name)))),
        h("span", { class: "help" }, "Replies always come back, so this only limits who can start a connection.")));
  };
  const colorIn = h("input", { type: "color", value: model.color, oninput: ev => { model.color = ev.target.value; model.colorTouched = true; } });
  const v6Box = h("div");
  const drawV6 = () => v6Box.replaceChildren(h("label", { class: "switch", style: "margin-bottom:6px" },
      h("input", { type: "checkbox", checked: !!model.ipv6, onchange: ev => { model.ipv6 = ev.target.checked ? "0x" + (model.vlan % 256).toString(16) : null; drawV6(); } }),
      h("strong", {}, "IPv6")),
    model.ipv6 ? h("label", { class: "field" }, h("span", {}, "IPv6 subnet id"), text("ipv6", { class: "mono", style: "width:8em" }),
      h("span", { class: "help" }, "Which /64 of the ISP's delegated /56 (0x0–0xff).")) : "");
  drawRoles(); drawAccess(); drawV6();
  const body = [
    err,
    h("div", { style: "display:grid;grid-template-columns:2fr 1fr auto;gap:0 16px" },
      h("label", { class: "field" }, h("span", {}, "Label"), text("label", { placeholder: "e.g. Guests" })),
      h("label", { class: "field" }, h("span", {}, "Interface name"), text("name", { class: "mono", placeholder: "e.g. guest" })),
      h("label", { class: "field" }, h("span", {}, "Colour"), colorIn)),
    h("div", { style: "display:grid;grid-template-columns:1fr 2fr;gap:0 16px" },
      h("label", { class: "field" }, h("span", {}, "VLAN id"), text("vlan", { type: "number", min: 2, max: 4094, class: "mono" })),
      h("label", { class: "field" }, h("span", {}, "Subnet"), h("div", { style: "display:flex;align-items:center;gap:6px" },
        text("prefix", { class: "mono", placeholder: "10.70.0", style: "width:10em" }), h("span", { class: "mono muted" }, ".0/24")),
        h("span", { class: "help" }, "Router is .1, fixed IPs .20–.99, DHCP .100–.250. Changing it moves this network's fixed IPs."))),
    v6Box,
    Object.keys(PROFILES).length ? h("label", { class: "field", style: "max-width:24em" }, h("span", {}, "Filtering for unnamed devices"),
      hwSelect([["", "AdGuard's global settings"], ...Object.values(PROFILES).map(pr => [pr.name, pr.label])], model.defaultProfile || "", v => { model.defaultProfile = v || null; }),
      h("span", { class: "help" }, "Named devices use their own profile. Edit profiles on the Filtering tab.")) : null,
    h("div", { class: "field" }, h("span", { style: "display:block;font-weight:600;margin-bottom:6px" }, "Role"), roleBox,
      h("span", { class: "help" }, "Picking a role resets the switches below to its defaults; anything you change afterwards is kept even if the role's defaults change later.")),
    accessBox,
  ];
  modal({
    title: isNew ? "Add network" : `Edit ${n.label}`, body, wide: true,
    foot: close => [
      h("button", { class: "btn btn-primary", onclick: () => {
        const payload = { ...model, label: model.label || model.name }; delete payload.colorTouched; if (isNew) delete payload.from;
        api("network-set", JSON.stringify(payload)).then(r => {
          close(); toast(`${r.network.label} saved — apply to activate`); reload(); refreshPending();
        }).catch(e => { err.hidden = false; err.textContent = e.message; err.scrollIntoView({ block: "nearest" }); });
      } }, isNew ? "Add network" : "Save"),
      h("button", { class: "btn btn-link", onclick: close }, "Cancel"),
      h("span", { class: "spacer" }),
      !isNew ? h("button", { class: "btn btn-link", style: "color:var(--bad)", disabled: n.devices > 0,
        title: n.devices ? "Move or forget its devices first" : "", onclick: () => confirmDialog(`Remove ${n.label}?`,
          `VLAN ${n.vlan} and ${n.prefix}.0/24 go away when you apply.`, "Remove network", true).then(ok => ok && api("network-remove", n.name).then(() => {
            close(); toast(`${n.label} removed — apply to activate`); reload(); refreshPending(); }).catch(e => { err.hidden = false; err.textContent = e.message; })) },
        n.devices ? `Remove (has ${n.devices} device${n.devices === 1 ? "" : "s"})` : "Remove network") : null,
    ],
  });
}

/* ------------------------------------------------------------- Hardware */
// Drivers phase 1 (docs/drivers.md): detection suggestions, router ports, manual
// switch/AP checklists, UPS (NUT), driver list, security scan. All data comes from
// `helios-ctl hardware`; edits go through hardware-set / hardware-remove / driver-set.
const HW_MODES = [["unused", "Unused"], ["access", "Access"], ["trunk", "Trunk"], ["wan", "WAN"]];
const HW_KIND = { switch: "Switch", ap: "Access point", router: "Router", ups: "UPS" };
const hwSlug = t => {
  let id = String(t || "").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 32).replace(/-+$/, "");
  if (id && !/^[a-z]/.test(id)) id = ("d-" + id).slice(0, 32).replace(/-+$/, "");
  return id;
};
const hwSelect = (opts, value, onchange, attrs = {}) => {
  const list = opts.slice();
  if (value && !list.some(([v]) => v === value)) list.push([value, `${value} (missing)`]);
  return h("select", { ...attrs, onchange: ev => onchange(ev.target.value) },
    list.map(([v, l]) => h("option", { value: v, selected: v === value }, l)));
};
const hwNetSelect = (value, onchange) => hwSelect(NETWORKS.map(n => [n.name, n.label]), value, onchange);
const hwSpeed = m => m >= 1000 ? `${m / 1000} Gb/s` : `${m} Mb/s`;
const hwErr = () => h("div", { class: "alert alert-bad", hidden: true });
const hwShowErr = (el, e) => { el.hidden = false; el.textContent = e.message || String(e); el.scrollIntoView({ block: "nearest" }); };
const hwSaved = (msg, reload) => { toast(`${msg} — apply to activate`); refreshPending(); return reload(); };
const deep = o => JSON.parse(JSON.stringify(o));

// Port mode → fields. `p` is a draft port: {mode, network, networks, wan}
function hwSetMode(p, mode) {
  p.mode = mode;
  if (mode === "access" && !p.network) p.network = (NETWORKS[0] || {}).name;
  if (mode === "trunk" && !p.networks) { p.networks = "all"; p.wan = false; }
}
function hwPortSettings(p, redraw, wanText) {
  if (p.mode === "access") return hwNetSelect(p.network, v => { p.network = v; });
  if (p.mode === "trunk") {
    const all = p.networks === "all";
    return h("div", { class: "port-trunk" },
      h("label", { class: "switch" }, h("input", { type: "checkbox", checked: all,
        onchange: ev => { p.networks = ev.target.checked ? "all" : []; redraw(); } }), "All networks"),
      all ? null : h("div", { class: "suggest" }, NETWORKS.map(n => h("label", { class: "switch" },
        h("input", { type: "checkbox", checked: p.networks.includes(n.name), onchange: ev => {
          const set = new Set(p.networks); ev.target.checked ? set.add(n.name) : set.delete(n.name); p.networks = [...set]; } }),
        vlanPill(n.name)))),
      h("label", { class: "switch" }, h("input", { type: "checkbox", checked: !!p.wan, onchange: ev => { p.wan = ev.target.checked; } }), "Carry WAN VLAN"));
  }
  if (p.mode === "wan") return h("span", { class: "muted small" }, wanText);
  return h("span", { class: "muted small" }, "Nothing connected through this port");
}
function hwCleanPort(p) {
  const o = p.match ? { match: p.match } : { port: p.port };
  if (p.label) o.label = p.label;
  o.mode = p.mode;
  if (p.mode === "access") o.network = p.network;
  if (p.mode === "trunk") { o.networks = p.networks; o.wan = !!p.wan; }
  if (p.note) o.note = p.note;
  return o;
}

const upsState = info => {
  const flags = String((info || {}).status || "").split(/\s+/).filter(Boolean);
  if (!flags.length) return { kind: "warn", text: "No reading" };
  if (flags.includes("LB")) return { kind: "bad", text: "Low battery" };
  if (flags.includes("OB")) return { kind: "warn", text: "On battery" };
  if (flags.includes("OL")) return { kind: "ok", text: flags.includes("CHRG") ? "On mains, charging" : "On mains" };
  return { kind: "warn", text: flags.join(" ") };
};
const fmtMin = s => s === null || s === undefined ? "—" : `${Math.round(s / 60)} min`;

/* Drivers and Security scan cards: shown on the Advanced tab */
function driversCard(hw, load) {
  const rows = hw.drivers.map(d => h("li", { class: "drv" },
    h("span", { class: "lhs", style: "align-items:flex-start" }, h("span", {},
      h("strong", {}, d.label || d.name), " ", d.builtin ? h("span", { class: "badge" }, "built in") : null, d.always ? h("span", { class: "badge", style: "margin-left:4px" }, "always on") : null,
      h("div", { class: "muted small" }, d.desc),
      h("div", { class: "chips", style: "margin-top:4px" }, ...(d.capabilities || []).map(c => h("span", { class: "badge media" }, c))))),
    h("label", { class: "switch", title: d.always ? "This driver is always on" : "" },
      h("input", { type: "checkbox", checked: d.always || d.enabled, disabled: !!d.always, onchange: ev => {
        const on = ev.target.checked;
        api("driver-set", JSON.stringify({ name: d.name, enabled: on, ...(on || d.name === "nut" ? (d.settings || {}) : {}) }))
          .then(() => hwSaved(`${d.label || d.name} ${on ? "enabled" : "disabled"}`, load)).catch(e => { toast(e.message, true); ev.target.checked = !on; });
      } }))));
  return card("Drivers", null, h("ul", { class: "list" }, rows),
    h("div", { class: "muted small", style: "margin-top:10px" }, "More drivers (switches, access points from specific vendors) will come from the HeliOS driver catalog, scanned and signed before you can install them."));
}

/* --- 6. Security scan */
function securityCard(hw) {
  const sc = hw.security;
  if (!sc) return card("Security scan", null, h("div", { class: "empty" }, "The weekly scan hasn't run yet."));
  const sev = [["critical", "bad"], ["high", "bad"], ["medium", "warn"], ["low", "ok"]];
  return card("Security scan", h("span", { class: "muted small" }, `Last run ${fmtAgo(sc.time)}`),
    h("div", { class: "chips", style: "margin-bottom:10px" }, h("span", { class: "badge" }, `${sc.total} finding${sc.total === 1 ? "" : "s"}`),
      ...sev.map(([k, kind]) => h("span", { class: "chip sev-" + kind }, h("span", { class: `dot ${sc[k] ? kind : "idle"}` }), ` ${sc[k] || 0} ${k}`))),
    (sc.top || []).length ? h("div", { class: "table-wrap" }, h("table", {}, h("thead", {}, h("tr", {}, h("th", {}, "Package"), h("th", {}, "Version"), h("th", {}, "CVE"), h("th", { class: "num" }, "Score"))),
      h("tbody", {}, ...sc.top.map(f => h("tr", {}, h("td", {}, h("strong", {}, f.name)), h("td", { class: "mono small" }, f.version || "—"),
        h("td", { class: "mono small" }, /^CVE-\d{4}-\d{4,}$/.test(f.cve || "") ? h("a", { href: `https://nvd.nist.gov/vuln/detail/${f.cve}`, target: "_blank", rel: "noopener noreferrer" }, f.cve) : (f.cve || "—")),
        h("td", { class: "num" }, f.score === null || f.score === undefined ? "—" : String(f.score)))))))
      : h("div", { class: "muted" }, "No findings to show."),
    h("div", { class: "muted small", style: "margin-top:8px" }, "vulnix checks the packages on the router against known vulnerabilities once a week. Updating HeliOS usually clears them."));
}


function viewHardware(root) {
  let routerDraft = null;                    // {device, fresh}: suggestion prefill, kept across redraws
  const load = () => Promise.all([loadNetworks(), api("hardware")]).then(([, hw]) => { draw(hw); })
    .catch(e => root.replaceChildren(h("div", { class: "alert alert-bad" }, e.message)));
  const draw = hw => {
    const cards = [suggestionsCard(hw), portsCard(hw), manualCard(hw), upsCard(hw)].filter(Boolean);
    root.replaceChildren(h("div", { class: "grid" }, ...cards));
  };

  /* --- 1. Found on your network */
  function suggestionsCard(hw) {
    const det = hw.detected;
    const scan = h("button", { class: "btn btn-secondary btn-sm", onclick: ev => {
      ev.target.disabled = true; ev.target.textContent = "Scanning…";
      api("detect").then(() => load()).then(() => toast("Scan finished")).catch(e => { toast(e.message, true); load(); });
    } }, "Scan again");
    const title = sg => sg.kind === "ups" ? "UPS" : sg.driver === "local-ports" ? "Router ports"
      : `${(sg.device || {}).label || "Device"} (${(HW_KIND[sg.device && sg.device.kind] || "device").toLowerCase()})`;
    const items = hw.suggestions.map(sg => h("li", {},
      h("span", { class: "lhs" }, h("span", { class: "dot warn" }), h("span", {}, h("strong", {}, title(sg)), h("div", { class: "muted small" }, sg.reason))),
      h("button", { class: "btn btn-primary btn-sm", onclick: () => setUp(sg, hw) }, "Set up")));
    return card("Found on your network", scan,
      h("div", { class: "muted small", style: "margin-bottom:8px" }, det && det.time ? `Last scan ${fmtAgo(det.time)} (${fmtTime(det.time)}).` : "The scan hasn't run yet — press “Scan again”."),
      items.length ? h("ul", { class: "list" }, items)
        : h("div", { class: "empty" }, det ? "Nothing new found. Everything detected is already set up." : "No scan results yet."));
  }
  function setUp(sg, hw) {
    const dev = sg.device;
    if (sg.kind === "ups" || (dev && dev.kind === "ups")) {
      confirmDialog("Set up this UPS?", `This turns on the UPS driver (Network UPS Tools) and adds “${dev.label}”. The router will watch the battery, send you alerts, and shut down cleanly when the battery runs low (by default at 20% charge or 3 minutes of runtime; you can change that afterwards).`, "Enable UPS driver")
        .then(ok => ok && api("hardware-set", JSON.stringify(dev)).then(() => hwSaved("UPS added", load)).catch(e => toast(e.message, true)));
    } else if (sg.driver === "local-ports" || (dev && dev.kind === "router")) {
      routerDraft = { device: dev, fresh: true };
      draw(hw);
      const c = $("#hw-ports"); if (c) { c.scrollIntoView({ behavior: "smooth", block: "start" }); c.classList.add("flash"); }
      toast("Choose what each port does, then press Save ports");
    } else editManual(dev, hw, load);
  }

  /* --- 2. This router's ports */
  function portsCard(hw) {
    const det = hw.detected;
    const existing = hw.devices.find(d => d.kind === "router");
    const device = routerDraft ? routerDraft.device : existing;
    const wanVlan = (hw.catalog && hw.catalog.wanVlan) || hw.wanVlan || 10;
    const err = hwErr();
    const matches = (e, p) => (e.match.mac && p.bus !== "dsa" && e.match.mac.toLowerCase() === (p.mac || "").toLowerCase()) || (e.match.name && e.match.name === p.name);
    const rows = [];                                   // draft rows for configurable ports
    const fixed = [];                                  // trunk / rescue ports (read-only)
    const entries = (device && device.ports) || [];
    const used = new Set();
    for (const p of (det && det.ports) || []) {
      if (p.role === "trunk" || p.role === "rescue") { fixed.push(p); continue; }
      const e = entries.find(x => matches(x, p));
      if (e) used.add(e);
      const r = e ? { ...deep(e), det: p } : { det: p, match: p.bus === "dsa" ? { name: p.name } : { mac: p.mac }, mode: "unused" };
      if (r.mode === "trunk" && !r.networks) r.networks = "all";
      rows.push(r);
    }
    for (const e of entries) if (!used.has(e)) rows.push({ ...deep(e), det: null });   // configured but not currently present
    const body = h("tbody");
    const drawRows = () => body.replaceChildren(
      ...fixed.map(p => h("tr", {}, ...portCells(p), h("td", {}, h("span", { class: "badge" }, p.role === "trunk" ? "Trunk to the switch" : "Rescue port")),
        h("td", { class: "muted small" }, p.role === "trunk" ? "Carries every network; can't be remapped." : "Always-on emergency access; can't be remapped."))),
      ...rows.map(r => h("tr", {}, ...portCells(r.det || { name: r.match.name || (r.label || "port"), mac: r.match.mac }, !r.det),
        h("td", {}, hwSelect(HW_MODES, r.mode, v => { hwSetMode(r, v); drawRows(); })),
        h("td", {}, hwPortSettings(r, drawRows, `This port is the internet connection (WAN VLAN ${wanVlan})`)))));
    function portCells(p, missing) {
      return [
        h("td", {}, h("strong", {}, p.name), missing ? h("div", { class: "secondary small" }, "not currently present") : null),
        h("td", { class: "mono small" }, p.mac || "shared (switch chip)"),
        h("td", { class: "small" }, p.bus ? `${p.bus.toUpperCase()}${p.driver ? " · " + p.driver : ""}` : "—"),
        h("td", { class: "small" }, p.carrier === undefined ? "—" : p.carrier ? statusEl("ok", p.speed ? hwSpeed(p.speed) : "Link up") : h("span", { class: "muted" }, "No link")),
      ];
    }
    drawRows();
    const save = () => {
      err.hidden = true;
      const ports = rows.filter(r => r.mode !== "unused").map(r => hwCleanPort({ ...r, det: undefined }));
      if (ports.filter(p => p.mode === "wan").length > 1) return hwShowErr(err, new Error("Only one port can be the WAN port."));
      const dev = { ...(existing || {}), id: "router", kind: "router", driver: "local-ports", label: (existing && existing.label) || "This router", ports };
      api("hardware-set", JSON.stringify(dev)).then(() => { routerDraft = null; return hwSaved("Router ports saved", load); }).catch(e => hwShowErr(err, e));
    };
    return h("div", { id: "hw-ports" }, card("This router's ports", routerDraft ? h("button", { class: "btn btn-link btn-sm", onclick: () => { routerDraft = null; draw(hw); } }, "Discard edits") : null,
      h("div", { class: "alert alert-warn small" }, h("strong", {}, "Careful: "), "changing a port can cut off whatever is plugged into it. Nothing changes on the router until you press Apply; after applying you have a few minutes to confirm, otherwise HeliOS rolls back automatically."),
      routerDraft && routerDraft.fresh ? h("div", { class: "alert alert-info small" }, "Found by the scan — pick a mode for the ports you want to use.") : null,
      err,
      !det ? h("div", { class: "empty" }, "No scan results yet — press “Scan again” above to find this router's ports.")
        : h("div", { class: "table-wrap" }, h("table", {}, h("thead", {}, h("tr", {},
          h("th", {}, "Port"), h("th", {}, "MAC"), h("th", {}, "Bus · driver"), h("th", {}, "Link"), h("th", {}, "Mode"), h("th", {}, "Settings"))), body)),
      det ? h("div", { class: "card-foot", style: "margin:12px -18px -16px" }, h("button", { class: "btn btn-primary", onclick: save }, "Save ports"),
        h("span", { class: "muted small" }, "Access: one network, untagged. Trunk: tagged networks. WAN: the internet connection (only one).")) : null));
  }

  /* --- 3. Switches and access points */
  function manualCard(hw) {
    const devs = hw.devices.filter(d => d.driver === "manual");
    const list = devs.map(d => {
      const lines = (hw.checklists || {})[d.id] || [];
      return h("div", { class: "hw-dev" },
        h("div", { class: "hw-dev-head" },
          h("div", {}, h("strong", {}, d.label), " ", h("span", { class: "badge" }, HW_KIND[d.kind] || d.kind), " ", h("span", { class: "muted mono small" }, d.id)),
          h("div", {}, h("button", { class: "btn btn-secondary btn-sm", onclick: () => editManual(d, hw, load) }, "Edit"), " ",
            h("button", { class: "btn btn-link btn-sm", style: "color:var(--bad)", onclick: () => confirmDialog(`Remove ${d.label}?`,
              "HeliOS forgets this device and its checklist. The device itself isn't touched.", "Remove", true).then(ok => ok &&
              api("hardware-remove", d.id).then(() => hwSaved(`${d.label} removed`, load)).catch(e => toast(e.message, true))) }, "Remove"))),
        d.notes ? h("div", { class: "muted small", style: "margin:2px 0 6px" }, d.notes) : null,
        lines.length ? h("div", {}, h("div", { class: "small", style: "font-weight:600;margin-top:6px" }, "Set this up on the device"),
          h("ul", { class: "checklist" }, lines.map(l => h("li", {}, l))))
          : h("div", { class: "muted small" }, "No ports or SSIDs described yet — press Edit to add them."));
    });
    return card("Switches and access points", h("div", { class: "right" },
      h("button", { class: "btn btn-secondary btn-sm", onclick: () => editManual({ kind: "switch" }, hw, load) }, "Add switch"),
      h("button", { class: "btn btn-secondary btn-sm", onclick: () => editManual({ kind: "ap" }, hw, load) }, "Add access point")),
      h("div", { class: "alert alert-info small" }, "HeliOS can't configure these devices itself (yet). Describe what each port and SSID should do and it writes the checklist you follow in the device's own web page."),
      list.length ? list : h("div", { class: "empty" }, "No switches or access points described yet."));
  }

  /* --- 4. UPS */
  function upsCard(hw) {
    const devs = hw.devices.filter(d => d.kind === "ups");
    const nut = hw.drivers.find(d => d.name === "nut") || {};
    if (!devs.length && !nut.enabled) return null;
    const cat = (hw.catalog && hw.catalog.nutDefaults) || {};
    const st = deep({ shutdown: { charge: 20, runtime: 180 }, server: { enabled: false, networks: [] }, ...cat, ...(nut.settings || {}) });
    const err = hwErr(), netBox = h("div");
    const num = (obj, key, attrs) => h("input", { type: "number", class: "mono", style: "width:6em", value: obj[key], ...attrs,
      oninput: ev => { obj[key] = ev.target.value === "" ? null : Number(ev.target.value); } });
    const drawNets = () => netBox.replaceChildren(st.server.enabled ? h("div", { class: "suggest" }, ...NETWORKS.map(n => h("label", { class: "switch", style: "margin-right:14px" },
      h("input", { type: "checkbox", checked: st.server.networks.includes(n.name), onchange: ev => {
        const set = new Set(st.server.networks); ev.target.checked ? set.add(n.name) : set.delete(n.name); st.server.networks = [...set]; } }), vlanPill(n.name)))) : "");
    drawNets();
    const save = () => {
      err.hidden = true;
      const { charge, runtime } = st.shutdown;
      if (!Number.isInteger(charge) || charge < 1 || charge > 100) return hwShowErr(err, new Error("Shutdown charge must be a whole number from 1 to 100."));
      if (!Number.isInteger(runtime) || runtime < 0) return hwShowErr(err, new Error("Shutdown runtime must be a whole number of seconds (0 or more)."));
      api("driver-set", JSON.stringify({ name: "nut", enabled: true, shutdown: st.shutdown, server: st.server }))
        .then(() => hwSaved("UPS settings saved", load)).catch(e => hwShowErr(err, e));
    };
    const one = d => {
      const u = (hw.ups || {})[d.id] || {}, s = upsState(u);
      return h("div", { class: "hw-dev" },
        h("div", { class: "hw-dev-head" }, h("div", {}, h("strong", {}, d.label), " ", h("span", { class: "muted mono small" }, (d.match || {}).usb || "")),
          h("button", { class: "btn btn-link btn-sm", style: "color:var(--bad)", onclick: () => confirmDialog(`Remove ${d.label}?`,
            "HeliOS stops monitoring this UPS. The UPS itself keeps working.", "Remove UPS", true).then(ok => ok &&
            api("hardware-remove", d.id).then(() => hwSaved("UPS removed", load)).catch(e => toast(e.message, true))) }, "Remove UPS")),
        h("dl", { class: "kv" },
          h("dt", {}, "Status"), h("dd", {}, statusEl(s.kind, s.text)),
          h("dt", {}, "Battery"), h("dd", {}, u.charge === null || u.charge === undefined ? "—" : h("div", { style: "display:flex;align-items:center;gap:10px" },
            h("div", { class: "meter", style: "width:140px" }, h("i", { style: `width:${Math.max(0, Math.min(100, u.charge))}%` })), `${u.charge}%`)),
          h("dt", {}, "Runtime left"), h("dd", {}, fmtMin(u.runtime)),
          h("dt", {}, "Load"), h("dd", {}, u.load === null || u.load === undefined ? "—" : `${u.load}%`),
          h("dt", {}, "Model"), h("dd", {}, u.model || "—")));
    };
    return card("UPS", null,
      devs.length ? devs.map(one) : h("div", { class: "empty" }, "No UPS set up. Plug a USB UPS in and press “Scan again”."),
      h("h3", { style: "margin:16px 0 8px" }, "Settings"), err,
      h("div", { class: "inline-fields" },
        h("label", { class: "field" }, h("span", {}, "Shut down at charge"), h("div", { class: "input-unit" }, num(st.shutdown, "charge", { min: 1, max: 100 }), h("span", {}, "%")),
          h("span", { class: "help" }, "When on battery and charge falls to this, the router shuts down cleanly.")),
        h("label", { class: "field" }, h("span", {}, "…or when runtime is below"), h("div", { class: "input-unit" }, num(st.shutdown, "runtime", { min: 0 }), h("span", {}, "seconds")),
          h("span", { class: "help" }, "Estimated runtime reported by the UPS.")),
      ),
      h("div", { class: "field" }, h("label", { class: "switch" }, h("input", { type: "checkbox", checked: st.server.enabled, onchange: ev => { st.server.enabled = ev.target.checked; drawNets(); } }),
          h("strong", {}, "Share with other machines")),
        h("span", { class: "help", style: "display:block;color:var(--text-2);font-size:12px;margin:4px 0" }, "Lets a NAS or server on the networks you choose follow this UPS and shut down with it (NUT server, port 3493)."), netBox),
      h("button", { class: "btn btn-primary", onclick: save }, "Save UPS settings"));
  }

  root.append(h("div", { class: "loading" }, "Loading…"));
  load();
}

// Editor for a manual switch / access point. `d` is an existing device, a suggestion's
// prefilled device, or just {kind} for a new one.
function editManual(d, hw, reload) {
  const isNew = !d.id || !hw.devices.some(x => x.id === d.id);
  const kind = d.kind;
  const model = { from: isNew ? null : d.id, id: d.id || "", label: d.label || "", notes: d.notes || "",
    ports: deep(d.ports || (kind === "ap" ? [{ port: "uplink", mode: "trunk", networks: "all", wan: false }] : [])),
    ssids: deep(d.ssids && d.ssids.length ? d.ssids : kind === "ap" ? [{ ssid: "", network: (NETWORKS[0] || {}).name }] : []) };
  let idTouched = !isNew || (!!d.id && !!d.label && hwSlug(d.label) !== d.id);
  const err = hwErr();
  const idIn = h("input", { type: "text", class: "mono", value: model.id, autocomplete: "off", placeholder: "e.g. main-switch",
    oninput: ev => { model.id = ev.target.value.trim(); idTouched = true; } });
  const labelIn = h("input", { type: "text", value: model.label, autocomplete: "off", placeholder: kind === "ap" ? "e.g. Living room AP" : "e.g. Main switch",
    oninput: ev => { model.label = ev.target.value; if (!idTouched) { model.id = hwSlug(model.label); idIn.value = model.id; } } });
  const portBody = h("tbody"), ssidBody = h("tbody");
  const drawPorts = () => portBody.replaceChildren(...model.ports.map((p, i) => h("tr", {},
    h("td", {}, h("input", { type: "text", value: p.port || "", style: "width:7em", placeholder: "1", autocomplete: "off", oninput: ev => { p.port = ev.target.value.trim(); } })),
    h("td", {}, hwSelect(HW_MODES, p.mode, v => { hwSetMode(p, v); drawPorts(); })),
    h("td", {}, hwPortSettings(p, drawPorts, "Untagged WAN VLAN — the modem / ISP handoff plugs in here")),
    h("td", {}, h("input", { type: "text", value: p.note || "", placeholder: "optional", autocomplete: "off", oninput: ev => { p.note = ev.target.value; } })),
    h("td", { class: "actions" }, h("button", { class: "btn btn-plain btn-sm", title: "Remove port", onclick: () => { model.ports.splice(i, 1); drawPorts(); } }, "✕")))));
  const drawSsids = () => ssidBody.replaceChildren(...model.ssids.map((s, i) => h("tr", {},
    h("td", {}, h("input", { type: "text", value: s.ssid, placeholder: "Wi-Fi name", autocomplete: "off", oninput: ev => { s.ssid = ev.target.value; } })),
    h("td", {}, hwNetSelect(s.network, v => { s.network = v; })),
    h("td", { class: "actions" }, h("button", { class: "btn btn-plain btn-sm", title: "Remove SSID", onclick: () => { model.ssids.splice(i, 1); drawSsids(); } }, "✕")))));
  drawPorts(); drawSsids();
  const nextPort = () => { const nums = model.ports.map(p => Number(p.port)).filter(Number.isFinite); return String((nums.length ? Math.max(...nums) : 0) + 1); };
  const body = [err,
    h("div", { style: "display:grid;grid-template-columns:2fr 1fr;gap:0 16px" },
      h("label", { class: "field" }, h("span", {}, "Label"), labelIn),
      h("label", { class: "field" }, h("span", {}, "ID"), idIn, h("span", { class: "help" }, "Lowercase letters, digits, dashes."))),
    h("label", { class: "field" }, h("span", {}, "Notes"), h("textarea", { rows: 2, style: "width:100%", placeholder: "Where it is, its management address…",
      oninput: ev => { model.notes = ev.target.value; } }, model.notes)),
    h("div", { class: "field" }, h("span", { style: "display:block;font-weight:600;margin-bottom:6px" }, kind === "ap" ? "Ethernet ports" : "Ports"),
      h("div", { class: "table-wrap" }, h("table", {}, h("thead", {}, h("tr", {}, h("th", {}, "Port"), h("th", {}, "Mode"), h("th", {}, "Networks"), h("th", {}, "Note"), h("th", {}, ""))), portBody)),
      h("button", { class: "btn btn-secondary btn-sm", style: "margin-top:8px", onclick: () => { model.ports.push({ port: nextPort(), mode: "access", network: (NETWORKS[0] || {}).name }); drawPorts(); } }, "Add port"),
      h("span", { class: "help", style: "display:block;color:var(--text-2);font-size:12px;margin-top:4px" }, "Describe how each port should be set up; the checklist is generated from this.")),
    kind === "ap" ? h("div", { class: "field" }, h("span", { style: "display:block;font-weight:600;margin-bottom:6px" }, "Wi-Fi networks (SSIDs)"),
      h("table", {}, h("thead", {}, h("tr", {}, h("th", {}, "SSID"), h("th", {}, "Network"), h("th", {}, ""))), ssidBody),
      h("button", { class: "btn btn-secondary btn-sm", style: "margin-top:8px", onclick: () => { model.ssids.push({ ssid: "", network: (NETWORKS[0] || {}).name }); drawSsids(); } }, "Add SSID")) : null,
  ];
  const title = `${isNew ? "Add" : "Edit"} ${kind === "ap" ? "access point" : "switch"}`;
  modal({ title: isNew ? title : `${title}: ${d.label}`, body, wide: true, foot: close => [
    h("button", { class: "btn btn-primary", onclick: () => {
      err.hidden = true;
      const payload = { ...(isNew ? {} : d), id: model.id || hwSlug(model.label), kind, driver: "manual", label: model.label.trim() || model.id, notes: model.notes,
        ports: model.ports.map(p => hwCleanPort(p)) };
      if (kind === "ap") payload.ssids = model.ssids.filter(s => s.ssid.trim()).map(s => ({ ssid: s.ssid.trim(), network: s.network }));
      if (model.from && model.from !== payload.id) payload.from = model.from;
      api("hardware-set", JSON.stringify(payload)).then(() => { close(); return hwSaved(`${payload.label} saved`, reload); }).catch(e => hwShowErr(err, e));
    } }, isNew ? `Add ${kind === "ap" ? "access point" : "switch"}` : "Save"),
    h("button", { class: "btn btn-link", onclick: close }, "Cancel"),
  ] });
}

/* ------------------------------------------------------- Remote access */
// docs/heliosphere.md: ways in (Tailscale, WireGuard, OpenVPN, proxy) and the way out.
// Everything comes from `helios-ctl remote`; edits go through remote-* commands.
function formDialog({ title, intro, fields, okLabel, submit }) {
  const vals = {}, err = hwErr();
  const inputs = fields.map(f => { vals[f.key] = ""; return h("label", { class: "field" }, h("span", {}, f.label),
    h("input", { type: f.type || "text", class: f.mono ? "mono" : null, autocomplete: "off", placeholder: f.placeholder || "", oninput: ev => { vals[f.key] = ev.target.value; } }),
    f.help ? h("span", { class: "help" }, f.help) : null); });
  modal({ title, body: [intro ? h("p", {}, intro) : null, err, ...inputs], foot: close => [
    h("button", { class: "btn btn-primary", onclick: () => { err.hidden = true;
      Promise.resolve().then(() => submit(vals)).then(close).catch(e => hwShowErr(err, e)); } }, okLabel),
    h("button", { class: "btn btn-link", onclick: close }, "Cancel")] });
}
// Parse untrusted SVG text and import only the <svg> element, minus scripts and event handlers
function safeSvg(text) {
  const doc = new DOMParser().parseFromString(String(text || ""), "image/svg+xml");
  const root = doc.documentElement;
  if (!root || root.localName !== "svg" || doc.querySelector("parsererror")) return null;
  const node = document.importNode(root, true);
  for (const el of [node, ...node.querySelectorAll("*")]) {
    if (["script", "foreignObject", "iframe", "object"].includes(el.localName)) { el.remove(); continue; }
    for (const a of [...el.attributes]) if (/^on/i.test(a.name) || /^\s*javascript:/i.test(a.value)) el.removeAttribute(a.name);
  }
  node.removeAttribute("width"); node.removeAttribute("height");
  return node;
}
// One-time display of a freshly generated client config (contains a private key)
function secretDialog({ title, filename, config, qrSvg }) {
  const url = URL.createObjectURL(new Blob([config], { type: "text/plain" }));
  const qr = qrSvg ? safeSvg(qrSvg) : null;
  modal({ title, wide: true, onClose: () => URL.revokeObjectURL(url), body: [
    h("div", { class: "alert alert-warn" }, h("strong", {}, "This is the only time the private key is shown. "),
      "HeliOS doesn't keep it. Save the file or scan the code now; if you lose it, remove this entry and add a new one."),
    h("div", { class: "secret-grid" },
      qr ? h("div", { class: "qr", role: "img", "aria-label": "QR code of the configuration" }, qr) : null,
      h("textarea", { class: "mono", readonly: true, rows: 14, style: "width:100%", onfocus: ev => ev.target.select() }, config))],
    foot: close => [h("a", { class: "btn btn-primary", href: url, download: filename }, `Download ${filename}`),
      h("button", { class: "btn btn-link", onclick: close }, "I've saved it")] });
}

const EGRESS_MODES = [
  ["direct", "Direct", "Out through the router's normal internet connection."],
  ["wireguard", "WireGuard VPN", "Through a VPN provider's WireGuard config (or your own server)."],
  ["openvpn", "OpenVPN", "Through a provider's .ovpn file."],
  ["proxy", "Upstream proxy", "Through a SOCKS5 or HTTP proxy you have access to."],
];

function viewRemote(root) {
  const load = () => Promise.all([loadNetworks(), api("remote")]).then(([, r]) => draw(r))
    .catch(e => root.replaceChildren(h("div", { class: "alert alert-bad" }, e.message)));
  const num = (obj, key, attrs = {}) => h("input", { type: "number", class: "mono", style: "width:7em", min: 1, max: 65535, value: obj[key], ...attrs,
    oninput: ev => { obj[key] = ev.target.value === "" ? null : Number(ev.target.value); } });
  const toggle = (obj, label) => h("label", { class: "switch" }, h("input", { type: "checkbox", checked: !!obj.enabled, onchange: ev => { obj.enabled = ev.target.checked; } }), h("strong", {}, label));
  const fmtHs = t => t ? fmtAgo(t) : "never";

  function draw(r) {
    if (!r.enabled) {
      root.replaceChildren(serviceOffCard("remote", "Remote access", "Use your home internet connection, with your DNS filtering, from anywhere: through Tailscale, WireGuard, OpenVPN or a proxy.", load));
      return;
    }
    const S = r.settings || {}, ing = S.ingress || {}, st = r.status || {};
    const ts = { enabled: true, ...ing.tailscale }, wg = { enabled: false, port: 51820, peers: [], ...ing.wireguard },
      ov = { enabled: false, port: 1194, proto: "udp", clients: [], ...ing.openvpn },
      px = { enabled: false, socksPort: 1080, httpPort: 3128, networks: [], users: [], ...ing.proxy };
    const save = (name, payload, label, err) => api("remote-set", JSON.stringify({ ingress: { [name]: payload } }))
      .then(() => hwSaved(label, load)).catch(e => hwShowErr(err, e));
    const remove = (title, text, okLabel, cmd, arg, msg) => confirmDialog(title, text, okLabel, true)
      .then(ok => ok && api(cmd, arg).then(() => hwSaved(msg, load)).catch(e => toast(e.message, true)));
    const saveBtn = fn => h("button", { class: "btn btn-primary btn-sm", onclick: fn }, "Save");
    const list = (items, empty) => items.length ? h("div", { class: "table-wrap" }, items) : h("div", { class: "muted small" }, empty);

    // Shared pieces: an option (switch + label, explanation underneath), a home-network
    // picker, and titled sections inside a card
    const opt = (obj, key, label, desc, onchange) => h("div", { class: "opt" },
      h("label", { class: "switch" }, h("input", { type: "checkbox", checked: !!obj[key], onchange: ev => { obj[key] = ev.target.checked; if (onchange) onchange(); } }), h("strong", {}, label)),
      desc ? h("div", { class: "opt-desc" }, desc) : null);
    const section = (title, ...kids) => h("div", { class: "card-section" }, h("h3", {}, title), ...kids);
    const netPicker = (obj, key, { only } = {}) => {
      const box = h("div"), warn = h("div");
      const draw = () => {
        const nets = NETWORKS.filter(n => !only || only(n));
        box.replaceChildren(h("div", { class: "net-grid" }, ...nets.map(n => {
          const on = obj[key].includes(n.name);
          return h("label", { class: "net-chip" + (on ? " on" : "") }, h("input", { type: "checkbox", checked: on, onchange: ev => {
            const set = new Set(obj[key]); ev.target.checked ? set.add(n.name) : set.delete(n.name); obj[key] = NETWORKS.map(x => x.name).filter(x => set.has(x)); draw(); } }),
            vlanPill(n.name));
        })));
        const admin = obj[key].filter(x => netInfo(x).access.admin);
        warn.replaceChildren(admin.length ? h("div", { class: "alert alert-warn small", style: "margin:8px 0 0" }, h("strong", {}, admin.map(x => netInfo(x).label).join(", ") + ": "),
          "an admin network. Remote devices would reach everything there, as if they were home.") : "");
      };
      draw();
      return h("div", {}, box, warn);
    };
    const copyBtn = text => h("button", { class: "btn btn-plain btn-sm", onclick: () => navigator.clipboard.writeText(text).then(() => toast("Copied")) }, "Copy");

    // Tailscale
    const tsE = hwErr(), tss = st.tailscale;
    ts.exitNode = ts.exitNode !== false; ts.hostname = ts.hostname || "heliosphere"; ts.lan = ts.lan || [];
    const tsCard = card("Tailscale", toggle(ts, "Enabled"),
      h("p", { class: "muted small", style: "margin-top:0" }, "For devices that run Tailscale. Nothing to forward on your router."), tsE,
      h("dl", { class: "kv" }, h("dt", {}, "Status"), h("dd", {}, tss && tss.online !== null ? statusEl(tss.online ? "ok" : "warn", tss.online ? "Online" : "Offline") : "—"),
        h("dt", {}, "Address"), h("dd", { class: "mono" }, (tss && tss.ip) || "—")),
      section("Options",
        opt(ts, "exitNode", "Exit node", `Devices can send all their internet traffic through home, with your DNS filtering. In the Tailscale app, pick “${ts.hostname}” as the exit node.`),
        h("label", { class: "field", style: "max-width:22em" }, h("span", {}, "Name on your tailnet"),
          h("input", { type: "text", class: "mono", value: ts.hostname, autocomplete: "off", oninput: ev => { ts.hostname = ev.target.value.trim().toLowerCase(); } }))),
      section("Home networks they can reach",
        h("p", { class: "muted small", style: "margin-top:0" }, "Shared as subnet routes. Approve them once in the Tailscale admin console (or with autoApprovers in your tailnet policy). None selected: internet only."),
        netPicker(ts, "lan")),
      h("div", { class: "card-actions" }, saveBtn(() => { tsE.hidden = true;
        save("tailscale", { enabled: ts.enabled, exitNode: ts.exitNode, hostname: ts.hostname, lan: ts.lan }, "Tailscale settings saved", tsE); })));

    // WireGuard
    const wgE = hwErr(), wgs = st.wireguard, wgPeers = (wgs && wgs.peers) || [];
    wg.lan = wg.lan || []; wg.tunnel = wg.tunnel || "full"; wg.keepalive = wg.keepalive ?? 25; wg.mtu = wg.mtu ?? null;
    const serverKey = (wgs && wgs.publicKey) || null;
    const tunnelBox = h("div", { class: "radio-cards" });
    const drawTunnel = () => tunnelBox.replaceChildren(...[["full", "Everything", "All internet traffic goes through home, filtered. Best on public Wi-Fi."],
      ["split", "Only home networks", "Just the home networks chosen below (and DNS); everything else uses the device's own connection."]].map(([k, l, d]) =>
      h("label", { class: "radio-card" + (wg.tunnel === k ? " on" : "") }, h("input", { type: "radio", name: "wg-tunnel", checked: wg.tunnel === k, onchange: () => { wg.tunnel = k; drawTunnel(); } }),
        h("div", {}, h("strong", {}, l), h("span", {}, d)))));
    drawTunnel();
    const wgCard = card("WireGuard", toggle(wg, "Enabled"),
      h("p", { class: "muted small", style: "margin-top:0" }, "For phones and laptops without Tailscale. HeliOS forwards the UDP port for you."), wgE,
      section("Connection",
        h("div", { class: "inline-fields" },
          h("label", { class: "field" }, h("span", {}, "UDP port"), num(wg, "port")),
          h("label", { class: "field" }, h("span", {}, "Keepalive (seconds)"), num(wg, "keepalive", { min: 0, max: 600 }), h("span", { class: "help" }, "0 = off. 25 keeps phones reachable behind NAT.")),
          h("label", { class: "field" }, h("span", {}, "MTU"), h("input", { type: "number", class: "mono", style: "width:7em", min: 1280, max: 1500, placeholder: "auto", value: wg.mtu ?? "",
            oninput: ev => { wg.mtu = ev.target.value === "" ? null : Number(ev.target.value); } }), h("span", { class: "help" }, "Leave empty unless pages stall (try 1380)."))),
        h("dl", { class: "kv" }, h("dt", {}, "Connect to"), h("dd", { class: "mono small" }, r.publicHost ? `${r.publicHost}:${wg.port}` : h("span", { class: "muted" }, "your current internet address (may change)"))),
        !r.publicHost ? h("div", { class: "alert alert-warn small", style: "margin:10px 0 0" }, "No public host name is set, so configs point at your current internet IP address, which may change. Set ",
          h("span", { class: "mono" }, "helios.heliosphere.publicHost"), " in site.nix.") : null),
      section("What goes through home", tunnelBox),
      section("Home networks they can reach", netPicker(wg, "lan")),
      h("p", { class: "muted small" }, "Tunnel, keepalive and MTU are written into each device's config: they apply to configs you create or renew after saving."),
      h("div", { class: "card-actions" }, saveBtn(() => { wgE.hidden = true;
        save("wireguard", { enabled: wg.enabled, port: wg.port, tunnel: wg.tunnel, keepalive: wg.keepalive, mtu: wg.mtu, lan: wg.lan }, "WireGuard settings saved", wgE); })),
      section("Server key",
        h("div", { class: "key-row" }, h("span", { class: "mono small key" }, serverKey || "Not created yet: enable WireGuard and apply."), serverKey ? copyBtn(serverKey) : null),
        h("div", { class: "card-actions" },
          h("button", { class: "btn btn-secondary btn-sm", disabled: !serverKey, onclick: rotateKey }, "Rotate key"),
          h("button", { class: "btn btn-link btn-sm", onclick: importKey }, "Import key…")),
        h("p", { class: "muted small", style: "margin-bottom:0" }, "Only the public key is shown; the private key never leaves the router.")),
      h("div", { class: "card-section" }, h("div", { class: "card-sub" }, h("h3", {}, "Peers"), h("button", { class: "btn btn-secondary btn-sm", onclick: () => addPeer() }, "Add peer")),
        list(wg.peers.length ? [h("table", {}, h("thead", {}, h("tr", {}, h("th", {}, "Name"), h("th", {}, "Address"), h("th", {}, "Last connected"), h("th", { class: "num" }, "Down / up"), h("th", { class: "actions" }, ""))),
          h("tbody", {}, ...wg.peers.map(p => { const live = wgPeers.find(x => x.name === p.name) || {};
            return h("tr", {}, h("td", {}, h("strong", {}, p.name)), h("td", { class: "mono small" }, p.ip), h("td", {}, fmtHs(live.lastHandshake)),
              h("td", { class: "num small" }, live.rx === undefined || live.rx === null ? "—" : `${fmtBytes(live.rx)} / ${fmtBytes(live.tx)}`),
              h("td", { class: "actions" },
                h("button", { class: "btn btn-link btn-sm", title: "New key pair and config, same address (lost or reset device)", onclick: () => renewPeer(p.name) }, "New config"),
                h("button", { class: "btn btn-link btn-sm", style: "color:var(--bad)", onclick: () => remove(`Remove ${p.name}?`,
                  "This device can no longer connect. It takes effect when you apply.", "Remove peer", "remote-wg-remove", p.name, `Peer ${p.name} removed`) }, "Remove"))); })))] : [], "No peers yet.")));
    const showPeer = (name, res) => secretDialog({ title: `WireGuard config for ${name}`, filename: `${name}.conf`, config: res.config, qrSvg: res.qrSvg });
    function addPeer() {
      formDialog({ title: "Add WireGuard peer", intro: "HeliOS creates a key pair and gives the device the next free address.", okLabel: "Create peer",
        fields: [{ key: "name", label: "Device name", placeholder: "e.g. pixel", mono: true, help: "Lowercase letters, digits, dashes." }],
        submit: v => api("remote-wg-add", v.name.trim()).then(res => { showPeer(v.name.trim(), res);
          toast(`Peer ${v.name.trim()} added — apply to activate`); refreshPending(); load(); }) });
    }
    function renewPeer(name) {
      confirmDialog(`New config for ${name}?`, "The device gets a new key pair at the same address. Its old config stops working once you apply.", "Create new config")
        .then(ok => ok && api("remote-wg-renew", name).then(res => { showPeer(name, res); toast(`New config for ${name} — apply to activate`); refreshPending(); load(); })
          .catch(e => toast(e.message, true)));
    }
    const keyDone = (res, what) => { modal({ title: what, body: [
        h("p", {}, "WireGuard restarted with the new key", res.restarted === false ? " (or will, the next time Remote access starts)" : "", "."),
        h("div", { class: "key-row" }, h("span", { class: "mono small key" }, res.publicKey), copyBtn(res.publicKey)),
        h("div", { class: "alert alert-warn small", style: "margin-top:12px" }, h("strong", {}, "Every peer needs the new server key. "),
          "Peers keep their own keys: on each device, replace the [Peer] PublicKey with the key above, or use “New config” for it.")],
      foot: close => [h("button", { class: "btn btn-primary", onclick: close }, "Done")] }); load(); };
    function rotateKey() {
      confirmDialog("Rotate the server key?", "WireGuard gets a new key pair right away, and every peer must update the server's public key before it can connect again. Use this if the router's key may have leaked.", "Rotate key", true)
        .then(ok => ok && api("remote-wg-rotate").then(res => keyDone(res, "Server key rotated")).catch(e => toast(e.message, true)));
    }
    function importKey() {
      formDialog({ title: "Import server key", intro: "Moving from another WireGuard server? Paste its private key (the PrivateKey line of its [Interface] section) and existing devices keep working unchanged.", okLabel: "Import key",
        fields: [{ key: "key", label: "Private key", type: "password", mono: true, placeholder: "44 characters, ends with =" }],
        submit: v => apiInput(v.key.trim(), "remote-wg-import").then(res => keyDone(res, "Server key imported")) });
    }

    // OpenVPN
    const ovE = hwErr(), ovs = st.openvpn, ovLive = (ovs && ovs.clients) || [];
    ov.lan = ov.lan || [];
    const ovCard = card("OpenVPN", toggle(ov, "Enabled"),
      h("p", { class: "muted small", style: "margin-top:0" }, "For devices or networks that only allow OpenVPN. TCP on a common port gets through strict firewalls."), ovE,
      section("Connection", h("div", { class: "inline-fields" }, h("label", { class: "field" }, h("span", {}, "Port"), num(ov, "port")),
        h("label", { class: "field" }, h("span", {}, "Protocol"), hwSelect([["udp", "UDP"], ["tcp", "TCP"]], ov.proto, v => { ov.proto = v; })))),
      section("Home networks they can reach", h("p", { class: "muted small", style: "margin-top:0" }, "All their internet traffic goes through home; these networks are reachable too. None selected: internet only."), netPicker(ov, "lan")),
      h("div", { class: "card-actions" }, saveBtn(() => { ovE.hidden = true; save("openvpn", { enabled: ov.enabled, port: ov.port, proto: ov.proto, lan: ov.lan }, "OpenVPN settings saved", ovE); })),
      h("div", { class: "card-section" }, h("div", { class: "card-sub" }, h("h3", {}, "Clients"), h("button", { class: "btn btn-secondary btn-sm", onclick: addClient }, "Add client")),
        list(ov.clients.length ? [h("table", {}, h("thead", {}, h("tr", {}, h("th", {}, "Name"), h("th", {}, "Status"), h("th", { class: "actions" }, ""))),
          h("tbody", {}, ...ov.clients.map(c => { const live = ovLive.find(x => x.name === c.name);
            return h("tr", {}, h("td", {}, h("strong", {}, c.name), h("div", { class: "secondary small mono" }, c.serial || "")),
              h("td", {}, c.revoked ? h("span", { class: "badge paused" }, "revoked") : live && live.connected !== null ? statusEl(live.connected ? "ok" : "warn", live.connected ? "Connected" : "Not connected") : "—"),
              h("td", { class: "actions" }, c.revoked ? null : h("button", { class: "btn btn-link btn-sm", style: "color:var(--bad)", onclick: () => remove(`Revoke ${c.name}?`,
                "Its certificate stops working for good. It takes effect when you apply.", "Revoke", "remote-ovpn-revoke", c.name, `Client ${c.name} revoked`) }, "Revoke"))); })))] : [], "No clients yet.")));
    function addClient() {
      formDialog({ title: "Add OpenVPN client", intro: "The first client also creates the certificate authority, which can take a moment.", okLabel: "Create client",
        fields: [{ key: "name", label: "Device name", placeholder: "e.g. laptop", mono: true, help: "Lowercase letters, digits, dashes." }],
        submit: v => api("remote-ovpn-add", v.name.trim()).then(res => { secretDialog({ title: `OpenVPN config for ${v.name.trim()}`, filename: `${v.name.trim()}.ovpn`, config: res.config });
          toast(`Client ${v.name.trim()} added — apply to activate`); refreshPending(); load(); }) });
    }

    // Proxy
    const pxE = hwErr(), pxs = st.proxy;
    const pxCard = card("Proxy (SOCKS5 and HTTP)", toggle(px, "Enabled"),
      h("p", { class: "muted small", style: "margin-top:0" }, "For apps and TVs at home that can't run a VPN, for example to send them through the way out below. It reaches the internet only, never your home networks."), pxE,
      section("Address and ports",
        h("div", { class: "inline-fields" }, h("label", { class: "field" }, h("span", {}, "SOCKS5 port"), num(px, "socksPort")), h("label", { class: "field" }, h("span", {}, "HTTP port"), num(px, "httpPort"))),
        h("p", { class: "muted small", style: "margin:0" }, "In the app, set the proxy to ", h("span", { class: "mono" }, `10.60.0.2:${px.socksPort}`), " (SOCKS5) or ",
          h("span", { class: "mono" }, `10.60.0.2:${px.httpPort}`), " (HTTP), with one of the users below.")),
      section("Who can connect",
        h("p", { class: "muted small", style: "margin-top:0" }, "Devices on these home networks can use the proxy. It's never offered on the internet: it has no encryption, so use WireGuard or Tailscale when you're away."),
        netPicker(px, "networks")),
      h("div", { class: "card-actions" }, pxs && pxs.running !== null ? statusEl(pxs.running ? "ok" : "warn", pxs.running ? "Running" : "Not running") : null,
        saveBtn(() => { pxE.hidden = true; save("proxy", { enabled: px.enabled, socksPort: px.socksPort, httpPort: px.httpPort, networks: px.networks }, "Proxy settings saved", pxE); })),
      h("div", { class: "card-section" }, h("div", { class: "card-sub" }, h("h3", {}, "Users"), h("button", { class: "btn btn-secondary btn-sm", onclick: addUser }, "Add user")),
        list(px.users.length ? [h("ul", { class: "list" }, px.users.map(u => h("li", {}, h("strong", {}, u),
          h("button", { class: "btn btn-link btn-sm", style: "color:var(--bad)", onclick: () => remove(`Remove ${u}?`, "This login stops working. It takes effect when you apply.", "Remove user", "remote-proxy-user-remove", u, `User ${u} removed`) }, "Remove"))))] : [],
          "No users yet. The proxy needs at least one before it can be enabled.")));
    function addUser() {
      formDialog({ title: "Add proxy user", intro: "Pick a long password. The proxy login isn't encrypted, but it only ever crosses your own networks.", okLabel: "Add user",
        fields: [{ key: "name", label: "User name", mono: true, help: "Lowercase letters, digits, dashes." }, { key: "pw", label: "Password", type: "password", help: "At least 16 characters." },
          { key: "pw2", label: "Repeat password", type: "password" }],
        submit: v => {
          if (v.pw.length < 16) throw new Error("The password must be at least 16 characters.");
          if (v.pw !== v.pw2) throw new Error("The passwords don't match.");
          return apiInput(v.pw, "remote-proxy-user", v.name.trim()).then(() => hwSaved(`User ${v.name.trim()} added`, load));
        } });
    }

    // Way out
    const eg = S.egress || {}, egs = st.egress;
    const E = { mode: eg.mode || "direct", killswitch: eg.killswitch !== false, proxy: { type: "socks5", host: "", port: 1080, ...(eg.proxy || {}) } };
    const egE = hwErr(), modeBox = h("div", { class: "radio-cards" }), cfgBox = h("div");
    const drawModes = () => modeBox.replaceChildren(...EGRESS_MODES.map(([k, l, d]) => h("label", { class: "radio-card" + (E.mode === k ? " on" : "") },
      h("input", { type: "radio", name: "egress-mode", checked: E.mode === k, onchange: () => { E.mode = k; drawModes(); drawCfg(); } }), h("div", {}, h("strong", {}, l), h("span", {}, d)))));
    const sec = { text: "", user: "", pass: "", px: { user: "", pass: "" } };
    const cfgStatus = kind => { const here = egs && egs.mode === E.mode && E.mode === (eg.mode || "direct");
      return h("div", { style: "margin:6px 0 10px" }, here ? statusEl(egs.configured ? "ok" : "warn", egs.configured ? "Configured" : "Not configured yet") : statusEl("warn", "Configured status shows once this mode is saved"),
        here && egs.configured ? h("span", { class: "muted small", style: "margin-left:10px" }, egs.up ? `Up${egs.publicIp ? ` · public address ${egs.publicIp}` : ""}` : "Down") : null); };
    const up = (kind, text, label) => { if (!text.trim()) return hwShowErr(egE, new Error("Paste a config or choose a file first.")); egE.hidden = true;
      apiInput(text, "remote-egress-upload", kind).then(() => { sec.text = ""; return hwSaved(label, load); }).catch(e => hwShowErr(egE, e)); };
    const clear = (kind, label) => confirmDialog(`Remove ${label}?`, "The stored file is deleted right away. The way out can't use it any more.", "Remove", true)
      .then(ok => ok && api("remote-egress-clear", kind).then(() => hwSaved(`${label} removed`, load)).catch(e => hwShowErr(egE, e)));
    const filePick = accept => h("input", { type: "file", accept, onchange: ev => { const f = ev.target.files[0]; if (f) f.text().then(t => { sec.text = t; ta.value = t; }); } });
    const ta = h("textarea", { rows: 8, style: "width:100%", class: "mono", spellcheck: "false", autocomplete: "off", oninput: ev => { sec.text = ev.target.value; } });
    const field = (label, key, obj, attrs) => h("label", { class: "field" }, h("span", {}, label), h("input", { type: "text", autocomplete: "off", value: obj[key] ?? "", ...attrs,
      oninput: ev => { obj[key] = attrs && attrs.type === "number" ? Number(ev.target.value) : ev.target.value.trim(); } }));
    const drawCfg = () => {
      if (E.mode === "wireguard" || E.mode === "openvpn") {
        const wgm = E.mode === "wireguard", kind = wgm ? "wireguard" : "openvpn", name = wgm ? "WireGuard config" : "OpenVPN config";
        ta.value = sec.text = "";
        cfgBox.replaceChildren(cfgStatus(kind),
          h("div", { class: "field" }, h("span", { style: "display:block;font-weight:600;margin-bottom:6px" }, wgm ? "wg-quick config (.conf)" : "OpenVPN config (.ovpn)"), ta,
            h("div", { style: "display:flex;gap:10px;align-items:center;margin-top:6px;flex-wrap:wrap" }, filePick(wgm ? ".conf,.txt" : ".ovpn,.conf,.txt"),
              h("button", { class: "btn btn-secondary btn-sm", onclick: () => up(kind, sec.text, `${name} uploaded`) }, "Upload config")),
            h("span", { class: "help", style: "display:block;color:var(--text-2);font-size:12px;margin-top:4px" }, "It's stored on the router, never shown again, and cleaned of DNS and script lines. It holds your provider's key.")),
          !wgm ? h("div", { class: "field" }, h("span", { style: "display:block;font-weight:600;margin-bottom:6px" }, "Provider login (optional)"),
            h("div", { class: "inline-fields" }, field("User name", "user", sec), h("label", { class: "field" }, h("span", {}, "Password"), h("input", { type: "password", autocomplete: "off", oninput: ev => { sec.pass = ev.target.value; } }))),
            h("div", { style: "display:flex;gap:10px" }, h("button", { class: "btn btn-secondary btn-sm", onclick: () => { if (!sec.user || !sec.pass) return hwShowErr(egE, new Error("Enter both a user name and a password."));
                up("auth", `${sec.user}\n${sec.pass}\n`, "Provider login saved"); } }, "Save login"),
              h("button", { class: "btn btn-link btn-sm", onclick: () => clear("auth", "provider login") }, "Remove login"))) : null,
          h("button", { class: "btn btn-link", style: "color:var(--bad);padding-left:0", onclick: () => clear(kind, name) }, "Remove config"));
      } else if (E.mode === "proxy") {
        cfgBox.replaceChildren(cfgStatus("proxy"),
          h("div", { class: "inline-fields" }, h("label", { class: "field" }, h("span", {}, "Type"), hwSelect([["socks5", "SOCKS5"], ["http", "HTTP"]], E.proxy.type, v => { E.proxy.type = v; })),
            field("Host", "host", E.proxy, { placeholder: "proxy.example.net" }), field("Port", "port", E.proxy, { type: "number", min: 1, max: 65535, style: "width:7em" })),
          h("div", { class: "field" }, h("span", { style: "display:block;font-weight:600;margin-bottom:6px" }, "Login (optional)"),
            h("div", { class: "inline-fields" }, field("User name", "user", sec.px), h("label", { class: "field" }, h("span", {}, "Password"), h("input", { type: "password", autocomplete: "off", oninput: ev => { sec.px.pass = ev.target.value; } }))),
            h("div", { style: "display:flex;gap:10px" }, h("button", { class: "btn btn-secondary btn-sm", onclick: () => { if (!sec.px.user) return hwShowErr(egE, new Error("Enter a user name."));
                up("auth", JSON.stringify({ user: sec.px.user, password: sec.px.pass }), "Proxy login saved"); } }, "Save login"),
              h("button", { class: "btn btn-link btn-sm", onclick: () => clear("auth", "proxy login") }, "Remove login")),
            h("span", { class: "help", style: "display:block;color:var(--text-2);font-size:12px;margin-top:4px" }, "Host, port and type are saved with “Save way out”. The login is stored separately and never shown again.")));
      } else cfgBox.replaceChildren(h("div", { class: "muted small" }, "Nothing to configure."));
    };
    drawModes(); drawCfg();
    const egSave = () => { egE.hidden = true;
      const egress = { mode: E.mode, killswitch: E.killswitch }; if (E.mode === "proxy") egress.proxy = { type: E.proxy.type, host: E.proxy.host, port: E.proxy.port };
      api("remote-set", JSON.stringify({ egress })).then(() => hwSaved("Way out saved", load)).catch(e => hwShowErr(egE, e)); };
    const outCard = card("Way out", egs ? h("span", { class: "small" }, statusEl(egs.up === false ? "warn" : "ok", `${egs.mode || "direct"}${egs.up === false ? " · down" : ""}${egs.publicIp ? ` · ${egs.publicIp}` : ""}`)) : null,
      h("p", { class: "muted small", style: "margin-top:0" }, "Where traffic from remote devices leaves for the internet. One at a time."), egE, modeBox,
      h("div", { style: "margin-top:4px" }, opt(E, "killswitch", "Kill switch", "If the way out goes down, remote traffic is dropped instead of leaking out through your normal connection. Recommended whenever you use a VPN or proxy.")),
      cfgBox, h("div", { style: "margin-top:14px" }, h("button", { class: "btn btn-primary", onclick: egSave }, "Save way out")));

    root.replaceChildren(h("div", { class: "grid" },
      h("div", { class: "alert alert-info small", style: "margin:0" }, h("strong", {}, "Remote devices can reach the internet, not your home network. "),
        "Their DNS goes through your filter, and all of them share one filtering profile: set it in AdGuard on the client ", h("span", { class: "mono" }, "10.60.0.2"), "."),
      tsCard, wgCard, ovCard, pxCard, outCard));
  }
  root.append(h("div", { class: "loading" }, "Loading…"));
  load();
}

/* ------------------------------------------------------------ Filtering */
// Magnetosphere (AdGuard Home) as people see it: profiles, network defaults, AdGuard itself
const PROFILE_UI = [["filtering", "Ads and trackers"], ["safeBrowsing", "Malware and phishing"], ["parental", "Adult content"],
  ["safeSearch", "Safe Search"], ["youtubeRestricted", "YouTube Restricted"]];
const adguardUrl = () => { const a = NETWORKS.find(n => n.access && n.access.admin); return a ? `http://${a.prefix}.1:3000` : null; };
const netPayload = (n, extra) => ({ from: n.name, name: n.name, label: n.label, vlan: n.vlan, prefix: n.prefix, ipv6: n.ipv6, role: n.role,
  ...(n.color && ROLES[n.role] && n.color !== ROLES[n.role].color ? { color: n.color } : {}), access: n.access, defaultProfile: n.defaultProfile ?? null, ...extra });
const profileSummary = p => p.global ? [h("span", { class: "badge" }, "AdGuard's global settings")]
  : [...PROFILE_UI.filter(([k]) => p[k]).map(([, l]) => h("span", { class: "badge" }, l)),
     ...(p.blockedServices || []).length ? [h("span", { class: "badge paused" }, `${p.blockedServices.length} blocked service${p.blockedServices.length === 1 ? "" : "s"}`)] : [],
     ...(!PROFILE_UI.some(([k]) => p[k]) && !(p.blockedServices || []).length ? [h("span", { class: "muted small" }, "nothing blocked")] : [])];

function viewFiltering(root) {
  let data = null;
  const load = () => Promise.all([loadNetworks(), api("profiles")]).then(([, r]) => { data = r; setProfiles(r.profiles); draw(); })
    .catch(e => root.replaceChildren(h("div", { class: "alert alert-bad" }, e.message)));
  const draw = () => {
    const ag = data.adguard || {}, url = adguardUrl();
    const agStatus = ag.configured === false ? statusEl("warn", "HeliOS has no AdGuard login yet")
      : ag.reachable ? statusEl("ok", `Running${ag.version ? ` · ${ag.version}` : ""}${ag.protection === false ? " · protection paused" : ""}`) : statusEl("bad", "Not reachable");
    const agCard = card("AdGuard Home", agStatus,
      h("p", { class: "muted small", style: "margin-top:0" }, "Every network's DNS goes through AdGuard Home on the router. HeliOS keeps one AdGuard client per named device (its profile) and one per network default below. Block lists, upstream DNS servers and the query log live in AdGuard itself."),
      ag.configured === false ? h("div", { class: "alert alert-warn small" }, "Create /var/lib/helios/adguard.json with AdGuard's URL, user and password so HeliOS can sync profiles (see the README).") : null,
      h("div", { class: "card-actions", style: "margin-top:6px" },
        url ? h("a", { class: "btn btn-primary btn-sm", href: url, target: "_blank", rel: "noopener noreferrer" }, "Open AdGuard Home ↗") : null,
        h("button", { class: "btn btn-secondary btn-sm", onclick: ev => { ev.target.disabled = true; api("adguard-sync").then(r => toast(r.message || "Synced")).catch(e => toast(e.message, true)).finally(() => { ev.target.disabled = false; }); } }, "Sync profiles now"),
        url ? h("span", { class: "muted small mono" }, url) : null));
    const rows = data.profiles.map(p => h("tr", {},
      h("td", {}, h("strong", {}, p.label), h("div", { class: "secondary small" }, p.desc || ""), h("div", { class: "secondary mono small" }, p.name)),
      h("td", {}, h("div", { class: "chips" }, ...profileSummary(p))),
      h("td", { class: "small" }, [p.devices.length ? `${p.devices.length} device${p.devices.length === 1 ? "" : "s"}` : null,
        ...p.networks.map(n => `${netInfo(n).label} default`)].filter(Boolean).join(" · ") || h("span", { class: "muted" }, "unused")),
      h("td", { class: "actions" }, h("button", { class: "btn btn-secondary btn-sm", onclick: () => editProfile(p) }, "Edit"))));
    const profCard = card("Filtering profiles", h("button", { class: "btn btn-primary btn-sm", onclick: () => editProfile(null) }, "Add profile"),
      h("p", { class: "muted small", style: "margin-top:0" }, "Each named device uses one profile (Devices → edit). Rename, change or remove these to fit your household or business; a profile in use can't be removed."),
      h("table", {}, h("thead", {}, h("tr", {}, h("th", {}, "Profile"), h("th", {}, "Blocks"), h("th", {}, "Used by"), h("th", { class: "actions" }, ""))), h("tbody", {}, ...rows)));
    const defRows = NETWORKS.map(n => h("tr", {}, h("td", {}, vlanPill(n.name)),
      h("td", {}, hwSelect([["", "AdGuard's global settings"], ...data.profiles.map(p => [p.name, p.label])], n.defaultProfile || "", v =>
        api("network-set", JSON.stringify(netPayload(n, { defaultProfile: v || null }))).then(() => hwSaved(`${n.label}: default filtering saved`, load)).catch(e => toast(e.message, true))))));
    const defCard = card("Defaults for unnamed devices", null,
      h("p", { class: "muted small", style: "margin-top:0" }, "Devices you haven't named yet get their network's default. Named devices always use their own profile. New devices start with it too."),
      h("table", {}, h("thead", {}, h("tr", {}, h("th", {}, "Network"), h("th", {}, "Default profile"))), h("tbody", {}, ...defRows)));
    root.replaceChildren(h("div", { class: "grid" }, agCard, profCard, defCard));
  };
  function editProfile(p) {
    const isNew = !p, services = (data.adguard || {}).services;
    const m = p ? JSON.parse(JSON.stringify(p)) : { name: "", label: "", desc: "", global: false, filtering: true, safeBrowsing: true, parental: false, safeSearch: false, youtubeRestricted: false, blockedServices: [] };
    const from = p ? p.name : null, err = hwErr();
    let idTouched = !isNew;
    const idIn = h("input", { type: "text", class: "mono", value: m.name, autocomplete: "off", oninput: ev => { idTouched = true; m.name = ev.target.value.trim(); } });
    const swBox = h("div"), svcBox = h("div");
    const drawSw = () => swBox.replaceChildren(
      h("div", { class: "opt" }, h("label", { class: "switch" }, h("input", { type: "checkbox", checked: m.global, onchange: ev => { m.global = ev.target.checked; drawSw(); drawSvc(); } }), h("strong", {}, "Use AdGuard's global settings")),
        h("div", { class: "opt-desc" }, "Whatever is set in AdGuard Home itself; the switches below don't apply.")),
      ...PROFILE_UI.map(([k, l]) => h("div", { class: "opt" + (m.global ? " disabled" : "") }, h("label", { class: "switch" },
        h("input", { type: "checkbox", checked: !!m[k], disabled: m.global, onchange: ev => { m[k] = ev.target.checked; } }), h("strong", {}, l)),
        h("div", { class: "opt-desc" }, (data.settings || {})[k] || ""))));
    let q = "";
    const drawSvc = () => {
      if (m.global) return svcBox.replaceChildren();
      const chosen = h("div", { class: "net-grid" }, ...m.blockedServices.map(id => h("span", { class: "net-chip on" }, (services || []).find(x => x.id === id)?.name || id,
        h("button", { class: "btn btn-link btn-sm", style: "padding:0 0 0 4px", title: "Unblock", onclick: () => { m.blockedServices = m.blockedServices.filter(x => x !== id); drawSvc(); } }, "×"))));
      const picker = services ? h("div", { style: "margin-top:8px" },
        h("input", { type: "search", placeholder: "Search services (YouTube, TikTok, Roblox…)", value: q, style: "width:100%", oninput: ev => { q = ev.target.value.toLowerCase(); drawList(); } }), list)
        : h("label", { class: "field", style: "margin-top:8px" }, h("span", {}, "Service ids, comma-separated"), h("input", { type: "text", class: "mono", value: m.blockedServices.join(", "),
            oninput: ev => { m.blockedServices = ev.target.value.split(/[\s,]+/).filter(Boolean); } }), h("span", { class: "help" }, "AdGuard isn't reachable, so its list of services can't be shown. Ids look like youtube, tiktok, roblox."));
      svcBox.replaceChildren(h("div", { class: "card-section" }, h("h3", {}, "Blocked services"), m.blockedServices.length ? chosen : h("div", { class: "muted small" }, "None. Pick from AdGuard's list below."), picker));
      drawList();
    };
    const list = h("div", { class: "svc-list" });
    const drawList = () => { if (!services) return;
      const hits = services.filter(x => !m.blockedServices.includes(x.id) && (!q || x.name.toLowerCase().includes(q) || x.id.includes(q))).slice(0, 40);
      list.replaceChildren(...hits.map(x => h("button", { type: "button", onclick: () => { m.blockedServices = [...m.blockedServices, x.id].sort(); drawSvc(); } }, x.name))); };
    drawSw(); drawSvc();
    const slug = t => t.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").replace(/^[^a-z]+/, "").slice(0, 32);
    modal({ title: isNew ? "Add filtering profile" : `Edit ${p.label}`, wide: true, body: [err,
      h("div", { style: "display:grid;grid-template-columns:2fr 1fr;gap:0 16px" },
        h("label", { class: "field" }, h("span", {}, "Name"), h("input", { type: "text", value: m.label, placeholder: "e.g. Guests, Staff, Teens", oninput: ev => { m.label = ev.target.value; if (!idTouched) { m.name = slug(m.label); idIn.value = m.name; } } })),
        h("label", { class: "field" }, h("span", {}, "Id"), idIn, h("span", { class: "help" }, "Devices refer to this. Renaming moves them along."))),
      h("label", { class: "field" }, h("span", {}, "Description"), h("input", { type: "text", value: m.desc, placeholder: "Who it's for", oninput: ev => { m.desc = ev.target.value; } })),
      h("div", { class: "card-section" }, h("h3", {}, "What it blocks"), swBox), svcBox],
      foot: close => [
        h("button", { class: "btn btn-primary", onclick: () => { err.hidden = true;
          api("profile-set", JSON.stringify({ ...m, ...(from ? { from } : {}), devices: undefined, networks: undefined })).then(() => { close(); hwSaved(`${m.label || m.name} saved`, load); }).catch(e => hwShowErr(err, e)); } }, isNew ? "Add profile" : "Save"),
        h("button", { class: "btn btn-link", onclick: close }, "Cancel"),
        h("span", { class: "spacer" }),
        !isNew ? h("button", { class: "btn btn-link", style: "color:var(--bad)", disabled: p.devices.length + p.networks.length > 0, title: p.devices.length + p.networks.length ? "Still in use" : "",
          onclick: () => confirmDialog(`Remove ${p.label}?`, "Devices can't use it any more. Nothing uses it right now.", "Remove profile", true).then(ok => ok &&
            api("profile-remove", p.name).then(() => { close(); hwSaved(`${p.label} removed`, load); }).catch(e => hwShowErr(err, e))) },
          p.devices.length + p.networks.length ? "Remove (in use)" : "Remove profile") : null] });
  }
  root.append(h("div", { class: "loading" }, "Loading…"));
  load();
}

// The Services card: what runs, what's on by default, what can be added later
const SIZE_TEXT = { small: "small download", medium: "medium download", large: "large download" };
function servicesCard(r, reload) {
  const unitDot = units => { const v = Object.values(units || {}); if (!v.length) return null;
    const bad = v.some(x => x === "failed"), up = v.every(x => x === "active");
    return h("span", { class: "dot " + (bad ? "bad" : up ? "ok" : "idle"), title: Object.entries(units).map(([u, x]) => `${u}: ${x}`).join("\n") }); };
  const set = (x, state) => api("service-set", x.id, state).then(() => { toast(state === "default" ? `${x.label} follows site.nix again — apply to activate` : `${x.label} turned ${state} — apply to ${state === "on" ? "download and start it" : "stop and remove it"}`);
    refreshPending(); reload(); }).catch(e => toast(e.message, true));
  const row = x => h("div", { class: "svc-row" },
    h("label", { class: "switch" }, h("input", { type: "checkbox", checked: x.enabled, onchange: ev => set(x, ev.target.checked ? "on" : "off") }), h("strong", {}, x.label)),
    h("div", { class: "svc-meta" }, unitDot(x.units), h("span", { class: "muted small" }, SIZE_TEXT[x.size] || ""),
      x.override === null ? h("span", { class: "muted small" }, "· site.nix default") : h("span", { class: "small" }, "· set here ",
        h("button", { class: "btn btn-link btn-sm", style: "padding:0 4px", title: `Go back to the site.nix default (${x.default ? "on" : "off"})`, onclick: () => set(x, "default") }, "reset")),
      x.tab && x.enabled ? h("a", { class: "small", href: `#${x.tab}`, style: "margin-left:8px" }, "Settings →") : null),
    h("div", { class: "opt-desc", style: "grid-column:1 / -1" }, x.desc));
  const group = g => r.services.filter(x => x.group === g).map(row);
  return card("Services", null,
    h("p", { class: "muted small", style: "margin-top:0" }, "Turning a service on downloads its software at the next apply; turning it off removes it at the next apply. Both can be rolled back like any other change."),
    h("div", { class: "card-section", style: "margin-top:0;border-top:0;padding-top:0" }, h("h3", {}, "Core: always on"),
      h("ul", { class: "list" }, r.core.map(c => h("li", {}, h("span", { class: "lhs" }, unitDot(c.units), h("strong", {}, c.label)), h("span", { class: "muted small", style: "text-align:right" }, c.desc))))),
    h("div", { class: "card-section" }, h("h3", {}, "On by default"), ...group("default")),
    h("div", { class: "card-section" }, h("h3", {}, "Optional extras"), ...group("optional")),
    h("div", { class: "card-section" }, h("h3", {}, "Switched elsewhere"),
      h("ul", { class: "list" }, r.elsewhere.map(e => h("li", {}, e.tab ? h("a", { href: `#${e.tab}` }, e.label) : h("strong", {}, e.label), h("span", { class: "muted small", style: "text-align:right" }, e.desc))))));
}

// AI log triage settings (Advanced, while the service is on)
const GB = b => `${(b / 2 ** 30).toFixed(b >= 2 ** 30 * 10 ? 0 : 1)} GB`;
function logwatchCard(r, reload) {
  const S = JSON.parse(JSON.stringify(r.settings)), err = hwErr(), hw = r.hardware;
  const opt = (obj, key, label, desc) => h("div", { class: "opt" }, h("label", { class: "switch" }, h("input", { type: "checkbox", checked: !!obj[key], onchange: ev => { obj[key] = ev.target.checked; } }), h("strong", {}, label)),
    h("div", { class: "opt-desc" }, desc));
  const section = (title, ...kids) => h("div", { class: "card-section" }, h("h3", {}, title), ...kids);
  const numIn = (obj, key, attrs = {}) => h("input", { type: "number", class: "mono", style: "width:7em", value: obj[key] ?? "", ...attrs, oninput: ev => { obj[key] = ev.target.value === "" ? null : Number(ev.target.value); } });
  const dl = r.download && r.download.state === "running" ? r.download : null;
  // Models
  const modelBox = h("div");
  const drawModels = () => modelBox.replaceChildren(h("div", { class: "radio-cards auto" },
    ...r.presets.map(pr => h("label", { class: "radio-card" + (S.model === pr.id ? " on" : "") },
      h("input", { type: "radio", name: "lw-model", checked: S.model === pr.id, onchange: () => { S.model = pr.id; drawModels(); } }),
      h("div", {}, h("strong", {}, `${pr.label} · ${pr.params}`),
        h("span", {}, `${GB(pr.bytes)} download, needs ~${(pr.ramMB / 1024).toFixed(1)} GB RAM. ${pr.desc}`),
        h("div", { class: "chips", style: "margin-top:6px" },
          r.recommended === pr.id ? h("span", { class: "badge new" }, "recommended") : null,
          pr.downloaded ? h("span", { class: "badge" }, "downloaded") : null,
          !pr.fits ? h("span", { class: "badge paused", title: "More than half of this router's spare memory" }, "too big here") : null)))),
    h("label", { class: "radio-card" + (S.model === "site" ? " on" : "") }, h("input", { type: "radio", name: "lw-model", checked: S.model === "site", onchange: () => { S.model = "site"; drawModels(); } }),
      h("div", {}, h("strong", {}, "From site.nix"), h("span", {}, h("span", { class: "mono" }, (r.site && r.site.model) || "—"), " — a file you put there yourself."))),
    h("label", { class: "radio-card" + (S.model === "custom" ? " on" : "") }, h("input", { type: "radio", name: "lw-model", checked: S.model === "custom", onchange: () => { S.model = "custom"; drawModels(); } }),
      h("div", {}, h("strong", {}, "Custom file"), h("span", {}, "Any GGUF model on the router."),
        S.model === "custom" ? h("input", { type: "text", class: "mono", style: "width:100%;margin-top:6px", placeholder: "/var/lib/llm/my-model.gguf", value: S.customPath || "", oninput: ev => { S.customPath = ev.target.value.trim() || null; } }) : null))));
  drawModels();
  const chosen = r.presets.find(x => x.id === S.model);
  const downloadBox = h("div", { class: "card-actions" },
    dl ? h("div", { style: "flex:1" }, h("div", { class: "small" }, `Downloading ${dl.file}: ${GB(dl.done)} of ${GB(dl.bytes)}`),
      h("div", { class: "meter", style: "margin-top:4px" }, h("i", { style: `width:${Math.min(100, 100 * dl.done / dl.bytes).toFixed(1)}%` })))
      : h("button", { class: "btn btn-secondary btn-sm", disabled: !r.presets.length, onclick: () => {
          const target = r.presets.find(x => x.id === S.model) || r.presets.find(x => x.id === r.recommended);
          if (!target) return toast("Pick one of the presets to download", true);
          confirmDialog(`Download ${target.label} (${target.params})?`, `${GB(target.bytes)} from Qwen on Hugging Face. HeliOS checks the file against its published checksum before using it. It runs in the background at the lowest priority.`, "Download")
            .then(ok => ok && api("logwatch-model-get", target.id).then(() => { toast("Download started"); setTimeout(reload, 1500); }).catch(e => toast(e.message, true))); } }, "Download selected model"),
    r.download && r.download.state === "failed" ? h("span", { class: "small", style: "color:var(--bad)" }, `Last download failed: ${r.download.error}`) : null,
    chosen && !chosen.downloaded && !dl ? h("span", { class: "muted small" }, "The selected model isn't downloaded yet: triage waits until it is.") : null);
  const files = Object.entries(r.files || {});
  const filesBox = files.length ? h("ul", { class: "list" }, files.map(([f, b]) => h("li", {}, h("span", { class: "mono small" }, f), h("span", { class: "small muted" }, GB(b),
    h("button", { class: "btn btn-link btn-sm", style: "color:var(--bad)", onclick: () => confirmDialog(`Delete ${f}?`, "Frees the disk space. You can download it again later.", "Delete", true)
      .then(ok => ok && api("logwatch-model-remove", f).then(() => { toast(`${f} deleted`); reload(); }).catch(e => toast(e.message, true))) }, "Delete"))))) : null;
  // Where it runs
  const bk = [["site", "site.nix default", `Currently: ${(r.site && r.site.backend) || "?"}`], ["vulkan", "Built-in GPU", "Fastest on this router when it has an iGPU (Vulkan)."],
    ["cpu", "CPU", "Works everywhere; slower, and it competes with routing for CPU (the limits below keep that in check)."],
    ["remote", "Another machine", "A llama.cpp server elsewhere (set helios.logwatch.remoteUrl in site.nix). Lets you run big models on a PC with a GPU."]];
  const bkBox = h("div"); const drawBk = () => bkBox.replaceChildren(h("div", { class: "radio-cards auto" }, ...bk.map(([k, l, d]) => h("label", { class: "radio-card" + (S.backend === k ? " on" : "") },
    h("input", { type: "radio", name: "lw-backend", checked: S.backend === k, onchange: () => { S.backend = k; drawBk(); } }), h("div", {}, h("strong", {}, l), h("span", {}, d)))))); drawBk();
  const pz = r.pressure || {};
  const status = r.paused ? statusEl("warn", "Paused") : r.units["logwatch.timer"] === "active" ? statusEl("ok", "Running on schedule") : statusEl("idle", "Not running");
  return card("AI log triage", status,
    h("p", { class: "muted small", style: "margin-top:0" }, `This router: ${(hw.memoryMB / 1024).toFixed(0)} GB RAM, ${hw.cpus} CPU cores, ${hw.gpu ? "a GPU" : "no GPU"}. `,
      r.recommended ? ["Recommended model: ", h("strong", {}, (r.presets.find(x => x.id === r.recommended) || {}).label)] : "Too little memory for a local model: use another machine."),
    r.paused ? h("div", { class: "alert alert-warn small" }, h("strong", {}, "Paused: "), `the router was busy (${r.paused.reason}). Resumes ${fmtAgo(r.paused.until).replace(" ago", "")}`.replace("Resumes in in", "Resumes in"), " ",
      h("button", { class: "btn btn-link btn-sm", onclick: () => api("logwatch-resume").then(() => { toast("Resumed"); reload(); }) }, "Resume now")) : null,
    err,
    section("Model", modelBox, downloadBox, filesBox),
    section("Where it runs", bkBox),
    section("Schedule and limits",
      h("div", { class: "inline-fields" },
        h("label", { class: "field" }, h("span", {}, "Check every (minutes)"), numIn(S, "interval", { min: 5, max: 240 })),
        h("label", { class: "field" }, h("span", {}, "CPU threads"), numIn(S, "threads", { min: 1, max: 64 })),
        h("label", { class: "field" }, h("span", {}, "CPU limit (%)"), numIn(S, "cpuPercent", { min: 25, max: 6400 }), h("span", { class: "help" }, "100% = one core.")),
        h("label", { class: "field" }, h("span", {}, "Memory cap (MB)"), numIn(S, "memoryMB", { min: 512, placeholder: chosen ? String(chosen.ramMB) : "auto" }), h("span", { class: "help" }, "Empty = what the model needs."))),
      opt(S, "keepLoaded", "Keep the model loaded", "Faster checks, but holds its memory all the time. Off: the model loads for each check and is unloaded after.")),
    section("Protection",
      opt(S.guard, "enabled", "Pause when the router is busy", "Checked every minute. If routing has to wait for CPU or memory beyond these levels, log triage stops for a while and you get a notification."),
      h("div", { class: "inline-fields" },
        h("label", { class: "field" }, h("span", {}, "CPU pressure (%)"), numIn(S.guard, "cpuPressure", { min: 5, max: 95 }), h("span", { class: "help" }, `Now: ${pz.cpu ?? "—"}%`)),
        h("label", { class: "field" }, h("span", {}, "Memory pressure (%)"), numIn(S.guard, "memoryPressure", { min: 5, max: 95 }), h("span", { class: "help" }, `Now: ${pz.memory ?? "—"}%`)),
        h("label", { class: "field" }, h("span", {}, "Pause for (hours)"), numIn(S.guard, "pauseHours", { min: 1, max: 72 }))),
      h("p", { class: "muted small" }, "Always on regardless: it runs at the lowest CPU and disk priority, can't use more memory than its cap, and is the first thing stopped if memory runs out.")),
    h("div", { class: "card-actions" },
      h("button", { class: "btn btn-primary", onclick: () => { err.hidden = true; api("logwatch-set", JSON.stringify(S)).then(() => hwSaved("AI log triage settings saved", reload)).catch(e => hwShowErr(err, e)); } }, "Save"),
      h("button", { class: "btn btn-link", onclick: () => confirmDialog("Reset AI log triage?", "Back to the defaults: the Small model, every 10 minutes, loaded on demand, with the busy-router pause on.", "Reset")
        .then(ok => ok && api("logwatch-reset").then(() => hwSaved("Back to defaults", reload)).catch(e => hwShowErr(err, e))) }, "Reset to defaults"),
      h("span", { class: "spacer" }), h("span", { class: "muted small" }, "Switch it off under Services above.")));
}

// Shown by a service's own tab when the service is off (or on but not applied yet)
function serviceOffCard(id, title, pitch, reload) {
  const x = SERVICES[id];
  if (x && x.enabled) return card(title, statusEl("warn", "Not started yet"), h("p", {}, pitch), h("p", {}, "It's turned on and starts when you apply the pending changes."));
  return card(title, null, h("p", {}, pitch),
    h("div", { class: "card-actions" }, h("button", { class: "btn btn-primary btn-sm", onclick: () => api("service-set", id, "on").then(() => {
      toast(`${x ? x.label : title} turned on — apply to download and start it`); refreshPending(); return loadServices(); }).then(reload).catch(e => toast(e.message, true)) }, "Turn on"),
      h("span", { class: "muted small" }, `Optional extra${x && x.size ? ` · ${SIZE_TEXT[x.size]}` : ""}. Also under Advanced → Services.`)));
}

/* ----------------------------------------------------------- Reboots */
const DAY_LABELS = [["mon", "Mon"], ["tue", "Tue"], ["wed", "Wed"], ["thu", "Thu"], ["fri", "Fri"], ["sat", "Sat"], ["sun", "Sun"]];
const fmtWhen = t => new Date(t * 1000).toLocaleString([], { weekday: "short", hour: "2-digit", minute: "2-digit" });
function rebootNow(r) {
  return confirmDialog("Reboot the router now?", `Everything on your network loses internet for a minute or two while it restarts${r && r.needed ? ` into kernel ${r.installed}` : ""}.`, "Reboot now", true)
    .then(ok => ok && api("reboot").then(() => toast("Rebooting — this page reconnects when the router is back")).catch(e => toast(e.message, true)));
}
// Overview banner: only when an installed update waits for a reboot
function rebootBanner(r) {
  if (!r.needed) return "";
  return h("div", { class: "alert alert-info", style: "display:flex;gap:14px;align-items:center;flex-wrap:wrap;margin-bottom:16px" },
    h("div", { style: "flex:1;min-width:16em" }, h("strong", {}, "Reboot to finish an update. "),
      r.installed && r.running ? `Kernel ${r.installed} is installed; the router still runs ${r.running}. ` : `Updated ${r.reasons.join(", ")} take effect after a reboot. `,
      r.nextWindow ? `It reboots by itself ${fmtWhen(r.nextWindow)}.` : "Everything else is already updated."),
    h("button", { class: "btn btn-primary btn-sm", onclick: () => rebootNow(r) }, "Reboot now"),
    r.nextWindow ? null : h("a", { class: "btn btn-secondary btn-sm", href: "#advanced", onclick: () => setAdvanced(true) }, "Schedule…"));
}
// Advanced: Maintenance card (reboot window)
function maintenanceCard(r, reload) {
  const w = { ...r.maintenance.rebootWindow, days: [...r.maintenance.rebootWindow.days] }, err = hwErr();
  const days = h("div", { class: "net-grid" }, ...DAY_LABELS.map(([k, l]) => h("label", { class: "net-chip" + (w.days.includes(k) ? " on" : "") },
    h("input", { type: "checkbox", checked: w.days.includes(k), onchange: ev => { const set = new Set(w.days); ev.target.checked ? set.add(k) : set.delete(k);
      w.days = DAY_LABELS.map(([d]) => d).filter(d => set.has(d)); ev.target.parentElement.classList.toggle("on", ev.target.checked); } }), l)));
  return card("Maintenance", r.needed ? statusEl("warn", "Reboot needed") : statusEl("ok", "Up to date"),
    h("dl", { class: "kv" }, h("dt", {}, "Running kernel"), h("dd", { class: "mono" }, r.running || "—"),
      h("dt", {}, "Installed kernel"), h("dd", { class: "mono" }, r.installed || "—"),
      h("dt", {}, "Next reboot window"), h("dd", {}, r.nextWindow ? fmtWhen(r.nextWindow) : "None")),
    h("p", { class: "muted small" }, "Most updates take effect right away. Only a new kernel waits for a reboot. NixOS has no live kernel patching, so a short scheduled reboot is the way to stay current."),
    err,
    h("div", { class: "opt" }, h("label", { class: "switch" }, h("input", { type: "checkbox", checked: w.enabled, onchange: ev => { w.enabled = ev.target.checked; } }), h("strong", {}, "Reboot automatically when an update needs it")),
      h("div", { class: "opt-desc" }, "Only in this window, only if a reboot is needed, never while a change is being applied, and a window missed while the router was off isn't caught up later. You get a notification first.")),
    h("div", { class: "inline-fields", style: "align-items:flex-end" },
      h("div", { class: "field" }, h("span", { style: "display:block;font-weight:600;margin-bottom:6px" }, "Days"), days),
      h("label", { class: "field" }, h("span", {}, "Time"), h("input", { type: "time", value: w.time, oninput: ev => { w.time = ev.target.value; } }))),
    h("div", { class: "card-actions" },
      h("button", { class: "btn btn-primary btn-sm", onclick: () => { err.hidden = true;
        api("maintenance-set", JSON.stringify({ rebootWindow: w })).then(() => hwSaved("Reboot window saved", reload)).catch(e => hwShowErr(err, e)); } }, "Save"),
      h("button", { class: "btn btn-secondary btn-sm", onclick: () => rebootNow(r) }, "Reboot now")));
}

/* -------------------------------------------------------------- Printing */
function viewPrinting(root) {
  let data = null;
  const load = () => Promise.all([loadNetworks().catch(() => {}), api("printing")]).then(([, r]) => { data = r; draw(); })
    .catch(e => root.replaceChildren(h("div", { class: "alert alert-bad" }, e.message)));
  const act = (p, msg) => p.then(() => { toast(msg); load(); }).catch(e => toast(e.message, true));
  const stateEl = x => !x.live ? h("span", { class: "muted small" }, data.running ? "Not set up yet: apply" : "—")
    : statusEl(x.live.state === "paused" ? "warn" : "ok", x.live.state === "paused" ? "Paused" : x.live.state === "printing" ? "Printing" : "Ready");
  const draw = () => {
    if (!data.enabled) {
      root.replaceChildren(serviceOffCard("printing", "Print server", "Share USB and network printers with your networks, including AirPrint for phones and tablets.", load));
      return;
    }
    const a = NETWORKS.find(n => n.access && n.access.admin), cupsUrl = a ? `http://${a.prefix}.1:631/` : null;
    const nets = (data.networks || []).map(n => netInfo(n).label);
    const server = card("Print server", data.running ? statusEl("ok", "Running") : statusEl("bad", "Not running"),
      h("p", { class: "muted small", style: "margin-top:0" }, nets.length ? `Shared with ${nets.join(", ")}. Phones, tablets and computers there find the printers on their own (AirPrint / IPP).`
        : "No network has printing access yet (Networks → access)."),
      h("div", { class: "card-actions", style: "margin-top:4px" },
        cupsUrl ? h("a", { class: "btn btn-secondary btn-sm", href: cupsUrl, target: "_blank", rel: "noopener noreferrer" }, "Open CUPS ↗") : null,
        h("span", { class: "muted small" }, "CUPS has every advanced option (paper, trays, quality, access lists). Its admin pages ask for a router login.")));
    const prow = x => h("tr", {},
      h("td", {}, h("strong", {}, x.description || x.name), x.default ? h("span", { class: "badge", style: "margin-left:6px" }, "default") : null,
        h("div", { class: "secondary mono small" }, x.name), x.location ? h("div", { class: "secondary small" }, x.location) : null),
      h("td", { class: "small" }, x.model === "everywhere" ? "Driverless" : h("span", { class: "mono", title: x.model }, x.model.length > 34 ? x.model.slice(0, 32) + "…" : x.model),
        h("div", { class: "secondary mono small", title: x.deviceUri }, x.deviceUri.split("://")[0])),
      h("td", {}, stateEl(x), x.jobs && x.jobs.length ? h("div", { class: "secondary small" }, `${x.jobs.length} in queue`) : null),
      h("td", { class: "actions" },
        h("button", { class: "btn btn-secondary btn-sm", disabled: !x.live, onclick: () => act(api("printer-test", x.name), `Test page sent to ${x.description || x.name}`) }, "Test page"),
        x.live ? h("button", { class: "btn btn-link btn-sm", onclick: () => act(api("printer-pause", x.name, x.live.state === "paused" ? "off" : "on"), x.live.state === "paused" ? "Resumed" : "Paused") },
          x.live.state === "paused" ? "Resume" : "Pause") : null,
        h("button", { class: "btn btn-link btn-sm", onclick: () => editPrinter(x) }, "Edit")));
    const printers = card("Printers", h("button", { class: "btn btn-primary btn-sm", onclick: () => editPrinter(null) }, "Add printer"),
      data.printers.length ? h("table", {}, h("thead", {}, h("tr", {}, h("th", {}, "Printer"), h("th", {}, "Driver"), h("th", {}, "Status"), h("th", { class: "actions" }, ""))),
        h("tbody", {}, ...data.printers.map(prow))) : h("div", { class: "empty" }, "No printers yet. Plug one in by USB or connect it to your network, then add it below."));
    const found = card("Found on your network", h("button", { class: "btn btn-link btn-sm", onclick: load }, "Look again"),
      data.discovered.length ? h("ul", { class: "list" }, data.discovered.map(d => h("li", {},
        h("div", { class: "name-cell" }, h("div", { class: "primary" }, d.label || d.uri, d.driverless ? h("span", { class: "badge", style: "margin-left:6px" }, "driverless") : null),
          h("div", { class: "secondary mono small" }, d.uri)),
        h("button", { class: "btn btn-secondary btn-sm", onclick: () => editPrinter(null, d) }, "Add"))))
        : h("div", { class: "muted small" }, data.running ? "Nothing new. USB printers show up when plugged in; network printers when they're on and announce themselves." : "CUPS isn't running."));
    const queue = card("Print queue", null, data.jobs.length ? h("table", { class: "small" }, h("thead", {}, h("tr", {}, h("th", {}, "Job"), h("th", {}, "Printer"), h("th", {}, "From"), h("th", {}, "Size"), h("th", {}, "Sent"), h("th", { class: "actions" }, ""))),
      h("tbody", {}, ...data.jobs.map(j => h("tr", {}, h("td", { class: "mono" }, j.id), h("td", {}, j.printer), h("td", {}, j.user), h("td", {}, j.size ? fmtBytes(j.size) : "—"), h("td", { class: "muted" }, j.submitted),
        h("td", { class: "actions" }, h("button", { class: "btn btn-link btn-sm", style: "color:var(--bad)", onclick: () => act(api("print-cancel", j.id), `${j.id} cancelled`) }, "Cancel"))))))
      : h("div", { class: "muted small" }, "Nothing waiting."));
    const drvState = Object.fromEntries(data.drivers.map(d => [d.id, d.enabled]));
    const drivers = card("Drivers", null,
      h("p", { class: "muted small", style: "margin-top:0" }, "Most printers made in the last ten years need none: pick “Driverless” when adding them. Turn a driver on only for an older printer that isn't driverless."),
      h("div", {}, ...data.drivers.map(d => h("div", { class: "opt" }, h("label", { class: "switch" }, h("input", { type: "checkbox", checked: d.enabled, onchange: ev => { drvState[d.id] = ev.target.checked; } }), h("strong", {}, d.label)),
        h("div", { class: "opt-desc" }, d.desc)))),
      data.siteDrivers.length ? h("div", { class: "muted small", style: "margin:0 0 10px" }, "Also installed from site.nix: ", data.siteDrivers.map(x => h("span", { class: "mono", style: "margin-right:8px" }, x))) : null,
      h("div", { class: "card-actions" }, h("button", { class: "btn btn-primary btn-sm", onclick: () =>
        api("printing-drivers", JSON.stringify(Object.keys(drvState).filter(k => drvState[k]))).then(() => { toast("Drivers saved — apply to install"); refreshPending(); load(); }).catch(e => toast(e.message, true)) }, "Save drivers")));
    root.replaceChildren(h("div", { class: "grid" }, server, printers, found, queue, drivers));
  };
  function editPrinter(x, found) {
    const isNew = !x, err = hwErr();
    const slug = t => (t || "").replace(/%20/g, "-").replace(/[^A-Za-z0-9_-]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 40) || "printer";
    const m = x ? { ...x } : { name: slug(found ? found.label.split(" ").slice(-2).join("-") : ""), description: found ? found.label : "", location: "",
      deviceUri: found ? found.uri : "", model: found && found.driverless ? "everywhere" : "", default: !data.printers.length };
    const uris = [...data.discovered, ...data.printers.map(p => ({ uri: p.deviceUri, label: p.description || p.name }))];
    const modelBox = h("div"), results = h("div", { class: "svc-list", style: "flex-direction:column;align-items:stretch;max-height:220px" });
    const drawModel = () => modelBox.replaceChildren(h("div", { class: "muted small" }, "Selected: ", h("strong", {}, m.model === "everywhere" ? "Driverless (IPP Everywhere)" : (m.model || "none yet"))));
    const search = q => api("printer-models", q).then(r => results.replaceChildren(...r.models.map(md => h("button", { type: "button", style: "text-align:left", onclick: () => { m.model = md.model; drawModel(); } }, md.desc || md.model))))
      .catch(e => results.replaceChildren(h("span", { class: "muted small" }, e.message)));
    drawModel(); search(found ? (found.label.split(" ")[0] || "") : "");
    const text = (key, attrs = {}) => h("input", { type: "text", value: m[key] || "", autocomplete: "off", ...attrs, oninput: ev => { m[key] = ev.target.value.trim(); } });
    let uriIn;   // the connection field; the picker above it fills it in
    modal({ title: isNew ? "Add printer" : `Edit ${x.description || x.name}`, wide: true, body: [err,
      h("div", { style: "display:grid;grid-template-columns:2fr 1fr;gap:0 16px" },
        h("label", { class: "field" }, h("span", {}, "Name people see"), text("description", { placeholder: "e.g. Office laser" })),
        h("label", { class: "field" }, h("span", {}, "Queue name"), text("name", { class: "mono" }), h("span", { class: "help" }, "Letters, digits, - and _."))),
      h("label", { class: "field" }, h("span", {}, "Location"), text("location", { placeholder: "e.g. Upstairs office" })),
      h("label", { class: "field" }, h("span", {}, "Connection"),
        uris.length ? hwSelect([["", "Pick a detected printer…"], ...uris.map(u => [u.uri, `${u.label} — ${u.uri.split("://")[0]}`])], uris.some(u => u.uri === m.deviceUri) ? m.deviceUri : "",
          v => { if (v) { m.deviceUri = v; uriIn.value = v; } }) : null,
        h("div", { style: "margin-top:6px" }), (uriIn = text("deviceUri", { class: "mono", placeholder: "usb://…, ipp://printer.local/ipp/print, socket://10.30.0.50" }))),
      h("div", { class: "card-section" }, h("h3", {}, "Driver"),
        h("p", { class: "muted small", style: "margin-top:0" }, "Driverless works for most printers. Otherwise search for your model; if it's missing, turn on a driver package below the printer list and apply first."),
        h("input", { type: "search", placeholder: "Search models, e.g. Brother HL-L2350", style: "width:100%", oninput: ev => { clearTimeout(results._t); results._t = setTimeout(() => search(ev.target.value), 250); } }),
        results, modelBox),
      h("label", { class: "switch", style: "margin-top:12px" }, h("input", { type: "checkbox", checked: !!m.default, onchange: ev => { m.default = ev.target.checked; } }), h("strong", {}, "Default printer"))],
      foot: close => [
        h("button", { class: "btn btn-primary", onclick: () => { err.hidden = true;
          const body = { name: m.name, description: m.description || m.name, location: m.location, deviceUri: m.deviceUri, model: m.model, default: !!m.default, ...(x ? { from: x.name } : {}) };
          api("printer-set", JSON.stringify(body)).then(() => { close(); hwSaved(`${body.description} saved`, load); }).catch(e => hwShowErr(err, e)); } }, isNew ? "Add printer" : "Save"),
        h("button", { class: "btn btn-link", onclick: close }, "Cancel"),
        h("span", { class: "spacer" }),
        !isNew ? h("button", { class: "btn btn-link", style: "color:var(--bad)", onclick: () => confirmDialog(`Remove ${x.description || x.name}?`, "The printer disappears from every device when you apply.", "Remove printer", true)
          .then(ok => ok && api("printer-remove", x.name).then(() => { close(); hwSaved(`${x.description || x.name} removed`, load); }).catch(e => hwShowErr(err, e))) }, "Remove printer") : null] });
  }
  root.append(h("div", { class: "loading" }, "Loading…"));
  load();
}

/* ------------------------------------------------------------- Advanced */
/* ------------------------------------------------------- Admin account */
// The router login (Cockpit, sudo): change its password, and two-factor sign-in
// for Cockpit with an authenticator app (TOTP). Takes effect at once: no apply.
function accountCard(r, reload, g) {
  const err = hwErr();
  const on = r.totp, enforced = r.totpEnforced !== false;
  const gated = g && g.enforced;   // the passkey gate asks for the second factor instead
  return card("Admin account", gated ? statusEl("ok", "Passkey sign-in") : on && enforced ? statusEl("ok", "Two-factor on") : on ? statusEl("bad", "Two-factor not enforced") : statusEl("warn", "Password only"),
    h("dl", { class: "kv" },
      h("dt", {}, "Login"), h("dd", { class: "mono" }, r.user),
      h("dt", {}, "Password set"), h("dd", {}, r.passwordChanged ? new Date(r.passwordChanged * 1000).toLocaleDateString([], { year: "numeric", month: "short", day: "numeric" }) : "—"),
      h("dt", {}, "Second factor"), h("dd", {}, gated ? "At the sign-in gate: passkey (or code fallback)" : !on ? "None" : enforced ? "A code from your app, at Cockpit sign-in" : "Code set up, but not asked for"),
      h("dt", {}, "SSH keys"), h("dd", {}, `${r.sshKeys || 0} (set in site.nix)`)),
    gated ? null : h("div", { class: "alert alert-info small" }, h("strong", {}, "Recommended: a passkey. "),
      "Use Passkey sign-in (below) for the strongest second factor with the fewest prompts. A code at Cockpit sign-in (here) is the simpler fallback."),
    h("p", { class: "muted small" }, "The second factor covers these pages. SSH only takes keys, and the console, the rescue port and Cockpit's administrative access keep the password alone, so a lost key or phone can't lock you out of the router itself."),
    on && !enforced ? h("div", { class: "alert alert-bad small" }, h("strong", {}, "Cockpit isn't asking for the code. "),
      "This system version doesn't have two-factor sign-in (a rollback to an older generation?). Update or roll forward to turn it back on.") : null,
    r.mutableUsers === false ? h("div", { class: "alert alert-info small" }, "Passwords on this router are set in its Nix configuration (users.mutableUsers = false), so change them there.") : null,
    err,
    h("div", { class: "card-actions" },
      r.mutableUsers === false ? null : h("button", { class: "btn btn-secondary btn-sm", onclick: () => passwordDialog(r.user, reload) }, "Change password"),
      gated ? null : on
        ? [h("button", { class: "btn btn-secondary btn-sm", onclick: () => currentCodeDialog({ title: "Move to a new phone",
              intro: "First, a current code from the app on your old phone (or a recovery code).", okLabel: "Next",
              submit: code => totpSetup(r.user, reload, code) }) }, "Move to a new phone"),
           h("button", { class: "btn btn-danger btn-sm", onclick: () => currentCodeDialog({ title: "Turn off two-factor sign-in?",
              intro: "Cockpit will ask for the password only. The app's codes and the recovery codes stop working. Enter a current code from the app (or a recovery code) to confirm.",
              okLabel: "Turn off", danger: true,
              submit: code => apiInput(code, "account-2fa-disable").then(() => { toast("Two-factor sign-in is off"); reload(); }) }) }, "Turn off")]
        : h("button", { class: "btn btn-secondary btn-sm", onclick: () => totpSetup(r.user, reload, null) }, "Use a code at Cockpit sign-in")));
}

/* -------------------------------------------------- Passkey sign-in (gate) */
// Authelia in front of Cockpit (modules/gate.nix). Passkeys are the preferred
// second factor: one that checks its PIN gets you past the gate on its own, so the
// admin password is typed once (in Cockpit). A code is the fallback: password and
// code at the gate, then the password again in Cockpit.
const PASSKEY_WHY = [h("strong", {}, "Passkeys are HeliOS's recommended second factor. "),
  "A FIDO2 key (YubiKey, pico-fido…) or your phone checks its PIN and your touch, can't be phished, and lets you type the admin password just once. ",
  "A 6-digit code works as a fallback, but then you type the password twice (at the gate and in Cockpit), and codes can be phished."];

function askPassword({ title, intro, okLabel, danger = false, submit }) {
  const pw = h("input", { type: "password", autocomplete: "current-password" });
  const err = hwErr();
  const m = modal({ title, body: [intro ? h("p", {}, intro) : null, err, h("label", { class: "field" }, h("span", {}, "Admin password"), pw)],
    foot: close => [
      h("button", { class: "btn " + (danger ? "btn-danger" : "btn-primary"), onclick: ev => {
        err.hidden = true; ev.target.disabled = true;
        Promise.resolve().then(() => submit(pw.value)).then(close).catch(e => hwShowErr(err, e))
          .finally(() => { ev.target.disabled = false; });
      } }, okLabel),
      h("button", { class: "btn btn-link", onclick: close }, "Cancel")] });
  pw.addEventListener("keydown", ev => { if (ev.key === "Enter") m.root.querySelector(".modal-foot .btn").click(); });
  pw.focus();
}

function downloadText(text, filename, type = "application/x-x509-ca-cert") {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const a = h("a", { href: url, download: filename });
  document.body.append(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 5000);
}

function gateStep(done, title, ...body) {
  return h("li", { class: "gate-step" + (done ? " done" : "") },
    statusEl(done ? "ok" : "warn", title), h("div", { class: "gate-step-body" }, body));
}

function passkeyCard(g, reload) {
  const err = hwErr();
  const show = e => hwShowErr(err, e);
  if (!g.enabled) {
    return card("Passkey sign-in", h("span", { class: "muted small" }, "Off"),
      h("div", { class: "alert alert-info small" }, PASSKEY_WHY),
      h("p", {}, "Adds a sign-in page in front of these pages. Optional: it downloads Authelia (about 50 MB) at the next Apply; then set it up here."),
      err,
      h("div", { class: "card-actions" }, h("button", { class: "btn btn-primary btn-sm", onclick: () =>
        api("service-set", "gate", "on").then(() => { toast("Passkey sign-in turned on — apply to download and start it"); refreshPending(); reload(); }).catch(show) },
        "Turn on passkey sign-in")));
  }
  const keys = g.passkeys || [], gatePage = `${g.url}/auth/`;
  const pwAct = (title, intro, okLabel, cmd, extra = {}, after = null, danger = false) => askPassword({ title, intro, okLabel, danger,
    submit: pw => apiInput(JSON.stringify({ current: pw, ...extra }), cmd).then(r => { if (after) after(r); reload(); }) });
  const canEnforce = g.synced && (keys.length > 0 || g.totp);
  return card("Passkey sign-in",
    g.enforced ? statusEl("ok", "Required at sign-in") : statusEl("warn", "Set up, not required yet"),
    h("div", { class: "alert alert-info small" }, PASSKEY_WHY),
    g.error ? h("div", { class: "alert alert-warn small" }, `The sign-in service isn't answering yet (${g.error}). If you just applied, give it a minute.`) : null,
    err,
    h("ol", { class: "gate-steps" },
      gateStep(g.synced, "The gate knows the admin password",
        h("p", { class: "muted small" }, "The sign-in page uses the same admin password as Cockpit. Copy it there once; changing the password here keeps both in step."),
        g.synced ? null : h("button", { class: "btn btn-secondary btn-sm", onclick: () =>
          pwAct("Copy the admin password to the sign-in page", "Enter the current admin password.", "Copy", "gate-sync", {}, () => toast("Password copied to the sign-in page")) }, "Copy password")),
      gateStep(!!g.ca, "Your browser trusts the router",
        h("p", { class: "muted small" }, "Browsers only allow passkeys on pages with a trusted certificate. Download the router's certificate and install it as a trusted authority, once per device (on a phone: Settings → Security → install a certificate)."),
        h("button", { class: "btn btn-secondary btn-sm", onclick: () => api("gate-ca").then(r => downloadText(r.pem, r.filename)).catch(show) }, "Download certificate")),
      gateStep(keys.length > 0, `Passkeys (${keys.length}) — recommended`,
        keys.length ? h("ul", { class: "list" }, keys.map(k => h("li", {},
          h("span", {}, k.description, k.discoverable ? null : h("span", { class: "muted small" }, " (not stored on the key)")),
          h("span", { class: "muted small" }, k.lastUsed ? `last used ${fmtAgo(Date.parse(k.lastUsed) / 1000)}` : "not used yet"),
          h("button", { class: "btn btn-link btn-sm", onclick: () => pwAct(`Remove "${k.description}"?`, "It won't open the sign-in page any more.", "Remove", "gate-passkey-remove", { kid: k.kid }, () => toast("Passkey removed"), true) }, "Remove"))))
          : h("p", { class: "muted small" }, "None yet. Register at least two if you can (a spare key, or your phone), so losing one isn't a problem."),
        h("button", { class: "btn btn-primary btn-sm", disabled: !g.synced, title: g.synced ? null : "Copy the password first",
          onclick: () => addPasskeyDialog(gatePage, reload) }, "Add passkey")),
      gateStep(!!g.totp, "Code from an app — fallback",
        h("p", { class: "muted small" }, "For when you don't have a passkey to hand. With a code you type the admin password twice: at the gate and in Cockpit."),
        g.totp
          ? h("button", { class: "btn btn-link btn-sm", onclick: () => pwAct("Remove the code?", "The gate will only take passkeys.", "Remove", "gate-totp-remove", {}, () => toast("Code removed"), true) }, "Remove code")
          : h("button", { class: "btn btn-secondary btn-sm", disabled: !g.synced, onclick: () => askPassword({ title: "Set up a code (fallback)", intro: "Enter the current admin password.", okLabel: "Next",
              submit: pw => apiInput(JSON.stringify({ current: pw }), "gate-totp-begin").then(b => gateTotpDialog(b, reload)) }) }, "Set up code")),
      gateStep(g.enforced, "Require it at sign-in",
        h("p", { class: "muted small" }, `Then sign in at ${g.url} (other addresses go there).`, g.rescue ? " The rescue port skips it: admin password only." : "",
          g.legacyTotp ? " The code Cockpit asks for now is retired at the same time; the gate takes over." : ""),
        g.enforced
          ? h("button", { class: "btn btn-danger btn-sm", onclick: () => pwAct("Stop requiring passkey sign-in?", "Cockpit will ask for the admin password only. Your passkeys and code stay registered.", "Stop requiring", "gate-enforce", { on: false }, () => toast("No longer required"), true) }, "Stop requiring")
          : h("button", { class: "btn btn-primary btn-sm", disabled: !canEnforce, title: canEnforce ? null : "Copy the password and add a passkey (or code) first",
              onclick: () => pwAct("Require passkey sign-in?", "Make sure you've signed in at the gate once with your passkey. If anything goes wrong: the rescue port, or `sudo helios-ctl gate-off` over SSH or on the console.", "Require", "gate-enforce", { on: true },
                r => toast(r.retiredCockpitCode ? "Required — the old Cockpit code is retired" : "Required at sign-in")) }, "Require at sign-in"))),
    h("p", { class: "muted small" }, `Authelia ${g.autheliaVersion || ""}. Locked out? Use the rescue port, or run sudo helios-ctl gate-off over SSH or on the console.`));
}

function addPasskeyDialog(gatePage, reload) {
  const codeBox = h("div", { class: "mono", style: "font-size:1.3em;letter-spacing:.1em;margin:6px 0" }, "—");
  const err = hwErr();
  const fetchCode = () => api("gate-code").then(r => {
    codeBox.textContent = r.code || "—";
    if (!r.code && r.text) codeBox.textContent = r.text;
  }).catch(e => hwShowErr(err, e));
  modal({ title: "Add a passkey", wide: true, onClose: reload, body: [
    h("ol", {},
      h("li", {}, "Open the ", h("a", { href: gatePage, target: "_blank", rel: "noopener noreferrer" }, "sign-in page"), " and sign in with the admin password."),
      h("li", {}, "Go to Settings → Two-Factor Authentication and add a ", h("strong", {}, "WebAuthn credential"), " (that's the passkey)."),
      h("li", {}, "It asks for a one-time code to confirm it's you. Get it here:", codeBox,
        h("button", { class: "btn btn-secondary btn-sm", onclick: fetchCode }, "Show latest code")),
      h("li", {}, "Plug in or tap your key (or pick your phone), enter its PIN and touch it. Give it a name you'll recognise.")),
    h("p", { class: "muted small" }, "The key's PIN is checked on the key itself; the router never sees it."),
    err],
  foot: close => [h("button", { class: "btn btn-primary", onclick: close }, "Done")] });
}

function gateTotpDialog(b, reload) {
  const qr = b.qrSvg ? safeSvg(b.qrSvg) : null;
  const code = h("input", { type: "text", inputmode: "numeric", autocomplete: "one-time-code", maxlength: 7, class: "mono", placeholder: "123456" });
  const err = hwErr();
  modal({ title: "Code (fallback)", wide: true, onClose: reload, body: [
    h("p", {}, "Scan this into an authenticator app (or type the key), then enter the code it shows to check it worked."),
    h("div", { class: "secret-grid" },
      qr ? h("div", { class: "qr", role: "img", "aria-label": "QR code for the authenticator app" }, qr) : null,
      h("div", {}, h("div", { class: "muted small" }, "Key"), h("div", { class: "mono", style: "word-break:break-all" }, b.secret.replace(/(.{4})/g, "$1 ").trim()))),
    err, h("label", { class: "field" }, h("span", {}, "Code"), code)],
  foot: close => [
    h("button", { class: "btn btn-primary", onclick: () => { err.hidden = true;
      apiInput(code.value.trim(), "gate-totp-check").then(() => { close(); toast("Code works — it's ready as a fallback"); }).catch(e => hwShowErr(err, e)); } }, "Check"),
    h("button", { class: "btn btn-link", onclick: close }, "Close")] });
}

// Asks for a current app code (or recovery code); submit(code) returns a promise,
// and its error is shown in the dialog
function currentCodeDialog({ title, intro, okLabel, danger = false, submit }) {
  const code = h("input", { type: "text", inputmode: "numeric", autocomplete: "one-time-code", maxlength: 9, class: "mono", placeholder: "123456" });
  const err = hwErr();
  const m = modal({ title, body: [h("p", {}, intro), err, h("label", { class: "field" }, h("span", {}, "Code"), code)],
    foot: close => [
      h("button", { class: "btn " + (danger ? "btn-danger" : "btn-primary"), onclick: ev => {
        err.hidden = true; ev.target.disabled = true;
        Promise.resolve().then(() => submit(code.value.trim())).then(close).catch(e => hwShowErr(err, e))
          .finally(() => { ev.target.disabled = false; });
      } }, okLabel),
      h("button", { class: "btn btn-link", onclick: close }, "Cancel")] });
  code.addEventListener("keydown", ev => { if (ev.key === "Enter") m.root.querySelector(".modal-foot .btn").click(); });
  code.focus();
}

function passwordDialog(user, reload) {
  const cur = h("input", { type: "password", autocomplete: "current-password" });
  const pw = h("input", { type: "password", autocomplete: "new-password" });
  const pw2 = h("input", { type: "password", autocomplete: "new-password" });
  const err = hwErr();
  modal({ title: `New password for ${user}`, body: [
    h("p", {}, "Used to sign in to Cockpit, for sudo and on the console. It changes right away; this session stays signed in."),
    err,
    h("label", { class: "field" }, h("span", {}, "Current password"), cur),
    h("label", { class: "field" }, h("span", {}, "New password"), pw, h("span", { class: "help" }, "12–128 characters. A passphrase of a few words works well.")),
    h("label", { class: "field" }, h("span", {}, "Confirm"), pw2)],
  foot: close => [
    h("button", { class: "btn btn-primary", onclick: ev => {
      err.hidden = true;
      if (pw.value !== pw2.value) return hwShowErr(err, new Error("The two passwords don't match"));
      ev.target.disabled = true;
      apiInput(JSON.stringify({ current: cur.value, new: pw.value }), "account-password")
        .then(() => { close(); toast("Password changed"); reload(); })
        .catch(e => hwShowErr(err, e))
        .finally(() => { ev.target.disabled = false; });
    } }, "Change password"),
    h("button", { class: "btn btn-link", onclick: close }, "Cancel")] });
}

// currentCode: null when turning it on, a current code when moving to a new phone.
// Returns the begin call's promise, so a wrong current code shows in the caller's dialog.
function totpSetup(user, reload, currentCode) {
  const replacing = currentCode != null;
  return apiInput(replacing ? currentCode : "", "account-2fa-begin").then(b => {
    const qr = b.qrSvg ? safeSvg(b.qrSvg) : null;
    const code = h("input", { type: "text", inputmode: "numeric", autocomplete: "one-time-code", maxlength: 7, class: "mono", placeholder: "123456" });
    const err = hwErr();
    const body = h("div", {},
      h("p", {}, "1. In an authenticator app (Aegis, 2FAS, Google Authenticator, a password manager…), add an account by scanning this code, or type in the key."),
      replacing ? h("div", { class: "alert alert-info small" }, "The current app keeps working until you finish here.") : null,
      h("div", { class: "secret-grid" },
        qr ? h("div", { class: "qr", role: "img", "aria-label": "QR code for the authenticator app" }, qr) : null,
        h("div", {},
          h("div", { class: "muted small" }, "Key"), h("div", { class: "mono", style: "word-break:break-all;font-size:1.05em;margin:4px 0 12px" }, b.secret.replace(/(.{4})/g, "$1 ").trim()),
          h("div", { class: "muted small" }, `Account: ${user} · time-based · 6 digits · 30 s`))),
      h("p", { style: "margin-top:14px" }, "2. Enter the code the app shows now."),
      err,
      h("label", { class: "field" }, h("span", {}, "Code"), code));
    const m = modal({ title: "Two-factor sign-in", wide: true, body, foot: close => [
      h("button", { class: "btn btn-primary", onclick: ev => {
        err.hidden = true; ev.target.disabled = true;
        apiInput(code.value.trim(), "account-2fa-enable")
          .then(r => { close(); recoveryDialog(r.recoveryCodes); reload(); })
          .catch(e => hwShowErr(err, e))
          .finally(() => { ev.target.disabled = false; });
      } }, "Turn on"),
      h("button", { class: "btn btn-link", onclick: close }, "Cancel")] });
    code.addEventListener("keydown", ev => { if (ev.key === "Enter") m.root.querySelector(".btn-primary").click(); });
    code.focus();
  }, e => { if (replacing) throw e; toast(e.message, true); });
}

function recoveryDialog(codes) {
  const text = `HeliOS recovery codes (each works once, in place of an app code):\n${codes.join("\n")}\n`;
  const url = URL.createObjectURL(new Blob([text], { type: "text/plain" }));
  modal({ title: "Two-factor sign-in is on", onClose: () => URL.revokeObjectURL(url), body: [
    h("div", { class: "alert alert-warn" }, h("strong", {}, "Save these recovery codes now. "),
      "Each one signs you in once without the phone. They aren't shown again."),
    h("pre", { class: "mono", style: "font-size:1.1em;line-height:1.6" }, codes.join("\n")),
    h("p", { class: "muted small" }, "Lost the phone and the codes? Sign in with SSH or on the console and delete /var/lib/helios/totp/<login>, or boot the installer's Recovery shell.")],
  foot: close => [h("a", { class: "btn btn-primary", href: url, download: "helios-recovery-codes.txt" }, "Download"),
    h("button", { class: "btn btn-link", onclick: close }, "I've saved them")] });
}

function viewAdvanced(root) {
  const grid = h("div", { class: "grid" });
  root.append(h("div", { class: "alert alert-info small" }, "Administration and settings most people never need. Hide this page again with the gear button at the top right."), grid);
  const link = (href, label, ext) => h("a", { href, target: ext ? "_blank" : null, rel: ext ? "noopener noreferrer" : null,
    onclick: ev => { if (!ext && window.cockpit && cockpit.jump) { ev.preventDefault(); cockpit.jump(href.replace(/^\.\./, "")); } } }, label);
  const svc = h("div"), lw = h("div"), mt = h("div"), sys = h("div"), bk = h("div"), drv = h("div"), sec = h("div"), acct = h("div", { class: "acct-stack" });
  grid.append(acct, svc, mt, lw, sys, bk, drv, sec);
  const loadAcct = () => Promise.all([api("account"), api("gate").catch(() => null)])
    .then(([r, g]) => acct.replaceChildren(accountCard(r, loadAcct, g), g ? passkeyCard(g, loadAcct) : ""))
    .catch(e => acct.replaceChildren(h("div", { class: "alert alert-bad" }, e.message)));
  loadAcct();
  const loadMt = () => api("reboot-status").then(r => mt.replaceChildren(maintenanceCard(r, loadMt))).catch(e => mt.replaceChildren(h("div", { class: "alert alert-bad" }, e.message)));
  loadMt();
  const loadLw = () => SERVICES.logwatch && SERVICES.logwatch.enabled
    ? api("logwatch").then(r => {
        lw.replaceChildren(logwatchCard(r, loadLw));
        // Follow a running download; skip a refresh while someone is typing in the card
        if (r.download && r.download.state === "running") setTimeout(function tick() {
          if (!document.body.contains(lw)) return;
          if (lw.contains(document.activeElement) && document.activeElement.tagName === "INPUT" || document.querySelector(".modal")) return setTimeout(tick, 2000);
          loadLw();
        }, 2000);
      }).catch(e => lw.replaceChildren(h("div", { class: "alert alert-bad" }, e.message)))
    : lw.replaceChildren();
  const loadSvc = () => loadServices().then(r => { svc.replaceChildren(servicesCard(r, loadSvc)); loadLw(); }).catch(e => svc.replaceChildren(h("div", { class: "alert alert-bad" }, e.message)));
  loadSvc();
  api("status").then(st => {
    const admin = NETWORKS.find(n => n.access && n.access.admin);
    sys.replaceChildren(card("System", null,
      h("dl", { class: "kv" }, h("dt", {}, "Hostname"), h("dd", { class: "mono" }, st.hostname || "—"), h("dt", {}, "Domain"), h("dd", { class: "mono" }, st.domain || "—"), h("dt", {}, "Uptime"), h("dd", {}, fmtDur(st.uptime))),
      h("h3", { style: "margin:14px 0 6px" }, "More tools"),
      h("ul", { class: "list" },
        h("li", {}, link("../system", "System overview"), h("span", { class: "muted small" }, "Cockpit: hardware, time, shutdown")),
        h("li", {}, link("../system/logs", "Logs"), h("span", { class: "muted small" }, "Cockpit: the journal")),
        h("li", {}, link("../system/services", "Services"), h("span", { class: "muted small" }, "Cockpit: start, stop, restart")),
        h("li", {}, link("../updates", "Software updates"), h("span", { class: "muted small" }, "Cockpit")),
        admin ? h("li", {}, link(`http://${admin.prefix}.1:3000`, "AdGuard Home", true), h("span", { class: "muted small" }, "DNS filter settings, query log (admin networks only)")) : null)));
  }).catch(e => sys.replaceChildren(h("div", { class: "alert alert-bad" }, e.message)));
  const loadBackup = () => api("history").then(r => {
    bk.replaceChildren(card("Backups", h("button", { class: "btn btn-secondary btn-sm", onclick: ev => { ev.target.disabled = true;
        api("backup").then(b => { toast(b.changed ? "Backup saved" : "Nothing changed since the last backup"); loadBackup(); }).catch(e => { toast(e.message, true); ev.target.disabled = false; }); } }, "Back up now"),
      h("dl", { class: "kv" }, h("dt", {}, "Last backup"), h("dd", {}, r.backup && r.backup.time ? `${fmtTime(r.backup.time)} (${fmtAgo(r.backup.time)})` : "Never"),
        h("dt", {}, "Off-site copy"), h("dd", {}, !r.backup ? "—" : r.backup.pushed === true ? statusEl("ok", "Pushed to git remote") : r.backup.pushed === false ? statusEl("bad", "Push failed") : statusEl("warn", "No git remote set")))));
  }).catch(e => bk.replaceChildren(h("div", { class: "alert alert-bad" }, e.message)));
  loadBackup();
  const loadHw = () => api("hardware").then(hw => { drv.replaceChildren(driversCard(hw, loadHw)); sec.replaceChildren(securityCard(hw)); })
    .catch(e => drv.replaceChildren(h("div", { class: "alert alert-bad" }, e.message)));
  loadHw();
}

/* ---------------------------------------------------------- Advanced mode */
const ADV_KEY = "photosphere-advanced";
const ADV_TABS = new Set(["networks", "filtering", "printing", "hardware", "remote", "advanced"]);
const advancedOn = () => { try { return localStorage.getItem(ADV_KEY) === "1"; } catch (e) { return false; } };
function setAdvanced(on) { try { on ? localStorage.setItem(ADV_KEY, "1") : localStorage.removeItem(ADV_KEY); } catch (e) { /* storage blocked */ } applyAdvanced(); }
// Switchable services (Advanced → Services); tabs of services that are off stay hidden
let SERVICES = {};
const loadServices = () => api("services").then(r => { SERVICES = Object.fromEntries(r.services.map(x => [x.id, x])); applyAdvanced(); return r; });
const serviceOff = id => SERVICES[id] && !SERVICES[id].enabled;
// The phone dropdown mirrors whichever tabs are visible
function syncTabMenu() {
  const sel = $("#tab-menu");
  if (!sel) return;
  const cur = (location.hash || "#overview").slice(1).split("/")[0];
  sel.replaceChildren(...[...document.querySelectorAll("#tabs a")].filter(a => !a.hidden).map(a => {
    const badge = a.querySelector(".tab-badge");
    const label = a.firstChild.textContent.trim() + (badge && !badge.hidden && badge.textContent ? ` (${badge.textContent})` : "");
    return h("option", { value: a.dataset.tab, selected: a.dataset.tab === cur }, label);
  }));
}
function applyAdvanced() {
  const on = advancedOn();
  for (const a of document.querySelectorAll("#tabs a[data-adv]")) a.hidden = !on || (a.dataset.svc && serviceOff(a.dataset.svc));
  // Hide empty second-row groups (plain JS rather than CSS :has(), for older Firefox)
  for (const sub of document.querySelectorAll("#tabs .tab-sub")) sub.hidden = ![...sub.querySelectorAll("a")].some(a => !a.hidden);
  syncTabMenu();
  const b = $("#adv-btn");
  b.classList.toggle("on", on); b.setAttribute("aria-pressed", on ? "true" : "false");
  b.title = on ? "Hide advanced settings" : "Show advanced settings";
  b.setAttribute("aria-label", b.title);
}
$("#adv-btn").addEventListener("click", () => {
  const on = !advancedOn(); setAdvanced(on);
  if (!on && ADV_TABS.has(currentTab())) location.hash = "#overview"; else rerender();
});
$("#tab-menu").addEventListener("change", ev => { location.hash = "#" + ev.target.value; });
window.addEventListener("hashchange", syncTabMenu);
applyAdvanced();
loadServices().catch(() => {});

const VIEWS = new Map(Object.entries({ overview: viewOverview, devices: viewDevices, networks: viewNetworks, filtering: viewFiltering, printing: viewPrinting, hardware: viewHardware, remote: viewRemote, traffic: viewTraffic, qos: viewQos, upnp: viewUpnp, notifications: viewSolarWind, history: viewHistory, advanced: viewAdvanced }));
function currentTab() { const t = (location.hash || "#overview").slice(1).split("/")[0]; return VIEWS.has(t) ? t : "overview"; }
function rerender() {
  cleanupView();
  const tab = currentTab();
  for (const a of document.querySelectorAll("#tabs a")) a.classList.toggle("active", a.dataset.tab === tab);
  const root = $("#view");
  root.replaceChildren();
  if (ADV_TABS.has(tab) && !advancedOn()) {
    root.append(card("Advanced setting", null, h("p", {}, "This page is part of the advanced settings, which are hidden by default."),
      h("button", { class: "btn btn-primary", onclick: () => { setAdvanced(true); rerender(); } }, "Show advanced settings")));
    return;
  }
  VIEWS.get(tab)(root);
}
window.addEventListener("hashchange", rerender);
let resizeT = null;
window.addEventListener("resize", () => { clearTimeout(resizeT); resizeT = setTimeout(() => document.dispatchEvent(new CustomEvent("helios-samples")), 150); });

api("status").then(st => {
  RESERVED = ["heliosphere", ...(st.reservedNames || [])];
  if (st.domain) DOMAIN = st.domain;
  $("#head-title").textContent = `HeliOS on ${st.hostname}`; $("#head-sub").textContent = `Up ${fmtDur(st.uptime)}`; document.title = `HeliOS on ${st.hostname}`;
  const tb = $("#tab-new"); tb.hidden = !st.devices.new; tb.textContent = st.devices.new; syncTabMenu();
}).catch(() => {});
refreshPending();
loadNetworks().catch(e => toast(e.message, true)).finally(rerender);
