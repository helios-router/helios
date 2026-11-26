# Drivers — design (draft)

Status: phase 1 is implemented (see "Phase 1 interface" below); later phases are proposals.

HeliOS today configures only the router itself. Drivers let it also see and
manage the hardware around it: its own extra ports, managed switches, Wi-Fi
access points and UPSes. The aim is to support as much hardware as possible
without baking any vendor into the core, and without ever installing code
the user didn't approve.

## Principles

1. **Detect automatically, install only with approval.** HeliOS looks at its
   own hardware and listens on the network, then *suggests* drivers in
   Photosphere. Nothing is installed or configured until the user clicks
   Install and applies.
2. **"Download" = enable a pinned Nix package.** Drivers are Nix packages and
   modules from a driver catalog that is a flake input locked by hash. Enabling
   one adds its name to `state.json`; the next apply builds it, fetching only
   what that driver needs. No code is fetched at runtime, from arbitrary URLs,
   or outside a rebuild — so drivers get the same review, pinning, git history
   and confirm-or-rollback as everything else.
3. **The core knows capabilities, not vendors.** The core talks to drivers
   through a small set of capabilities (below). Networks, ports and SSIDs stay
   defined once, in `state.json`; drivers translate them to a device.
4. **Every push is a plan first.** A driver shows the exact changes it would
   make (a diff) before applying, and must be able to verify and undo them.
   Changing a switch can cut the router off in a way its own 120 s rollback
   can't fix, so switch/AP drivers get their own confirm-or-revert step.
5. **Manual always works.** If no driver fits, HeliOS still produces a
   per-device checklist ("trunk port: tag VLANs 20, 30, 40…", "SSID Guests →
   VLAN 70"). Drivers automate the checklist; they never replace it.

## Capabilities

| Capability | What the driver can do | Examples |
|---|---|---|
| `ports` | Map physical ports to networks: access (one untagged network), trunk (tagged list), WAN | the router's own NICs / DSA switch chip, managed switches |
| `vlans` | Make sure VLANs exist / are carried on trunk links | MVRP, managed switches |
| `ssids` | Create SSIDs and map each to a network (or per-device VLANs via RADIUS) | UniFi, OpenWrt APs, Merlin |
| `ups` | Report battery/runtime/load, raise events (on battery, low battery, back), trigger graceful shutdown | any UPS supported by NUT |
| `inventory` | Report model, firmware, port status and link speeds for the Overview | most managed devices |

A driver declares which capabilities it has and which of these operations it
supports for each: `detect`, `read` (current state), `plan` (diff against
`state.json`), `apply`, `verify`, `revert`.

## Detection

Detection is read-only, runs on admin/management networks only, and is
**passive by default**:

| Source | Finds | Active? |
|---|---|---|
| Local: `/sys/class/net`, DSA (`dsa` master/ports), PCI/USB IDs | the router's own ports and switch chip, USB NICs | no |
| Local: USB HID Power Device class, `nut-scanner -U` | USB UPSes | no |
| LLDP (`lldpd`) | switches/APs announcing themselves: vendor, model, management IP, port | no (listens) |
| mDNS / SSDP | APs, controllers, network UPS cards | no (listens) |
| Vendor discovery (e.g. UniFi UDP 10001) | specific vendors' devices | opt-in |
| SNMP `sysObjectID` / `sysDescr` probe, `nut-scanner` SNMP/XML | managed switches, network UPS cards | opt-in |

Each finding is matched against the catalog's fingerprints (vendor OUI, LLDP
system description, sysObjectID, USB VID:PID) to suggest a driver and how
sure the match is. Findings land in Photosphere → **Hardware**, never in the
firewall or anywhere that changes behaviour.

## Distribution

- **Built in** (part of HeliOS, always available): `local-ports` (the router's
  own NICs/DSA chip via systemd-networkd VLAN-aware bridges), `nut` (UPS via
  Network UPS Tools — covers most brands over USB, serial, SNMP and network),
  `mvrp` (announce VLANs on the trunk), `lldp` (discovery), `manual`
  (the checklist).
