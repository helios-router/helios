#!/usr/bin/env python3
"""Heliosphere (remote access) regression tests, in throwaway network namespaces.

A model of the remote-access container and the router around it, built from veth
pairs. The container's nftables chains copy what modules/heliosphere.nix generates
(same order, same matches), and the host's copy the relevant part of Heliopause:

  T  "tailscale0"  100.64.0.0/24, fd7a::/64  Tailscale clients
  W  "wg-in"       10.61.0.0/24              WireGuard clients
  O  "tun-in"      10.62.0.0/24              OpenVPN clients
  C  the container: eth0 10.60.0.2 to the host, and the egress tunnel (a veth to
     N, named wg-out or tun-out) routed the way wg-quick, OpenVPN or tun2socks set it up
  H  the host (router): ve-heliosphere 10.60.0.1 (DNS server), "trusted" LAN, "wan"
  L  a home network (10.20.0.0/24): one server
  N  the internet: 203.0.113.10, 1.1.1.1 (in the DoH set), 8.8.8.8, 2001:db8:ff::10.
     It sees traffic through the tunnel from 10.99.0.2 and through the router's WAN
     from 192.0.2.254, so every server log says which way a packet came.

Checked for the old container ordering ("established" accepted first, no IPv6
rule) and the current one:
  - kill switch: open TCP and UDP connections, and new ones, never leave through
    eth0 once the tunnel is gone. The tunnel goes away two ways: its routes go but
    the device stays (an OpenVPN reconnect with persist-tun), and the device goes
    (wg-quick down, tun2socks or OpenVPN exiting). Then the tunnel comes back.
  - per-way-in home-network access: removing it from one way in cuts that way's
    open connections and new ones, while another way in keeps its access.
  - IPv6 from clients doesn't get round the IPv4-only DNS redirect, the DoH set
    or the kill switch.
  - DNS is always redirected to the router, also while the tunnel is down.
    DoT, DoH and WARP are blocked for every way in.
  - legitimate traffic: replies, home-network access, the host, recovery.

Needs root (CI) because it turns on IP forwarding inside its namespaces; without
it (a rootless container with read-only /proc/sys) it reports SKIPPED, or fails
when HELIOS_NETNS_REQUIRE_FORWARD=1. Exit status 0 = every check passed for the
current ordering; "VULNERABLE" lines for the old ordering are expected and shown,
not an error. Everything lives in namespaces that vanish when it exits.

The real Apply restarts the container (nixpkgs containers: restartIfChanged), which
empties its connection table too. These tests reload the rules without a restart,
so the rules themselves are what's tested.
"""
import os
import subprocess
import sys
import tempfile
import time

PY = sys.executable
if os.environ.get("HELIOS_NETNS") != "1":
    os.environ["HELIOS_NETNS"] = "1"
    flags = "-n" if os.geteuid() == 0 else "-rn"
    os.execvp("unshare", ["unshare", flags, PY, "-I", os.path.abspath(__file__)] + sys.argv[1:])

TMP = tempfile.mkdtemp(prefix="helios-hs-")
# HELIOS_NETNS_DRY=1: build the namespaces, routing and every ruleset, send no
# traffic (checks the harness and the nft syntax where forwarding can't be on)
DRY = os.environ.get("HELIOS_NETNS_DRY") == "1"
children = {}
procs = []
LANS = ["10.20.0.0/24", "10.30.0.0/24"]
INGRESS = {"tailscale": ("T", "tailscale0", "100.64.0.2"), "wireguard": ("W", "wg-in", "10.61.0.2"),
           "openvpn": ("O", "tun-in", "10.62.0.2")}
TUN_IF = {"wireguard": "wg-out", "openvpn": "tun-out", "proxy": "tun-out"}
NET = "203.0.113.10"


def write_file(path, text, mode="w"):
    with open(path, mode) as f:
        f.write(text)


def sh(cmd, check=True):
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if check and r.returncode:
        raise RuntimeError(f"{cmd}\n{r.stderr}")
    return r.stdout


def spawn_ns(name):
    children[name] = subprocess.Popen(["unshare", "-n", "sleep", "infinity"])
    time.sleep(0.1)


