# NixOS VM tests: nix build path:.#vmTests.router -L
{ self, pkgs }:
{
  router = pkgs.testers.runNixOSTest (import ./router.nix { inherit self; });
}