- **Optional** (`helios-drivers`, a separate flake that the site template
  includes as an input, pinned in `flake.lock`): vendor drivers such as
  UniFi (controller API), OpenWrt (ubus/SSH), MikroTik (RouterOS API),
  Asuswrt-Merlin (SSH script), and switch families as they're written.
  Present in the lock file but not built until enabled.
- **Third party**: any flake exposing a module with the same driver
  interface; the user adds it as an input by hand (advanced, unlisted).
- Updating drivers = updating the lock file, like any other input, through
  Photosphere's normal apply.

## Supply chain security

A driver runs on the router and can touch switches, APs and power. A
compromised driver release is the most likely way to attack a HeliOS
household, so drivers are treated as untrusted until proven otherwise, and
"proven" is re-checked continuously.

### What each check catches

| Check | Catches | Misses |
|---|---|---|
| Hash pinning (`flake.lock`) | anything changing under a version you already have | a malicious *new* version you choose to update to |
| Signed releases (cosign / Sigstore) + provenance | releases not built by the catalog's CI from the reviewed source | a malicious commit that got reviewed and merged |
| Syft SBOM + Grype, `vulnix` (Nix-native) | known CVEs in the driver and its dependencies | unknown bugs, deliberately malicious code |
| ClamAV | known malware files bundled in a release | nearly all source-level attacks; included as a cheap extra, not relied on |
| Review + diff of every update (two people for the catalog) | malicious or sloppy code changes | what reviewers miss |
| Cooldown (new versions wait N days before auto-update) | compromised releases caught by others first | slow-burn attacks |
| Sandboxing at runtime | limits damage when everything above failed | — |

### Where scanning runs

Heavy scanning runs in the **driver catalog's GitHub Actions CI**, not on every router:

- **On each release**: build reproducibly, generate the SBOM, run Grype,
  `vulnix`, ClamAV and the driver's tests, sign the build and its scan report.
- **Weekly**: rescan every published version against fresh vulnerability
  data. A new severe CVE in a version already in use triggers an advisory.
- The result is a **signed advisory feed**: per driver version, *ok*,
  *deprecated* (update when convenient), *demoted* (update soon, warn) or
  *blocked* (remove or roll back now), plus a safe target version.

The feed is published from the catalog repo (a GitHub release asset or Pages file). Routers fetch it on a schedule (data only, never code), check its signature
against a key pinned in HeliOS, and run a local `vulnix` pass over the whole
system closure, HeliOS core and nixpkgs included, not just drivers.

### What the router does with it

| Feed says | Router does |
|---|---|
| ok, newer version available | per policy: notify, or auto-update after the cooldown |
| deprecated | info notification |
| demoted | urgent notification; auto-update to the safe version if policy allows |
| blocked | urgent notification, then roll back to the last safe version or disable the driver, automatically |

The feed can only make things **more restrictive** or point at versions that
are already signed in the catalog's history. It can't introduce new code or
new drivers. So a stolen feed key can disable drivers but can't install
anything.

Disabling a driver never breaks the network: drivers push configuration to a
device, and the device keeps running what it last received. What stops is
management, and for UPS, automatic shutdown. HeliOS says exactly that in the
notification.

### Unattended updates

Auto-updates are opt-in per driver (`"updates": "notify" | "auto"`). There is
nobody to click Confirm, so an automatic update uses a **health check**
instead of the 120 s window: the router stays reachable on an admin network,
core services are up, and the driver's `verify` passes. If any check fails,
it rolls back and sends an urgent notification. Every automatic change is a
git commit like any other.

### Runtime sandboxing

Each driver runs as its own systemd service with its own user, with:

- `ProtectSystem=strict` and only its own state directory writable
- no access to other drivers' secrets
- network access limited to the devices it manages (`IPAddressAllow=`),
  plus nothing on the internet
- no root. Changes to the router itself (ports, VLANs) go through
  helios-ctl's own plan/apply, never straight from the driver

### Limits

No scanner proves a driver is safe. These checks make an attack harder to
land, quicker to detect and smaller in effect. Built-in drivers live in the
HeliOS repo and get the same CI scans. Third-party drivers outside the
catalog get the sandbox and local `vulnix` scan, but no feed coverage, and
Photosphere says so.

## State and secrets

