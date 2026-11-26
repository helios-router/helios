# flake.nix for a HeliOS router (flake setup).
#   nix flake init -t github:helios-router/helios   (in /etc/nixos)
# The configuration name must match helios.hostName so a plain
# `nixos-rebuild switch` (which Photosphere runs) picks it up.
{
  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-25.11";
    helios.url = "github:helios-router/helios";
    helios.inputs.nixpkgs.follows = "nixpkgs";
  };

  outputs = { nixpkgs, helios, ... }: {
    nixosConfigurations.helios = nixpkgs.lib.nixosSystem {
      system = "x86_64-linux";
      modules = [
        helios.nixosModules.default
        ./hardware-configuration.nix
        ./site.nix
        {
          boot.loader.systemd-boot.enable = true;
          boot.loader.systemd-boot.configurationLimit = 20;
          boot.loader.efi.canTouchEfiVariables = true;
          system.stateVersion = "25.11";
        }
      ];
    };
  };
}