def ns(name, cmd, check=True):
    return sh(f"nsenter -t {children[name].pid} -n sh -c '{cmd}'", check)


def bg(name, argv):
    return subprocess.Popen(["nsenter", "-t", str(children[name].pid), "-n"] + argv)


_n = [0]


def veth(a, a_if, b, b_if):
    """A veth pair between namespace a ("C" = this one) and b. Made under temporary
    names, then renamed where they land (several namespaces have an "eth0")."""
    _n[0] += 1
    ta, tb = f"hva{_n[0]}", f"hvb{_n[0]}"
    sh(f"ip link add name {ta} type veth peer name {tb}")
    sh(f"ip link set {tb} netns {children[b].pid}")
    ns(b, f"ip link set {tb} name {b_if}")
    if a == "C":
        sh(f"ip link set {ta} name {a_if}")
    else:
        sh(f"ip link set {ta} netns {children[a].pid}")
        ns(a, f"ip link set {ta} name {a_if}")


def up(name, iface, *addrs):
    run = sh if name == "C" else (lambda c: ns(name, c))
    for a in addrs:
        run(f"ip {'-6 ' if ':' in a else ''}addr add {a} dev {iface}" + (" nodad" if ":" in a else ""))
    run(f"ip link set {iface} up")


def setup():
    for n in ("H", "L", "N", "T", "W", "O"):
        spawn_ns(n)
    sh("ip link set lo up")
    for n in children:
        ns(n, "ip link set lo up")
    ok = subprocess.run("sysctl -qw net.ipv4.ip_forward=1 net.ipv6.conf.all.forwarding=1",
                        shell=True, capture_output=True).returncode == 0
    if not ok and not DRY:
        return False
    if ok:
        ns("H", "sysctl -qw net.ipv4.ip_forward=1 net.ipv6.conf.all.forwarding=1")
    # container <-> host
    veth("C", "eth0", "H", "ve-heliosphere")
    up("C", "eth0", "10.60.0.2/24")
    up("H", "ve-heliosphere", "10.60.0.1/24")
    sh("ip route add default via 10.60.0.1 dev eth0")
    # ways in
    for _, (cl, ifc, ip) in INGRESS.items():
        veth("C", ifc, cl, "eth0")
        gw = ip.rsplit(".", 1)[0] + ".1"
        up("C", ifc, gw + "/24", *(["fd7a::1/64"] if cl == "T" else []))
        up(cl, "eth0", ip + "/24", *(["fd7a::2/64"] if cl == "T" else []))
        ns(cl, f"ip route add default via {gw}")
        if cl == "T":
            ns(cl, "ip -6 route add default via fd7a::1")
    # host <-> home network and internet
    veth("H", "trusted", "L", "eth0")
    up("H", "trusted", "10.20.0.1/24")
    up("L", "eth0", "10.20.0.100/24")
    ns("L", "ip route add default via 10.20.0.1")
    veth("H", "wan", "N", "wan0")
    up("H", "wan", "192.0.2.254/24")
    up("N", "wan0", "192.0.2.1/24")
    ns("H", "ip route add default via 192.0.2.1")
    for a in (NET + "/32", "1.1.1.1/32", "8.8.8.8/32", "2001:db8:ff::10/128"):
        ns("N", f"ip {'-6 ' if ':' in a else ''}addr add {a} dev lo")
    load_host(["10.20.0.0/24"])
    return ok


# ------------------------------------------------------------------ the tunnel
def tunnel_up(mode):
    """The egress tunnel and its routing, as each way out sets it up."""
    t = TUN_IF[mode]
    if subprocess.run(["ip", "link", "show", t], capture_output=True).returncode != 0:
        sh(f"ip link add name {t} type veth peer name tun0")
        sh(f"ip link set tun0 netns {children['N'].pid}")
        up("C", t, "10.99.0.2/24", "2001:db8:99::2/64")
        up("N", "tun0", "10.99.0.1/24", "2001:db8:99::1/64")
    tunnel_routes(mode, add=True)


