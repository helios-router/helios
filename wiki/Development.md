# Development

## Repository layout

| Path | Contents |
|---|---|
| `modules/` | NixOS modules; `default.nix` imports them, `options.nix` declares `helios.*`. |
| `pkgs/` | `helios-ctl.py` (the backend), `photosphere/` (the Cockpit plugin: `index.html`, `photosphere.js`, `photosphere.css`, `manifest.json`), `logwatch.sh`, and the catalogs `*.json`. |
| `templates/` | `site.nix`, `state.json`, `flake.nix`, `configuration.nix`, tailnet policy, Caddyfile. |
| `installer/` | `iso.nix` (the installer ISO: boot menu with Install HeliOS and Recovery shell, `helios-mount`), `tool.nix` (`helios-install` and its pinned `flake.lock`), `offline.nix` (what the image carries to install with no network) and `helios-install.sh`. |
| `tests/` | `test_helios_ctl.py`, `fixtures/`, `stubs/` (fake system tools), `mock/base1/cockpit.js` (fake Cockpit), `demo.sh`, `build-pages.sh`, `browsers.cjs`, `shoot.js`. |
| `docs/` | Design notes: `heliosphere.md` (remote access contract), `drivers.md` (driver design and the phase 1 interface). |
| `wiki/` | Source of these wiki pages. |
| `.github/` | Workflows, `scripts/pr-guard.py`, `dependabot.yml`, `CODEOWNERS`. |
| `flake.nix` | `nixosModules.default` (the modules, plus `heliosUnstable`: the pinned `nixpkgs-unstable` input's packages, used only where stable lacks a security fix, today Authelia), the `templates.default` template, an `example` system and an `installer` system, `installer-iso` package, and `checks`. |

## Architecture in one paragraph

Nix modules read `config.helios` (from `site.nix`) and the parsed `state.json`. `options.nix` resolves networks and service switches into `config.helios.internal`, which every module shares, and writes `/etc/helios/config.json`. `helios-ctl.py` is a single Python file (standard library plus `bcrypt` for ntfy password hashes) that reads that JSON and `state.json`, and prints JSON. Photosphere is plain JavaScript that calls `helios-ctl` through Cockpit's `cockpit.spawn` with superuser rights. See [How It Works](How-It-Works).

## Running the tests

### Backend

```
python3 tests/test_helios_ctl.py
```

It needs Python 3 and `bcrypt` (CI installs `bcrypt==5.0.0`). The tests run `helios-ctl` for real against:

* `tests/stubs/`, fake `nft`, `ip`, `tc`, `systemctl`, `nixos-rebuild`, `tailscale`, `wg`, `lp*` and friends, put first on `PATH`;
* a mock AdGuard API and a mock ntfy server on localhost;
* a throwaway config directory and fake root built from `tests/fixtures/`.

`helios-ctl` honours environment overrides for this: `HELIOS_CONFIG`, `HELIOS_DATA_DIR`, `HELIOS_REBUILD`, `HELIOS_FAKE_ROOT`, `HELIOS_NO_SYSTEMD` (run apply in-process), `HELIOS_UPS_SPOOL`. They are for tests; do not set them on a router.

Also run ShellCheck as CI does:

```
shellcheck -s bash installer/helios-install.sh tests/*.sh pkgs/*.sh
```

### Nix

```
nix flake check --print-build-logs
```

Builds the `example` system (the site template with placeholders, which exercises the firewall and every module) and the `installer` system. Build the ISO with `nix build --impure .#installer-iso`.

### VM tests

```
nix build .#vmTests.router .#vmTests.hardening .#vmTests.passkey -L
```

NixOS VM tests (need KVM): `router` (DHCP, NAT, isolation between networks), `hardening` (blocked devices, Log AI, remote access accounts, Cockpit two-factor sign-in, the sign-in gate) and `passkey` (a client on the trusted network registers a real WebAuthn passkey at the sign-in gate with headless Chromium's virtual authenticator, then signs in with it alone and reaches Cockpit). `nix build --impure .#vmTests.offline-install` installs from the offline image. To debug one interactively: `nix build .#vmTests.passkey.driverInteractive && ./result/bin/nixos-test-driver`.

### Browsers

`tests/browsers.cjs` is the cross-browser smoke test CI runs, using Playwright against the demo. For each engine, at phone (390 by 844) and desktop (1366 by 900) sizes it checks that every page renders without JavaScript errors, that the page dropdown shows and navigates on phones, that tab rows show on desktops, that the header title does not overlap, and that nothing scrolls sideways. It writes a screenshot of every page.

```
tests/demo.sh 8765 &
NODE_PATH=<dir with node_modules/playwright> node tests/browsers.cjs \
  http://127.0.0.1:8765/photosphere/index.html shots firefox,webkit,chromium
```

CI installs Playwright 1.65.1 into a temporary directory with `npm install --no-save --ignore-scripts`, then `playwright install --with-deps firefox webkit chromium`. Engines default to `firefox,webkit` when the last argument is omitted.

`tests/shoot.js` is an older screenshot script using puppeteer-core and `/usr/bin/google-chrome` (with `--no-sandbox`), reading from `WEBROOT` (default `/tmp/heliosweb`) and writing to its first argument.

## The demo

`tests/demo.sh [port] [--lan]` serves the real Photosphere files against `tests/mock/base1/cockpit.js`, a stand-in for Cockpit that answers `helios-ctl` calls with stateful sample data. It also symlinks the real catalogs (`networks`, `profiles`, `services`, `logwatch-models`, `drivers`). Open `http://127.0.0.1:<port>/photosphere/index.html`. Nothing touches a router. Responses are never cached, so edits show on reload. `--lan` binds all interfaces so you can open it on a phone; it then shows the fake data to your whole network while it runs. Advanced tabs appear after the gear button is pressed.

`tests/build-pages.sh <out-dir>` builds the same demo as static files (real copies, not symlinks), adds a banner saying it is sample data, a landing page that redirects to the demo, and `.nojekyll`. The `gh-pages` branch holds exactly this output, and GitHub Pages serves it. To refresh:

```
tests/build-pages.sh /tmp/pages
cd <gh-pages worktree>
rsync -a --delete --exclude .git /tmp/pages/ ./
```

then commit and push the branch. Do not edit `gh-pages` by hand.

## Calling helios-ctl by hand

On a router: `sudo helios-ctl <command>`; every command prints one line of JSON with `"ok": true` or an error. Examples: `status`, `devices`, `networks`, `pending`, `apply-start`, `apply-status`, `confirm`, `rollback`, `history`, `qos`, `upnp`, `hardware`, `remote`, `profiles`, `services`, `notify-info`, `account`.

| Group | Commands |
|---|---|
| Overview | `status`, `counters`, `vnstat`, `history`, `backup`, `uplink-check` |
| Devices and networks | `devices`, `device-set`, `device-remove`, `block`, `restrict-unnamed`, `networks`, `network-set`, `network-remove` |
| Change flow | `pending`, `discard`, `restore`, `apply-start`, `apply-run`, `apply-status`, `apply-dismiss`, `confirm`, `rollback`, `settings-set` |
| Filtering | `profiles`, `profile-set`, `profile-remove`, `adguard-sync` |
| WAN | `qos`, `qos-set`, `qos-suggest`, `upnp`, `upnp-block`, `upnp-unblock`, `upnp-close`, `upnp-clear` |
| Notifications | `notify-setup`, `notify-credentials`, `notify-info`, `notify-test`, `notify-recent` |
| Hardware | `detect`, `hardware`, `hardware-set`, `hardware-remove`, `driver-set`, `ups-event`, `ups-forward`, `vulnix-report` |
| Remote access | `remote`, `remote-set`, `remote-wg-*`, `remote-ovpn-*`, `remote-proxy-user*`, `remote-egress-*` (see `docs/heliosphere.md`) |
| Services and printing | `services`, `service-set`, `printing`, `printer-set`, `printer-remove`, `printer-test`, `printer-pause`, `printing-drivers`, `printer-models`, `print-cancel` |
| Log triage | `logwatch`, `logwatch-set`, `logwatch-reset`, `logwatch-model-get`, `logwatch-model-remove`, `logwatch-guard`, `logwatch-resume` |
| Maintenance and recovery | `reboot-status`, `reboot`, `maintenance-set`, `reboot-check`, `reboot-window`, `unit-failed`, `recover` |

Commands that only read or touch runtime state skip the git bookkeeping; those that edit `state.json` take a lock and leave the change pending until applied.

## Adding things

### A filtering profile

Add an object to `defaults` in `pkgs/profiles.json` with `name`, `label`, `desc`, `global`, `filtering`, `safeBrowsing`, `parental`, `safeSearch`, `youtubeRestricted` and `blockedServices`. `helios-ctl` turns a profile into AdGuard client settings (`profile_settings`). New settings need support there and in the Photosphere Filtering page. A site's own `profiles` list in `state.json` replaces the defaults once edited.

### A driver

1. Add the driver to `drivers` in `pkgs/drivers.json`: `label`, `builtin`, `default`, `always`, `kinds`, `capabilities`, `desc`. Vendor fingerprints such as `upsVendors` live in the same file.
2. Put the system side in `modules/rosetta.nix`, switched with `I.driverOn "<name>"`. Reuse the `sandbox` helper there for services (dynamic user, read-only system, no capabilities).
3. If it needs settings or devices, extend `driver_state`, `hardware`, `hardware-set` and their validation in `helios-ctl.py`, and add a detection step if it detects hardware.
4. Add a stub under `tests/stubs/` if it calls a new tool, and tests in `tests/test_helios_ctl.py`.
5. Never store credentials in `state.json`. `docs/drivers.md` explains the principles (detect automatically, install only with approval, every push is a plan first).

### A service you can switch on and off

1. Add it to `services` in `pkgs/services.json` (group `default` or `optional`, label, size, `tab`, `units`, description).
2. Add its default to `serviceDefaults` in `modules/options.nix`. `cfg.internal.on "<id>"` takes the site default from there.
3. Make the module conditional with `lib.mkIf (cfg.internal.on "<id>")`.
4. Add its unit to the `services` list in `options.nix` so it shows on the Overview, and to `watched` in `modules/recovery.nix` if it should be restarted and reported.
5. If it has a Photosphere page that should hide when off, give the tab link `data-svc="<id>"` in `index.html`.

### A network role or access flag

Roles and flags live in `pkgs/networks.json`. A new flag needs an entry in `access`, a value in every role, rules in `modules/heliopause.nix` (and wherever else it acts), and an entry in the access list in `photosphere.js`. `helios-ctl` takes its list of valid flags from the catalog.

### A Photosphere page

Add a tab link in `index.html`, a `view<Name>(root)` function in `photosphere.js`, and register it in the `VIEWS` table. Pages talk to the backend only through `api("<command>", ...)`. Advanced pages go in `ADV_TABS`. Extend `tests/mock/base1/cockpit.js` so the demo and browser tests render the page.

## Keeping the public and private split

HeliOS is public; your router is not. Nothing household-specific belongs in `modules/`, `pkgs/` or anywhere else in this repository.

* Names, addresses, MACs, users, SSH keys and keys of any kind come only from `site.nix` and `state.json`. `.gitignore` lists `/site.nix`, `/state.json` and `/hardware-configuration.nix` at the top level so they are not committed by accident.
* Modules must not know a network by name. Everything is generated from networks and their access flags; the default network names appear only in `pkgs/networks.json`, the templates, and comments or messages.
* Examples in code, tests and docs use generic values: `example.com`, RFC 5737 addresses, `home.arpa`, made-up MACs and planet names.
* Secrets stay under `/var/lib/...` on the router and never go into `state.json`, git or the Nix store.
* Keep your own site configuration in a separate private repository, and consume HeliOS as a flake input or a checkout.
* Before opening a pull request, search your diff for your own host names, addresses and keys. The PR guard flags new URLs and sensitive paths but cannot know what is private to you.

## Contributing checklist

* `python3 tests/test_helios_ctl.py` passes and ShellCheck is clean.
* `nix flake check` passes.
* For UI changes, the demo renders every page, in light and dark themes and at phone width.
* Sensitive paths (see [Updates and Security](Updates-and-Security)) will get extra review; say in the description why they change.
* Add or update tests and, if behaviour changes, the relevant wiki page.
