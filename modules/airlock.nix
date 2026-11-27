# Airlock: the one controlled way in.
# Admin access: your user, SSH (keys only; password only on the physical rescue port).
# Cockpit sign-in: password, plus a one-time code once two-factor sign-in is on
# (Photosphere → Advanced → Admin account). The code's secret lives in
# /var/lib/helios/totp/<user>, root-only; deleting that file turns it off
# again (from SSH, the console or the installer's recovery shell).
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

  # TOTP for Cockpit only: SSH is key-only, and Cockpit's "administrative access"
  # (sudo) and the console keep the password alone, so a lost phone never locks
  # you out of the box itself. allowNullOTP: no secret file = no code asked.
  # The rescue port's password SSH (above) skips the code too: it needs someone
  # at the router, who could also use the console.
  security.pam.services.cockpit = {
    googleAuthenticator = { enable = true; allowNullOTP = true; };
    # Check the password first and stop there if it's wrong (NixOS makes this
    # step "optional"). Then wrong passwords from anyone on an admin network
    # don't use up the code's rate limit (3 tries / 30 s) and lock you out.
    rules.auth.unix-early.control = lib.mkForce "requisite";
    rules.auth.google_authenticator.settings = {
      secret = "/var/lib/helios/totp/\${USER}";
      user = "root";
    };
  };
  systemd.tmpfiles.rules = [ "d /var/lib/helios/totp 0700 root root -" ];

  security.sudo.execWheelOnly = true;
  nix.settings.allowed-users = [ "@wheel" ];
}