def tunnel_routes(mode, add):
    t, op = TUN_IF[mode], "replace" if add else "del"
    if mode == "openvpn":
        # redirect-gateway def1 (and its IPv6 counterpart): two halves in the main table
        for r in ("0.0.0.0/1", "128.0.0.0/1"):
            sh(f"ip route {op} {r} via 10.99.0.1 dev {t}", check=add)
        for r in ("::/1", "8000::/1"):
            sh(f"ip -6 route {op} {r} via 2001:db8:99::1 dev {t}", check=add)
    else:
        sh(f"ip route {op} default via 10.99.0.1 dev {t} table 100", check=add)
        sh(f"ip -6 route {op} default via 2001:db8:99::1 dev {t} table 100", check=add)


def tunnel_device_gone(mode):
    tunnel_device_gone_if(TUN_IF[mode])


def tunnel_device_gone_if(name):
    sh(f"ip link del {name} 2>/dev/null; true", check=False)


def policy_rules(mode, proxy_main_rule=True):
    """helios-policy-routing, plus each way out's own rules."""
    for fam in ("", "-6 "):
        for prio in (80, 85, 90, 190, 200):
            sh(f"ip {fam}rule del priority {prio} 2>/dev/null; true", check=False)
    sh("ip rule add to 10.60.0.0/24 lookup main priority 80")
    sh("ip rule add to 10.20.0.0/24 lookup main priority 85")
    sh("ip rule add fwmark 0x1 lookup main priority 90")
    if mode == "wireguard":
        # wg-quick: the main table without its default route first, then the tunnel's
        for fam in ("", "-6 "):
            sh(f"ip {fam}rule add lookup main suppress_prefixlength 0 priority 190")
            sh(f"ip {fam}rule add not fwmark 51820 lookup 100 priority 200")
    elif mode == "proxy":
        # tun2socks -tun-post-up in modules/heliosphere.nix (the main-table rule is
        # what lets replies reach WireGuard / OpenVPN clients)
        for fam in ("", "-6 "):
            if proxy_main_rule:
                sh(f"ip {fam}rule add lookup main suppress_prefixlength 0 priority 190")
            sh(f"ip {fam}rule add not fwmark 0x2 lookup 100 priority 200")


# ------------------------------------------------------------------ the rules
def container_rules(order, lan, mode=None, killswitch=True):
    """The container's table from modules/heliosphere.nix. lan: way in -> home networks."""
    tun = TUN_IF.get(mode)
    new = order == "new"
    head = ("meta nfproto ipv6 reject with icmpx admin-prohibited\n    ct state invalid drop\n"
            "    ct direction reply ct state established accept") if new else \
        "ct state vmap { established : accept, related : accept, invalid : drop }"
    ways = "".join(f'iifname "{INGRESS[m][1]}" ip daddr {{ {", ".join(n)} }} accept\n    ' for m, n in lan.items() if n)
    ks = f'oifname "eth0" ip daddr != 10.60.0.1 drop' if tun and killswitch else ""
    out_ks = """oifname "eth0" ip daddr 10.60.0.1 accept
    oifname "eth0" meta mark { 0x1, 0x2, 51820 } accept
    oifname "eth0" meta mark & 0xff0000 == 0x80000 accept
    oifname "eth0" drop""" if tun and killswitch else ""
    out_v6 = ('meta nfproto ipv6 oifname { "eth0"' + (f', "{tun}"' if tun else "") +
              " } reject with icmpx admin-prohibited") if new else ""
    oifs = '"eth0"' + (f', "{tun}"' if tun else "")
    return f"""flush ruleset
table inet heliosphere {{
  set doh_v4 {{ type ipv4_addr; flags interval; elements = {{ 1.1.1.1 }} }}
  set warp_v4 {{ type ipv4_addr; flags interval; elements = {{ 162.159.192.0/24 }} }}
  chain dns {{
    type nat hook prerouting priority dstnat;
    iifname != "eth0" meta l4proto {{ tcp, udp }} th dport 53 dnat ip to 10.60.0.1
  }}
  chain forward {{
    type filter hook forward priority filter; policy accept;
    {head}
    meta l4proto {{ tcp, udp }} th dport 853 reject
    ip daddr @doh_v4 reject
    ip daddr @warp_v4 reject
    udp dport 2408 reject
    {ways}ip daddr {{ {", ".join(LANS)} }} drop
    {ks}
  }}
  chain output {{
    type filter hook output priority filter; policy accept;
    {out_v6}
    {out_ks}
  }}
  chain postrouting {{
    type nat hook postrouting priority srcnat;
    ip saddr {{ 10.61.0.0/24, 10.62.0.0/24 }} oifname {{ {oifs} }} masquerade
    ip saddr 100.64.0.0/24 oifname {{ {oifs} }} masquerade
    ip6 saddr fd7a::/64 oifname {{ {oifs} }} masquerade
  }}
}}
"""


