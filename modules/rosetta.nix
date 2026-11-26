# Rosetta: drivers, the translator between HeliOS and hardware it doesn't know yet.
# Built-in drivers (phase 1, see docs/drivers.md): LLDP neighbour discovery,
# hardware detection, UPS via Network UPS Tools, and the weekly vulnix scan.
# Everything here is switched on from state.json (Photosphere → Hardware).
{ config, lib, pkgs, ... }:

let
  cfg = config.helios;
  I = cfg.internal;
  ctl = "${I.helios-ctl}/bin/helios-ctl";

  # Hardening shared by every driver service. Drivers get their own dynamic
  # user, a read-only system, no new privileges and no capabilities; each
  # service adds back only what it needs.
  sandbox = extra: {
    DynamicUser = true;
    NoNewPrivileges = true;
    ProtectSystem = "strict";
    ProtectHome = true;
    PrivateTmp = true;
    PrivateDevices = true;
    ProtectKernelTunables = true;
    ProtectKernelModules = true;
    ProtectKernelLogs = true;
    ProtectControlGroups = true;
    ProtectClock = true;
    ProtectHostname = true;
    RestrictNamespaces = true;
    RestrictRealtime = true;
    RestrictSUIDSGID = true;
    LockPersonality = true;
    MemoryDenyWriteExecute = true;
    SystemCallArchitectures = "native";
    SystemCallFilter = [ "@system-service" "~@privileged @resources" ];
    CapabilityBoundingSet = "";
    UMask = "0077";
  } // extra;

  lldpOn = I.driverOn "lldp";

  # ------------------------------------------------------------------ UPS
  ups = I.upsDevices;
  nutOn = I.driverOn "nut" && ups != [ ];
  nutCfg = lib.recursiveUpdate I.drvCatalog.nutDefaults (I.driverSettings "nut");
  server = nutCfg.server;
  serverNets = if server.enabled or false then builtins.filter (n: lib.elem n I.names) (server.networks or [ ]) else [ ];
  nutSecrets = "/var/lib/helios-nut";
  spool = "/run/helios-ups";
  usbIds = u: lib.splitString ":" u.match.usb;
