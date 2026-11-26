# Notifications

The Notifications tab sets up and tests push notifications from the router to your phone. HeliOS runs its own notification server (ntfy) on the router. Your phone subscribes to it with the free ntfy app, and the router posts messages when something needs your attention. (Inside HeliOS this feature is called Solar Wind.)

Related pages: [Overview](Page-Overview), [Hardware](Page-Hardware) (UPS alerts).

## Two topics

Messages go to one of two topics. Subscribe to both on your phone.

| Topic | Name (default) | Used for |
| --- | --- | --- |
| Flare | `flare` | Urgent things: a service that stopped or stays down, critical log events, new serious security findings, UPS trouble. Sent at high priority. |
| Aurora | `aurora` | Information: internet is back, a service recovered, a reboot is needed or about to happen, AI log summaries, a finished security scan, normal UPS events. Sent at default priority. |

The topic names can be changed in the site configuration (`helios.solarWind.topics`). The page shows the real names.

## When is the tab empty?

If notifications are switched off in the site configuration, the tab shows one card: "Notifications are off", telling you to set `helios.solarWind.enable = true` in `site.nix`. The feature is on by default. The setting is changed in the site file, not in Photosphere.

## Cards

### Recent notifications

The messages from the last 72 hours, newest first, up to 40, from both topics. Each row has a coloured pill (red flare, blue aurora), a title, the message (shortened after 220 characters) and how long ago it was sent. If there are none: "Nothing sent yet. Use Send test to try it."

The server only keeps messages for 72 hours.

### Notifications

| Item | Meaning |
| --- | --- |
| Status | "Ready" once the server's users and tokens exist, otherwise "Starting up". |
| Reachable | Who can reach the server, from the `exposure` setting: "Home network and Tailscale only" (local), "Public, through your reverse proxy" (proxy) or "Public, through Tailscale Funnel" (funnel). |
| Server | The address your phone uses: your configured public URL, or `http://<gateway of your first admin network>:2586`. |
| Topics | The two topic names. |
| Send test flare / Send test aurora | Sends a real test message to that topic immediately ("Test from <router name>"). The recent list refreshes a moment later. If the server does not accept it you see "The notification server did not accept the message. Is ntfy-sh running?". |

### Phone login

The phone gets its own read-only login. A first login named `phone` with a random password is created on first boot. You can replace it.

| Field | Details |
| --- | --- |
| Username | 3 to 32 characters: lowercase letters, digits, `-` or `_`, starting with a letter. `helios` is reserved for the router. |
| Password | 12 to 128 characters. |
| Confirm password | Must match. |
| Save | Saves the login straight away (not a pending change) and restarts the notification server. The old phone login stops working. The password is sent to the router on its input stream, not in a command line. |

The password is stored hashed for the server, and also kept in plain text in a root-only file on the router (`/var/lib/helios/solarwind-phone.json`) so **Show** can display it later. This tab refreshes every 20 seconds but pauses while you type in the login form.

### Subscribe on your phone

1. Install the **ntfy** app (Android or iOS).
2. In its Settings, Manage users, add this server with the login shown.
3. Subscribe to the two topic names on that server.

The card also shows the server address (with **Copy**), the username, and the password (hidden until you press **Show**, then **Copy** appears). If exposure is "local", a note explains that phones receive notifications only on your network or over Tailscale. To receive them anywhere, set `exposure` to `funnel` (no domain needed) or `proxy` in the site configuration.

## What sends notifications

| Event | Topic |
| --- | --- |
| Internet outage ended ("Internet is back", with how long it was down) | aurora |
| A watched service failed and automatic restarts did not help | flare |
| A watched service stayed down after repeated restarts, or cannot be restarted | flare |
| A failed service is running again | aurora |
| A new kernel needs a reboot (once per kernel), and the router is rebooting now or at the scheduled window, or the scheduled reboot was skipped | aurora |
| UPS: on battery, battery low, forced shutdown, communication lost, replace battery, monitor stopped, not reachable | flare |
| UPS: back on mains, communication restored | aurora |
| Weekly security scan finds new critical or high findings | flare |
| Weekly security scan finishes with only lower findings | aurora |
| AI log triage: critical log events (including repeated hits of the router's admin-port trap and every successful SSH login), warnings it summarises | flare or aurora |
| AI log triage paused or resumed because the router was busy | aurora |

The log-based alerts need the optional log watcher service to be on (Advanced tab, Services). The other alerts need only notifications to be on.

## Setup details

- On first boot HeliOS generates two users for the server: `helios` (write-only, used by the router itself through a token) and `phone` (read-only). Nobody else can sign up, and access is deny-all by default. The secrets are kept in `/var/lib/helios/solarwind.env`, never in the Nix store.
- The server listens on port 2586 by default. Phones on non-admin networks cannot reach it (it is treated like other admin ports), unless you use the proxy or funnel exposure.
- `iosRelay` (site option, off by default) lets iPhones get instant push through ntfy.sh. Only message ids are relayed.

## Gotchas

- Sending is best effort. If the notification server is down, a notification is dropped, except UPS events, which are queued on the router and left in the queue when sending fails.
- Changing the phone login restarts the server, so there is a short gap in delivery.
- The aurora topic description in the site options mentions backups, but the nightly backup does not send a notification.
- Test notifications do not count as pending changes and need no Apply.
- Remember to rotate the phone login if a phone is lost.

## Under the hood

- Page: `viewSolarWind` (`pkgs/photosphere/photosphere.js:1053`), login form `phoneLoginForm` (`:1030`).
- Backend: `cmd_notify_info` (`pkgs/helios-ctl.py:2546`, with `--reveal` for the password), `cmd_notify_test` (`:2554`), `cmd_notify_recent` (`:2563`), `cmd_notify_credentials` (`:2497`), `sw_setup` (`:2459`), the sender `notify` (`:2425`).
- Callers of `notify`: `cmd_uplink_check` (`:2599`), `cmd_unit_failed` / `cmd_recover` (`:1187`, `:1204`), reboot commands (`:1130-1170`), `cmd_ups_forward` (`:3254`), `cmd_vulnix_report` (`:3300`), and `pkgs/logwatch.sh`.
- Router side: `modules/solar-wind.nix` (ntfy server, setup unit, Tailscale Funnel), site options in `modules/options.nix` (`helios.solarWind.*`), alert rules in `modules/discovery.nix`.
- State is not in `state.json`: tokens and logins live in `/var/lib/helios`.
