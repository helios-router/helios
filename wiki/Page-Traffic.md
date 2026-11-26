# Traffic

The Traffic tab shows how much data is moving through the router: a live graph for the last three minutes, a per-network table of current speeds, and long-term totals per day and per month. It is read-only. Nothing on this tab changes any setting.

Related pages: [Overview](Page-Overview) (the WAN graph and the QoS summary), [Devices](Page-Devices), [Networks](Page-Networks).

## Choosing what to look at

The **Interface** bar at the top has one button per interface. The choice applies to the live graph and to the daily and monthly history, and is remembered until you reload the page.

| Button | Interface | What it counts |
| --- | --- | --- |
| Internet (WAN) | `wan` | Everything to and from your ISP. |
| One per network | the network's VLAN interface (for example `family`) | Traffic between that network and the router, which is what its devices send and receive. |
| Tailscale (admin) | `tailscale0` | Traffic over your tailnet. |
| Heliosphere (family exit) | `ve-heliosphere` | Traffic of the remote access container. |

The Tailscale and Heliosphere buttons are always shown. If that feature is off, the interface does not exist and the charts stay empty.

### Download and upload

Download and upload are always from the point of view of the devices. On the WAN, download is what your ISP sends you. On a network interface, "download" is what the router sends to the devices on it, and "upload" is what they send to the router. The same swap is applied to the daily and monthly totals.

## Live

| Element | Meaning |
| --- | --- |
| Header | Current download and upload speed for the selected interface. |
| Graph | The last 3 minutes, one point every 2 seconds. The vertical axis scales automatically. Hover for the speed at that moment. Labels at the right end show the latest values. Until two samples are collected it says "Collecting samples...". |

Speeds are in decimal units (1 Mb/s = 1,000,000 bits per second), calculated from the difference of the interface counters between samples. They are bits per second, not bytes. The samples are kept in your browser only, so they start empty each time you load the page. Sampling continues while you look at other Photosphere tabs, so the graph is already filled when you open this one.

## Right now, per network

A table with the current download and upload for every interface except the WAN: each network, Tailscale and Heliosphere. A dash means no sample yet. Use this to see which network is busy before opening its graph.

## Last 14 days

A bar chart of daily totals for the selected interface, one pair of bars per day (download and upload). Hover a day for the exact amounts. Data sizes use decimal units (KB, MB, GB). The title shows the interface.

If there is no data, the card says "No history for this interface yet." If the statistics service has not collected anything yet, it shows the message "vnstat has no data yet (it needs a few minutes after first start)".

## Monthly totals

A table of the last 12 months, newest first, with download and upload for the selected interface.

## Where the numbers come from

- The live graph reads the kernel's byte counters for each interface every 2 seconds (`helios-ctl counters`).
- The daily and monthly totals come from vnStat, a small statistics service that runs on the router and keeps counters across reboots (`helios-ctl vnstat`). It returns the last 14 days and 12 months for the interfaces HeliOS knows about.

## Gotchas

- The WAN figures include the router's own traffic (updates, DNS lookups, notifications) as well as your devices'.
- Traffic between two networks passes through the router, so it shows up on both networks' interfaces but not on the WAN.
- Totals are per interface, not per device. There is no per-device usage on this tab.
- Because live samples live in the browser, closing the page loses them. Daily and monthly totals are kept by the router.
- The [QoS](Page-QoS) and [UPnP](Page-UPnP) tabs next to this one show the smart queue and open ports. They are not part of this tab.

## Under the hood

- Page: `viewTraffic` (`pkgs/photosphere/photosphere.js:780`); charts `lineChart` (`:230`) and `barChart` (`:298`); sampler `sampleCounters` (`:184`, every 2 s, 90 samples kept); direction swap `dirOf` (`:180`); interface buttons `ifaceLabels` (`:24`).
- Backend: `cmd_counters` (`pkgs/helios-ctl.py:2001`) reads `/sys/class/net/<iface>/statistics/{rx,tx}_bytes`; `cmd_vnstat` (`:2011`) runs `vnstat --json`.
- The interface list HeliOS reports on is `ifaces` in `/etc/helios/config.json`, built in `modules/options.nix` (wan, your networks, Tailscale and Heliosphere when enabled, trunk0). `trunk0` is collected but has no button.
- vnStat is enabled in `modules/hubble.nix`.
