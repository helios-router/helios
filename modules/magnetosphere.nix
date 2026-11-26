# Magnetosphere — DNS filtering. AdGuard Home answers every VLAN; its global
# settings are the strictest profile (what unknown devices get). Per-device
# profiles (admin/adult/kid/default) are synced from state.json by helios-ctl
# on every apply, using the credentials in /var/lib/helios/adguard.json.
# Enforcement (DNS hijack, DoT/DoH/WARP blocks) lives in heliopause.nix.
{ config, lib, ... }:

{
  services.resolved.enable = false;          # frees port 53 for AdGuard
  networking.nameservers = [ "127.0.0.1" ];  # the router's own lookups go through AdGuard too
  services.adguardhome = {
    enable = true;
    mutableSettings = true;                  # filters/upstreams are set in AdGuard's UI (backed up nightly)
  };
}
