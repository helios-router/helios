# site.nix — everything specific to YOUR household lives here (and in state.json).
# Copy this file next to your configuration.nix and fill in the CHANGE-ME values.
# Every option is documented in modules/options.nix.
{ ... }:

{
  helios = {
    hostName = "helios";              # router name; also <hostName>.<domain>
    domain = "home.arpa";             # local DNS suffix (home.arpa is the standard for homes)

    admin = {
      user = "CHANGE-ME";             # your login on the router
      sshKeys = [
        # Hardware keys recommended: ssh-keygen -t ed25519-sk -O resident -O verify-required
        # "sk-ssh-ed25519@openssh.com AAAA... you@laptop"
      ];
    };

    # Devices, QoS and UPnP — edited by Photosphere (Cockpit), committed to git in configDir
    state = builtins.fromJSON (builtins.readFile ./state.json);
    configDir = "/etc/nixos";         # writable checkout containing this file and state.json

    interfaces = {
      trunkMac = "CHANGE-ME";         # NIC cabled to the switch trunk (`ip link`)
      rescueMac = null;               # optional spare NIC for out-of-band rescue (password SSH)
      wanVlan = 10;                   # switch VLAN the modem's port is in
    };

    # Networks (VLANs) live in state.json "networks" — edit them in Photosphere → Networks.

    # Media servers (Jellyfin, Plex, Emby…) are set per device in Photosphere:
    # Devices → edit → "Media server". They're stored in state.json.

    tailscale = {
      enable = true;
      tag = "tag:helios";             # must exist in your tailnet policy (templates/tailnet-policy.hujson)
      loginServer = null;             # your Headscale, e.g. "https://headscale.example.com" (null = Tailscale's)
    };

    # Second, DNS-filtered exit node for family devices
    heliosphere.enable = false;

    # Passkey sign-in for Photosphere (Authelia gate). Usually switched on later in
    # Photosphere → Advanced → Services, then set up under Admin account.
    gate.enable = false;

    solarWind = {
      enable = true;
      # local  = reachable from admin networks (trusted/mgmt by default) and Tailscale only
      # funnel = public at https://<hostName>.<tailnet>.ts.net via Tailscale Funnel (no domain/VPS needed)
      # proxy  = your own reverse proxy (templates/Caddyfile.solarwind) forwards to the router
      exposure = "local";
      publicUrl = null;               # e.g. "https://helios.your-tailnet.ts.net" or "https://alerts.example.com"
      iosRelay = false;               # true if you use iPhones (only message IDs go through ntfy.sh)
    };

    printing = {
      enable = false;
      drivers = [ ];                  # e.g. [ pkgs.canon-cups-ufr2 ] (add `pkgs` to the args above)
      unfreeDrivers = [ ];            # e.g. [ "canon-cups-ufr2" ]
    };

    logwatch = {
      enable = false;                 # needs a GGUF model at /var/lib/llm/model.gguf
      backend = "vulkan";             # vulkan (iGPU) | cpu | remote
      remoteUrl = null;               # llama.cpp server when backend = "remote"
    };
  };
}
