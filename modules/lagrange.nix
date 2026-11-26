# Lagrange: interfaces and VLANs, the fixed points everything else sits on.
# Interfaces, VLANs, mDNS reflection, NTP. DHCP and local names: galileo.nix.
# Router-on-a-stick: one NIC trunked to a managed switch carries the WAN
# (modem on a switch port in VLAN helios.interfaces.wanVlan) and all LAN VLANs.
{ config, lib, pkgs, ... }:

let
  cfg = config.helios;
  I = cfg.internal;
  lans = I.byName;                 # state.json networks (Photosphere → Networks)
  hasRescue = cfg.interfaces.rescueMac != null;

  # Router ports (Photosphere → Hardware → This router's ports). With any port
  # mapped as access or trunk, the trunk NIC and those ports join a VLAN-aware
  # bridge, lan0, and the network interfaces sit on lan0 instead of trunk0.
  # With none, the layout is the plain router-on-a-stick one.
  ports = I.routerPorts;
  wanPort = lib.findFirst (p: p.mode == "wan") null ports;
  bridged = builtins.filter (p: p.mode == "access" || p.mode == "trunk") ports;
  useBridge = bridged != [ ];
  wanVlan = cfg.interfaces.wanVlan;
  wanOnTrunk = wanPort == null;           # modem behind the switch, as a VLAN on the trunk
  vlanIds = map I.vlanOf (lib.attrNames lans);
  tagged = ids: map (id: { VLAN = id; }) ids;
  portVlans = p:
    if p.mode == "access" then let id = I.vlanOf p.network; in [{ VLAN = id; PVID = id; EgressUntagged = id; }]
    else tagged (map I.vlanOf (I.portNets p) ++ lib.optional ((p.wan or false) && wanOnTrunk) wanVlan);
  # Rename by permanent MAC so names survive reboots and USB re-plugs;
  # switch-chip (DSA) ports keep their kernel names unless they're the WAN port.
  portLink = p:
    if p.match ? mac then { matchConfig.PermanentMACAddress = lib.toLower p.match.mac; linkConfig.Name = I.portIface p; }
    else { matchConfig.OriginalName = p.match.name; linkConfig.Name = I.portIface p; };
