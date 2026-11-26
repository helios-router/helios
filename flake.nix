{
  description = "HeliOS — a NixOS router: VLAN tiers, DNS filtering, Tailscale exits, a Cockpit UI and self-hosted push alerts";

  # nixpkgs is only used for the installer ISO and CI checks; your router's
  # flake brings its own (templates/flake.nix sets helios.inputs.nixpkgs.follows).
  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-25.11";

  outputs = { self, nixpkgs }:
    let
      system = "x86_64-linux";
      lib = nixpkgs.lib;
      # The site template with its CHANGE-ME values filled by placeholders
      example = lib.nixosSystem {
        inherit system;
        modules = [
          self.nixosModules.default
          ./templates/site.nix
          {
            helios.admin.user = lib.mkForce "admin";
            helios.interfaces.trunkMac = lib.mkForce "02:00:00:00:00:01";
            fileSystems."/" = { device = "/dev/disk/by-label/nixos"; fsType = "ext4"; };
            fileSystems."/boot" = { device = "/dev/disk/by-label/ESP"; fsType = "vfat"; };
            boot.loader.systemd-boot.enable = true;
            system.stateVersion = "25.11";
          }
        ];
      };
      installer = lib.nixosSystem {
        inherit system;
        specialArgs.heliosSrc = self;
        modules = [ ./installer/iso.nix ];
      };
    in
    {
      nixosModules.default = ./modules;
      nixosModules.helios = ./modules;
      templates.default = {
        path = ./templates;
        description = "A HeliOS site: configuration.nix, site.nix and state.json to fill in";
      };

      nixosConfigurations = { inherit example installer; };
      legacyPackages.${system}.vmTests = import ./tests/vm { inherit self; pkgs = nixpkgs.legacyPackages.${system}; };
      packages.${system} = {
        installer-iso = installer.config.system.build.isoImage;
        default = self.packages.${system}.installer-iso;
      };
      # `nix flake check`: the template evaluates and its firewall/system builds
      checks.${system} = {
        example = example.config.system.build.toplevel;
        installer = installer.config.system.build.toplevel;
      };
    };
}
