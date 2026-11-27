# HeliOS installer ISO: the NixOS minimal installer plus `helios-install`.
#   nix build --impure .#installer-iso
# (--impure: the image's contents are worked out from the build graph)
#
# One image for every install: it carries everything a standard install needs,
# so it works with no network, and when it finds internet it offers to download
# updates during the install.
#
# Boot menu:
#   Install HeliOS   starts the install wizard (helios-install) on the console
#   Recovery shell   a root shell for repairing an installed router (helios-mount)
# NixOS's own extras stay below them: "Options" (nomodeset, serial console,
# copy to RAM), firmware setup and shutdown.
{ heliosSrc, nixpkgsSrc, unstableSrc ? null, offlinePaths ? [ ] }:
{ config, lib, pkgs, modulesPath, ... }:

let
  # Finds the installed system (by the labels helios-install gives it) and
  # mounts it at /mnt, btrfs subvolumes included
  mount = pkgs.writeShellApplication {
    name = "helios-mount";
    runtimeInputs = with pkgs; [ util-linux coreutils ];
    text = ''
      [ "$(id -u)" = 0 ] || { echo "Run as root"; exit 1; }
      root=/dev/disk/by-label/nixos
      esp=/dev/disk/by-label/ESP
      [ -e "$root" ] || { echo "No HeliOS install found (no partition labelled 'nixos'). Disks:"; lsblk -o NAME,SIZE,FSTYPE,LABEL; exit 1; }
      if mountpoint -q /mnt; then echo "/mnt is already mounted"; exit 0; fi
      mkdir -p /mnt
      if [ "$(blkid -o value -s TYPE "$root")" = btrfs ]; then
        o=compress=zstd,noatime
        mount -o "subvol=@,$o" "$root" /mnt
        mount -o "subvol=@home,$o" "$root" /mnt/home
        mount -o "subvol=@nix,$o" "$root" /mnt/nix
        mount -o "subvol=@log,$o" "$root" /mnt/var/log
      else
        mount "$root" /mnt
      fi
      [ -e "$esp" ] && mount -o umask=077 "$esp" /mnt/boot
      [ -e /dev/disk/by-label/swap ] && swapon /dev/disk/by-label/swap 2>/dev/null || true
      echo "Mounted the installed system at /mnt. Enter it with: nixos-enter --root /mnt"
    '';
  };

  recoveryHelp = ''

    HeliOS recovery shell (root). To repair an installed router:
      helios-mount                          mount it at /mnt
      nixos-enter --root /mnt               a shell inside it
    Inside (after nixos-enter):
      passwd <admin>                        reset the admin password
      rm /var/lib/helios/totp/<admin>       turn off the code Cockpit asks for
      helios-ctl gate-off                   stop requiring passkey sign-in
      nix-env -p /nix/var/nix/profiles/system --list-generations
      nix-env -p /nix/var/nix/profiles/system --rollback \
        && /nix/var/nix/profiles/system/bin/switch-to-configuration boot
                                            boot the previous generation
    The install wizard is on the other boot entry, or run: helios-install
  '';
in
{
  imports = [
    "${modulesPath}/installer/cd-dvd/installation-cd-minimal.nix"
    (import ./tool.nix { inherit heliosSrc nixpkgsSrc unstableSrc offlinePaths; })
  ];

  options.helios.installer.wizard = lib.mkOption {
    type = lib.types.bool;
    default = true;
    description = "Start helios-install on the first console after boot.";
  };

  config = {
    image.baseName = lib.mkForce "helios-installer-${config.system.nixos.release}-x86_64-linux";
    isoImage.volumeID = lib.mkForce "HELIOS_INSTALL";
    isoImage.storeContents = offlinePaths;
    environment.systemPackages = [ mount ];

    # Boot menu labels: "<prepend><distroName> <label><append>"
    system.nixos.distroName = "HeliOS";
    system.nixos.label = lib.mkForce "";
    isoImage.prependToMenuLabel = "Install ";
    isoImage.appendToMenuLabel = "";

    specialisation.recovery.configuration = {
      helios.installer.wizard = false;
      system.nixos.distroName = lib.mkForce "Recovery";
      isoImage.prependToMenuLabel = lib.mkForce "";
      isoImage.appendToMenuLabel = lib.mkForce "shell";
      services.getty.autologinUser = lib.mkForce "root";
      # (beats the mkForce on the parent's helpLine below)
      services.getty.helpLine = lib.mkOverride 40 recoveryHelp;
    };

    # Shown on the console after auto-login (the wizard starts on tty1 too)
    services.getty.helpLine = lib.mkForce ''

      HeliOS installer. The install wizard starts on this console; to start
      it again (or on another console, Alt+F2):  sudo helios-install
      No internet needed: everything for a standard install is on this image.
    '';

    # The wizard: on tty1's auto-login. Leaving it (Ctrl+C, or an error) drops
    # to the shell; logging out starts it again.
    environment.loginShellInit = lib.mkIf config.helios.installer.wizard ''
      if [ "$(tty)" = /dev/tty1 ] && [ "$(id -u)" != 0 ]; then
        sudo helios-install
        echo
        echo "The installer has stopped. Start it again with: sudo helios-install"
      fi
    '';
  };
}
