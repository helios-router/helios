# Heliosphere — remote access (design + contract)

User-facing name: **Remote access**. Heliosphere stays the internal name.

Heliosphere is a NixOS container (`ve-heliosphere`, 10.60.0.2) whose traffic the
host firewall treats as a filtered network: internet only, DNS through
Magnetosphere, never the LAN. Everything below runs inside it, so every way in
inherits that isolation.

## Ways in (any combination)

| Method | For | Listens | Clients land on |
|---|---|---|---|
| Tailscale | devices with Tailscale (default) | tailnet | exit node `heliosphere` |
| WireGuard | phones/laptops without Tailscale | WAN UDP port (default 51820), DNAT to the container | 10.61.0.0/24 |
| OpenVPN | devices or networks that only allow OpenVPN, TCP 443-style fallbacks | WAN UDP/TCP port (default 1194/udp) | 10.62.0.0/24 |
| Proxy (SOCKS5 + HTTP, 3proxy) | apps/TVs at home that can't run a VPN, e.g. to use the way out | chosen home networks only | n/a (per connection) |

Clients of every method get DNS from the router's AdGuard (10.60.0.1) and
can't reach the LAN. All clients look like 10.60.0.2 to AdGuard, so they
share one filtering profile (set it on the client 10.60.0.2 in AdGuard).

The proxy has no encryption, so it's **never offered on the WAN**: its login
would cross the internet in clear text and invite password guessing. It's a
convenience for devices on your own networks, for example to send a TV app
through the way out. Away from home, use WireGuard, OpenVPN or Tailscale.

**Home network access** (`lan`, per way in, default none): Tailscale
(advertised as subnet routes), WireGuard and OpenVPN can each be allowed to
reach chosen home networks. The container enforces it per interface, so
allowing it for one way in doesn't open it for another. The host allows
Heliosphere into the union of those networks. Picking an admin network
gives remote devices everything that network can reach; Photosphere warns
about that.

## Way out (one at a time)

| Mode | How | Config the user provides |
|---|---|---|
| `direct` (default) | through the router's normal internet | — |
| `wireguard` | `wg-quick` with a provider's config (Mullvad, Proton, a VPS…) | `wg-out.conf` |
| `openvpn` | OpenVPN client with a provider's `.ovpn` | `client.ovpn` (+ optional `auth.txt`) |
| `proxy` | `tun2socks`: all container traffic through an upstream SOCKS5/HTTP proxy | host, port, optional user/password |

**Kill switch** (default on): if the way out is down, Heliosphere traffic is
dropped rather than leaking out directly. Only these may leave the container
outside the tunnel:
- DNS to 10.60.0.1
- replies to incoming connections, marked with conntrack mark 0x1
- the tunnel's own packets (fwmark 51820 for wg-quick, 0x2 for OpenVPN and tun2socks)

**DNS filtering through a tunnel**: the host firewall can't see inside the
tunnel. So the container enforces it itself: it redirects port 53 from
clients to 10.60.0.1, and blocks DoT (853) and the known DoH/WARP addresses
(the same list as the host, `pkgs/blocklists.json`).

**Replies to incoming connections**: with a tunnel as the default route,
replies to WireGuard/OpenVPN/proxy clients would leave through the tunnel.
So connections that arrive on eth0 get conntrack mark 0x1. Their replies get
fwmark 0x1, and `ip rule fwmark 0x1 lookup main` sends them back the way they
came.

## Configuration

`site.nix` (infrastructure, rarely changed):
- `helios.heliosphere.enable`: the container
- `helios.heliosphere.tag`: its Tailscale tag
- `helios.heliosphere.authKeyFile`: its Tailscale auth key
- `helios.heliosphere.publicHost` (new): the name or IP that WireGuard and OpenVPN client configs point at. With null, it's the current WAN IP, and Photosphere warns that it may change.

`state.json` (Photosphere → Remote access):

