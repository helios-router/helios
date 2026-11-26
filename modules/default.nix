# HeliOS — a NixOS router. Import this directory (or the flake's
# nixosModules.default) and set helios.* in your site.nix.
{
  imports = [
    ./options.nix
    ./lagrange.nix       # Lagrange: interfaces, VLANs, mDNS, NTP
    ./galileo.nix        # Galileo: DHCP and local names
    ./heliopause.nix     # firewall (+ Magnetosphere enforcement)
    ./magnetosphere.nix  # DNS filtering (AdGuard Home)
    ./airlock.nix        # Airlock: admin user, SSH
    ./tailscale.nix      # admin exit + subnet router
    ./heliosphere.nix    # filtered family exit (optional)
    ./solar-wind.nix     # push notifications (ntfy)
    ./photosphere.nix    # Cockpit pages + helios-ctl + timers
    ./voyager.nix        # Voyager: QoS and UPnP
    ./hubble.nix         # Hubble: watchdog and traffic stats
    ./pioneer.nix        # Pioneer: print server (optional)
    ./discovery.nix      # Discovery: AI log triage (optional)
    ./rosetta.nix        # Rosetta: hardware detection, UPS, LLDP, vulnerability scan
    ./recovery.nix       # restart failed services, alert when they stay down
  ];
}
