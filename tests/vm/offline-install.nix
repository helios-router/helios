# The offline installer end to end: a machine with the installer image's tools
# and store contents, and no network at all (test VMs never have internet),
# runs helios-install with scripted answers onto an empty disk. Passing means
# nothing in a standard install needs a download.
{ installerArgs }:

{
  name = "helios-offline-install";

  nodes.installer = { lib, pkgs, ... }: {
    imports = [ (import ../../installer/tool.nix installerArgs) ];
    environment.systemPackages = [ pkgs.git ];   # for the checks below
    virtualisation.memorySize = 4096;
    virtualisation.diskSize = 8192;
    virtualisation.emptyDiskImages = [ 8192 ];   # /dev/vdb: the install target
    virtualisation.useEFIBoot = true;            # the template installs systemd-boot
    nix.settings.substituters = lib.mkForce [ ];
    # No user-mode (internet) NIC at all, so the test is offline even when the
    # Nix sandbox is off; eth1 stays, on the test's private switch only
    virtualisation.qemu.networkingOptions = lib.mkForce [ ];
    virtualisation.restrictNetwork = true;
  };

  testScript = ''
    installer.wait_for_unit("multi-user.target")
    installer.fail("getent hosts github.com")   # really offline

    answers = "\n".join([
        "vdb",            # disk
        "ext4",           # filesystem
        "0",              # swap GiB
        "helios-test",    # hostname
        "home.arpa",      # domain
        "admin",          # admin login
        "",               # SSH key
        "eth1",           # trunk port
        "10",             # WAN VLAN
        "y",              # Tailscale
        "y",              # ...understood that it's an admin door
        "",               # Headscale URL (none)
        "y",              # notifications
        "y",              # print server
        "n",              # remote access
                          # (Log AI is not asked offline)
        "vdb",            # confirm the disk
        "test-password",  # admin password
    ]) + "\n"

    with subtest("helios-install completes with no network"):
        installer.succeed(f"printf '%s' '{answers}' | helios-install >&2", timeout=3600)

    with subtest("the installed system is what was asked for"):
        # The profile links to /nix/store/…: look for it on the new disk, not the live system
        installer.succeed("test -e /mnt$(readlink -f /mnt/nix/var/nix/profiles/system)/init")
        installer.succeed("grep -q 'hostName = \"helios-test\"' /mnt/etc/nixos/site.nix")
        installer.succeed("test -f /mnt/etc/nixos/flake.lock")
        installer.succeed("git -C /mnt/etc/nixos log --oneline | grep -q 'HeliOS install: helios-test'")
        installer.succeed("readlink -f /mnt/nix/var/nix/profiles/system | grep -q helios-test")
        installer.succeed("nixos-enter --root /mnt -c 'getent shadow admin | cut -d: -f2 | grep -q .'")
        installer.succeed("test -e /mnt/boot/EFI/systemd/systemd-bootx64.efi")
  '';
}
