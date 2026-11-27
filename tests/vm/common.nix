# Shared pieces of the router VM tests: every machine sits on one test segment
# (vlan 1) that plays the switch trunk; isp and the clients tag their own 802.1Q VLANs.
rec {
  # The MAC the test driver gives node number n's first NIC on vlan 1
  # (nixos/lib/qemu-common.nix qemuNicMac)
  nodeMac = lib: n: "52:54:00:12:01:${lib.optionalString (n < 16) "0"}${lib.toLower (lib.toHexString n)}";

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

  # The ISP: DHCP on the WAN VLAN (10) and "the internet", one page on 192.0.2.1
  isp = { pkgs, ... }: {
    imports = [
      (vlanHost 10 {
        address = [ "192.0.2.1/24" ];
        networkConfig = { DHCPServer = true; LinkLocalAddressing = "no"; ConfigureWithoutCarrier = true; };
        dhcpServerConfig = { PoolOffset = 100; PoolSize = 50; EmitRouter = true; EmitDNS = false; };
      })
    ];
    networking.firewall.enable = false;
    systemd.services.internet = {
      wantedBy = [ "multi-user.target" ];
      serviceConfig.ExecStart = "${pkgs.python3}/bin/python3 -m http.server 80 --directory ${pkgs.writeTextDir "index.html" "helios-test-internet\n"}";
    };
  };

  # A HeliOS router from the template, with everything that needs the real
  # internet or extra hardware off (a test turns back on what it covers with lib.mkOverride 40)
  router = self: { config, lib, ... }: {
    imports = [
      self.nixosModules.default
      ../../templates/site.nix
      trunkOnly
    ];
    virtualisation.memorySize = lib.mkDefault 2048;
    helios.admin.user = lib.mkForce "admin";
    # The test driver names machines by hostName; keep the script's `router`
    helios.hostName = lib.mkForce "router";
    helios.interfaces.trunkMac = lib.mkForce (nodeMac lib config.virtualisation.test.nodeNumber);
    helios.tailscale.enable = lib.mkForce false;
    helios.solarWind.enable = lib.mkForce false;
    helios.logwatch.enable = lib.mkForce false;
    helios.printing.enable = lib.mkForce false;
    helios.heliosphere.enable = lib.mkForce false;
  };

  # Python for the test scripts: wait for a DHCP lease and return the address
  leaseHelper = ''
    import re

    def lease(m, iface, prefix):
        m.wait_until_succeeds(f"ip -4 -o addr show dev {iface} | grep -q 'inet {prefix}\\.'", timeout=180)
        out = m.succeed(f"ip -4 -o addr show dev {iface}")
        return re.search(r"inet (\d+\.\d+\.\d+\.\d+)/", out).group(1)
  '';
}
