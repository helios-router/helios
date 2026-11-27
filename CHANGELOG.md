# Changelog

Notable changes to HeliOS. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/). Until the first tagged release, routers
track `main` through the flake, and the commit hash is the version.

## [Unreleased]

### Security
- Heliopause checks source addresses before connection tracking. Conntrack
  matches by addresses and ports, not by interface, so a device on a non-admin
  network could inject packets into another network's open connections. Non-admin
  networks now also get IPv6 source checks (from the routing table, so SLAAC
  prefixes keep working), and the rescue subnet is only accepted on the rescue port.
- Changing a network's access now affects connections that are already open.
  Replies still pass, but the side that opened a connection is checked against the
  rules on every packet. Removing admin access ends open SSH and Cockpit sessions,
  removing a reach permission cuts the flows it allowed, and removing a port
  forward stops its traffic.
- WAN port forwards are only accepted to UPnP networks and to the remote-access
  container's VPN ports. A stray DNAT rule can no longer open an admin network.
- With the sign-in gate on, only Caddy (and root) can connect to Cockpit's
  localhost port. Before, any local service account could skip the gate, or claim
  to be on the rescue port through `X-Forwarded-For` and skip the TOTP code.
- helios-ctl hands private files (Authelia's users file, Heliosphere secrets) to
  their service account through the file descriptor. Before, the account owning
  the folder could swap in a symlink and get root to chown any file, such as
  `/etc/shadow`. `write_json` now uses a fresh temp file instead of a fixed
  `.tmp` name that a service could plant as a link. `restore` takes only a commit
  id, and printer names and job ids can't start with `-`.
- Pausing the Log AI now really stops its timer runs. The pause check runs with
  full privileges, so it can read the pause file.
- The PR guard blocks file names that git has to quote. Before, those files
  skipped every line check, so a `fetchurl` or a download piped into a shell in a file with a
  non-ASCII name passed.
- Pausing a device now also cuts it off by MAC, so it can't keep its internet
  over IPv6. DoH and WARP blocks cover IPv6 too, and IPv6 from the remote-access
  container is dropped. (#4)
- Uploaded WireGuard and OpenVPN egress configs can no longer smuggle in
  `PostUp`/`up` hooks through comments, quoting or `--` prefixes. (#4)
- Fixed shell injection through the egress proxy host (IPv6 scope IDs), symlink
  writes from the container into host files, and injection through UPS labels and
  the logwatch model path. Validation regexes no longer accept a trailing
  newline. (#4)
- An apply that fails after the new system generation became active now gets the
  confirm-or-roll-back window instead of staying in place. (#4)
- Heliosphere and model files are no longer world-readable. (#3)
- Fixed path traversal and tab-dispatch issues found by code scanning; CI
  dependencies are pinned. (#2)

### Added
- Passkey sign-in (optional `gate` service): an Authelia gate in front of
  Cockpit. Passkeys are the preferred second factor (a key with a PIN gets past
  the gate alone, so the admin password is typed once); a code is the fallback;
  the rescue port skips it. Set up in Photosphere → Advanced; the installer
  offers it and recommends passkeys. Authelia comes from a pinned
  `nixpkgs-unstable` flake input while stable lacks a security fix.
  Optionally the sign-in page gets its own name (`helios.gate.domain`) and a
  Let's Encrypt certificate through Cloudflare or deSEC DNS, so passkeys work
  without installing the router's certificate; the router's own certificate
  stays as the fallback.
- Admin account card (Photosphere → Advanced): change the admin password and turn on
  two-factor sign-in (TOTP) for Cockpit, with one-use recovery codes. Changes
  need the current password or code. The hardening VM test signs in through
  Cockpit's PAM service.
- Installer boot menu: **Install HeliOS** starts the install wizard directly;
  **Recovery shell** boots to a root shell with `helios-mount` for repairing an
  installed router.
- Offline installer ISO. Backups are redacted. (#7)
- Fuzzing of `helios-ctl`'s upload parsers and settings validators
  (ClusterFuzzLite). (#6)
- CodeQL code-quality rules alongside the security ones. (#5)
- A NixOS VM test of the router: DHCP, NAT and isolation between networks.
- Hubble: a hardware-agnostic watchdog with a softdog fallback.

### Changed
- One installer image: the netinstall image is gone. The offline image installs with
  no network as before and, when it finds internet, offers to download updates
  (newest HeliOS and NixOS) during the install.

### Fixed
- miniupnpd boot race.
- CodeQL code-quality alerts: files closed properly, documented ignored errors,
  unused code removed. (#8)

[Unreleased]: https://github.com/helios-router/helios/commits/main
