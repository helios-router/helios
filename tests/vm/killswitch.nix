# Remote access kill switch, end to end: a phone connects to Heliosphere over
# WireGuard; Heliosphere's way out is a WireGuard, OpenVPN or SOCKS5 tunnel to
# the "isp" machine, which also runs the destination (198.51.100.10). With TCP
# and UDP sessions running, the tunnel is stopped inside the container. Nothing
# from the phone may then appear on the router's WAN outside the tunnel
# (tcpdump on the WAN, and the destination's own log of who it heard from),
# new connections must not get out either, DNS keeps answering (from the
# router, docs/heliosphere.md), and once the tunnel is back new connections go
# through it again. A control run with the kill switch off shows the same
# capture does see a leak. Also: restarting a tunnel leaves no extra routing rules.
#
# Keys and certificates here are test values only.
{ self }:

let
  common = import ./common.nix;
  # WireGuard keys (tests only)
  keys = {
    remote = { priv = "fbu3gBzTFhTo2QyNWd1rtjcV7Kn0POgRmOKREySeCeg="; pub = "95eK3vpStESaSUwcq+kc4iHUub/RquZHbBNRSVBoLiM="; };
    egress = { priv = "UIOShc8zMnnc29GqKMI+43j2fak8vxcJZhYbfLRgfqk="; pub = "n2WxxO+DdUCQ7qS1L3+8hQ78pJb8+ffmuEf1GrC1A1E="; };
    upstream = { priv = "pU5wAaiIXbStRDH8RUtg/ZIO8zGYuwCDqNK1acXXp2Q="; pub = "nIavaUXITdLqs0KdgYhX2KUkBwvRosjxut/B/zZgqko="; };
  };
  dest = "198.51.100.10";
