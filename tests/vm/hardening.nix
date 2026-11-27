# The security changes, on a running router:
#  - a device blocked in Photosphere is cut off by its MAC (so IPv6/SLAAC can't
#    get around it), and the IPv6 DoH / WARP block sets load;
#  - Log AI: llama-cpp runs as the fixed `llm` account, can only reach
#    localhost, and the model folder is put back to owner-only;
#  - Remote access: OpenVPN and 3proxy run as their own accounts in the
#    Heliosphere container, the CRL and the proxy users file belong to them
#    (and nobody else can read them), and a LAN client can use the proxy;
#  - Admin account: Cockpit's PAM service asks for the password, then a TOTP
#    code once two-factor sign-in is on (set up through helios-ctl, as
#    Photosphere does); wrong passwords stop before the code's rate limit;
#    recovery codes work once; changes need the current password or code.
{ self }:

let
  common = import ./common.nix;
  proxyPassword = "correct-horse-battery-staple";   # test value only
in
{
  name = "helios-hardening";

  nodes = {
    isp = common.isp;
    trusted = common.client 20;
    blocked = common.client 20;

    router = { config, lib, nodes, pkgs, ... }: {
      imports = [ (common.router self) ];
      virtualisation.memorySize = 3072;
      # Sign in through Cockpit's PAM service from the shell; compute TOTP codes
      environment.systemPackages = [ pkgs.pamtester pkgs.oath-toolkit ];
      helios.logwatch.enable = lib.mkOverride 40 true;
      helios.heliosphere.enable = lib.mkOverride 40 true;
      # Client configs need a host to point at (the WAN address would do, with a warning)
      helios.heliosphere.publicHost = "vpn.example.test";
      helios.state = lib.mkOverride 40 (lib.recursiveUpdate
        (builtins.fromJSON (builtins.readFile ../../templates/state.json))
        {
          devices = [
            {
              mac = common.nodeMac lib nodes.blocked.virtualisation.test.nodeNumber;
              name = "blocked-pc"; vlan = "trusted"; profile = "default"; blocked = true;
            }
            # Named, so it isn't quarantined as an unknown device (those only reach the internet)
            {
              mac = common.nodeMac lib nodes.trusted.virtualisation.test.nodeNumber;
              name = "laptop"; vlan = "trusted"; profile = "default";
            }
          ];
          heliosphere.ingress = {
            tailscale.enabled = false;           # needs the real internet
            openvpn.enabled = true;
            proxy = { enabled = true; networks = [ "trusted" ]; users = [ "tester" ]; };
          };
        });
    };
  };

  testScript = { nodes, ... }: common.leaseHelper + ''
    import json
    import time

    def container(cmd):
        return router.succeed(f"nixos-container run heliosphere -- {cmd}")

    def owner_mode(path):
        return router.succeed(f"stat -c '%a %U' {path}").strip()

    start_all()
    router.wait_for_unit("multi-user.target")
    router.wait_until_succeeds("ip -4 -o addr show dev wan | grep -q 'inet 192.0.2.'", timeout=180)

    with subtest("a blocked device is cut off by MAC, others are not"):
        blocked_mac = blocked.succeed("cat /sys/class/net/vlan20/address").strip()
        rules = router.succeed("nft list ruleset")
        assert blocked_mac in rules, f"{blocked_mac} not in the ruleset (cutoff_mac)"
        trusted_mac = trusted.succeed("cat /sys/class/net/vlan20/address").strip()
        assert trusted_mac in router.succeed("nft list set inet heliopause known_macs"), f"{trusted_mac} not a known device"
        for s in ("set cutoff_mac", "set doh_v6", "set warp_v6"):
            assert s in rules, f"{s} missing from the ruleset"
        lease(trusted, "vlan20", "10.20.0")
        lease(blocked, "vlan20", "10.20.0")     # DHCP still answers; forwarding doesn't
        trusted.succeed("curl -fsS --max-time 10 http://192.0.2.1/ | grep -q helios-test-internet")
        blocked.fail("curl -fsS --max-time 10 http://192.0.2.1/")

    with subtest("Log AI: llama-cpp is the llm account, localhost only"):
        router.succeed("id llm")
        show = router.succeed("systemctl show llama-cpp -p User -p DynamicUser -p IPAddressDeny -p IPAddressAllow")
        print(show)
        assert "User=llm" in show, show
        assert "DynamicUser=no" in show, show
        assert "0.0.0.0/0" in show and "::/0" in show, "IPAddressDeny is not 'any'"
        assert "127.0.0.0/8" in show, "IPAddressAllow is not localhost"
        # A model folder someone loosened is put back to owner-only
        router.succeed("mkdir -p /var/lib/llm && chmod 755 /var/lib/llm && touch /var/lib/llm/m.gguf && chmod 644 /var/lib/llm/m.gguf")
        router.succeed("systemd-tmpfiles --create --prefix=/var/lib/llm")
        # Folder root:llm 0750, models llm's 0400, a root-owned copy included
        assert router.succeed("stat -c '%a %U %G' /var/lib/llm").strip() == "750 root llm"
        assert owner_mode("/var/lib/llm/m.gguf") == "400 llm", owner_mode("/var/lib/llm/m.gguf")

    with subtest("Remote access: container up, folders owned by their services"):
        router.wait_for_unit("container@heliosphere.service", timeout=300)
        # networkd leaves the container link alone (systemd's default for it runs a
        # DHCP server that takes port 67 from Kea), and only Kea serves DHCP
        router.succeed("networkctl list ve-heliosphere --no-legend | grep -q unmanaged")
        dhcp = router.succeed("ss -Hulpn 'sport = :67'")
        print(dhcp)
        assert all("kea-dhcp4" in line for line in dhcp.splitlines() if line.strip()), dhcp
        assert owner_mode("/var/lib/heliosphere/openvpn/server") == "700 heliosphere-ovpn"
        assert owner_mode("/var/lib/heliosphere/proxy") == "700 heliosphere-proxy"
        # Same uids inside the container, so the bind mounts keep their owners
        assert container("id -u heliosphere-ovpn").strip() == "1194"
        assert container("id -u heliosphere-proxy").strip() == "1195"
        # helios-ctl edits the state file in configDir, as Photosphere does
        router.succeed("mkdir -p /etc/nixos && cp ${builtins.toFile "state.json" (builtins.toJSON nodes.router.helios.state)} /etc/nixos/state.json")

    with subtest("OpenVPN: client added, CRL readable only by OpenVPN, server runs as heliosphere-ovpn"):
        res = json.loads(router.succeed("helios-ctl remote-ovpn-add phone"))
        assert res["ok"] and "<key>" in res["config"], res.get("error")
        assert owner_mode("/var/lib/heliosphere/openvpn/server/crl.pem") == "400 heliosphere-ovpn"
        assert owner_mode("/var/lib/heliosphere/openvpn/server/server.crt") == "600 root"
        assert owner_mode("/var/lib/heliosphere/openvpn/ca/ca.key") == "600 root"
        container("systemctl restart openvpn-ingress")
        router.wait_until_succeeds("nixos-container run heliosphere -- systemctl is-active openvpn-ingress", timeout=60)
        assert container("ps -o user= -C openvpn").split() == ["heliosphere-ovpn"]
        container("runuser -u heliosphere-ovpn -- cat /var/lib/heliosphere/openvpn/server/crl.pem >/dev/null")
        router.fail("nixos-container run heliosphere -- runuser -u nobody -- cat /var/lib/heliosphere/openvpn/server/crl.pem")
        # The WAN port goes to the container
        assert "udp dport 1194 dnat ip to" in router.succeed("nft list ruleset")

    with subtest("Proxy: users file is 3proxy's own, and a LAN client can use it"):
        router.succeed("printf '%s' '${proxyPassword}' | helios-ctl remote-proxy-user tester")
        assert owner_mode("/var/lib/heliosphere/proxy/users") == "400 heliosphere-proxy"
        container("systemctl restart 3proxy")
        router.wait_until_succeeds("nixos-container run heliosphere -- systemctl is-active 3proxy", timeout=60)
        assert container("ps -o user= -C 3proxy").split() == ["heliosphere-proxy"]
        router.fail("nixos-container run heliosphere -- runuser -u nobody -- cat /var/lib/heliosphere/proxy/users")
        try:
            trusted.wait_until_succeeds("nc -z -w 5 10.60.0.2 1080", timeout=60)
        except Exception:
            # Show where the path breaks before failing
            for m, cmd in ((router, "ip -4 addr show dev ve-heliosphere; ip -4 route; sysctl net.ipv4.ip_forward"),
                           (router, "nixos-container run heliosphere -- sh -c 'ip -4 addr; ip -4 route; ss -ltnp'"),
                           (router, "nixos-container run heliosphere -- systemctl cat 3proxy | grep -E 'ExecStart|^[a-z]' ; nixos-container run heliosphere -- sh -c 'cat /etc/3proxy.conf 2>/dev/null || true'"),
                           (router, "nc -z -w 5 10.60.0.2 1080 && echo router-reaches-proxy || echo router-cannot-reach-proxy"),
                           (router, "nft list chain inet heliopause forward"),
                           (trusted, "ip -4 route; ping -c1 -W2 10.60.0.2 || true")):
                print(m.execute(cmd)[1])
            raise
        trusted.succeed("curl -fsS --max-time 15 --socks5-hostname tester:${proxyPassword}@10.60.0.2:1080 http://192.0.2.1/ | grep -q helios-test-internet")
        trusted.fail("curl -fsS --max-time 15 --socks5-hostname tester:wrong-password-here@10.60.0.2:1080 http://192.0.2.1/")

    with subtest("Admin account: Cockpit sign-in with two-factor"):
        def login(password, code=None):
            # pamtester answers one prompt per line; the pause keeps the code for the second prompt
            feed = f"printf '%s\\n' '{password}'" + (f"; sleep 1; printf '%s\\n' '{code}'" if code is not None else "")
            return router.execute(f"({feed}) | pamtester cockpit admin authenticate 2>&1")[0] == 0
        def ctl(cmd, stdin=""):
            return json.loads(router.succeed(f"printf '%s' '{stdin}' | helios-ctl {cmd}"))
        def totp(secret):
            return router.succeed(f"oathtool --totp -b {secret}").strip()

        pam = router.succeed("cat /etc/pam.d/cockpit")
        print(pam)
        assert "pam_google_authenticator.so" in pam and "secret=/var/lib/helios/totp/" in pam, "no TOTP in Cockpit's PAM"
        assert any("requisite" in l and "pam_unix.so" in l for l in pam.splitlines()), "password isn't checked first"
        assert owner_mode("/var/lib/helios/totp") == "700 root"

        router.succeed("echo 'admin:first-password-1' | chpasswd")
        assert login("first-password-1"), "password-only sign-in failed with two-factor off"
        assert not login("wrong-password-1")

        # Turn it on as Photosphere does (qrencode must be on helios-ctl's PATH)
        b = ctl("account-2fa-begin")
        assert b["ok"] and b["qrSvg"] and "<svg" in b["qrSvg"], b
        secret = b["secret"]
        r = ctl("account-2fa-enable", totp(secret))
        assert r["ok"], r
        recovery = r["recoveryCodes"]
        assert owner_mode("/var/lib/helios/totp/admin") == "400 root"
        st = ctl("account")
        assert st["totp"] and st["totpEnforced"], st

        key = "/var/lib/helios/totp/admin"
        assert not login("first-password-1"), "signed in without a code"
        before = router.succeed(f"cat {key}")   # the try above updated its rate-limit line
        assert not login("wrong-password-1", totp(secret))
        assert router.succeed(f"cat {key}") == before, "a wrong password reached the code check (rate limit)"
        assert not login("first-password-1", "000000" if totp(secret) != "000000" else "111111")
        assert login("first-password-1", totp(secret)), "password + code failed"

        time.sleep(31)   # past the code's rate limit (3 tries / 30 s)
        assert login("first-password-1", recovery[0]), "recovery code failed"
        assert not login("first-password-1", recovery[0]), "a recovery code worked twice"

        # Changes need proof beyond the session
        assert not ctl("account-2fa-disable", "12345678")["ok"]
        assert not ctl("account-password", json.dumps({"current": "wrong-password-1", "new": "second-password-2"}))["ok"]
        r = ctl("account-password", json.dumps({"current": "first-password-1", "new": "second-password-2"}))
        assert r["ok"], r   # libcrypt checked the yescrypt hash
        assert ctl("account-2fa-disable", recovery[1])["ok"]
        assert not ctl("account")["totp"]
        assert login("second-password-2"), "password-only sign-in failed after turning two-factor off"
  '';
}
