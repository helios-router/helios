# Configuration

HeliOS is configured in two places, both in your own private repository (`/etc/nixos` by default):

* `site.nix`: `helios.*` NixOS options, for the fixed parts. Start from `templates/site.nix`.
* `state.json`: the parts that change often, edited in [Photosphere](Page-Header-and-Navigation) or by hand. Start from `templates/state.json`.

Everything is declared in `modules/options.nix`. [How It Works](How-It-Works) explains how the two fit together.

## helios.* options

"Required" means there is no default and the build fails without it.

### Router

| Option | Default | Meaning |
|---|---|---|
| `helios.hostName` | `"helios"` | Router host name; also `<hostName>.<domain>`. With a flake, the configuration name must equal it. |
| `helios.domain` | `"home.arpa"` | Local DNS suffix handed out by DHCP. |
| `helios.admin.user` | required | Your login on the router (member of `wheel`, SSH). |
| `helios.admin.sshKeys` | `[ ]` | Authorized SSH public keys (hardware `sk-ssh-ed25519` keys recommended). |
| `helios.configDir` | `"/etc/nixos"` | Writable checkout holding `state.json`. Photosphere edits it, commits to git there and runs `nixos-rebuild`. |
| `helios.stateFile` | `"state.json"` | State file name inside `configDir`. |
| `helios.state` | required | The parsed state: `builtins.fromJSON (builtins.readFile ./state.json)`. |

### Interfaces

| Option | Default | Meaning |
|---|---|---|
| `helios.interfaces.trunkMac` | required | MAC of the NIC cabled to the switch trunk. It carries the WAN and every network. Named `trunk0`. |
| `helios.interfaces.rescueMac` | `null` | MAC of a spare NIC used as an out-of-band rescue port (static IP, password SSH). `null` means none. Named `rescue0`. |
| `helios.interfaces.rescueAddress` | `"192.168.255.1"` | Rescue port address (/24). Plug a laptop in with `.2`. |
| `helios.interfaces.wanVlan` | `10` | VLAN (2 to 4094) the modem is on at the switch. |

### Tailscale (the router itself)

| Option | Default | Meaning |
|---|---|---|
| `helios.tailscale.enable` | `true` | Run Tailscale on the router. |
| `helios.tailscale.authKeyFile` | `"/var/lib/tailscale/authkey"` | File holding a (tagged) auth key. |
| `helios.tailscale.loginServer` | `null` | `https://...` URL of your own Headscale (or other) control server instead of Tailscale's. Used by the router and Heliosphere. |
| `helios.tailscale.tag` | `"tag:helios"` | Tag advertised by the router (must exist in your tailnet policy). |
| `helios.tailscale.adminExit` | `true` | Advertise the router as an unfiltered exit node. Restrict it to yourself in tailnet grants. |
| `helios.tailscale.advertiseRoutes` | `true` | Advertise networks with the `tailnet` flag as subnet routes. |

### Heliosphere (remote access container)

| Option | Default | Meaning |
|---|---|---|
| `helios.heliosphere.enable` | `false` | Default for the Remote access service. Photosphere can override it (see Services below). |
| `helios.heliosphere.tag` | `"tag:heliosphere"` | Tailscale tag of the container. |
| `helios.heliosphere.authKeyFile` | `"/var/lib/heliosphere/tailscale/authkey"` | Its Tailscale auth key. |
| `helios.heliosphere.publicHost` | `null` | Name or IP that WireGuard and OpenVPN client configs connect to. `null` uses the WAN IP at the time a client is added, which may change. |

### Solar Wind (notifications)

| Option | Default | Meaning |
|---|---|---|
| `helios.solarWind.enable` | `true` | Default for the Notifications service (self-hosted ntfy). |
| `helios.solarWind.port` | `2586` | ntfy port. |
| `helios.solarWind.exposure` | `"local"` | `local`: admin networks and Tailscale only. `proxy`: your reverse proxy (see `templates/Caddyfile.solarwind`) forwards to the router over Tailscale. `funnel`: Tailscale Funnel publishes it at `https://<host>.<tailnet>.ts.net`. |
| `helios.solarWind.publicUrl` | `null` | URL phones subscribe to. Required for `proxy` and `funnel`. |
| `helios.solarWind.iosRelay` | `false` | Let iPhones get instant push through ntfy.sh (only message IDs are relayed). |
| `helios.solarWind.topics.flare` | `"flare"` | Urgent topic: critical logs, trap hits, failures. |
| `helios.solarWind.topics.aurora` | `"aurora"` | Informational topic: internet back, AI summaries, backups. |

