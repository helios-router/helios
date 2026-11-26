# Hardware (Advanced)

This tab is shown only in Advanced mode (gear button), under the Overview tab. It is where HeliOS learns about the equipment around the router: the router's own spare Ethernet ports, switches and access points, and a USB UPS. HeliOS does not configure switches or access points itself. It tells you what to set up on them.

Related pages: [Networks](Page-Networks), [Overview](Page-Overview), [Notifications](Page-Notifications) (UPS alerts arrive there).

The tab has up to four cards: Found on your network, This router's ports, Switches and access points, and UPS (only when a UPS is set up or UPS support is turned on). The driver list and security scan are not on this tab: they are on the Advanced tab.

## How detection works

The router scans itself every 6 hours (first scan 2 minutes after boot) and when you press **Scan again**. The scan is read-only and records:

- **Ports**: every physical network interface and switch-chip port, with its MAC address, bus (PCI, USB or DSA switch chip), driver, link state and speed.
- **USB devices**: anything that is not a hub. A device from a known UPS vendor (APC, CyberPower, Eaton, Tripp Lite, Liebert, PowerCOM, Phoenixtec), or with "UPS" in its name, is classed as a UPS.
- **Neighbours (LLDP)**: switches and access points that announce themselves on the trunk, if the LLDP driver is on (it is by default). HeliOS only listens and never announces.

Each port gets a role: **trunk** (the NIC that carries all VLANs to your switch), **rescue** (the emergency port), **wan** or **mapped** (already configured by you), or **free** (available).

## Found on your network

| Item | Meaning |
| --- | --- |
| Scan again | Runs the scan now. The button shows "Scanning..." while it works. |
| "Last scan ..." | When the last scan finished. |
| Suggestions | One row per thing the scan found that is not set up yet, each with a **Set up** button. |

What can be suggested:

- **A UPS** that is plugged in and not configured yet. **Set up** asks for confirmation ("Enable UPS driver"), then turns on the UPS driver and adds the UPS (see UPS below).
- **Router ports**, if the router has free Ethernet ports and no router entry yet. **Set up** scrolls to the ports card with the free ports listed (all set to Unused) so you can choose what each does, then press Save ports.
- **A switch or access point** that announced itself over LLDP and is not yet in your list (it is called a switch if it advertises bridge or switch capability, an access point if it advertises WLAN capability). **Set up** opens the editor described below, prefilled.

If there is nothing to suggest, the card says everything detected is already set up.

## This router's ports

Use this card when the router has more Ethernet ports than the trunk and rescue ones and you want to plug devices straight into the router. The card shows nothing until a scan has run.

| Column | Meaning |
| --- | --- |
| Port | Interface name (plus "not currently present" if configured but missing). |
| MAC | Hardware address, or "shared (switch chip)" for ports of a built-in switch chip. |
| Bus - driver | PCI, USB or DSA, and the kernel driver. |
| Link | Link speed with a green tick, or "No link". |
| Mode | The port's job (below). |
| Settings | Depends on the mode. |

The trunk and rescue ports are listed with a badge and cannot be remapped.

| Mode | Settings | What it does |
| --- | --- | --- |
| Unused | none | Nothing is configured on the port. Unused ports are not saved. |
| Access | One network | Untagged port for devices on that network. |
| Trunk | "All networks" switch or a list of networks, and "Carry WAN VLAN" | Tagged VLANs, for a switch or access point connected to this port. |
| WAN | none | The port is the internet connection. Only one port may be WAN. Use this when the modem plugs straight into the router. |

**Save ports** (`hardware-set`) validates the list, checks the network names exist and that only one port is WAN, and stores it as a pending change. **Discard edits** appears while you are editing a suggestion. A yellow warning reminds you that changing a port can cut off whatever is plugged into it, and that you have a limited time to confirm after applying.

What the router does after Apply (`modules/network.nix`):

- With no access or trunk ports, the plain layout is used: one trunk NIC carries all VLANs.
- With any access or trunk port, the router creates a VLAN-aware bridge named `lan0`. The trunk NIC and those ports join it, and the network interfaces sit on the bridge.
- A NIC port is renamed to `p<mac>` by its permanent MAC address so the name survives reboots and USB re-plugs. Switch-chip ports keep their kernel name.
- If a port is set to WAN, the router's internet connection comes from that port and the WAN VLAN is no longer expected on the trunk.

## Switches and access points

For each device you describe, HeliOS writes a checklist of what to set in the device's own web page. It never connects to the device.

