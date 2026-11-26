# Advanced tab

Administration and settings most people never need. This tab is only visible in advanced mode (gear button, see [Header and navigation](Page-Header-and-Navigation)). Cards, top to bottom: Services, Maintenance, AI log triage (only while that service is on), System, Backups, Drivers, Security scan.

## Services card

Turn optional parts of HeliOS on or off. Turning a service on downloads its software at the next Apply; turning it off removes it at the next Apply. Both can be rolled back like any change.

**Core: always on** (no switches; a dot shows health: green all running, red if any failed, grey otherwise; hover for each unit):

| Core service | What it is |
|---|---|
| Networks and DHCP | VLANs, addresses, local names (Kea, systemd-networkd) |
| Firewall | Heliopause: isolation between networks, the trap, DNS enforcement |
| DNS and filtering | AdGuard Home |
| Time (NTP) | chrony |
| Management | Cockpit, SSH, config backups and change history |
| Health checks | Outage monitor, hardware watchdog, hardware detection, traffic stats |

**On by default / Optional extras** (each has a switch, a health dot, a download size hint, and a short description):

| Service | Group | Tab |
|---|---|---|
| Notifications | on by default (small) | [Notifications](Page-Notifications) |
| Weekly vulnerability scan | on by default (small) | none |
| Print server | optional (medium) | [Printing](Page-Printing) |
| Remote access | optional (medium) | [Remote access](Page-Remote-Access) |
| AI log triage | optional (large) | this tab |

Each row shows "site.nix default" when you have not touched it, or "set here" with a **reset** button that returns it to the `site.nix` default. A **Settings** link appears for services that are on and have a tab. Toasts say "apply to download and start it" or "apply to stop and remove it".

**Switched elsewhere** (links only): QoS ([QoS](Page-QoS)), UPnP ([UPnP](Page-UPnP)), UPS and neighbour discovery (drivers on [Hardware](Page-Hardware)), and Tailscale admin access, which is set in `site.nix` only because it needs an auth key and switching it off could cut off your remote admin access.

Gotcha: the tab of a service that is switched on but not applied yet shows (for Remote access and Printing) a "Not started yet" card.

## Maintenance card

Header: **Reboot needed** (yellow) or **Up to date**.

Most updates take effect when you apply. Only a new kernel (or its modules or boot image) waits for a reboot; NixOS does not patch the kernel live.

| Item | Meaning |
|---|---|
| Running kernel / Installed kernel | What is booted and what is installed. |
| Next reboot window | The next time the window opens, or "None". |
| Reboot automatically when an update needs it | Off by default. When on, the router reboots in the window below, **only if a reboot is needed**, never while a change is being applied (it skips and sends a notification, then tries again next window), and a window missed while the router was off is not caught up. A notification is sent first. |
| Days | Mon to Sun checkboxes; at least one (default Sun). |
| Time | 24-hour time, default 04:00, router's local time. |
| **Save** | Pending change ("Reboot window: sun 04:00" or "Reboot window off"). |
| **Reboot now** | Confirms, then reboots immediately. Refused while an apply is running or waiting for confirmation. |

Gotcha: "Next reboot window" and the Overview banner are computed from the saved settings, so they can show a time before you have applied; the timer itself only exists after Apply. The router also checks once a day and sends one notification per new kernel.

## AI log triage card

Only shown while the AI log triage service is on. A local language model reads warning-and-worse log lines on a schedule and sends a notification if something needs attention. Known-critical patterns (disk errors, out of memory, link down, failed services, any SSH login, trap hits) alert immediately without the AI. Repeats are collapsed and a pattern already seen in the last 7 days is not sent to the model again. The log text is treated as data; the model can only choose whether to alert and write a short summary. Medium severity goes to the informational topic, high severity to the urgent one, low is ignored. Needs [Notifications](Page-Notifications) to send anything.

Header status: **Running on schedule**, **Paused** or **Not running**. A grey line tells you this router's RAM, CPU cores, whether it has a GPU, and the recommended model (the largest that fits in half of RAM minus 2 GB; without a GPU and under 8 cores, no bigger than Medium; or "Too little memory for a local model: use another machine").

If paused: a warning with the reason and when it resumes, and **Resume now**.

### Model