```json
"drivers": {
  "nut":  { "enabled": true },
  "unifi": { "enabled": true, "controller": "https://10.99.0.5:8443" }
},
"hardware": [
  { "id": "switch-1", "driver": "manual", "label": "Main switch",
    "ports": { "1": { "mode": "trunk", "networks": "all" }, "2": { "mode": "access", "network": "trusted" } } }
]
```

Credentials (controller passwords, SNMP communities, API tokens) never go in
`state.json` or git: they live under `/var/lib/helios/drivers/<name>/`,
mode 0600, entered in Photosphere and backed up separately.

## UPS

The `nut` driver is the first concrete one because it's high value and
vendor-neutral:

- Detects USB UPSes automatically; network UPS cards (SNMP/XML) on opt-in.
- Overview tile: on mains / on battery, charge, runtime, load.
- Notifications: urgent when on battery and when low; info when power is back.
- Graceful shutdown of the router at a configurable runtime or charge
  threshold, and optionally acts as a NUT server so Unraid or other machines
  shut down too.

## Phases

1. **Framework + built-ins**: driver interface in helios-ctl, Hardware tab,
   local detection, LLDP listening, the manual checklist, `nut`, `local-ports`,
   the per-driver sandbox, and the weekly local `vulnix` scan of the system.
2. **MVRP** and the optional catalog flake with the first vendor driver
   (whichever hardware the first user actually owns), shipped together with
   the catalog CI (signing, SBOM, scans) and the advisory feed. No optional
   driver ships before those exist.
3. More vendor drivers; opt-in active discovery.

## Open questions

- Which hardware to target first in phase 2.
- Whether HeliOS should also manage a UPS's own settings, or only read it.
- How far per-device Wi-Fi VLANs (RADIUS) should go in the core vs drivers.

## Phase 1 interface (implemented contract)

Built-in drivers are listed in `pkgs/drivers.json` (`drivers`, `upsVendors`,
`nutDefaults`). It reaches helios-ctl through `/etc/helios/config.json` as
`driverCatalog`, or from the file next to the script in a source checkout.

### state.json

```json
"drivers": {
  "lldp": { "enabled": true },
  "nut":  { "enabled": true, "shutdown": { "charge": 20, "runtime": 180 },
            "server": { "enabled": false, "networks": ["trusted"] } }
},
"hardware": [
  { "id": "router", "kind": "router", "driver": "local-ports", "label": "This router",
    "ports": [
      { "match": { "mac": "aa:bb:cc:dd:ee:01" }, "label": "Port 2", "mode": "access", "network": "trusted" },
      { "match": { "name": "lan2" }, "mode": "trunk", "networks": ["family", "iot"], "wan": false },
      { "match": { "mac": "aa:bb:cc:dd:ee:03" }, "mode": "wan" } ] },
  { "id": "main-switch", "kind": "switch", "driver": "manual", "label": "Main switch", "notes": "",
    "ports": [
      { "port": "1", "mode": "trunk", "networks": "all", "wan": true, "note": "to router" },
      { "port": "2", "mode": "wan", "note": "modem" },
      { "port": "3", "mode": "access", "network": "trusted" } ] },
  { "id": "living-ap", "kind": "ap", "driver": "manual", "label": "Living room AP",
    "ports": [ { "port": "uplink", "mode": "trunk", "networks": "all", "wan": false } ],
    "ssids": [ { "ssid": "Home", "network": "family" }, { "ssid": "Guests", "network": "guest" } ] },
  { "id": "ups", "kind": "ups", "driver": "nut", "label": "Office UPS",
    "match": { "usb": "051d:0002", "serial": "3B1234X56789" }, "nutDriver": "usbhid-ups" }
]
```

- A driver missing from `drivers` uses its catalog `default`. Drivers marked `always`
  can't be disabled.
- `id`: `^[a-z][a-z0-9-]{0,31}$`, unique. At most one `kind: router` device, with id `router`.
- Port `mode`: `access` (needs `network`), `trunk` (`networks`: `"all"` or a list,
  `wan`: also carry the WAN VLAN), `wan` (on a switch: untagged WAN VLAN for the
  modem; on the router: the WAN port itself, at most one), `unused`.
