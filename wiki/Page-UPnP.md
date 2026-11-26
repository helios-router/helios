# UPnP tab

UPnP and NAT-PMP let an app or game on your network ask the router to open a port to the internet for it, without you setting up port forwarding by hand (consoles, video calls, torrent clients, some cameras). The convenience has a cost: any device that can ask can open a door to itself, including a compromised one. This tab shows what is open, lets you close it, and lets you block devices or ports for good.

Defaults: UPnP is **on**, but only networks that have the "UPnP" access option (see [Networks](Page-Networks)) can use it. Devices on other networks can never open ports. The router's own remote access container (Heliosphere) can never use it either.

## Toolbar

| Item | What it does |
|---|---|
| **UPnP & NAT-PMP** switch | Turns the feature on or off. It is a pending change ("UPnP setting saved, apply to activate"). Off removes the UPnP service at the next apply. |
| Text next to it | "Devices on *Family, Kids* can open ports to themselves. Other networks never can." or, if no network allows it, "No network allows UPnP (Networks, access)." |
| **Close all** | Greyed out when nothing is open. After confirming, removes every open port mapping right now. Apps that still need one ask again within a few minutes. |

## Open ports table

The page reloads every 10 seconds (it skips a refresh while a dialog is open or you are typing).

| Column | Meaning |
|---|---|
| Device | The device's name if you have named it in [Devices](Page-Devices); otherwise "Unnamed device". A **blocked** badge appears if the device or port is on the block list (hover for why). Under the name: the device's IP address. |
| Protocol | TCP or UDP. |
| Public port | The port opened on the internet side. |
| Device port | The port on the device it forwards to. |
| Requested by | The description the app gave when it asked, or a dash. |
| Expires | "in 59m" and so on if the app asked for a lease time, or "Until reboot". |

Empty table text: "No open ports right now." or "UPnP is off."

Row buttons:

| Button | Effect |
|---|---|
| **Close** | Removes that one mapping immediately. The app may ask again. Not a pending change. |
| **Block device** | Only shown for named devices. Confirm, then the device is added to the block list and everything it has open is closed now. The block itself is a pending change; it becomes permanent at Apply. |
| **Block port** | Blocks that public port number for all devices (same confirm and behaviour). |

## Blocked card

For devices that open ports they should not, and ports that should never face the internet. Blocks are checked first, before anything is allowed.

**Devices**: chips for each blocked device with an **x** to unblock. Below, a drop-down of named devices (those with a known fixed address) and **Add to block list**. Only named devices can be blocked, because they have a fixed IP address; an unnamed device must be named first.

**Public ports**: chips for blocked ports and ranges. Type a port such as `3389` or a range such as `6000-6100` and press **Add to block list**. Ports below 1024 are never allowed anyway.

Unblocking is a pending change too ("Unblocked, apply to activate").

## How blocking behaves

1. You press Block: the entry is saved to `state.json` (pending) and matching open ports are closed right away. To close them, HeliOS briefly stops and restarts the UPnP service (the service rewrites its lease list from a file when it starts).
2. You press Apply: the UPnP service is configured with deny rules for those devices and ports.
3. Until Apply, a device could ask for a new port again; it would show the blocked badge.

The rules the router uses, in order: deny blocked device addresses; deny blocked public ports; allow ports 1024-65535 for devices on UPnP networks mapping to themselves; deny everything else. "Secure mode" is on, so a device can only open ports that point to itself, never to another device.

## State

```json
"upnp": { "enabled": true, "blockedDevices": ["camera"], "blockedPorts": ["3389", "6000-6100"] }
```

Missing keys: `enabled` true, empty block lists. Ports are normalized (a one-port range is saved as a single number). Block entries can only name a device from `devices` that has an IP.

## Gotchas

- Which networks may use UPnP is decided on the Networks tab (the "UPnP" access option), not here.
- If every network has UPnP off, the service does not run even with the switch on.
- Closing ports does not stop an app asking again. Block the device or port if you want that.
- The "Expires" text for lease-less mappings, "Until reboot", is accurate: the list is kept in memory and is empty after a router restart.
- Remote access uses its own ports (see [Remote access](Page-Remote-Access)). Photosphere refuses to save a Remote access port that a UPnP mapping already uses.
- Changing UPnP or QoS settings both show up in the pending bar as "Change upnp settings".

## Under the hood

- `pkgs/photosphere/photosphere.js:929-985`: `viewUpnp()`.
- `pkgs/helios-ctl.py:2210-2224`: `parse_upnp()` reads `/run/miniupnpd.leases` (`UPNP_LEASES`, line 2227).
- `pkgs/helios-ctl.py:2230-2299`: block-list helpers, `upnp_blocked()`, `upnp_remove()` (stops `miniupnpd`, edits the lease file, starts it).
- `pkgs/helios-ctl.py:2289-2362`: `upnp`, `upnp-clear`, `upnp-close`, `upnp-block`, `upnp-unblock`. The switch uses `settings-set --upnp` (`:815-832`).
- `pkgs/helios-ctl.py:324-326`: both `qos` and `upnp` changes are described as "Change ... settings".
- `modules/voyager.nix:65-79`: `services.miniupnpd` (WAN interface, NAT-PMP and UPnP, `secure_mode=yes`, lease file, ACL lines).
- `modules/heliopause.nix:129-130`: firewall accepts SSDP (UDP 1900), NAT-PMP (5351) and the control port (TCP 5000) from UPnP networks; `:161` accepts forwarded ports opened by UPnP.
- `modules/options.nix:282`: "UPnP" entry in the status service list.