in
{
  networking.hostName = cfg.hostName;
  networking.useNetworkd = true;
  networking.useDHCP = false;
  systemd.network.enable = true;

  boot.kernel.sysctl = {
    "net.ipv4.conf.all.forwarding" = true;
    "net.ipv6.conf.all.forwarding" = true;
    # Hardening
    "net.ipv4.conf.all.rp_filter" = 2;              # loose: plays nice with Tailscale
    "net.ipv4.conf.default.rp_filter" = 2;
    "net.ipv4.conf.all.accept_redirects" = 0;
    "net.ipv4.conf.default.accept_redirects" = 0;
    "net.ipv6.conf.all.accept_redirects" = 0;
    "net.ipv6.conf.default.accept_redirects" = 0;
    "net.ipv4.conf.all.send_redirects" = 0;
    "net.ipv4.conf.all.accept_source_route" = 0;
    "net.ipv6.conf.all.accept_source_route" = 0;
    "net.ipv4.conf.all.log_martians" = 1;
    "net.ipv4.tcp_syncookies" = 1;
    "net.ipv4.icmp_echo_ignore_broadcasts" = 1;
    "kernel.dmesg_restrict" = 1;
    "kernel.kptr_restrict" = 2;
  };

  # ---------------------------------------------------------------- links
  # Names pinned by MAC so a USB NIC never swaps places with an onboard one
  systemd.network.links =
    { "10-trunk" = { matchConfig.PermanentMACAddress = cfg.interfaces.trunkMac; linkConfig.Name = "trunk0"; }; }
    // lib.optionalAttrs hasRescue {
      "10-rescue" = { matchConfig.PermanentMACAddress = cfg.interfaces.rescueMac; linkConfig.Name = "rescue0"; };
    }
    // lib.listToAttrs (map (p: lib.nameValuePair "11-port-${I.portIface p}" (portLink p)) ports);

  systemd.network.netdevs =
    lib.optionalAttrs wanOnTrunk {
      "20-wan" = { netdevConfig = { Kind = "vlan"; Name = "wan"; }; vlanConfig.Id = wanVlan; };
    }
    // lib.optionalAttrs useBridge {
      "15-lan0" = {
        netdevConfig = { Kind = "bridge"; Name = "lan0"; };
        bridgeConfig = { VLANFiltering = true; DefaultPVID = "none"; STP = false; };
      };
    }
    // lib.mapAttrs' (name: v: lib.nameValuePair "20-${name}" {
         netdevConfig = { Kind = "vlan"; Name = name; };
         vlanConfig.Id = v.vlan;
       }) lans;

  systemd.network.networks =
    {
      "30-trunk" = if useBridge then {
        matchConfig.Name = "trunk0";
        networkConfig = { Bridge = "lan0"; LinkLocalAddressing = "no"; };
        bridgeVLANs = tagged (vlanIds ++ lib.optional wanOnTrunk wanVlan);
        linkConfig.RequiredForOnline = "enslaved";
      } else {
        matchConfig.Name = "trunk0";
        vlan = lib.optional wanOnTrunk "wan" ++ lib.attrNames lans;
        networkConfig.LinkLocalAddressing = "no";
        linkConfig.RequiredForOnline = "carrier";
      };
      "40-wan" = {
        matchConfig.Name = "wan";
        networkConfig = { DHCP = "yes"; IPv6AcceptRA = true; };
        dhcpV6Config = { WithoutRA = "solicit"; PrefixDelegationHint = "::/56"; };
        linkConfig.RequiredForOnline = "routable";
      };
    }
    // lib.optionalAttrs useBridge {
      # The bridge itself carries every VLAN so the network interfaces on it see their traffic
      "31-lan0" = {
        matchConfig.Name = "lan0";
        vlan = lib.optional wanOnTrunk "wan" ++ lib.attrNames lans;
        bridgeVLANs = tagged (vlanIds ++ lib.optional wanOnTrunk wanVlan);
        networkConfig = { LinkLocalAddressing = "no"; ConfigureWithoutCarrier = true; };
        linkConfig.RequiredForOnline = "carrier";
      };
    }
    // lib.listToAttrs (map (p: lib.nameValuePair "32-port-${I.portIface p}" {
         matchConfig.Name = I.portIface p;
         networkConfig = { Bridge = "lan0"; LinkLocalAddressing = "no"; ConfigureWithoutCarrier = true; };
         bridgeVLANs = portVlans p;
         linkConfig.RequiredForOnline = "no";
       }) bridged)
    // lib.optionalAttrs hasRescue {
      # Plug a laptop in here (give it .2/24) if the trunk NIC or the switch
      # ever dies — SSH in and fix things without the network.
      "35-rescue" = {
        matchConfig.Name = "rescue0";
        address = [ "${cfg.interfaces.rescueAddress}/24" ];
        networkConfig.ConfigureWithoutCarrier = true;
        linkConfig.RequiredForOnline = "no";
      };
    }
    // lib.mapAttrs' (name: v: lib.nameValuePair "50-${name}" {
         matchConfig.Name = name;
         address = [ "${v.prefix}.1/24" ];
         networkConfig = {
           ConfigureWithoutCarrier = true;
         } // lib.optionalAttrs (v.ipv6 != null) {
           DHCPPrefixDelegation = true;   # carve a /64 from the ISP's delegated prefix
           IPv6SendRA = true;             # SLAAC + advertise the router as DNS
         };
         dhcpPrefixDelegationConfig = lib.mkIf (v.ipv6 != null) { SubnetId = v.ipv6; };
         linkConfig.RequiredForOnline = "no";
       }) lans;

  # Printer discovery and casting across networks with "mdns" access. Reflecting
  # mDNS into a network leaks LAN names there. An empty allowInterfaces would mean
  # "every interface", so fall back to lo when no network opts in.
  services.avahi = let m = I.where (a: a.mdns); in {
    enable = true;
    reflector = lib.length m > 1;
    allowInterfaces = if m == [ ] then [ "lo" ] else m;
  };

  environment.systemPackages = with pkgs; [ tcpdump nftables ethtool dnsutils wakeonlan ];

  # NTP for the LAN
  services.chrony = {
    enable = true;
    extraConfig = lib.concatMapStrings (n: "allow ${cfg.internal.net n}\n") cfg.internal.names;
  };
}
