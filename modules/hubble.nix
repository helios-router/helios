# Hubble: monitoring. Watchdog and traffic stats (vnstat).
# Service recovery and reboot checks live in recovery.nix.
#
# Watchdog: systemd pings /dev/watchdog0 and the machine reboots if it stops (a
# hung kernel or PID 1). Hardware watchdog drivers load by themselves when the
# kernel finds the chip, so nothing is forced here (forcing the wrong one fails
# module loading at boot):
#   Intel chipsets  iTCO_wdt (via lpc_ich)      AMD chipsets  sp5100_tco
#   ACPI WDAT table wdat_wdt                    ARM / SoC     their built-in driver
#   VMs             i6300esb, ib700wdt
# If none appears, helios-watchdog loads softdog: a software watchdog that still
# reboots a hung system, but not a fully frozen kernel. systemd re-tries opening
# the device on every ping, so a watchdog that appears late is picked up.
{ lib, pkgs, options, ... }:

lib.mkMerge [
  {
    services.vnstat.enable = true;

    systemd.services.helios-watchdog = {
      description = "HeliOS: use the hardware watchdog, or softdog if there is none";
      wantedBy = [ "multi-user.target" ];
      after = [ "systemd-udev-trigger.service" "systemd-modules-load.service" ];
      path = [ pkgs.kmod pkgs.coreutils ];
      # exec, not oneshot: the wait below must not hold up boot
      serviceConfig = { Type = "exec"; };
      script = ''
        for _ in $(seq 30); do [ -e /dev/watchdog0 ] && break; sleep 1; done
        if [ -e /dev/watchdog0 ]; then
          echo "Hardware watchdog: $(cat /sys/class/watchdog/watchdog0/identity 2>/dev/null || echo unknown)"
        else
          echo "No hardware watchdog found; using softdog (reboots a hung system, not a frozen kernel)"
          modprobe softdog
        fi
      '';
    };
  }

  # Reboot if the router hangs for 30 s; force a stuck shutdown after 10 min.
  # (Option name changed in NixOS 25.11.)
  (if options.systemd ? settings then {
    systemd.settings.Manager = { RuntimeWatchdogSec = "30s"; RebootWatchdogSec = "10min"; };
  } else {
    systemd.watchdog = { runtimeTime = "30s"; rebootTime = "10min"; };
  })
]
