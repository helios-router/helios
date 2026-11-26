# Airlock: the one controlled way in.
# Admin access: your user, SSH (keys only; password only on the physical rescue port).
{ config, lib, ... }:

let cfg = config.helios; in
{
  users.users.${cfg.admin.user} = {
    isNormalUser = true;
    extraGroups = [ "wheel" ];
    openssh.authorizedKeys.keys = cfg.admin.sshKeys;
  };

  # Reachable only from admin networks, Tailscale and the rescue port — Heliopause never exposes it on WAN.
  services.openssh = {
    enable = true;
    openFirewall = false;
    settings = {
      PasswordAuthentication = false;
      KbdInteractiveAuthentication = false;
      PermitRootLogin = "no";
      X11Forwarding = false;
      MaxAuthTries = 3;
    };
    extraConfig = lib.optionalString (cfg.interfaces.rescueMac != null) ''
      Match LocalAddress ${cfg.interfaces.rescueAddress}
        PasswordAuthentication yes
    '';
  };

  security.sudo.execWheelOnly = true;
  nix.settings.allowed-users = [ "@wheel" ];
}
