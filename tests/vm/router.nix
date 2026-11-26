# Router-on-a-stick end to end: every machine sits on one test segment (vlan 1)
# that plays the switch trunk; isp and the clients tag their own 802.1Q VLANs.
{ self }:

let
  # Plain eth1 on the trunk: no test-framework 192.168.1.x / 2001:db8 addresses,
  # so the only path between machines is through the router's VLANs.
  trunkOnly = { lib, ... }: {
    virtualisation.vlans = [ 1 ];
    networking.interfaces.eth1.ipv4.addresses = lib.mkForce [ ];
    networking.interfaces.eth1.ipv6.addresses = lib.mkForce [ ];
  };

  # A machine with one tagged VLAN interface "vlan<id>" on eth1
  vlanHost = id: extra: { lib, pkgs, ... }: {
    imports = [ trunkOnly ];
    networking.useNetworkd = true;
    networking.useDHCP = false;
    systemd.network.netdevs."20-vlan${toString id}" = {
      netdevConfig = { Kind = "vlan"; Name = "vlan${toString id}"; };
      vlanConfig.Id = id;
    };
    systemd.network.networks."30-eth1" = {
      matchConfig.Name = "eth1";
      vlan = [ "vlan${toString id}" ];
      networkConfig.LinkLocalAddressing = "no";
    };
    systemd.network.networks."31-vlan${toString id}" = lib.mkMerge [
      { matchConfig.Name = "vlan${toString id}"; }
      extra
    ];
    environment.systemPackages = [ pkgs.curl pkgs.netcat ];
  };

  client = id: vlanHost id { networkConfig = { DHCP = "ipv4"; IPv6AcceptRA = false; LinkLocalAddressing = "no"; }; };
