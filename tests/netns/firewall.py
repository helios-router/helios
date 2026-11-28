#!/usr/bin/env python3
"""Heliopause's real rulesets against real traffic, in throwaway network namespaces.

Loads the rulesets modules/heliopause.nix generates (tests/netns/rulesets.nix:
`nix build .#heliopauseRulesets`) into a router namespace whose interfaces carry
the template site's network names, and checks what happens to connections that
are already open when a permission goes away, in BOTH directions:

  T  trusted  10.20.0.100  fd20::100   admin, reaches every network
  F  family   10.30.0.100  fd30::100   reaches nothing (a variant lets it reach iot)
  I  iot      10.40.0.100              internet only, no IPv6
  isp  "the internet": 203.0.113.10, 2001:db8:ff::10 (and the router's upstream)

For each kind of revocation (reach, internet, admin access, a device forgotten
and so quarantined, a device paused): open TCP and UDP connections, send data
both ways, apply the variant without the permission, then try both directions
again (the server first, before the client sends anything, where the rules can
see the change at all), then give the permission back and open new connections.

Also: ICMP errors ("related" packets) forged from one network about another
network's connections must not cross between them, while real ones from the
internet still arrive; an unrelated connection must not notice a reload; and a
spoofed packet copying another network's open connection must not get through.

Needs root (CI): it turns on IP forwarding in its namespaces.
  sudo python3 -I tests/netns/firewall.py result/
Exit status 0 = every check passed. Never touches the host's own network.
"""
import os
import select
import socket
import struct
import subprocess
import sys
import tempfile
import time

PY = sys.executable
if len(sys.argv) != 2 or not os.path.isdir(sys.argv[1]):
    sys.exit("usage: firewall.py <directory with the .nft rulesets>")
RULES = os.path.abspath(sys.argv[1])
if os.environ.get("HELIOS_NETNS") != "1":
    os.environ["HELIOS_NETNS"] = "1"
    os.execvp("unshare", ["unshare", "-n" if os.geteuid() == 0 else "-rn", PY, "-I", os.path.abspath(__file__), RULES])

TMP = tempfile.mkdtemp(prefix="helios-fw-")
children = {}
procs = []
MAC = {"T": "02:00:00:00:00:20", "F": "02:00:00:00:00:30", "I": "02:00:00:00:00:40"}
NET = "203.0.113.10"
NET6 = "2001:db8:ff::10"


def write_file(path, text, mode="w"):
    with open(path, mode) as f:
        f.write(text)


def read_file(path):
    with open(path) as f:
        return f.read()


def sh(cmd, check=True):
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if check and r.returncode:
        raise RuntimeError(f"{cmd}\n{r.stderr}")
    return r.stdout


def ns(name, cmd, check=True):
    return sh(f"nsenter -t {children[name].pid} -n sh -c '{cmd}'", check)


def bg(name, argv):
    p = subprocess.Popen((["nsenter", "-t", str(children[name].pid), "-n"] if name != "R" else []) + argv)
    procs.append(p)
    return p


