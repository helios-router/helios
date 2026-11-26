# Overview

The Overview tab is the first page you see in Photosphere. It answers one question: is the router healthy right now? It shows internet status, how many devices are on your networks, live throughput, the state of the router's services, and anything that needs your attention (such as a pending reboot).

This page also describes the parts of Photosphere that appear on every tab: the header, the gear button, the pending-changes bar and the Apply dialog.

Related pages: [Devices](Page-Devices), [Traffic](Page-Traffic), [Hardware](Page-Hardware), [Header and navigation](Page-Header-and-Navigation), [QoS](Page-QoS), [History](Page-History).

## Parts shared by every tab

### Header

| Item | What it shows |
| --- | --- |
| Title | `HeliOS on <hostname>`. The browser tab title uses the same text. |
| Subtitle | `Up <time>`: the router's uptime. |
| Tab bar | The main tabs: Overview, Devices, Traffic, Notifications, QoS, UPnP, History. |
| Second row of tabs | Appear only in Advanced mode (see below). |
| Red number on the Devices tab | How many devices have a DHCP lease but are not named yet. Clicking the tab takes you to them. |
| Gear button | Turns Advanced mode on or off. |
| Sun/moon button | Switches light and dark theme. The choice is remembered in your browser. |

On a narrow screen the tab bar becomes a drop-down list.

### Advanced mode (the gear button)

Advanced mode is stored only in your browser (local storage key `photosphere-advanced`), not on the router. When it is on, these extra tabs appear under their main tab:

| Main tab | Advanced tab shown beneath it |
| --- | --- |
| Overview | [Hardware](Page-Hardware) |
| Devices | [Networks](Page-Networks) |
| Traffic | [Filtering](Page-Filtering) |
| Notifications | Remote access (only if the Remote access service is on) |
| QoS / UPnP | Printing (only if the Printing service is on) |
| History | Advanced |

If you open an advanced tab while Advanced mode is off, you get a card with a "Show advanced settings" button instead of the page. Turning Advanced mode off while on an advanced tab sends you back to Overview.

### Administrative access

If Cockpit's "Administrative access" is off, a yellow banner says so. HeliOS reads leases and firewall state as root, so most pages show nothing until you turn it on in Cockpit's top bar.

### The pending-changes bar

Almost every edit in Photosphere (naming a device, editing a network, changing a profile, saving ports) only changes the router's settings file, `state.json`. Nothing on the router changes yet. A bar appears under the header listing up to six pending changes ("and N more" beyond that).

| Button | What it does |
| --- | --- |
| Apply changes | Starts a rebuild of the router with your changes (`helios-ctl apply-start`). Opens the Apply dialog. |
| Discard | Asks for confirmation, then throws away every edit since the last confirmed apply (`helios-ctl discard`, which restores `state.json` from git). |

The bar refreshes every 5 seconds. Things that take effect immediately and do not show up as pending: the Pause button on [Devices](Page-Devices), Send test on [Notifications](Page-Notifications), Reboot now, Restart on a service.

### The Apply dialog

1. **Rebuilding.** The router runs `nixos-rebuild switch` in the background (as a systemd unit, so closing the browser does not stop it). The log is shown live. This usually takes under a minute but can be longer.
2. **Changes are live.** After the rebuild, HeliOS syncs the filtering profiles to AdGuard Home and cleans up stale printers. A countdown starts: you have **120 seconds** to press **Keep changes**.
3. **Keep changes** commits `state.json` to the router's git history (visible on the History tab). **Revert now** rolls back immediately.
4. If you do nothing, the router rolls itself back to the previous system generation when the countdown ends. This is the safety net for a change that cuts you off.

If the rebuild fails, nothing on the router changed and your edits stay pending: fix or discard them. The dialog can be reopened while an apply is running or waiting for confirmation (it reopens by itself after a page reload).

## What the Overview tab shows

### Reboot banner

Shown only when an update is installed but the router is still running the old kernel (or kernel modules, or boot image). It says which kernel is running and which is installed.