in
{
  name = "helios-router";

  nodes = {
    isp = { pkgs, ... }: {
      imports = [
        (vlanHost 10 {
          address = [ "192.0.2.1/24" ];
          networkConfig = { DHCPServer = true; LinkLocalAddressing = "no"; ConfigureWithoutCarrier = true; };
          dhcpServerConfig = { PoolOffset = 100; PoolSize = 50; EmitRouter = true; EmitDNS = false; };
        })
      ];
      networking.firewall.enable = false;
      # "The internet": one page
      systemd.services.internet = {
        wantedBy = [ "multi-user.target" ];
        serviceConfig.ExecStart = "${pkgs.python3}/bin/python3 -m http.server 80 --directory ${pkgs.writeTextDir "index.html" "helios-test-internet\n"}";
      };
    };

    trusted = client 20;
    iot = client 40;

    router = { config, lib, ... }: {
      imports = [
        self.nixosModules.default
        ../../templates/site.nix
        trunkOnly
      ];
      virtualisation.memorySize = 2048;
      # A virtual hardware watchdog, so the test sees the "driver loads by itself" path
      virtualisation.qemu.options = [ "-device i6300esb" ];
      helios.admin.user = lib.mkForce "admin";
      # The test driver names machines by hostName; keep the script's `router`
      helios.hostName = lib.mkForce "router";
      # The MAC the test driver gives this node's first NIC on vlan 1 (nixos/lib/qemu-common.nix qemuNicMac)
      helios.interfaces.trunkMac = lib.mkForce (
        let n = config.virtualisation.test.nodeNumber;
        in "52:54:00:12:01:${lib.optionalString (n < 16) "0"}${lib.toLower (lib.toHexString n)}");
      # Nothing that needs the real internet or extra hardware
      helios.tailscale.enable = lib.mkForce false;
      helios.solarWind.enable = lib.mkForce false;
      helios.logwatch.enable = lib.mkForce false;
      helios.printing.enable = lib.mkForce false;
      helios.heliosphere.enable = lib.mkForce false;
    };
  };

  testScript = ''
    import re

    def lease(m, iface, prefix):
        m.wait_until_succeeds(f"ip -4 -o addr show dev {iface} | grep -q 'inet {prefix}\\.'", timeout=180)
        out = m.succeed(f"ip -4 -o addr show dev {iface}")
        return re.search(r"inet (\d+\.\d+\.\d+\.\d+)/", out).group(1)

    start_all()

    with subtest("router boots, services up, trunk renamed, WAN leased"):
        router.wait_for_unit("multi-user.target")
        for u in ["nftables.service", "kea-dhcp4-server.service", "systemd-networkd.service"]:
            router.wait_for_unit(u)
        router.succeed("ip link show trunk0")
        router.fail("ip link show eth1")
        router.succeed("ip -d link show wan | grep -q 'vlan protocol 802.1Q id 10'")
        router.succeed("ip link show wan | grep -q 'wan@trunk0'")
        wan_ip = lease(router, "wan", "192.0.2")
        print(f"router WAN lease: {wan_ip}")

    with subtest("watchdog: hardware driver autoloads and systemd uses it"):
        router.fail("systemctl is-failed --quiet systemd-modules-load.service")
        router.wait_until_succeeds("test -e /dev/watchdog0", timeout=60)
        ident = router.succeed("cat /sys/class/watchdog/watchdog0/identity").strip()
        assert "6300" in ident, f"expected the i6300ESB watchdog, got {ident!r}"
        router.wait_until_succeeds("grep -qx active /sys/class/watchdog/watchdog0/state", timeout=60)
        router.fail("lsmod | grep -q '^softdog'")

    with subtest("miniupnpd starts first time (waits for the LAN VLANs)"):
        router.wait_for_unit("miniupnpd.service")
        restarts = router.succeed("systemctl show -p NRestarts --value miniupnpd.service").strip()
        assert restarts == "0", f"miniupnpd needed {restarts} restart(s) to come up"
        # The failure handler is loaded (OnFailure= references it) but must never have run
        ran = router.succeed("systemctl show -p ExecMainStartTimestampMonotonic --value 'helios-unit-failed@miniupnpd.service.service'").strip()
        assert ran in ("", "0"), f"helios-unit-failed ran for miniupnpd (started at {ran})"
        # ...and the old symptom never appears in miniupnpd's log
        router.fail("journalctl -b --no-pager -o cat -u miniupnpd.service | grep -Eqi 'is down|valid interface name'")

    with subtest("DHCP on trusted and iot"):
        trusted_ip = lease(trusted, "vlan20", "10.20.0")
        last = int(trusted_ip.split(".")[3])
        assert 100 <= last <= 250, f"trusted got {trusted_ip}, outside .100-.250"
        trusted.succeed("ip route show default | grep -q 'via 10.20.0.1 dev vlan20'")
        iot_ip = lease(iot, "vlan40", "10.40.0")
        iot.succeed("ip route show default | grep -q 'via 10.40.0.1 dev vlan40'")
        print(f"trusted={trusted_ip} iot={iot_ip}")

    with subtest("internet through the router (NAT)"):
        isp.wait_for_unit("internet.service")
        isp.wait_for_open_port(80)
        # isp has no route to 10.0.0.0/8, so an answer proves masquerading
        isp.fail("ip route get 10.20.0.100 | grep -q via")
        trusted.succeed("curl -fsS --max-time 10 http://192.0.2.1/ | grep -q helios-test-internet")
        iot.succeed("curl -fsS --max-time 10 http://192.0.2.1/ | grep -q helios-test-internet")

    with subtest("isolation: iot cannot reach trusted, trusted reaches iot"):
        trusted.succeed(f"ping -c 2 -W 2 {iot_ip}")
        iot.fail(f"ping -c 2 -W 2 {trusted_ip}")

    with subtest("admin access: SSH from trusted only"):
        router.wait_for_unit("sshd.service")
        trusted.succeed("nc -z -w 5 10.20.0.1 22")
        iot.fail("nc -z -w 5 10.40.0.1 22")
        iot.fail("nc -z -w 5 10.20.0.1 22")

    with subtest("ruleset contains the heliopause tables"):
        rules = router.succeed("nft list ruleset")
        assert "table inet heliopause {" in rules, "table inet heliopause missing"
        assert "table inet heliopause-nat {" in rules, "table inet heliopause-nat missing"
  '';
}
