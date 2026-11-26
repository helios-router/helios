# Installation

There are two routes: the installer ISO, which wipes a disk and sets everything up, or adding HeliOS to a NixOS machine you already have. Both end with the same layout: `/etc/nixos` as a git repository holding `site.nix` and `state.json`.

Keep that repository private. It will hold your MAC addresses, names and keys.

## Before you start

* A small x86-64 box with one NIC for the switch trunk. A second NIC is optional and becomes the rescue port.
* A managed switch that supports VLANs, and VLAN-aware access points if you use Wi-Fi. HeliOS does not configure them.
* A modem or ONT. It sits in its own VLAN (default 10) on the switch.
* Internet access during install (the install downloads HeliOS and NixOS packages).
* Optional: a Tailscale account (or Headscale server), a tagged auth key.

## Route 1: the installer ISO

Get the ISO:

* build it: `nix build .#installer-iso` in a checkout of the repository; or
* download the `helios-installer-iso` artifact from a CI run on `main` (kept 14 days). Pull request runs do not build it.

Write it to a USB stick, boot the router from it, plug a cable with DHCP and internet into any port, and run:

```
sudo helios-install
```

The installer asks, in order:

| Step | Question | Default |
|---|---|---|
| Disk | Which disk (shown with size, model, transport) | none |
| Filesystem | `ext4` or `btrfs` (btrfs gets subvolumes `@`, `@home`, `@nix`, `@log`, zstd compression) | `ext4` |
| Swap | Swap partition size in GiB | `0` |
| Name | Router host name (lowercase letters, digits, dashes) | `helios` |
| Domain | Local DNS domain | `home.arpa` |
| Admin | Admin login name (not `root`) | `admin` |
| SSH key | One public key line, or empty | empty |
| Trunk | The port cabled to the switch trunk, chosen from a list with MAC addresses | none |
| WAN VLAN | Switch VLAN the modem's port is in | `10` |
| Software | Tailscale (and an optional Headscale URL, which must start with `https://`), Notifications, Print server, Remote access, Log AI | Tailscale and Notifications yes, others no |

It then prints a summary. Nothing is written until you type the disk's name to confirm. After that it:

1. wipes the disk and creates an EFI partition (1 GiB), optional swap, and the root filesystem, mounted under `/mnt`;
2. runs `nixos-generate-config`, then installs `flake.nix`, `site.nix`, `state.json` and `tailnet-policy.hujson` from `templates/` into `/mnt/etc/nixos`, filling in your answers;
3. makes `/etc/nixos` a git repository with a first commit;
4. runs `nixos-install` from that flake (no root password is set);
5. asks you to set the admin user's password, used for `sudo` and for logging in to Cockpit.

Then remove the stick and reboot. The installed router follows `github:helios-router/helios` for updates. The boot loader is systemd-boot.

## Route 2: an existing NixOS machine

### With a flake

In `/etc/nixos`:

```
nix flake init -t github:helios-router/helios
```

This writes `flake.nix`, `site.nix`, `state.json`, `configuration.nix`, the tailnet policy and the Caddyfile. Then:

1. Edit `site.nix` and fill in every `CHANGE-ME`: `helios.admin.user`, `helios.interfaces.trunkMac`. Find the MAC with `ip link`.
2. Keep the configuration name in `flake.nix` (`nixosConfigurations.helios` by default) equal to `helios.hostName`. Photosphere runs a plain `nixos-rebuild switch`, which picks the configuration by host name.
3. Make sure a `hardware-configuration.nix` exists in `/etc/nixos` (the template imports it).
4. Put the directory under git: `git init && git add -A && git commit -m "HeliOS site"`. Flakes only see tracked files.
5. `sudo nixos-rebuild switch`.

### Without a flake

```
cd /etc/nixos
git clone https://github.com/helios-router/helios
cp helios/templates/{configuration.nix,site.nix,state.json} .
$EDITOR site.nix          # fill in every CHANGE-ME
git init && git add -A && git commit -m "HeliOS site"
sudo nixos-rebuild switch
```

