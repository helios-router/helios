# helios-install, plus everything an install needs without a network: prebuilt
# HeliOS systems, the build tools to turn the installer's answers into the final
# system, and the exact HeliOS and nixpkgs sources with a flake.lock pinned to
# them. (offlinePaths is empty only for the pure `nix flake check` evaluation.)
# Used by iso.nix and the VM test.
{ heliosSrc, nixpkgsSrc, unstableSrc ? null, offlinePaths ? [ ] }:
{ config, lib, pkgs, ... }:

let
  offline = offlinePaths != [ ];

  # The HeliOS source carried on the image. A release image (from a clean git
  # commit) uses it as is. A development image built from a working folder gets
  # only the parts HeliOS needs, so nothing else in that folder (private site
  # files, for one) can end up on the image.
  release = heliosSrc ? rev;
  keep = [ "flake.nix" "flake.lock" "LICENSE" "modules" "pkgs" "templates" "installer" "tests" ];
  src = if release then heliosSrc else builtins.path {
    name = "source";
    path = heliosSrc;
    filter = path: _type:
      builtins.elem (builtins.head (lib.splitString "/" (lib.removePrefix "${toString heliosSrc}/" (toString path)))) keep;
  };
  # A development source's hash is filled in by helios-install (`nix hash path`)
  srcNarHash = if release then heliosSrc.narHash else "@HELIOS_NARHASH@";
  heliosUrl = { owner = "helios-router"; repo = "helios"; };

  # Lock for /etc/nixos/flake.lock (matches templates/flake.nix). Both inputs are
  # pinned by store path to the sources on this image: Nix fetches github: inputs
  # through its download cache even when the source is already present, so only
  # path pins work offline. `original` stays github:, so the first
  # `nix flake update` moves the router to normal github: pins.
  heliosLocked = { type = "path"; path = "${src}"; narHash = srcNarHash; lastModified = heliosSrc.lastModified or 0; }
    // lib.optionalAttrs release { inherit (heliosSrc) rev; };
  lock = {
    version = 7;
    root = "root";
    nodes = {
      root.inputs = { helios = "helios"; nixpkgs = "nixpkgs"; };
      nixpkgs = {
        # rev too: nixpkgs puts it in the NixOS version, so the installed system matches
        # the prebuilt ones on the image (without it, the version ends in ".dirty")
        locked = { type = "path"; path = "${nixpkgsSrc}"; inherit (nixpkgsSrc) narHash lastModified rev; };
        original = { type = "github"; owner = "NixOS"; repo = "nixpkgs"; ref = "nixos-${lib.versions.majorMinor lib.version}"; };
      };
      helios = {
        inputs = { nixpkgs = [ "nixpkgs" ]; } // lib.optionalAttrs (unstableSrc != null) { nixpkgs-unstable = "nixpkgs-unstable"; };
        locked = heliosLocked;
        original = { type = "github"; inherit (heliosUrl) owner repo; };
      };
    } // lib.optionalAttrs (unstableSrc != null) {
      # HeliOS's own pin for the few packages it takes from unstable (Authelia)
      nixpkgs-unstable = {
        locked = { type = "path"; path = "${unstableSrc}"; inherit (unstableSrc) narHash lastModified rev; };
        original = { type = "github"; owner = "NixOS"; repo = "nixpkgs"; ref = "nixos-unstable"; };
      };
    };
  };

  install = pkgs.writeShellApplication {
    name = "helios-install";
    runtimeInputs = with pkgs; [
      gptfdisk parted dosfstools e2fsprogs btrfs-progs util-linux iproute2 gnused coreutils
      gawk git nixos-install-tools config.nix.package shadow getent config.systemd.package
    ];
    runtimeEnv = {
      HELIOS_TEMPLATES = "${src}/templates";
      HELIOS_URL = "github:${heliosUrl.owner}/${heliosUrl.repo}";
      HELIOS_OFFLINE = if offline then "1" else "";
      HELIOS_LOCK = if offline then "${pkgs.writeText "flake.lock" (builtins.toJSON lock)}" else "";
      # Sources the lock points at: copied onto the new system so it can rebuild offline
      HELIOS_SRC = "${src}";
      HELIOS_SOURCES = if offline then "${src} ${nixpkgsSrc}${lib.optionalString (unstableSrc != null) " ${unstableSrc}"}" else "";
      HELIOS_PIN_SOURCES = if offline then "1" else "";
    };
    text = builtins.readFile ./helios-install.sh;
  };
in
{
  nix.settings.experimental-features = [ "nix-command" "flakes" ];
  environment.systemPackages = [ install ];
  # Registered in the image's Nix store, so nixos-install finds them without a network
  system.extraDependencies = offlinePaths;
}
