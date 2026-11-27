#!/usr/bin/env python3
"""Supply-chain guard for HeliOS pull requests.

    python3 -I .github/scripts/pr-guard.py <base-sha> <head-sha>

Reads only the git diff (never runs the PR's code). Findings are
  BLOCK   fails the check: things with almost no honest use in this repo
          (hidden Unicode, pipe-to-shell, obfuscated blobs, unknown sources);
  REVIEW  passes, but is listed in the job summary for a careful human read
          (new downloads, flake inputs, catalogs, CI, executables).
The workflow runs the BASE branch's copy of this file, so a PR can't edit it
to let itself through; a change to this file is always flagged for review.
"""
import os
import re
import subprocess
import sys

base, head = sys.argv[1], sys.argv[2]

# Sources a flake input or a fetcher may point at without being blocked
TRUSTED_SOURCES = [
    r"github:NixOS/",
    r"https://github\.com/NixOS/",
    r"https://(cache|channels|releases)\.nixos\.org/",
]
# Files we expect to change rarely and that decide what gets installed or trusted
SENSITIVE = [
    (r"^\.github/", "CI / repository automation"),
    (r"^flake\.(nix|lock)$", "flake inputs (what every router builds from)"),
    (r"^pkgs/[^/]+\.json$", "catalog (drivers, blocklists, models, services…)"),
    (r"^installer/", "installer (runs as root on a blank machine)"),
    (r"^modules/(airlock|heliopause|recovery)\.nix$", "access control / firewall / recovery"),
    (r"(^|/)CODEOWNERS$|^SECURITY\.md$", "review rules"),
]
TEXT_EXT = {".nix", ".py", ".sh", ".js", ".cjs", ".mjs", ".css", ".html", ".json", ".md",
            ".yml", ".yaml", ".toml", ".hujson", ".txt", ".lock", ""}
ALLOWED_BINARY = re.compile(r"^(docs|branding|pkgs/photosphere)/[^/]+\.(png|jpg|jpeg|webp)$")

# (severity, pattern, why) checked on every added line
LINE_RULES = [
    ("BLOCK", re.compile(r"[\u202a-\u202e\u2066-\u2069]"), "bidirectional control character (Trojan Source)"),
    ("BLOCK", re.compile(r"[\u200b-\u200d\u2060\ufeff\u00ad\u180e]"), "invisible / zero-width character"),
    ("BLOCK", re.compile(r"(curl|wget)\b[^|\n]*\|\s*(sudo\s+)?(ba|z|da)?sh\b|bash\s+<\(\s*(curl|wget)"), "download piped into a shell"),
    ("BLOCK", re.compile(r"[A-Za-z0-9+/]{200,}={0,2}"), "long base64-looking blob"),
    ("BLOCK", re.compile(r"(\\x[0-9a-fA-F]{2}){12,}"), "hex-escaped payload"),
    ("BLOCK", re.compile(r"\b(eval|exec)\s*\(\s*(base64|codecs|zlib|bytes\.fromhex|atob|unescape|String\.fromCharCode)"), "decode-then-execute"),
    ("BLOCK", re.compile(r"new\s+Function\s*\(|\bdocument\.write\s*\("), "dynamic code in the UI"),
    ("REVIEW", re.compile(r"\b(fetchurl|fetchzip|fetchTarball|fetchFromGitHub|fetchFromGitLab|fetchgit|fetchGit|fetchClosure|builtins\.fetch\w*|fetchPypi|buildGoModule|buildNpmPackage)\b"), "new download in Nix"),
    ("REVIEW", re.compile(r"\b(sha256|hash|outputHash|vendorHash|npmDepsHash)\s*=\s*\""), "content hash added or changed"),
    ("REVIEW", re.compile(r"\b(subprocess|os\.system|os\.popen|urllib|requests|socket)\b"), "process or network use in Python"),
    ("REVIEW", re.compile(r"\b(fetch|XMLHttpRequest|WebSocket|importScripts)\s*\(|\bimport\s*\("), "network or dynamic import in JS"),
    ("REVIEW", re.compile(r"https?://[^\s\"'<>)]+"), "URL"),
    ("REVIEW", re.compile(r"\b(allowUnfree|allowInsecure|permittedInsecurePackages|NIXPKGS_ALLOW|--impure|__noChroot|sandbox\s*=\s*false)\b"), "weakens Nix safety defaults"),
    ("REVIEW", re.compile(r"pull_request_target|workflow_run|secrets\.|permissions:\s*write-all|contents:\s*write|id-token:\s*write"), "privileged CI"),
    ("REVIEW", re.compile(r"uses:\s*[\w.-]+/[\w./-]+@(?![0-9a-f]{40}\b)"), "action not pinned to a commit"),
]
URL_RE = re.compile(r"(?:github:|https?://)[^\s\"'<>)]+")


def git(*args):
    # quotepath=off: non-ASCII names come out as themselves, not "b/\303\251…"
    return subprocess.run(["git", "-c", "core.quotepath=off", *args], check=True, capture_output=True, text=True, errors="replace").stdout


findings = []  # (severity, file, line, message, snippet)


def add(sev, path, line, msg, text=""):
    findings.append((sev, path, line, msg, text.strip()[:160]))


def trusted(url):
    return any(re.match(p, url) for p in TRUSTED_SOURCES)


