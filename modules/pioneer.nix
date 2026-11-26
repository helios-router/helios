# Pioneer: the print server (after the Pioneer plaque, the one printed page sent into space).
# CUPS print server for USB and network printers, shared to networks with
# "printing" access and announced over mDNS.
#  - Printers and free drivers: state.json "printing" (Photosphere → Printing).
#  - Proprietary drivers: site.nix (helios.printing.drivers + unfreeDrivers).
#  - Anything else: CUPS's own web interface, http://<admin gateway>:631.
{ config, lib, pkgs, ... }:

let
  cfg = config.helios;
  p = cfg.printing;
  st = { drivers = [ ]; printers = [ ]; } // (cfg.state.printing or { });
  catalog = (lib.importJSON ../pkgs/printing.json).drivers;
  nets = map cfg.internal.net (cfg.internal.where (a: a.printing));
  freeDrivers = map (id: pkgs.${catalog.${id}.attr}) (builtins.filter (id: catalog ? ${id}) st.drivers);
  default = lib.findFirst (x: x.default or false) null st.printers;
in
lib.mkIf (cfg.internal.on "printing") {
  services.printing = {
    enable = true;
    listenAddresses = [ "*:631" ];        # Heliopause limits 631 to networks with "printing" access
    allowFrom = [ "localhost" ] ++ nets;
    browsing = true;
    defaultShared = true;
    webInterface = true;
    drivers = p.drivers ++ freeDrivers;
  };
  services.ipp-usb.enable = p.ippUsb;
  services.avahi.publish = { enable = true; userServices = true; };

  # Printers added in Photosphere. NixOS creates/updates them; helios-ctl removes
  # the ones taken out of state.json after an apply (it remembers what it made).
  hardware.printers = {
    ensurePrinters = map (x: {
      inherit (x) name deviceUri model;
      description = x.description or x.name;
      location = x.location or "";
      ppdOptions = x.options or { };
    }) st.printers;
    ensureDefaultPrinter = lib.mkIf (default != null) default.name;
  };

  nixpkgs.config.allowUnfreePredicate = lib.mkIf (p.unfreeDrivers != [ ])
    (pkg: builtins.elem (lib.getName pkg) p.unfreeDrivers);
}
