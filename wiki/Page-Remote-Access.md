# Remote access tab

Remote access lets you use your home internet connection, with your DNS filtering, from anywhere. It runs in a separate container on the router (internally called Heliosphere, address 10.60.0.2). The firewall treats that container as a filtered network: internet only, DNS through the router's filter, and **never your home networks** unless you allow specific ones per way in.

This is an **advanced** tab (see [Header and navigation](Page-Header-and-Navigation)) and appears only when the Remote access service is on. If it is off, the tab shows a card with a **Turn on** button; turning it on is a pending change and the software is downloaded at the next Apply.

Design notes for contributors are in `docs/heliosphere.md`.

## Basics

- **Ways in** (use any combination): Tailscale, WireGuard, OpenVPN, Proxy.
- **Way out** (one at a time): where traffic from remote devices leaves for the internet: directly, or through a VPN or proxy.
- All remote devices look like 10.60.0.2 to the DNS filter, so they **share one filtering profile**. Set it in AdGuard on the client `10.60.0.2` (see [Filtering](Page-Filtering)). The blue notice at the top of the tab says this.
- Every change here is a pending change, except stored secrets (see below).

## Tailscale card

For devices that run Tailscale. Nothing to forward on your router. The container needs a Tailscale auth key in the file set by `helios.heliosphere.authKeyFile` in `site.nix`.

| Item | Default | Meaning |
|---|---|---|
| Enabled | on | Turns the Tailscale way in on or off. |
| Status | | Online / Offline and the Tailscale address, read live from the container. A dash if unknown. |
| Exit node | on | Devices can send all their internet traffic through home, with your DNS filtering. In the Tailscale app, pick the host name below as the exit node. |
| Name on your tailnet | `heliosphere` | Lowercase letters, digits and dashes, up to 63 characters. |
| Home networks they can reach | none | Networks shared as subnet routes. Approve them once in the Tailscale admin console (or with autoApprovers in your tailnet policy). None selected: internet only. |

**Save** stores the card. Saving with Tailscale enabled, no exit node and no home network is refused: it would give devices nothing.

## WireGuard card

For phones and laptops without Tailscale. HeliOS forwards the UDP port from the internet to the container for you.

| Item | Default | Meaning |
|---|---|---|
| Enabled | off | Turns WireGuard on. |
| UDP port | 51820 | 1 to 65535. |
| Keepalive (seconds) | 25 | 0 turns it off (0 to 600). 25 keeps phones reachable behind NAT. |
| MTU | automatic | Empty, or 1280 to 1500. Try 1380 if pages stall. |
| Connect to | | `host:port` that client configs point at. If `helios.heliosphere.publicHost` is not set in `site.nix`, it shows "your current internet address (may change)" with a warning, and configs use the router's current WAN IP. |
| What goes through home | Everything | **Everything**: all internet traffic goes through home, filtered (best on public Wi-Fi). **Only home networks**: just the chosen networks plus the router's DNS; the rest uses the device's own connection. "Only home networks" needs at least one home network selected. |
| Home networks they can reach | none | Per way in, enforced inside the container. |

Tunnel, keepalive, MTU and port are written into each device's config. They apply to configs you create or renew after saving; existing configs keep their old values.

### Server key

Shows the server's **public** key (Copy button), or "Not created yet: enable WireGuard and apply." The private key never leaves the router.

| Button | What it does |
|---|---|
| **Rotate key** | After a warning, makes a new server key pair and restarts WireGuard immediately (not a pending change). Every peer must then change the `[Peer] PublicKey` in its config, or use "New config". Use it if the key may have leaked. |
| **Import key...** | Paste an existing server private key (44 characters, from another WireGuard server) so existing devices keep working. Same immediate effect and result as rotate. |

### Peers

Table: Name, Address (10.61.0.x), Last connected ("never" or "3m ago", from the last WireGuard handshake), Down / up (bytes counted by WireGuard since it started).

