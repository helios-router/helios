# Hubble: monitoring. Hardware watchdog and traffic stats (vnstat).
# Service recovery and reboot checks live in recovery.nix.
{ lib, options, ... }:

lib.mkMerge [
  {
    boot.kernelModules = [ "iTCO_wdt" ];
    services.vnstat.enable = true;
  }

  # ------------------------------------------------------- hardware watchdog
  # Reboot if the router hangs for 30 s; force a stuck shutdown after 10 min.
  # (Option name changed in NixOS 25.11.)
  (if options.systemd ? settings then {
    systemd.settings.Manager = { RuntimeWatchdogSec = "30s"; RebootWatchdogSec = "10min"; };
  } else {
    systemd.watchdog = { runtimeTime = "30s"; rebootTime = "10min"; };
  })
]