```json
"heliosphere": {
  "ingress": {
    "tailscale": { "enabled": true, "exitNode": true, "hostname": "heliosphere", "lan": [] },
    "wireguard": { "enabled": false, "port": 51820, "lan": [], "tunnel": "full",
                   "keepalive": 25, "mtu": null,
                   "peers": [ { "name": "pixel", "publicKey": "base64=", "ip": "10.61.0.2" } ] },
    "openvpn":   { "enabled": false, "port": 1194, "proto": "udp", "lan": [],
                   "clients": [ { "name": "laptop", "serial": "0A1B…", "revoked": false } ] },
    "proxy":     { "enabled": false, "socksPort": 1080, "httpPort": 3128,
                   "networks": ["iot"], "users": ["tv"] }
  },
  "egress": { "mode": "direct", "killswitch": true,
              "proxy": { "type": "socks5", "host": "proxy.example.net", "port": 1080 } }
}
```

Missing keys take the defaults shown, with Tailscale on and everything else
off, so existing sites keep working. Names: `^[a-z0-9][a-z0-9-]{0,31}$`. Ports
1–65535, all distinct, not 22/53/67/123/443/2586/3493/5353/9090/41641 or a
UPnP port. If the proxy is enabled, it needs at least one user, and at
least one network in `networks`. A non-direct egress mode needs its config
file to exist.

Secrets are never stored in state.json or git. They live under `/var/lib/heliosphere/`, mode 0600 unless noted:

| Path | Contents | In container |
|---|---|---|
| `tailscale/` | Tailscale state + auth key | rw |
| `wireguard/private` | server key (generated in the container) | rw |
| `openvpn/ca/` | CA key and cert, issued-cert index | **not mounted** |
| `openvpn/server/` | `ca.crt server.crt server.key tls-crypt.key crl.pem` | ro |
| `proxy/users` | 3proxy users file, `name:CR:<md5-crypt>` | ro |
| `egress/` | `wg-out.conf`, `client.ovpn`, `auth.txt`, `proxy.json` | ro |

Client private keys (WireGuard peers, OpenVPN clients) are generated by
helios-ctl, returned **once** in the command's output, and never stored.

## helios-ctl commands

| Command | Effect / output |
|---|---|
| `remote` | `{ok, enabled, publicHost, settings, status, dirty}`. `settings` is the resolved heliosphere state. `status` holds per-method live info: `tailscale {online, ip}`, `wireguard {publicKey, peers: [{name, ip, lastHandshake, rx, tx}]}`, `openvpn {clients: [{name, connected}]}`, `proxy {running}`, `egress {mode, configured, up, publicIp}`. Missing info is `null`, never an error. |
| `remote-set <json>` | validate and store `ingress.*` flags and ports, the proxy's networks, each way in's `lan`, Tailscale's `exitNode`/`hostname`, WireGuard's `tunnel` (`full` or `split`)/`keepalive`/`mtu`, and `egress.mode`, `killswitch` and `egress.proxy` |
| `remote-wg-add <name>` | new peer: keypair and next free IP. Returns `{config, qrSvg}`; the private key appears only there |
| `remote-wg-remove <name>` | drop the peer |
| `remote-wg-renew <name>` | new key pair for an existing peer (same address); returns `{config, qrSvg}` once |
| `remote-wg-rotate` | new server key, applied immediately; returns `{publicKey, previousPublicKey, restarted}`. Peers keep their keys and must update the server's public key |
| `remote-wg-import` | server private key on stdin (e.g. moving from another server); same output as rotate |
| `remote-ovpn-add <name>` | creates the PKI on first use (EC P-256 CA, server cert, tls-crypt key), then issues a client cert. Returns `{config}` (an inline `.ovpn`) |
| `remote-ovpn-revoke <name>` | revoke the cert and regenerate `crl.pem` |
| `remote-proxy-user <name>` | password on stdin (≥ 16 chars) → users file; name added to `proxy.users` |
| `remote-proxy-user-remove <name>` | remove the user |
| `remote-egress-upload <wireguard\|openvpn\|auth>` | config on stdin. Validated, and cleaned of `DNS=`, `PostUp/PreUp/PostDown/PreDown`, `up`/`down`/`script-security` and other hook lines; then stored |
| `remote-egress-clear <kind>` | delete that file |

Every state change shows in the pending banner and applies with
confirm/rollback. Secret files change right away, because they hold no
routing decisions. The commit message names what changed, never the secrets.
