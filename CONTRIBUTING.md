# Contributing to HeliOS

Thanks for helping. HeliOS runs on people's home routers, so every change is
reviewed with that in mind: a bug here can expose a whole home network.

## Reporting bugs and asking questions

- **Bugs and feature requests:** open a [GitHub issue](https://github.com/helios-router/helios/issues).
  Say what hardware you run on, what you did, what you expected and what happened.
  The apply log Photosphere shows, or `journalctl` output, helps; leave out
  passwords, keys and public IP addresses.
- **Security vulnerabilities:** never in a public issue. Follow [SECURITY.md](SECURITY.md)
  (GitHub private vulnerability reporting).

## Sending a change

1. Fork the repository and create a branch from `main`.
2. Make the change, with tests (see below).
3. Run the checks you can locally, then open a pull request against `main` that
   says **what** changed and **why**. Link the issue it fixes, if any.
4. A maintainer reviews it (see [CODEOWNERS](.github/CODEOWNERS)); CI must pass before it's merged.

Keep pull requests focused: one fix or feature each. Commits should explain why,
not only what.

## What a change needs

- **Tests for new behaviour.** `helios-ctl` changes get a test in
  `tests/test_helios_ctl.py` (the tools it calls are stubbed in `tests/stubs/`).
  Firewall, networking or service changes should keep the NixOS VM test
  (`tests/vm/`) passing, and extend it when they change what's isolated from what.
  Photosphere changes must keep working in the browser test (`tests/browsers.cjs`).
- **Security fixes come with a regression test** that fails without the fix.
- **No new dependencies without a reason.** Flake inputs, fetchers and CI actions
  are checked by the PR guard (`.github/scripts/pr-guard.py`); actions are pinned to
  a commit SHA, and Python test dependencies are hash-pinned.
- **Input from the browser or the network is untrusted.** `helios-ctl` runs as
  root: validate every value before it reaches a command line, a generated config
  file or the filesystem (`re.fullmatch` or `\Z`, never a bare `$`), and pass
  commands as argument lists, never through a shell.
- **Secrets never land in the Nix store, git or logs.** Write them with mode 0600
  (see `hs_write`).

## Checks

CI (`.github/workflows/`) runs all of these on every pull request:

| Check | Locally |
|---|---|
| `helios-ctl` tests | `python tests/test_helios_ctl.py` |
| Shell lint | `shellcheck -s bash installer/helios-install.sh tests/*.sh pkgs/*.sh` |
| Nix evaluation | `nix flake check` |
| VM tests: router (DHCP, NAT, isolation), hardening (blocked devices, Log AI, remote access accounts, Cockpit two-factor sign-in) | `nix build .#vmTests.router .#vmTests.hardening` (needs KVM) |
| VM test: offline install | `nix build --impure .#vmTests.offline-install` (needs KVM) |
| Photosphere in Firefox, WebKit, Chromium | `tests/demo.sh 8765`, then `node tests/browsers.cjs …` (see `ci.yml`) |
| CodeQL, zizmor, actionlint, dependency review, PR guard | on GitHub only |

## Licence

HeliOS is GPL-3.0 ([LICENSE](LICENSE)). By contributing you agree that your
contribution is licensed under it.