def setup():
    for n in ("T", "F", "I", "isp"):
        children[n] = subprocess.Popen(["unshare", "-n", "sleep", "infinity"])
    time.sleep(0.2)
    sh("ip link set lo up")
    if subprocess.run("sysctl -qw net.ipv4.ip_forward=1 net.ipv6.conf.all.forwarding=1",
                      shell=True, capture_output=True).returncode:
        return False
    sh("sysctl -qw net.ipv4.conf.all.rp_filter=2 net.ipv4.conf.default.rp_filter=2")   # lagrange.nix
    for i, (cl, ifc, v4, v6) in enumerate([("T", "trusted", "10.20.0", "fd20::"), ("F", "family", "10.30.0", "fd30::"),
                                            ("I", "iot", "10.40.0", None), ("isp", "wan", "192.0.2", "2001:db8::")]):
        sh(f"ip link add name {ifc} type veth peer name hv{i}")
        sh(f"ip link set hv{i} netns {children[cl].pid}")
        peer = "wan0" if cl == "isp" else "eth0"
        ns(cl, f"ip link set lo up; ip link set hv{i} name {peer}")
        if cl in MAC:
            ns(cl, f"ip link set {peer} address {MAC[cl]}")
        rv4, cv4 = (f"{v4}.254", f"{v4}.1") if cl == "isp" else (f"{v4}.1", f"{v4}.100")
        sh(f"ip addr add {rv4}/24 dev {ifc}; ip link set {ifc} up")
        ns(cl, f"ip addr add {cv4}/24 dev {peer}; ip link set {peer} up")
        if v6:
            rv6, cv6 = (f"{v6}254", f"{v6}1") if cl == "isp" else (f"{v6}1", f"{v6}100")
            sh(f"ip -6 addr add {rv6}/64 dev {ifc} nodad")
            ns(cl, f"ip -6 addr add {cv6}/64 dev {peer} nodad")
        if cl != "isp":
            ns(cl, f"ip route add default via {rv4}" + (f"; ip -6 route add default via {rv6}" if v6 else ""))
    sh("ip route add default via 192.0.2.1; ip -6 route add default via 2001:db8::1")
    ns("isp", f"ip addr add {NET}/32 dev lo; ip -6 addr add {NET6}/128 dev lo nodad")
    # The internet has no route back to the home networks over IPv4 (NAT); IPv6 is routed
    ns("isp", "ip -6 route add fd00::/8 via 2001:db8::254")
    return True


def load(variant):
    path = os.path.join(TMP, f"{variant}.nft")
    write_file(path, "flush ruleset\n" + read_file(os.path.join(RULES, f"{variant}.nft")))
    sh(f"nft -f {path}")


# ------------------------------------------------------------------ traffic
# A peer that listens or connects, logs every line it receives, and sends each
# line appended to its "go" file. UDP listeners answer whoever spoke last.
PEER = r"""
import socket, sys, threading, time
role, proto, host, port, log, go = sys.argv[1:7]
sport = int(sys.argv[7]) if len(sys.argv) > 7 else 0
fam = socket.AF_INET6 if ":" in host else socket.AF_INET
dgram = proto == "udp"
s = socket.socket(fam, socket.SOCK_DGRAM if dgram else socket.SOCK_STREAM)
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
out = open(log, "a", buffering=1)
peer = [None]
if role == "listen":
    s.bind((host, int(port)))
    if not dgram:
        s.listen(1); s, a = s.accept(); peer[0] = a
else:
    if sport:
        s.bind(("", sport))
    s.connect((host, int(port)))
    peer[0] = (host, int(port))
def rx():
    while True:
        try:
            d, a = s.recvfrom(4096)
        except OSError as e:
            out.write("ERR-" + type(e).__name__ + "\n")
            if not dgram:
                return
            continue
        if not d and not dgram:
            return
        peer[0] = a or peer[0]
        for line in d.decode(errors="replace").split():
            out.write(line + " " + str(a[0] if a else peer[0][0]) + " " + str(a[1] if a else peer[0][1]) + "\n")
threading.Thread(target=rx, daemon=True).start()
seen = 0
while True:
    with open(go) as f:
        lines = f.read().split()
    for m in lines[seen:]:
        try:
            if dgram and role == "listen":
                if peer[0]:
                    s.sendto((m + "\n").encode(), peer[0])
            else:
                s.send((m + "\n").encode())
        except OSError:
            pass
    seen = len(lines)
    time.sleep(0.05)
"""


class Peer:
    def __init__(self, where, role, proto, host, port, sport=0):
        tag = f"{where}-{role}-{proto}-{host}-{port}-{len(procs)}"
        self.log, self.go = os.path.join(TMP, tag + ".log"), os.path.join(TMP, tag + ".go")
        write_file(self.log, "")
        write_file(self.go, "")
        bg(where, [PY, "-I", os.path.join(TMP, "peer.py"), role, proto, host, str(port), self.log, self.go]
           + ([str(sport)] if sport else []))
        time.sleep(0.3)

    def say(self, msg):
        write_file(self.go, msg + "\n", "a")
        time.sleep(0.8)
        return self

    def got(self, msg):
        return any(line.split()[0] == msg for line in read_file(self.log).splitlines() if line.split())

    def lines(self):
        return [line.split() for line in read_file(self.log).splitlines() if line.split()]


