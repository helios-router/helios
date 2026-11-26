# Devices

The Devices tab lists every device HeliOS knows about: devices you have named, and devices that have a DHCP lease but have not been named yet. Naming a device gives it a fixed IP address, a filtering profile, a local name (`name.home.arpa`, using your configured domain) and optional media permissions.

Related pages: [Networks](Page-Networks) (Advanced), [Filtering](Page-Filtering) (Advanced), [Overview](Page-Overview).

## How a device gets on the list

- **Named devices** come from the router's settings file (`state.json`). They stay on the list whether or not they are online.
- **Unnamed devices** appear when they get a DHCP lease from the router. They are shown with a **NEW** badge and the name they call themselves (their DHCP hostname). The red number on the Devices tab counts them.
- A device is matched by its **MAC address**. Phones that use a random (private) MAC per Wi-Fi network will show up as a new device on each network.

Online status is read from the router's neighbour table. A device counts as online while its entry is reachable or stale, so a device that just left can look online for a short while.

The list refreshes every 15 seconds.

## The restriction banner

Above the table, one of three messages appears. They control whether unnamed devices get full access.

| State | What you see | What it means |
| --- | --- | --- |
| Off (default) | Blue banner "New devices get full access" with a **Restrict unnamed devices...** button. | An unnamed device gets the same access as any named device on its network until you notice it. |
| Waiting | Amber banner "Unnamed-device restriction is waiting". | The setting is on, but it does not start until at least one device on an admin network (such as Trusted) is named. This stops you locking yourself out. |
| Active | A grey line "Unnamed devices on ... only get filtered internet until you name them" with a **Turn off** link. | Restriction is enforced. |

**Restrict unnamed devices...** opens a dialog that lists the unnamed devices that would be restricted now and warns you to name the device you are using first. Pressing the button runs `helios-ctl restrict-unnamed on`, which is a pending change: it takes effect when you apply. **Turn off** does the same with `off`.

What restriction does on the router (after apply):

- It covers only networks whose **Restrict unnamed** access flag is on (see [Networks](Page-Networks)). With the built-in roles that is Trusted, Family and Mgmt.
- An unnamed device on those networks can get DHCP, DNS (forced through AdGuard Home) and NTP from the router, and filtered internet if the network has internet access. It cannot reach other networks or the router's admin pages.
- Tailscale and the rescue port are never restricted.
- The dialog says the apply rolls back by itself after two minutes if you cannot confirm it.

The banner's matching is by MAC address, so it covers IPv4 and IPv6.

## The toolbar

| Control | What it does |
| --- | --- |
| Network chips | **All** or one network. Each chip shows how many devices are on it. A device is counted on the network of its current lease if it has one, otherwise on its configured network. |
| New only | Shows only unnamed devices. |
| Search | Matches name, DHCP hostname, current IP, fixed IP, MAC and notes. |

The footer shows "N of M devices - K online". Your filter settings are remembered until you reload the page.

## The table

| Column | Meaning |
| --- | --- |
| Pause button | Only on named devices whose network has internet access. A green play icon means the device has internet; a red pause icon means it is paused. Clicking toggles it immediately (see Pause below). |
| Device | A status dot (green online, grey offline, red paused), the name, badges, and a second line with the notes (or the DHCP hostname). Unnamed devices show their hostname in italics and "Not named yet". |
| Network | The network pill. If a named device is currently leasing on a different network than the one you reserved for it, a line "reserved on <network>" shows in amber. |
| IP address | The current IP. Below it: "fixed" if the current IP is the reserved one, "fixed 10.x.x.x" if the reservation differs, or "DHCP" for unnamed devices. |
| MAC | The hardware address. |
| Filtering | The label of the device's filtering profile. For unnamed devices this always shows the "default" profile's label, even if their network has a different default profile (see Gotchas). |
| Seen | "Now" if online, otherwise how long ago the lease started, or a dash. |
| Edit / Name it | Opens the editor. |

### Badges

| Badge | Meaning |
| --- | --- |
| NEW | Not named yet. |
| PAUSED | The device is cut off from the internet and other networks (see Pause). |
| RESTRICTED | Unnamed, and the restriction above is active for its network. |
| Media | Marked as a media device (TV, streaming stick, speaker). |
| Media server | Marked as a media server. Hover to see its ports. |

### Pause

