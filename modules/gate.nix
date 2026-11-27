# Gate: the optional sign-in gate in front of Cockpit (Photosphere).
#
# Off by default: Cockpit serves :9090 itself, with the admin password (plus a
# code from airlock.nix's TOTP, if set up). Turn it on as the "gate" service
# (Photosphere → Advanced → Services, or helios.gate.enable); the next Apply
# downloads Authelia and Caddy. Then:
#
# Caddy serves https://<host>.<domain>:9090 and Cockpit listens on localhost
# only. Once two-factor sign-in is on, every request first needs an Authelia
# session; then Cockpit asks for the admin password as usual.
#
#   preferred: a passkey (FIDO2 key or phone) that checks its PIN/biometric.
#              On its own it satisfies the gate, so the admin password is typed
#              once, at Cockpit.
#   fallback:  the admin password plus a 6-digit code at the gate, then the
#              password again at Cockpit.
#   rescue:    the rescue port skips the gate (matched by the client's source
#              address, which the firewall only accepts on rescue0): admin
#              password only.
#
# Even then, the gate only enforces once Photosphere turns it on (a passkey or code is
# registered and the gate knows the admin password), so upgrading a router never
# locks anyone out. Off switch from SSH or the console: helios-ctl gate-off.
#
# Passkey-alone-is-two-factors relies on Authelia's
# webauthn.experimental_enable_passkey_uv_two_factors. Authelia plans to replace
# it, and a release without it will refuse to start with it set. The hardening VM
# test starts Authelia with this config, so an update that drops it fails CI
# before it reaches a router (then move to Authelia's replacement policies).
{ config, lib, pkgs, heliosUnstable, ... }:

let
  cfg = config.helios;
  I = cfg.internal;
  localName = "${cfg.hostName}.${cfg.domain}";
  # The sign-in page's name: the owner's own domain if set (a Let's Encrypt
  # certificate, nothing to install), else <hostName>.<domain>
  ownDomain = cfg.gate.domain;
  fqdn = if ownDomain != null then ownDomain else localName;
  dnsProvider = cfg.gate.acme.dnsProvider;
  caddyPackage = if ownDomain != null then pkgs.callPackage ../pkgs/caddy-dns.nix { provider = dnsProvider; } else pkgs.caddy;
  # The DNS provider's API token: set in Photosphere, kept encrypted with
  # systemd-creds (host key), handed to Caddy as a credential
  credName = "helios-dns-token";
  dnsCred = "${gateDir}/dns-token.cred";
  tokenFile = "/run/credentials/caddy.service/${credName}";
  # How each provider's Caddy plugin takes the token
  dnsDirective = {
    cloudflare = "dns cloudflare {file.${tokenFile}}";
    desec = "dns desec {\n      token {file.${tokenFile}}\n    }";
  }.${dnsProvider};
  # deSEC's two nameservers can be minutes apart, and Let's Encrypt checks both:
  # wait before asking it to look (caddy-dns/desec issue #14; deSEC forum)
  dnsWait = {
    cloudflare = "";
    desec = "propagation_delay 3m\n    propagation_timeout 5m";
  }.${dnsProvider};
  # The own-domain site's certificate, switched at runtime by helios-ctl: the
  # router's own until a token is set (the DNS plugins refuse an empty one, and
  # Caddy wouldn't start), then Let's Encrypt with the router's own as fallback
  tlsInternal = pkgs.writeText "gate-tls-internal.caddy" "tls internal\n";
  tlsAcme = pkgs.writeText "gate-tls-acme.caddy" ''
    tls {
      issuer acme {
        ${dnsDirective}
        # Check the record on public DNS: the router answers this name itself
        resolvers 1.1.1.1 1.0.0.1
        ${dnsWait}
      }
      issuer internal
    }
  '';
  tlsLink = "${gateDir}/tls.caddy";
  tlsMode = "${gateDir}/tls-mode";       # "acme" once a token is set
  port = 9090;
  cockpitPort = 9089;     # localhost only, behind Caddy
  autheliaPort = 9091;    # localhost only
  name = "helios";
  stateDir = "/var/lib/authelia-${name}";
  gateDir = "/var/lib/helios-gate";          # Caddy reads the "enforced" flag here
  user = "authelia-${name}";

  # Stay on stable nixpkgs unless it lacks a security fix: this is the oldest
  # Authelia release with every published advisory fixed. Raise it when a new
  # advisory comes out (https://github.com/authelia/authelia/security/advisories).
  autheliaSecure = "4.39.20";
  stablePkg = pkgs.authelia;
  unstablePkg = if heliosUnstable == null then null else heliosUnstable.authelia;
  authelia =
    if lib.versionAtLeast stablePkg.version autheliaSecure then stablePkg
    else if unstablePkg != null && lib.versionAtLeast unstablePkg.version autheliaSecure then unstablePkg
    else stablePkg;

  rescue = cfg.interfaces.rescueMac != null;
  rescueNet = "${lib.concatStringsSep "." (lib.take 3 (lib.splitString "." cfg.interfaces.rescueAddress))}.0/24";

  # Settings shared by the service and helios-ctl's storage commands
  storage.local.path = "${stateDir}/db.sqlite3";
  cliConfig = (pkgs.formats.yaml { }).generate "authelia-cli.yml" { inherit storage; };

  init = pkgs.writeShellScript "helios-gate-init" ''
    set -eu
    umask 077
    install -d -m 0700 -o ${user} -g ${user} ${stateDir} ${stateDir}/secrets
    install -d -m 0750 -o root -g caddy ${gateDir}
    ${lib.optionalString (ownDomain != null) ''
    # Own domain: an (empty) encrypted token until one is set, and the matching
    # certificate config for Caddy to import
    if [ ! -s ${dnsCred} ]; then
      printf "" | ${config.systemd.package}/bin/systemd-creds encrypt --with-key=host --name=${credName} - ${dnsCred}
      chmod 0400 ${dnsCred}
    fi
    if [ "$(cat ${tlsMode} 2>/dev/null)" = acme ]; then ln -sfn ${tlsAcme} ${tlsLink}; else ln -sfn ${tlsInternal} ${tlsLink}; fi
    ''}
    for s in jwt session storage; do
      f=${stateDir}/secrets/$s
      [ -s "$f" ] || ${pkgs.openssl}/bin/openssl rand -hex 48 > "$f"
    done
    # Until Photosphere copies the admin password here, the gate user has a random
    # password nobody knows (and the gate isn't enforced)
    if [ ! -s ${stateDir}/users.yml ]; then
      digest=$(${authelia}/bin/authelia crypto hash generate argon2 --random --random.length 64 | ${pkgs.gnused}/bin/sed -n 's/^Digest: //p')
      [ -n "$digest" ]
      printf 'users:\n  %s:\n    disabled: false\n    displayname: "%s"\n    email: "%s@%s"\n    password: "%s"\n    groups: []\n' \
        '${cfg.admin.user}' '${cfg.admin.user}' '${cfg.admin.user}' '${fqdn}' "$digest" > ${stateDir}/users.yml
    fi
    chown -R ${user}:${user} ${stateDir}
  '';
