# HeliOS installer ISO: the NixOS minimal installer plus `helios-install`.
#   nix build --impure .#installer-iso    offline image (default): installs with no network
#                                         (--impure: its contents are worked out from the build graph)
#   nix build .#installer-iso-netinstall  small image: downloads everything during the install
{ heliosSrc, nixpkgsSrc, offlinePaths ? [ ] }:
{ config, lib, modulesPath, ... }:

let offline = offlinePaths != [ ]; in
{
  imports = [
    "${modulesPath}/installer/cd-dvd/installation-cd-minimal.nix"
    (import ./tool.nix { inherit heliosSrc nixpkgsSrc offlinePaths; })
  ];

  image.baseName = lib.mkForce "helios-installer-${lib.optionalString (!offline) "netinstall-"}${config.system.nixos.release}-x86_64-linux";
  isoImage.volumeID = lib.mkForce "HELIOS_INSTALL";
  isoImage.storeContents = offlinePaths;
  # Shown on the console after auto-login
  services.getty.helpLine = lib.mkForce (''

    HeliOS installer. Run:  sudo helios-install
  '' + (if offline
    then "  No internet needed: everything for a standard install is on this image.\n"
    else "  This is the netinstall image: connect a cable with internet first.\n"));
}
