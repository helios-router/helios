# QoS tab (smart queue)

QoS keeps your connection responsive when it is busy. Without it, a big download or upload fills the queue in your modem, and every call, game and web page waits behind it ("bufferbloat"). With QoS on, HeliOS limits traffic on the WAN port to just under the speed of your line, so the waiting line forms inside the router, where it is short and fair.

The tab has two parts: live statistics (top, two panels) and the settings form (below).

QoS is **off by default** and needs both speeds before it can be turned on. Every change here is a pending change: it only takes effect after you press Apply in the [pending bar](Page-Header-and-Navigation).

## Statistics panels

Two panels, **Download** and **Upload**, refresh every 3 seconds. Download is measured on an internal mirror of the WAN's incoming traffic (`ifb4wan`), Upload on the WAN port.

If QoS is off, a panel says "QoS is off." If QoS is on in the settings but nothing is running yet, it says "Not active, apply QoS or check the helios-sqm service."

| Item | Meaning |
|---|---|
| Pill in the header | The speed cap the queue is using now, e.g. "900 Mb/s cap". |
| Avg queue delay | Average time packets waited in the queue (the "Best effort" class, or the first class if there is only one). Lower is better. |
| Peak delay | Highest wait seen in that class. |
| Dropped / marked | Total packets the queue dropped or marked to tell senders to slow down. A few is normal and healthy; QoS works by doing this. |
| Class table | Per priority class: bytes sent, average delay, drops and ECN marks (marks are a gentle "slow down" signal that does not lose the packet). |

Gotchas:

- The statistics only exist for the CAKE queue manager. If you choose **fq_codel + HTB**, QoS still works, but the panels show "Not active".
- Class names are shown by position: Bulk, Best effort, Voice & video. With "Four classes" the third class is really video and the fourth shows as "Tin 3". With "No priorities" there is one class, labelled "Bulk".

## Smart queue (QoS) card

### Enable QoS

Checkbox. Turns the feature on. It needs both speeds filled in; saving with QoS on and a missing speed fails with "set both rates before turning QoS on".

### Speeds

| Field | Notes |
|---|---|
| Download (Mb/s) | The speed your line really delivers. HeliOS shapes to exactly what you enter, so enter slightly less than your plan (about 90%) so the queue stays at the router. |
| Upload (Mb/s) | Same, about 95% of the real speed. Steps of 0.5. |

Helpers:

- **Suggest settings**: fills a suggestion panel without any speed test. It checks whether a cable modem answers at 192.168.100.1 (then it suggests the Cable link type, otherwise Ethernet / fibre) and suggests Per device fairness and CAKE.
- **Measure my connection**: asks for confirmation, then pauses QoS for about 20 seconds (it stops the QoS service, tests, then starts it again), downloads and uploads test data from Cloudflare's public speed test, and measures latency to 1.1.1.1 idle and under load. Anything else busy on the network makes the result lower. It then suggests download at 90% and upload at 95% of what it measured, turns on the ACK filter if download is at least 8 times upload, and suggests fq_codel if download is over 2000 Mb/s (CAKE can run out of CPU on small routers). It also notes if the speed is close to the trunk cable's speed, since that cable also carries your home traffic.
- The result line shows measured speeds, idle and loaded latency, and a **bufferbloat grade** without QoS: A when latency rises by under 5 ms under load, B under 30 ms, C under 60 ms, D under 200 ms, F beyond that.
- **Use these** copies the suggestions into the form. Nothing is saved until you press Save.
- **Bufferbloat test**: link to waveform.com's test in a new tab, for an independent check.

### Link type

How your modem frames packets, so the router counts the extra bytes. Pick what matches your line.

| Choice | For | CAKE keyword |
|---|---|---|
| Cable | DOCSIS cable modem | `docsis` |
| Ethernet / fibre (**default**) | Fibre ONT, Ethernet handoff, unsure | `ethernet` |
| VDSL | Phone-line VDSL/VDSL2 (PTM) | `pppoe-ptm` |
| ADSL | Older phone-line ADSL (ATM cells) | `pppoe-llcsnap` |
| Custom | Set it yourself | `overhead N mpu N [atm]` |

With **Custom**, three more fields appear: **Overhead (bytes)** (-64 to 256, default 38), **Minimum packet (MPU)** (0 to 256, default 84) and **ATM cells** (checkbox, default off). They are ignored for the other choices.

### Fairness

