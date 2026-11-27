# Header and navigation

Everything that stays on screen no matter which tab you open: the title, the tabs (or the phone dropdown), the theme and gear buttons, the "pending changes" bar, the apply dialog and the reboot notice. This page explains how each one works.

## Title bar

| Item | What it shows |
|---|---|
| Sun logo and title | "HeliOS on *hostname*" (the router's host name). The browser tab title uses the same text. |
| Subtitle | "Connecting..." until the first answer arrives, then "Up 3d 4h" (router uptime). It is read once when the page loads and is not refreshed, so reload the page to update it. |

## Tabs

Tabs are two rows high. The main row is always visible. A second, smaller row sits under some main tabs and holds the **advanced** tabs. These only appear once you turn on advanced mode with the gear button.

| Main tab | Advanced tab under it |
|---|---|
| Overview | Hardware |
| Devices (with a badge) | Networks |
| Traffic | Filtering |
| Notifications | Remote access |
| QoS and UPnP (side by side) | Printing |
| History | Advanced |

See [Overview](Page-Overview), [Hardware](Page-Hardware), [Devices](Page-Devices), [Networks](Page-Networks), [Traffic](Page-Traffic), [Filtering](Page-Filtering), [Notifications](Page-Notifications), [Remote access](Page-Remote-Access), [QoS](Page-QoS), [UPnP](Page-UPnP), [Printing](Page-Printing), [History](Page-History) and [Advanced](Page-Advanced).

Details:

- **Devices badge**: the number of devices currently on the network that you have not named yet. It is hidden when the number is 0. The badge is read once at page load.
- **Remote access and Printing** are hidden even in advanced mode when their service is switched off (Advanced, Services). Open their address directly and you get a card with a "Turn on" button instead. See [Remote access](Page-Remote-Access) and [Printing](Page-Printing).
- **Address**: the tab is kept in the URL (`#qos`, `#history`, ...). An unknown name falls back to Overview. You can bookmark a tab.
- **Active tab** is underlined.
- **Empty second rows** are hidden: if none of the advanced tabs under a column is visible, that row disappears.

### Phone dropdown

When the window is 1000 pixels wide or narrower, the tab row is replaced by a single drop-down list (the phone's own picker). It lists exactly the tabs that are visible right now, so advanced tabs show up in it after you press the gear. The Devices entry includes the count, for example "Devices (3)". Choosing an entry changes the page.

## Theme button

The sun/moon button switches between light and dark.

| Situation | Result |
|---|---|
| You never pressed it | Photosphere follows Cockpit's own style setting; if that is "auto", it follows the operating system's dark/light preference, and changes live when that changes. |
| You pressed it | Your choice is stored in the browser and wins from then on. The icon shows the theme you would switch to. |

The choice is stored per browser (not on the router). If the browser blocks storage, the button still works but is forgotten on reload. Charts redraw when the theme changes.

## Gear button (advanced mode)

The gear shows or hides the advanced tabs.

- It is stored in the browser, per browser and per router address. It is not a router setting, and other people using the UI are not affected.
- Default is off.
- If you turn it off while on an advanced tab, you are taken to Overview.
- If you open an advanced tab by address while advanced mode is off, you see a card "Advanced setting" with a "Show advanced settings" button. Pressing it turns the gear on.
- Advanced mode only hides tabs. It does not change what the router does.

## "Administrative access needed" notice

Photosphere runs its commands as root through Cockpit's superuser channel. If Cockpit refuses (administrative access is not switched on in Cockpit's top bar), a yellow notice appears under the header. It disappears after the next command that works.

## Pending changes bar

Most changes in Photosphere are **saved but not live**. Saving only edits the router's settings file (`state.json`). A yellow bar appears under the header until you apply or discard.

| Part | What it does |
|---|---|
| Title | "1 pending change" or "N pending changes". |
| List | A plain-English line per change, such as "Add earth (family, default)" or "Change qos settings". The first 6 are listed, then "...and N more". |
| **Discard** | After a confirmation, throws away every edit since the last apply (it restores the settings file from the last saved version). Nothing on the router changes. |
| **Apply changes** | Starts the apply, and opens the apply dialog. |

The bar refreshes every 5 seconds and after every save, and it is hidden when there are no changes.

What it does **not** cover:

- Some actions happen immediately and never show up as pending: closing UPnP ports, printer test pages, pausing and cancelling print jobs, rotating or importing the WireGuard server key, creating or removing stored secrets (VPN configs, logins, proxy passwords), starting a model download, backing up, rebooting, changing the admin password, turning two-factor sign-in on or off.
- Pausing and resuming a device is committed right away when nothing else is pending. If other edits are already pending, the pause is part of them instead.
- Discard only reverts the settings file. Secret files that were written immediately are not undone.

Some pages (Remote access, Advanced, Printing) say "apply to activate" in their notices: that is this bar.

## Apply dialog

Pressing **Apply changes** runs a rebuild of the router's configuration in the background (it keeps going if you close the page) and then gives you a time-limited chance to confirm. The dialog opens by itself if you load Photosphere while an apply is running or waiting for confirmation, and it polls every second.

| Stage | What you see | What is happening |
|---|---|---|
| Running | "Rebuilding with your changes. This usually takes under a minute", the list of changes, a progress bar and the live log. | `nixos-rebuild switch` in a background unit. When it succeeds, HeliOS also syncs AdGuard client profiles and removes printers that no longer exist in the settings. |
| Keep these changes? | "Changes are live." and a countdown from 2:00 with **Keep changes** and **Revert now**. | The new configuration is already running. A timer will roll back automatically after 120 seconds. |
| Failed | "The rebuild failed. Nothing on the router changed. Your edits are still pending." plus **Close**. | The edits stay pending so you can fix or discard them. |
| Rolled back | "Rolled back." plus a message and **Close**. | The previous system generation is running again. |
| Done | Dialog closes, page redraws. | Changes were kept. |

- **Keep changes** stops the rollback timer and saves the settings as a new entry in [History](Page-History). The entry's title is the first change, plus "(+N more)".
- **Revert now**, or letting the countdown run out, runs `nixos-rebuild switch --rollback` and restores the settings file to the last saved version. **Your pending edits are discarded in a rollback**, unlike after a failed rebuild.
- The countdown is the safety net for changes that cut you off (for example a wrong VLAN): if you cannot reach the page to confirm, the router returns to the last good state by itself. Closing the page during the countdown therefore also ends in a rollback unless you confirm in time.
- Only one apply can run at a time. "Nothing to apply" is the error if there are no changes.

## Reboot notice

Most updates take effect when you apply. A new kernel, kernel modules or boot image only run after a reboot. HeliOS compares what is booted with what is installed.

- **Overview banner** (Overview tab only): when a reboot is waiting, a blue banner says "Reboot to finish an update", names the running and installed kernel, and shows either "It reboots by itself *Sun 04:00*" (if a reboot window is set) or "Everything else is already updated." Buttons: **Reboot now**, and **Schedule...** (only shown when no window is set; opens Advanced).
- **Reboot now** asks for confirmation ("Everything on your network loses internet for a minute or two"). It is refused while an apply is running or waiting for confirmation.
- **Notification**: once a day HeliOS checks, and sends one notification per new kernel ("Reboot needed") through [Notifications](Page-Notifications), if that service is on.
- The schedule itself lives on the Advanced tab: see [Advanced](Page-Advanced), "Maintenance".

## Messages ("toasts")

Short messages appear at the bottom: green for success (about 3.5 seconds), red for errors (about 7 seconds).

## Under the hood

- `pkgs/photosphere/index.html:15-50`: header, tabs and second-row structure, `#tab-menu`, gear and theme buttons, `#access` alert, `#pending` bar.
- `pkgs/photosphere/photosphere.js:127-152`: theme (`photosphere-theme` in localStorage, falls back to Cockpit's `shell:style`).
- `pkgs/photosphere/photosphere.js:154-170`: `api()` runs `helios-ctl` with Cockpit's superuser channel and shows the access notice.
- `pkgs/photosphere/photosphere.js:341-362`: `refreshPending()`, discard and apply buttons (5 s poll).
- `pkgs/photosphere/photosphere.js:387-436`: `openApplyModal()`, 1 s poll of `apply-status`.
- `pkgs/photosphere/photosphere.js:2247-2300`: advanced mode (`photosphere-advanced`), `ADV_TABS`, `applyAdvanced()`, `syncTabMenu()`, `VIEWS`, `rerender()`.
- `pkgs/photosphere/photosphere.css:357-368`: phone dropdown breakpoint (max-width 1000px).
- `pkgs/photosphere/photosphere.js:2065-2081`: `rebootNow()` and the Overview reboot banner.
- `pkgs/helios-ctl.py:835-858`: `pending`, `discard`, `restore`. `:295-345` `describe_change()` builds the list.
- `pkgs/helios-ctl.py:875-980`: `apply-start`, `apply-run` (`CONFIRM_SECONDS = 120`, line 65), `confirm`, `rollback`, `apply-status`, `apply-dismiss`.
- `pkgs/helios-ctl.py:1069-1170`: `reboot_status()`, `reboot`, `reboot-window`, `reboot-check`.
- `modules/recovery.nix:82-85`: the daily `helios-reboot-check` timer.