### Printing

| Option | Default | Meaning |
|---|---|---|
| `helios.printing.enable` | `false` | Default for the Print server service (CUPS, shared to networks with `printing`). |
| `helios.printing.drivers` | `[ ]` | CUPS driver packages from nixpkgs; empty for driverless printers. |
| `helios.printing.unfreeDrivers` | `[ ]` | Package names of proprietary drivers to allow, for example `[ "canon-cups-ufr2" ]`. |
| `helios.printing.ippUsb` | `true` | Enable `ipp-usb` for driverless USB printers. |

### Logwatch (Discovery, AI log triage)

| Option | Default | Meaning |
|---|---|---|
| `helios.logwatch.enable` | `false` | Default for the AI log triage service. |
| `helios.gate.enable` | `false` | Default for the Passkey sign-in service: an Authelia sign-in gate (behind Caddy) in front of Cockpit. Passkeys are the preferred second factor, a code the fallback. Set it up in Photosphere → Advanced → Admin account; see [Advanced](Page-Advanced#passkey-sign-in-card). |
| `helios.logwatch.model` | `"/var/lib/llm/model.gguf"` | Model file used when Photosphere's model is "site". |
| `helios.logwatch.backend` | `"vulkan"` | `vulkan` (iGPU), `cpu`, or `remote` (an existing llama.cpp server). Used when Photosphere's backend is "site". |
| `helios.logwatch.remoteUrl` | `null` | llama.cpp server URL; required when the backend is `remote`. |

### Removed options

`helios.vlans` and `helios.mediaServer` no longer exist; the build stops with a message pointing to `state.json` (`networks`, and a per-device `mediaServer`).

### Checks at build time

The build fails, with a message, when:

* any network lacks unique names, VLAN ids or prefixes; a name is not 1 to 12 lowercase letters or digits (or is `wan`, `lo`, `trunk0`, `rescue0`, `tailscale0`, `ifb4wan`, `lan0`); a network uses the WAN VLAN; a prefix is 10.60.0, 10.61.0 or 10.62.0;
* no network has `admin` access;
* a device is on a network that does not exist, or a `reach` entry names a missing network;
* router ports: one port uses a missing network, more than one is `wan`, the trunk or rescue NIC is mapped, or a port matches neither a MAC nor a name (or both);
* Heliosphere: ports are not distinct or collide with 22, 53, 67, 123, 443, 2586, 3493, 5353, 9090 or 41641; a way in's `lan` names a missing network; the proxy has no user or no network; egress mode is not `direct`, `wireguard`, `openvpn` or `proxy`, or `proxy` has no `egress.proxy`;
* printing: a printer name is not letters, digits, `-` and `_` (up to 64), or more than one printer is the default;
* `solarWind.exposure` is `proxy` or `funnel` without `publicUrl`; `funnel` without `tailscale.enable`, or together with `tailscale.loginServer`;
* `logwatch.backend = "remote"` without `remoteUrl`.

## Services: site.nix default and Photosphere override

Five services are switchable. `site.nix` sets each default; a choice in Photosphere (Advanced, Services) is stored in `state.json` under `services` and wins; "reset" goes back to the default.

| Service id | Default from | Default |
|---|---|---|
| `notifications` | `helios.solarWind.enable` | on |
| `vulnScan` | built in | on |
| `printing` | `helios.printing.enable` | off |
| `remote` | `helios.heliosphere.enable` | off |
| `logwatch` | `helios.logwatch.enable` | off |
| `gate` | `helios.gate.enable` | off |

Core services always run: networks and DHCP, firewall, DNS and filtering, NTP, management (Cockpit, SSH, backups), health checks. Turning a service on downloads its software at the next apply; turning it off removes it. Tailscale admin access is set only in `site.nix`.

## state.json keys

Top-level keys. All are optional except where noted; missing keys take the defaults shown. `version` is in the template but nothing in the code reads it.

| Key | Meaning |
|---|---|
| `version` | Template marker (`1`). Unused. |
| `restrictUnnamed` | `true` limits unnamed devices on `quarantine` networks to filtered internet. Needs a named device on an admin network to take effect. |
| `networks` | List of networks. Absent: the five defaults. |
| `devices` | Named devices. |
| `qos` | Smart queue settings. |
| `upnp` | UPnP / NAT-PMP settings. |
| `profiles` | Filtering profiles. Absent: the defaults in `pkgs/profiles.json`. |
| `maintenance` | Reboot window. |
| `logwatch` | AI log triage settings. |
| `drivers` | Built-in driver switches and settings. |
| `hardware` | The router's ports, switches, access points and UPSes. |
| `heliosphere` | Remote access: ways in and way out. |
| `printing` | Print drivers and printers. |
| `services` | Service overrides. |

### networks

| Field | Meaning |
|---|---|
| `name` | Interface name: 1 to 12 lowercase letters or digits, starting with a letter. |
| `label` | Display name (up to 24 characters). |
| `vlan` | 2 to 4094, not the WAN VLAN. |
| `prefix` | First three octets of a private /24, for example `10.70.0`. |
| `ipv6` | Subnet id (`0x0` to `0xff`) carved from the ISP's delegated prefix, or `null` for none. |
| `role` | `trusted`, `family`, `iot`, `guest`, `strict` or `mgmt`. |
| `color` | Optional `#rrggbb`. |
| `access` | Optional overrides of the role's flags (`admin`, `internet`, `reach`, `filtered`, `mdns`, `printing`, `upnp`, `cast`, `forceNtp`, `tailnet`, `quarantine`). |
| `defaultProfile` | Optional filtering profile for unnamed devices on this network. |

At most 32 networks. `reach` is `"all"` or a list of network names.

### devices

| Field | Meaning |
|---|---|
| `mac` | Lowercase `aa:bb:cc:dd:ee:ff`. |
| `name` | Lowercase letters, digits and dashes, up to 32. Becomes `<name>.<domain>`. Not starting with `net-`. |
| `vlan` | Network name. |
| `ip` | Fixed address in that network (`.20` to `.99`, chosen automatically). |
| `profile` | Filtering profile name. |
| `blocked` | `true` while paused. |
| `media` | A TV or similar that may reach media servers and be cast to. Ignored on admin networks. |
| `mediaServers` | Names of the servers a media device may use; absent means all. |
| `mediaServer` | `{ "tcp": [..], "udp": [..], "networks": [..] }` marks this device as a server. |
| `notes` | Free text, up to 200 characters. |

### qos

| Field | Default | Meaning |
|---|---|---|
| `enabled` | `false` | Needs both rates. |
| `down`, `up` | `null` | Rates such as `500mbit`, `50mbit` (kbit, mbit or gbit). |
| `algorithm` | `cake` | `cake` or `fq_codel` (with HTB; ignores fairness and priority). |
| `link` | `ethernet` | `docsis`, `ethernet`, `vdsl`, `adsl` or `custom`. |
| `overhead`, `mpu`, `atm` | `38`, `84`, `false` | Used when `link` is `custom`. |
| `fairness` | `device` | `device` (per home device, NAT-aware), `triple` or `flow`. |
| `priority` | `diffserv3` | `diffserv4`, `diffserv3` or `besteffort`. |
| `ackFilter` | `false` | Helps when upload is much smaller than download. |
| `rtt` | `internet` | `internet`, `regional` or `metro`. |
| `wash` | `true` | Clear incoming DSCP marks after classifying. |

### upnp

| Field | Default | Meaning |
|---|---|---|
| `enabled` | `true` | UPnP on or off. Only networks with the `upnp` flag may use it. |
| `blockedDevices` | `[ ]` | Device names that may not open ports. |
| `blockedPorts` | `[ ]` | Public ports or ranges never to map, for example `"3389"` or `"6000-6100"`. |

### profiles

Each profile: `name` (id), `label`, `desc`, and the switches below. Devices and networks refer to the `name`.

| Field | Meaning |
|---|---|
| `global` | Use AdGuard's own global settings instead of the switches. |
| `filtering` | Block ads, trackers and block-list entries. |
| `safeBrowsing` | Block known malware and phishing. |
| `parental` | Block adult content. |
| `safeSearch` | Force Safe Search on search engines. |
| `youtubeRestricted` | YouTube Restricted Mode. |
| `blockedServices` | AdGuard service ids to block. |

### maintenance

`rebootWindow`: `{ "enabled": false, "days": ["sun"], "time": "04:00" }`. Reboots only inside the window, only when an update needs it.

### logwatch

Overrides of the defaults in `pkgs/logwatch-models.json`.

| Field | Default | Allowed |
|---|---|---|
| `model` | `small` | a preset id, `custom` (needs `customPath`, a `.gguf` file directly in `/var/lib/llm`, so only root can replace it) or `site` |
| `backend` | `site` | `site`, `vulkan`, `cpu`, `remote` |
| `interval` | `10` | minutes, 5 to 240 |
| `keepLoaded` | `false` | keep the model in memory between runs |
| `threads` | `2` | 1 to 64 |
| `cpuPercent` | `150` | 25 to 6400 |
| `memoryMB` | `null` | 512 to 262144; `null` uses the preset's RAM need, else 2560 |
| `guard.enabled` | `true` | pause when the router is busy |
| `guard.cpuPressure`, `guard.memoryPressure` | `40`, `20` | 5 to 95 (percent) |
| `guard.pauseHours` | `6` | 1 to 72 |

### drivers and hardware

`drivers`: `{ "<name>": { "enabled": true, ... } }` for `manual`, `local-ports`, `lldp`, `nut`. Drivers marked `always` cannot be disabled. `nut` settings: `shutdown` (`charge` default 20 percent, `runtime` default 180 seconds) and `server` (`enabled`, `networks`) to share the UPS with other machines on port 3493.

`hardware` is a list of devices, each with `id` (`^[a-z][a-z0-9-]{0,31}$`, unique), `kind` (`router`, `switch`, `ap`, `ups`), `driver`, `label`, and:

* `router` (id `router`, at most one): `ports`, each `{ match: { mac } or { name }, label, mode, network or networks, wan }`. The trunk NIC and the rescue port cannot be mapped.
* `switch`, `ap`: `ports` (`port`, `mode`, `network`/`networks`, `wan`, `note`), `ssids` (`ssid`, `network`), `notes`. HeliOS writes a checklist; it does not configure these devices.
* `ups`: `match.usb` (`vvvv:pppp`, optional `serial`) and `nutDriver` (default `usbhid-ups`).

Port `mode`: `access` (one untagged network), `trunk` (`networks` is `"all"` or a list; `wan` also carries the WAN VLAN), `wan`, `unused`. With any router port mapped as access or trunk, the trunk NIC and those ports join a VLAN-aware bridge `lan0`. At most 32 devices, 64 ports each, 16 SSIDs each.

### heliosphere

```json
"heliosphere": {
  "ingress": {
    "tailscale": { "enabled": true, "exitNode": true, "hostname": "heliosphere", "lan": [] },
    "wireguard": { "enabled": false, "port": 51820, "lan": [], "tunnel": "full", "keepalive": 25, "mtu": null,
                   "peers": [ { "name": "pixel", "publicKey": "base64=", "ip": "10.61.0.2" } ] },
    "openvpn":   { "enabled": false, "port": 1194, "proto": "udp", "lan": [],
                   "clients": [ { "name": "laptop", "serial": "0A1B", "revoked": false } ] },
    "proxy":     { "enabled": false, "socksPort": 1080, "httpPort": 3128, "networks": [ "iot" ], "users": [ "tv" ] }
  },
  "egress": { "mode": "direct", "killswitch": true,
              "proxy": { "type": "socks5", "host": "proxy.example.net", "port": 1080 } }
}
```

The values shown are the defaults except the example peers, clients, users and egress proxy. `lan` lists the home networks that way in may reach (default none); `tunnel` is `full` or `split`. Names match `^[a-z0-9][a-z0-9-]{0,31}$`. Secrets are never in `state.json`.

### printing

`{ "drivers": [ "<id from printing.json>" ], "printers": [ { "name", "deviceUri", "model", "description", "location", "default", "options" } ] }`. `model` is a driver name, or `everywhere` for driverless printers. `options` (PPD options) is read by the NixOS module; Photosphere does not set it.

### services

`{ "<service id>": { "enabled": true } }` overrides the `site.nix` default. See the table above.

## Catalogs in pkgs/

Read-only data files shipped with HeliOS. Changes to `pkgs/*.json` are flagged for careful review by CI (see [Updates and Security](Updates-and-Security)).

| File | Holds |
|---|---|
| `networks.json` | `access`: help text for each flag. `roles`: label, colour, naming theme, description and flags of `trusted`, `family`, `iot`, `guest`, `strict`, `mgmt`. `defaults`: the five default networks. |
| `drivers.json` | `drivers` (`manual`, `local-ports`, `lldp`, `nut`: label, `builtin`, `default`, `always`, `kinds`, `capabilities`, `desc`), `upsVendors` (USB vendor ids: APC, CyberPower, Eaton / MGE, Tripp Lite, Liebert / Vertiv, PowerCOM, Phoenixtec) and `nutDefaults`. |
| `blocklists.json` | `doh_v4`: public resolver addresses filtered networks may not reach at all (1.1.1.1, 1.0.0.1, 8.8.8.8, 8.8.4.4, 9.9.9.9, 149.112.112.112, 94.140.14.14, 94.140.15.15, 208.67.222.222, 208.67.220.220). `warp_v4`: four Cloudflare WARP /24 ranges. Used by Heliopause and Heliosphere. |
| `profiles.json` | `settings`: help text for each profile switch. `defaults`: `admin` (Unfiltered), `adult` (Standard: ads, trackers, malware), `kid` (Kids: adds adult content blocking, Safe Search, YouTube Restricted Mode), `default` (AdGuard defaults). |
| `printing.json` | Free CUPS driver packages Photosphere can enable: `gutenprint`, `hplip`, `brlaser`, `foo2zjs`, `splix`, `epson-escpr`, `epson-escpr2`. |
| `services.json` | `core` services, switchable `services` (group, label, size, tab, units, description) and `elsewhere` (QoS, UPnP, UPS and neighbour discovery, Tailscale admin access). |
| `logwatch-models.json` | `baseUrl` (Hugging Face), `presets`, `defaults`. |

AI log triage presets (all Qwen3 GGUF files; the SHA-256 in the file is checked after download):

| Preset | Parameters | Download | RAM needed |
|---|---|---|---|
| `tiny` | 0.6B | 0.64 GB | 1024 MB |
| `small` (default) | 1.7B | 1.83 GB | 2560 MB |
| `medium` | 4B | 2.50 GB | 3584 MB |
| `large` | 8B | 5.03 GB | 6656 MB |
| `xl` | 14B | 9.00 GB | 11264 MB |

Photosphere recommends the largest preset whose RAM need fits in half of (RAM minus 2 GB), capped at `medium` without a GPU unless there are 8 or more CPU cores.

## Other files in templates/

| File | Use |
|---|---|
| `site.nix`, `state.json` | Starting points for your site. |
| `flake.nix` | Flake setup; its configuration name (`helios`) must match `helios.hostName`. |
| `configuration.nix` | Non-flake setup; imports `./helios/modules`. |
| `tailnet-policy.hujson` | Tailscale policy to merge into yours: tags, grants, Funnel, auto-approvers. |
| `Caddyfile.solarwind` | Reverse proxy for `exposure = "proxy"`. |

## Runtime files

| Path | Holds |
|---|---|
| `/etc/helios/config.json` | Generated from your options; read by `helios-ctl`. |
| `/var/lib/helios/adguard.json` | AdGuard URL and login, for profile sync. You create it after AdGuard's setup wizard. |
| `/var/lib/helios/solarwind-token`, `solarwind.env` | Generated on first boot; never in the Nix store. |
| `/var/lib/helios/apply.json`, `apply.log` | State and log of the current apply. |
| `/var/lib/heliosphere/` | Remote access secrets and PKI. |
| `/var/lib/llm/` | Downloaded log triage models: files 0400, owned by `llm` (the account llama-cpp runs as); the folder is root's with group `llm` (0750), so llama-cpp can read but not change it. |
