#!/usr/bin/env python3
"""Heliopause connection-tracking regression test, in throwaway network namespaces.

Runs real kernel conntrack + nftables against a router namespace whose forward
and input chains copy the structure modules/heliopause.nix generates (same rule
order, same matches) for two networks:

  trusted  r-trusted  10.20.0.0/24  fd20::/64  admin, reaches every network, internet
  iot      r-iot      10.40.0.0/24  fd40::/64  not admin, internet only
  wan      wan        192.0.2.0/24  2001:db8::/64  "the internet" (masqueraded)

It checks, for the old ordering ("established" accepted before anything else)
and the current one, that:
  1. a spoofed packet from iot copying trusted's open UDP flow is dropped
  2. iot can't send from another network's IPv6 address
  3. taking away a reach permission cuts a connection that is already open
  4. taking away admin access cuts an open session to the router
  5. a spoofed packet from iot copying trusted's open flow to the router is dropped
and that legitimate traffic keeps working (new connections, replies, NAT, a
trusted connection that stays open across a ruleset reload).

No root needed:  unshare -rn python3 tests/netns/conntrack.py
(as root it makes its own namespace). Exit status 0 = every check passed for
the current ordering; the old ordering is expected to fail 1-4 (shown as
"vulnerable") and that is reported, not an error. Never touches the host's own
network: everything lives in namespaces that vanish when it exits.
"""
import os
import subprocess
import sys
import tempfile
import time

PY = sys.executable
if os.geteuid() != 0 or os.environ.get("HELIOS_NETNS") != "1":
    # Re-run inside a fresh user + network namespace
    os.environ["HELIOS_NETNS"] = "1"
    flags = "-n" if os.geteuid() == 0 else "-rn"
    os.execvp("unshare", ["unshare", flags, PY, "-I", os.path.abspath(__file__)] + sys.argv[1:])

TMP = tempfile.mkdtemp(prefix="helios-netns-")
children = {}


def write_file(path, text, mode="w"):
    with open(path, mode) as f:
        f.write(text)


def sh(cmd, check=True):
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if check and r.returncode:
        raise RuntimeError(f"{cmd}\n{r.stderr}")
    return r.stdout


def spawn_ns(name):
    p = subprocess.Popen(["unshare", "-n", "sleep", "infinity"])
    time.sleep(0.1)
    children[name] = p
    return p.pid


def ns(name, cmd, check=True):
    return sh(f"nsenter -t {children[name].pid} -n sh -c '{cmd}'", check)


def bg(name, argv):
    return subprocess.Popen(["nsenter", "-t", str(children[name].pid), "-n"] + argv)


def link(host, rname, hname, v4, v6, rv4, rv6, gw4, gw6):
    sh(f"ip link add name {rname} type veth peer name {hname}")
    sh(f"ip link set {hname} netns {children[host].pid}")
    sh(f"ip addr add {rv4} dev {rname}; ip -6 addr add {rv6} dev {rname} nodad; ip link set {rname} up")
    ns(host, f"ip link set lo up; ip link set {hname} up; ip addr add {v4} dev {hname}; "
             f"ip -6 addr add {v6} dev {hname} nodad; ip route add default via {gw4}; ip -6 route add default via {gw6}")


def setup():
    for n in ("trusted", "iot", "isp"):
        spawn_ns(n)
    sh("ip link set lo up")
    global FORWARDING
    # Some sandboxes (rootless containers) mount /proc/sys read-only: then only
    # the input-chain checks can run, and the forward ones are reported skipped.
    # Set HELIOS_NETNS_REQUIRE_FORWARD=1 (CI) to make that an error instead
    FORWARDING = subprocess.run("sysctl -qw net.ipv4.ip_forward=1 net.ipv6.conf.all.forwarding=1",
                                shell=True, capture_output=True).returncode == 0
    # Loose reverse path filtering, like lagrange.nix
    sh("sysctl -qw net.ipv4.conf.all.rp_filter=2 net.ipv4.conf.default.rp_filter=2", check=False)
    link("trusted", "r-trusted", "eth0", "10.20.0.100/24", "fd20::100/64", "10.20.0.1/24", "fd20::1/64", "10.20.0.1", "fd20::1")
    link("iot", "r-iot", "eth0", "10.40.0.100/24", "fd40::100/64", "10.40.0.1/24", "fd40::1/64", "10.40.0.1", "fd40::1")
    link("isp", "wan", "eth0", "192.0.2.1/24", "2001:db8::1/64", "192.0.2.254/24", "2001:db8::254/64", "192.0.2.254", "2001:db8::254")
    # The isp has no route back to the home networks: an answer proves masquerading (IPv4)
    ns("isp", "ip route del default; ip -6 route del default; ip -6 route add fd00::/8 via 2001:db8::254")