`templates/configuration.nix` imports `./hardware-configuration.nix`, `./helios/modules` and `./site.nix`, enables systemd-boot and sets `system.stateVersion = "25.11"`. Check these against your machine. `helios/` is its own git checkout; Photosphere commits only `state.json` and `backups/` in `/etc/nixos`.

## Set up the switch and access points

The router carries every network as a tagged VLAN on one cable. Your switch must match.

| Where | Setting |
|---|---|
| Modem's port | Untagged in the WAN VLAN (`helios.interfaces.wanVlan`, default 10). |
| Router's trunk port | Tagged with every network's VLAN id (defaults: 20, 30, 40, 50, 99) and with the WAN VLAN. |
| Access points' ports | Tagged with the VLANs their SSIDs use. Map each SSID to a network. |
| Other ports | Untagged (access) in the VLAN of the network that device belongs on. |

Use [Page-Hardware](Page-Hardware) to describe your switch and access points; HeliOS writes a per-device checklist you follow in the device's own web page. The VLAN ids must match the ones on the [Networks](Page-Networks) page.

Optional extras:

* **Rescue port.** Set `helios.interfaces.rescueMac` to a spare NIC. It gets `192.168.255.1/24` (change with `rescueAddress`), and is the only place SSH password login is allowed. Plug in a laptop at `192.168.255.2` if the trunk or switch ever fails.
* **Extra router ports.** If the router has more NICs or a switch chip, map them on the Hardware page as access ports, trunks or the WAN port. The trunk NIC and ports join a VLAN-aware bridge, `lan0`.

## First boot

1. **AdGuard Home.** Open its setup wizard (port 3000 on an admin network, for example `http://10.20.0.1:3000` with the default networks) and create the admin login. Let DNS listen on all interfaces; Heliopause restricts who can reach it. Then create `/var/lib/helios/adguard.json`:
   ```
   {"url":"http://127.0.0.1:3000","user":"<your user>","password":"<your password>"}
   ```
   Without it, Photosphere cannot sync per-device filtering profiles.
2. **Tailscale.** Put a tagged auth key in `/var/lib/tailscale/authkey`, and merge `templates/tailnet-policy.hujson` into your tailnet policy (replace the example emails). The router advertises `tag:helios`, the Remote access container `tag:heliosphere`.
3. **Photosphere.** From a device on an admin network (Trusted or Mgmt by default), open `https://<host>.<domain>:9090` or `https://<admin gateway>:9090`. Log in as your admin user and turn on "Administrative access" in Cockpit's top bar; Photosphere needs it. Choose HeliOS in the menu.
4. **Name your devices** on the [Devices](Page-Devices) page. Unnamed devices on Trusted, Family and Mgmt are limited to filtered internet once at least one device on an admin network is named.
5. **Notifications.** Open [Notifications](Page-Notifications) and follow "Subscribe on your phone" in the ntfy app.
6. Optional: set QoS speeds ([QoS](Page-QoS)), review [UPnP](Page-UPnP).

If you use the `funnel` or `proxy` exposure for notifications, or Remote access, see [Configuration](Configuration) for the options and the tailnet policy template for the matching rules.

## Headscale

To use your own control server instead of Tailscale's, set in `site.nix`:

```
helios.tailscale.loginServer = "https://headscale.example.com";
```

* The value must start with `https://`. It applies to the router and to the Remote access container.
* `helios.solarWind.exposure = "funnel"` needs Tailscale's own servers, so the build fails with a Headscale server; use `local` or `proxy`.
* The installer asks for this URL when you enable Tailscale.
* `templates/tailnet-policy.hujson` is written for Tailscale's policy format. Check which of its parts your server supports, and keep the tags `helios.tailscale.tag` and `helios.heliosphere.tag` allowed for your auth keys.

## What to check afterwards

* [Overview](Page-Overview): internet online, services all running.
* Reach the router by SSH from an admin network and by the rescue port if you set one.
* Run an apply once ([How It Works](How-It-Works)) and confirm it, so you know the confirm-or-rollback flow before you need it.
* Back up the `/etc/nixos` repository somewhere private. The nightly backup pushes to a git remote if one is configured there.