| Control | What it does |
| --- | --- |
| Reboot now | Asks for confirmation, then reboots the router (`helios-ctl reboot`). Every network loses internet for a minute or two. Refused while a change is being applied or waiting for confirmation. |
| Schedule... | Shown only if no reboot window is set. Turns on Advanced mode and opens the Advanced tab, where the reboot window lives. |

If a reboot window is set, the banner says when the router will reboot by itself.

### Tiles

The row of tiles refreshes every 10 seconds (the live numbers update every 2 seconds).

| Tile | Meaning |
| --- | --- |
| Internet | Online, Offline or Unknown, with how long it has been in that state. This comes from the outage monitor, which pings 1.1.1.1, 9.9.9.9 and 8.8.8.8 every 30 seconds. Two failed checks in a row count as an outage (and the outage is dated from the first failed check). |
| Devices | The number of active DHCP leases across all networks, and on how many networks. Below it: "N new waiting to be named" (leases from devices you have not named, with a **new** badge) or "N named" (how many devices you have named in total, online or not). The lease count can include a device that has just left, until its lease expires. |
| Download now / Upload now | Current WAN throughput, measured from interface counters. The footer shows the QoS cap from the [QoS](Page-QoS) tab settings (a dash if no cap is set). |
| Outages (24 h) | Outages recorded in the last 24 hours, and when the last one ended and how long it lasted. |
| UPS | Only shown when a UPS is set up on the [Hardware](Page-Hardware) tab. Status, charge and remaining runtime. Clicking it opens Hardware. |

### Cards

| Card | Contents |
| --- | --- |
| Internet throughput | Live chart of WAN download and upload for the last 3 minutes (see [Traffic](Page-Traffic) for how to read it). |
| Internet | WAN link state, IPv4 address, IPv6 address (global scope only), default gateway, and the last outage. |
| Networks | Each network with its VLAN id, gateway address and number of leases. The "Devices" link goes to [Devices](Page-Devices). |
| Services | The services HeliOS watches (DHCP, DNS filter, firewall, NTP, and any optional ones that are installed), each with a green, amber or red dot and a **Restart** button. The header says "All running" or "N failed". Restart runs `systemctl restart <unit>` directly; it is not a pending change. Services that are not installed and not running are hidden. |
| Tailscale | Whether the router's admin exit node is online, its Tailscale address, how many peers are online, and whether the Heliosphere (family exit) container is running. |
| System | Uptime, load average (1, 5, 15 minutes) and memory used with a bar. |
| QoS & UPnP | Whether the smart queue (CAKE) is on and its download and upload rates, and how many UPnP ports are open. "Tune" opens the [QoS](Page-QoS) tab. |

## Gotchas

- "Devices" on the tile counts leases, not devices that answer right now. The Devices tab has the real online state.
- If the router has just started, the Internet tile may say Unknown until the first outage check has run.

## Under the hood

- Page: `viewOverview` in `pkgs/photosphere/photosphere.js:450`. Data comes from `helios-ctl status` (`pkgs/helios-ctl.py:1947`), `reboot-status` (`:1098`) and `hardware` (for the UPS tile).
- Pending bar and Apply dialog: `photosphere.js:341` and `:387`. Backend: `cmd_pending`, `cmd_discard`, `cmd_apply_start`, `cmd_apply_run`, `cmd_confirm`, `cmd_rollback` at `helios-ctl.py:835-974`. The 120 s window is `CONFIRM_SECONDS` (`:65`).
- Outage monitor: `cmd_uplink_check` (`helios-ctl.py:2599`), run by the `helios-uplink` timer in `modules/photosphere.nix`. State is in `/var/lib/helios/uplink.json` and `outages.jsonl`.
- The list of watched services is built in `modules/options.nix` (the `services` list written to `/etc/helios/config.json`).
- Advanced mode: `photosphere.js:2248-2285`.
- The offline demo uses the mock backend in `tests/mock/base1/cockpit.js`, which answers the same `helios-ctl` calls with sample data.