in
{
  # Firewall needs the UPS sharing networks (port 3493)
  helios.internal.nutServerNets = if nutOn then serverNets else [ ];

  # ------------------------------------------------------------- LLDP (listen only)
  services.lldpd = lib.mkIf lldpOn {
    enable = true;
    extraArgs = [ "-r" ];   # receive-only: learn neighbours, announce nothing
  };

  # ------------------------------------------------------- hardware detection
  # Read-only scan of the router's own ports, USB devices and LLDP neighbours.
  # Photosphere's "Scan again" also runs `helios-ctl detect` directly.
  systemd.services.helios-detect = {
    description = "HeliOS hardware detection";
    after = [ "network.target" ] ++ lib.optional lldpOn "lldpd.service";
    path = lib.optional lldpOn pkgs.lldpd;
    serviceConfig = sandbox {
      Type = "oneshot";
      ExecStart = "${ctl} detect";
      StateDirectory = "helios-detect";
      StateDirectoryMode = "0755";
      UMask = "0022";
      SupplementaryGroups = lib.optional lldpOn "_lldpd";   # lldpcli's socket
      RestrictAddressFamilies = [ "AF_UNIX" "AF_NETLINK" ];
      IPAddressDeny = "any";
    };
  };
  systemd.timers.helios-detect = {
    wantedBy = [ "timers.target" ];
    timerConfig = { OnBootSec = "2min"; OnUnitActiveSec = "6h"; RandomizedDelaySec = "10min"; };
  };

  # ------------------------------------------------------------------ NUT
  power.ups = lib.mkIf nutOn {
    enable = true;
    mode = if serverNets != [ ] then "netserver" else "standalone";
    ups = lib.listToAttrs (map (u: lib.nameValuePair u.id {
      driver = u.nutDriver or "usbhid-ups";
      port = "auto";
      description = u.label or u.id;
      directives = [
        "vendorid = ${lib.elemAt (usbIds u) 0}"
        "productid = ${lib.elemAt (usbIds u) 1}"
        # Our own low-battery thresholds instead of the UPS's: LB (and so the
        # shutdown) fires at whichever comes first
        "ignorelb"
        "override.battery.charge.low = ${toString nutCfg.shutdown.charge}"
        "override.battery.runtime.low = ${toString nutCfg.shutdown.runtime}"
      ] ++ lib.optional ((u.match.serial or "") != "") ''serial = "${u.match.serial}"'';
    }) ups);
    upsd.listen = [ { address = "127.0.0.1"; port = 3493; } ]
      ++ map (n: { address = I.gw n; port = 3493; }) serverNets;
    users = {
      upsmon = { passwordFile = "${nutSecrets}/upsmon.pw"; upsmon = "primary"; };
    } // lib.optionalAttrs (serverNets != [ ]) {
      # Other machines (e.g. a NAS) log in with this to watch the UPS and shut down too
      upsclient = { passwordFile = "${nutSecrets}/upsclient.pw"; upsmon = "secondary"; };
    };
    upsmon = {
      monitor = lib.listToAttrs (map (u: lib.nameValuePair u.id {
        system = "${u.id}@localhost";
        user = "upsmon";
        passwordFile = "${nutSecrets}/upsmon.pw";
        type = "primary";
        powerValue = 1;
      }) ups);
      settings = {
        NOTIFYCMD = "${ctl} ups-event";
        NOTIFYFLAG = map (t: [ t "SYSLOG+EXEC" ])
          [ "ONLINE" "ONBATT" "LOWBATT" "FSD" "COMMOK" "COMMBAD" "SHUTDOWN" "REPLBATT" "NOCOMM" "NOPARENT" ];
      };
    };
  };

  # Random passwords for upsd's users, made once before NUT starts
  systemd.services.helios-nut-secrets = lib.mkIf nutOn {
    description = "HeliOS: NUT passwords";
    wantedBy = [ "multi-user.target" ];
    before = [ "upsd.service" "upsmon.service" ];
    requiredBy = [ "upsd.service" "upsmon.service" ];
    serviceConfig = { Type = "oneshot"; RemainAfterExit = true; UMask = "0077"; };
    script = ''
      mkdir -p ${nutSecrets}
      for u in upsmon upsclient; do
        [ -s ${nutSecrets}/$u.pw ] || ${pkgs.openssl}/bin/openssl rand -hex 24 > ${nutSecrets}/$u.pw
      done
    '';
  };

  # UPS events: upsmon's NOTIFYCMD runs as the unprivileged nutmon user and
  # only drops an event file here; a root unit forwards it as a notification,
  # so the NUT side never sees the notification token.
  systemd.tmpfiles.rules = lib.mkIf nutOn [ "d ${spool} 0700 ${config.power.ups.upsmon.user} root -" ];
  systemd.paths.helios-ups-forward = lib.mkIf nutOn {
    wantedBy = [ "multi-user.target" ];
    pathConfig.DirectoryNotEmpty = spool;
  };
  systemd.services.helios-ups-forward = lib.mkIf nutOn {
    description = "HeliOS: forward UPS events as notifications";
    serviceConfig = { Type = "oneshot"; ExecStart = "${ctl} ups-forward"; };
  };

  # ------------------------------------------------------ weekly vulnix scan
  # Checks everything in the running system (HeliOS, drivers and nixpkgs)
  # against the NVD. The scan runs sandboxed; the "+" step that summarises it
  # and sends the notification runs as root.
  systemd.services.helios-vulnix = {
    description = "HeliOS: weekly vulnerability scan (vulnix)";
    after = [ "network-online.target" ];
    wants = [ "network-online.target" ];
    serviceConfig = sandbox {
      Type = "oneshot";
      StateDirectory = "helios-vulnix";
      CacheDirectory = "helios-vulnix";
      # vulnix exits 2 when it finds anything; that's a result, not a failure
      ExecStart = "${pkgs.bash}/bin/bash -c '${pkgs.vulnix}/bin/vulnix --system --json --cache-dir \"$CACHE_DIRECTORY\" > \"$STATE_DIRECTORY/report.json\"; rc=$?; [ $rc -le 2 ]'";
      ExecStartPost = "+${ctl} vulnix-report /var/lib/helios-vulnix/report.json";
      MemoryDenyWriteExecute = false;   # Python
      TimeoutStartSec = "1h";
    };
  };
  systemd.timers.helios-vulnix = lib.mkIf (I.on "vulnScan") {
    wantedBy = [ "timers.target" ];
    timerConfig = { OnCalendar = "weekly"; Persistent = true; RandomizedDelaySec = "6h"; };
  };
}
