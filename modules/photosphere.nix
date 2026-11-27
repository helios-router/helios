# Photosphere — HeliOS's pages in Cockpit, the helios-ctl backend behind them,
# and the small timers they report on (outage monitor, nightly config backup).
#
# One-time: /var/lib/helios/adguard.json = {"url":"http://127.0.0.1:3000","user":"…","password":"…"}
# so Photosphere can set per-device AdGuard profiles.
{ config, lib, pkgs, ... }:

let
  cfg = config.helios;

  runtimePath = lib.makeBinPath (with pkgs; [
    git iproute2 nftables iputils vnstat nix systemd coreutils ntfy-sh
    # Remote access: OpenVPN PKI and proxy passwords, WireGuard keys, client QR codes
    openssl wireguard-tools qrencode openvpn
    # Admin account: chpasswd (only on the system path with mutable users)
    shadow
  ] ++ lib.optional cfg.tailscale.enable config.services.tailscale.package)
    + ":/run/current-system/sw/bin";

  helios-ctl = pkgs.writeShellScriptBin "helios-ctl" ''
    export PATH=${runtimePath}:$PATH
    export HELIOS_SELF=/run/current-system/sw/bin/helios-ctl
    # Admin account: checks the current password against its shadow hash
    export HELIOS_LIBCRYPT=${lib.getLib pkgs.libxcrypt}/lib/libcrypt.so.2
    exec ${pkgs.python3.withPackages (ps: [ ps.bcrypt ])}/bin/python3 ${../pkgs/helios-ctl.py} "$@"
  '';

  photosphere = pkgs.runCommand "cockpit-photosphere" { } ''
    mkdir -p $out/share/cockpit
    cp -r ${../pkgs/photosphere} $out/share/cockpit/photosphere
  '';
in
{
  helios.internal.helios-ctl = helios-ctl;

  environment.systemPackages = [ helios-ctl photosphere ];
  systemd.tmpfiles.rules = [ "d /var/lib/helios 0750 root root -" ];

  # Cockpit: https://<trusted gateway>:9090 — reachable from admin networks and Tailscale only
  services.cockpit = {
    enable = true;
    port = 9090;
    openFirewall = false;
    allowed-origins = [
      "https://${cfg.internal.adminGw}:9090"
      "https://${cfg.hostName}:9090"
      "https://${cfg.hostName}.${cfg.domain}:9090"
    ];
  };

  # Outage monitor: two missed checks in a row = outage; logged for History and
  # announced on Solar Wind (aurora) once the internet is back.
  systemd.services.helios-uplink = {
    description = "HeliOS internet uplink check";
    serviceConfig = { Type = "oneshot"; ExecStart = "${helios-ctl}/bin/helios-ctl uplink-check"; };
  };
  systemd.timers.helios-uplink = {
    wantedBy = [ "timers.target" ];
    timerConfig = { OnBootSec = "1min"; OnUnitActiveSec = "30s"; AccuracySec = "1s"; };
  };

  # Nightly snapshot of settings that live outside Nix (AdGuard's UI changes),
  # committed in configDir and pushed if a git remote is set.
  systemd.services.helios-backup = {
    description = "HeliOS config backup";
    serviceConfig = { Type = "oneshot"; ExecStart = "${helios-ctl}/bin/helios-ctl backup"; };
  };
  systemd.timers.helios-backup = {
    wantedBy = [ "timers.target" ];
    timerConfig = { OnCalendar = "*-*-* 03:17"; Persistent = true; RandomizedDelaySec = "10min"; };
  };
}
