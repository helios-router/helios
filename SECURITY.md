# Security

Report a vulnerability privately through GitHub: **Security → Report a vulnerability**
on this repository, or go straight to
<https://github.com/helios-router/helios/security/advisories/new>.
Please don't open a public issue for it.

## Security baseline

[SECURITY_AUDIT.md](SECURITY_AUDIT.md) records the security boundaries, every audit
finding with its severity and status, what was verified by design, and the regression
test that covers each fix. Check a security-relevant change against it, and update it
in the same pull request.

## How changes are guarded

- Every pull request runs `.github/workflows/pr-guard.yml`: a diff scanner (hidden
  Unicode, pipe-to-shell, packed or binary payloads, new downloads and sources),
  GitHub's dependency review, and workflow linting (zizmor, actionlint). The scanner
  runs from the base branch, so a PR can't switch it off.
- CodeQL (Python, JavaScript, Actions; security and code-quality rules) and OpenSSF Scorecard run on every pull request, on `main` and weekly (CodeQL), and on `main` and weekly (Scorecard).
- All actions are pinned to commit SHAs, and CI's test dependencies (`tests/ci/`) are pinned by hash;
  Dependabot updates them after a 7-day cooldown.
- nixpkgs updates arrive as a weekly pull request that must pass CI before merging.
- CODEOWNERS requires the owner's review on every change.