def ruleset(order, iot_reaches_trusted=False, iot_admin=False):
    """The chains heliopause.nix generates, reduced to these networks."""
    old = order == "old"
    head_old = "ct state vmap { established : accept, related : accept, invalid : drop }"
    head_new = "ct state invalid drop\n    ct state related accept\n    ct direction reply ct state established accept"
    antispoof = """iifname "r-iot" ip saddr != 10.40.0.0/24 drop""" + (
        "" if old else """
    iifname { "r-iot" } meta nfproto ipv6 fib saddr . iif oif missing drop
    ip saddr 192.168.255.0/24 iifname != "rescue0" drop""")
    admin_ifs = '"r-trusted"' + (', "r-iot"' if iot_admin else "")
    non_admin = "" if iot_admin else """
    iifname { "r-iot" } tcp dport { 22, 2222 } drop"""
    inp_spoof = "" if iot_admin else (
        'iifname "r-iot" ip saddr != { 10.40.0.0/24, 0.0.0.0 } drop\n    '
        'iifname { "r-iot" } meta nfproto ipv6 meta l4proto != ipv6-icmp fib saddr . iif oif missing drop\n    ')
    inp_head = head_old if old else 'ip saddr 192.168.255.0/24 iifname != "rescue0" drop\n    ' + inp_spoof + head_new
    fwd = f"""
    ip saddr @cutoff reject
    ip daddr @cutoff drop
    {head_old + chr(10) + '    ' + antispoof if old else antispoof + chr(10) + '    ' + head_new}
    iifname "r-trusted" accept
    {'iifname "r-iot" oifname "r-trusted" accept' if iot_reaches_trusted else ''}
    iifname {{ "r-trusted", "r-iot" }} oifname "wan" accept"""
    return f"""flush ruleset
table inet heliopause {{
  set cutoff {{ type ipv4_addr; }}
  chain input {{
    type filter hook input priority filter; policy drop;
    iif lo accept
    {inp_head}
    meta l4proto {{ icmp, ipv6-icmp }} accept{non_admin}
    iifname {{ {admin_ifs} }} accept
    iifname {{ "r-iot" }} meta l4proto {{ tcp, udp }} th dport 53 accept
  }}
  chain forward {{
    type filter hook forward priority filter; policy drop;{fwd}
  }}
}}
table inet heliopause-nat {{
  chain postrouting {{
    type nat hook postrouting priority srcnat;
    oifname "wan" meta nfproto ipv4 masquerade
  }}
}}
"""


def load(rules):
    path = os.path.join(TMP, "rules.nft")
    write_file(path, rules)
    sh(f"nft -f {path}")


