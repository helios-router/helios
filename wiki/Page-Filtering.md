# Filtering (Advanced)

This tab is shown only in Advanced mode (gear button), under the Traffic tab. It manages **filtering profiles**: named sets of DNS-filtering rules (ads and trackers, malware, adult content, Safe Search, blocked services). Each named device uses one profile, and each network can have a default profile for devices that are not named yet.

The filtering itself is done by AdGuard Home, which runs on the router and answers DNS for every network. HeliOS keeps AdGuard's "clients" in step with your profiles. Block lists, upstream DNS servers and the query log are managed in AdGuard Home itself, not here.

Related pages: [Devices](Page-Devices) (choose a profile per device), [Networks](Page-Networks) (the "Filtered DNS" switch and the default profile per network).

## Cards on this tab

### AdGuard Home

| Item | Meaning |
| --- | --- |
| Status | "Running" (with AdGuard's version, and "protection paused" if protection is switched off inside AdGuard), "Not reachable", or "HeliOS has no AdGuard login yet". |
| Warning box | Shown when there is no login: create `/var/lib/helios/adguard.json` with AdGuard's URL, user and password so HeliOS can sync profiles. |
| Open AdGuard Home | Opens AdGuard's own web page in a new tab, at `http://<gateway of your first admin network>:3000`. It works only from admin networks. |
| Sync profiles now | Runs `helios-ctl adguard-sync` immediately. The result ("N added, N updated, N removed") appears as a message. |

"Sync profiles now" reads your current settings, including edits you have not applied yet, so it can push a pending change to AdGuard early. A sync also runs automatically after every Apply and every rollback.

### Filtering profiles

The table lists every profile.

| Column | Meaning |
| --- | --- |
| Profile | Label, description and id (the id is what devices refer to). |
| Blocks | Badges for each switch that is on, plus "N blocked services". "AdGuard's global settings" if the profile defers to AdGuard, and "nothing blocked" if everything is off. |
| Used by | How many devices use it, and which networks use it as their default (for example "Family default"), or "unused". |
| Edit | Opens the editor. **Add profile** (top right) opens it for a new profile. |

The profiles a site starts with:

| Id | Label | What it does |
| --- | --- | --- |
| admin | Unfiltered | No filtering. For your own devices and servers. |
| adult | Standard | Blocks ads, trackers and malware. No content limits. |
| kid | Kids | Standard plus adult-content blocking, Safe Search and YouTube Restricted Mode. |
| default | AdGuard defaults | Uses whatever AdGuard's global settings say. This is what unknown devices get unless their network sets a default. |

Once you edit anything, your list replaces these defaults.

### Defaults for unnamed devices

One row per network, each with a drop-down: **AdGuard's global settings** or any of your profiles. Changing it saves immediately (a pending change) with `network-set`. Devices you have not named yet get their network's default. Named devices always use their own profile.

## The profile editor

| Field | Details |
| --- | --- |
| Name | The label people see, up to 32 characters. |
| Id | Lowercase letters, digits and dashes, starting with a letter, up to 32. Filled in from the name until you edit it. Renaming the id moves devices and network defaults along. |
| Description | Up to 160 characters. |
| Use AdGuard's global settings | The profile defers entirely to AdGuard Home's own settings. The switches and services below are ignored. |
| Ads and trackers | Block ads, trackers and anything on your block lists. |
| Malware and phishing | Block known malware and phishing sites (AdGuard's browsing security). |
| Adult content | Block adult content (AdGuard's parental control). |
| Safe Search | Force Safe Search on Google, Bing, DuckDuckGo, Pixabay and Yandex. |
| YouTube Restricted | YouTube Restricted Mode. |
| Blocked services | Whole services to block (for example YouTube, TikTok, Roblox), picked from AdGuard's own list. Search, click to add, click the cross to remove. Up to 100. If AdGuard cannot be reached, the list cannot be shown and you type service ids separated by commas instead. If a profile blocks no services it uses AdGuard's global blocked-services list. |

A new profile starts with Ads and trackers and Malware and phishing on.

Buttons: **Add profile / Save**, **Cancel**, and **Remove profile**. Removal is blocked while a device or a network default still uses the profile ("Remove (in use)"), and you cannot remove the last profile. There can be at most 32 profiles.

Saving runs `helios-ctl profile-set` and is a pending change until you apply.

## What the router does after Apply

1. The rebuild finishes, then HeliOS signs in to AdGuard Home with the login in `adguard.json`.
2. For every named device it creates or updates an AdGuard client named after the device, identified by its fixed IP and MAC address, carrying the profile's settings.
3. For every network with a default profile it creates a client called `net-<network>` identified by the whole subnet. AdGuard prefers the exact device match, so named devices keep their own profile.
4. Clients that HeliOS created earlier but no longer wants are deleted. Clients you made by hand in AdGuard are left alone.

## Gotchas

- Filtering only applies to DNS that reaches AdGuard. On networks with the **Filtered DNS** switch on (see [Networks](Page-Networks)), the firewall redirects all DNS to AdGuard and blocks DNS-over-TLS, known DNS-over-HTTPS servers and Cloudflare WARP, so devices cannot bypass it. On networks without it, a device that sets its own DNS server is not filtered.
- Profile changes need Apply (or **Sync profiles now**) to reach AdGuard.
- AdGuard's global settings are the fallback. Check them in AdGuard if devices on the "default" profile are filtered differently than you expect.
- The profile list is also used by the Devices editor. A profile id that no longer exists makes a device show the raw id instead of a label.
- AdGuard's own settings (block lists, upstreams) are backed up nightly into the router's git history.

## Under the hood

- Page: `viewFiltering` (`pkgs/photosphere/photosphere.js:1868`), editor `editProfile` (inside it), summary badges `PROFILE_UI` (`:1858`).
- Backend: `cmd_profiles` (`pkgs/helios-ctl.py:1810`), `cmd_profile_set` (`:1823`), `cmd_profile_remove` (`:1872`), `adguard_status` (`:1791`), `adguard_sync` (`:1010`), `profile_settings` (`:983`, the mapping to AdGuard per-client settings), `cmd_adguard_sync` (`:1899`).
- State keys: `profiles[]` (`name`, `label`, `desc`, `global`, `filtering`, `safeBrowsing`, `parental`, `safeSearch`, `youtubeRestricted`, `blockedServices`), each device's `profile`, and each network's `defaultProfile`.
- Defaults and switch descriptions: `pkgs/profiles.json`. The AdGuard login file is `/var/lib/helios/adguard.json`; the list of clients HeliOS manages is `adguard-managed.json` in the same folder.
- Router side: `modules/magnetosphere.nix` (AdGuard Home), DNS redirect and bypass blocks in `modules/heliopause.nix`, bypass address lists in `pkgs/blocklists.json`.