def load(rules, where="C"):
    path = os.path.join(TMP, f"rules-{where}.nft")
    write_file(path, rules)
    if where == "C":
        sh(f"nft -f {path}")
        # An observer in its own table (it only counts, never decides): client
        # internet traffic that leaves the container through eth0
        write_file(path + ".observe", """table inet observe {
  counter out_eth0 { }
  chain post {
    type filter hook postrouting priority 300; policy accept;
    oifname "eth0" ip daddr != { 10.60.0.0/24, 10.20.0.0/24, 10.30.0.0/24 } counter name out_eth0
  }
}
""")
        sh(f"nft -f {path}.observe")
    else:
        sh(f"nsenter -t {children[where].pid} -n nft -f {path}")


def load_host(union):
    """Heliopause's part: the container may only send from 10.60.0.2, IPv4 only,
    to the union of the home networks its ways in allow, and the internet. The
    first rule counts client internet traffic arriving from the container."""
    load(f"""flush ruleset
table inet heliopause {{
  counter leak {{ }}
  chain pre {{
    type filter hook prerouting priority raw;
    iifname "ve-heliosphere" ip daddr != {{ 10.60.0.0/24, 10.20.0.0/24, 10.30.0.0/24 }} counter name leak
  }}
  chain forward {{
    type filter hook forward priority filter; policy drop;
    iifname "ve-heliosphere" ip saddr != 10.60.0.2 drop
    iifname "ve-heliosphere" meta nfproto ipv6 drop
    ct state invalid drop
    ct state related accept
    ct direction reply ct state established accept
    {('iifname "ve-heliosphere" oifname "trusted" accept' if union else "")}
    iifname "ve-heliosphere" oifname "wan" accept
  }}
}}
table inet heliopause-nat {{
  chain postrouting {{
    type nat hook postrouting priority srcnat;
    oifname "wan" meta nfproto ipv4 masquerade
  }}
}}
""", where="H")


def leaked():
    """Client internet packets the host saw arrive from the container so far."""
    out = sh(f"nsenter -t {children['H'].pid} -n nft list counter inet heliopause leak")
    return int(out.split("packets")[1].split()[0])


def left_eth0():
    """Client internet packets the container sent out of eth0 since its rules loaded."""
    out = sh("nft list counter inet observe out_eth0")
    return int(out.split("packets")[1].split()[0])


# ------------------------------------------------------------------ traffic
SERVER = r"""
import socket, sys, threading
host, port, kind, path = sys.argv[1], int(sys.argv[2]), sys.argv[3], sys.argv[4]
fam = socket.AF_INET6 if ":" in host else socket.AF_INET
s = socket.socket(fam, socket.SOCK_DGRAM if kind == "udp" else socket.SOCK_STREAM)
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind((host, port)); out = open(path, "a", buffering=1)
if kind == "udp":
    while True:
        d, a = s.recvfrom(2048); out.write(a[0] + " " + d.decode().strip() + "\n"); s.sendto(b"ack\n", a)
s.listen(16)
def serve(c, a):
    for line in c.makefile():
        out.write(a[0] + " " + line.strip() + "\n"); c.sendall(b"ack\n")
while True:
    c, a = s.accept(); threading.Thread(target=serve, args=(c, a), daemon=True).start()
"""
CLIENT = r"""
import socket, sys, time, threading
host, port, kind, go = sys.argv[1], int(sys.argv[2]), sys.argv[3], sys.argv[4]
fam = socket.AF_INET6 if ":" in host else socket.AF_INET
s = socket.socket(fam, socket.SOCK_DGRAM if kind == "udp" else socket.SOCK_STREAM)
s.settimeout(5)
try:
    s.connect((host, port))
except OSError:
    open(go + ".replies", "a").write("connect-failed\n"); sys.exit()
s.settimeout(None)
def replies():
    with open(go + ".replies", "a", buffering=1) as f:
        while True:
            try:
                d = s.recv(2048)
            except OSError:
                return
            if not d and kind == "tcp":
                return
            f.write(d.decode())
threading.Thread(target=replies, daemon=True).start()
seen = 0
while True:
    with open(go) as f:
        lines = f.read().split()
    for msg in lines[seen:]:
        try:
            s.send((msg + "\n").encode())
        except OSError:
            pass
    seen = len(lines); time.sleep(0.05)
"""


