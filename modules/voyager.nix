# Voyager: the WAN edge. QoS (CAKE / fq_codel SQM) and UPnP / NAT-PMP port
# mapping. Settings come from state.json (Photosphere → QoS / UPnP).
{ config, lib, pkgs, ... }:

let
  cfg = config.helios;
  # QoS settings (state.json "qos", Photosphere → QoS). Off until rates are set.
  qos = {
    enabled = false; down = null; up = null;
    algorithm = "cake";        # cake | fq_codel (HTB + fq_codel: lighter on weak CPUs)
    link = "ethernet";         # docsis | ethernet | vdsl | adsl | custom
    overhead = 38; mpu = 84; atm = false;   # used when link = custom
    fairness = "device";       # device (per home device, NAT-aware) | triple | flow
    priority = "diffserv3";    # diffserv4 | diffserv3 | besteffort
    ackFilter = false;         # helps a much smaller upload (cable, DSL)
    rtt = "internet";          # internet | regional | metro
    wash = true;               # clear incoming DSCP marks after classifying
  } // (cfg.state.qos or { });
  # Link-layer overhead: CAKE keywords, and the equivalent for fq_codel's stab
  cakeLink = {
    docsis = "docsis"; ethernet = "ethernet"; vdsl = "pppoe-ptm"; adsl = "pppoe-llcsnap";
    custom = "overhead ${toString qos.overhead} mpu ${toString qos.mpu} ${if qos.atm then "atm" else "noatm"}";
  }.${qos.link};
  stab = {
    docsis = "overhead 18 mpu 64 linklayer ethernet"; ethernet = "overhead 38 mpu 84 linklayer ethernet";
    vdsl = "overhead 30 linklayer ethernet"; adsl = "overhead 40 linklayer atm";
    custom = "overhead ${toString qos.overhead} mpu ${toString qos.mpu} linklayer ${if qos.atm then "atm" else "ethernet"}";
  }.${qos.link};
  fair = {
    device = { up = "dual-srchost nat"; down = "dual-dsthost nat"; };
    triple = { up = "triple-isolate nat"; down = "triple-isolate nat"; };
    flow = { up = "flows"; down = "flows"; };
  }.${qos.fairness};
  cake = dir: rate: "cake bandwidth ${rate} ${cakeLink} ${fair.${dir}} ${qos.priority} ${qos.rtt}"
    + lib.optionalString (dir == "up" && qos.ackFilter) " ack-filter"
    + lib.optionalString (dir == "down") " ingress${lib.optionalString qos.wash " wash"}";
  shape = dev: dir: rate: if qos.algorithm == "cake" then ''
    tc qdisc replace dev ${dev} root ${cake dir rate}
  '' else ''
    tc qdisc del dev ${dev} root 2>/dev/null || true
    tc qdisc add dev ${dev} root handle 1: stab ${stab} htb default 10
    tc class add dev ${dev} parent 1: classid 1:10 htb rate ${rate} ceil ${rate}
    tc qdisc add dev ${dev} parent 1:10 fq_codel
  '';
  # UPnP: blockedDevices (device names) and blockedPorts ("3389", "6000-6100") are
  # denied before anything is allowed (Photosphere → UPnP)
  upnp = { enabled = true; blockedDevices = [ ]; blockedPorts = [ ]; } // (cfg.state.upnp or { });
  blockedIps = cfg.internal.ipsWhere (d: lib.elem d.name upnp.blockedDevices);
  denyLines = map (ip: "deny 0-65535 ${ip}/32 0-65535") blockedIps
    ++ map (p: "deny ${toString p} 0.0.0.0/0 0-65535") upnp.blockedPorts;
  upnpNets = cfg.internal.where (a: a.upnp);
in
{
  # ---------------------------------------------------------------- QoS (SQM)
    # Shapes the WAN in both directions to just under the line's speed, so the
    # queue lives here (short, fair) instead of in the modem. Download is
    # shaped through an IFB mirror of the WAN's ingress.
    boot.kernelModules = [ "sch_cake" "sch_fq_codel" "sch_htb" "ifb" "act_mirred" ];

    systemd.services.helios-sqm = lib.mkIf (qos.enabled && qos.down != null && qos.up != null) {
      description = "HeliOS QoS (${qos.algorithm}) on wan";
      after = [ "network-online.target" "sys-subsystem-net-devices-wan.device" ];
      wants = [ "network-online.target" ];
      bindsTo = [ "sys-subsystem-net-devices-wan.device" ];
      wantedBy = [ "multi-user.target" ];
      restartTriggers = [ (builtins.toJSON qos) ];
      path = [ pkgs.iproute2 ];
      serviceConfig = { Type = "oneshot"; RemainAfterExit = true; };
      script = ''
        ${shape "wan" "up" qos.up}
        ip link show ifb4wan >/dev/null 2>&1 || ip link add ifb4wan type ifb
        ip link set ifb4wan up
        tc qdisc del dev wan ingress 2>/dev/null || true
        tc qdisc add dev wan handle ffff: ingress
        tc filter add dev wan parent ffff: matchall action mirred egress redirect dev ifb4wan
        ${shape "ifb4wan" "down" qos.down}      '';
      preStop = ''
        tc qdisc del dev wan root 2>/dev/null || true
        tc qdisc del dev wan ingress 2>/dev/null || true
        ip link del ifb4wan 2>/dev/null || true
      '';
    };

  # ----------------------------------------------------------- UPnP / NAT-PMP
    # Only networks with "upnp" access may ask; Heliosphere never can.
    # secure_mode = a device can only open ports to itself.
    services.miniupnpd = lib.mkIf (upnp.enabled && upnpNets != [ ]) {
      enable = true;
      externalInterface = "wan";
      internalIPs = upnpNets;
      natpmp = true;
      upnp = true;
      appendConfig = ''
        http_port=5000
        secure_mode=yes
        system_uptime=yes
        clean_ruleset_interval=600
        lease_file=/run/miniupnpd.leases
        ${lib.concatStringsSep "\n        " denyLines}
        ${lib.concatMapStringsSep "\n        " (n: "allow 1024-65535 ${cfg.internal.net n} 1024-65535") upnpNets}
        deny 0-65535 0.0.0.0/0 0-65535
      '';
    };
}
