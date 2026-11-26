# Tailscale on the router: admin exit node + subnet router. Who may use which
# exit / subnet is decided by your tailnet policy (templates/tailnet-policy.hujson).
{ config, lib, ... }:

let
  cfg = config.helios;
  ts = cfg.tailscale;
  routes = lib.concatMapStringsSep "," cfg.internal.net (cfg.internal.where (a: a.tailnet));
in
lib.mkIf ts.enable {
  services.tailscale = {
    enable = true;
    useRoutingFeatures = "both";
    authKeyFile = ts.authKeyFile;
    extraUpFlags = [
      "--advertise-tags=${ts.tag}"
      "--accept-dns=false"            # the router resolves via its own AdGuard
      "--netfilter-mode=off"          # Heliopause handles tailscale0; Tailscale's own rules would be flushed on rebuilds
    ]
    ++ lib.optional (ts.loginServer != null) "--login-server=${ts.loginServer}"
    ++ lib.optional ts.adminExit "--advertise-exit-node"
    ++ lib.optional ts.advertiseRoutes "--advertise-routes=${routes}";
  };
}
