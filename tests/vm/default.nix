# NixOS VM tests: nix build path:.#vmTests.<name> -L
{ self, pkgs, installerArgs }:
{
  router = pkgs.testers.runNixOSTest (import ./router.nix { inherit self; });
  hardening = pkgs.testers.runNixOSTest (import ./hardening.nix { inherit self; });
  passkey = pkgs.testers.runNixOSTest (import ./passkey.nix { inherit self; });
  offline-install = pkgs.testers.runNixOSTest (import ./offline-install.nix { inherit installerArgs; });
}
