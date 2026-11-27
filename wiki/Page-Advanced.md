# Advanced tab

Administration and settings most people never need. This tab is only visible in advanced mode (gear button, see [Header and navigation](Page-Header-and-Navigation)). Cards, top to bottom: Admin account, Passkey sign-in, Services, Maintenance, AI log triage (only while that service is on), System, Backups, Drivers, Security scan.

## Admin account card

The router login you chose in the installer (`helios.admin.user`). Changes here take effect at once: there is nothing to apply.

| Row | Shows |
|---|---|
| Login | The admin user name |
| Password set | When the password was last changed |
| Two-factor sign-in | On or off. "Set up, but not asked for" (with a red warning) means the key exists but the running system version doesn't ask for it, for example after rolling back to a generation from before two-factor sign-in existed |
| SSH keys | How many keys `site.nix` authorizes (`helios.admin.sshKeys`; edit them there) |

Until passkey sign-in is required, the second factor can be a code that Cockpit itself asks for (below). The card recommends [Passkey sign-in](#passkey-sign-in-card) instead; once that's required it takes over and these buttons go away.

* **Change password**: asks for the current password, then the new one (12–128 characters) twice. Used for Cockpit, `sudo` and the console. Your Cockpit session stays signed in. Hidden if the router sets passwords in its Nix configuration (`users.mutableUsers = false`); change them there.
* **Turn on two-factor sign-in**: scan the QR code (or type the key) into an authenticator app, then enter the 6-digit code it shows. Nothing changes until that code matches. You then get five one-use recovery codes, shown once; save or download them. From then on Cockpit asks for a code after the password.
* **Move to a new phone**: asks for a current code from the old phone (or a recovery code), then the same setup with a new key. The old app keeps working until you finish.
* **Turn off**: asks for a current code (or a recovery code); then Cockpit asks for the password only again.

Asking for the current password or code means an unlocked browser or a stolen Cockpit session can't change your sign-in. A recovery code used here is used up, as it would be at sign-in.

Two-factor sign-in covers Cockpit (and so Photosphere). SSH only accepts keys, and the console and Cockpit's administrative access (`sudo`) keep the password alone, so a lost phone can't lock you out of the router itself. The key is stored root-only in `/var/lib/helios/totp/<login>`; deleting that file (over SSH, on the console, or from the installer's [Recovery shell](Installation#recovery-shell)) turns two-factor sign-in off.

At sign-in, Cockpit checks the password first and only asks for the code when the password is right. The code allows 3 tries per 30 seconds; wrong passwords don't count toward that, so nobody can keep you locked out by guessing passwords.

The rescue port's password SSH login (`helios.interfaces.rescueMac`) does not ask for the code either. It needs someone plugged in at the router, who could use the console just as well.

## Passkey sign-in card

**The full walkthrough, with per-device certificate instructions and troubleshooting, is [Passkey Sign-In](Passkey-Sign-In).** This section describes the card.

An optional sign-in page (Authelia) in front of these pages. **Passkeys are HeliOS's recommended second factor**, and the card says so: a FIDO2 key (YubiKey, pico-fido…) or a phone checks its own PIN and your touch, can't be phished, and lets you type the admin password only once. A code from an authenticator app works as a fallback, but it can be phished and you type the password twice (at the gate and in Cockpit).

| How you sign in | At the gate | In Cockpit |
|---|---|---|
| Passkey (recommended) | the key's PIN and a touch | admin password |
| Code (fallback) | admin password, then the 6-digit code | admin password again |
| Rescue port | skipped | admin password only |

While the service is off the card offers **Turn on passkey sign-in** (Authelia downloads at the next Apply, about 50 MB; the installer offers it too when it has internet). Once on, it's a checklist:

1. **The gate knows the admin password.** Copy it once (asks for the current password). Changing the password in the Admin account card keeps both in step.
2. **A certificate your browser trusts.** Browsers only allow passkeys on a page with a trusted certificate (codes don't need one). Two ways:
   * **Your own name (nothing to install).** Set `helios.gate.domain` in `site.nix` to a name under a domain whose DNS is on Cloudflare, or a free deSEC name (`yourname.dedyn.io`, set `helios.gate.acme.dnsProvider = "desec"`). The step then shows **Set Cloudflare/deSEC token** with where to create one (Cloudflare: the "Edit zone DNS" template for that zone; deSEC: Token management, optionally limited to TXT records under `_acme-challenge`). The token is stored on the router only (root-only, never in git or backups). The router gets a Let's Encrypt certificate by writing a DNS record, so nothing is exposed to the internet, and renews it by itself; the router answers that name on your networks. Until the certificate arrives, and if renewal keeps failing (a long outage, a revoked token), the router's own certificate is used, so keep it installed on a device or two as a backup. Passkeys are tied to this name: changing it means registering them again.
   * **The router's own certificate.** **Download certificate** gives the router's certificate authority (Caddy's); install it once per device as a trusted authority. Works with no internet at all.
3. **Passkeys.** **Add passkey** explains the steps: open the sign-in page (`https://<host>.<domain>:9090/auth/`), sign in with the admin password, add a WebAuthn credential under Settings → Two-Factor Authentication, confirm with the one-time code the dialog shows (**Show latest code**), then the key's PIN and a touch. The key must check its PIN (user verification `required`) and store the passkey (discoverable). Register two if you can. Each has **Remove**.
4. **Code from an app (fallback).** **Set up code** shows a QR code; enter a code to check the app has it. **Remove code**.
5. **Try it.** Sign in at the sign-in page with the passkey in a private window before requiring it.
6. **Require it at sign-in.** Only possible once the password is copied and a passkey or code exists. From then on, sign in at `https://<host>.<domain>:9090` (other addresses redirect there). Any code Cockpit itself asked for until now (the Admin account card's) is retired: the gate asks for the second factor instead. **Stop requiring** turns it off again.

Every change asks for the admin password. The last second factor can't be removed while the gate is required. Locked out: use the rescue port, or run `sudo helios-ctl gate-off` over SSH or on the console (or from the installer's [Recovery shell](Installation#recovery-shell): `helios-mount`, `nixos-enter --root /mnt`, then `helios-ctl gate-off`).

Over Tailscale, use the router's `<host>.<domain>` name too (set split DNS for the domain to the router in your tailnet's DNS settings): passkeys are tied to that name.

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

- `pkgs/photosphere/photosphere.js:2316-2362`: `viewAdvanced()`; `:2203-2314` `accountCard`, `currentCodeDialog`, `passwordDialog`, `totpSetup`, `recoveryDialog`; `:1952-1974` `servicesCard`; `:1978-2052` `logwatchCard`; `:2082-2102` `maintenanceCard`; `:1297-1327` Drivers and Security cards.
- `pkgs/helios-ctl.py:4315-4528`: `account`, `account-password`, `account-2fa-begin`, `account-2fa-enable`, `account-2fa-disable`; `modules/airlock.nix:34-50` (Cockpit PAM with `pam_google_authenticator`).
- `pkgs/services.json`: service catalog. `pkgs/helios-ctl.py:1517-1572`: `services`, `service-set`; `modules/options.nix:11-21` defaults.
- `pkgs/helios-ctl.py:1069-1170`: reboot status, `maintenance-set`, `reboot`, `reboot-window`, `reboot-check`; `modules/recovery.nix:47-93`.
- `pkgs/helios-ctl.py:1244-1510`: AI log triage commands; `pkgs/logwatch-models.json` (presets, defaults); `pkgs/logwatch.sh`; `modules/discovery.nix`.
- `pkgs/helios-ctl.py:2863-2900` `driver-set`; `:3300-3330` `vulnix-report`; `modules/rosetta.nix:153-175`.