# A line-logging server, and a client that keeps one connection open and sends
# a line each time its "go" file gains one
SERVER = r"""
import socket, sys, threading
fam = socket.AF_INET6 if ":" in sys.argv[1] else socket.AF_INET
kind = socket.SOCK_DGRAM if sys.argv[3] == "udp" else socket.SOCK_STREAM
s = socket.socket(fam, kind); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind((sys.argv[1], int(sys.argv[2]))); out = open(sys.argv[4], "a", buffering=1)
if kind == socket.SOCK_DGRAM:
    while True:
        d, a = s.recvfrom(2048); out.write(d.decode().strip() + "\n"); s.sendto(b"ack\n", a)
s.listen(8)
def serve(c):
    for line in c.makefile():
        out.write(line.strip() + "\n"); c.sendall(b"ack\n")
while True:
    c, _ = s.accept(); threading.Thread(target=serve, args=(c,), daemon=True).start()
"""
CLIENT = r"""
import socket, sys, time, os
host, port, src, sport, kind, go = sys.argv[1], int(sys.argv[2]), sys.argv[3], int(sys.argv[4]), sys.argv[5], sys.argv[6]
fam = socket.AF_INET6 if ":" in host else socket.AF_INET
s = socket.socket(fam, socket.SOCK_DGRAM if kind == "udp" else socket.SOCK_STREAM)
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
if src: s.bind((src, sport))
s.connect((host, port)); seen = 0
while True:
    lines = open(go).read().split()
    for msg in lines[seen:]:
        try: s.send((msg + "\n").encode())
        except OSError: pass
    seen = len(lines); time.sleep(0.05)
"""
write_file(os.path.join(TMP, "server.py"), SERVER)
write_file(os.path.join(TMP, "client.py"), CLIENT)
procs = []


def server(name, addr, port, kind):
    log = os.path.join(TMP, f"{name}-{port}-{kind}.log")
    write_file(log, "")
    procs.append(bg(name, [PY, "-I", os.path.join(TMP, "server.py"), addr, str(port), kind, log]))
    time.sleep(0.3)
    return log


def client(name, host, port, kind, src="", sport=0):
    go = os.path.join(TMP, f"go-{name}-{port}-{kind}-{len(procs)}")
    write_file(go, "")
    procs.append(bg(name, [PY, "-I", os.path.join(TMP, "client.py"), host, str(port), src, str(sport), kind, go]))
    time.sleep(0.3)

    def say(msg):
        write_file(go, msg + "\n", "a")
        time.sleep(0.6)
    return say


def got(log, msg):
    with open(log) as f:
        return msg in f.read().split()


def stop_all():
    for p in procs:
        p.kill()
    procs.clear()
    sh("nft flush ruleset", check=False)
    sh("echo f > /proc/net/nf_conntrack 2>/dev/null; true", check=False)


def run_input(order):
    res = {}
    load(ruleset(order))
    # 5. Spoofed packet riding trusted's open UDP flow to the router's DNS port
    log = server_local("10.20.0.1", 53, "udp", order)
    client("trusted", "10.20.0.1", 53, "udp", "10.20.0.100", 5053)("legit-dns")
    ns("iot", "ip addr add 10.20.0.100/32 dev eth0")
    client("iot", "10.20.0.1", 53, "udp", "10.20.0.100", 5053)("spoofed-dns")
    ns("iot", "ip addr del 10.20.0.100/32 dev eth0")
    client("iot", "10.20.0.1", 53, "udp", "10.40.0.100", 5054)("iot-dns")
    res["legit: trusted DNS to the router"] = got(log, "legit-dns")
    res["legit: iot DNS to the router from its own address"] = got(log, "iot-dns")
    res["deny: spoofed packet on trusted's open flow to the router"] = not got(log, "spoofed-dns")
    stop_all()

    # 4. Admin access taken away while iot has a session open to the router
    load(ruleset(order, iot_admin=True))
    loga = server_local("10.40.0.1", 2222, "tcp", order)
    a = client("iot", "10.40.0.1", 2222, "tcp")
    a("admin-before")
    load(ruleset(order, iot_admin=False))
    a("admin-after")
    res["legit: admin session while admin"] = got(loga, "admin-before")
    res["deny: demoted network's open admin session is cut"] = not got(loga, "admin-after")
    stop_all()
    return res


def server_local(addr, port, kind, tag):
    log = os.path.join(TMP, f"router-{tag}-{port}-{kind}.log")
    write_file(log, "")
    procs.append(subprocess.Popen([PY, "-I", os.path.join(TMP, "server.py"), addr, str(port), kind, log]))
    time.sleep(0.3)
    return log


