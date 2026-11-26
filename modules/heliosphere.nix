# Heliosphere ("Remote access" in Photosphere) — a NixOS container for
# devices away from home. Ways in: Tailscale exit node, WireGuard, OpenVPN,
# SOCKS5/HTTP proxy (any mix). Way out: direct, or through a WireGuard /
# OpenVPN / proxy upstream with a kill switch. The host's Heliopause treats
# ve-heliosphere as a filtered network (internet only, DNS via
# Magnetosphere), so every way in inherits that isolation. Design and
# contract: docs/heliosphere.md. In AdGuard, 10.60.0.2 is the client that
# gets the remote-access profile.
{ config, lib, pkgs, ... }:

let
  cfg = config.helios;
  hs = cfg.heliosphere;
  R = cfg.internal.remote;
  eg = R.egress;
  bl = lib.importJSON ../pkgs/blocklists.json;
  hostIp = "10.60.0.1";
  localIp = "10.60.0.2";
  dir = "/var/lib/heliosphere";
  stateVersion = config.system.stateVersion;

  tunnel = eg.mode != "direct";
  tunIf = { wireguard = "wg-out"; openvpn = "tun-out"; proxy = "tun-out"; }.${eg.mode} or null;
  usesWg = R.wireguard.enabled || eg.mode == "wireguard";

  # Marks (see docs/heliosphere.md): 0x1 = reply to a connection that came in
  # on eth0, 0x2 = OpenVPN/tun2socks' own packets, 51820 = wg-quick's own.
  ip = "${pkgs.iproute2}/bin/ip";

  # LAN access per way in (state.json heliosphere.ingress.<way>.lan). Enforced
  # inside the container per interface, and on the host for the union.
  netOf = n: "${cfg.internal.byName.${n}.prefix}.0/24";
  lanOf = m: lib.optionals R.${m}.enabled (map netOf (builtins.filter (n: lib.elem n cfg.internal.names) R.${m}.lan));
  ingressIf = { tailscale = "tailscale0"; wireguard = "wg-in"; openvpn = "tun-in"; };
  lanAny = lib.unique (lib.concatMap lanOf [ "tailscale" "wireguard" "openvpn" ]);
  allLans = map netOf cfg.internal.names;
  ovpnGid = 1194;   # fixed, so host and container agree on who owns crl.pem
