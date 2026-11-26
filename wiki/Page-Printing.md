# Printing tab

An optional print server (CUPS) on the router. It shares USB and network printers with the networks you choose, and announces them so phones, tablets and computers find them on their own (AirPrint / IPP). This is an **advanced** tab (see [Header and navigation](Page-Header-and-Navigation)), and it only appears when the Print server service is on.

## If the print server is off

The tab shows a "Print server" card with a **Turn on** button ("Optional extra, medium download. Also under Advanced, Services."). Turning it on is a pending change; the software is downloaded and started at the next Apply. Until then the card reads "Not started yet: it's turned on and starts when you apply the pending changes." Turning the service off later (on [Advanced](Page-Advanced)) removes it at the next apply.

Whether it starts on is set in `site.nix` (`helios.printing.enable`); the Advanced switch overrides it.

## Print server card

| Item | Meaning |
|---|---|
| Status | **Running** (green) or **Not running** (red). Based on whether CUPS answers. |
| Text | "Shared with *Family, Guests*. Phones, tablets and computers there find the printers on their own." If no network has the Printing access option: "No network has printing access yet (Networks, access)." Set that per network on [Networks](Page-Networks). |
| **Open CUPS** | Opens CUPS's own web page at `http://<admin network gateway>:631/` in a new tab. It has every advanced option (paper, trays, quality, access lists) and asks for a router login. Only shown if an admin network exists. |

## Printers card

Button: **Add printer**. Empty text: "No printers yet. Plug one in by USB or connect it to your network, then add it below."

| Column | Meaning |
|---|---|
| Printer | The name people see (description), a **default** badge if it is the default printer, the queue name in small type, and the location if set. |
| Driver | "Driverless" or the driver model (shortened, hover for the full name), and under it the connection type (`usb`, `ipp`, `socket`, ...). |
| Status | **Ready**, **Printing** or **Paused**, plus "N in queue". "Not set up yet: apply" when the printer is saved but has not been created in CUPS yet; a dash when CUPS is not running. |

Row buttons:

| Button | What it does |
|---|---|
| **Test page** | Sends HeliOS's test page right away. Disabled until the printer exists in CUPS (that is, after Apply). |
| **Pause** / **Resume** | Disables or enables the queue in CUPS right away. A paused queue still accepts jobs, but nothing prints. Not a pending change. |
| **Edit** | Opens the printer dialog. |

### Add or edit printer dialog

| Field | Notes |
|---|---|
| Name people see | The description, up to 127 characters. Defaults to the queue name if empty. |
| Queue name | Letters, digits, `-` and `_`, up to 64, no spaces. Must be unique. When adding from a discovered printer it is filled in from its name. Renaming an existing printer is allowed. |
| Location | Free text, up to 127 characters, optional. |
| Connection | A drop-down of detected printers (found on the network, plus printers you already added), and a text box for the address. Accepted prefixes: `usb://`, `ipp://`, `ipps://`, `dnssd://`, `socket://`, `lpd://`, `http://`, `https://`, `hp://`, `smb://`. Examples: `ipp://printer.example.com/ipp/print`, `socket://10.20.0.50`. |
| Driver | A search box over the installed driver list (plus the "Driverless (IPP Everywhere)" choice, always first). The selected driver is shown below the list. Printers found as driverless are preselected as driverless. |
| Default printer | Makes this the default. Only one printer can be the default; choosing it here clears the flag on the others. The first printer you add is default by default. |

Buttons: **Add printer** / **Save**, **Cancel**, and for existing printers **Remove printer** (confirm: "The printer disappears from every device when you apply.").

Saving, adding and removing are pending changes. When you apply, the printers are created or updated in CUPS. Printers you remove are deleted from CUPS right after the apply (and after a rollback), because the configuration tool itself only creates and updates them.

If the driver search cannot find your model, enable a driver package in the Drivers card below, apply, then search again.

## Found on your network card