def stop_traffic():
    for p in procs:
        p.kill()
    procs.clear()
    time.sleep(0.2)


# ------------------------------------------------------------------ ICMP errors
def csum(b):
    if len(b) % 2:
        b += b"\0"
    s = sum(struct.unpack(f"!{len(b) // 2}H", b))
    while s >> 16:
        s = (s & 0xFFFF) + (s >> 16)
    return ~s & 0xFFFF


def icmp4_port_unreachable(inner_src, inner_dst, sport, dport):
    udp = struct.pack("!HHHH", sport, dport, 12, 0) + b"data"
    ip = struct.pack("!BBHHHBBH4s4s", 0x45, 0, 20 + len(udp), 0, 0, 64, 17, 0,
                     socket.inet_aton(inner_src), socket.inet_aton(inner_dst))
    ip = ip[:10] + struct.pack("!H", csum(ip)) + ip[12:]
    body = struct.pack("!BBHI", 3, 3, 0, 0) + ip + udp
    return body[:2] + struct.pack("!H", csum(body)) + body[4:]


def icmp6_port_unreachable(inner_src, inner_dst, sport, dport):
    udp = struct.pack("!HHHH", sport, dport, 12, 0) + b"data"
    ip6 = struct.pack("!IHBB16s16s", 6 << 28, len(udp), 17, 64,
                      socket.inet_pton(socket.AF_INET6, inner_src), socket.inet_pton(socket.AF_INET6, inner_dst))
    return struct.pack("!BBHI", 1, 4, 0, 0) + ip6 + udp    # the kernel fills in an ICMPv6 checksum


SEND_ICMP = r"""
import socket, sys
fam, dst, hexpkt = sys.argv[1], sys.argv[2], sys.argv[3]
if fam == "4":
    s = socket.socket(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_ICMP)
else:
    s = socket.socket(socket.AF_INET6, socket.SOCK_RAW, socket.IPPROTO_ICMPV6)
s.sendto(bytes.fromhex(hexpkt), (dst, 0))
"""
# Logs "<source> <type>" for every ICMP / ICMPv6 error that arrives
SNIFF = r"""
import select, socket, sys
out = open(sys.argv[1], "a", buffering=1)
s4 = socket.socket(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_ICMP)
s6 = socket.socket(socket.AF_INET6, socket.SOCK_RAW, socket.IPPROTO_ICMPV6)
while True:
    for s in select.select([s4, s6], [], [])[0]:
        d, a = s.recvfrom(4096)
        t = d[20] if s is s4 else d[0]
        if (s is s4 and t in (3, 4, 5, 11, 12)) or (s is s6 and t < 128):
            out.write(f"{a[0]} {t}\n")
"""


def send_icmp(where, fam, dst, pkt):
    ns(where, f"{PY} -I {os.path.join(TMP, 'send_icmp.py')} {fam} {dst} {pkt.hex()}")
    time.sleep(0.5)


def sniffer(where):
    log = os.path.join(TMP, f"sniff-{where}-{len(procs)}.log")
    write_file(log, "")
    bg(where, [PY, "-I", os.path.join(TMP, "sniff.py"), log])
    time.sleep(0.3)
    return log


def errors_from(log, src):
    return [line for line in read_file(log).splitlines() if line.split()[0] == src]


