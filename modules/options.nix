# HeliOS options. Everything specific to one household is set in your site.nix
# (copy templates/site.nix) and state.json — never in these modules.
{ config, lib, pkgs, ... }:

let
  inherit (lib) mkOption mkEnableOption types;
  cfg = config.helios;

  catalog = lib.importJSON ../pkgs/networks.json;
  drvCatalog = lib.importJSON ../pkgs/drivers.json;
  svcCatalog = lib.importJSON ../pkgs/services.json;
  # Switchable services: site.nix gives the default, Photosphere (state.json
  # "services") can override it. Modules ask `cfg.internal.on "<id>"`.
  serviceDefaults = {
    notifications = cfg.solarWind.enable;
    vulnScan = true;
    printing = cfg.printing.enable;
    remote = cfg.heliosphere.enable;
    logwatch = cfg.logwatch.enable;
  };
  on = id: ((state.services or { }).${id} or { }).enabled or serviceDefaults.${id};
  state = cfg.state;
  devices = state.devices or [ ];
  withIp = builtins.filter (d: (d.ip or null) != null) devices;

  # Networks come from state.json (edited in Photosphere → Networks); a site
  # without a "networks" list gets the five defaults. Each network's access is
  # its role's access with the network's own overrides on top.
  resolve = n: n // {
    ipv6 = n.ipv6 or null;
    access = (catalog.roles.${n.role} or (throw "network ${n.name}: unknown role ${n.role}")).access // (n.access or { });
  };
  networks = map resolve (state.networks or catalog.defaults);
  names = map (n: n.name) networks;
  byName = lib.listToAttrs (map (n: lib.nameValuePair n.name n) networks);
  where = pred: map (n: n.name) (builtins.filter (n: pred n.access) networks);
  admins = where (a: a.admin);
  unique = l: lib.length (lib.unique l) == lib.length l;
  reserved = [ "wan" "lo" "trunk0" "rescue0" "tailscale0" "ifb4wan" "lan0" ];

  # Drivers and hardware (Photosphere → Hardware; see docs/drivers.md)
  hardware = state.hardware or [ ];
  driverOn = name: let d = drvCatalog.drivers.${name}; in
    (d.always or false) || ((state.drivers or { }).${name}.enabled or d.default);
  driverSettings = name: (state.drivers or { }).${name} or { };
  routerPorts = builtins.filter (p: (p.mode or "unused") != "unused")
    ((lib.findFirst (h: h.kind == "router") { } hardware).ports or [ ]);
  portNets = p: if p.mode == "access" then [ p.network ]
    else if p.mode == "trunk" then (if p.networks == "all" then names else p.networks) else [ ];
  macOf = p: lib.toLower (p.match.mac or "");

  # Heliosphere / Remote access settings with defaults (docs/heliosphere.md)
  hsState = state.heliosphere or { };
  hsIn = hsState.ingress or { };
  remote = {
    # lan = home networks that clients of this way in may reach (default: none)
    tailscale = { enabled = true; exitNode = true; hostname = "heliosphere"; lan = [ ]; } // (hsIn.tailscale or { });
    wireguard = { enabled = false; port = 51820; peers = [ ]; lan = [ ]; tunnel = "full"; keepalive = 25; mtu = null; } // (hsIn.wireguard or { });
    openvpn = { enabled = false; port = 1194; proto = "udp"; clients = [ ]; lan = [ ]; } // (hsIn.openvpn or { });
    proxy = { enabled = false; socksPort = 1080; httpPort = 3128; networks = [ ]; users = [ ]; } // (builtins.removeAttrs (hsIn.proxy or { }) [ "wan" ]);
    egress = { mode = "direct"; killswitch = true; proxy = null; } // (hsState.egress or { });
  };
  hsPorts = lib.optional remote.wireguard.enabled remote.wireguard.port
    ++ lib.optional remote.openvpn.enabled remote.openvpn.port
    ++ lib.optionals remote.proxy.enabled [ remote.proxy.socksPort remote.proxy.httpPort ];
