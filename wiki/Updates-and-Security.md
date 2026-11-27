# Updates and Security

## Updating a router

Nothing updates itself: the modules do not enable automatic upgrades. You choose when.

| Setup | How |
|---|---|
| Flake (installer or template) | In `/etc/nixos`: `nix flake update helios`, then `sudo nixos-rebuild switch`. |
| Non-flake | Update the `helios/` checkout in `/etc/nixos` (for example `git pull`), then `sudo nixos-rebuild switch`. |

Notes:

* The Photosphere Apply button only works while `state.json` has a pending change; with none, `helios-ctl apply-start` answers "nothing to apply". After a flake update, run `nixos-rebuild switch` yourself. That path has no 120 second auto-rollback; the previous generation stays in the boot menu and `nixos-rebuild switch --rollback` returns to it.
* Changes made in Photosphere are applied with confirm-or-rollback; see [How It Works](How-It-Works).
* The router's nixpkgs follows `nixos-25.11` through your own `flake.lock` (the template sets `helios.inputs.nixpkgs.follows = "nixpkgs"`).
* Most updates take effect at once. A new kernel needs a reboot: `helios-reboot-check` runs daily and notifies once, [Overview](Page-Overview) shows a banner, and the optional reboot window on the [Advanced](Page-Advanced) page reboots at a time you choose, only if needed, never during an apply.
* A weekly vulnerability scan (`vulnix`, against the NVD) covers everything in the running system; new findings are sent as a notification and listed in the Security scan card on the [Advanced](Page-Advanced) page. Updating HeliOS usually clears them.

## How the repository is kept current

### Weekly flake.lock pull request

`.github/workflows/update-flake-lock.yml` runs Mondays at 06:17 UTC (and on demand). It runs Determinate Systems' update-flake-lock action and opens a pull request titled "flake.lock: update nixpkgs" with the `dependencies` label. CI must pass before it is merged; routers then pick it up on their next `nix flake update helios`.

PRs opened with the default `GITHUB_TOKEN` do not trigger CI. The workflow therefore uses a repository secret `FLAKE_UPDATE_TOKEN` when it exists (a fine-grained token for this repository only, with contents and pull-requests write) and falls back to the default token.

### Dependabot

`.github/dependabot.yml` updates GitHub Actions weekly, grouped into one pull request, and waits 7 days after a release before proposing it. Nix inputs are not covered by Dependabot; the workflow above handles them. All actions are pinned to full commit SHAs.

## CI jobs

`.github/workflows/ci.yml` runs on pushes to `main`, on pull requests and on demand, with read-only token permissions. A new push cancels the previous run of the same ref.

| Job | What it does |
|---|---|
| `nix flake check` | Builds the example system (the site template with placeholders) and the installer system. |
| `helios-ctl tests and lint` | Python 3.13: runs `tests/test_helios_ctl.py`, then ShellCheck on `installer/helios-install.sh`, `tests/*.sh` and `pkgs/*.sh`. |
| `Photosphere in Firefox, WebKit, Chromium` | Starts the demo (`tests/demo.sh`) and runs `tests/browsers.cjs` with Playwright on phone and desktop sizes; uploads screenshots if it fails. |
| `installer ISO` | Not on pull requests; after `nix flake check`, builds the installer ISO and uploads it as the `helios-installer-iso` artifact for 14 days. |

Other scans:

| Workflow | Triggers | What it does |
|---|---|---|
| CodeQL | push to `main`, pull requests, Wednesdays 04:41 UTC | Analyses Python, JavaScript/TypeScript and Actions with the `security-extended` queries. |
| Scorecard | push to `main`, Saturdays 05:23 UTC | OpenSSF Scorecard (branch protection, pinned actions, token permissions and more); results are published and uploaded to code scanning. |

## The pull request guard

`.github/workflows/pr-guard.yml` runs on every pull request with a read-only token, no secrets, and the `pull_request` trigger (never `pull_request_target`). Three jobs:

| Job | Checks |
|---|---|
| supply-chain checks | Runs `.github/scripts/pr-guard.py` on the diff. The script comes from the base branch, so a pull request cannot weaken the check that reviews it. It reads the git diff only and never runs the PR's code. |
| dependency-review | GitHub's dependency review; fails on moderate severity or higher. |
| workflow security | zizmor (medium severity and up) and actionlint on the workflows. |

The guard reports in the job summary and fails only on findings it considers to have almost no honest use here.

### What blocks (the check fails)