in
lib.mkIf (cfg.internal.on "remote") {
  # OpenVPN in the container drops to nobody:heliosphere-ovpn and re-reads crl.pem on each new
  # client, so the server side is 0710/0640 to this group instead of world-readable. Same gid
  # here and in the container (no user namespace), so the bind mount keeps the group.
  users.groups.heliosphere-ovpn.gid = ovpnGid;
  systemd.tmpfiles.rules = [
    "d ${builtins.dirOf hs.authKeyFile} 0700 root root -"
    "d ${dir} 0755 root root -"
    "d ${dir}/wireguard 0700 root root -"
    "d ${dir}/openvpn 0755 root root -"
    "d ${dir}/openvpn/ca 0700 root root -"
    "d ${dir}/openvpn/server 0710 root heliosphere-ovpn -"
    # Routers set up before this got 0644 files; z only touches files that already exist
    "z ${dir}/openvpn/server/crl.pem 0640 root heliosphere-ovpn -"
    "z ${dir}/openvpn/server/ca.crt 0600 root root -"
    "z ${dir}/openvpn/server/server.crt 0600 root root -"
    "z ${dir}/openvpn/ca/ca.crt 0600 root root -"
    "z ${dir}/wireguard/public 0600 root root -"
    "d ${dir}/proxy 0700 root root -"
    "d ${dir}/egress 0700 root root -"
  ];
  # The container can't load kernel modules itself
  boot.kernelModules = lib.optional usesWg "wireguard" ++ [ "tun" ];

  containers.heliosphere = {
    autoStart = true;
    privateNetwork = true;
    hostAddress = hostIp;
    localAddress = localIp;
    enableTun = true;
    bindMounts = {
      "/var/lib/tailscale" = { hostPath = builtins.dirOf hs.authKeyFile; isReadOnly = false; };
      "${dir}/wireguard" = { hostPath = "${dir}/wireguard"; isReadOnly = false; };
      # The CA (openvpn/ca) stays on the host; the container gets only the server side
      "${dir}/openvpn/server" = { hostPath = "${dir}/openvpn/server"; isReadOnly = true; };
      "${dir}/proxy" = { hostPath = "${dir}/proxy"; isReadOnly = true; };
      "${dir}/egress" = { hostPath = "${dir}/egress"; isReadOnly = true; };
    };

    config = { lib, pkgs, ... }: {
      system.stateVersion = stateVersion;
      users.groups.heliosphere-ovpn.gid = ovpnGid;
      networking.useHostResolvConf = lib.mkForce false;
      networking.nameservers = [ hostIp ];
      services.resolved.enable = false;
      networking.firewall.enable = false;   # Heliopause on the host does the isolation
      boot.kernel.sysctl."net.ipv4.ip_forward" = 1;

      # ------------------------------------------------------------ ways in
      services.tailscale = lib.mkIf R.tailscale.enabled {
        enable = true;
        useRoutingFeatures = "server";
        authKeyFile = "/var/lib/tailscale/${builtins.baseNameOf hs.authKeyFile}";
        extraUpFlags = [ "--hostname=${R.tailscale.hostname}" "--advertise-tags=${hs.tag}" ]
          ++ lib.optional (cfg.tailscale.loginServer != null) "--login-server=${cfg.tailscale.loginServer}"
          ++ lib.optional R.tailscale.exitNode "--advertise-exit-node"
          ++ lib.optional (lanOf "tailscale" != [ ]) "--advertise-routes=${lib.concatStringsSep "," (lanOf "tailscale")}";
      };

      networking.wireguard.interfaces.wg-in = lib.mkIf R.wireguard.enabled {
        ips = [ "10.61.0.1/24" ];
        listenPort = R.wireguard.port;
        mtu = R.wireguard.mtu;
        privateKeyFile = "${dir}/wireguard/private";
        generatePrivateKeyFile = true;
        # helios-ctl reads the public key from here when it writes client configs
        postSetup = "(umask 077; ${pkgs.wireguard-tools}/bin/wg pubkey < ${dir}/wireguard/private > ${dir}/wireguard/public)";
        peers = map (p: { inherit (p) publicKey name; allowedIPs = [ "${p.ip}/32" ]; }) R.wireguard.peers;
      };

      services.openvpn.servers = lib.mkMerge [
        (lib.mkIf R.openvpn.enabled {
          ingress.config = ''
            dev tun-in
            dev-type tun
            proto ${R.openvpn.proto}
            port ${toString R.openvpn.port}
            server 10.62.0.0 255.255.255.0
            topology subnet
            ca ${dir}/openvpn/server/ca.crt
            cert ${dir}/openvpn/server/server.crt
            key ${dir}/openvpn/server/server.key
            dh none
            tls-crypt ${dir}/openvpn/server/tls-crypt.key
            crl-verify ${dir}/openvpn/server/crl.pem
            tls-version-min 1.2
            data-ciphers AES-256-GCM:CHACHA20-POLY1305
            push "redirect-gateway def1"
            push "dhcp-option DNS ${hostIp}"
            keepalive 10 60
            persist-key
            persist-tun
            user nobody
            group heliosphere-ovpn
            status /run/openvpn-ingress.status 10
          '';
        })
        (lib.mkIf (eg.mode == "openvpn") {
          # The provider's file first; our lines after it win
          egress.config = ''
            config ${dir}/egress/client.ovpn
            dev tun-out
            dev-type tun
            mark 2
            pull-filter ignore "dhcp-option"
            auth-nocache
          '';
        })
      ];
      # Don't crash-loop before helios-ctl has made the PKI / the user uploaded a config
      systemd.services.openvpn-ingress = lib.mkIf R.openvpn.enabled { unitConfig.ConditionPathExists = "${dir}/openvpn/server/server.crt"; };
      systemd.services.openvpn-egress = lib.mkIf (eg.mode == "openvpn") { unitConfig.ConditionPathExists = "${dir}/egress/client.ovpn"; };

      services._3proxy = lib.mkIf R.proxy.enabled {
        enable = true;
        usersFile = "${dir}/proxy/users";
        denyPrivate = true;                     # never the LAN, whatever a client asks for
        resolution.nserver = [ hostIp ];        # names resolve through Magnetosphere
        services = map (t: {
          type = t.type;
          auth = [ "strong" ];
          bindAddress = localIp;
          bindPort = t.port;
          acl = [ { rule = "allow"; users = R.proxy.users; } ];
        }) [ { type = "socks"; port = R.proxy.socksPort; } { type = "proxy"; port = R.proxy.httpPort; } ];
      };

      # ------------------------------------------------------------ way out
      networking.wg-quick.interfaces.wg-out = lib.mkIf (eg.mode == "wireguard") {
        configFile = "${dir}/egress/wg-out.conf";
      };
      systemd.services.wg-quick-wg-out = lib.mkIf (eg.mode == "wireguard") { unitConfig.ConditionPathExists = "${dir}/egress/wg-out.conf"; };

      systemd.services.helios-egress-proxy = lib.mkIf (eg.mode == "proxy") {
        description = "Remote access: all traffic through the upstream proxy";
        wantedBy = [ "multi-user.target" ];
        after = [ "network.target" ];
        path = [ pkgs.jq pkgs.iproute2 ];
        serviceConfig = { Restart = "always"; RestartSec = 5; };
        # Optional credentials live in egress/proxy.json, not in the Nix store
        script = ''
          creds=""
          if [ -s ${dir}/egress/proxy.json ]; then
            u=$(jq -r '.user // empty | @uri' ${dir}/egress/proxy.json)
            p=$(jq -r '.password // empty | @uri' ${dir}/egress/proxy.json)
            [ -n "$u" ] && creds="$u:$p@"
          fi
          exec ${pkgs.tun2socks}/bin/tun2socks -device tun://tun-out -interface eth0 -fwmark 2 \
            -proxy "${eg.proxy.type or "socks5"}://$creds${eg.proxy.host or ""}:${toString (eg.proxy.port or 1080)}" \
            -tun-post-up "ip addr add 198.18.0.1/15 dev tun-out; ip link set tun-out up; ip route replace default dev tun-out table 100; ip rule add not fwmark 0x2 table 100 priority 200 2>/dev/null || true"
        '';
      };

      # Policy routing shared by every way out: the host stays reachable, and
      # replies to incoming connections leave the way they came in.
      systemd.services.helios-policy-routing = {
        description = "Remote access: policy routing";
        wantedBy = [ "multi-user.target" ];
        after = [ "network.target" ];
        serviceConfig = { Type = "oneshot"; RemainAfterExit = true; };
        script = ''
          ${ip} rule del priority 80 2>/dev/null || true
          ${ip} rule del priority 90 2>/dev/null || true
          ${ip} rule add to 10.60.0.0/24 lookup main priority 80
          ${ip} rule add fwmark 0x1 lookup main priority 90
          # Home networks some way in may reach stay on the main table, past any tunnel
          ${lib.concatMapStrings (n: "${ip} rule del to ${n} priority 85 2>/dev/null || true
${ip} rule add to ${n} lookup main priority 85
") lanAny}        '';
      };

      # ------------------------------------------------------------ firewall
      networking.nftables = {
        enable = true;
        ruleset = ''
          table inet heliosphere {
            set doh_v4 { type ipv4_addr; flags interval; elements = { ${lib.concatStringsSep ", " bl.doh_v4} } }
            set warp_v4 { type ipv4_addr; flags interval; elements = { ${lib.concatStringsSep ", " bl.warp_v4} } }

            chain mark_in {
              type filter hook prerouting priority mangle;
              iifname "eth0" ct state new ct mark set 0x1
            }
            chain mark_out {
              type route hook output priority mangle;
              ct mark 0x1 meta mark set 0x1
            }

            chain dns {
              type nat hook prerouting priority dstnat;
              # Clients' DNS always goes to Magnetosphere, whatever they ask for
              iifname != "eth0" meta l4proto { tcp, udp } th dport 53 dnat ip to ${hostIp}
            }

            chain forward {
              type filter hook forward priority filter; policy accept;
              ct state vmap { established : accept, related : accept, invalid : drop }
              # The host can't see inside a tunnel, so the DNS bypass blocks happen here too
              meta l4proto { tcp, udp } th dport 853 reject
              ip daddr @doh_v4 reject
              ip daddr @warp_v4 reject
              udp dport 2408 reject
              # Home networks: only the ways in that were allowed, only the networks chosen
              ${lib.concatMapStrings (m: lib.optionalString (lanOf m != [ ]) "iifname \"${ingressIf.${m}}\" ip daddr { ${lib.concatStringsSep ", " (lanOf m)} } accept
              ") [ "tailscale" "wireguard" "openvpn" ]}ip daddr { ${lib.concatStringsSep ", " allLans} } drop
              ${lib.optionalString (tunnel && eg.killswitch) ''
              # Kill switch: client traffic goes into the tunnel or nowhere
              oifname "eth0" ip daddr != ${hostIp} drop''}
            }

            chain output {
              type filter hook output priority filter; policy accept;
              ${lib.optionalString R.proxy.enabled ''
              # The proxy connects on clients' behalf: same DNS bypass blocks
              ip daddr @doh_v4 reject
              ip daddr @warp_v4 reject
              meta l4proto { tcp, udp } th dport 853 reject''}
              ${lib.optionalString (tunnel && eg.killswitch) ''
              # Kill switch for the container's own traffic: only the host, replies
              # to incoming connections and the tunnel itself may use eth0 directly
              oifname "eth0" ip daddr ${hostIp} accept
              oifname "eth0" meta mark { 0x1, 0x2, 51820 } accept
              oifname "eth0" meta mark & 0xff0000 == 0x80000 accept
              oifname "eth0" drop''}
            }

            chain postrouting {
              type nat hook postrouting priority srcnat;
              ip saddr { 10.61.0.0/24, 10.62.0.0/24 } oifname { "eth0"${lib.optionalString tunnel '', "${tunIf}"''} } masquerade
            }
          }
        '';
      };
    };
  };
}