def server(name, addr, port, kind):
    log = os.path.join(TMP, f"srv-{name}-{addr}-{port}-{kind}-{len(procs)}.log")
    write_file(log, "")
    procs.append(bg(name, [PY, "-I", os.path.join(TMP, "server.py"), addr, str(port), kind, log]))
    time.sleep(0.3)
    return log


class Client:
    def __init__(self, name, host, port, kind):
        self.go = os.path.join(TMP, f"go-{name}-{host}-{port}-{kind}-{len(procs)}")
        write_file(self.go, "")
        write_file(self.go + ".replies", "")
        procs.append(bg(name, [PY, "-I", os.path.join(TMP, "client.py"), host, str(port), kind, self.go]))
        time.sleep(0.4)

    def say(self, msg):
        write_file(self.go, msg + "\n", "a")
        time.sleep(0.8)
        return self

    def acks(self):
        with open(self.go + ".replies") as f:
            return f.read().count("ack")


def seen(log, msg):
    """The source addresses a message arrived from (empty: it never arrived)."""
    with open(log) as f:
        return {line.split()[0] for line in f if line.split()[1:] == [msg]}


def stop_traffic():
    for p in procs:
        p.kill()
    procs.clear()


# ------------------------------------------------------------------ checks
def run_egress(order, mode):
    """Kill switch, DNS and IPv6 with one way out."""
    res = {}
    allow = {"tailscale": ["10.20.0.0/24"], "wireguard": ["10.20.0.0/24"], "openvpn": ["10.20.0.0/24"]}
    for other in set(TUN_IF.values()):
        tunnel_device_gone_if(other)
    policy_rules(mode)
    tunnel_up(mode)
    load(container_rules(order, allow, mode))
    tun_src = "10.99.0.2"

    for how in ("route", "device"):
        udp = server("N", "0.0.0.0", 9000, "udp")
        tcp = server("N", "0.0.0.0", 7000, "tcp")
        u = Client("W", NET, 9000, "udp").say("u-before")
        t = Client("W", NET, 7000, "tcp").say("t-before")
        res[f"legit: {how}: UDP through the tunnel"] = seen(udp, "u-before") == {tun_src}
        res[f"legit: {how}: TCP through the tunnel, replies come back"] = seen(tcp, "t-before") == {tun_src} and t.acks() >= 1
        base, base_c = leaked(), left_eth0()
        if how == "route":
            tunnel_routes(mode, add=False)      # routes gone, device still there
        else:
            tunnel_device_gone(mode)            # the device itself goes away
        time.sleep(0.5)
        u.say("u-during")
        t.say("t-during")
        Client("W", NET, 9000, "udp").say("u-new-during")
        Client("W", NET, 7000, "tcp").say("t-new-during")
        res[f"deny: {how}: open or new connections leave through eth0 when the tunnel goes"] = \
            leaked() == base and left_eth0() == base_c
        res[f"deny: {how}: nothing reaches the internet outside the tunnel"] = not (
            seen(udp, "u-during") | seen(tcp, "t-during") | seen(udp, "u-new-during") | seen(tcp, "t-new-during")) - {tun_src}
        # DNS keeps working (through the router), the host stays reachable
        dns = server("H", "10.60.0.1", 53, "udp")
        far = server("N", "8.8.8.8", 53, "udp")
        Client("W", "8.8.8.8", 53, "udp").say("dns-during")
        res[f"legit: {how}: DNS still answered by the router while the tunnel is down"] = bool(seen(dns, "dns-during"))
        res[f"deny: {how}: DNS never reaches another resolver"] = not seen(far, "dns-during")
        host = server("H", "10.60.0.1", 5300, "udp")
        sh(f"{PY} -I -c \"import socket;socket.socket(socket.AF_INET,socket.SOCK_DGRAM).sendto(b'host-check',('10.60.0.1',5300))\"")
        time.sleep(0.5)
        res[f"legit: {how}: the container still reaches the host"] = bool(seen(host, "host-check"))
        # Recovery
        tunnel_up(mode)
        time.sleep(0.5)
        Client("W", NET, 9000, "udp").say("u-after")
        Client("W", NET, 7000, "tcp").say("t-after")
        res[f"legit: {how}: new connections use the tunnel again once it's back"] = \
            seen(udp, "u-after") == {tun_src} and seen(tcp, "t-after") == {tun_src}
        stop_traffic()
        if how == "route":
            # Control, on its own (no client left retrying): with the kill switch off
            # the same outage does send traffic out of eth0, so the counters above
            # can see a leak when there is one
            ctl = server("N", "0.0.0.0", 9002, "udp")
            tunnel_routes(mode, add=False)
            load(container_rules(order, allow, mode, killswitch=False))
            c0, h0 = left_eth0(), leaked()
            Client("W", NET, 9002, "udp").say("u-control")
            res[f"legit: {how}: (control) with the kill switch off, the counters see traffic outside the tunnel"] = \
                left_eth0() > c0 and leaked() > h0 and "192.0.2.254" in seen(ctl, "u-control")
            stop_traffic()
            tunnel_up(mode)
            load(container_rules(order, allow, mode))

    # IPv6 from a Tailscale client: the tunnel has IPv6, the filtering doesn't
    v6 = server("N", "2001:db8:ff::10", 9006, "udp")
    v6dns = server("N", "2001:db8:ff::10", 53, "udp")
    Client("T", "2001:db8:ff::10", 9006, "udp").say("v6-out")
    Client("T", "2001:db8:ff::10", 53, "udp").say("v6-dns")
    res["deny: client IPv6 forwarded (gets round the IPv4-only filtering)"] = not seen(v6, "v6-out")
    res["deny: client DNS over IPv6 skips the redirect to the router"] = not seen(v6dns, "v6-dns")
    stop_traffic()

    # DoT, DoH (1.1.1.1 is in the set), WARP: blocked for every way in; DNS redirected
    dot = server("N", NET, 853, "tcp")
    doh = server("N", "1.1.1.1", 443, "tcp")
    dns = server("H", "10.60.0.1", 53, "udp")
    for m, (cl, _, _) in INGRESS.items():
        Client(cl, NET, 853, "tcp").say(f"dot-{m}")
        Client(cl, "1.1.1.1", 443, "tcp").say(f"doh-{m}")
        Client(cl, "8.8.8.8", 53, "udp").say(f"dns-{m}")
        res[f"deny: {m}: DoT and DoH blocked"] = not seen(dot, f"dot-{m}") and not seen(doh, f"doh-{m}")
        res[f"legit: {m}: DNS redirected to the router"] = bool(seen(dns, f"dns-{m}"))
    stop_traffic()
    return res


