# NixOS VM tests: nix build path:.#vmTests.<name> -L
{ self, pkgs, installerArgs }:
{
  router = pkgs.testers.runNixOSTest (import ./router.nix { inherit self; });
  offline-install = pkgs.testers.runNixOSTest (import ./offline-install.nix { inherit installerArgs; });
}
