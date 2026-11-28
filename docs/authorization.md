# Who can run what: helios-ctl authorization

`helios-ctl` (`pkgs/helios-ctl.py`) is HeliOS's privileged backend. This page records
every way it gets run, as which account, and what stops anyone else. Checked in the
November 2026 audit; re-check it when you add a command or a caller. Line numbers
are as of that audit. Search for the command name if they've moved.

## The boundary

- **Photosphere** runs it as `cockpit.spawn([helios-ctl, …], { superuser: "require" })`
  (`pkgs/photosphere/photosphere.js`, `api()` / `apiInput()`). "require" means Cockpit
  must already hold administrative access for the session. If it doesn't, the call
  fails with `access-denied` and the page shows its "no access" message. Secrets
  (passwords, codes, keys, uploaded configs) go on stdin, never argv.
- **Cockpit gets root through the system's sudo.** `security.sudo.execWheelOnly = true`
  (`modules/airlock.nix`), the admin account is the only member of `wheel`, and no
  module adds sudo or polkit rules. A Cockpit (or SSH) user outside wheel can't get
  root, so can't run `helios-ctl` as root.
- **`helios-ctl` itself has no caller check.** It's on everyone's PATH
  (`environment.systemPackages`). Run by an ordinary account, it has that account's
  rights only. Everything it changes is root's (`/var/lib/helios` 0750, the TOTP
  folder 0700, `/var/lib/heliosphere/*` 0700, `/etc/shadow`, the gate folder
  root:caddy 0750, `/etc/nixos`), so every privileged command fails with a permission
  error before it changes anything. What it does reach first:
  - `remote-wg-rotate` runs `wg genkey` (harmless).
  - `recover` and `reboot` ask `systemctl`, which asks polkit. Polkit refuses a
    non-admin without an active local session. Since this audit, `reboot` reports
    the refusal instead of claiming success.
  - The VM test `tests/vm/hardening.nix` ("an account outside wheel changes
    nothing") runs the privileged commands as a non-wheel account. It checks that
    shadow, keys, the gate flag, TOTP, egress files and the boot id are unchanged.
- **`NO_GIT` is not authorization.** It only skips `git_ready()` (initialising
  `/etc/nixos` as a repository) for commands that don't change `state.json`.

## Commands that change credentials

| Command | Needs the current admin password or code? |
|---|---|
| `account-password` | current password (checked against `/etc/shadow`) |
| `account-2fa-begin` / `-disable` | the current code (or a recovery code), once two-factor is on |
| `account-2fa-enable` | a code from the new, pending secret |
| `gate-sync`, `gate-enforce`, `gate-dns-token`, `gate-totp-begin`, `gate-totp-remove`, `gate-passkey-remove` | current password |
| `gate-off` | **nothing**: it's the recovery path from SSH, the console or the installer's shell, so root is the requirement |
| `notify-credentials`, `notify-info --reveal`, `remote-wg-rotate` / `-import` / `-renew`, `remote-proxy-user`, `remote-ovpn-add` / `-revoke` | nothing beyond the Cockpit session's administrative access |

The last row is a deliberate choice: those secrets belong to devices (phones,
VPN clients), not to the admin, and the Cockpit session already proved who you are.
Re-asking for the password there would be hardening, not a missing control.

## Callers that aren't Photosphere

| Command | Run by | As |
|---|---|---|
| `uplink-check` | `helios-uplink.service` (timer) | root |
| `backup` | `helios-backup.service` (timer) | root |
| `notify-setup` | `helios-solarwind-setup.service` | root |
| `reboot-window`, `reboot-check`, `recover` | `modules/recovery.nix` timers | root |
| `unit-failed <unit>` | `helios-unit-failed@.service` (OnFailure) | root; the unit name is validated (`UNIT_RE`) |
| `logwatch-guard --check` | logwatch / llama-cpp `ExecCondition`, with `+` | root |
| `logwatch-guard` | `helios-ai-guard.service` | root |
| `vulnix-report` | vulnix's `ExecStartPost`, with `+` | root (reads the report the sandboxed scan wrote) |
| `detect` | `helios-detect.service` | DynamicUser, sandboxed; writes only its StateDirectory |
| `ups-event` | upsmon's NOTIFYCMD | the NUT monitor account; writes a JSON file to its own spool, which root's `ups-forward` sends as a notification |
| `apply-run`, `logwatch-model-fetch`, `rollback --auto` | transient units started by `apply-start`, `logwatch-model-get`, the apply timer | root |

The account that runs `ups-event` controls notification text, and root follows links
when it reads that spool (SECURITY_AUDIT.md HA-16, open).

## Every command

PS = from Photosphere (root, through Cockpit's administrative access). "files" = what
it writes. Every command that writes is stopped for non-admins by root-only files.

| Command | Caller | Input (validation) | Writes |
|---|---|---|---|
| status, devices, networks, pending, apply-status, counters, vnstat, qos, upnp, history, hardware, reboot-status, logwatch, services, printing, profiles, remote, account, gate, gate-code, gate-ca, notify-recent | PS | none | nothing |
| device-set, network-set, qos-set, settings-set, upnp-block/-unblock, driver-set, hardware-set, maintenance-set, logwatch-set, printer-set, printing-drivers, profile-set, remote-set | PS | JSON (each has its own validation: MAC/NAME/VLAN regexes, catalogs, ranges) | state.json |
| device-remove, network-remove, hardware-remove, printer-remove, profile-remove, remote-wg-remove, remote-proxy-user-remove | PS | a name, looked up | state.json (and Heliosphere files) |
| block | PS | MAC (looked up), on/off | state.json, live nft sets |
| restrict-unnamed, service-set, logwatch-reset | PS | on/off/default, catalog id | state.json |
| discard, restore | PS | restore: a commit id (hex) | state.json |
| apply-start, confirm, rollback, apply-dismiss | PS | none | starts nixos-rebuild; git; apply state |
| adguard-sync | PS | none | AdGuard clients |
| upnp-clear, upnp-close | PS | protocol, port | live UPnP mappings |
| qos-suggest | PS | --measure | nothing |
| backup | PS, timer | none | /etc/nixos backups, git push |
| reboot | PS | none | reboots (refused mid-apply) |
| detect | PS (root), timer (DynamicUser) | none | hardware.json |
| logwatch-model-get / -remove, logwatch-resume | PS | catalog preset; file name (no `/`, `.gguf`) | model files, pause file |
| printer-test, printer-pause, print-cancel, printer-models | PS | printer name / job id (no leading `-`) | CUPS |
| remote-wg-add/-renew, remote-wg-rotate/-import, remote-ovpn-add/-revoke, remote-proxy-user, remote-egress-upload/-clear | PS | names (`NAME_RE`); stdin keys, passwords, configs (sanitised, fuzzed) | /var/lib/heliosphere |
| notify-credentials, notify-info, notify-test | PS | user (regex), stdin password | Solar Wind credentials |
| account-*, gate-* | PS (gate-off: CLI only) | stdin JSON with the current password or code (see above) | shadow, TOTP, Authelia, gate flag, DNS token |
