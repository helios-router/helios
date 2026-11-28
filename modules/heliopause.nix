# Heliopause — the firewall, the edge of the solar system.
# Also carries Magnetosphere's enforcement rules (DNS hijack, anti-bypass),
# since nftables needs them in the same chains.
#
# Every rule below is generated from the networks in state.json and their
# access flags (see pkgs/networks.json for what each flag means). Nothing here
# knows a network by name.
{ config, lib, pkgs, ... }:

let
  cfg = config.helios;
  I = cfg.internal;
  net = I.net;
  where = I.where;

  quote = names: lib.concatMapStringsSep ", " (n: ''"${n}"'') names;
  # nft rejects an empty set, so a rule for "no networks" is simply left out
  on = names: rule: lib.optionalString (names != [ ]) ''iifname { ${quote names} } ${rule}'';
  lines = lib.concatStringsSep "\n          ";

  hs = I.on "remote";
  bl = lib.importJSON ../pkgs/blocklists.json;
  R = I.remote;   # Heliosphere's ways in (docs/heliosphere.md)
  hsIp = "10.60.0.2";
  proxyPorts = "{ ${toString R.proxy.socksPort}, ${toString R.proxy.httpPort} }";
  hsLan = lib.unique (lib.concatMap (m: lib.optionals R.${m}.enabled (builtins.filter (n: lib.elem n I.names) R.${m}.lan)) [ "tailscale" "wireguard" "openvpn" ]);
  ts = cfg.tailscale.enable;
  nonAdmin = where (a: !a.admin);
  filtered = where (a: a.filtered) ++ lib.optional hs "ve-heliosphere";
  adminIfs = I.admins ++ [ "rescue0" ] ++ lib.optional ts "tailscale0";
  internet = where (a: a.internet) ++ lib.optional hs "ve-heliosphere";

  # ------------------------------------------------------------ revocation
  # Every rule that accepts a connection tags it (conntrack mark) with a hash of
  # the rule itself. Replies pass only while their tag is still one of this
  # ruleset's, so when an Apply takes a permission away, the rule (and its tag)
  # is gone and the other side's packets stop too, at once, while connections
  # whose rule didn't change carry on untouched. Each packet the opening side
  # sends is first marked "revoked", then re-tagged by whichever rule still
  # allows it: a change no rule text shows (a device forgotten, or paused by MAC)
  # takes the connection's replies away as soon as that side sends anything.
  # ICMP errors about a connection ("related") pass only along its own path.
  tagOf = text: "0x8" + builtins.substring 0 7 (builtins.hashString "sha256" text);
  revoked = "0x7fffffff";     # never a tag (tags start at 0x80000000)
  # Every interface traffic can be forwarded to, for rules that name only where it comes from
  allIfs = lib.unique (I.ifaces ++ [ "rescue0" ]);
  # A forward rule from one interface to another, tagged
  fwd = iif: oif: match: comment: rec {
    inherit iif oif;
    tag = tagOf "forward ${iif}>${oif} ${match}";
    line = ''iifname "${iif}" oifname "${oif}" ${match} ct mark set ${tag} accept   # ${comment}'';
  };
  # From one interface to anywhere: one rule per known destination, and one for
  # the rest (no ICMP errors for those: their path isn't known)
  fwdAll = iif: comment: map (o: fwd iif o "" comment) (lib.remove iif allIfs)
    ++ [ rec { inherit iif; oif = null; tag = tagOf "forward ${iif}>*"; line = ''iifname "${iif}" ct mark set ${tag} accept   # ${comment}''; } ];
  # An input rule (to the router itself), one per interface, tagged
  inp = names: match: comment: map (n: rec {
    iif = n;
    tag = tagOf "input ${n} ${match}";
    line = ''iifname "${n}" ${match} ct mark set ${tag} accept   # ${comment}'';
  }) names;
  render = rules: lines (map (r: r.line) rules);
  icmpTag = tagOf "input icmp";
  # The router's own connections, by the interface they leave on (for ICMP errors back)
  localTags = map (o: { oif = o; tag = tagOf "output ${o}"; }) allIfs;

  # Who may open connections to whom
  reachFwd = lib.concatMap (n:
    let r = n.access.reach; in
    if r == "all" then fwdAll n.name "${n.name} reaches every network"
    else map (t: fwd n.name t "" "reach") r) I.networks;

  # Media servers (state.json "mediaServer" on a device): media devices allowed to
  # use this server (their "mediaServers" list, or all servers when they have
  # none), plus the networks the server lists, may reach its ports.
  portSet = ps: "{ ${lib.concatMapStringsSep ", " toString ps} }";
  mediaFwd = lib.concatMap (d:
    let
      m = d.mediaServer;
      dst = lib.optional ((m.tcp or [ ]) != [ ]) "ip daddr ${d.ip} tcp dport ${portSet m.tcp}"
        ++ lib.optional ((m.udp or [ ]) != [ ]) "ip daddr ${d.ip} udp dport ${portSet m.udp}";
      nets = builtins.filter (n: lib.elem n I.names) (m.networks or [ ]);
      clients = builtins.filter (c: isMediaClient c && ((c.mediaServers or null) == null || lib.elem d.name c.mediaServers)) I.withIp;
      clientNets = lib.unique (map (c: c.vlan) clients);
      ipsOn = v: map (c: c.ip) (builtins.filter (c: c.vlan == v) clients);
    in
      lib.concatMap (v: map (r: fwd v d.vlan "ip saddr { ${lib.concatStringsSep ", " (ipsOn v)} } ${r}" d.name) dst) clientNets
      ++ lib.concatMap (n: map (r: fwd n d.vlan r d.name) dst) nets)
    I.mediaServers;
  mediaNets = lib.unique (map (d: d.vlan) (builtins.filter isMediaClient I.withIp));
  castFwd = lib.concatMap (c: map (m: fwd c m "ip daddr @media_clients" "casting to TVs") mediaNets) (where (a: a.cast));

  # Unnamed devices are restricted on networks with "quarantine" until you name
  # them. Matched by MAC (covers IPv4 and IPv6). Needs state.json
  # "restrictUnnamed": true (Photosphere → Devices) AND a named device on an admin
  # network, so neither a fresh install nor an upgrade locks its owner out;
  # Tailscale and the rescue port are never affected.
  armed = (cfg.state.restrictUnnamed or false)
    && builtins.any (d: (I.byName.${d.vlan}.access.admin or false)) I.devices;
  qNets = lib.optionals armed (where (a: a.quarantine or false));
  knownMacs = lib.unique (map (d: lib.toLower d.mac) I.devices);
  macElems = lib.optionalString (knownMacs != [ ]) "elements = { ${lib.concatStringsSep ", " knownMacs} }";
  blockedMacs = lib.unique (map (d: lib.toLower d.mac) (builtins.filter (d: d.blocked or false) I.devices));
  unknown = rule: on qNets "ether saddr != @known_macs ${rule}";

  isMediaClient = d: (d.media or false) && !(I.byName.${d.vlan}.access.admin or true);
  rescue = cfg.interfaces.rescueAddress;
  # Numeric: the ruleset is checked at build time, where the account doesn't exist
  caddyUid = let u = config.users.users.caddy.uid or null; in if u != null then u else config.ids.uids.caddy;
  rescueNet = "${lib.concatStringsSep "." (lib.take 3 (lib.splitString "." rescue))}.0/24";
  # Networks whose devices may open ports with UPnP / NAT-PMP (voyager.nix)
  upnpNets = lib.optionals ((cfg.state.upnp or { }).enabled or true) (where (a: a.upnp));
  sw = cfg.solarWind;
  trapPorts = lib.concatMapStringsSep ", " toString ([ 22 23 80 443 2222 3000 8080 8090 9090 ] ++ lib.optional (I.on "notifications") sw.port);

  # Every accepting rule, in chain order
  quarantineFwd = map (n: fwd n "wan" "" "unnamed device: internet") (builtins.filter (n: I.byName.${n}.access.internet) qNets);
  forwardRules = lib.optionals ts (fwdAll "tailscale0" "you over Tailscale reach every network")
    ++ reachFwd
    # Port forwards opened by UPnP/NAT-PMP: only to networks that may ask
    # (miniupnpd only maps for "upnp" networks), so a stray DNAT rule can't open
    # an admin network to the internet
    ++ map (n: fwd "wan" n "ct status dnat" "UPnP / NAT-PMP port forward") upnpNets
    # Remote access: the WireGuard / OpenVPN ports (heliopause-nat below)
    ++ lib.optional (hs && (R.wireguard.enabled || R.openvpn.enabled)) (fwd "wan" "ve-heliosphere" "ip daddr ${hsIp} ct status dnat" "remote access ports")
    # Remote access clients may reach the home networks their way in allows
    ++ lib.optionals hs (map (l: fwd "ve-heliosphere" l "" "remote access to a home network") hsLan)
    # Remote access proxy for chosen networks (never offered on the WAN: it isn't encrypted)
    ++ lib.optionals (hs && R.proxy.enabled) (map (n: fwd n "ve-heliosphere" "ip daddr ${hsIp} tcp dport ${proxyPorts}" "remote access proxy")
      (builtins.filter (n: lib.elem n I.names) R.proxy.networks))
    # Pinholes: media servers, and casting to media devices
    ++ mediaFwd ++ castFwd
    # Internet access; anything not allowed above never crosses networks
    ++ map (n: fwd n "wan" "" "internet") internet;
  allFwd = quarantineFwd ++ forwardRules;
  fwdTags = lib.unique (map (r: r.tag) allFwd);
  # An ICMP error about a connection travels its path, either way
  fwdRelated = lib.unique (lib.concatMap (r: [ ''${r.tag} . "${r.oif}" . "${r.iif}"'' ''${r.tag} . "${r.iif}" . "${r.oif}"'' ])
    (builtins.filter (r: r.oif != null) allFwd));

  quarantineIn = lib.concatMap (n: inp [ n ] "meta l4proto { tcp, udp } th dport 53" "unnamed device: DNS"
    ++ inp [ n ] "udp dport { 67, 123, 547 }" "unnamed device: DHCP, NTP") qNets;
  inputRules = inp [ "wan" ] "udp dport 546" "DHCPv6-PD"
    ++ inp adminIfs "" "admin"
    ++ inp (nonAdmin ++ lib.optional hs "ve-heliosphere") "meta l4proto { tcp, udp } th dport 53" "DNS"
    ++ inp nonAdmin "udp dport { 67, 123 }" "DHCP, NTP"
    ++ inp (where (a: !a.admin && a.printing)) "tcp dport 631" "printing"
    ++ inp (where (a: !a.admin && a.upnp)) "udp dport { 1900, 5351 }" "UPnP SSDP, NAT-PMP"
    ++ inp (where (a: !a.admin && a.upnp)) "tcp dport 5000" "miniupnpd control"
    ++ inp (where (a: !a.admin && a.mdns)) "udp dport 5353" "mDNS"
    ++ inp (builtins.filter (n: !I.byName.${n}.access.admin) (I.nutServerNets or [ ])) "tcp dport 3493" "UPS status for other machines (NUT)"
    ++ lib.optionals ts (inp [ "wan" ] "udp dport 41641" "Tailscale direct");
  allIn = quarantineIn ++ inputRules;
  inTags = lib.unique ([ icmpTag ] ++ map (r: r.tag) allIn);
  inRelated = lib.unique (map (r: ''${r.tag} . "${r.iif}"'') allIn ++ map (l: ''${l.tag} . "${l.oif}"'') localTags);
  set = elems: "{ ${lib.concatStringsSep ", " elems} }";
in
{
  networking.firewall.enable = false;
  networking.nftables = {
    enable = true;
    ruleset = ''
      table inet heliopause {
        # Public DNS / DoH resolvers filtered networks must not reach directly
        set doh_v4 { type ipv4_addr; flags interval; elements = { ${lib.concatStringsSep ", " bl.doh_v4} } }
        # Cloudflare WARP endpoints (verify current ranges before relying on this)
        set warp_v4 { type ipv4_addr; flags interval; elements = { ${lib.concatStringsSep ", " bl.warp_v4} } }
        set doh_v6 { type ipv6_addr; flags interval; elements = { ${lib.concatStringsSep ", " bl.doh_v6} } }
        set warp_v6 { type ipv6_addr; flags interval; elements = { ${lib.concatStringsSep ", " bl.warp_v6} } }
        # From state.json: devices marked "media" (TVs) — may reach media servers, may be cast to
        set media_clients { type ipv4_addr; ${I.nftElems (I.ipsWhere isMediaClient)} }
        # From state.json: devices cut off entirely ("blocked"). Photosphere's
        # Pause button also adds/removes elements live.
        set cutoff { type ipv4_addr; ${I.nftElems (I.ipsWhere (d: d.blocked or false))} }
        # The same devices by MAC, so a paused device can't carry on over IPv6 (SLAAC)
        set cutoff_mac { type ether_addr; ${lib.optionalString (blockedMacs != [ ]) "elements = { ${lib.concatStringsSep ", " blockedMacs} }"} }
        # Trap: anything on a non-admin network poking the router's admin ports
        # is dropped and logged once per hour per device ("heliopause-trap:" →
        # logwatch → Solar Wind flare). DNS/DHCP keep working.
        set trapped { type ipv4_addr; flags timeout; timeout 1h; }
        # Named devices (state.json); anything else on a "quarantine" network is restricted
        set known_macs { type ether_addr; ${macElems} }

        # Unnamed devices: DHCP, DNS and NTP from the router, nothing else (admin
        # ports hit the trap like on any other network)
        chain quarantine_in {
          ${render quarantineIn}
          tcp dport { ${trapPorts} } ip saddr != @trapped add @trapped { ip saddr } log prefix "heliopause-trap: " level warn
          drop
        }
        # Unnamed devices: filtered internet only (where the network has internet), never other networks
        chain quarantine_fwd {
          meta l4proto { tcp, udp } th dport 853 reject
          ip daddr @doh_v4 reject
          ip daddr @warp_v4 reject
          ip6 daddr @doh_v6 reject
          ip6 daddr @warp_v6 reject
          udp dport 2408 reject
          ${render quarantineFwd}
          drop
        }

        chain input {
          type filter hook input priority filter; policy drop;
          iif lo accept
          # The rescue address only answers on the rescue port itself — otherwise
          # Linux would accept it from any VLAN and expose password SSH there.
          ip daddr ${rescue} iifname != "rescue0" drop
          # ...and the rescue subnet only ever comes from the rescue port: Caddy and
          # PAM skip the second factor for it by source address (gate.nix, airlock.nix)
          ip saddr ${rescueNet} iifname != "rescue0" drop
          # Non-admin networks only send from their own addresses (0.0.0.0: a DHCP
          # request before the device has one; IPv6: what the router routes back
          # there, link-local included; ICMPv6 left alone for neighbour discovery)
          ${lines (map (n: ''iifname "${n}" ip saddr != { ${net n}, 0.0.0.0 } drop'') nonAdmin)}
          ${on nonAdmin "meta nfproto ipv6 meta l4proto != ipv6-icmp fib saddr . iif oif missing drop"}
          # Replies to the router's own connections pass. Packets a client sends on
          # an open connection go through the rules below again (and the router's
          # answers are checked in "output"), so taking a network's admin access
          # away also cuts its open SSH / Cockpit sessions, both ways.
          ct state invalid drop
          # ICMP errors about a connection only from where that connection goes
          ct state related ct mark . iifname ${set inRelated} accept
          ct state related drop
          ct direction reply ct state established accept
          ct direction original ct mark set ${revoked}
          iifname "wan" icmp type echo-request limit rate over 10/second drop
          meta l4proto { icmp, ipv6-icmp } ct mark set ${icmpTag} accept
          ${unknown "jump quarantine_in   # not named yet"}
          ${on nonAdmin ''tcp dport { ${trapPorts} } ip saddr != @trapped add @trapped { ip saddr } log prefix "heliopause-trap: " level warn''}
          ${on nonAdmin "tcp dport { ${trapPorts} } drop"}
          ${render inputRules}
        }

        chain output {
          type filter hook output priority filter; policy accept;
          ${lib.optionalString (I.on "gate") ''
          # Sign-in gate: Cockpit listens on localhost behind Caddy and believes the
          # client address Caddy sends (X-Forwarded-For, for the rescue-port rule).
          # Only Caddy (and root) may connect, so no local service account can skip
          # the gate or claim to be on the rescue port.
          oifname "lo" tcp dport ${toString I.gate.cockpitPort} meta skuid != { 0, ${toString caddyUid} } reject with tcp reset''}
          # The router's own connections, tagged by where they leave (ICMP errors back)
          ct state new ct mark set oifname map { ${lib.concatMapStringsSep ", " (l: ''"${l.oif}" : ${l.tag}'') localTags} }
          # The router's answers on connections a network may no longer open (admin
          # access taken away): nothing more, not even what the router was sending
          oifname != "lo" ct direction reply ct state established ct mark != ${set ([ "0x0" ] ++ inTags)} drop
        }
        chain forward {
          type filter hook forward priority filter; policy drop;
          # Blocked devices first, so pausing one also kills its open connections
          # (by MAC, for IPv6: the connection's replies stop once the device sends anything)
          ip saddr @cutoff ct mark set ${revoked} reject
          ether saddr @cutoff_mac ct mark set ${revoked} reject
          ip daddr @cutoff drop

          # Anti-spoofing, before connection tracking: conntrack matches a packet by
          # addresses and ports only, not the interface it came in on, so a spoofed
          # packet copying another network's open connection would otherwise pass
          # as "established". A non-admin network may only send from its own subnet
          # (IPv4), or from a prefix the router routes to it (IPv6: SLAAC prefixes
          # come from the ISP, so the routing table is the source of truth).
          ${lines (map (n: ''iifname "${n}" ip saddr != ${net n} drop'') nonAdmin)}
          ${on nonAdmin "meta nfproto ipv6 fib saddr . iif oif missing drop"}
          ip saddr ${rescueNet} iifname != "rescue0" drop
          ${lib.optionalString hs ''iifname "ve-heliosphere" ip saddr != 10.60.0.2 drop''}
          # Remote access is IPv4-only, and its filtering (inside the container too) only knows IPv4
          ${lib.optionalString hs ''iifname "ve-heliosphere" meta nfproto ipv6 drop''}

          # The side that opened a connection goes through the rules below on every
          # packet, and replies pass only while the rule that allowed the connection
          # is still in this ruleset (its tag, see "revocation" above), so a policy
          # change (reach, internet, quarantine, a port forward, remote access)
          # applies to connections that are already open, in both directions.
          ct state invalid drop
          # ICMP errors about a connection only along that connection's path: a
          # forged one quoting another network's connection doesn't cross over
          ${lib.optionalString (fwdRelated != [ ]) "ct state related ct mark . iifname . oifname ${set fwdRelated} accept"}
          ct state related drop
          ct direction reply ct state established ct mark ${set ([ "0x0" ] ++ fwdTags)} accept
          ct direction original ct mark set ${revoked}

          # Devices not named yet on "quarantine" networks
          ${unknown "jump quarantine_fwd"}

          # Magnetosphere anti-bypass for filtered networks: DoT, direct DoH, WARP
          ${on filtered "meta l4proto { tcp, udp } th dport 853 reject"}
          ${on filtered "ip daddr @doh_v4 reject"}
          ${on filtered "ip daddr @warp_v4 reject"}
          ${on filtered "ip6 daddr @doh_v6 reject"}
          ${on filtered "ip6 daddr @warp_v6 reject"}
          ${on filtered "udp dport 2408 reject"}

          ${render forwardRules}
        }
      }

      table inet heliopause-nat {
        set known_macs { type ether_addr; ${macElems} }
        chain prerouting {
          type nat hook prerouting priority dstnat;
          # Magnetosphere: hijack hard-coded DNS on filtered networks (and from unnamed devices) back to AdGuard
          ${unknown "meta l4proto { tcp, udp } th dport 53 redirect to :53"}
          ${on filtered "meta l4proto { tcp, udp } th dport 53 redirect to :53"}
          # Remote access: WireGuard / OpenVPN ports on the WAN go to Heliosphere (the proxy is LAN-only)
          ${lib.optionalString (hs && R.wireguard.enabled) ''iifname "wan" udp dport ${toString R.wireguard.port} dnat ip to ${hsIp}   # WireGuard''}
          ${lib.optionalString (hs && R.openvpn.enabled) ''iifname "wan" ${R.openvpn.proto} dport ${toString R.openvpn.port} dnat ip to ${hsIp}   # OpenVPN''}
          # Gadgets get their time from the router, whatever server they hard-code
          ${on (where (a: a.forceNtp)) "udp dport 123 redirect to :123"}
        }
        chain postrouting {
          type nat hook postrouting priority srcnat;
          oifname "wan" meta nfproto ipv4 masquerade
          ${lib.optionalString ts ''iifname "tailscale0" oifname "wan" meta nfproto ipv6 masquerade   # exit-node IPv6''}
        }
      }
    '';
  };
}
