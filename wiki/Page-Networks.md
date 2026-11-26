# Networks (Advanced)

This tab is shown only in Advanced mode (gear button), under the Devices tab. It defines your networks: each one is a VLAN with its own subnet, a role, and a set of access switches that decide what devices on it may do. Everything the firewall, DHCP and DNS do per network is generated from this list.

Related pages: [Devices](Page-Devices), [Hardware](Page-Hardware) (which switch ports carry which network), [Filtering](Page-Filtering).

## Default networks

A fresh install has five networks:

| Name | VLAN | Subnet | Role |
| --- | --- | --- | --- |
| trusted | 20 | 10.20.0.0/24 | Trusted |
| family | 30 | 10.30.0.0/24 | Family |
| iot | 40 | 10.40.0.0/24 | IoT |
| strict | 50 | 10.50.0.0/24 | Strict |
| mgmt | 99 | 10.99.0.0/24 | Mgmt |

The list is copied into `state.json` the first time you edit a network. Until then the built-in defaults are used. Each network gets the router at `.1`, fixed IPs for named devices at `.20` to `.99`, and a DHCP pool of `.100` to `.250`.

## The Networks card

A blue note reminds you that each network is a VLAN on the trunk: HeliOS sets up the router side, and your switch ports and Wi-Fi SSIDs must carry the same VLAN ids (the [Hardware](Page-Hardware) tab can write a checklist for that).

| Column | Meaning |
| --- | --- |
| Network | Coloured pill with the label, and the interface name underneath. |
| VLAN | The VLAN id. |
| Subnet | The `/24` subnet, and "IPv6 0x20" style id if IPv6 is on, otherwise "IPv4 only". |
| Role | The role name. "customised" appears if you have changed any access switch from the role's defaults (hover to see which). |
| Access | One badge per access switch that is on, and a "Reaches:" line (every network, a list, or "No other network"). Hover a badge for its description. |
| Devices | How many named devices are on this network. |
| Edit | Opens the editor. **Add network** (top right) opens the same editor for a new network. |

## The "Who can reach whom" card

A grid of networks: rows are where connections start, columns are where they go. Replies to allowed connections always come back.

| Symbol | Meaning |
| --- | --- |
| tick | The row's network may open connections to the column's network. |
| TVs | It may not, except to devices marked "Media device" (the row has "Cast to TVs" on). |
| dash | Not allowed. |
| dot | Same network. |
| Internet column | Tick if the network has internet access. |

The note under the grid adds that admin networks and Tailscale can also manage the router, and that media servers add their own exceptions (see [Devices](Page-Devices)).

## The editor

Saving runs `helios-ctl network-set` and is a pending change until you apply. Errors appear in a red box at the top of the dialog.

| Field | Details |
| --- | --- |
| Label | What people see. Up to 24 characters. If left empty, the interface name is used. |
| Interface name | 1 to 12 lowercase letters or digits, starting with a letter. It becomes the Linux interface name. Not allowed: `wan`, `lo`, `trunk0`, `rescue0`, `tailscale0`, `ifb4wan`, `lan0`, or anything starting with `ve-`. Must be unique. |
| Colour | Used for the pill. Follows the role's colour until you change it yourself. |
| VLAN id | 2 to 4094. Must not be the WAN VLAN (10 unless your site changed it) and must be unique. A new network suggests 70, then 80, 90 and so on, whichever is free. |
| Subnet | The first three octets of a private `/24`, for example `10.70.0`. Must be unique, and must not overlap `10.60.0`, `10.61.0` or `10.62.0`, which the remote access container uses. A new network suggests `10.<vlan>.0`. Changing it moves the fixed IPs of devices on this network, keeping the last number. |
| IPv6 | Off by default. When on, you choose a subnet id from `0x0` to `0xff`. The router takes that `/64` from the `/56` your ISP delegates, advertises it and acts as the IPv6 DNS server. Must be unique. |
| Filtering for unnamed devices | Only shown when profiles exist. Picks the profile that devices you have not named get, or "AdGuard's global settings". See [Filtering](Page-Filtering). Named devices always use their own profile. |
| Role | Radio cards: Trusted, Family, IoT, Guest, Strict, Mgmt. Picking a role resets the access switches below to that role's defaults. |
| Access switches | See below. A "changed" badge marks any switch that differs from the role. Only the differences are saved, so later improvements to a role's defaults still reach your network. |
| Can open connections to | "Every network", or a list of the other networks. |

Buttons: **Add network / Save**, **Cancel**, and for existing networks **Remove network**.

Rules enforced when saving:

- At most 32 networks.
- At least one network must keep admin access, or you would lock yourself out.
- Renaming a network (changing the interface name) follows through to devices, other networks' reach lists, switch port and SSID settings, UPS sharing and the remote access proxy list. It does not update the "home networks" lists of the Tailscale, WireGuard and OpenVPN entry points on the Remote access tab (see Gotchas).

**Remove network** is disabled while the network has named devices ("Move or forget its devices first"). You cannot remove the last admin network or the last network. Removing a network also turns port modes that used it to "unused" and drops SSIDs and other references to it. The confirmation says VLAN and subnet go away when you apply.

## The roles

| Role | Intended for | Admin | Internet | Reaches | Filtered DNS | Notes |
| --- | --- | --- | --- | --- | --- | --- |
| Trusted | Your own devices and servers | yes | yes | every network | no | mDNS, printing, UPnP, cast, tailnet, restrict unnamed |
| Family | Household devices | no | yes | none | yes | mDNS, printing, UPnP, cast, tailnet, restrict unnamed |
| IoT | Gadgets, isolated | no | yes | none | yes | mDNS, forced NTP, tailnet |
| Guest | Visitors | no | yes | none | yes | nothing else |
| Strict | Unknown wired ports | no | yes | none | yes | printing |
| Mgmt | Switch and AP admin pages | yes | no | none | no | tailnet, restrict unnamed |

The role list lives in `pkgs/networks.json`, so a site can add roles.

## The access switches

Each switch is turned into firewall, DNS or discovery settings at the next Apply.

| Switch | What it does |
| --- | --- |
| Admin | Devices here may manage the router (SSH, Cockpit on port 9090, AdGuard Home) and skip the "trap". On every non-admin network, connections to the router's admin ports (22, 23, 80, 443, 2222, 3000, 8080, 8090, 9090, and the notification port) are dropped and logged once per hour per device. DHCP and DNS still work. |
| Internet | May reach the internet. Without it, the network is cut off from the WAN. |
| Reach (Can open connections to) | Which other networks it may start connections to. Replies always return. |
| Filtered DNS | All DNS is forced through AdGuard Home (hard-coded DNS servers are redirected), and DNS-over-TLS, well-known DNS-over-HTTPS addresses and Cloudflare WARP are blocked so filtering cannot be bypassed. |
| mDNS | Joins mDNS reflection between networks that have it, for printer discovery and casting. Reflecting leaks local names into the network, so only enable it where that is wanted. |
| Printing | May use the router's print server (port 631). |
| UPnP | Devices may open ports with UPnP or NAT-PMP. |
| Cast to TVs | May reach devices marked "Media device" on other networks. |
| Forced NTP | Hard-coded time servers are redirected to the router. |
| On tailnet | The network is advertised as a Tailscale subnet route (if enabled in the site configuration). |
| Restrict unnamed | If "Restrict unnamed devices" is turned on in [Devices](Page-Devices), unnamed devices on this network get filtered internet only. |

Anti-spoofing: on non-admin networks, packets whose source address is outside the network's own subnet are dropped.

## Gotchas

- Changing a network's VLAN id or subnet changes what your switch and Wi-Fi must carry. The rebuild applies it, but if a switch port still uses the old VLAN, devices on it lose service. The 120-second confirm window helps here.
- Moving a network's subnet moves named devices' fixed IPs but running devices keep their old lease until it expires or they reconnect.
- Rename does not update the "home networks" lists on the Remote access tab for Tailscale, WireGuard and OpenVPN. A name that no longer exists is silently ignored by the firewall, so the entry point loses access to that network until you tick it again.
- With a Trusted-style role that has "Restrict unnamed" on, make sure you have named your own device before enabling restriction.

## Under the hood

- Page: `viewNetworks` (`pkgs/photosphere/photosphere.js:1112`), editor `editNetwork` (`:1147`), switch labels `ACCESS_UI` (`:1105`).
- Backend: `cmd_networks` (`pkgs/helios-ctl.py:617`), `cmd_network_set` (`:652`), `cmd_network_remove` (`:746`), `clean_access` (`:635`), rename and drop helpers `hardware_rename_network` (`:2666`) and `hardware_drop_network` (`:2684`).
- State key: `networks[]` in `state.json` (`name`, `label`, `vlan`, `prefix`, `ipv6`, `role`, `color`, `access` (overrides only), `defaultProfile`).
- Catalog: `pkgs/networks.json` (roles, switch descriptions, default networks).
- Router side: VLAN interfaces, addresses and IPv6 in `modules/lagrange.nix`, DHCP pools in `modules/galileo.nix`; every firewall rule in `modules/heliopause.nix`; Tailscale routes in `modules/tailscale.nix`.