| Control | What it does |
| --- | --- |
| Add switch / Add access point | Opens the editor. |
| Edit | Reopens the editor. |
| Remove | Asks for confirmation, then forgets the device and its checklist. The device itself is not touched. |
| Checklist | "Set this up on the device", one line per port and SSID, such as "Port 2: access - untagged VLAN 30 (Family)". |

The editor:

| Field | Details |
| --- | --- |
| Label | Display name, up to 40 characters. |
| ID | Lowercase letters, digits and dashes, starting with a letter, up to 32. Filled in from the label until you edit it. |
| Notes | Up to 200 characters (for example, where it is and its management address). |
| Ports (Ethernet ports for an AP) | A table: port name or number (1 to 16 characters), mode (Unused, Access, Trunk, WAN), the networks for that mode, and a note (up to 100 characters). **Add port** suggests the next number. Up to 64 ports. |
| Wi-Fi networks (access points only) | A table of SSID (1 to 32 characters) and the network it maps to. Up to 16. Blank SSIDs are dropped. |

A new access point starts with one trunk uplink port carrying all networks. Switches and access points are stored in `state.json`, so they appear in the pending-changes list, but they change nothing on the router itself: they only generate the checklist.

## UPS

This card appears when a UPS is set up or the UPS driver ("UPS (Network UPS Tools)") is on.

**Per UPS:** status (On mains, On mains and charging, On battery, Low battery, or "No reading"), a battery bar and percentage, runtime left in minutes, load percentage, and model. **Remove UPS** asks for confirmation and stops monitoring it.

**Settings:**

| Field | Details |
| --- | --- |
| Shut down at charge | When on battery and the charge falls to this, the router shuts down cleanly. Default 20. |
| ...or when runtime is below | Seconds of estimated runtime. Default 180. |
| Share with other machines | Runs a NUT server on port 3493 so a NAS or server on the networks you tick can follow this UPS and shut down with it. Off by default. |

**Save UPS settings** stores them (`driver-set`) as a pending change.

What the router does after Apply (`modules/drivers.nix`): it runs Network UPS Tools (the UPS id is its NUT name), overrides the UPS's own low-battery thresholds with yours (whichever is reached first triggers shutdown), and listens on localhost, plus on the gateway address of each network you chose for sharing. The firewall opens port 3493 only on those networks. Events (on battery, back on mains, low battery, forced shutdown, communication lost or restored, replace battery) become notifications: urgent ones go to the flare topic, the rest to aurora. See [Notifications](Page-Notifications).

If the UPS driver was off, adding a UPS turns it on.

## Gotchas

- Nothing here changes the router until you press Apply, then confirm within 120 seconds.
- Mistakes on the ports card can cut you off: a port set to WAN stops being a LAN port, and the VLAN-aware bridge changes how the trunk is wired. Keep the rescue port (the emergency Ethernet port) in mind.
- Shutdown thresholds: the page accepts 1 to 100 for charge, but the backend only accepts 5 to 90 and runtime 0 to 3600 seconds, so values outside that range are rejected with an error when you save.
- The UPS runtime estimate and status come from `upsc`. If NUT is not running yet (before the first apply after adding the UPS), the card shows dashes and "No reading".
- The UPS tile on [Overview](Page-Overview) only appears once a UPS is added.

## Under the hood

- Page: `viewHardware` (`pkgs/photosphere/photosphere.js:1331`), manual editor `editManual` (`:1507`), UPS status text `upsState` (`:1287`).
- Backend: `cmd_detect` (`pkgs/helios-ctl.py:2854`), `detect_ports` (`:2740`), `detect_usb` (`:2781`), `detect_lldp` (`:2824`), `cmd_hardware` (`:3218`), `make_suggestions` (`:3118`), `cmd_hardware_set` (`:3022`), `cmd_hardware_remove` (`:3096`), `clean_ports` (`:2941`), `checklist` (`:3186`), `cmd_driver_set` (`:2863`), UPS events `cmd_ups_event` / `cmd_ups_forward` (`:3239`, `:3254`).
- State keys: `hardware[]` (each with `id`, `kind` router/switch/ap/ups, `driver`, `label`, `notes`, `ports`, `ssids`, or `match.usb` and `nutDriver` for a UPS) and `drivers.nut` (`enabled`, `shutdown.charge`, `shutdown.runtime`, `server.enabled`, `server.networks`).
- Driver catalog and UPS vendors: `pkgs/drivers.json`.
- Router side: `modules/network.nix` (ports, bridge), `modules/drivers.nix` (scan timer, LLDP, NUT, event forwarding), `modules/heliopause.nix` (port 3493).