- Router ports are matched by permanent MAC, or by kernel name for switch-chip
  (DSA) ports, which share one MAC. The trunk NIC and the rescue port can't be mapped.
- `kind: ups` needs `match.usb` (`vvvv:pppp`, hex); `serial` is optional and
  only matters with several identical UPSes. `nutDriver` defaults to `usbhid-ups`.
  Adding a UPS enables the `nut` driver if it's off.

### Detection file

The `helios-detect` service (sandboxed, no root) runs `helios-ctl detect` and
writes `/var/lib/helios-detect/hardware.json`:

```json
{ "time": 1795650000,
  "ports": [ { "name": "enp3s0", "mac": "aa:bb:cc:dd:ee:01", "driver": "r8169", "bus": "pci",
               "speed": 1000, "carrier": true, "role": "free" } ],
  "usb":   [ { "id": "051d:0002", "vendor": "American Power Conversion", "product": "Back-UPS ES 600M1",
               "serial": "3B1234X56789", "kind": "ups" } ],
  "lldp":  [ { "localPort": "trunk0", "chassis": "switch-1", "mgmtIp": "10.99.0.2",
               "sysDescr": "...", "port": "Ethernet1/0/8", "portDescr": "...",
               "capabilities": ["Bridge"] } ] }
```

Port `role`: `trunk` (the trunk NIC), `rescue`, `wan`, `mapped` (in `hardware`),
`free`. Only physical ports are listed (`/sys/class/net/*/device` exists, or a
DSA port). `bus`: `pci`, `usb`, `dsa`, `platform`.

### helios-ctl commands

| Command | Output / effect |
|---|---|
| `detect` | writes the detection file, prints it with `ok` |
| `hardware` | `{ok, catalog, drivers, devices, detected, suggestions, ups, checklists, security, dirty}` |
| `driver-set <json>` | `{name, enabled, ...settings}` → `state.drivers` |
| `hardware-set <json>` | add/replace a device (by `id`; `from` to rename) |
| `hardware-remove <id>` | remove a device |
| `ups-event` | upsmon's NOTIFYCMD (env `NOTIFYTYPE`, `UPSNAME`, message as argv). Runs as the `nut` user and only writes one JSON event `{time, type, ups, message}` into the spool dir (`HELIOS_UPS_SPOOL`, default `/run/helios-ups`; tmp file + rename). No token, no network |
| `ups-forward` | runs as root when the spool isn't empty (systemd path unit): sends each event in name order as a notification (`flare` for ONBATT/LOWBATT/FSD/SHUTDOWN/COMMBAD/NOCOMM/REPLBATT/NOPARENT, else `aurora`; title uses the UPS device's label), deletes it after a successful send, keeps it on failure. Prints `{ok, sent, failed}` |
| `vulnix-report <file>` | summarises a vulnix JSON report, stores the summary, notifies on new findings |

`hardware` details:
- `catalog`: `{kinds, modes, upsVendors, nutDefaults, wanVlan}`.
- `drivers`: every catalog driver, merged with state: `{name, label, desc, enabled, always, builtin, capabilities, kinds, settings}`.
- `devices`: `state.hardware` as stored.
- `detected`: the detection file, or `null` if detection hasn't run.
- `suggestions`: `[{kind, driver, reason, device}]`, where `device` is a prefilled
  `hardware` entry. It suggests a UPS for a detected UPS not yet configured, `local-ports`
  when free ports exist and there's no router device, and a `manual` switch/AP for each
  LLDP neighbour not yet configured (matched by label = chassis name). Already
  configured things aren't suggested.
- `ups`: `{<id>: {status, charge, runtime, load, model, raw}}` from `upsc <id>@localhost`.
  `status` is NUT's `ups.status` (`OL`, `OB`, `LB`…), with `null` values when upsc fails.
- `checklists`: `{<id>: ["Port 1: trunk — tagged VLANs 10 (WAN), 20 (Trusted), …", "SSID “Guests” → VLAN 70 (Guests)", …]}`
  for every `manual` device.
- `security`: the last vulnix summary `{time, total, critical, high, medium, low, top: [{name, version, cve, score}]}`, or `null`.
