# Solar Wind — self-hosted ntfy push notifications.
#
# Topics: "flare" (urgent) and "aurora" (informational). Access is deny-all by
# default; on first boot helios-solarwind-setup generates
#   - a publish-only token for the router's own alerts   (/var/lib/helios/solarwind-token)
#   - a read-only "phone" login for your phone(s)         (sudo helios-ctl notify-info)
# and hands them to ntfy through an environment file, never the Nix store.
#
# Exposure (helios.solarWind.exposure):
#   local  — admin networks and Tailscale only
#   proxy  — your reverse proxy elsewhere forwards to <router-tailscale-ip>:<port>
#   funnel — Tailscale Funnel publishes it at https://<host>.<tailnet>.ts.net
{ config, lib, pkgs, ... }:

let
  cfg = config.helios;
  sw = cfg.solarWind;
  envFile = "/var/lib/helios/solarwind.env";
  listen = if sw.exposure == "funnel" then "127.0.0.1:${toString sw.port}" else ":${toString sw.port}";
  baseUrl = if sw.publicUrl != null then sw.publicUrl else "http://${cfg.internal.adminGw}:${toString sw.port}";
in
lib.mkIf (cfg.internal.on "notifications") {
  services.ntfy-sh = {
    enable = true;
    environmentFile = envFile;
    settings = {
      base-url = baseUrl;
      listen-http = listen;
      behind-proxy = sw.exposure != "local";
      auth-default-access = "deny-all";
      enable-signup = false;
      enable-login = true;
      cache-duration = "72h";
    } // lib.optionalAttrs sw.iosRelay { upstream-base-url = "https://ntfy.sh"; };
  };

  # Generates users/tokens once (idempotent); ntfy starts only after that.
  systemd.services.helios-solarwind-setup = {
    description = "HeliOS notifications: generate ntfy users and tokens";
    wantedBy = [ "multi-user.target" ];
    before = [ "ntfy-sh.service" ];
    requiredBy = [ "ntfy-sh.service" ];
    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
      ExecStart = "${config.helios.internal.helios-ctl}/bin/helios-ctl notify-setup";
    };
  };
  systemd.services.ntfy-sh.unitConfig.ConditionPathExists = envFile;

  # Funnel: publish 127.0.0.1:<port> at https://<host>.<tailnet>.ts.net
  # (tailnet policy needs the "funnel" node attribute for this node — see template)
  systemd.services.helios-solarwind-funnel = lib.mkIf (sw.exposure == "funnel") {
    description = "HeliOS notifications via Tailscale Funnel";
    after = [ "tailscaled.service" "ntfy-sh.service" ];
    wants = [ "tailscaled.service" ];
    wantedBy = [ "multi-user.target" ];
    path = [ config.services.tailscale.package ];
    serviceConfig = { Type = "oneshot"; RemainAfterExit = true; Restart = "on-failure"; RestartSec = 30; };
    script = "tailscale funnel --bg --https=443 http://127.0.0.1:${toString sw.port}";
    preStop = "tailscale funnel --https=443 off || true";
  };
}
