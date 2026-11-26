# HeliOS — a NixOS router. Import this directory (or the flake's
# nixosModules.default) and set helios.* in your site.nix.
{
  imports = [
    ./options.nix
    ./network.nix        # interfaces, VLANs, mDNS, NTP
    ./galileo.nix        # Galileo: DHCP and local names
    ./heliopause.nix     # firewall (+ Magnetosphere enforcement)
    ./magnetosphere.nix  # DNS filtering (AdGuard Home)
    ./access.nix         # admin user, SSH
    ./tailscale.nix      # admin exit + subnet router
    ./heliosphere.nix    # filtered family exit (optional)
    ./solar-wind.nix     # push notifications (ntfy)
    ./photosphere.nix    # Cockpit pages + helios-ctl + timers
    ./voyager.nix        # Voyager: QoS and UPnP
    ./hubble.nix         # Hubble: watchdog and traffic stats
    ./printing.nix       # CUPS (optional)
    ./discovery.nix      # Discovery: AI log triage (optional)
    ./drivers.nix        # hardware detection, UPS, LLDP, vulnerability scan
    ./recovery.nix       # restart failed services, alert when they stay down
  ];
}