# ------------------------------------------------------------ file-level checks
for row in git("diff", "--raw", "-M", "--no-ext-diff", base, head).splitlines():
    meta, *paths = row.split("\t")
    old_mode, new_mode, _, _, status = meta[1:].split()
    path = paths[-1]
    if new_mode == "120000":
        add("BLOCK", path, 0, "symlink added (can point outside the repo)")
    elif new_mode == "100755" and old_mode != "100755":
        add("REVIEW", path, 0, "made executable")
    if status.startswith("D"):
        if path == ".github/scripts/pr-guard.py":
            add("BLOCK", path, 0, "deletes the PR guard")
        continue
    if path == ".github/scripts/pr-guard.py":
        add("REVIEW", path, 0, "changes the PR guard itself: takes effect only after merge, read it closely")
    for pat, why in SENSITIVE:
        if re.search(pat, path):
            add("REVIEW", path, 0, f"sensitive path: {why}")

def is_text(path):
    data = subprocess.run(["git", "show", f"{head}:{path}"], capture_output=True).stdout
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return b"\0" not in data


# File names git still has to quote (tabs, newlines, quotes, backslashes, control
# characters) would hide the file from the line checks below, which only read
# "+++ b/<path>" headers: block them, and anything else outside a plain charset.
SAFE_PATH = re.compile(r"[A-Za-z0-9._/@+-]+")
for name in git("diff", "--name-only", "-z", "-M", "--no-ext-diff", base, head).split("\0"):
    if name and not SAFE_PATH.fullmatch(name):
        add("BLOCK", name, 0, "file name with unusual characters (would skip the line checks)")

numstat = git("diff", "--numstat", "-M", "--no-ext-diff", base, head)
for row in numstat.splitlines():
    added, _, path = row.split("\t", 2)
    path = path.split(" => ")[-1].rstrip("}")
    if added == "-":  # git says binary
        if ALLOWED_BINARY.search(path):
            add("REVIEW", path, 0, "image added/changed")
        else:
            add("BLOCK", path, 0, "binary file (can't be reviewed; build from source instead)")
    elif not is_text(path):
        add("BLOCK", path, 0, "binary content in a text-diffed file")
    elif os.path.splitext(path)[1] not in TEXT_EXT:
        add("REVIEW", path, 0, "unusual file type")

# ------------------------------------------------------------ line-level checks
diff = git("diff", "-U0", "-M", "--no-ext-diff", "--no-color", base, head)
path, lineno = None, 0
for raw in diff.splitlines():
    if raw.startswith("+++ "):
        path = raw[6:] if raw.startswith("+++ b/") else None
        if path is None and raw != "+++ /dev/null":
            add("BLOCK", raw[4:], 0, "diff header the guard can't read (line checks skipped)")
        continue
    m = re.match(r"@@ -\S+ \+(\d+)", raw)
    if m:
        lineno = int(m.group(1))
        continue
    if not path or not raw.startswith("+"):
        continue
    line = raw[1:]
    for sev, rx, why in LINE_RULES:
        if rx.search(line):
            if why == "URL":
                for url in URL_RE.findall(line):
                    if not trusted(url):
                        add("REVIEW", path, lineno, f"new URL: {url}", "")
                continue
            if why == "long base64-looking blob" and path.endswith(".lock"):
                continue
            add(sev, path, lineno, why, line)
    if len(line) > 300 and not path.endswith((".lock", ".md", ".svg", ".json")):
        # Minified/packed code is long with almost no spaces; long prose-ish lines are only listed
        if line.count(" ") / len(line) < 0.03:
            add("BLOCK", path, lineno, f"{len(line)}-character line with almost no spaces (minified or packed)", line)
        elif len(line) > 1000:
            add("REVIEW", path, lineno, f"{len(line)}-character line", line)
    if path == "flake.lock":
        for url in re.findall(r'"(?:owner|repo|url)":\s*"([^"]+)"', line):
            add("REVIEW", path, lineno, f"lock entry: {url}")
    if path == "flake.nix":
        for url in URL_RE.findall(line):
            if not trusted(url):
                add("BLOCK", path, lineno, f"flake input from an untrusted source: {url}", line)
    lineno += 1

# ------------------------------------------------------------ report
block = [f for f in findings if f[0] == "BLOCK"]
review = [f for f in findings if f[0] == "REVIEW"]
out = ["## PR guard", ""]
out.append(f"**{len(block)} blocking, {len(review)} to review.**" if findings else "Nothing flagged.")
for sev, items in (("Blocking", block), ("Review carefully", review)):
    if items:
        out += ["", f"### {sev}", "", "| file | line | finding |", "|---|---|---|"]
        seen = set()
        for _, p, n, msg, text in items:
            if (p, n, msg) in seen:
                continue
            seen.add((p, n, msg))
            snippet = f" — `{text.replace('|', '¦').replace('`', '´')}`" if text else ""
            out.append(f"| `{p}` | {n or ''} | {msg}{snippet} |")
report = "\n".join(out) + "\n"
print(report)
if os.environ.get("GITHUB_STEP_SUMMARY"):
    with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as fh:
        fh.write(report)
for _, p, n, msg, _ in block:
    print(f"::error file={p},line={n or 1}::{msg}")
sys.exit(1 if block else 0)