def run_ingress(order):
    """Home-network access per way in, taken away from one while another keeps it."""
    res = {}
    policy_rules("direct")
    tunnel_device_gone("wireguard")
    tunnel_device_gone("openvpn")
    everyone = {m: ["10.20.0.0/24"] for m in INGRESS}
    load_host(["10.20.0.0/24"])
    for m, (cl, _, _) in INGRESS.items():
        other = next(x for x in INGRESS if x != m)
        ocl = INGRESS[other][0]
        load(container_rules(order, everyone))
        lan = server("L", "10.20.0.100", 7000, "tcp")
        c = Client(cl, "10.20.0.100", 7000, "tcp").say(f"{m}-before")
        k = Client(ocl, "10.20.0.100", 7000, "tcp").say(f"{other}-before")
        res[f"legit: {m}: reaches the home network while allowed, replies come back"] = \
            bool(seen(lan, f"{m}-before")) and c.acks() >= 1
        # Apply: this way in loses the home network, the other keeps it (so the
        # host, which only sees the container as a whole, still allows it)
        load(container_rules(order, {**everyone, m: []}))
        c.say(f"{m}-after")
        k.say(f"{other}-after")
        Client(cl, "10.20.0.100", 7000, "tcp").say(f"{m}-new")
        Client(ocl, "10.20.0.100", 7000, "tcp").say(f"{other}-new")
        res[f"deny: {m}: open connection cut when its access is taken away"] = not seen(lan, f"{m}-after")
        res[f"deny: {m}: no new connections after that"] = not seen(lan, f"{m}-new")
        res[f"legit: {m}: {other} keeps its open connection and opens new ones"] = \
            bool(seen(lan, f"{other}-after")) and bool(seen(lan, f"{other}-new"))
        stop_traffic()
    return res


