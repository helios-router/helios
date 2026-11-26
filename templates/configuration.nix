# configuration.nix for a HeliOS router (non-flake setup).
#   git clone https://github.com/helios-router/helios /etc/nixos/helios
#   cp /etc/nixos/helios/templates/{site.nix,state.json} /etc/nixos/
# then fill in site.nix and run: sudo nixos-rebuild switch
{ ... }:

{
  imports = [
    ./hardware-configuration.nix
    ./helios/modules
    ./site.nix
  ];

  boot.loader.systemd-boot.enable = true;
  boot.loader.systemd-boot.configurationLimit = 20;   # keep generations for rollback
  boot.loader.efi.canTouchEfiVariables = true;

  system.stateVersion = "25.11";
}