- **Add peer**: asks for a name (lowercase letters, digits, dashes, up to 32). HeliOS creates a key pair and the next free address and shows the device config and a QR code **once**. The private key is not stored. Save the file or scan the code before closing the dialog; if you lose it, use New config. Adding a peer needs WireGuard to have started once: "enable it and apply first".
- **New config**: new key pair, same address, for a lost or reset device. The old config stops working after Apply.
- **Remove**: the device can no longer connect after Apply.

## OpenVPN card

For devices or networks that only allow OpenVPN. Use TCP on a common port to get through strict firewalls.

| Item | Default | Meaning |
|---|---|---|
| Enabled | off | |
| Port | 1194 | |
| Protocol | UDP | UDP or TCP. |
| Home networks they can reach | none | All internet traffic goes through home; these networks are reachable too. |

Clients: Name and serial, Status (**Connected** / **Not connected** / dash, or a **revoked** badge), and a **Revoke** button. **Add client** asks for a name; the first client also creates the certificate authority (an EC P-256 CA, server certificate and a shared `tls-crypt` key), which can take a moment. You get an inline `.ovpn` file once (download button), including the client's private key. Revoke makes the certificate stop working for good (the revocation list is regenerated at once; the state change applies at Apply).

Gotcha: client files embed the host, port and protocol at the time you create them. If you change the port or protocol later, existing files do not follow; revoke and add new clients.

## Proxy card (SOCKS5 and HTTP)

For apps and TVs at home that cannot run a VPN, for example to send them out through the way out below. It reaches the internet only, never your home networks, and is **never offered on the internet**: it has no encryption.

| Item | Default | Meaning |
|---|---|---|
| Enabled | off | Needs at least one user and at least one network, or saving fails. |
| SOCKS5 port | 1080 | |
| HTTP port | 3128 | |
| Who can connect | none | Home networks whose devices may use the proxy. |
| Status | | Running / Not running, shown next to Save. |

In the app, set the proxy to `10.60.0.2:<port>` (the page shows the exact lines), with one of the users. Users: **Add user** asks for a name and a password of at least 16 characters (at most 128, repeated to confirm). The password is hashed and stored right away (not a pending change); the name appears in the list. **Remove** deletes the login. You cannot remove the last user while the proxy is on.

## Way out card

Where remote devices' traffic leaves for the internet. One at a time. The header shows the mode, "down" if it is not working and the public address seen from outside.

| Mode | How |
|---|---|
| Direct (default) | Through the router's normal connection. |
| WireGuard VPN | A provider's `wg-quick` config (or your own server). |
| OpenVPN | A provider's `.ovpn` file. |
| Upstream proxy | A SOCKS5 or HTTP proxy you have access to; all container traffic goes through it. |

**Kill switch** (default on): if the way out goes down, remote traffic is dropped instead of leaking out through your normal connection. It only matters when a VPN or proxy is selected.

Per mode:

- **WireGuard / OpenVPN**: a status line ("Configured" / "Not configured yet" and "Up, public address ..." or "Down"), a text box and file picker, **Upload config**, and **Remove config**. The file is stored on the router (mode 0600) and never shown again. Lines that could run commands or change DNS are removed: for WireGuard `DNS`, `PreUp/PostUp/PreDown/PostDown`, `SaveConfig`, `Table`; for OpenVPN `up`, `down`, `script-security`, `plugin`, `push` and similar. OpenVPN files that point to other files (`ca`, `cert`, `key`, ...) are refused: paste the contents inline. OpenVPN also gets **Provider login (optional)** with **Save login** and **Remove login**.
- **Upstream proxy**: type (SOCKS5 or HTTP), host, port, and optional login with **Save login** / **Remove login**. Host, type and port are saved with **Save way out**; the login is stored separately.
- Saving a WireGuard or OpenVPN mode fails with "upload the ... configuration first" if its file does not exist. The status ("Configured status shows once this mode is saved") only reflects the mode that is currently applied.
- A stored file cannot be removed while the way out is set to that mode: switch to Direct first.

