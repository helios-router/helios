# History tab

History answers three questions: what changed on the router and when (and can I go back), has the internet gone down, and is my configuration backed up. It has no settings of its own other than **Restore** and **Back up now**.

The page is loaded once when you open it; reload to refresh. (After Back up now it refreshes itself.)

## Changes card

Heading note: "Every apply, pause and backup is a commit in /etc/nixos."

HeliOS keeps its settings file (`state.json`) in a git repository in the router's configuration folder. Each time you press **Keep changes** after an apply, one entry is added. The list shows up to the 60 newest.

| Item | Meaning |
|---|---|
| Title | What changed, in plain words: the first change in that apply and "(+N more)", or "Pause earth", "Resume earth", "Backup: AdGuard settings", "Initial snapshot of /etc/nixos". |
| Small line | The short commit id, the date and time, and who made it (normally `helios`). |
| **Current** pill | On the newest entry. |
| **Restore** | On older entries. |

### Restore

1. Press **Restore**. A dialog says the settings will go back to how they were at that point, as pending changes.
2. After confirming, the old settings file is loaded as a **pending change**. Nothing on the router changes yet.
3. Review the pending bar (see [Header and navigation](Page-Header-and-Navigation)), then **Apply** to make it live (with the usual confirm-or-roll-back countdown) or **Discard** to cancel.

Gotchas:

- Restore brings back the **whole** settings file, not only the parts the dialog names (devices, QoS, UPnP). That includes networks, hardware, remote access, printers, services, reboot window and log AI settings.
- It does not bring back secret files (VPN keys, proxy passwords, stored way-out configs) or AdGuard's own settings, which are not in the settings file.
- If the older version has no settings file you get "that version has no state.json".
- "Current" means the newest entry, even if you have pending edits.

## Internet outages card

An outage monitor checks every 30 seconds by pinging 1.1.1.1, 9.9.9.9 and 8.8.8.8. If all three fail twice in a row it records an outage, counted from the first missed check. When the internet returns, the outage is saved (start, end, length) and a notification "Internet is back" goes out through [Notifications](Page-Notifications), if that service is on.

| Part | Meaning |
|---|---|
| Seven-day strip | One box per day for the last 7 days (local time of your browser), labelled with the day. It shows the total outage time that day, or a dash if there was none. Days with outages are highlighted; hover for the count and total. |
| List | The 8 newest outages: red icon, start time and duration. |
| Empty text | "No outages recorded." |

The page asks for the last 100 outages. The monitor can only see the router's own view of the internet: it records nothing while the router itself is off.

## Backups card

Config backup runs automatically every night at about 03:17 (with up to 10 minutes of random delay; if the router was off, it runs soon after boot).

| Item | Meaning |
|---|---|
| **Back up now** | Runs the backup immediately. Toast: "Backup saved" or "Nothing changed since the last backup". |
| Last backup | Time and how long ago, or "Never". |
| Off-site copy | "Pushed to git remote" (green), "Push failed" (red), or "No git remote set" (yellow). |
| Includes | Says "/etc/nixos and AdGuard's settings". |

What a backup does: it copies AdGuard Home's settings file (which you change in AdGuard's own web interface, so Nix does not know about it) into `backups/adguard` in the configuration repository and commits it. If the repository has a git remote, it then pushes the current branch there, which also sends all your earlier change entries.

The same backup button exists on [Advanced](Page-Advanced).

## System generations card

Lists the newest 6 NixOS system builds (generation number and date). The one marked **Running** is the generation the router's system profile currently points to, which is the latest applied build, not necessarily the one you booted into (after a kernel update, the booted kernel can be older until you reboot).

Below: "Older generations are also in the boot menu. `nixos-rebuild switch --rollback` returns to the previous one." This is an advanced recovery tool for use at the router's console or over SSH, not something Photosphere does for you (the apply dialog's rollback uses it automatically).

## Under the hood

- `pkgs/photosphere/photosphere.js:987-1028`: `viewHistory()`.
- `pkgs/helios-ctl.py:2379-2395`: `history` (`git log -n 60`, `nix-env --list-generations` for generations, outages tail of 100, `backup.json`).
- `pkgs/helios-ctl.py:850-860`: `restore` (writes `git show <rev>:state.json` over the working copy).
- `pkgs/helios-ctl.py:927-938`: `confirm` creates the commit; `:779-801` pause/resume commit.
- `pkgs/helios-ctl.py:2397-2420`: `backup`.
- `pkgs/helios-ctl.py:2365-2377` and `:2599-2630`: outage log (`/var/lib/helios/outages.jsonl`) and `uplink-check`.
- `modules/photosphere.nix:44-69`: `helios-uplink` (every 30 s) and `helios-backup` (03:17) timers.