in
{
  name = "helios-killswitch";

  nodes = {
    # The internet: DHCP for the router's WAN, the destination, and the three
    # kinds of upstream a provider would run
    isp = { lib, pkgs, ... }: let
      pki = pkgs.runCommand "killswitch-test-pki" { nativeBuildInputs = [ pkgs.openssl ]; } ''
        mkdir $out; cd $out
        openssl genpkey -algorithm EC -pkeyopt ec_paramgen_curve:P-256 -out ca.key
        openssl req -x509 -new -key ca.key -out ca.crt -days 3650 -subj /CN=test-ca
        for n in server client; do
          openssl genpkey -algorithm EC -pkeyopt ec_paramgen_curve:P-256 -out $n.key
          openssl req -new -key $n.key -out $n.csr -subj /CN=$n
          if [ $n = server ]; then eku=serverAuth; else eku=clientAuth; fi
          printf 'basicConstraints=CA:FALSE\nkeyUsage=digitalSignature,keyAgreement\nextendedKeyUsage=%s\n' $eku > $n.ext
          openssl x509 -req -in $n.csr -CA ca.crt -CAkey ca.key -CAcreateserial -out $n.crt -days 3650 -extfile $n.ext
        done
        {
          echo client; echo "dev tun"; echo "proto udp"; echo "remote 192.0.2.1 1194"; echo nobind; echo "remote-cert-tls server"
          echo "<ca>"; cat ca.crt; echo "</ca>"; echo "<cert>"; cat client.crt; echo "</cert>"; echo "<key>"; cat client.key; echo "</key>"
        } > client.ovpn
      '';
    in {
      imports = [ common.isp ];
      virtualisation.memorySize = 1024;
      environment.etc."killswitch/client.ovpn".source = "${pki}/client.ovpn";
      environment.systemPackages = [ pkgs.tcpdump ];
      boot.kernel.sysctl."net.ipv4.ip_forward" = 1;
      # The destination: logs "<source address> <line>" for TCP and UDP
      systemd.services.dest = {
        wantedBy = [ "multi-user.target" ];
        after = [ "network-online.target" ];
        path = [ pkgs.iproute2 ];
        preStart = "ip addr add ${dest}/32 dev lo 2>/dev/null || true";
        serviceConfig.ExecStart = "${pkgs.python3}/bin/python3 -u ${pkgs.writeText "dest.py" ''
          import socket, threading
          out = open("/tmp/dest.log", "a", buffering=1)
          def tcp():
              s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
              s.bind(("${dest}", 7000)); s.listen(16)
              def serve(c, a):
                  for line in c.makefile():
                      out.write(a[0] + " " + line.strip() + "\n")
                      try:
                          c.sendall(b"ack\n")
                      except OSError:
                          return
              while True:
                  c, a = s.accept(); threading.Thread(target=serve, args=(c, a), daemon=True).start()
          threading.Thread(target=tcp, daemon=True).start()
          u = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); u.bind(("${dest}", 7001))
          while True:
              d, a = u.recvfrom(2048)
              out.write(a[0] + " " + d.decode().strip() + "\n"); u.sendto(b"ack\n", a)
        ''}";
      };
      # WireGuard upstream (a VPN provider)
      networking.wireguard.interfaces.wg-up = {
        ips = [ "10.99.0.1/24" ];
        listenPort = 51900;
        privateKeyFile = "${pkgs.writeText "upstream.key" keys.upstream.priv}";
        peers = [ { publicKey = keys.egress.pub; allowedIPs = [ "10.99.0.2/32" ]; } ];
      };
      # OpenVPN upstream
      services.openvpn.servers.up.config = ''
        dev tun-up
        proto udp
        port 1194
        server 10.98.0.0 255.255.255.0
        topology subnet
        ca ${pki}/ca.crt
        cert ${pki}/server.crt
        key ${pki}/server.key
        dh none
        push "redirect-gateway def1"
        keepalive 5 30
      '';
      # SOCKS5 upstream (TCP and UDP)
      services.dante = {
        enable = true;
        config = ''
          internal: 192.0.2.1 port = 1080
          external: 192.0.2.1
          clientmethod: none
          socksmethod: none
          client pass { from: 0.0.0.0/0 to: 0.0.0.0/0 }
          socks pass { from: 0.0.0.0/0 to: 0.0.0.0/0 command: connect udpassociate }
          socks pass { from: 0.0.0.0/0 to: 0.0.0.0/0 command: udpreply }
        '';
      };
      systemd.services.dante.after = [ "network-online.target" "systemd-networkd-wait-online.service" ];
    };

    # A phone away from home: WireGuard to the router's WAN address, set up by the script
    remote = { pkgs, ... }: {
      imports = [ (common.vlanHost 10 { address = [ "192.0.2.50/24" ]; networkConfig.LinkLocalAddressing = "no"; }) ];
      environment.systemPackages = [ pkgs.wireguard-tools pkgs.python3 pkgs.dig ];
    };

    router = { config, lib, pkgs, ... }: let
      template = builtins.fromJSON (builtins.readFile ../../templates/state.json);
      stateWith = egress: lib.recursiveUpdate template {
        heliosphere = {
          ingress = {
            tailscale.enabled = false;              # needs the real internet
            wireguard = { enabled = true; peers = [ { name = "phone"; publicKey = keys.remote.pub; ip = "10.61.0.2"; } ]; };
          };
          inherit egress;
        };
      };
      socks = { type = "socks5"; host = "192.0.2.1"; port = 1080; };
    in {
      imports = [ (common.router self) ];
      virtualisation.memorySize = 3072;
      environment.systemPackages = [ pkgs.tcpdump pkgs.dig pkgs.python3 ];
      helios.heliosphere.enable = lib.mkOverride 40 true;
      helios.heliosphere.publicHost = "vpn.example.test";
      helios.state = lib.mkOverride 40 (stateWith { mode = "wireguard"; killswitch = true; });
      # The other ways out, switched to by the script (each restarts the container)
      specialisation = {
        nokill.configuration.helios.state = lib.mkOverride 30 (stateWith { mode = "wireguard"; killswitch = false; });
        openvpn.configuration.helios.state = lib.mkOverride 30 (stateWith { mode = "openvpn"; killswitch = true; });
        proxy.configuration.helios.state = lib.mkOverride 30 (stateWith { mode = "proxy"; killswitch = true; proxy = socks; });
      };
      # The egress tunnel's config, uploaded through helios-ctl as Photosphere does
      environment.etc."killswitch/wg-out.conf".text = ''
        [Interface]
        PrivateKey = ${keys.egress.priv}
        Address = 10.99.0.2/24

        [Peer]
        PublicKey = ${keys.upstream.pub}
        Endpoint = 192.0.2.1:51900
        AllowedIPs = 0.0.0.0/0
      '';
    };
  };

  testScript = { nodes, ... }: common.leaseHelper + ''
    import time

    SENDER = "${builtins.toFile "sender.py" ''
      import socket, sys, time
      proto, host, port, tag = sys.argv[1:5]
      dur = float(sys.argv[5]) if len(sys.argv) > 5 else 1e9
      s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM if proto == "udp" else socket.SOCK_STREAM)
      s.settimeout(3)
      s.connect((host, int(port)))
      s.settimeout(0.05)
      end, i = time.time() + dur, 0
      while time.time() < end:
          try:
              s.send(f"{tag}-{i}\n".encode())
          except OSError:
              pass
          i += 1
          try:
              print(s.recv(4096).decode().strip(), flush=True)
          except OSError:
              pass
          time.sleep(0.2)
    ''}"
    DEST = "${dest}"
    OVPN = "${nodes.isp.environment.etc."killswitch/client.ovpn".source}"

    def container(cmd):
        return router.succeed(f"nixos-container run heliosphere -- {cmd}")

    def heard(tag):
        """Source addresses the destination heard <tag>-... from, after the last mark"""
        lines = isp.succeed("cat /tmp/dest.log").splitlines()
        lines = lines[max((i for i, l in enumerate(lines) if l == "MARK"), default=-1) + 1:]
        return {l.split()[0] for l in lines if len(l.split()) == 2 and l.split()[1].startswith(tag + "-")}

    def send(proto, tag, seconds):
        port = 7000 if proto == "tcp" else 7001
        remote.execute(f"timeout {seconds + 5} python3 {SENDER} {proto} {DEST} {port} {tag} {seconds} >/dev/null 2>&1")

    def through_tunnel(tag, proto="tcp", timeout=120):
        """Wait until the phone's traffic reaches the destination, not from the router's WAN address"""
        end = time.time() + timeout
        while time.time() < end:
            send(proto, tag, 2)
            got = heard(tag)
            if got and wan_ip not in got:
                return got
            assert wan_ip not in got, f"{tag}: the phone's traffic left through the WAN directly: {got}"
            time.sleep(3)
        raise Exception(f"{tag}: never reached the destination through the tunnel")

    start_all()
    router.wait_for_unit("multi-user.target")
    wan_ip = lease(router, "wan", "192.0.2")
    for u in ("dest.service", "openvpn-up.service", "dante.service"):
        isp.wait_for_unit(u)
    isp.wait_until_succeeds("ip link show wg-up", timeout=60)   # networkd sets it up, no unit of its own
    router.wait_for_unit("container@heliosphere.service", timeout=300)

    with subtest("set up: egress configs uploaded, the phone connected"):
        router.succeed("helios-ctl remote-egress-upload wireguard < /etc/killswitch/wg-out.conf")
        router.succeed(f"helios-ctl remote-egress-upload openvpn < {OVPN}")
        container("systemctl start wg-quick-wg-out.service")
        router.wait_until_succeeds("test -s /var/lib/heliosphere/wireguard/public", timeout=60)
        srv_pub = router.succeed("cat /var/lib/heliosphere/wireguard/public").strip()
        remote.succeed(
            "ip link add wg0 type wireguard"
            " && printf %s ${keys.remote.priv} > /root/wg.key"
            f" && wg set wg0 private-key /root/wg.key peer {srv_pub} endpoint {wan_ip}:51820 allowed-ips 0.0.0.0/0 persistent-keepalive 5"
            " && ip addr add 10.61.0.2/24 dev wg0 && ip link set wg0 up"
            f" && ip route add {DEST}/32 dev wg0 && ip route add 8.8.8.8/32 dev wg0")
        try:
            remote.wait_until_succeeds("ping -c 1 -W 2 10.61.0.1", timeout=60)
        except Exception:
            for m, cmd in ((remote, "wg show; ip route"),
                           (router, "nft list table inet heliopause-nat; nft list chain inet heliopause forward | head -40"),
                           (router, "nixos-container run heliosphere -- sh -c 'wg show; ip rule; ip route; nft list ruleset | head -80; sysctl net.ipv4.conf.all.rp_filter net.ipv4.conf.eth0.rp_filter'"),
                           (router, "tcpdump -c 10 -ni any udp port 51820 & sleep 4; kill %1")):
                print(m.execute(cmd)[1])
            raise
        # AdGuard only serves DNS once its setup wizard is done (a step of the
        # install): stand in for it on the router's address the container uses, and
        # answer everything NXDOMAIN. What's tested is that the phone's DNS is
        # redirected there and never leaves on the WAN, not AdGuard itself.
        router.succeed("systemctl stop adguardhome.service")
        router.succeed("systemd-run --unit dns-stand-in /run/current-system/sw/bin/python3 -c '"
                       "import socket\n"
                       "s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.bind((\"10.60.0.1\", 53))\n"
                       "while True:\n"
                       "    d, a = s.recvfrom(512); s.sendto(d[:2] + bytes([0x81, 0x83]) + d[4:6] + bytes(6) + d[12:], a)'")
        router.wait_until_succeeds("dig +time=3 +tries=1 @10.60.0.1 example.com | grep -q 'status: NXDOMAIN'", timeout=30)

    def outage(mode, unit, killswitch=True):
        tag = mode
        isp.succeed("echo MARK >> /tmp/dest.log")
        before = through_tunnel(f"{tag}-probe")
        print(f"{mode}: the destination hears the phone from {before} (router WAN {wan_ip})")
        rules = container("ip rule")
        # Sessions that keep sending through the outage
        remote.succeed(f"(timeout 40 python3 {SENDER} tcp {DEST} 7000 {tag}-tcp >/tmp/{tag}-tcp.out 2>&1 &)")
        remote.succeed(f"(timeout 40 python3 {SENDER} udp {DEST} 7001 {tag}-udp >/tmp/{tag}-udp.out 2>&1 &)")
        isp.wait_until_succeeds(f"grep -q ' {tag}-tcp-' /tmp/dest.log && grep -q ' {tag}-udp-' /tmp/dest.log", timeout=30)
        udp_before = heard(f"{tag}-udp")
        assert wan_ip not in heard(f"{tag}-tcp") | udp_before, f"{mode}: sessions not through the tunnel"
        # Everything the phone could send in clear on the WAN
        router.succeed(f"(tcpdump -ni wan -U -w /tmp/{tag}.pcap 'host {DEST} or (host 8.8.8.8 and port 53)' >/dev/null 2>&1 &)")
        time.sleep(2)
        isp.succeed("echo MARK >> /tmp/dest.log")
        container(f"systemctl stop {unit}")
        time.sleep(8)                                         # the sessions keep trying
        send("tcp", f"{tag}-new-tcp", 3)                      # new connections during the outage
        send("udp", f"{tag}-new-udp", 3)
        dns = remote.execute("dig +time=5 +tries=1 @8.8.8.8 example.com")[1]
        time.sleep(2)
        router.succeed("pkill tcpdump; sleep 1")
        captured = router.succeed(f"tcpdump -nr /tmp/{tag}.pcap 2>/dev/null | wc -l").strip()
        print(router.succeed(f"tcpdump -nr /tmp/{tag}.pcap 2>/dev/null | head -20"))
        leaked = {t: heard(f"{tag}-{t}") for t in ("tcp", "udp", "new-tcp", "new-udp")}
        print(f"{mode}: {captured} packet(s) on the WAN outside the tunnel; heard during the outage: {leaked}; DNS: {dns.strip()[:300]}")
        # (what the tunnel still carried before it stopped isn't a leak: only the WAN address is)
        via_wan = {t: wan_ip in got for t, got in leaked.items()}
        if killswitch:
            assert captured == "0", f"{mode}: the phone's traffic appeared on the WAN while the tunnel was down"
            assert not any(via_wan.values()), f"{mode}: the destination heard the phone from the WAN address: {leaked}"
            assert "status: NXDOMAIN" in dns, f"{mode}: the router didn't answer DNS while the tunnel was down"
        else:
            assert int(captured) > 0 and any(via_wan.values()), \
                f"{mode}: the control (kill switch off) saw no leak, so the capture proves nothing"
        container(f"systemctl start {unit}")
        isp.succeed("echo MARK >> /tmp/dest.log")
        after = through_tunnel(f"{tag}-recovered")
        print(f"{mode}: back through the tunnel, heard from {after}")
        if udp_before:
            through_tunnel(f"{tag}-recovered-udp", proto="udp", timeout=60)
        # Restarting the tunnel leaves the same routing rules, nothing stale
        container(f"systemctl restart {unit}")
        through_tunnel(f"{tag}-restarted")
        assert container("ip rule") == rules, f"{mode}: routing rules changed across restarts:\n{rules}\n---\n{container('ip rule')}"

    def switch(spec):
        status, out = router.execute(f"/run/booted-system/specialisation/{spec}/bin/switch-to-configuration test 2>&1", timeout=600)
        # avahi-daemon sometimes trips on its own PID file when restarted mid-switch;
        # anything else failing is a real failure
        failed = router.succeed("systemctl list-units --state=failed --plain --no-legend | awk '{print $1}'").split()
        assert status == 0 or (status == 4 and set(failed) <= {"avahi-daemon.service"}), f"switch to {spec}: {status} {failed}\n{out[-2000:]}"
        if failed:
            router.succeed("systemctl reset-failed; systemctl restart avahi-daemon.service")
        router.wait_for_unit("container@heliosphere.service", timeout=300)
        remote.wait_until_succeeds("ping -c 1 -W 2 10.61.0.1", timeout=120)

    with subtest("WireGuard way out: kill switch holds, recovers"):
        outage("wireguard", "wg-quick-wg-out.service")

    with subtest("control: kill switch off, the same outage is visible on the WAN"):
        switch("nokill")
        outage("nokill", "wg-quick-wg-out.service", killswitch=False)

    with subtest("OpenVPN way out: kill switch holds, recovers"):
        switch("openvpn")
        outage("openvpn", "openvpn-egress.service")

    with subtest("SOCKS5 way out: kill switch holds, recovers"):
        switch("proxy")
        outage("proxy", "helios-egress-proxy.service")
  '';
}