# ------------------------------------------------------------------ checks
def revoke(res, name, grant, revoked, client, server, host, port, server_first=True):
    """Open TCP and UDP connections under `grant`, load `revoked`, try both
    directions, load `grant` again and open new ones."""
    for proto, p in (("tcp", port), ("udp", port + 1)):
        load(grant)
        srv = Peer(server, "listen", proto, "::" if ":" in host else "0.0.0.0", p)
        cli = Peer(client, "connect", proto, host, p)
        cli.say("c1")
        srv.say("s1")
        res[f"legit: {name} ({proto}): both directions while allowed"] = srv.got("c1") and cli.got("s1")
        load(revoked)
        order = [(srv, "s2"), (cli, "c2")] if server_first else [(cli, "c2"), (srv, "s2")]
        for who, msg in order:
            who.say(msg)
        res[f"deny: {name} ({proto}): client can't send on the open connection"] = not srv.got("c2")
        res[f"deny: {name} ({proto}): server can't send on the open connection"] = not cli.got("s2")
        stop_traffic()
        load(grant)
        srv = Peer(server, "listen", proto, "::" if ":" in host else "0.0.0.0", p + 10)
        cli = Peer(client, "connect", proto, host, p + 10)
        cli.say("c3")
        srv.say("s3")
        res[f"legit: {name} ({proto}): new connections once it's given back"] = srv.got("c3") and cli.got("s3")
        stop_traffic()


def pause(res, v6):
    """Photosphere's Pause: helios-ctl adds the device to the cutoff sets live."""
    host, port = (NET6, 7400) if v6 else (NET, 7300)
    fam = "IPv6" if v6 else "IPv4"
    elems = ["cutoff { 10.20.0.100 }", f"cutoff_mac {{ {MAC['T']} }}"]
    for proto, p in (("tcp", port), ("udp", port + 1)):
        load("base")
        srv = Peer("isp", "listen", proto, "::" if v6 else "0.0.0.0", p)
        cli = Peer("T", "connect", proto, host, p)
        cli.say("c1")
        srv.say("s1")
        res[f"legit: pause {fam} ({proto}): both directions before"] = srv.got("c1") and cli.got("s1")
        for e in elems:
            sh(f"nft add element inet heliopause {e}")
        # The device's own packet first: on IPv6 the router knows it only by MAC
        cli.say("c2")
        srv.say("s2")
        res[f"deny: pause {fam} ({proto}): device can't send"] = not srv.got("c2")
        res[f"deny: pause {fam} ({proto}): nothing reaches the device on its open connection"] = not cli.got("s2")
        for e in elems:
            sh(f"nft delete element inet heliopause {e}")
        stop_traffic()
        srv = Peer("isp", "listen", proto, "::" if v6 else "0.0.0.0", p + 10)
        cli = Peer("T", "connect", proto, host, p + 10)
        cli.say("c3")
        srv.say("s3")
        res[f"legit: pause {fam} ({proto}): new connections after unpausing"] = srv.got("c3") and cli.got("s3")
        stop_traffic()


def unrelated_reload(res):
    load("base")
    srv = Peer("isp", "listen", "tcp", "0.0.0.0", 7500)
    cli = Peer("T", "connect", "tcp", NET, 7500)
    cli.say("c1")
    srv.say("s1")
    load("iot-no-internet")       # a change that doesn't concern trusted
    srv.say("s2")                 # the server speaks first after the reload
    cli.say("c2")
    res["legit: an unrelated change leaves trusted's open connection alone, both ways"] = \
        cli.got("s1") and cli.got("s2") and srv.got("c2")
    stop_traffic()


def spoof(res):
    load("base")
    srv = Peer("isp", "listen", "udp", "0.0.0.0", 7600)
    Peer("T", "connect", "udp", NET, 7600, sport=5600).say("legit-flow")
    ns("I", "ip addr add 10.20.0.100/32 dev eth0")
    Peer("I", "connect", "udp", NET, 7600, sport=5600).say("spoofed-flow")
    ns("I", "ip addr del 10.20.0.100/32 dev eth0")
    res["legit: trusted's UDP flow to the internet"] = srv.got("legit-flow")
    res["deny: iot copying trusted's open flow"] = not srv.got("spoofed-flow")
    stop_traffic()