| Choice | Behaviour |
|---|---|
| Per device (**default**) | Each home device gets an even share, then its own connections share that. Understands NAT, so devices are told apart behind the router. |
| Balanced | CAKE's "triple isolate": shares between devices and destinations together. |
| Per connection | Every connection equal, whichever device owns it. |

### Priority

| Choice | Behaviour |
|---|---|
| Four classes | Bulk, normal, video, voice, when apps mark their traffic. |
| Three classes (**default**) | Bulk, normal, voice. CAKE's default. |
| No priorities | All traffic treated the same; fairness still applies. |

### Fine-tuning

| Field | Default | Notes |
|---|---|---|
| ACK filter (upload) | off | Drops redundant acknowledgement packets when upload is much smaller than download (cable, DSL). Little effect on symmetric lines. |
| Clear incoming priority marks | on | Ignores priority marks set by the internet on incoming traffic after classifying, so outside traffic cannot claim priority inside. This is applied on the download direction only. |
| Typical distance | Internet-wide (100 ms) | Tunes how quickly the queue reacts. Choices: Internet-wide (100 ms), Regional (30 ms), Same city (10 ms). Leave it unless nearly everything you use is close by. |
| Queue manager | CAKE (recommended) | CAKE, or "fq_codel + HTB (lighter)" for weak CPUs. fq_codel ignores Fairness, Priority, ACK filter, Distance and the clear-marks option: it shares per connection with one class. |

### Save

Saves everything in the form at once. Errors (for example a bad rate) show in a red box at the top of the card. After saving you see "QoS settings saved, apply to activate" and the pending bar lists "Change qos settings".

## What the router does after Apply

When QoS is enabled and both speeds are set, a service called `helios-sqm` runs at boot (and re-runs when any QoS setting changes):

1. Upload: on the WAN port, one CAKE (or HTB + fq_codel) queue limited to your upload speed.
2. Download: incoming WAN traffic is mirrored to a virtual interface `ifb4wan`, which gets the same kind of queue limited to your download speed.
3. If you turn QoS off, the service is removed and stopping it deletes the queues and the virtual interface.

## State and defaults

Stored in `state.json` under `qos`. Anything missing uses the default.

| Key | Default | Allowed |
|---|---|---|
| `enabled` | `false` | true/false |
| `down`, `up` | `null` | `500mbit`, `50mbit`, `500kbit`, `1gbit` style (number plus kbit, mbit or gbit) |
| `algorithm` | `cake` | `cake`, `fq_codel` |
| `link` | `ethernet` | `docsis`, `ethernet`, `vdsl`, `adsl`, `custom` |
| `overhead`, `mpu`, `atm` | 38, 84, false | custom link only |
| `fairness` | `device` | `device`, `triple`, `flow` |
| `priority` | `diffserv3` | `diffserv4`, `diffserv3`, `besteffort` |
| `ackFilter` | `false` | true/false |
| `rtt` | `internet` | `internet`, `regional`, `metro` |
| `wash` | `true` | true/false |

## Gotchas

- QoS limits only the WAN port. Traffic between your home networks is not shaped.
- If your speed varies a lot (shared cable, mobile), set the speeds a little lower than the worst you see.
- Speeds are entered in Mb/s and saved as `...mbit` (for example 1000 for a gigabit line).
- Measuring while QoS is off or not yet applied does not touch the QoS service.
- An unknown setting key is rejected (`unknown QoS setting`), so hand-edited keys in `state.json` can break saving from the form.

## Under the hood

- `pkgs/photosphere/photosphere.js:837-927`: `viewQos()` (form, suggestions, panels, 3 s refresh at line 925).
- `pkgs/helios-ctl.py:2029-2043`: `cake_stats()` (reads `tc -s -j qdisc show`, only the `cake` kind).
- `pkgs/helios-ctl.py:2046-2093`: `QOS_DEFAULTS`, `qos` and `qos-set` validation.
- `pkgs/helios-ctl.py:2095-2207`: `probe_cable_modem()`, `qos_measure()` (stops and restarts `helios-sqm.service`), `qos_recommend()`, `qos-suggest [--measure]`.
- `pkgs/helios-ctl.py:815-832`: older `settings-set --qos/--up/--down` command (the tab itself uses `qos-set`; `settings-set` does not require both speeds).
- `modules/voyager.nix:1-79`: how each key turns into `tc` commands and the `helios-sqm` unit.
- `modules/options.nix:281`: the "QoS" entry in the status service list.
- `modules/recovery.nix:30`: `helios-sqm` is watched (alert only, no automatic restart).
