# Changelog

Notable changes to HeliOS. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/). Until the first tagged release, routers
track `main` through the flake, and the commit hash is the version.

## [Unreleased]

### Security
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
- Admin account card (Photosphere → Advanced): change the admin password and turn on
  two-factor sign-in (TOTP) for Cockpit, with one-use recovery codes.
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
