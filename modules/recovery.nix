# Service recovery: keep the router's services up, and say so loudly when that fails.
#
#  1. Long-running services restart on failure (5 s apart, at most 5 times in
#     10 minutes; then systemd gives up instead of looping).
#  2. When a service ends up failed (restarts exhausted, or a one-shot like the
#     firewall load failed), helios-unit-failed@ sends an urgent notification
#     with the last log lines.
#  3. helios-recover (every 10 min) retries failed services with a cooldown,
#     sends "recovered" when one comes back, and escalates once when it can't.
# The hardware watchdog (hubble.nix) covers a hung kernel or systemd.
#
# Reboots: most updates apply without one, but a new kernel only runs after a
# reboot. helios-reboot-check tells you once; the optional reboot window
# (state.json "maintenance", Photosphere → Advanced) reboots then, only if needed.
{ config, lib, pkgs, ... }:

let
  cfg = config.helios;
  I = cfg.internal;
  ctl = "${I.helios-ctl}/bin/helios-ctl";

  # { unit = restart?; } for what this site actually runs. Conditions mirror the
  # modules' own, so no stub units appear for services that are off.
  watched = {
    kea-dhcp4-server = true;
    adguardhome = true;
    chronyd = true;
    sshd = true;
    nftables = false;            # one-shot: alert only (a broken ruleset needs a human)
    systemd-networkd = false;    # has its own restart policy
  }
  // lib.optionalAttrs ((cfg.state.qos.enabled or false) && (cfg.state.qos.down or null) != null) { helios-sqm = false; }
  // lib.optionalAttrs (config.services.miniupnpd.enable) { miniupnpd = true; }
  // lib.optionalAttrs (I.on "notifications") { ntfy-sh = true; }
  // lib.optionalAttrs cfg.tailscale.enable { tailscaled = true; }
  // lib.optionalAttrs (I.on "printing") { cups = true; }
  // lib.optionalAttrs (I.on "remote") { "container@heliosphere" = true; }
  // lib.optionalAttrs (I.driverOn "lldp") { lldpd = true; }
  // lib.optionalAttrs (I.driverOn "nut" && I.upsDevices != [ ]) { upsd = true; upsmon = true; };

  win = { enabled = false; days = [ "sun" ]; time = "04:00"; } // ((cfg.state.maintenance or { }).rebootWindow or { });
  dayName = d: { mon = "Mon"; tue = "Tue"; wed = "Wed"; thu = "Thu"; fri = "Fri"; sat = "Sat"; sun = "Sun"; }.${d};
  onCalendar = "${lib.concatMapStringsSep "," dayName win.days} *-*-* ${win.time}:00";

  policy = name: restart: {
    unitConfig = {
      OnFailure = [ "helios-unit-failed@%n.service" ];
    } // lib.optionalAttrs restart {
      # Defaults only: a service module's own limits (e.g. AdGuard's) win
      StartLimitIntervalSec = lib.mkDefault 600;
      StartLimitBurst = lib.mkDefault 5;
    };
    serviceConfig = lib.optionalAttrs restart {
      Restart = lib.mkDefault "on-failure";
      RestartSec = lib.mkDefault "5s";
    };
  };
in
{
  systemd.services = lib.mapAttrs policy watched // {
    "helios-unit-failed@" = {
      description = "HeliOS: report failed unit %i";
      serviceConfig = { Type = "oneshot"; ExecStart = "${ctl} unit-failed %i"; };
    };
    helios-recover = {
      description = "HeliOS: retry failed services";
      serviceConfig = { Type = "oneshot"; ExecStart = "${ctl} recover"; };
    };
    helios-reboot-check = {
      description = "HeliOS: is a reboot needed for an update?";
      serviceConfig = { Type = "oneshot"; ExecStart = "${ctl} reboot-check"; };
    };
    helios-reboot-window = lib.mkIf win.enabled {
      description = "HeliOS: reboot in the maintenance window if an update needs it";
      serviceConfig = { Type = "oneshot"; ExecStart = "${ctl} reboot-window"; };
    };
  };
  systemd.timers.helios-recover = {
    wantedBy = [ "timers.target" ];
    timerConfig = { OnBootSec = "10min"; OnUnitActiveSec = "10min"; };
  };
  systemd.timers.helios-reboot-check = {
    wantedBy = [ "timers.target" ];
    timerConfig = { OnBootSec = "30min"; OnCalendar = "daily"; RandomizedDelaySec = "1h"; };
  };
  systemd.timers.helios-reboot-window = lib.mkIf win.enabled {
    wantedBy = [ "timers.target" ];
    # No Persistent: a window missed while the router was off is not caught up at boot
    timerConfig = { OnCalendar = onCalendar; Persistent = false; };
  };

  # What helios-ctl watches and may restart
  helios.internal.recoveryUnits = lib.mapAttrsToList (n: r: { unit = n; restart = r; }) watched;
}
