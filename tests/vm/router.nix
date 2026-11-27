# Router-on-a-stick end to end: every machine sits on one test segment (vlan 1)
# that plays the switch trunk; isp and the clients tag their own 802.1Q VLANs.
{ self }:

let common = import ./common.nix; in
{
  name = "helios-router";

  nodes = {
    isp = common.isp;
    trusted = common.client 20;
    iot = common.client 40;
    router = {
      imports = [ (common.router self) ];
      # A virtual hardware watchdog, so the test sees the "driver loads by itself" path
      virtualisation.qemu.options = [ "-device i6300esb" ];
    };
  };

  testScript = common.leaseHelper + ''

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
