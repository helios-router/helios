# What the offline installer image carries so `helios-install` works with no
# network:
#  1. complete HeliOS systems for the usual installer choices (all their packages);
#  2. the build inputs of every part that changes with your answers (host name,
#     users, ports, disk layout, hardware). Those are computed, not listed by hand:
#     each system is also built with every answer changed, and the derivations that
#     differ are exactly what an install rebuilds.
{ self, nixpkgs, system ? "x86_64-linux" }:

let
  lib = nixpkgs.lib;
  pkgs = nixpkgs.legacyPackages.${system};

  # Mirrors templates/flake.nix plus a typical nixos-generate-config result
  variant = extra: lib.nixosSystem {
    inherit system;
    modules = [
      self.nixosModules.default
      ../templates/site.nix
      ({ lib, ... }: {
        helios.admin.user = lib.mkForce "admin";
        helios.interfaces.trunkMac = lib.mkForce "02:00:00:00:00:01";
        boot.loader.systemd-boot.enable = true;
        boot.loader.systemd-boot.configurationLimit = 20;
        boot.loader.efi.canTouchEfiVariables = true;
        system.stateVersion = "25.11";
        nixpkgs.hostPlatform = lib.mkDefault system;
        boot.initrd.availableKernelModules = [ "xhci_pci" "ahci" "nvme" "usb_storage" "sd_mod" ];
        boot.kernelModules = [ "kvm-intel" ];
        fileSystems."/boot" = { device = "/dev/disk/by-uuid/0000-0000"; fsType = "vfat"; options = [ "fmask=0077" "dmask=0077" ]; };
      })
      extra
    ];
  };
  ext4 = { fileSystems."/" = { device = "/dev/disk/by-uuid/00000000-0000-0000-0000-000000000000"; fsType = "ext4"; }; };
  btrfs = { fileSystems."/" = { device = "/dev/disk/by-uuid/00000000-0000-0000-0000-000000000000"; fsType = "btrfs"; options = [ "subvol=@" "compress=zstd" "noatime" ]; }; };

  # The same system with every installer answer different (and other hardware,
  # including a VM's), so every answer-dependent derivation shows up as changed
  # (mkOverride 40 beats the mkForce placeholders above)
  changed = { lib, modulesPath, ... }: {
    imports = [ (modulesPath + "/profiles/qemu-guest.nix") ];
    helios.hostName = lib.mkOverride 40 "zz-changed";
    helios.domain = lib.mkOverride 40 "changed.test";
    helios.admin.user = lib.mkOverride 40 "zzadmin";
    helios.admin.sshKeys = lib.mkOverride 40 [ "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIChangedChangedChangedChangedChangedChang zz@changed" ];
    helios.interfaces.trunkMac = lib.mkOverride 40 "02:00:00:00:00:99";
    helios.interfaces.wanVlan = lib.mkOverride 40 11;
    helios.tailscale.loginServer = lib.mkOverride 40 "https://headscale.changed.test";
    swapDevices = [ { device = "/dev/disk/by-uuid/11111111-1111-1111-1111-111111111111"; } ];
    boot.initrd.availableKernelModules = [ "ehci_pci" "uas" "sdhci_pci" "virtio_blk" ];
    boot.kernelModules = [ "kvm-amd" ];
    networking.useDHCP = lib.mkDefault true;
  };

  shapes = {
    # The installer's defaults: Tailscale and notifications on
    default = ext4;
    # Every optional service the offline image can install, on btrfs
    full = { lib, ... }: {
      imports = [ btrfs ];
      helios.printing.enable = lib.mkForce true;
      helios.heliosphere.enable = lib.mkForce true;
    };
    # Everything optional off
    minimal = { lib, ... }: {
      imports = [ ext4 ];
      helios.tailscale.enable = lib.mkForce false;
      helios.solarWind.enable = lib.mkForce false;
    };
  };
  systems = lib.mapAttrs (_: variant) shapes;
  changedSystems = lib.mapAttrs (_: shape: variant { imports = [ shape changed ]; }) shapes;

  toplevel = s: s.config.system.build.toplevel;
  names = builtins.attrNames shapes;

  # --- Build inputs of the answer-dependent derivations -----------------------
  # Read the .drv graph of each system and its changed twin (no building). The
  # derivations that differ are rebuilt by an install; whatever they take as
  # input from outside that set (exact packages and outputs) goes on the image.
  # Reading .drv files is only allowed in impure evaluation
  readDrv = p:
    if builtins ? currentSystem then builtins.readFile p
    else throw "The offline installer image works out its contents from the system's build graph, which needs impure evaluation: add --impure (nix build --impure .#installer-iso, or .#vmTests.offline-install).";
  inputsOf = drv:
    let
      # inputDrvs entries look like ("/nix/store/…-x.drv",["out","dev"])
      m = builtins.split ''[(]"(/nix/store/[a-z0-9]{32}-[^"]+[.]drv)",[[]([a-zA-Z0-9",_-]*)]'' (readDrv drv);
      pairs = builtins.filter builtins.isList m;
    in map (x: { drv = builtins.elemAt x 0; outputs = builtins.filter (o: o != "") (lib.splitString "\"" (builtins.replaceStrings [ "," ] [ "" ] (builtins.elemAt x 1))); }) pairs;
  closure = top: map (x: x.key) (builtins.genericClosure {
    startSet = [ { key = builtins.unsafeDiscardStringContext (builtins.unsafeDiscardOutputDependency top.drvPath); } ];
    operator = x: map (i: { key = i.drv; }) (inputsOf x.key);
  });
  neededFor = name:
    let
      a = closure (toplevel systems.${name});
      b = closure (toplevel changedSystems.${name});
      inA = lib.genAttrs a (_: true);
      inB = lib.genAttrs b (_: true);
      changedDrvs = builtins.filter (d: !(inB ? ${d})) a ++ builtins.filter (d: !(inA ? ${d})) b;
      isChanged = lib.genAttrs changedDrvs (_: true);
    in lib.concatMap (d: builtins.filter (i: !(isChanged ? ${i.drv})) (inputsOf d)) changedDrvs;
  # Answers also combine options in ways none of the prebuilt systems has (ext4
  # with printing, say), which gives new versions of files that merge several
  # options (udev rules, hwdb, /etc, unit lists). Those are the derivations whose
  # name comes out different across the prebuilt systems: carry their inputs too
  # (all of them: an install may reuse one prebuilt version and rebuild another)
  # Only names that differ between systems: compilers built in several stages
  # repeat a name inside every system and need not come along
  closures = map (s: closure (toplevel s)) (builtins.attrValues systems);
  allDrvs = lib.unique (lib.concatLists closures);
  inAll = let sets = map (c: lib.genAttrs c (_: true)) closures; in d: lib.all (s: s ? ${d}) sets;
  drvName = d: builtins.substring 44 (builtins.stringLength d) d;   # drop "/nix/store/<hash>-"
  differing = builtins.filter (d: !(inAll d)) allDrvs;
  byName = builtins.groupBy drvName differing;
  mixed = builtins.filter (d: builtins.length byName.${drvName d} > 1) differing;
  neededForMixes = lib.concatMap inputsOf mixed;
  # Output paths of a .drv: its first list, entries ("out","/nix/store/…","","")
  outputPaths = drv:
    let
      section = builtins.head (lib.splitString "],[" (readDrv drv));
      m = builtins.filter builtins.isList (builtins.split ''[(]"([a-zA-Z0-9_-]+)","(/nix/store/[a-z0-9]{32}-[^"]+)"'' section);
    in builtins.listToAttrs (map (x: lib.nameValuePair (builtins.elemAt x 0) (builtins.elemAt x 1)) m);
  # One store path per needed output, with context so the image includes it
  needed = lib.concatMap (i:
    let paths = outputPaths i.drv; in
    map (o: builtins.appendContext paths.${o} { ${i.drv} = { outputs = [ o ]; }; }) i.outputs)
    (lib.unique (lib.concatMap neededFor names ++ neededForMixes));
in
map toplevel (builtins.attrValues systems)
++ needed
++ [ pkgs.stdenv pkgs.stdenvNoCC ]