in
{
  imports = [
    (lib.mkRemovedOptionModule [ "helios" "vlans" ] ''
      Networks (VLANs) now live in state.json so Photosphere can edit them:
      Photosphere → Networks, or a "networks" list as in templates/state.json.
      Without one, the five default networks are used.'')
    (lib.mkRemovedOptionModule [ "helios" "mediaServer" ] ''
      Media servers are now set per device in state.json, so Photosphere can edit them:
      Devices → edit the server → "Media server", or add to its entry
        "mediaServer": { "tcp": [ 8096 ], "udp": [ ], "networks": [ "family" ] }'')
  ];

  options.helios = {
    hostName = mkOption { type = types.str; default = "helios"; description = "Router hostname (also <hostName>.<domain>)."; };
    domain = mkOption { type = types.str; default = "home.arpa"; description = "Local DNS suffix handed out by DHCP. home.arpa is the standard for homes."; };

    admin = {
      user = mkOption { type = types.str; description = "Your login on the router (wheel, SSH)."; };
      sshKeys = mkOption { type = types.listOf types.str; default = [ ]; description = "Authorized SSH public keys (sk-ssh-ed25519 hardware keys recommended)."; };
    };

    configDir = mkOption {
      type = types.str;
      default = "/etc/nixos";
      description = "Writable checkout that holds state.json. Photosphere edits it, commits to git there and runs nixos-rebuild.";
    };
    stateFile = mkOption { type = types.str; default = "state.json"; description = "State file name inside configDir."; };
    state = mkOption {
      type = types.attrs;
      description = "Parsed state.json: set to `builtins.fromJSON (builtins.readFile ./state.json)` in site.nix.";
    };

    interfaces = {
      trunkMac = mkOption { type = types.str; description = "MAC of the NIC trunked to the switch (carries WAN + all VLANs)."; };
      rescueMac = mkOption { type = types.nullOr types.str; default = null; description = "MAC of a spare NIC used as an out-of-band rescue port (static IP, password SSH). null = none."; };
      rescueAddress = mkOption { type = types.str; default = "192.168.255.1"; description = "Rescue port address (/24). Plug a laptop in with .2."; };
      wanVlan = mkOption { type = types.ints.between 2 4094; default = 10; description = "VLAN the modem is on at the switch."; };
    };

    tailscale = {
      enable = mkOption { type = types.bool; default = true; };
      authKeyFile = mkOption { type = types.str; default = "/var/lib/tailscale/authkey"; };
      loginServer = mkOption {
        type = types.nullOr (types.strMatching "https://[^ ]+");
        default = null;
        example = "https://headscale.example.com";
        description = "Your own Headscale (or other) control server instead of Tailscale's. null = Tailscale's own. Used by the router and Heliosphere.";
      };
      tag = mkOption { type = types.str; default = "tag:helios"; description = "Tag advertised by the router node (must exist in your tailnet policy)."; };
      adminExit = mkOption { type = types.bool; default = true; description = "Advertise the router as an unfiltered exit node (restrict it to yourself in tailnet grants)."; };
      advertiseRoutes = mkOption { type = types.bool; default = true; description = "Advertise the networks with \"tailnet\" access as subnet routes."; };
    };

    heliosphere = {
      enable = mkEnableOption "Heliosphere, a second, DNS-filtered Tailscale exit node (NixOS container) for family use";
      tag = mkOption { type = types.str; default = "tag:heliosphere"; };
      authKeyFile = mkOption { type = types.str; default = "/var/lib/heliosphere/tailscale/authkey"; };
      publicHost = mkOption {
        type = types.nullOr types.str;
        default = null;
        example = "home.example.com";
        description = "Name or IP that WireGuard/OpenVPN client configs connect to. null = the WAN IP at the time a client is added (may change).";
      };
    };

    solarWind = {
      enable = mkOption { type = types.bool; default = true; description = "Notifications (internally \"Solar Wind\"): self-hosted ntfy push notifications."; };
      port = mkOption { type = types.port; default = 2586; };
      exposure = mkOption {
        type = types.enum [ "local" "proxy" "funnel" ];
        default = "local";
        description = ''
          How phones reach the notification server:
            local  — only from admin networks and Tailscale (no internet exposure)
            proxy  — a reverse proxy you run elsewhere (e.g. Caddy on a VPS) forwards to the router over Tailscale;
                     see templates/Caddyfile.solarwind
            funnel — Tailscale Funnel publishes it at https://<host>.<tailnet>.ts.net — no domain or VPS needed
        '';
      };
      publicUrl = mkOption { type = types.nullOr types.str; default = null; description = "URL phones subscribe to, e.g. https://alerts.example.com or https://helios.your-tailnet.ts.net. Required for proxy/funnel."; };
      iosRelay = mkOption { type = types.bool; default = false; description = "Let iPhones get instant push via ntfy.sh (only message IDs are relayed; content stays on your server)."; };
      topics = {
        flare = mkOption { type = types.str; default = "flare"; description = "Urgent topic: critical logs, trap hits, failures."; };
        aurora = mkOption { type = types.str; default = "aurora"; description = "Informational topic: internet back, AI log summaries, backups."; };
      };
    };

    printing = {
      enable = mkEnableOption "a CUPS print server for USB printers, shared to networks with printing access";
      drivers = mkOption { type = types.listOf types.package; default = [ ]; description = "CUPS driver packages (leave empty for driverless/IPP-USB printers)."; };
      unfreeDrivers = mkOption { type = types.listOf types.str; default = [ ]; description = "Package names of proprietary drivers to allow, e.g. [ \"canon-cups-ufr2\" ]."; };
      ippUsb = mkOption { type = types.bool; default = true; description = "Enable ipp-usb for driverless USB printers."; };
    };

    logwatch = {
      enable = mkEnableOption "AI triage of journal warnings with a small local model";
      model = mkOption { type = types.str; default = "/var/lib/llm/model.gguf"; };
      backend = mkOption { type = types.enum [ "vulkan" "cpu" "remote" ]; default = "vulkan"; description = "vulkan = local iGPU, cpu = local CPU, remote = an existing llama.cpp server (remoteUrl)."; };
      remoteUrl = mkOption { type = types.nullOr types.str; default = null; description = "llama.cpp server URL when backend = remote."; };
    };

    internal = mkOption {
      type = types.attrs;
      internal = true;
      description = "Derived values shared between HeliOS modules.";
    };
  };

  config = {
    # A headless router doesn't need the NixOS HTML manual (man pages stay). It's
    # also the heaviest thing to rebuild when the NixOS version string changes.
    documentation.nixos.enable = lib.mkDefault false;

    assertions = [
      { assertion = cfg.tailscale.loginServer == null || cfg.solarWind.exposure != "funnel";
        message = "helios.solarWind.exposure = \"funnel\" needs Tailscale's own servers; with tailscale.loginServer (Headscale) use \"local\" or \"proxy\""; }
      { assertion = !(builtins.any (n: lib.elem n.prefix [ "10.60.0" "10.61.0" "10.62.0" ]) networks);
        message = "state.json networks: 10.60.0, 10.61.0 and 10.62.0 are reserved for Remote access (Heliosphere)"; }
      { assertion = unique hsPorts && !(builtins.any (p: lib.elem p [ 22 53 67 123 443 2586 3493 5353 9090 41641 ]) hsPorts);
        message = "state.json heliosphere: ports must be distinct and not one the router itself uses"; }
      { assertion = builtins.all (m: builtins.all (n: lib.elem n names) remote.${m}.lan) [ "tailscale" "wireguard" "openvpn" ];
        message = "state.json heliosphere: a way in's \"lan\" names a network that doesn't exist"; }
      { assertion = !remote.proxy.enabled || (remote.proxy.users != [ ] && remote.proxy.networks != [ ]);
        message = "state.json heliosphere: the proxy needs at least one user and at least one network (it is never offered on the internet)"; }
      { assertion = lib.elem remote.egress.mode [ "direct" "wireguard" "openvpn" "proxy" ] && (remote.egress.mode != "proxy" || remote.egress.proxy != null);
        message = "state.json heliosphere: egress mode must be direct, wireguard, openvpn or proxy (proxy needs egress.proxy)"; }
      { assertion = builtins.all (x: builtins.match "[A-Za-z0-9_-]{1,64}" x.name != null) (state.printing.printers or [ ])
          && lib.length (builtins.filter (x: x.default or false) (state.printing.printers or [ ])) <= 1;
        message = "state.json printing: printer names are letters, digits, - and _ (max 64), and at most one is the default"; }
      { assertion = admins != [ ];
        message = "state.json networks: at least one network needs admin access, or you lock yourself out"; }
      { assertion = unique names && unique (map (n: n.vlan) networks) && unique (map (n: n.prefix) networks);
        message = "state.json networks: names, VLAN ids and prefixes must be unique"; }
      { assertion = !(lib.elem cfg.interfaces.wanVlan (map (n: n.vlan) networks));
        message = "state.json networks: VLAN ${toString cfg.interfaces.wanVlan} is the WAN VLAN"; }
      { assertion = builtins.all (n: builtins.match "[a-z][a-z0-9]{0,11}" n != null && !(lib.elem n reserved)) names;
        message = "state.json networks: names must be 1-12 lowercase letters/digits, not ${lib.concatStringsSep ", " reserved}"; }
      { assertion = builtins.all (d: lib.elem d.vlan names) devices;
        message = "state.json: a device is on a network that doesn't exist"; }
      { assertion = builtins.all (p: builtins.all (n: lib.elem n names) (portNets p)) routerPorts;
        message = "state.json hardware: a router port uses a network that doesn't exist"; }
      { assertion = lib.length (builtins.filter (p: p.mode == "wan") routerPorts) <= 1;
        message = "state.json hardware: only one router port can be the WAN port"; }
      { assertion = !(builtins.any (p: lib.elem (macOf p) (map lib.toLower ([ cfg.interfaces.trunkMac ] ++ lib.optional (cfg.interfaces.rescueMac != null) cfg.interfaces.rescueMac))) routerPorts);
        message = "state.json hardware: the trunk NIC and the rescue port can't be mapped as router ports"; }
      { assertion = builtins.all (p: (p.match ? mac) != (p.match ? name)) routerPorts;
        message = "state.json hardware: each router port matches either a MAC or a (switch-chip) port name"; }
      { assertion = builtins.all (n: builtins.all (t: lib.elem t names) (if n.access.reach == "all" then [ ] else n.access.reach)) networks;
        message = "state.json networks: \"reach\" names a network that doesn't exist"; }
      { assertion = cfg.solarWind.exposure == "local" || cfg.solarWind.publicUrl != null;
        message = "helios.solarWind.publicUrl is required when exposure is \"${cfg.solarWind.exposure}\""; }
      { assertion = cfg.solarWind.exposure != "funnel" || cfg.tailscale.enable;
        message = "helios.solarWind.exposure = \"funnel\" needs helios.tailscale.enable"; }
      { assertion = cfg.logwatch.backend != "remote" || cfg.logwatch.remoteUrl != null;
        message = "helios.logwatch.remoteUrl is required when backend = \"remote\""; }
    ];

    helios.internal = rec {
      inherit devices withIp;
      # Devices that serve media (state.json "mediaServer": { tcp, udp, networks })
      mediaServers = builtins.filter (d: (d.mediaServer or null) != null) withIp;
      ipsWhere = pred: map (d: d.ip) (builtins.filter pred withIp);
      # nft refuses "elements = { }", so only emit the line when there is something
      nftElems = ips: lib.optionalString (ips != [ ]) "elements = { ${lib.concatStringsSep ", " ips} }";
      inherit catalog networks names byName where admins;
      inherit drvCatalog hardware driverOn driverSettings routerPorts portNets remote on;
      upsDevices = builtins.filter (h: h.kind == "ups") hardware;
      # Interface name for a router port: switch-chip ports keep theirs, NICs get p<mac>
      portIface = p: if p.mode == "wan" then "wan"
        else if p.match ? name then p.match.name else "p" + lib.replaceStrings [ ":" ] [ "" ] (macOf p);
      vlanOf = name: byName.${name}.vlan;
      gw = name: "${byName.${name}.prefix}.1";
      net = name: "${byName.${name}.prefix}.0/24";
      # The router's "home" address (first admin network): Cockpit, Solar Wind, its own name
      # (falls back so a config with no admin network reaches its assertion instead of crashing)
      adminGw = if admins != [ ] then gw (lib.head admins) else if names != [ ] then gw (lib.head names) else "127.0.0.1";
      solarWindUrl = "http://127.0.0.1:${toString cfg.solarWind.port}";
      solarWindTokenFile = "/var/lib/helios/solarwind-token";
      ifaces = [ "wan" ] ++ names
        ++ lib.optional cfg.tailscale.enable "tailscale0"
        ++ lib.optional (on "remote") "ve-heliosphere"
        ++ [ "trunk0" ];
    };

    # Everything helios-ctl / Photosphere need to know, generated from the options
    environment.etc."helios/config.json".text = builtins.toJSON {
      inherit (cfg) hostName domain configDir stateFile;
      inherit (cfg.interfaces) wanVlan;
      networks = networks;
      roles = catalog.roles;
      accessHelp = catalog.access;
      defaultNetworks = catalog.defaults;
      driverCatalog = drvCatalog;
      recovery = { units = cfg.internal.recoveryUnits or [ ]; cooldownMin = 30; maxPerDay = 6; };
      serviceCatalog = svcCatalog;
      inherit serviceDefaults;
      profileCatalog = lib.importJSON ../pkgs/profiles.json;
      printing = {
        enabled = on "printing";
        catalog = (lib.importJSON ../pkgs/printing.json).drivers;
        # site.nix drivers (often proprietary), by package name, so Photosphere can list them
        siteDrivers = map lib.getName cfg.printing.drivers;
        testPage = "${pkgs.cups}/share/cups/data/testprint";
      };
      logwatch = {
        enabled = on "logwatch";
        catalog = lib.importJSON ../pkgs/logwatch-models.json;
        site = { inherit (cfg.logwatch) backend model remoteUrl; };
      };
      inherit (cfg.interfaces) trunkMac rescueMac;
      detectFile = "/var/lib/helios-detect/hardware.json";
      vulnixSummary = "/var/lib/helios/vulnix-summary.json";
      ifaces = cfg.internal.ifaces;
      firewallTable = "heliopause";
      services = lib.filter (s: s != null) [
        [ "kea-dhcp4-server" "DHCP" ]
        [ "adguardhome" "Magnetosphere (DNS filter)" ]
        [ "nftables" "Heliopause (firewall)" ]
        (if (state.qos.enabled or false) then [ "helios-sqm" "QoS" ] else null)
        (if (state.upnp.enabled or true) then [ "miniupnpd" "UPnP" ] else null)
        [ "chronyd" "NTP" ]
        (if cfg.tailscale.enable then [ "tailscaled" "Tailscale (admin exit)" ] else null)
        (if on "remote" then [ "container@heliosphere" "Remote access" ] else null)
        (if on "notifications" then [ "ntfy-sh" "Notifications (ntfy)" ] else null)
        (if on "printing" then [ "cups" "Printing" ] else null)
        (if on "logwatch" && cfg.logwatch.backend != "remote" then [ "llama-cpp" "Log AI" ] else null)
        (if on "logwatch" then [ "logwatch.timer" "Log watcher" ] else null)
        [ "helios-uplink.timer" "Outage monitor" ]
        [ "helios-backup.timer" "Config backup" ]
        [ "helios-detect.timer" "Hardware detection" ]
        [ "helios-vulnix.timer" "Vulnerability scan" ]
        (if driverOn "lldp" then [ "lldpd" "Neighbour discovery (LLDP)" ] else null)
        (if driverOn "nut" && builtins.any (h: h.kind == "ups") hardware then [ "upsmon" "UPS monitor" ] else null)
      ];
      solarWind = {
        inherit (cfg.solarWind) exposure publicUrl topics;
        enable = on "notifications";
        url = cfg.internal.solarWindUrl;
        lanUrl = "http://${cfg.internal.adminGw}:${toString cfg.solarWind.port}";
        tokenFile = cfg.internal.solarWindTokenFile;
      };
      heliosphere = {
        enabled = on "remote";
        inherit (cfg.heliosphere) publicHost;
        dir = "/var/lib/heliosphere";
        container = "heliosphere";
        wgSubnet = "10.61.0";
        ovpnSubnet = "10.62.0";
      };
      reservedNames = [ cfg.hostName "heliosphere" ];
    };
  };
}
