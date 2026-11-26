# Galileo: addresses and names. Kea hands out addresses on every network
# (fixed ones for named devices, from state.json via Photosphere → Devices),
# and every named device answers as <name>.<domain> from any network.
# Named after Galileo, who named Jupiter's moons (and the probe that visited them).
{ config, lib, ... }:

let
  cfg = config.helios;
  I = cfg.internal;
  lans = I.byName;
in
{
  # ---------------------------------------------------------------- DHCP
  services.kea.dhcp4 = {
    enable = true;
    settings = {
      interfaces-config.interfaces = lib.attrNames lans;
      lease-database = { type = "memfile"; persist = true; name = "/var/lib/kea/dhcp4.leases"; };
      valid-lifetime = 7200;
      subnet4 = map (name: let v = lans.${name}; in {
        id = v.vlan;
        subnet = "${v.prefix}.0/24";
        interface = name;
        pools = [{ pool = "${v.prefix}.100 - ${v.prefix}.250"; }];
        option-data = [
          { name = "routers"; data = "${v.prefix}.1"; }
          { name = "domain-name-servers"; data = "${v.prefix}.1"; }
          { name = "ntp-servers"; data = "${v.prefix}.1"; }
          { name = "domain-name"; data = cfg.domain; }
          { name = "domain-search"; data = cfg.domain; }
        ];
        # Fixed IPs (.20–.99) come from state.json via Photosphere → Devices
        reservations = map (d: {
          hw-address = lib.toLower d.mac;
          ip-address = d.ip;
          hostname = d.name;
        }) (builtins.filter (d: d.vlan == name) I.withIp);
      }) (lib.attrNames lans);
    };
  };

  # ----------------------------------------------------------- local names
  # Every named device answers as <name>.<domain> from any VLAN (AdGuard reads /etc/hosts).
  networking.hosts =
    { ${I.adminGw} = [ "${cfg.hostName}.${cfg.domain}" cfg.hostName ]; }
    // builtins.listToAttrs (map (d: lib.nameValuePair d.ip [ "${d.name}.${cfg.domain}" d.name ]) I.withIp);
}