def run(order):
    res = run_input(order)
    if not FORWARDING:
        return res
    load(ruleset(order))

    # 1. Spoofed packet riding trusted's open UDP flow to the internet
    log = server("isp", "192.0.2.1", 9000, "udp")
    t = client("trusted", "192.0.2.1", 9000, "udp", "10.20.0.100", 5000)
    t("legit-udp")
    ns("iot", "ip addr add 10.20.0.100/32 dev eth0")
    spoof = client("iot", "192.0.2.1", 9000, "udp", "10.20.0.100", 5000)
    spoof("spoofed-udp")
    ns("iot", "ip addr del 10.20.0.100/32 dev eth0")
    res["legit: trusted UDP to internet (NAT)"] = got(log, "legit-udp")
    res["deny: spoofed packet on trusted's open flow"] = not got(log, "spoofed-udp")

    # 2. IPv6 source spoofing from iot (new connection, no conntrack involved)
    log6 = server("isp", "2001:db8::1", 9001, "udp")
    ns("iot", "ip -6 addr add fd20::bad/128 dev eth0 nodad")
    client("iot", "2001:db8::1", 9001, "udp", "fd20::bad", 5001)("spoofed-v6")
    client("iot", "2001:db8::1", 9001, "udp", "fd40::100", 5002)("legit-v6")
    ns("iot", "ip -6 addr del fd20::bad/128 dev eth0")
    res["legit: iot IPv6 from its own prefix"] = got(log6, "legit-v6")
    res["deny: iot IPv6 from trusted's prefix"] = not got(log6, "spoofed-v6")

    # 3. Reach revoked while iot has a connection open into trusted
    load(ruleset(order, iot_reaches_trusted=True))
    logr = server("trusted", "10.20.0.100", 7000, "tcp")
    c = client("iot", "10.20.0.100", 7000, "tcp")
    c("before-revoke")
    # A trusted connection to the internet that must survive the reload
    logk = server("isp", "192.0.2.1", 7001, "tcp")
    k = client("trusted", "192.0.2.1", 7001, "tcp")
    k("keep-1")
    load(ruleset(order, iot_reaches_trusted=False))      # what a rebuild does: flush + load
    c("after-revoke")
    k("keep-2")
    res["legit: iot reaches trusted while allowed"] = got(logr, "before-revoke")
    res["deny: revoked reach cuts the open connection"] = not got(logr, "after-revoke")
    res["legit: trusted's open internet connection survives a reload"] = got(logk, "keep-2")
    logn = server("iot", "10.40.0.100", 7002, "tcp")
    client("trusted", "10.40.0.100", 7002, "tcp")("trusted-new")
    res["legit: trusted opens new connections to iot after the reload"] = got(logn, "trusted-new")
    stop_all()
    return res


def main():
    failed = False
    try:
        setup()
        if not FORWARDING and os.environ.get("HELIOS_NETNS_REQUIRE_FORWARD") == "1":
            raise RuntimeError("IP forwarding unavailable but HELIOS_NETNS_REQUIRE_FORWARD=1")
        if not FORWARDING:
            print("NOTE: can't enable IP forwarding here (read-only /proc/sys): forward-chain checks 1-3 SKIPPED,"
                  " run as root (CI) for those")
        for order in ("old", "new"):
            print(f"\n== {order} ordering" + (" (before the fix, expected to be vulnerable)" if order == "old" else ""))
            for name, ok in run(order).items():
                tag = "ok" if ok else ("VULNERABLE" if order == "old" and name.startswith("deny") else "FAIL")
                print(f"  [{tag}] {name}")
                if order == "new" and not ok:
                    failed = True
                if order == "old" and name.startswith("legit") and not ok:
                    failed = True     # the harness itself is broken
    finally:
        for p in procs + list(children.values()):
            p.kill()
    print("\nFAILED" if failed else ("\nPASSED" if FORWARDING else "\nPASSED (input chain only)"))
    sys.exit(1 if failed else 0)


FORWARDING = False


main()
