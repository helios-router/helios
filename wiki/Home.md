# HeliOS

HeliOS is a router operating system built on NixOS. It turns a small x86-64 box and a VLAN-capable managed switch into a router that:

* splits the home into separate networks (VLANs), each with a role that decides what it may reach;
* generates the nftables firewall from those roles;
* filters DNS per device with AdGuard Home;
* gives remote access through Tailscale, WireGuard or OpenVPN in an isolated container;
* sends push notifications from a self-hosted ntfy server;
* is managed from a web page (a Cockpit plugin called Photosphere), with automatic rollback if a change locks you out.

The router is described by two files you own, `site.nix` and `state.json`, kept in your own private git repository. Nothing household-specific lives in the HeliOS repository itself.

## Who it is for

* People comfortable with NixOS, git and a command line who want a home router they can read, version and roll back.
* People who already have, or will buy, a managed switch that supports VLANs. HeliOS configures the router side only; it does not configure the switch or access points. It can write a checklist for them (see [Page-Hardware](Page-Hardware)).
* Not a click-and-forget appliance. The README states that the project is young, was written with AI assistance and reviewed by the maintainer, and that you should read what you deploy and keep a way back (rollback, the rescue port).

## What you need

* A small x86-64 machine with one NIC cabled to a managed switch (router-on-a-stick). A second NIC is optional and becomes an out-of-band rescue port.
* NixOS 25.11 or newer (the flake follows `nixos-25.11`).
* Optional: a Tailscale account or your own Headscale server; a GPU or iGPU for AI log triage; a domain and a server only if you put notifications behind your own reverse proxy.

## Parts

| Name | What it is | Module |
|---|---|---|
| Heliopause | nftables firewall, DNS hijack and bypass blocking | `heliopause.nix` |
| Magnetosphere | DNS filtering with AdGuard Home | `magnetosphere.nix` |
| Heliosphere | remote access container (shown as "Remote access") | `heliosphere.nix` |
| Photosphere | the web UI, a Cockpit plugin, plus `helios-ctl` | `photosphere.nix`, `pkgs/` |
| Solar Wind | ntfy push notifications (shown as "Notifications") | `solar-wind.nix` |
| Voyager | WAN edge: QoS and UPnP / NAT-PMP | `voyager.nix` |
| Hubble | hardware watchdog and traffic statistics | `hubble.nix` |
| Discovery | optional AI log triage | `discovery.nix` |

Supporting modules: `lagrange.nix` (VLANs, mDNS, NTP), `galileo.nix` (Galileo: DHCP and local names), `airlock.nix` (admin user, SSH), `tailscale.nix`, `pioneer.nix`, `rosetta.nix`, `recovery.nix`, `options.nix`. See [How It Works](How-It-Works) for each.

## Wiki map

| Page | Read it to |
|---|---|
| [How It Works](How-It-Works) | understand the architecture: files, modules, networks and roles, the apply and rollback flow |
| [Configuration](Configuration) | look up every `helios.*` option, every `state.json` key and every catalog file |
| [Installation](Installation) | install from the ISO or by hand, set up the switch, first boot, Headscale |
| [Passkey Sign-In](Passkey-Sign-In) | the optional sign-in page: passkeys (recommended) or a code on top of the admin password, certificates, setup, lockouts |
| [Updates and Security](Updates-and-Security) | update a router; understand CI, the PR guard and vulnerability reporting |
| [Development](Development) | work on the repository: layout, tests, demo, adding drivers, profiles and services |

Photosphere pages, one per tab:

| Page | Page | Page |
|---|---|---|
| [Header and Navigation](Page-Header-and-Navigation) | [Overview](Page-Overview) | [Hardware](Page-Hardware) |
| [Devices](Page-Devices) | [Networks](Page-Networks) | [Traffic](Page-Traffic) |
| [Filtering](Page-Filtering) | [Notifications](Page-Notifications) | [Remote Access](Page-Remote-Access) |
| [QoS](Page-QoS) | [UPnP](Page-UPnP) | [Printing](Page-Printing) |
| [History](Page-History) | [Advanced](Page-Advanced) | |

A read-only demo of the web UI with made-up data is published from the `gh-pages` branch (linked from the README).

## Reporting problems

* Documentation errors and bugs: open an issue.
* Security problems: report privately, not in a public issue. See [Updates and Security](Updates-and-Security).