def proxy_routing_without_main_rule():
    """Proxy egress routed as tun2socks' post-up did before: is a WireGuard client's
    reply delivered, or sent back into the tunnel?"""
    for other in set(TUN_IF.values()):
        tunnel_device_gone_if(other)
    policy_rules("proxy", proxy_main_rule=False)
    tunnel_up("proxy")
    load(container_rules("new", {}, "proxy"))
    server("N", "0.0.0.0", 9100, "udp")
    c = Client("W", NET, 9100, "udp").say("proxy-reply-check")
    ok = c.acks() >= 1
    stop_traffic()
    return ok


def main():
    write_file(os.path.join(TMP, "server.py"), SERVER)
    write_file(os.path.join(TMP, "client.py"), CLIENT)
    failed = False
    try:
        forwarding = setup()
        if DRY:
            for order in ("old", "new"):
                for mode in ("wireguard", "openvpn", "proxy"):
                    for other in set(TUN_IF.values()):
                        tunnel_device_gone_if(other)
                    policy_rules(mode)
                    tunnel_up(mode)
                    tunnel_routes(mode, add=False)
                    tunnel_up(mode)
                    tunnel_device_gone(mode)
                    tunnel_up(mode)
                    load(container_rules(order, {m: ["10.20.0.0/24"] for m in INGRESS}, mode))
                load(container_rules(order, {"wireguard": [], "tailscale": ["10.20.0.0/24"]}))
            load_host([])
            print("DRY RUN: namespaces, routing and rulesets load; counter reads", leaked())
            return
        if not forwarding:
            msg = "can't enable IP forwarding here (read-only /proc/sys): Heliosphere checks SKIPPED, run as root (CI)"
            if os.environ.get("HELIOS_NETNS_REQUIRE_FORWARD") == "1":
                raise RuntimeError(msg)
            print("NOTE: " + msg)
            return
        got = proxy_routing_without_main_rule()
        print("INFO: proxy egress with tun2socks' old routing: a WireGuard client "
              + ("gets its replies" if got else "gets NO replies (they go back into the tunnel)"))
        for order in ("old", "new"):
            print(f"\n== {order} ordering" + (" (before the fix)" if order == "old" else ""))
            results = []
            for mode in ("wireguard", "openvpn", "proxy"):
                results += [(f"{mode} egress: {k}", v) for k, v in run_egress(order, mode).items()]
            results += [(f"ways in: {k}", v) for k, v in run_ingress(order).items()]
            for name, ok in results:
                deny = ": deny: " in name or name.startswith("deny")
                tag = "ok" if ok else ("VULNERABLE" if order == "old" and deny else "FAIL")
                print(f"  [{tag}] {name}")
                if not ok and (order == "new" or not deny):
                    failed = True   # the fix doesn't hold, or legitimate traffic broke
    finally:
        for p in procs + list(children.values()):
            p.kill()
    print("\nFAILED" if failed else "\nPASSED")
    sys.exit(1 if failed else 0)


main()
