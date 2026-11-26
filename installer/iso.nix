# HeliOS installer ISO: the NixOS minimal installer plus `helios-install`,
# a text installer that partitions a disk, writes /etc/nixos from the HeliOS
# templates and runs nixos-install. Build: nix build .#installer-iso
{ config, lib, pkgs, modulesPath, heliosSrc, ... }:

let
  install = pkgs.writeShellApplication {
    name = "helios-install";
    runtimeInputs = with pkgs; [ gptfdisk dosfstools e2fsprogs btrfs-progs util-linux iproute2 gnused coreutils git nixos-install-tools ];
    runtimeEnv = {
      HELIOS_TEMPLATES = "${heliosSrc}/templates";
      # Where the installed router fetches HeliOS from (and updates from)
      HELIOS_URL = "github:helios-router/helios";
    };
    text = builtins.readFile ./helios-install.sh;
  };
in
{
  imports = [ "${modulesPath}/installer/cd-dvd/installation-cd-minimal.nix" ];

  image.baseName = lib.mkForce "helios-installer-${config.system.nixos.release}-${pkgs.stdenv.hostPlatform.system}";
  isoImage.volumeID = lib.mkForce "HELIOS_INSTALL";
  nix.settings.experimental-features = [ "nix-command" "flakes" ];
  environment.systemPackages = [ install ];
  # Shown on the console after auto-login
  services.getty.helpLine = lib.mkForce ''

    HeliOS installer. Connect a cable with internet, then run:  sudo helios-install
  '';
}