The way out's public address is found by the container asking an external address-lookup service (`api.ipify.org`) through the tunnel whenever the page loads.

## What the router does

- Host firewall: WireGuard and OpenVPN ports on the WAN are redirected to the container. The proxy ports are opened only for the networks you chose.
- Container firewall: remote clients' DNS is redirected to the router's filter; DNS-over-TLS (853) and the known DNS-over-HTTPS and WARP addresses are blocked, because the host cannot see inside a tunnel.
- Replies to incoming connections leave the way they came (marked packets), so a tunnel as default route does not break WireGuard/OpenVPN/proxy clients.
- Subnets: WireGuard 10.61.0.0/24, OpenVPN 10.62.0.0/24 (these and 10.60.0.0/24 cannot be used for your own networks).

## Rules the page enforces

- Names: `^[a-z0-9][a-z0-9-]{0,31}$`.
- The four ports (WireGuard, OpenVPN, SOCKS5, HTTP) must all be different, even for ways in that are off. They cannot be 22, 53, 67, 123, 443, 2586, 3493, 5353, 9090 or 41641, or a port currently opened by UPnP (see [UPnP](Page-UPnP)).
- Home networks picked must exist. Picking an **admin** network shows a warning: remote devices would reach everything there as if at home.

## Secrets and what is immediate

Stored under `/var/lib/heliosphere/`, never in `state.json` or git: Tailscale state, WireGuard server key, the OpenVPN CA and server files, proxy users, and way-out files. The following happen immediately, not through the pending bar: rotate/import key, uploading or removing way-out files, adding or removing a proxy user. Peer and client lists, ports and modes are pending. Discard does not undo the immediate ones.

## State

```json
"heliosphere": {
  "ingress": {
    "tailscale": { "enabled": true, "exitNode": true, "hostname": "heliosphere", "lan": [] },
    "wireguard": { "enabled": false, "port": 51820, "tunnel": "full", "keepalive": 25, "mtu": null, "lan": [],
                   "peers": [ { "name": "pixel", "publicKey": "base64=", "ip": "10.61.0.2" } ] },
    "openvpn":   { "enabled": false, "port": 1194, "proto": "udp", "lan": [],
                   "clients": [ { "name": "laptop", "serial": "0A1B", "revoked": false } ] },
    "proxy":     { "enabled": false, "socksPort": 1080, "httpPort": 3128, "networks": ["iot"], "users": ["tv"] }
  },
  "egress": { "mode": "direct", "killswitch": true, "proxy": { "type": "socks5", "host": "proxy.example.net", "port": 1080 } }
}
```

## Gotchas

- First WireGuard use: enable, Save, Apply, then Add peer.
- The tab loads status once when opened (no automatic refresh); reload to see new handshakes.
- If no public host name is set in `site.nix`, client configs contain your current WAN IP, which can change.
- An OpenVPN file with a bare `auth-user-pass` line keeps it as is; only `auth-user-pass <file>` is rewritten to use the saved login.

## Under the hood

- `pkgs/photosphere/photosphere.js:1563-1605`: dialogs (`formDialog`, `secretDialog`); `:1607-1858`: `viewRemote()`.
- `pkgs/helios-ctl.py:3340-3445`: defaults, `hs_resolve()`, endpoint helpers; `:3447-3565` `remote-set` validation; `:3567-3631` `hs_status()` and `remote`.
- `pkgs/helios-ctl.py:3634-3748`: WireGuard peers, rotate, import; `:3751-3887` OpenVPN PKI, add, revoke; `:3890-3925` proxy users.
- `pkgs/helios-ctl.py:3930-4046`: way-out upload cleaning and clear; `:4049-4090` `describe_remote()`.
- `modules/heliosphere.nix`: the container, ways in, way out, kill switch, DNS rules. `modules/heliopause.nix:21-31,145,164-167,185-187`: host rules and WAN port redirects.
- `modules/options.nix:52-60,176-190`: defaults and assertions.
- `docs/heliosphere.md`: design and command contract.