in
{
  config = lib.mkMerge [ {
  # What helios-ctl needs to manage the gate (also when it's off, to say so)
  helios.internal.gate = {
    enabled = I.on "gate";
    inherit fqdn port stateDir gateDir;
    url = "https://${fqdn}:${toString port}";
    # Package paths only while it's on, so routers without the gate (and the
    # installer image) don't carry Authelia
    authelia = if I.on "gate" then "${authelia}/bin/authelia" else null;
    autheliaVersion = if I.on "gate" then authelia.version else null;
    cliConfig = if I.on "gate" then "${cliConfig}" else null;
    storageKeyFile = "${stateDir}/secrets/storage";
    usersFile = "${stateDir}/users.yml";
    notifications = "${stateDir}/notifications.txt";
    caCert = "/var/lib/caddy/.local/share/caddy/pki/authorities/local/root.crt";
    # Own domain (helios.gate.domain): where the token goes and where Caddy keeps
    # the Let's Encrypt certificate
    inherit ownDomain dnsProvider dnsCred credName tlsLink tlsMode;
    tlsInternal = if I.on "gate" then "${tlsInternal}" else null;
    tlsAcme = if ownDomain == null then null else "${tlsAcme}";
    acmeCert = if ownDomain == null then null
      else "/var/lib/caddy/.local/share/caddy/certificates/acme-v02.api.letsencrypt.org-directory/${ownDomain}/${ownDomain}.crt";
  };
} (lib.mkIf (I.on "gate") {
  warnings = lib.optional (!lib.versionAtLeast authelia.version autheliaSecure)
    "HeliOS: Authelia ${authelia.version} lacks security fixes from ${autheliaSecure}; use HeliOS's flake (it brings a newer Authelia) or a newer nixpkgs.";

  # ---------------------------------------------------------------- Authelia
  services.authelia.instances.${name} = {
    enable = true;
    package = authelia;
    secrets = {
      jwtSecretFile = "${stateDir}/secrets/jwt";
      sessionSecretFile = "${stateDir}/secrets/session";
      storageEncryptionKeyFile = "${stateDir}/secrets/storage";
    };
    settings = {
      theme = "auto";
      default_2fa_method = "webauthn";
      server.address = "tcp://127.0.0.1:${toString autheliaPort}/auth";
      log.level = "info";
      ntp = { disable_startup_check = true; disable_failure = true; };   # often offline at boot
      authentication_backend = {
        password_reset.disable = true;
        password_change.disable = true;   # Photosphere changes it (with the router's)
        file = { path = "${stateDir}/users.yml"; watch = true; };
      };
      access_control = {
        default_policy = "deny";
        rules = [ { domain = [ fqdn ]; policy = "two_factor"; } ];
      };
      session = {
        name = "helios_gate";
        expiration = "12h";
        inactivity = "1h";
        remember_me = "-1";
        cookies = [ {
          domain = fqdn;
          authelia_url = "https://${fqdn}:${toString port}/auth";
          default_redirection_url = "https://${fqdn}:${toString port}/";
        } ];
      };
      regulation = { max_retries = 5; find_time = "2m"; ban_time = "10m"; };
      inherit storage;
      notifier.filesystem.filename = "${stateDir}/notifications.txt";
      totp = { issuer = "HeliOS (${cfg.hostName})"; period = 30; skew = 1; };
      webauthn = {
        display_name = "HeliOS (${cfg.hostName})";
        enable_passkey_login = true;
        # Any passkey (device, password manager or hardware key: no brand or model
        # restriction, synced passkeys allowed) that checked it is you (PIN, biometric,
        # master password) satisfies two_factor on its own
        experimental_enable_passkey_uv_two_factors = true;
        attestation_conveyance_preference = "none";   # DIY keys (pico-fido) aren't in FIDO's metadata
        selection_criteria = {
          discoverability = "required";               # stored on the key: no username to type
          user_verification = "required";             # always the key's PIN (or biometric)
        };
      };
    };
  };

  systemd.services.helios-gate-init = {
    description = "HeliOS: sign-in gate secrets and user file";
    wantedBy = [ "multi-user.target" ];
    before = [ "authelia-${name}.service" "caddy.service" ];
    requiredBy = [ "authelia-${name}.service" ];
    serviceConfig = { Type = "oneshot"; RemainAfterExit = true; ExecStart = init; };
  };

  # ---------------------------------------------------------------- Cockpit behind Caddy
  systemd.sockets.cockpit.listenStreams = lib.mkForce [ "" "127.0.0.1:${toString cockpitPort}" ];
  services.cockpit.allowed-origins = lib.optional rescue "https://${cfg.interfaces.rescueAddress}:${toString port}"
    ++ lib.optional (ownDomain != null) "https://${ownDomain}:${toString port}";
  # The router answers its own domain on every network (AdGuard reads /etc/hosts)
  networking.hosts = lib.mkIf (ownDomain != null) { ${I.adminGw} = [ ownDomain ]; };
  services.cockpit.settings.WebService = {
    AllowUnencrypted = true;              # plain HTTP on localhost; Caddy does TLS
    ProtocolHeader = "X-Forwarded-Proto";
    # The client's address for PAM (the rescue-port rule in airlock.nix). Only
    # Caddy and local processes can reach Cockpit now, and Caddy sets this itself.
    ForwardedForHeader = "X-Forwarded-For";
  };

  services.caddy = {
    enable = true;
    package = caddyPackage;
    # No admin API: on localhost it would let any local user rewrite the gate.
    # Config changes restart Caddy instead of reloading it.
    enableReload = false;
    globalConfig = ''
      admin off
      auto_https disable_redirects
      skip_install_trust
      default_sni ${fqdn}
      ${lib.optionalString (cfg.gate.acme.email != null) "email ${cfg.gate.acme.email}"}
    '';
    # The same routes for every name the router answers on
    extraConfig = ''
      (gate_routes) {
        encode zstd gzip
        # In this order (a route keeps it): the first matching handle wins
        route {
          ${lib.optionalString rescue ''
          # Rescue port: no gate, admin password only. Matched on the client's address
          # (the firewall only lets the rescue subnet in on rescue0), never the Host header.
          @rescue remote_ip ${rescueNet}
          handle @rescue {
            reverse_proxy 127.0.0.1:${toString cockpitPort}
          }
          ''}
          # The gate's own pages
          handle /auth* {
            reverse_proxy 127.0.0.1:${toString autheliaPort}
          }
          # Two-factor sign-in on (Photosphere writes the flag): passkeys and the
          # session cookie need the sign-in page's name, so other names redirect to it
          @othername {
            file {
              root ${gateDir}
              try_files enforced
            }
            not host ${fqdn}
          }
          handle @othername {
            redir https://${fqdn}:${toString port}{uri} 302
          }
          @enforced file {
            root ${gateDir}
            try_files enforced
          }
          handle @enforced {
            forward_auth 127.0.0.1:${toString autheliaPort} {
              uri /auth/api/authz/forward-auth
              copy_headers Remote-User
            }
            reverse_proxy 127.0.0.1:${toString cockpitPort}
          }
          handle {
            reverse_proxy 127.0.0.1:${toString cockpitPort}
          }
        }
      }
    '';
    virtualHosts = {
      # The router's own names and addresses: its own certificate authority
      "https://${localName}:${toString port}, https://${cfg.hostName}:${toString port}, https://${I.adminGw}:${toString port}${lib.optionalString rescue ", https://${cfg.interfaces.rescueAddress}:${toString port}"}".extraConfig = ''
        tls internal
        import gate_routes
      '';
    } // lib.optionalAttrs (ownDomain != null) {
      # The owner's domain: Let's Encrypt through a DNS record at the provider
      # (nothing exposed); while that fails, the router's own certificate
      "https://${ownDomain}:${toString port}".extraConfig = ''
        import ${tlsLink}
        import gate_routes
      '';
    };
  };
  systemd.services.caddy = {
    after = [ "helios-gate-init.service" ];
    wants = [ "helios-gate-init.service" ];
    serviceConfig.LoadCredentialEncrypted = lib.mkIf (ownDomain != null) [ "${credName}:${dnsCred}" ];
  };
}) ];
}