The Pause button runs `helios-ctl block <mac> on|off`. It adds or removes the device's fixed IP in the firewall's `cutoff` set, which blocks its traffic and kills its open connections. It takes effect at once, **without Apply**, and the change is committed to the router's history straight away (unless other edits are pending, in which case it is saved with the rest). The block survives reboots because paused devices are rebuilt into the firewall from `state.json`.

Pausing needs a named device (it needs the fixed IP). If the live firewall update fails, a red message says so; the paused flag is still saved.

## The editor

Open it with **Name it** (unnamed device) or **Edit** (named). Saving runs `helios-ctl device-set` and is a pending change until you apply.

| Field | Details |
| --- | --- |
| Name | Lowercase letters, digits and dashes, up to 32 characters, starting with a letter or digit. Must be unique among devices and cannot start with `net-` (reserved for network defaults). The router's own name and `heliosphere` are left out of the suggestions, but saving does not check for them, so avoid using them. The device answers as `name.<your domain>` from every network. Under the field are one-click name suggestions: planets and moons for most networks, Kuiper-belt objects for networks whose role uses that theme (IoT and Mgmt by default). |
| Network | Which network the device belongs to. The fixed IP is reserved on this network. |
| Filtering profile | One of your profiles (see [Filtering](Page-Filtering)). New devices start with their network's default profile, or "default" if none is set. Changing the network on a new device changes the preselected profile only while you have not picked one yourself. |
| Media device | Not shown on admin networks (they already reach everything). For TVs, streaming sticks, consoles and speakers. A media device can reach media servers and phones on casting-enabled networks can reach it. If any media servers exist you can choose "every media server, including future ones" or "only these". |
| Media server | For a device that runs Plex, Jellyfin, Emby, a music server or similar. Media devices may reach it on the ports you list, and nothing else of theirs can reach it. |
| TCP ports / UDP ports | Up to 16 each, 1-65535. A server needs at least one port. Preset buttons fill common ports (Jellyfin, Emby, Plex, Navidrome, Lyrion, Audiobookshelf, MPD, HDHomeRun, Channels DVR, Tvheadend). |
| Whole networks that may use it too | Entire networks that may reach the server's ports, such as Family so phones and laptops can stream. Networks that already reach everything are not listed. |
| Notes | Free text, up to 200 characters. |

Buttons: **Save device / Save**, **Cancel**, and for named devices **Forget device**.

- **Fixed IP.** You do not choose the address. HeliOS picks the first free address from `.20` to `.99` on the network (for example 10.20.0.20 to 10.20.0.99). It keeps the same address when you edit, and picks a new one if you change the network. If a network has no free address left, saving fails. DHCP hands out addresses from `.100` to `.250`.
- **Forget device** removes the name and reservation (and removes it from any media device's "only these servers" list). If the device still has a lease it comes back as NEW.
- Renaming a media server updates the media devices that list it.

### What the router does after Apply

- Kea DHCP gets a reservation (MAC to fixed IP and hostname) for every named device.
- `/etc/hosts` (read by AdGuard Home) gets `name.<domain>` for each device.
- The firewall gets the media pinholes and the `media_clients` set.
- AdGuard Home gets one client per device carrying its profile (synced right after the rebuild).

## Gotchas

- Edits only take effect after **Apply**, except Pause.
- A device that already holds a lease may keep its old address until the lease renews (leases last 2 hours) or it reconnects, even after you reserve a fixed IP.
- Naming a device you are currently using, while restriction is on, matters: name it first.
- Unnamed devices show the "default" profile label in the Filtering column, but if their network has a default profile set under [Filtering](Page-Filtering), that is what AdGuard actually applies to them.
- "Forget device" does not remove its AdGuard client immediately; it goes at the next Apply (or "Sync profiles now").

## Under the hood

- Page: `viewDevices` (`pkgs/photosphere/photosphere.js:556`), editor `editDevice` (`:676`), presets `MEDIA_PRESETS` (`:654`).
- Backend: `cmd_devices` (`pkgs/helios-ctl.py:414`), `cmd_device_set` (`:501`), `cmd_device_remove` (`:570`), `cmd_block` (`:779`), `cmd_restrict_unnamed` (`:462`), `restrict_status` (`:451`).
- State keys in `state.json`: `devices[]` (`mac`, `name`, `vlan`, `ip`, `profile`, `blocked`, `media`, `mediaServers`, `mediaServer`, `notes`) and `restrictUnnamed`.
- Router side: DHCP reservations and `/etc/hosts` in `modules/galileo.nix`; pause set, media pinholes and restriction chains in `modules/heliopause.nix`.
