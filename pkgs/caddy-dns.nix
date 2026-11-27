# Caddy with one DNS-provider plugin, for the sign-in gate's own-domain
# certificate (helios.gate.domain): Let's Encrypt checks a DNS record the router
# writes through the provider's API, so nothing has to be reachable from the
# internet. Only built on routers that set a domain. Bump a tag and its hash
# together (a build with the old hash prints the new one).
{ caddy, lib, provider }:

let
  providers = {
    # Cloudflare: your own domain on Cloudflare (an API token for its DNS)
    cloudflare = { plugin = "github.com/caddy-dns/cloudflare@v0.2.4"; hash = "sha256-Q0lgI8MY90u/5R/xXBVPQWCZBN7dUZ0kcuDxD0xd0fo="; };
    # deSEC: free names (yourname.dedyn.io) or your own domain, from a non-profit DNS host
    desec = { plugin = "github.com/caddy-dns/desec@v1.1.0"; hash = lib.fakeHash; };
  };
  p = providers.${provider};
in
caddy.withPlugins { plugins = [ p.plugin ]; inherit (p) hash; }