Lists connected USB printers and network printers the print server can see that you have not added yet. Each row has a **driverless** badge when the printer speaks IPP Everywhere, the address, and an **Add** button that opens the add dialog pre-filled. **Look again** reloads the list.

Empty text: "Nothing new. USB printers show up when plugged in; network printers when they're on and announce themselves." (or "CUPS isn't running."). Printers already added are not listed.

## Print queue card

Shows jobs waiting: job id (`queue-12`), printer, who sent it, size, when, and a **Cancel** button (immediate). Empty text: "Nothing waiting."

## Drivers card

Most printers made in the last ten years need no driver: use "Driverless". For older printers, switch on a driver package. Each is a free-software driver collection.

| Driver | For |
|---|---|
| Gutenprint | Hundreds of inkjets and photo printers: Canon, Epson, HP, Lexmark and more. |
| HP (HPLIP) | HP inkjets and LaserJets that do not print driverless. |
| Brother lasers | Brother monochrome laser printers (HL-, DCP-, MFC- series). |
| foo2zjs | ZjStream and similar lasers: older HP LaserJet, Minolta, Samsung, Oki, Xerox. |
| SpliX | Samsung and Xerox SPL laser printers. |
| Epson ESC/P-R | Most Epson inkjets. |
| Epson ESC/P-R 2 | Newer Epson inkjets and EcoTank models. |

**Save drivers** stores the list (pending: "Printer drivers: +brlaser"); the packages are installed at Apply. A line "Also installed from site.nix" lists drivers added in `site.nix` by package name. Proprietary drivers can only be added in `site.nix` (`helios.printing.drivers` and `unfreeDrivers`).

## Networks and the firewall

Printing is shared only with networks that have the Printing access option. The router allows TCP port 631 from those networks (not the admin network, which already has access to everything). Discovery across networks needs the mDNS access option on the network too (see [Networks](Page-Networks)). The router announces printers over mDNS (Avahi). Driverless USB printers use `ipp-usb` (on by default, `helios.printing.ippUsb`).

## State

```json
"printing": {
  "drivers": ["brlaser"],
  "printers": [
    { "name": "office", "description": "Office laser", "location": "Upstairs",
      "deviceUri": "ipp://printer.example.com/ipp/print", "model": "everywhere", "default": true }
  ]
}
```

## Gotchas

- Test page, Pause/Resume and Cancel only work for printers that exist in CUPS, so add the printer, Apply, then use them. The message is "no printer ... (apply first if you just added it)".
- Queue names must be unique and cannot contain spaces. Nix refuses to build if two printers are default or a name is invalid.
- The default flag is stored on at most one printer.
- The driver search shows up to about 150 matches; type more of the model to narrow down.

## Under the hood

- `pkgs/photosphere/photosphere.js:2106-2199`: `viewPrinting()` and `editPrinter()`.
- `pkgs/helios-ctl.py:1561-1632`: printer names/URI regexes, `cups_status()` (`lpstat`), `discover_printers()` (`lpinfo -v`, `driverless`).
- `pkgs/helios-ctl.py:1634-1772`: `printing`, `printer-models` (`lpinfo -m`), `printer-set`, `printer-remove`, `printing-drivers`, `printer-test` (`lp`), `printer-pause` (`cupsdisable/cupsenable`), `print-cancel`, `printers_cleanup()` (`lpadmin -x`).
- `pkgs/helios-ctl.py:1774-1783`: `describe_printing()` (pending bar text).
- `pkgs/printing.json`: driver catalog (`attr` is the nixpkgs package).
- `modules/printing.nix`: CUPS, `ipp-usb`, Avahi publishing, `hardware.printers.ensurePrinters`.
- `modules/heliopause.nix:128`: TCP 631 for networks with printing access. `modules/network.nix:177-190`: mDNS reflection.
- `modules/options.nix:154-159, 189-191`: `helios.printing.*` options and the printer name / single-default assertion.
- `pkgs/services.json`: the "Print server" switch (`printing`, unit `cups`).
