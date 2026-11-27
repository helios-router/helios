/* Test double for Cockpit's cockpit.js: answers helios-ctl calls with realistic,
   stateful demo data so the pages can be rendered and screenshotted offline. */
(function () {
  const T = () => Date.now() / 1000;
  const now0 = T();
  const devices = [
    { mac: "11:22:33:44:55:66", name: "titan", vlan: "trusted", ip: "10.20.0.10", profile: "admin", blocked: false, media: false, notes: "Home server", mediaServer: { tcp: [8096, 8920], udp: [], networks: ["family"] } },
    { mac: "02:00:00:00:20:11", name: "oberon", vlan: "trusted", ip: "10.20.0.11", profile: "admin", blocked: false, media: false, notes: "Music server", mediaServer: { tcp: [4533], udp: [], networks: [] } },
    { mac: "3c:7c:3f:1a:22:b0", name: "mars", vlan: "trusted", ip: "10.20.0.20", profile: "admin", blocked: false, media: false, notes: "Desktop" },
    { mac: "f4:6d:3f:82:10:9e", name: "venus", vlan: "trusted", ip: "10.20.0.21", profile: "admin", blocked: false, media: false, notes: "Laptop" },
    { mac: "a2:5b:11:0c:7e:41", name: "mercury", vlan: "family", ip: "10.30.0.20", profile: "admin", blocked: false, media: false, notes: "Phone" },
    { mac: "68:ec:c5:31:aa:02", name: "europa", vlan: "family", ip: "10.30.0.21", profile: "adult", blocked: false, media: false, notes: "Family phone" },
    { mac: "d8:43:ae:5f:90:17", name: "ceres", vlan: "family", ip: "10.30.0.22", profile: "kid", blocked: false, media: false, notes: "Kid's gaming PC" },
    { mac: "5e:21:9c:44:01:b3", name: "vesta", vlan: "family", ip: "10.30.0.23", profile: "kid", blocked: true, media: false, notes: "Kid's phone" },
    { mac: "7c:ed:8d:02:6a:c4", name: "ganymede", vlan: "family", ip: "10.30.0.24", profile: "adult", blocked: false, media: false, notes: "Xbox Series X" },
    { mac: "ac:f1:08:7b:3e:55", name: "pluto", vlan: "iot", ip: "10.40.0.20", profile: "default", blocked: false, media: true, notes: "Living-room TV" },
    { mac: "e8:9f:6d:20:c1:08", name: "charon", vlan: "iot", ip: "10.40.0.21", profile: "default", blocked: false, media: true, notes: "Streaming stick" },
    { mac: "50:ec:50:9a:4d:71", name: "haumea", vlan: "iot", ip: "10.40.0.22", profile: "default", blocked: false, media: false, notes: "Robot vacuum" },
    { mac: "84:f3:eb:11:0f:2c", name: "quaoar", vlan: "iot", ip: "10.40.0.23", profile: "default", blocked: false, media: false, notes: "Smart plug" },
    { mac: "9c:6b:00:0a:51:e2", name: "jupiter", vlan: "mgmt", ip: "10.99.0.2", profile: "admin", blocked: false, media: false, notes: "Managed switch" },
    { mac: "04:42:1a:6c:9d:30", name: "io", vlan: "mgmt", ip: "10.99.0.3", profile: "admin", blocked: false, media: false, notes: "Wi-Fi access point" },
  ];
  let committed = JSON.parse(JSON.stringify(devices));
  const unknown = [
    { mac: "2c:4d:54:e1:77:09", hostname: "Galaxy-Tab-A8", vlan: "family", ip: "10.30.0.117" },
    { mac: "c4:5b:be:4a:21:f0", hostname: "ESP_4A21F0", vlan: "iot", ip: "10.40.0.104" },
    { mac: "00:e0:4c:68:12:aa", hostname: "DESKTOP-K3J2QH", vlan: "strict", ip: "10.50.0.100" },
  ];
  const offline = new Set(["mars", "venus"]);
  const T0 = Date.now() / 1000;
  // Services (pkgs/services.json): site.nix defaults + overrides; tabs follow the *applied* state
  const SCAT = (() => { const x = new XMLHttpRequest(); x.open("GET", "../services.json", false); x.send(); return JSON.parse(x.responseText); })();
  const SVC_DEFAULTS = { notifications: true, vulnScan: true, printing: true, remote: true, logwatch: false, gate: false };
  let svcOver = {}, svcOver0 = {};
  let restrictUnnamed = false, restrictUnnamed0 = false;
  let maint = { rebootWindow: { enabled: false, days: ["sun"], time: "04:00" } }, maint0 = JSON.parse(JSON.stringify(maint));
  let account = { totp: false, pending: null };
  let gate = { synced: false, enforced: false, passkeys: [], totp: false };
  let lwState = {}, lwState0 = {}, lwFiles = { "model.gguf": 1107409472 }, lwDl = null,
    lwPaused = { since: T0 - 3600, until: T0 + 5 * 3600, reason: "CPU pressure 52%" };
  const svcOn = (id, over = svcOver) => (over[id] ?? SVC_DEFAULTS[id]);
  // Printing (docs: modules/pioneer.nix). Live CUPS state is faked; the config part is state like the rest.
  const PRN_CAT = { gutenprint: { label: "Gutenprint", desc: "Hundreds of inkjets and photo printers: Canon, Epson, HP, Lexmark and more." },
    hplip: { label: "HP (HPLIP)", desc: "HP inkjets and LaserJets that don't print driverless." }, brlaser: { label: "Brother lasers", desc: "Brother monochrome laser printers (HL-, DCP-, MFC- series)." },
    foo2zjs: { label: "foo2zjs", desc: "ZjStream and similar lasers: older HP LaserJet, Minolta, Samsung, Oki, Xerox." }, splix: { label: "SpliX", desc: "Samsung and Xerox SPL laser printers." },
    "epson-escpr": { label: "Epson ESC/P-R", desc: "Most Epson inkjets." }, "epson-escpr2": { label: "Epson ESC/P-R 2", desc: "Newer Epson inkjets and EcoTank models." } };
  let printing = { drivers: [], printers: [{ name: "den-inkjet", description: "Den inkjet", location: "Den", deviceUri: "ipp://10.30.0.50/ipp/print", model: "everywhere", default: true }] };
  let printing0 = JSON.parse(JSON.stringify(printing)), printLive = { "den-inkjet": "printing" }, printJobs = [{ id: "den-inkjet-7", printer: "den-inkjet", user: "europa", size: 1048576, submitted: "today 10:05" }];
  const PRN_FOUND = [{ uri: "usb://Brother/HL-L2350DW%20series?serial=E78123", kind: "direct", label: "Brother HL-L2350DW series", driverless: false },
    { uri: "ipp://10.30.0.50/ipp/print", kind: "network", label: "10.30.0.50 ipp print", driverless: true },
    { uri: "ipps://Canon%20TS5300._ipps._tcp.local/", kind: "network", label: "Canon TS5300._ipps._tcp.local", driverless: true }];
  const PRN_MODELS = [["drv:///brlaser.drv/brl2350.ppd", "Brother HL-L2350DW series, using brlaser v6"], ["gutenprint.5.3://bjc-PIXMA-iP4200/expert", "Canon PIXMA iP4200 - CUPS+Gutenprint v5.3.5"],
    ["drv:///sample.drv/generic.ppd", "Generic PostScript Printer"], ["drv:///sample.drv/laserjet.ppd", "HP LaserJet Series PCL 4/5"]];
  let upnpMaps = [
    { proto: "UDP", ext_port: 3074, ip: "10.30.0.24", int_port: 3074, expires: T0 + 2900, desc: "Xbox Live" },
    { proto: "UDP", ext_port: 88, ip: "10.30.0.24", int_port: 88, expires: T0 + 2900, desc: "Xbox Live" },
    { proto: "TCP", ext_port: 27036, ip: "10.30.0.22", int_port: 27036, expires: null, desc: "Steam" },
    { proto: "TCP", ext_port: 8443, ip: "10.30.0.117", int_port: 8443, expires: null, desc: "IPCam remote view" }];
  let qos = { enabled: true, down: "500mbit", up: "50mbit", algorithm: "cake", link: "docsis", overhead: 38, mpu: 84, atm: false,
    fairness: "device", priority: "diffserv3", ackFilter: true, rtt: "internet", wash: true }, upnp = { enabled: true }, qos0 = { ...qos }, upnp0 = { ...upnp };
  let apply = {};
  // Roles + default networks: the real pkgs/networks.json (demo.sh serves it next to the page)
  const CAT = (() => { const x = new XMLHttpRequest(); x.open("GET", "../networks.json", false); x.send(); return JSON.parse(x.responseText); })();
  let networks = JSON.parse(JSON.stringify(CAT.defaults)), networks0 = JSON.parse(JSON.stringify(networks));
  const access = n => ({ ...CAT.roles[n.role].access, ...(n.access || {}) });
  // Filtering profiles: the real pkgs/profiles.json (demo.sh serves it next to the page)
  const PCAT = (() => { const x = new XMLHttpRequest(); x.open("GET", "../profiles.json", false); x.send(); return JSON.parse(x.responseText); })();
  let profiles = JSON.parse(JSON.stringify(PCAT.defaults)), profiles0 = JSON.parse(JSON.stringify(profiles));
  const AG_SERVICES = [["youtube", "YouTube"], ["tiktok", "TikTok"], ["instagram", "Instagram"], ["facebook", "Facebook"], ["snapchat", "Snapchat"], ["roblox", "Roblox"],
    ["fortnite", "Fortnite"], ["minecraft", "Minecraft"], ["discord", "Discord"], ["twitch", "Twitch"], ["netflix", "Netflix"], ["reddit", "Reddit"], ["steam", "Steam"], ["x", "X (Twitter)"]]
    .map(([id, name]) => ({ id, name }));
  const netView = n => ({ ...n, label: n.label || n.name, color: n.color || CAT.roles[n.role].color, desc: CAT.roles[n.role].desc,
    access: access(n), overrides: Object.keys(n.access || {}).sort(), devices: devices.filter(d => d.vlan === n.name).length });
  // Driver framework (phase 1): the real catalog (demo.sh serves pkgs/drivers.json next to the page)
  const DRV = (() => { const x = new XMLHttpRequest(); x.open("GET", "../drivers.json", false); x.send(); return JSON.parse(x.responseText); })();
  const NUT0 = DRV.nutDefaults;
  let drivers = {}, hardware = [], drivers0 = {}, hardware0 = [];
  const detected = {
    time: Math.floor(now0 - 7200),
    ports: [
      { name: "trunk0", mac: "02:00:00:00:01:01", driver: "igc", bus: "pci", speed: 2500, carrier: true, role: "trunk" },
      { name: "rescue0", mac: "02:00:00:00:01:02", driver: "r8169", bus: "pci", speed: 1000, carrier: false, role: "rescue" },
      { name: "enp4s0", mac: "02:00:00:00:01:03", driver: "igc", bus: "pci", speed: 1000, carrier: true, role: "free" },
      { name: "enp5s0", mac: "02:00:00:00:01:04", driver: "igc", bus: "pci", speed: 0, carrier: false, role: "free" },
      { name: "enx020000000105", mac: "02:00:00:00:01:05", driver: "r8152", bus: "usb", speed: 1000, carrier: true, role: "free" },
    ],
    usb: [{ id: "051d:0002", vendor: "American Power Conversion", product: "Back-UPS ES 700G", serial: "4B0000X00001", kind: "ups" }],
    lldp: [
      { localPort: "trunk0", chassis: "switch-1", mgmtIp: "10.99.0.2", sysDescr: "Managed 8-port gigabit switch", port: "Ethernet1/0/8", portDescr: "uplink to router", capabilities: ["Bridge"] },
      { localPort: "trunk0", chassis: "ap-1", mgmtIp: "10.99.0.3", sysDescr: "Wi-Fi 6 access point", port: "eth0", portDescr: "eth0", capabilities: ["WLAN Access Point"] },
    ],
  };
  const drvOn = n => (drivers[n] ? drivers[n].enabled : DRV.drivers[n].default) !== false;
  const slug = t => t.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
  const netLabel = n => (networks.find(x => x.name === n) || { label: n }).label;
  const vlanOf = n => (networks.find(x => x.name === n) || { vlan: "?" }).vlan;
  const portMapped = p => { const r = hardware.find(d => d.kind === "router"); return r && r.ports.find(e => (e.match.mac && p.bus !== "dsa" && e.match.mac === p.mac) || (e.match.name && e.match.name === p.name)); };
  function checklist(d) {
    const out = [];
    const vl = n => `${vlanOf(n)} (${netLabel(n)})`;
    for (const p of d.ports || []) {
      const note = p.note ? ` — ${p.note}` : "";
      if (p.mode === "access") out.push(`Port ${p.port}: access — untagged VLAN ${vl(p.network)}${note}`);
      else if (p.mode === "trunk") out.push(`Port ${p.port}: trunk — tagged VLANs ${(p.networks === "all" ? networks.map(n => n.name) : p.networks).map(vl).join(", ")}${p.wan ? ", 10 (WAN)" : ""}${note}`);
      else if (p.mode === "wan") out.push(`Port ${p.port}: WAN — untagged VLAN 10 (modem)${note}`);
      else out.push(`Port ${p.port}: unused — disable it or leave it empty${note}`);
    }
    for (const x of d.ssids || []) out.push(`SSID “${x.ssid}” → VLAN ${vl(x.network)}`);
    return out;
  }
  function suggestions() {
    const out = [];
    for (const u of detected.usb) if (u.kind === "ups" && !hardware.some(d => d.kind === "ups" && d.match.usb === u.id))
      out.push({ kind: "ups", driver: "nut", reason: `${u.vendor} ${u.product} found on USB`,
        device: { id: "ups", kind: "ups", driver: "nut", label: `${DRV.upsVendors[u.id.split(":")[0]] || u.vendor} ${u.product}`, match: { usb: u.id, serial: u.serial }, nutDriver: "usbhid-ups" } });
    const free = detected.ports.filter(p => !["trunk", "rescue"].includes(p.role) && !portMapped(p));
    if (free.length && !hardware.some(d => d.kind === "router"))
      out.push({ kind: "ports", driver: "local-ports", reason: `${free.length} spare Ethernet port${free.length === 1 ? "" : "s"} found (${free.map(p => p.name).join(", ")})`,
        device: { id: "router", kind: "router", driver: "local-ports", label: "This router", ports: free.map(p => ({ match: { mac: p.mac }, mode: "unused" })) } });
    for (const n of detected.lldp) if (!hardware.some(d => d.label === n.chassis)) {
      const ap = n.capabilities.some(c => /access point/i.test(c));
      out.push({ kind: ap ? "ap" : "switch", driver: "manual", reason: `${ap ? "Access point" : "Switch"} “${n.chassis}” announced itself on ${n.localPort} (${n.mgmtIp})`,
        device: { id: slug(n.chassis), kind: ap ? "ap" : "switch", driver: "manual", label: n.chassis, notes: `Found via LLDP on ${n.localPort}; management address ${n.mgmtIp}.`,
          ports: ap ? [{ port: "uplink", mode: "trunk", networks: "all", wan: false }] : [], ...(ap ? { ssids: [] } : {}) } });
    }
    return out;
  }
  function validHw(d, others) {
    if (!/^[a-z][a-z0-9-]{0,31}$/.test(d.id || "")) return "id must be lowercase letters, digits or dashes, starting with a letter (max 32)";
    if (others.some(x => x.id === d.id)) return `a device with id '${d.id}' already exists`;
    if (!["router", "switch", "ap", "ups"].includes(d.kind)) return "kind must be router, switch, ap or ups";
    if (d.kind === "router" && d.id !== "router") return "the router device must have id 'router'";
    if (d.kind === "router" && others.some(x => x.kind === "router")) return "there can be only one router device";
    if (d.driver !== { router: "local-ports", switch: "manual", ap: "manual", ups: "nut" }[d.kind]) return `driver '${d.driver}' doesn't fit a ${d.kind}`;
    if (!String(d.label || "").trim()) return "label is required";
    if (d.kind === "ups") {
      if (!/^[0-9a-f]{4}:[0-9a-f]{4}$/i.test((d.match || {}).usb || "")) return "a UPS needs match.usb as vvvv:pppp (hex)";
      return null;
    }
    const seen = new Set(); let wans = 0;
    for (const p of d.ports || []) {
      const name = d.kind === "router" ? ((p.match || {}).mac || (p.match || {}).name) : p.port;
      if (!name) return d.kind === "router" ? "every router port needs match.mac or match.name" : "every port needs a name";
      if (seen.has(name)) return `port ${name} is listed twice`;
      seen.add(name);
      if (d.kind === "router") {
        const det = detected.ports.find(x => (p.match.mac && x.mac === p.match.mac) || (p.match.name && x.name === p.match.name));
        if (det && ["trunk", "rescue"].includes(det.role)) return `${det.name} is the ${det.role} port and can't be mapped`;
      }
      if (!["access", "trunk", "wan", "unused"].includes(p.mode)) return `port ${name}: unknown mode '${p.mode}'`;
      if (p.mode === "access" && !networks.some(n => n.name === p.network)) return `port ${name}: access needs a network`;
      if (p.mode === "trunk") {
        if (p.networks !== "all" && !(Array.isArray(p.networks) && p.networks.every(n => networks.some(x => x.name === n)))) return `port ${name}: trunk networks must be "all" or a list of networks`;
        if (p.networks !== "all" && !p.networks.length && !p.wan) return `port ${name}: a trunk needs at least one network`;
      }
      if (p.mode === "wan") wans++;
    }
    if (d.kind === "router" && wans > 1) return "only one router port can be the WAN port";
    for (const x of d.ssids || []) {
      if (d.kind !== "ap") return "only access points have SSIDs";
      if (!x.ssid || x.ssid.length > 32) return "an SSID must be 1-32 characters";
      if (!networks.some(n => n.name === x.network)) return `SSID ${x.ssid}: unknown network`;
    }
    return null;
  }
  // Remote access (docs/heliosphere.md). Secrets (client keys, egress files) never enter this state.
  let heliosphereOn = true;
  let remote = { ingress: { tailscale: { enabled: true, exitNode: true, hostname: "heliosphere", lan: [] },
      wireguard: { enabled: true, port: 51820, lan: [], tunnel: "full", keepalive: 25, mtu: null, peers: [{ name: "phone-1", publicKey: "mockPubKeyPhone1AAAAAAAAAAAAAAAAAAAAAAAAA=", ip: "10.61.0.2" }, { name: "laptop-1", publicKey: "mockPubKeyLaptop1AAAAAAAAAAAAAAAAAAAAAAAA=", ip: "10.61.0.3" }] },
      openvpn: { enabled: false, port: 1194, proto: "udp", clients: [], lan: [] },
      proxy: { enabled: false, socksPort: 1080, httpPort: 3128, networks: [], users: [] } },
    egress: { mode: "direct", killswitch: true, proxy: { type: "socks5", host: "proxy.example.net", port: 1080 } } };
  let remote0 = JSON.parse(JSON.stringify(remote));
  const egressFiles = { wireguard: false, openvpn: false, auth: false };
  let serverKey = "mockServerKey0000000000000000000000000000000=";
  const RESERVED_PORTS = [22, 53, 67, 123, 443, 2586, 3493, 5353, 9090, 41641];
  const rnd = n => Array.from({ length: n }, () => "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"[Math.floor(Math.random() * 64)]).join("");
  const nameOk = n => /^[a-z0-9][a-z0-9-]{0,31}$/.test(n || "");
  function fakeQr(text) {   // QR-like (finder patterns + noise), not scannable
    const N = 29; let seed = 0; for (const c of text) seed = (seed * 31 + c.charCodeAt(0)) >>> 0;
    const rand = () => (seed = (seed * 1664525 + 1013904223) >>> 0) / 4294967296;
    const finder = (x, y) => x < 8 && y < 8 || x >= N - 8 && y < 8 || x < 8 && y >= N - 8;
    let d = "";
    for (let y = 0; y < N; y++) for (let x = 0; x < N; x++) {
      let on;
      if (finder(x, y)) { const lx = x < 8 ? x : x - (N - 8), ly = y < 8 ? y : y - (N - 8), m = Math.max(Math.abs(lx - 3.5), Math.abs(ly - 3.5)); on = m <= 3.5 && (m > 2.5 || m <= 1.5); }
      else on = rand() < 0.5;
      if (on) d += `M${x},${y}h1v1h-1z`;
    }
    return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="-2 -2 ${N + 4} ${N + 4}" shape-rendering="crispEdges"><rect x="-2" y="-2" width="${N + 4}" height="${N + 4}" fill="#fff"/><path d="${d}" fill="#000"/></svg>`;
  }
  function validRemote(r) {
    const i = r.ingress, ports = [];
    for (const [k, v] of [["WireGuard port", i.wireguard.port], ["OpenVPN port", i.openvpn.port], ["SOCKS port", i.proxy.socksPort], ["HTTP port", i.proxy.httpPort]]) {
      if (!Number.isInteger(v) || v < 1 || v > 65535) return `${k} must be 1-65535`;
      if (RESERVED_PORTS.includes(v)) return `${k}: ${v} is reserved for another service`;
      ports.push(v);
    }
    if (new Set(ports).size !== ports.length) return "ports must all be different";
    if (!["udp", "tcp"].includes(i.openvpn.proto)) return "OpenVPN protocol must be udp or tcp";
    if (!i.proxy.networks.every(n => networks.some(x => x.name === n))) return "unknown network in the proxy's networks";
    if (i.proxy.enabled && !i.proxy.users.length) return "the proxy needs at least one user";
    if (i.tailscale.enabled && !i.tailscale.exitNode && !i.tailscale.lan.length) return "Tailscale needs the exit node or at least one home network, or it gives devices nothing";
    if (i.wireguard.tunnel === "split" && !i.wireguard.lan.length) return "“Only home networks” needs at least one home network to reach";
    if (!Number.isInteger(i.wireguard.keepalive) || i.wireguard.keepalive < 0 || i.wireguard.keepalive > 600) return "wireguard.keepalive must be 0 (off) to 600 seconds";
    if (i.wireguard.mtu !== null && (!Number.isInteger(i.wireguard.mtu) || i.wireguard.mtu < 1280 || i.wireguard.mtu > 1500)) return "wireguard.mtu must be empty (automatic) or 1280 to 1500";
    if (!/^[a-z0-9][a-z0-9-]{0,62}$/.test(i.tailscale.hostname)) return "tailscale.hostname: lowercase letters, digits and dashes (max 63)";
    if (i.proxy.enabled && !i.proxy.networks.length) return "the proxy needs at least one home network (it isn't offered on the internet: it has no encryption)";
    const e = r.egress;
    if (!["direct", "wireguard", "openvpn", "proxy"].includes(e.mode)) return "unknown way out";
    if (typeof e.killswitch !== "boolean") return "kill switch must be on or off";
    if (e.mode === "wireguard" && !egressFiles.wireguard) return "upload a WireGuard config first";
    if (e.mode === "openvpn" && !egressFiles.openvpn) return "upload an OpenVPN config first";
    if (e.mode === "proxy") {
      if (!["socks5", "http"].includes(e.proxy.type)) return "proxy type must be socks5 or http";
      if (!/^[A-Za-z0-9.-]+$/.test(e.proxy.host || "")) return "enter the proxy's host name or address";
      if (!Number.isInteger(e.proxy.port) || e.proxy.port < 1 || e.proxy.port > 65535) return "proxy port must be 1-65535";
    }
    return null;
  }
  function remoteChanges() {
    const m = [], a = remote0.ingress, b = remote.ingress;
    const flag = (label, x, y) => { if (x.enabled !== y.enabled) m.push(`${y.enabled ? "Enable" : "Disable"} ${label}`); };
    flag("Tailscale exit", a.tailscale, b.tailscale); flag("WireGuard", a.wireguard, b.wireguard); flag("OpenVPN", a.openvpn, b.openvpn); flag("proxy", a.proxy, b.proxy);
    if (a.wireguard.port !== b.wireguard.port) m.push(`Change WireGuard port to ${b.wireguard.port}`);
    if (["exitNode", "hostname", "lan"].some(k => JSON.stringify(a.tailscale[k]) !== JSON.stringify(b.tailscale[k]))) m.push("Change Tailscale settings");
    if (["tunnel", "keepalive", "mtu", "lan"].some(k => JSON.stringify(a.wireguard[k]) !== JSON.stringify(b.wireguard[k]))) m.push("Change WireGuard settings");
    if (JSON.stringify(a.openvpn.lan) !== JSON.stringify(b.openvpn.lan)) m.push("Change OpenVPN home networks");
    for (const y of b.wireguard.peers) { const x = a.wireguard.peers.find(q => q.name === y.name); if (x && x.publicKey !== y.publicKey) m.push(`New key for WireGuard peer ${y.name}`); }
    if (a.openvpn.port !== b.openvpn.port || a.openvpn.proto !== b.openvpn.proto) m.push(`Change OpenVPN to ${b.openvpn.port}/${b.openvpn.proto}`);
    if (["socksPort", "httpPort"].some(k => a.proxy[k] !== b.proxy[k]) || JSON.stringify(a.proxy.networks) !== JSON.stringify(b.proxy.networks)) m.push("Change proxy settings");
    const diff = (label, xs, ys, key, extra) => { for (const y of ys) if (!xs.some(x => x[key] === y[key])) m.push(`Add ${label} ${y[key]}`);
      for (const x of xs) if (!ys.some(y => y[key] === x[key])) m.push(`Remove ${label} ${x[key]}`); if (extra) extra(xs, ys); };
    diff("WireGuard peer", a.wireguard.peers, b.wireguard.peers, "name");
    diff("OpenVPN client", a.openvpn.clients, b.openvpn.clients, "name", (xs, ys) => { for (const y of ys) { const x = xs.find(q => q.name === y.name); if (x && !x.revoked && y.revoked) m.push(`Revoke OpenVPN client ${y.name}`); } });
    diff("proxy user", a.proxy.users.map(name => ({ name })), b.proxy.users.map(name => ({ name })), "name");
    const e0 = remote0.egress, e1 = remote.egress;
    if (e0.mode !== e1.mode) m.push(`Way out: ${e0.mode} → ${e1.mode}`);
    if (e0.killswitch !== e1.killswitch) m.push(`Kill switch ${e1.killswitch ? "on" : "off"}`);
    if (JSON.stringify(e0.proxy) !== JSON.stringify(e1.proxy)) m.push("Change upstream proxy");
    return m;
  }
  const outages = [
    { down_at: now0 - 86400 * 5 - 3600 * 3, up_at: now0 - 86400 * 5 - 3600 * 3 + 420, duration: 420 },
    { down_at: now0 - 86400 * 2 - 3600 * 9, up_at: now0 - 86400 * 2 - 3600 * 9 + 95, duration: 95 },
    { down_at: now0 - 3600 * 6, up_at: now0 - 3600 * 6 + 187, duration: 187 },
  ];
  const commits = [
    ["Pause vesta", 3600 * 1.2], ["Add pluto (iot, default)", 3600 * 5], ["Backup: AdGuard settings", 3600 * 20],
    ["Edit ceres: profile", 86400 * 1.4], ["Change qos settings", 86400 * 2.1], ["Add ganymede (family, adult)", 86400 * 3],
    ["Add jupiter (mgmt, admin) (+1 more)", 86400 * 4], ["Initial snapshot of /etc/nixos", 86400 * 6],
  ].map(([subject, ago], i) => ({ rev: (Math.random().toString(16) + "00000000").slice(2, 10) + i, short: "", time: now0 - ago, author: i === 7 ? "admin" : "helios", subject }));
  commits.forEach(c => { c.short = c.rev.slice(0, 8); });

  // traffic counters: smooth pseudo-random rates
  const ifs = ["wan", "trusted", "family", "iot", "strict", "mgmt", "tailscale0", "ve-heliosphere", "trunk0"];
  const ctr = Object.fromEntries(ifs.map(i => [i, { rx: 1e12 * Math.random(), tx: 1e11 * Math.random() }]));
  let lastT = T();
  function rates(i, t) {
    const w = Math.sin(t / 9) * 0.35 + Math.sin(t / 3.7) * 0.15 + 0.6;
    const base = { wan: [620e6, 24e6], family: [8e6, 380e6], iot: [2e6, 140e6], trusted: [9e6, 95e6], strict: [0.1e6, 1.5e6],
      mgmt: [0.05e6, 0.08e6], tailscale0: [3e6, 6e6], "ve-heliosphere": [1.2e6, 18e6], trunk0: [650e6, 640e6] }[i];
    return [base[0] * w, base[1] * (0.7 + 0.3 * Math.cos(t / 5))];
  }
  window.__solMock = { rates, setHeliosphere: on => { heliosphereOn = on; } };

  function devicesOut() {
    const t = T();
    const list = devices.map(d => ({ ...d, known: true, online: !offline.has(d.name) && !d.blocked, neigh_state: "REACHABLE",
      lease_ip: d.ip, lease_vlan: d.vlan, hostname: { titan: "nas", mars: "mars", ceres: "DESKTOP-KIDS", pluto: "LGwebOSTV", charon: "Chromecast", haumea: "roborock-vacuum-a75" }[d.name] || "",
      lease_since: t - 3600 * (2 + d.ip.length % 7), current_ip: d.ip }));
    for (const u of unknown) if (!devices.find(d => d.mac === u.mac))
      list.push({ mac: u.mac, name: "", vlan: u.vlan, ip: null, profile: "default", blocked: false, media: false, notes: "", known: false,
        online: true, lease_ip: u.ip, lease_vlan: u.vlan, hostname: u.hostname, lease_since: t - 900, current_ip: u.ip });
    list.sort((a, b) => (a.known - b.known) || (a.name || a.mac).localeCompare(b.name || b.mac));
    return list;
  }
  function changes() {
    const msgs = [];
    const od = Object.fromEntries(committed.map(d => [d.mac, d])), nd = Object.fromEntries(devices.map(d => [d.mac, d]));
    for (const [m, d] of Object.entries(nd)) {
      if (!od[m]) msgs.push(`Add ${d.name} (${d.vlan}, ${d.profile})`);
      else if (JSON.stringify(od[m]) !== JSON.stringify(d)) msgs.push(`Edit ${d.name}: ${["name", "vlan", "ip", "profile", "media", "mediaServers", "mediaServer", "notes"].filter(k => JSON.stringify(od[m][k]) !== JSON.stringify(d[k])).join(", ")}`);
    }
    for (const [m, d] of Object.entries(od)) if (!nd[m]) msgs.push(`Remove ${d.name}`);
    const on = Object.fromEntries(networks0.map(n => [n.name, n])), nn = Object.fromEntries(networks.map(n => [n.name, n]));
    for (const [k, n] of Object.entries(nn)) {
      if (!on[k]) msgs.push(`Add network ${k} (VLAN ${n.vlan}, ${n.role})`);
      else if (JSON.stringify(on[k]) !== JSON.stringify(n)) msgs.push(`Edit network ${k}: ${["label", "vlan", "prefix", "ipv6", "role", "color", "access", "defaultProfile"].filter(f => JSON.stringify(on[k][f]) !== JSON.stringify(n[f])).join(", ")}`);
    }
    for (const k of Object.keys(on)) if (!nn[k]) msgs.push(`Remove network ${k}`);
    if (JSON.stringify(qos) !== JSON.stringify(qos0)) msgs.push("Change qos settings");
    if (JSON.stringify(upnp) !== JSON.stringify(upnp0)) msgs.push("Change upnp settings");
    for (const n of Object.keys({ ...drivers0, ...drivers })) {
      const a = JSON.stringify(drivers0[n] || null), b = JSON.stringify(drivers[n] || null);
      if (a === b) continue;
      const was = (drivers0[n] || {}).enabled, now = (drivers[n] || {}).enabled;
      msgs.push(was !== now && now !== undefined ? `${now ? "Enable" : "Disable"} driver ${n}` : `Change driver ${n} settings`);
    }
    const oh = Object.fromEntries(hardware0.map(d => [d.id, d])), nh = Object.fromEntries(hardware.map(d => [d.id, d]));
    for (const [k, d] of Object.entries(nh)) {
      if (!oh[k]) msgs.push(`Add ${d.kind} ${d.label}`);
      else if (JSON.stringify(oh[k]) !== JSON.stringify(d)) msgs.push(`Edit ${d.kind} ${d.label}`);
    }
    for (const [k, d] of Object.entries(oh)) if (!nh[k]) msgs.push(`Remove ${d.kind} ${d.label}`);
    const p0 = Object.fromEntries(profiles0.map(x => [x.name, x])), p1 = Object.fromEntries(profiles.map(x => [x.name, x]));
    for (const k of Object.keys(p1)) { if (!p0[k]) msgs.push(`Add filtering profile ${k}`); else if (JSON.stringify(p0[k]) !== JSON.stringify(p1[k])) msgs.push(`Edit filtering profile ${k}`); }
    for (const k of Object.keys(p0)) if (!p1[k]) msgs.push(`Remove filtering profile ${k}`);
    const pr0 = Object.fromEntries(printing0.printers.map(x => [x.name, x])), pr1 = Object.fromEntries(printing.printers.map(x => [x.name, x]));
    for (const k of Object.keys(pr1)) { if (!pr0[k]) msgs.push(`Add printer ${k}`); else if (JSON.stringify(pr0[k]) !== JSON.stringify(pr1[k])) msgs.push(`Edit printer ${k}`); }
    for (const k of Object.keys(pr0)) if (!pr1[k]) msgs.push(`Remove printer ${k}`);
    if (JSON.stringify(printing0.drivers) !== JSON.stringify(printing.drivers)) msgs.push("Printer drivers: " + printing.drivers.join(", "));
    for (const [id, m] of Object.entries(SCAT.services)) { const a = svcOn(id, svcOver0), b = svcOn(id);
      if (a !== b) msgs.push(`${b ? "Turn on" : "Turn off"} ${m.label}`); else if ((svcOver0[id] ?? null) !== (svcOver[id] ?? null)) msgs.push(svcOver[id] === undefined ? `${m.label}: follow site.nix` : `${m.label}: pin ${b ? "on" : "off"}`); }
    if (JSON.stringify(lwState0) !== JSON.stringify(lwState)) msgs.push(Object.keys(lwState).length ? "Change AI log triage settings" : "AI log triage: back to defaults");
    if (JSON.stringify(maint0) !== JSON.stringify(maint)) msgs.push(maint.rebootWindow.enabled ? `Reboot window: ${maint.rebootWindow.days.join(", ")} ${maint.rebootWindow.time}` : "Reboot window off");
    if (restrictUnnamed !== restrictUnnamed0) msgs.push(`Restrict unnamed devices: ${restrictUnnamed ? "on" : "off"}`);
    return msgs.concat(remoteChanges());
  }
  const tin = (rate, sent, avg, peak, drops, ecn) => ({ threshold_rate: rate, sent_bytes: sent, sent_packets: Math.round(sent / 900), drops, ecn_mark: ecn, ack_drops: 0, peak_delay_us: peak, avg_delay_us: avg, base_delay_us: 6, sparse_flows: 2, bulk_flows: 1, backlog_bytes: 0 });

  const phone = { user: "phone", password: "xK3vQ9mR2pLw7TnB5cYh8dJz" };
  function solctl(args, stdin) {
    const [cmd, ...rest] = args;
    const t = T();
    switch (cmd) {
      case "status": {
        const list = devicesOut();
        const counts = Object.fromEntries(networks.map(n => [n.name, 0]));
        list.filter(d => d.online).forEach(d => counts[d.lease_vlan]++);
        return { ok: true, hostname: "helios", domain: "home.arpa", reservedNames: ["helios", "heliosphere"],
          solarWind: { enabled: true, exposure: "proxy", url: "https://alerts.example.com", configured: true }, uptime: 86400 * 6 + 3600 * 4 + 1260, load: [0.42, 0.37, 0.31], memory: { total: 8.2e9, used: 3.1e9 },
          wan: { ifname: "wan", up: true, ipv4: "98.42.17.201/22", ipv6: "2600:6c50:7f:1a::1/128", gateway: "98.42.16.1" },
          uplink: { state: "up", since: outages[2].up_at, last_outage: outages[2], outages_24h: 1 },
          vlans: networks.map(n => ({ name: n.name, id: n.vlan, gateway: `${n.prefix}.1`, clients: counts[n.name] || 0 })),
          devices: { total: list.length, new: list.filter(d => !d.known).length, named: devices.length, blocked: devices.filter(d => d.blocked).length },
          services: [["kea-dhcp4-server", "DHCP"], ["adguardhome", "Magnetosphere (DNS filter)"], ["nftables", "Heliopause (firewall)"], ["helios-sqm", "QoS"], ["miniupnpd", "UPnP"], ["chronyd", "NTP"],
            ["tailscaled", "Tailscale (admin exit)"], ["container@heliosphere", "Heliosphere (family exit)"], ["ntfy-sh", "Notifications (ntfy)"], ["cups", "Printing"], ["llama-cpp", "Log AI"],
            ["logwatch.timer", "Log watcher"], ["helios-uplink.timer", "Outage monitor"], ["helios-backup.timer", "Config backup"]].map(([u, l]) => ({
            unit: u, label: l, active: "active", sub: u.endsWith(".timer") ? "waiting" : u === "nftables" || u === "helios-sqm" ? "exited" : "running", loaded: true })),
          qos, upnp: { ...upnp, mappings: 3 }, tailscale: { online: true, name: "helios", ips: ["100.101.12.7"], peers_online: 6, peers: 9 },
          pending: changes().length > 0, apply };
      }
      case "networks": return { ok: true, networks: networks.map(netView), roles: CAT.roles, access: CAT.access, wanVlan: 10 };
      case "network-set": {
        const d = JSON.parse(rest[0]), from = d.from || d.name, old = networks.find(n => n.name === from);
        if (!/^[a-z][a-z0-9]{0,11}$/.test(d.name)) return { ok: false, error: "network name: 1-12 lowercase letters/digits, starting with a letter (it becomes the interface name)" };
        const others = networks.filter(n => n !== old), vlan = Number(d.vlan);
        if (!(vlan >= 2 && vlan <= 4094) || vlan === 10) return { ok: false, error: "VLAN id must be 2-4094 and not 10 (the WAN VLAN)" };
        for (const n of others) {
          if (n.name === d.name) return { ok: false, error: `network '${d.name}' already exists` };
          if (n.vlan === vlan) return { ok: false, error: `VLAN ${vlan} is already used by ${n.name}` };
          if (n.prefix === d.prefix) return { ok: false, error: `subnet ${d.prefix}.0/24 is already used by ${n.name}` };
        }
        if (!/^10\.\d{1,3}\.\d{1,3}$|^192\.168\.\d{1,3}$|^172\.(1[6-9]|2\d|3[01])\.\d{1,3}$/.test(d.prefix || "")) return { ok: false, error: "subnet must be the first three octets of a private /24, e.g. 10.70.0" };
        const base = CAT.roles[d.role].access, acc = {};
        for (const [k, v] of Object.entries(d.access || {})) if (JSON.stringify(v) !== JSON.stringify(base[k])) acc[k] = v;
        const nn = { name: d.name, label: d.label || d.name, vlan, prefix: d.prefix, ipv6: d.ipv6 || null, role: d.role };
        const dp = "defaultProfile" in d ? d.defaultProfile : old && old.defaultProfile;
        if (dp) { if (!profiles.some(x => x.name === dp)) return { ok: false, error: `unknown profile '${dp}'` }; nn.defaultProfile = dp; }
        if (d.color) nn.color = d.color;
        if (Object.keys(acc).length) nn.access = acc;
        if (![...others, nn].some(n => access(n).admin)) return { ok: false, error: "at least one network must keep admin access, or you'd lock yourself out" };
        if (old) {
          devices.forEach(x => { if (x.vlan === from) { x.vlan = d.name; if (old.prefix !== d.prefix) x.ip = `${d.prefix}.${x.ip.split(".")[3]}`; } });
          networks[networks.indexOf(old)] = nn;
        } else networks.push(nn);
        return { ok: true, network: netView(nn), dirty: true };
      }
      case "network-remove": {
        const used = devices.filter(x => x.vlan === rest[0]).map(x => x.name);
        if (used.length) return { ok: false, error: `move or forget its devices first: ${used.slice(0, 5).join(", ")}` };
        const restN = networks.filter(n => n.name !== rest[0]);
        if (!restN.some(n => access(n).admin)) return { ok: false, error: "that's the last admin network — removing it would lock you out" };
        networks = restN; return { ok: true, dirty: true };
      }
      case "detect": detected.time = Math.floor(t); return new Promise(res => setTimeout(() => res({ ok: true, ...detected }), 900));
      case "hardware": {
        const dr = Object.entries(DRV.drivers).map(([name, c]) => ({ name, label: c.label, desc: c.desc, enabled: !!c.always || drvOn(name), always: !!c.always, builtin: !!c.builtin,
          capabilities: c.capabilities, kinds: c.kinds, settings: name === "nut" ? (({ enabled, ...r }) => ({ ...NUT0, ...r }))(drivers.nut || {}) : {} }));
        const ups = {}, checklists = {};
        for (const d of hardware) {
          if (d.kind === "ups") ups[d.id] = drvOn("nut") ? { status: "OL", charge: 100, runtime: 1260, load: 18, model: "Back-UPS ES 700G", raw: { "ups.status": "OL" } }
            : { status: null, charge: null, runtime: null, load: null, model: null, raw: {} };
          if (d.driver === "manual") checklists[d.id] = checklist(d);
        }
        return { ok: true, catalog: { upsVendors: DRV.upsVendors, nutDefaults: NUT0 }, drivers: dr, devices: hardware, detected, suggestions: suggestions(), ups, checklists,
          security: { time: Math.floor(t - 86400 * 3), total: 7, critical: 0, high: 1, medium: 4, low: 2, top: [
            { name: "openssh", version: "9.7p1", cve: "CVE-2024-6387", score: 8.1 }, { name: "curl", version: "8.6.0", cve: "CVE-2024-2398", score: 5.3 },
            { name: "glibc", version: "2.39", cve: "CVE-2024-2961", score: 5.5 }, { name: "libxml2", version: "2.12.5", cve: "CVE-2024-25062", score: 5.5 },
            { name: "sqlite", version: "3.45.1", cve: "CVE-2024-0232", score: 4.7 }] }, dirty: changes().length > 0 };
      }
      case "driver-set": {
        const d = JSON.parse(rest[0]), { name, ...set } = d;
        if (!DRV.drivers[name]) return { ok: false, error: `unknown driver '${name}'` };
        if (typeof set.enabled !== "boolean") return { ok: false, error: "enabled must be true or false" };
        if (DRV.drivers[name].always && !set.enabled) return { ok: false, error: `the ${name} driver is always on` };
        if (name === "nut") {
          const sh = set.shutdown || NUT0.shutdown, sv = set.server || NUT0.server;
          if (!Number.isInteger(sh.charge) || sh.charge < 1 || sh.charge > 100) return { ok: false, error: "shutdown.charge must be 1-100" };
          if (!Number.isInteger(sh.runtime) || sh.runtime < 0) return { ok: false, error: "shutdown.runtime must be 0 or more seconds" };
          if (sv.enabled && !sv.networks.length) return { ok: false, error: "choose at least one network to share the UPS with" };
          if (!sv.networks.every(n => networks.some(x => x.name === n))) return { ok: false, error: "unknown network in server.networks" };
          set.shutdown = sh; set.server = sv;
        }
        drivers[name] = set;
        return { ok: true, driver: { name, ...set }, dirty: true };
      }
      case "hardware-set": {
        const d = JSON.parse(rest[0]), from = d.from || d.id; delete d.from;
        const others = hardware.filter(x => x.id !== from), err = validHw(d, others);
        if (err) return { ok: false, error: err };
        const i = hardware.findIndex(x => x.id === from);
        if (i >= 0) hardware[i] = d; else hardware.push(d);
        if (d.kind === "ups" && !drvOn("nut")) drivers.nut = { ...(drivers.nut || {}), enabled: true, shutdown: NUT0.shutdown, server: NUT0.server };
        return { ok: true, device: d, dirty: true };
      }
      case "hardware-remove": {
        const i = hardware.findIndex(x => x.id === rest[0]);
        if (i < 0) return { ok: false, error: `no device '${rest[0]}'` };
        hardware.splice(i, 1); return { ok: true, dirty: true };
      }
      case "remote": {
        const i = remote.ingress, t = T();
        return { ok: true, enabled: heliosphereOn && svcOn("remote", svcOver0), publicHost: "vpn.example.net", settings: JSON.parse(JSON.stringify(remote)), dirty: changes().length > 0,
          status: { tailscale: i.tailscale.enabled ? { online: true, ip: "100.101.12.8" } : null,
            wireguard: i.wireguard.enabled ? { publicKey: serverKey, peers: i.wireguard.peers.map((p, k) => ({ name: p.name, ip: p.ip, lastHandshake: k ? null : Math.floor(t - 95), rx: k ? null : 41e6, tx: k ? null : 3.2e6 })) } : null,
            openvpn: i.openvpn.enabled ? { clients: i.openvpn.clients.map(c => ({ name: c.name, connected: false })) } : null,
            proxy: i.proxy.enabled ? { running: true } : null,
            egress: { mode: remote0.egress.mode, configured: remote0.egress.mode === "direct" || (remote0.egress.mode === "proxy" ? true : egressFiles[remote0.egress.mode]), up: true, publicIp: "98.42.17.201" } } };
      }
      case "remote-set": {
        const d = JSON.parse(rest[0]), next = JSON.parse(JSON.stringify(remote));
        for (const k of ["tailscale", "wireguard", "openvpn", "proxy"]) for (const [f, v] of Object.entries((d.ingress || {})[k] || {})) if (!["peers", "clients", "users"].includes(f)) next.ingress[k][f] = v;
        if (d.egress) { const { proxy, ...e } = d.egress; Object.assign(next.egress, e); if (proxy) next.egress.proxy = proxy; }
        const err = validRemote(next); if (err) return { ok: false, error: err };
        remote = next; return { ok: true, dirty: true };
      }
      case "remote-wg-add": {
        const name = rest[0], wg = remote.ingress.wireguard;
        if (!nameOk(name)) return { ok: false, error: "name must be lowercase letters, digits or dashes (max 32)" };
        if (wg.peers.some(p => p.name === name)) return { ok: false, error: `peer '${name}' already exists` };
        let n = 2; while (wg.peers.some(p => p.ip === `10.61.0.${n}`)) n++;
        if (n > 254) return { ok: false, error: "no free addresses left" };
        const ip = `10.61.0.${n}`;
        wg.peers.push({ name, publicKey: rnd(43) + "=", ip });
        const config = `[Interface]\nPrivateKey = ${rnd(43)}=\nAddress = ${ip}/32\nDNS = 10.60.0.1\n\n[Peer]\nPublicKey = ${serverKey}\nEndpoint = vpn.example.net:${wg.port}\nAllowedIPs = 0.0.0.0/0, ::/0\nPersistentKeepalive = 25\n`.replace(/\\n/g, "\n");
        return { ok: true, config, qrSvg: fakeQr(config), dirty: true };
      }
      case "remote-wg-renew": {
        const wg = remote.ingress.wireguard, p_ = wg.peers.find(x => x.name === rest[0]);
        if (!p_) return { ok: false, error: `no WireGuard peer '${rest[0]}'` };
        p_.publicKey = rnd(43) + "=";
        const allowed = wg.tunnel === "split" ? [...wg.lan.map(n => (networks.find(x => x.name === n) || {}).prefix + ".0/24"), "10.60.0.1/32"].join(", ") : "0.0.0.0/0";
        const config = `[Interface]\nPrivateKey = ${rnd(43)}=\nAddress = ${p_.ip}/32\nDNS = 10.60.0.1\n${wg.mtu ? `MTU = ${wg.mtu}\n` : ""}\n[Peer]\nPublicKey = ${serverKey}\nEndpoint = vpn.example.net:${wg.port}\nAllowedIPs = ${allowed}\n${wg.keepalive ? `PersistentKeepalive = ${wg.keepalive}\n` : ""}`;
        return { ok: true, name: p_.name, ip: p_.ip, config, qrSvg: fakeQr(config), dirty: true };
      }
      case "remote-wg-rotate": case "remote-wg-import": {
        if (cmd === "remote-wg-import" && !/^[A-Za-z0-9+/]{43}=$/.test((stdin || "").trim())) return { ok: false, error: "that isn't a WireGuard private key (44 base64 characters)" };
        const prev = serverKey; serverKey = rnd(43) + "=";
        return { ok: true, publicKey: serverKey, previousPublicKey: prev, restarted: true };
      }
      case "remote-wg-remove": {
        const peers = remote.ingress.wireguard.peers, i = peers.findIndex(p => p.name === rest[0]);
        if (i < 0) return { ok: false, error: `no peer '${rest[0]}'` };
        peers.splice(i, 1); return { ok: true, dirty: true };
      }
      case "remote-ovpn-add": {
        const name = rest[0], ov = remote.ingress.openvpn;
        if (!nameOk(name)) return { ok: false, error: "name must be lowercase letters, digits or dashes (max 32)" };
        if (ov.clients.some(c => c.name === name)) return { ok: false, error: `client '${name}' already exists` };
        const serial = Array.from({ length: 16 }, () => "0123456789ABCDEF"[Math.floor(Math.random() * 16)]).join("");
        ov.clients.push({ name, serial, revoked: false });
        const pem = (t, n) => `<${t}>\n-----BEGIN MOCK ${t.toUpperCase()}-----\n${rnd(n)}\n-----END MOCK ${t.toUpperCase()}-----\n</${t}>\n`;
        const config = (`client\ndev tun\nproto ${ov.proto}\nremote vpn.example.net ${ov.port}\nnobind\npersist-key\nremote-cert-tls server\ncipher AES-256-GCM\nverb 3\n` + pem("ca", 60) + pem("cert", 60) + pem("key", 60) + pem("tls-crypt", 60)).replace(/\\n/g, "\n");
        return { ok: true, config, dirty: true };
      }
      case "remote-ovpn-revoke": {
        const c = remote.ingress.openvpn.clients.find(x => x.name === rest[0]);
        if (!c) return { ok: false, error: `no client '${rest[0]}'` };
        c.revoked = true; return { ok: true, dirty: true };
      }
      case "remote-proxy-user": {
        const name = rest[0], pw = (stdin || "").replace(/[\r\n]+$/, ""), users = remote.ingress.proxy.users;
        if (!nameOk(name)) return { ok: false, error: "name must be lowercase letters, digits or dashes (max 32)" };
        if (pw.length < 16) return { ok: false, error: "the password must be at least 16 characters" };
        if (!users.includes(name)) users.push(name);
        return { ok: true, dirty: true };
      }
      case "remote-proxy-user-remove": {
        const px = remote.ingress.proxy, i = px.users.indexOf(rest[0]);
        if (i < 0) return { ok: false, error: `no user '${rest[0]}'` };
        if (px.enabled && px.users.length === 1) return { ok: false, error: "the proxy is on and needs at least one user; turn it off first" };
        px.users.splice(i, 1); return { ok: true, dirty: true };
      }
      case "remote-egress-upload": {
        const kind = rest[0], text = stdin || "";
        if (!(kind in egressFiles)) return { ok: false, error: "kind must be wireguard, openvpn or auth" };
        if (kind === "wireguard" && !(/\[Interface\]/.test(text) && /\[Peer\]/.test(text))) return { ok: false, error: "that doesn't look like a wg-quick config (needs [Interface] and [Peer])" };
        if (kind === "openvpn" && !/^\s*remote\s+\S+/m.test(text)) return { ok: false, error: "that doesn't look like an .ovpn file (no 'remote' line)" };
        if (kind === "auth" && !text.trim()) return { ok: false, error: "empty login" };
        const hooks = text.split("\n").filter(l => /^\s*(DNS|PostUp|PreUp|PostDown|PreDown|up|down|script-security)\b/i.test(l)).length;
        egressFiles[kind] = true; return { ok: true, kind, removedLines: hooks };
      }
      case "remote-egress-clear": {
        const kind = rest[0];
        if (!(kind in egressFiles)) return { ok: false, error: "kind must be wireguard, openvpn or auth" };
        if (kind === remote.egress.mode || kind === remote0.egress.mode) return { ok: false, error: `the way out is set to ${kind}; switch to Direct first` };
        egressFiles[kind] = false; return { ok: true };
      }
      case "restrict-unnamed": restrictUnnamed = rest[0] === "on"; return { ok: true, dirty: true };
      case "devices": {
        const qn = networks.filter(n => access(n).quarantine).map(n => n.name), armed = restrictUnnamed && devices.some(d => access(networks.find(n => n.name === d.vlan) || networks[0]).admin);
        return { ok: true, devices: devicesOut().map(d => ({ ...d, restricted: armed && !d.known && qn.includes(d.lease_vlan || d.vlan) })), dirty: changes().length > 0,
          restrictUnnamed: { enabled: restrictUnnamed, armed, waitingForAdmin: restrictUnnamed && !armed, networks: qn }, profiles: profiles.map(({ name, label, desc }) => ({ name, label, desc })) };
      }
      case "profiles": return { ok: true, settings: PCAT.settings, dirty: changes().length > 0,
        adguard: { reachable: true, configured: true, version: "v0.107.63", protection: true, services: AG_SERVICES },
        profiles: profiles.map(p_ => ({ ...p_, devices: devices.filter(d => (d.profile || "default") === p_.name).map(d => d.name), networks: networks.filter(n => n.defaultProfile === p_.name).map(n => n.name) })) };
      case "profile-set": {
        const d = JSON.parse(rest[0]), from = d.from || d.name, old = d.from ? profiles.find(x => x.name === d.from) : undefined;
        if (!/^[a-z][a-z0-9-]{0,31}$/.test(d.name || "")) return { ok: false, error: "profile id: lowercase letters, digits and dashes, starting with a letter (max 32)" };
        if (profiles.some(x => x.name === d.name && x !== old)) return { ok: false, error: `there is already a profile '${d.name}'` };
        const np = { name: d.name, label: (d.label || d.name).trim(), desc: (d.desc || "").trim() };
        for (const k of ["global", "filtering", "safeBrowsing", "parental", "safeSearch", "youtubeRestricted"]) np[k] = !!d[k];
        np.blockedServices = [...new Set(d.blockedServices || [])].sort();
        if (old) { profiles[profiles.indexOf(old)] = np; if (from !== d.name) { devices.forEach(x => { if (x.profile === from) x.profile = d.name; }); networks.forEach(n => { if (n.defaultProfile === from) n.defaultProfile = d.name; }); } }
        else profiles.push(np);
        return { ok: true, profile: np, dirty: true };
      }
      case "profile-remove": {
        const users = devices.filter(d => (d.profile || "default") === rest[0]).map(d => d.name).concat(networks.filter(n => n.defaultProfile === rest[0]).map(n => `network ${n.name}`));
        if (users.length) return { ok: false, error: `still in use by ${users.slice(0, 5).join(", ")}: pick another profile for them first` };
        if (profiles.length === 1) return { ok: false, error: "keep at least one profile" };
        profiles = profiles.filter(x => x.name !== rest[0]); return { ok: true, dirty: true };
      }
      case "adguard-sync": return { ok: true, message: `Magnetosphere (AdGuard): 0 added, ${devices.length + networks.filter(n => n.defaultProfile).length} updated, 0 removed` };
      case "device-set": {
        const d = JSON.parse(rest[0]);
        if (!/^[a-z0-9][a-z0-9-]{0,31}$/.test(d.name)) return { ok: false, error: "name must be lowercase letters, digits or dashes (max 32)" };
        let ex = devices.find(x => x.mac === d.mac);
        const net = { trusted: "10.20.0", family: "10.30.0", iot: "10.40.0", strict: "10.50.0", mgmt: "10.99.0" }[d.vlan];
        let ip = ex && ex.vlan === d.vlan ? ex.ip : null;
        for (let i = 20; !ip && i < 100; i++) if (!devices.find(x => x.ip === `${net}.${i}`)) ip = `${net}.${i}`;
        const nd = { mac: d.mac, name: d.name, vlan: d.vlan, ip, profile: d.profile, blocked: ex ? ex.blocked : false, media: !!d.media && !access(networks.find(n => n.name === d.vlan)).admin, notes: d.notes || "" };
        if (nd.media && Array.isArray(d.mediaServers)) { if (!d.mediaServers.length) return { ok: false, error: "pick at least one media server, or allow all of them" }; nd.mediaServers = [...d.mediaServers]; }
        else if (ex) delete ex.mediaServers;
        if (d.mediaServer && !d.mediaServer.tcp.length && !d.mediaServer.udp.length) return { ok: false, error: "a media server needs at least one port" };
        if (d.mediaServer) nd.mediaServer = d.mediaServer; else if (ex) delete ex.mediaServer;
        if (ex) Object.assign(ex, nd); else devices.push(nd);
        return { ok: true, device: nd, dirty: true };
      }
      case "device-remove": { const i = devices.findIndex(x => x.mac === rest[0]); devices.splice(i, 1); return { ok: true }; }
      case "block": { const d = devices.find(x => x.mac === rest[0]); d.blocked = rest[1] === "on"; const c = committed.find(x => x.mac === d.mac); if (c) c.blocked = d.blocked; return { ok: true, blocked: d.blocked, live: true, dirty: false }; }
      case "settings-set": {
        for (let i = 0; i < rest.length; i += 2) {
          const [k, v] = [rest[i], rest[i + 1]];
          if (k === "--qos") qos = { ...qos, enabled: v === "on" };
          if (k === "--down") qos = { ...qos, down: v };
          if (k === "--up") qos = { ...qos, up: v };
          if (k === "--upnp") upnp = { enabled: v === "on" };
        }
        return { ok: true };
      }
      case "pending": { const c = changes(); return { ok: true, dirty: c.length > 0, changes: c, apply }; }
      case "discard": restrictUnnamed = restrictUnnamed0; maint = JSON.parse(JSON.stringify(maint0)); lwState = JSON.parse(JSON.stringify(lwState0)); svcOver = { ...svcOver0 }; printing = JSON.parse(JSON.stringify(printing0)); devices.length = 0; committed.forEach(d => devices.push({ ...d })); profiles = JSON.parse(JSON.stringify(profiles0)); networks = JSON.parse(JSON.stringify(networks0)); qos = { ...qos0 }; upnp = { ...upnp0 }; drivers = JSON.parse(JSON.stringify(drivers0)); hardware = JSON.parse(JSON.stringify(hardware0)); remote = JSON.parse(JSON.stringify(remote0)); return { ok: true };
      case "apply-start": apply = { state: "running", started: t, changes: changes() }; setTimeout(() => { apply = { ...apply, state: "confirm", deadline: T() + 120 }; }, 2500); return { ok: true };
      case "apply-status": return { ok: true, apply: { ...apply, remaining: apply.deadline ? Math.max(0, apply.deadline - T()) : undefined }, log: [
        "[10:41:02] nixos-rebuild switch …", "building the system configuration...", "these 4 derivations will be built:",
        "  /nix/store/9xk2…-kea-dhcp4.conf.drv", "  /nix/store/1mfa…-nftables-rules.drv", "  /nix/store/q8c1…-hosts.drv", "  /nix/store/vz0d…-nixos-system-helios-25.11.drv",
        "activating the configuration...", "setting up /etc...", "reloading the following units: nftables.service", "restarting the following units: kea-dhcp4-server.service",
        ...(apply.state === "confirm" ? ["[10:41:39] Syncing AdGuard client profiles …", "[10:41:39] Magnetosphere (AdGuard): 1 added, 15 updated, 0 removed", "[10:41:39] Applied. Waiting for confirmation (auto-rollback in 120s)."] : [])] };
      case "confirm": restrictUnnamed0 = restrictUnnamed; maint0 = JSON.parse(JSON.stringify(maint)); lwState0 = JSON.parse(JSON.stringify(lwState)); svcOver0 = { ...svcOver }; printing0 = JSON.parse(JSON.stringify(printing)); committed = JSON.parse(JSON.stringify(devices)); profiles0 = JSON.parse(JSON.stringify(profiles)); networks0 = JSON.parse(JSON.stringify(networks)); qos0 = { ...qos }; upnp0 = { ...upnp }; drivers0 = JSON.parse(JSON.stringify(drivers)); hardware0 = JSON.parse(JSON.stringify(hardware)); remote0 = JSON.parse(JSON.stringify(remote)); apply = { state: "done" }; return { ok: true };
      case "rollback": apply = { state: "rolledback", message: "Rolled back" }; return { ok: true };
      case "apply-dismiss": apply = {}; return { ok: true };
      case "counters": {
        const dt = t - lastT; lastT = t;
        for (const i of ifs) { const [rx, tx] = rates(i, t); const lan = i !== "wan" && i !== "trunk0"; ctr[i].rx += (lan ? tx : rx) / 8 * dt; ctr[i].tx += (lan ? rx : tx) / 8 * dt; }
        return { ok: true, t, ifaces: JSON.parse(JSON.stringify(ctr)) };
      }
      case "vnstat": {
        const d = new Date(), mk = (name, scale) => ({ name, total: {}, days: [...Array(14)].map((_, i) => {
          const x = new Date(d); x.setDate(d.getDate() - (13 - i));
          const wk = [0, 6].includes(x.getDay()) ? 1.45 : 1;
          return { date: { year: x.getFullYear(), month: x.getMonth() + 1, day: x.getDate() }, rx: (38 + (i * 37 % 23)) * 1e9 * wk * scale[0], tx: (3.2 + (i * 13 % 5) * 0.6) * 1e9 * scale[1] };
        }), months: [...Array(11)].map((_, i) => ({ date: { year: 2026, month: i + 1 }, rx: (820 + i * 41 + (i * 97 % 120)) * 1e9 * scale[0], tx: (68 + i * 4) * 1e9 * scale[1] })) });
        return { ok: true, interfaces: [mk("wan", [1, 1]), mk("family", [0.04, 9]), mk("iot", [0.02, 4.1]), mk("trusted", [0.08, 2.2])] };
      }
      case "logwatch": {
        const LWC = (() => { const x = new XMLHttpRequest(); x.open("GET", "../logwatch-models.json", false); x.send(); return JSON.parse(x.responseText); })();
        const hw = { memoryMB: 15872, cpus: 4, gpu: true }, budget = (hw.memoryMB - 2048) / 2;
        if (lwDl && lwDl.state === "running") { lwDl.done = Math.min(lwDl.bytes, lwDl.done + lwDl.bytes / 6); if (lwDl.done >= lwDl.bytes) { lwDl.state = "done"; lwFiles[lwDl.file] = lwDl.bytes; } }
        const st = JSON.parse(JSON.stringify({ ...LWC.defaults, ...lwState, guard: { ...LWC.defaults.guard, ...(lwState.guard || {}) } }));
        return { ok: true, enabled: svcOn("logwatch", svcOver0), settings: st, site: { backend: "vulkan", model: "/var/lib/llm/model.gguf", remoteUrl: null }, hardware: hw, recommended: "medium",
          presets: Object.entries(LWC.presets).map(([id, v]) => ({ id, ...v, downloaded: lwFiles[v.file] === v.bytes, fits: v.ramMB <= budget })),
          files: lwFiles, download: lwDl, paused: lwPaused, pressure: { cpu: 3.2, memory: 0.4 },
          units: { "logwatch.timer": lwPaused ? "inactive" : "active", "llama-cpp.service": "inactive", "helios-ai-guard.timer": "active" }, dirty: changes().length > 0 };
      }
      case "logwatch-set": { const d = JSON.parse(rest[0]);
        if (d.model && !["tiny", "small", "medium", "large", "xl", "site", "custom"].includes(d.model)) return { ok: false, error: "model must be a preset, custom or site" };
        if (d.model === "custom" && !d.customPath) return { ok: false, error: "a custom model needs customPath" };
        if (d.interval !== undefined && (d.interval < 5 || d.interval > 240)) return { ok: false, error: "interval must be a whole number from 5 to 240" };
        lwState = { ...lwState, ...d, guard: { ...(lwState.guard || {}), ...(d.guard || {}) } }; return { ok: true, dirty: true }; }
      case "logwatch-reset": lwState = {}; return { ok: true, dirty: true };
      case "logwatch-model-get": { const x = new XMLHttpRequest(); x.open("GET", "../logwatch-models.json", false); x.send(); const pr = JSON.parse(x.responseText).presets[rest[0]];
        lwDl = { preset: rest[0], file: pr.file, bytes: pr.bytes, done: 0, state: "running" }; return { ok: true, started: rest[0] }; }
      case "logwatch-model-remove": delete lwFiles[rest[0]]; return { ok: true };
      case "logwatch-resume": lwPaused = null; return { ok: true };
      case "reboot-status": {
        const w = maint.rebootWindow, next = (() => { if (!w.enabled) return null; const [hh, mm] = w.time.split(":").map(Number), d = new Date();
          for (let i = 0; i < 8; i++) { const x = new Date(d); x.setDate(d.getDate() + i); x.setHours(hh, mm, 0, 0); if (w.days.includes(["sun", "mon", "tue", "wed", "thu", "fri", "sat"][x.getDay()]) && x > d) return Math.floor(x / 1000); } return null; })();
        return { ok: true, needed: true, reasons: ["kernel", "kernel modules", "boot image"], running: "6.12.93", installed: "6.12.95", maintenance: JSON.parse(JSON.stringify(maint)), nextWindow: next };
      }
      case "maintenance-set": { const d = JSON.parse(rest[0]).rebootWindow || {};
        if (d.days && !d.days.length) return { ok: false, error: "days: one or more of mon, tue, wed, thu, fri, sat, sun" };
        if (d.time && !/^([01]\d|2[0-3]):[0-5]\d$/.test(d.time)) return { ok: false, error: "time must look like 04:00" };
        maint = { rebootWindow: { ...maint.rebootWindow, ...d } }; return { ok: true, dirty: true }; }
      case "reboot": return { ok: true };
      case "services": {
        const active = id => svcOn(id, svcOver0) ? "active" : "inactive";
        return { ok: true, dirty: changes().length > 0,
          core: SCAT.core.map(c => ({ ...c, units: Object.fromEntries(c.units.map(u => [u, "active"])) })),
          services: Object.entries(SCAT.services).map(([id, m]) => ({ id, ...m, default: SVC_DEFAULTS[id], override: svcOver[id] ?? null, enabled: svcOn(id),
            units: Object.fromEntries(m.units.map(u => [u, active(id)])) })), elsewhere: SCAT.elsewhere };
      }
      case "service-set": {
        if (!SCAT.services[rest[0]]) return { ok: false, error: `unknown service '${rest[0]}' (core services can't be switched off)` };
        if (rest[1] === "default") delete svcOver[rest[0]]; else svcOver[rest[0]] = rest[1] === "on";
        return { ok: true, dirty: true };
      }
      case "printing": {
        const used = new Set(printing.printers.map(x => x.deviceUri));
        if (!svcOn("printing", svcOver0)) return { ok: true, enabled: false, running: false, printers: [], jobs: [], discovered: [], drivers: [], siteDrivers: [], networks: [] };
        return { ok: true, enabled: true, running: true, dirty: changes().length > 0,
          printers: printing.printers.map(x => ({ ...x, live: printing0.printers.some(y => y.name === x.name) ? { state: printLive[x.name] || "idle" } : null, jobs: printJobs.filter(j => j.printer === x.name) })),
          jobs: printJobs, discovered: PRN_FOUND.filter(d => !used.has(d.uri)), siteDrivers: ["canon-cups-ufr2"],
          drivers: Object.entries(PRN_CAT).map(([id, v]) => ({ id, ...v, enabled: printing.drivers.includes(id) })),
          networks: networks.filter(n => access(n).printing).map(n => n.name) };
      }
      case "printer-models": { const q = (rest[0] || "").toLowerCase();
        return { ok: true, models: [{ model: "everywhere", desc: "Driverless (IPP Everywhere / AirPrint) — best for most printers made since ~2015" },
          ...PRN_MODELS.filter(([m, d]) => !q || d.toLowerCase().includes(q) || m.includes(q)).map(([model, desc]) => ({ model, desc }))] }; }
      case "printer-set": {
        const d = JSON.parse(rest[0]);
        if (!/^[A-Za-z0-9_-]{1,64}$/.test(d.name || "")) return { ok: false, error: "printer name: letters, digits, - and _ (max 64), no spaces" };
        if (!/^(usb|ipp|ipps|dnssd|socket|lpd|http|https|hp|smb):\/\/\S+$/.test(d.deviceUri || "")) return { ok: false, error: "connection must be a printer address like usb://…, ipp://…, dnssd://… or socket://…" };
        if (!d.model) return { ok: false, error: "pick a driver (or “everywhere” for driverless)" };
        const old = d.from ? printing.printers.find(x => x.name === d.from) : null;
        if (printing.printers.some(x => x.name === d.name && x !== old)) return { ok: false, error: `there is already a printer '${d.name}'` };
        const np = { name: d.name, description: d.description || d.name, location: d.location || "", deviceUri: d.deviceUri, model: d.model, ...(d.default ? { default: true } : {}) };
        if (np.default) printing.printers.forEach(x => delete x.default);
        if (old) printing.printers[printing.printers.indexOf(old)] = np; else printing.printers.push(np);
        return { ok: true, printer: np, dirty: true };
      }
      case "printer-remove": printing.printers = printing.printers.filter(x => x.name !== rest[0]); return { ok: true, dirty: true };
      case "printing-drivers": printing.drivers = JSON.parse(rest[0]).sort(); return { ok: true, drivers: printing.drivers, dirty: true };
      case "printer-test": printJobs.push({ id: `${rest[0]}-${40 + printJobs.length}`, printer: rest[0], user: "root", size: 20480, submitted: "just now" }); return { ok: true, job: printJobs.at(-1).id };
      case "printer-pause": printLive[rest[0]] = rest[1] === "on" ? "paused" : "idle"; return { ok: true };
      case "print-cancel": printJobs = printJobs.filter(j => j.id !== rest[0]); if (!printJobs.some(j => j.printer === "den-inkjet")) printLive["den-inkjet"] = printLive["den-inkjet"] === "paused" ? "paused" : "idle"; return { ok: true };
      case "qos-set": {
        const d = JSON.parse(rest[0]), q = { ...qos, ...d };
        const ch = { algorithm: ["cake", "fq_codel"], link: ["docsis", "ethernet", "vdsl", "adsl", "custom"], fairness: ["device", "triple", "flow"], priority: ["diffserv4", "diffserv3", "besteffort"], rtt: ["internet", "regional", "metro"] };
        for (const [k, v] of Object.entries(ch)) if (!v.includes(q[k])) return { ok: false, error: `${k} must be one of ${v.join(", ")}` };
        for (const k of ["down", "up"]) if (q[k] !== null && !/^\d+(\.\d+)?(kbit|mbit|gbit)$/.test(q[k])) return { ok: false, error: `${k} rate must look like 500mbit or 50mbit` };
        if (q.enabled && (!q.down || !q.up)) return { ok: false, error: "set both rates before turning QoS on (Suggest settings can measure them)" };
        qos = q; return { ok: true, settings: qos, dirty: true };
      }
      case "qos-suggest": {
        const measured = rest.includes("--measure") ? { down: 938.4, up: 38.1, idleMs: 14.2, loadedMs: 96.5 } : null;
        const sug = { link: "docsis", fairness: "device", algorithm: "cake" }, why = {
          link: "A cable modem answered at 192.168.100.1, so this looks like a cable (DOCSIS) line.",
          fairness: "Share the line evenly between home devices first, then between each device's connections.",
          algorithm: "CAKE handles overhead, fairness and priorities in one place." };
        if (measured) { Object.assign(sug, { down: "844mbit", up: "36mbit", ackFilter: true });
          Object.assign(why, { down: "90% of the measured 938.4 Mb/s, so the queue builds here, not at the ISP.", up: "95% of the measured 38.1 Mb/s.",
            ackFilter: "Download is 25× the upload: filtering redundant ACKs frees upload for everything else." }); }
        return new Promise(res => setTimeout(() => res({ ok: true, suggestions: sug, reasons: why, measured, bufferbloatGrade: measured ? "D" : null,
          detected: { cableModem: true, trunkMbps: 2500 } }), measured ? 2500 : 400));
      }
      case "qos": return { ok: true, settings: qos,
        upload: qos.enabled ? { dev: "wan", bandwidth: 6250000, drops: 1203, tins: [tin(390625, 12.3e6, 180, 2101, 3, 0), tin(6250000, 9.01e9, 1350, 8400, 1190, 12), tin(1562500, 98.7e6, 410, 3020, 10, 0)] } : null,
        download: qos.enabled ? { dev: "ifb4wan", bandwidth: 62500000, drops: 5021, tins: [tin(3906250, 81.2e6, 120, 1802, 2, 0), tin(62500000, 479.1e9, 940, 6120, 5011, 44), tin(15625000, 1.29e9, 300, 2440, 8, 0)] } : null };
      case "upnp": {
        const ipName = Object.fromEntries(devices.map(d => [d.ip, d.name]));
        const why = m => { const bd = (upnp.blockedDevices || []).find(n => ipName[m.ip] === n); if (bd) return `device ${bd} is blocked`;
          const bp = (upnp.blockedPorts || []).find(p_ => { const [lo, hi] = p_.split("-").map(Number); return m.ext_port >= lo && m.ext_port <= (hi || lo); }); return bp ? `port ${bp} is blocked` : null; };
        return { ok: true, enabled: upnp.enabled, blockedDevices: upnp.blockedDevices || [], blockedPorts: upnp.blockedPorts || [],
          networks: networks.filter(n => access(n).upnp).map(n => n.name),
          mappings: upnp.enabled ? upnpMaps.map(m => ({ ...m, device: ipName[m.ip] || null, blocked: why(m) })) : [] };
      }
      case "upnp-clear": { const n = upnpMaps.length; upnpMaps = []; return { ok: true, removed: n }; }
      case "upnp-close": { const before = upnpMaps.length; upnpMaps = upnpMaps.filter(m => !(m.proto === rest[0].toUpperCase() && m.ext_port === Number(rest[1])));
        return before === upnpMaps.length ? { ok: false, error: `no open ${rest[0].toUpperCase()} port ${rest[1]}` } : { ok: true, removed: before - upnpMaps.length }; }
      case "upnp-block": case "upnp-unblock": {
        const d = JSON.parse(rest[0]); if (!!d.device === !!d.port) return { ok: false, error: "block either a device or a port" };
        upnp = { blockedDevices: [], blockedPorts: [], ...upnp };
        const key = d.device ? "blockedDevices" : "blockedPorts", val = d.device || String(d.port).trim();
        const rng = d.port ? val.split("-").map(Number) : null;
        if (d.port && (!/^\d{1,5}(-\d{1,5})?$/.test(val) || rng[0] < 1 || (rng[1] ?? rng[0]) > 65535 || (rng[1] ?? rng[0]) < rng[0])) return { ok: false, error: "port must be a number or a range like 6000-6100 (1-65535)" };
        if (cmd === "upnp-unblock") { if (!upnp[key].includes(val)) return { ok: false, error: `${val} isn't blocked` }; upnp[key] = upnp[key].filter(x => x !== val); return { ok: true, dirty: true }; }
        if (!upnp[key].includes(val)) upnp[key] = [...upnp[key], val];
        const ip = d.device && (devices.find(x => x.name === d.device) || {}).ip, [lo, hi] = d.port ? val.split("-").map(Number) : [0, 0];
        const before = upnpMaps.length; upnpMaps = upnpMaps.filter(m => d.device ? m.ip !== ip : !(m.ext_port >= lo && m.ext_port <= (hi || lo)));
        return { ok: true, closed: before - upnpMaps.length, dirty: true };
      }
      case "history": return { ok: true, commits, generations: [43, 42, 41, 40, 39, 38].map((g, i) => ({ gen: g, date: new Date((now0 - i * 86400 * 0.9 - 3000) * 1000).toISOString().slice(0, 16).replace("T", " "), current: i === 0 })),
        backup: { time: now0 - 3600 * 20, changed: true, pushed: true }, outages: outages.slice().reverse() };
      case "notify-info": return { ok: true, enabled: true, exposure: "proxy", url: "https://alerts.example.com",
        topics: { flare: "flare", aurora: "aurora" }, user: phone.user, password: rest.includes("--reveal") ? phone.password : null, configured: true };
      case "notify-credentials": {
        const user = rest[rest.indexOf("--user") + 1] || "", pw = (stdin || "").replace(/[\r\n]+$/, "");
        if (!/^[a-z][a-z0-9_-]{2,31}$/.test(user)) return { ok: false, error: "Username must be 3-32 characters: lowercase letters, digits, - or _, starting with a letter" };
        if (user === "helios") return { ok: false, error: "The username “helios” is reserved for the router" };
        if (pw.length < 12 || pw.length > 128) return { ok: false, error: "Password must be 12 to 128 characters" };
        phone.user = user; phone.password = pw;
        return { ok: true, user, previous: "phone" };
      }
      case "notify-test": return { ok: true };
      case "notify-recent": return { ok: true, messages: [
        { kind: "aurora", time: t - 600, title: "Test from helios", message: "This is a test aurora notification from HeliOS.", priority: 3 },
        { kind: "flare", time: t - 3600 * 3, title: "helios: critical log events", message: "kernel: heliopause-trap: IN=iot SRC=10.40.0.104 DST=10.40.0.1 PROTO=TCP DPT=22", priority: 5 },
        { kind: "aurora", time: t - 3600 * 6 + 187, title: "Internet is back", message: "helios: internet was down for 3m 7s (from 16:11).", priority: 3 },
        { kind: "aurora", time: t - 3600 * 20, title: "helios: medium log warning", message: "kea-dhcp4-server reported repeated DHCPDECLINE messages from one IoT device — likely an IP conflict.", priority: 3 },
        { kind: "flare", time: t - 86400 * 2, title: "helios: critical log events", message: "r8152 2-1:1.0 trunk0: USB disconnect, device number 3", priority: 5 },
      ] };
      case "backup": return { ok: true, changed: false, time: t, pushed: true };
      case "account": return { ok: true, user: "admin", sshKeys: 1, passwordChanged: now0 - 86400 * 40, mutableUsers: true, totp: account.totp, totpEnforced: true, totpPending: !!account.pending };
      case "account-password": { let d = {}; try { d = JSON.parse(stdin || "{}"); } catch (e) { return { ok: false, error: "Expected JSON on stdin" }; }
        const pw = d.new || "";
        if (pw.length < 12 || pw.length > 128) return { ok: false, error: "Password must be 12 to 128 characters" };
        if (!d.current) return { ok: false, error: "The current password is wrong" };
        return { ok: true, user: "admin" }; }
      case "account-2fa-begin":
        if (account.totp && !/^(\d{6}|\d{8})$/.test((stdin || "").trim())) return { ok: false, error: "That code doesn't match. Enter the current code from the app, or a recovery code" };
        account.pending = "JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP";
        return { ok: true, user: "admin", secret: account.pending, uri: `otpauth://totp/HeliOS%20%28helios%29:admin?secret=${account.pending}`,
          qrSvg: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 21 21"><path d="M0 0h7v7H0zM14 0h7v7h-7zM0 14h7v7H0zM9 9h3v3H9z"/></svg>' };
      case "account-2fa-enable": if (!account.pending) return { ok: false, error: "The setup expired: start again" };
        if (!/^\d{6}$/.test((stdin || "").trim())) return { ok: false, error: "Enter the 6-digit code from the app" };
        account = { totp: true, pending: null }; return { ok: true, user: "admin", recoveryCodes: ["40912377", "18350264", "77201946", "50388412", "93016655"] };
      case "gate": {
        const enabled = svcOn("gate");
        return { ok: true, enabled, url: "https://helios.home.arpa:9090", fqdn: "helios.home.arpa", rescue: true, legacyTotp: account.totp,
          enforced: enabled && gate.enforced, synced: enabled && gate.synced, passkeys: enabled ? gate.passkeys : [], totp: enabled && gate.totp,
          error: null, autheliaVersion: "4.39.28", ca: true };
      }
      case "gate-sync": { const d = JSON.parse(stdin || "{}"); if (!d.current) return { ok: false, error: "The current password is wrong" };
        gate.synced = true;
        // the demo has no browser key ceremony: pretend one was registered later
        setTimeout(() => { if (!gate.passkeys.length) gate.passkeys = [{ kid: "demo1", description: "pico-fido", created: new Date().toISOString(), lastUsed: null, discoverable: true, verified: true }]; }, 4000);
        return { ok: true, user: "admin" }; }
      case "gate-code": return { ok: true, code: "7XKQ2M9P", sent: new Date().toISOString().slice(0, 19).replace("T", " "), text: null };
      case "gate-ca": return { ok: true, pem: "-----BEGIN CERTIFICATE-----\nDEMO\n-----END CERTIFICATE-----\n", filename: "helios-demo-ca.crt" };
      case "gate-totp-begin": { const d = JSON.parse(stdin || "{}"); if (!d.current) return { ok: false, error: "The current password is wrong" };
        gate.totp = true;
        return { ok: true, uri: "otpauth://totp/HeliOS:admin?secret=JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP", secret: "JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP",
          qrSvg: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 21 21"><path d="M0 0h7v7H0zM14 0h7v7h-7zM0 14h7v7H0zM9 9h3v3H9z"/></svg>' }; }
      case "gate-totp-check": return /^\d{6}$/.test((stdin || "").trim()) ? { ok: true } : { ok: false, error: "That code doesn't match. Check the phone's clock is right and try the next code" };
      case "gate-totp-remove": { const d = JSON.parse(stdin || "{}"); if (!d.current) return { ok: false, error: "The current password is wrong" };
        if (gate.enforced && !gate.passkeys.length) return { ok: false, error: "That's the last second factor: add a passkey first, or turn the gate off" };
        gate.totp = false; return { ok: true }; }
      case "gate-passkey-remove": { const d = JSON.parse(stdin || "{}"); if (!d.current) return { ok: false, error: "The current password is wrong" };
        if (gate.enforced && !gate.totp && gate.passkeys.length <= 1) return { ok: false, error: "That's the last second factor: add another passkey or a code first, or turn the gate off" };
        gate.passkeys = gate.passkeys.filter(k => k.kid !== d.kid); return { ok: true }; }
      case "gate-enforce": { const d = JSON.parse(stdin || "{}"); if (!d.current) return { ok: false, error: "The current password is wrong" };
        if (!d.on) { gate.enforced = false; return { ok: true, enforced: false }; }
        if (!gate.synced) return { ok: false, error: "Copy the admin password to the gate first" };
        if (!gate.passkeys.length && !gate.totp) return { ok: false, error: "Register a passkey (recommended) or a code first" };
        const retired = account.totp; account = { totp: false, pending: null }; gate.enforced = true;
        return { ok: true, enforced: true, retiredCockpitCode: retired }; }
      case "account-2fa-disable":
        if (account.totp && !/^(\d{6}|\d{8})$/.test((stdin || "").trim())) return { ok: false, error: "That code doesn't match. Enter the current code from the app, or a recovery code" };
        account = { totp: false, pending: null }; return { ok: true, user: "admin" };
      default: return { ok: false, error: `mock: unknown command ${cmd}` };
    }
  }

  function spawn(args, opts) {
    let stdin = "";
    const p = new Promise(res => setTimeout(() => {   // .input() is called synchronously, before this fires
      if (!args[0].endsWith("helios-ctl")) return res("");
      Promise.resolve(solctl(args.slice(1), stdin)).then(r => res(JSON.stringify(r)));
    }, 30));
    p.input = data => { stdin += data; return p; };
    return p;
  }
  window.cockpit = { spawn, location: { path: [], go() {} } };
})();