def related(res):
    """ICMP errors about trusted's open connections, forged from another network."""
    load("base")
    sniff = sniffer("T")
    # IPv4: trusted's UDP flow to the internet is masqueraded. The forger aims at
    # the router's WAN address with the NATed flow in its quoted header, which is
    # what the internet would send; NAT then hands it to trusted.
    srv = Peer("isp", "listen", "udp", "0.0.0.0", 9000)
    Peer("T", "connect", "udp", NET, 9000, sport=5000).say("v4-flow")
    nat_port = int(next(line[2] for line in srv.lines() if line[0] == "v4-flow"))
    pkt = icmp4_port_unreachable("192.0.2.254", NET, nat_port, 9000)
    send_icmp("I", "4", "192.0.2.254", pkt)
    send_icmp("isp", "4", "192.0.2.254", pkt)
    res["deny: iot forges an ICMP error into trusted's internet flow (IPv4, through NAT)"] = not errors_from(sniff, "10.40.0.100")
    res["legit: an ICMP error from the internet about trusted's flow arrives (IPv4)"] = bool(errors_from(sniff, "192.0.2.1"))
    # IPv6: no NAT; family forges straight at trusted
    Peer("isp", "listen", "udp", "::", 9001)
    Peer("T", "connect", "udp", NET6, 9001, sport=5001).say("v6-flow")
    pkt6 = icmp6_port_unreachable("fd20::100", NET6, 5001, 9001)
    send_icmp("F", "6", "fd20::100", pkt6)
    send_icmp("isp", "6", "fd20::100", pkt6)
    res["deny: family forges an ICMPv6 error into trusted's internet flow"] = not errors_from(sniff, "fd30::100")
    res["legit: an ICMPv6 error from the internet about trusted's flow arrives"] = bool(errors_from(sniff, "2001:db8::1"))
    # IPv4 between networks: trusted's flow to iot; family forges at trusted
    Peer("I", "listen", "udp", "0.0.0.0", 9002)
    Peer("T", "connect", "udp", "10.40.0.100", 9002, sport=5002).say("lan-flow")
    pkt = icmp4_port_unreachable("10.20.0.100", "10.40.0.100", 5002, 9002)
    send_icmp("F", "4", "10.20.0.100", pkt)
    send_icmp("I", "4", "10.20.0.100", pkt)
    res["deny: family forges an ICMP error into trusted's flow to iot"] = not errors_from(sniff, "10.30.0.100")
    res["legit: iot's own ICMP error about trusted's flow to it arrives"] = bool(errors_from(sniff, "10.40.0.100"))
    stop_traffic()


def main():
    for name, text in (("peer.py", PEER), ("send_icmp.py", SEND_ICMP), ("sniff.py", SNIFF)):
        write_file(os.path.join(TMP, name), text)
    failed = False
    try:
        if not setup():
            msg = "can't enable IP forwarding here (read-only /proc/sys): firewall checks SKIPPED, run as root (CI)"
            if os.environ.get("HELIOS_NETNS_REQUIRE_FORWARD") == "1":
                raise RuntimeError(msg)
            print("NOTE: " + msg)
            return
        res = {}
        spoof(res)
        unrelated_reload(res)
        revoke(res, "family's reach to iot taken away", "family-reaches-iot", "base", "F", "I", "10.40.0.100", 7000)
        revoke(res, "iot's internet taken away", "base", "iot-no-internet", "I", "isp", NET, 7100)
        revoke(res, "iot's admin access taken away", "iot-admin", "base", "I", "R", "10.40.0.1", 2200)
        # Forgetting a device changes no rule, only the set of known devices: the
        # device's next packet is what shows it's no longer allowed
        revoke(res, "family tablet forgotten (quarantined)", "family-reaches-iot", "family-reaches-iot-unnamed",
               "F", "I", "10.40.0.100", 7200, server_first=False)
        pause(res, v6=False)
        pause(res, v6=True)
        related(res)
        for name, ok in res.items():
            print(f"  [{'ok' if ok else 'FAIL'}] {name}")
            failed |= not ok
    finally:
        for p in procs + list(children.values()):
            p.kill()
    print("\nFAILED" if failed else "\nPASSED")
    sys.exit(1 if failed else 0)


main()
