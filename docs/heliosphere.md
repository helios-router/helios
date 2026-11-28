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
Heliosphere into the union of those networks: it sees every client as
10.60.0.2, so only the container can tell the ways in apart. Its firewall
accepts replies up front and checks the side that opened a connection on
every packet, so taking a network away from one way in also cuts that way's
open connections, even while another way in keeps the network. (An Apply that
changes Heliosphere also restarts the container, which empties its connection
table; the rules don't rely on that.) Picking an admin network
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

This covers connections that are already open, too. The container checks every
packet a client sends against the kill switch, so when the tunnel's routes or its
device go away, an open TCP or UDP connection is dropped rather than following the
main table's default route out of eth0. Only replies are accepted without that
check. (Masquerading on the tunnel already stops such connections. The kernel drops
a masqueraded connection whose output interface changes. The rule order doesn't
rely on that.) The host is a second line: it accepts nothing from the container that
doesn't come from 10.60.0.2, which tunnel-NATed packets don't.

**IPv4 only**: the container refuses to forward IPv6, and refuses its own IPv6
on eth0 and the tunnel. The DNS redirect, the DoH and WARP sets and the kill
switch only know IPv4, and an upstream tunnel with IPv6 would otherwise carry
clients' IPv6 (for example Tailscale exit-node traffic) past them. Clients fall
back to IPv4. The host drops IPv6 from the container as well.

**DNS filtering through a tunnel**: the host firewall can't see inside the
tunnel. So the container enforces it itself: it redirects port 53 from
clients to 10.60.0.1, and blocks DoT (853) and the known DoH/WARP addresses
(the same list as the host, `pkgs/blocklists.json`).

DNS keeps working while the way out is down, because it goes to the router.
That also means clients' DNS questions leave through the router's own internet
connection, not through the tunnel: the ISP can see which names remote clients
look up, even with a VPN way out.

**Replies to incoming connections**: with a tunnel as the default route,
replies to WireGuard/OpenVPN/proxy clients would leave through the tunnel.
So connections that arrive on eth0 get conntrack mark 0x1. Their replies get
fwmark 0x1, and `ip rule fwmark 0x1 lookup main` sends them back the way they
came.

Replies to WireGuard and OpenVPN clients themselves (10.61/10.62) take the main
table's connected routes: wg-quick adds `lookup main suppress_prefixlength 0`
before its tunnel table, and the proxy way out (tun2socks) adds the same rule at
priority 190. Without it, those replies would follow table 100 back into the proxy.

### Marks and routing rules

Every mark the container's firewall accepts, where it comes from, and what it does.
None is derived from packet contents, so a client can't choose one. A process
can set a socket mark only with CAP_NET_ADMIN, and only the root daemons below do.

| Mark | Set by | Meaning, where it's accepted |
|---|---|---|
| conntrack 0x1 | `mark_in` (prerouting): a **new** connection arriving on eth0, that is, to the container itself: WireGuard/OpenVPN transport the host forwards from the WAN, proxy users at home | Copied to the packet mark (below) for the container's replies on those connections |
| packet 0x1 | `mark_out` (route hook), from conntrack 0x1 | Rule 90 sends the reply back via `main` (eth0), past any tunnel; the kill switch (output) lets it out. A proxy user's *outgoing* proxied connection is a new connection of the container's own, without the mark, so it still takes the tunnel |
| packet 0x2 | OpenVPN's `mark 2` (egress config) and tun2socks' `-fwmark 2`, on their own transport sockets | tun2socks' rule 200 (`not fwmark 0x2`) keeps its transport out of table 100; the kill switch lets it out on eth0 |
| packet 51820 (0xca6c) | wg-quick, on its own UDP socket | wg-quick's `not fwmark 51820` rule; the kill switch lets it out on eth0 |
| packet 0x80000 (mask 0xff0000) | tailscaled, on its own transport | Tailscale's own routing rules; the kill switch lets it out on eth0. Clients' exit-node traffic is forwarded, not tailscaled's own, so it goes through the forward chain and its kill switch, which accepts no mark |
| conntrack 0x8xxxxxxx | each accepting rule in `forward` (`tagOf`) | The rule that allowed a connection; replies and ICMP errors pass only while it's still in the ruleset |
| conntrack 0x7fffffff | `forward`, on every packet from the opening side, before the rules | "Revoked" until a rule re-tags it |

| Rule (priority) | Added by | Purpose |
|---|---|---|
| 80: `to 10.60.0.0/24 lookup main` | `helios-policy-routing` | The host (DNS, the router) stays reachable whatever the tunnel does |
| 85: `to <home network> lookup main` | `helios-policy-routing`, one per network any way in may reach | Home networks past the tunnel. Routing only: the forward chain decides which way in may use it |
| 90: `fwmark 0x1 lookup 90` | `helios-policy-routing` | Replies to connections that came in on eth0, via the host (table 90 holds only that default route: `main` has OpenVPN's redirect-gateway routes) |
| 190: `lookup main suppress_prefixlength 0` | tun2socks (proxy way out) | Specific `main` routes (the clients' own subnets) before the tunnel; never `main`'s default route |
| 200: `not fwmark 0x2 lookup 100` | tun2socks | Everything else into the proxy tunnel (table 100) |
| 190 / 200: wg-quick's own pair (`main suppress_prefixlength 0`, `not fwmark 51820 lookup 51820`) | wg-quick (WireGuard way out); moved to 190/200 once it's up, since wg-quick puts them in front of every rule | Everything else into the WireGuard tunnel |
| OpenVPN | `redirect-gateway def1`: `0/1` and `128/1` via tun-out in `main` | Everything else into the OpenVPN tunnel |

When a tunnel goes away, its routes go with it (wg-quick also removes its rules).
tun2socks' rules 190/200 stay, harmless: table 100 is empty without its device,
so the lookup falls through to `main`, whose default route is eth0, where the kill
switch drops client traffic. `helios-policy-routing` deletes its rules before
adding them, and `ip rule add` refuses an exact duplicate, so restarts don't pile
rules up. `tests/vm/killswitch.nix` checks the rule list is the same before and
after restarting each tunnel.

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
| `openvpn/server/` | `ca.crt server.crt server.key tls-crypt.key crl.pem`; folder 0700 and `crl.pem` 0400, owned by `heliosphere-ovpn` (uid/gid 1194), the account OpenVPN drops to; it re-reads the CRL after that | ro |
| `proxy/users` | 3proxy users file, `name:CR:<md5-crypt>`; 0400, owned by `heliosphere-proxy` (uid/gid 1195), the account 3proxy runs as | ro |
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
| `remote-egress-upload <wireguard\|openvpn\|auth>` | config on stdin (UTF-8, no control characters). Validated, and cleaned of `DNS=`, `PostUp/PreUp/PostDown/PreDown`, `Table`, `up`/`down`/`script-security`, `engine` and other hook lines; OpenVPN directives that name a file or load code (`ca /path`, `config`, `iproute`, `tmp-dir`, `pkcs11-providers`…) are refused. OpenVPN files are read the way OpenVPN reads them (lines of at most 255 bytes, its quoting, inline blocks ending at a line starting with `</tag>`) and stored canonically quoted. Fuzzed in CI: [tests/fuzz](../tests/fuzz/README.md) |
| `remote-egress-clear <kind>` | delete that file |

Every state change shows in the pending banner and applies with
confirm/rollback. Secret files change right away, because they hold no
routing decisions. The commit message names what changed, never the secrets.