| Choice | Notes |
|---|---|
| Tiny 0.6B, Small 1.7B (default), Medium 4B, Large 8B, Extra large 14B | Each shows download size and RAM needed. Badges: **recommended**, **downloaded**, **too big here** (needs more than half of spare memory). |
| From site.nix | The model file set in `site.nix`. |
| Custom file | An absolute path ending in `.gguf`, no `..`. Required when chosen. |

**Download selected model**: confirms, then downloads from Hugging Face in the background at lowest priority (needs the file size plus 512 MB free in `/var/lib/llm`), shows a progress bar, and checks the file against its published checksum; a mismatch deletes it. Failures show "Last download failed: ...". If the chosen model is not downloaded, "triage waits until it is". A list of downloaded `.gguf` files has **Delete**; you cannot delete the one in use while the service is on.

### Where it runs

| Choice | Notes |
|---|---|
| site.nix default (default) | Shows the current value (the `site.nix` default backend is the GPU). |
| Built-in GPU | Vulkan, fastest when the router has an iGPU. |
| CPU | Works everywhere; slower, competes with routing. |
| Another machine | A llama.cpp server set by `helios.logwatch.remoteUrl` in `site.nix`. |

### Schedule and limits

| Field | Default | Range |
|---|---|---|
| Check every (minutes) | 10 | 5 to 240 |
| CPU threads | 2 | 1 to 64 |
| CPU limit (%) | 150 | 25 to 6400 (100% = one core) |
| Memory cap (MB) | empty (what the model needs) | 512 and up |
| Keep the model loaded | off | Faster checks but holds memory all the time; off loads it for each check. |

### Protection

| Field | Default | Meaning |
|---|---|---|
| Pause when the router is busy | on | Checked every minute. |
| CPU pressure (%) | 40 | 5 to 95; the card shows the current value. |
| Memory pressure (%) | 20 | 5 to 95. |
| Pause for (hours) | 6 | 1 to 72. |

When pressure is over a limit, triage and any model download stop, you get a notification, and everything resumes after the pause (another notification). Always on regardless: lowest CPU and disk priority, memory capped, first thing stopped if memory runs out.

Buttons: **Save** (pending), **Reset to defaults** (confirm; removes your settings).

## System card

Hostname, domain and uptime, plus links: Cockpit's System overview, Logs, Services and Software updates (within Cockpit), and AdGuard Home (`http://<admin gateway>:3000`, admin networks only, new tab).

## Backups card

Same as on [History](Page-History): **Back up now**, last backup time and whether it was pushed to a git remote.

## Drivers card

Built-in drivers with switches. Always-on ones are locked.

| Driver | Default | Notes |
|---|---|---|
| Manual checklist | always on | Describe a switch or access point; HeliOS writes the setup checklist. |
| Router ports | always on | The router's extra Ethernet ports or switch chip as access, trunk or WAN. |
| Neighbour discovery (LLDP) | on | Listens for switches and access points on the trunk. Read-only. |
| UPS (Network UPS Tools) | off | Battery status, alerts and clean shutdown. |

Changes are pending. See [Hardware](Page-Hardware).

## Security scan card

Result of the weekly vulnerability scan (if the service is on): total findings, counts by severity (critical 9.0 and up, high 7.0 to 8.9, medium 4.0 to 6.9, low), and a table of the top 10 (package, version, CVE link, score). If it has not run: "The weekly scan hasn't run yet." New critical/high findings send an urgent notification; otherwise a summary is sent.

## Under the hood

- `pkgs/photosphere/photosphere.js:2201-2247`: `viewAdvanced()`; `:1953-1977` `servicesCard`; `:1979-2054` `logwatchCard`; `:2082-2103` `maintenanceCard`; `:1298-1329` Drivers and Security cards.
- `pkgs/services.json`: service catalog. `pkgs/helios-ctl.py:1517-1572`: `services`, `service-set`; `modules/options.nix:11-21` defaults.
- `pkgs/helios-ctl.py:1069-1170`: reboot status, `maintenance-set`, `reboot`, `reboot-window`, `reboot-check`; `modules/recovery.nix:47-93`.
- `pkgs/helios-ctl.py:1244-1510`: AI log triage commands; `pkgs/logwatch-models.json` (presets, defaults); `pkgs/logwatch.sh`; `modules/discovery.nix`.
- `pkgs/helios-ctl.py:2863-2900` `driver-set`; `:3300-3330` `vulnix-report`; `modules/rosetta.nix:153-175`.