* bidirectional control characters (Trojan Source) or invisible and zero-width characters in an added line;
* a download piped into a shell (output of curl or wget fed straight into a shell, including process substitution);
* a base64-looking blob of 200 or more characters (not in `.lock` files), a run of 12 or more `\x` escapes, a decode-then-execute call (`eval` or `exec` of base64, zlib, hex and similar), creating functions from strings, or `document.write` calls;
* a very long line (over 300 characters) with almost no spaces, outside `.lock`, `.md`, `.svg` and `.json` files;
* an added symlink; a binary file (PNG, JPEG and WebP images directly in `docs/`, `branding/` or `pkgs/photosphere/` are allowed and listed for review); binary content in a text file; deleting the guard script;
* a flake input in `flake.nix` from outside `github:NixOS/`, `https://github.com/NixOS/` and `cache`, `channels` or `releases` on `nixos.org`.

### What is listed for human review (the check passes)

* changes to sensitive paths: `.github/`, `flake.nix`, `flake.lock`, `pkgs/*.json` catalogs, `installer/`, `modules/airlock.nix`, `modules/heliopause.nix`, `modules/recovery.nix`, `CODEOWNERS`, `SECURITY.md`; and changes to the guard itself;
* a file made executable, or an unusual file type;
* new downloads in Nix (fetchers, `buildGoModule`, `buildNpmPackage` and similar), added content hashes;
* process or network use in Python (`subprocess`, `os.system`, `urllib`, `requests`, `socket`), network or dynamic import in JavaScript (`fetch(`, `XMLHttpRequest`, `WebSocket`, `import(`);
* any new URL outside the trusted sources;
* weakened Nix safety (`allowUnfree`, `allowInsecure`, `--impure`, `sandbox = false` and similar);
* privileged CI (`pull_request_target`, `workflow_run`, `secrets.`, `contents: write`, `id-token: write`) and actions not pinned to a commit;
* very long lines (over 1000 characters), image changes, and any added `flake.lock` owner, repo or URL line.

A pull request that adds links to documentation will therefore list them under review; that is normal.

## Recommended GitHub settings

These are settings in the repository's GitHub configuration, not files in it. The first group is what the workflows and `SECURITY.md` rely on; the second is general hardening.

| Setting | Why |
|---|---|
| Private vulnerability reporting: on | `SECURITY.md` tells reporters to use Security, Report a vulnerability. |
| Dependency graph and Dependabot alerts: on | The dependency review job needs the dependency graph. |
| Code scanning: on | CodeQL and Scorecard upload results to it. |
| Actions: allow GitHub Actions to create pull requests | Needed by the weekly flake.lock workflow. |
| Secret `FLAKE_UPDATE_TOKEN` | Makes the flake.lock pull request run CI (see above). |
| Branch protection on `main`: require a pull request, with the CI and PR guard jobs as required status checks | Makes "CI must pass before merge" true. Do not require the ISO job; it never runs on pull requests. |
| Require review from code owners | `CODEOWNERS` assigns the maintainers team to every path; the rule only binds when this is on. |
| Block force pushes and deletion of `main` | Scorecard checks branch protection. |
| Workflow permissions default to read-only; require approval for workflows from first-time contributors | The workflows already request minimal permissions; this is the account-level backstop. |
| Pages from the `gh-pages` branch | Serves the demo (see [Development](Development)). |

## Reporting a vulnerability

Report it privately through GitHub: on the repository, open Security, then Report a vulnerability. Please do not open a public issue for a security problem.

## Hardening built into a router

* SSH: keys only, no root login, 3 attempts, never exposed on the WAN; password login exists only on the rescue port address.
* Cockpit sign-in can require a one-time code from an authenticator app (Photosphere → Advanced → Admin account), with one-use recovery codes. SSH, `sudo` and the console are not affected, so a lost phone cannot lock you out.
* The router's admin ports (SSH, Cockpit, AdGuard) are reachable only from admin networks, Tailscale and the rescue port. Probes from other networks are dropped and notified.
* Notifications are deny-all; the router publishes with a write-only token, phones log in with a read-only account. Both are generated on first boot and never enter the Nix store.
* Kernel hardening sysctls (rp_filter, no redirects, no source routing, SYN cookies, restricted dmesg and kernel pointers).
* Driver services run sandboxed (dynamic user, read-only system, no capabilities, no network for detection).
* Remote access secrets live under `/var/lib/heliosphere/`, outside git; the OpenVPN CA key is not visible to the container.
* Every change is a git commit and a NixOS generation, so you can read and undo it.
