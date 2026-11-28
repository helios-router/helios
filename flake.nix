{
  description = "HeliOS — a NixOS router: VLAN tiers, DNS filtering, Tailscale exits, a Cockpit UI and self-hosted push alerts";

  # nixpkgs is only used for the installer ISO and CI checks; your router's
  # flake brings its own (templates/flake.nix sets helios.inputs.nixpkgs.follows).
  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-25.11";
  # Only for packages whose security fixes stable doesn't have yet (Authelia,
  # see modules/gate.nix). Everything else comes from the router's nixpkgs.
  inputs.nixpkgs-unstable.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs = { self, nixpkgs, nixpkgs-unstable }:
    let
      system = "x86_64-linux";
      # The modules, plus nixpkgs-unstable's packages for the ones that need it
      heliosModule = { pkgs, ... }: {
        imports = [ ./modules ];
        _module.args.heliosUnstable = nixpkgs-unstable.legacyPackages.${pkgs.stdenv.hostPlatform.system};
      };
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
      # The installer image carries everything a standard install needs, and
      # offers to download updates during the install when it finds internet
      installerArgs = { heliosSrc = self; nixpkgsSrc = nixpkgs; unstableSrc = nixpkgs-unstable; offlinePaths = import ./installer/offline.nix { inherit self nixpkgs system; }; };
      installer = lib.nixosSystem { inherit system; modules = [ (import ./installer/iso.nix installerArgs) ]; };
      # The offline image's contents come from the build graph, which needs
      # --impure; a pure evaluation (nix flake check) gets a stub that says so
      impure = builtins ? currentSystem;
      needsImpure = nixpkgs.legacyPackages.${system}.runCommand "installer-iso-needs-impure" { } ''
        echo "The offline installer image needs impure evaluation: nix build --impure .#installer-iso" >&2
        exit 1
      '';
      # The same installer without the image contents: evaluates purely, for
      # `nix flake check` (not built as an image)
      installer-check = lib.nixosSystem {
        inherit system;
        modules = [ (import ./installer/iso.nix (installerArgs // { offlinePaths = [ ]; })) ];
      };
    in
    {
      nixosModules.default = heliosModule;
      nixosModules.helios = heliosModule;
      templates.default = {
        path = ./templates;
        description = "A HeliOS site: configuration.nix, site.nix and state.json to fill in";
      };

      nixosConfigurations = { inherit example; } // lib.optionalAttrs impure { inherit installer; };
      legacyPackages.${system} = {
        vmTests = import ./tests/vm { inherit self installerArgs; pkgs = nixpkgs.legacyPackages.${system}; };
        # The real firewall rulesets tests/netns/firewall.py loads into namespaces
        heliopauseRulesets = import ./tests/netns/rulesets.nix { inherit lib example; pkgs = nixpkgs.legacyPackages.${system}; };
      };
      packages.${system} = {
        installer-iso = if impure then installer.config.system.build.isoImage else needsImpure;
        default = self.packages.${system}.installer-iso;
      };
      # `nix flake check`: the template evaluates and its firewall/system builds
      checks.${system} = {
        example = example.config.system.build.toplevel;
        # The same site with passkey sign-in on and its own domain: builds Authelia
        # (from the unstable pin) and Caddy with a DNS plugin (Cloudflare, the default)
        example-gate = (example.extendModules {
          modules = [ { helios.gate.enable = lib.mkForce true; helios.gate.domain = "helios.example.com"; } ];
        }).config.system.build.toplevel;
        installer = installer-check.config.system.build.toplevel;
        # Caddy with each DNS plugin the gate's own-domain certificate can use
        caddy-cloudflare = nixpkgs.legacyPackages.${system}.callPackage ./pkgs/caddy-dns.nix { provider = "cloudflare"; };
        caddy-desec = nixpkgs.legacyPackages.${system}.callPackage ./pkgs/caddy-dns.nix { provider = "desec"; };
      };
    };
}
