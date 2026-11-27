#!/usr/bin/env python3
"""helios-ctl — HeliOS backend for Photosphere (the Cockpit pages); handy on the CLI too.

Every command prints JSON. Site settings come from /etc/helios/config.json
(generated from your helios.* options); devices/QoS/UPnP live in
<configDir>/state.json, tracked in git. Changes that need a rebuild go through
apply-start → apply-run → confirm, with a LuCI-style automatic rollback if the
change isn't confirmed within 120 s (e.g. you locked yourself out).

Environment overrides (used by the test suite):
  HELIOS_CONFIG      default /etc/helios/config.json
  HELIOS_DATA_DIR    default /var/lib/helios
  HELIOS_REBUILD     default "nixos-rebuild switch"
  HELIOS_FAKE_ROOT   prefix for /sys, /proc, /run and /var/lib reads
  HELIOS_NO_SYSTEMD  run apply in-process instead of via systemd-run
"""
import argparse
import base64
import datetime as dt
import fcntl
import hashlib
import hmac
import ipaddress
import json
import os
import pwd
import re
import secrets
import shlex
import shutil
import socket
import string
import struct
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

try:
    import bcrypt
except ImportError:  # only needed by notify-setup
    bcrypt = None

CONFIG_FILE = os.environ.get("HELIOS_CONFIG", "/etc/helios/config.json")
try:
    with open(CONFIG_FILE) as _f:
        CFG = json.load(_f)
except (OSError, ValueError):
    CFG = {}
CONFIG_DIR = CFG.get("configDir", "/etc/nixos")
STATE_NAME = CFG.get("stateFile", "state.json")
DATA_DIR = os.environ.get("HELIOS_DATA_DIR", "/var/lib/helios")
FAKE = os.environ.get("HELIOS_FAKE_ROOT", "")
STATE = os.path.join(CONFIG_DIR, STATE_NAME)
APPLY_STATUS = os.path.join(DATA_DIR, "apply.json")
APPLY_LOG = os.path.join(DATA_DIR, "apply.log")
UPLINK = os.path.join(DATA_DIR, "uplink.json")
OUTAGES = os.path.join(DATA_DIR, "outages.jsonl")
ADGUARD_CONF = os.path.join(DATA_DIR, "adguard.json")
ADGUARD_MANAGED = os.path.join(DATA_DIR, "adguard-managed.json")
SW = CFG.get("solarWind", {}) or {}
SW_ENV = os.path.join(DATA_DIR, "solarwind.env")
SW_TOKEN = SW.get("tokenFile", os.path.join(DATA_DIR, "solarwind-token"))
SW_PHONE = os.path.join(DATA_DIR, "solarwind-phone.json")
FW_TABLE = CFG.get("firewallTable", "heliopause")
CONFIRM_SECONDS = 120
DETECT_FILE = CFG.get("detectFile") or "/var/lib/helios-detect/hardware.json"
TRUNK_MAC = (CFG.get("trunkMac") or "").lower() or None
RESCUE_MAC = (CFG.get("rescueMac") or "").lower() or None
VULNIX_SUMMARY = CFG.get("vulnixSummary") or os.path.join(DATA_DIR, "vulnix-summary.json")

# Network roles + default networks: from config.json (built from pkgs/networks.json),
# or the file next to this script when run from a source checkout (tests).
def _catalog():
    if CFG.get("roles"):
        return {"roles": CFG["roles"], "access": CFG.get("accessHelp", {}), "defaults": CFG.get("defaultNetworks", [])}
    try:
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "networks.json")) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"roles": {}, "access": {}, "defaults": []}


CATALOG = _catalog()


# Built-in drivers (docs/drivers.md): from config.json, or the file next to this script.
def _driver_catalog():
    if CFG.get("driverCatalog"):
        return CFG["driverCatalog"]
    try:
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "drivers.json")) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"drivers": {}, "upsVendors": {}, "nutDefaults": {}}


DRIVERS = _driver_catalog()
ROLES = CATALOG["roles"]
ACCESS_KEYS = list(CATALOG.get("access") or next(iter(ROLES.values()), {}).get("access", {}))
WAN_VLAN = CFG.get("wanVlan", 10)
NET_NAME_RE = re.compile(r"^[a-z][a-z0-9]{0,11}\Z")
NET_RESERVED = {"wan", "lo", "trunk0", "rescue0", "tailscale0", "ifb4wan", "lan0"}
MAX_NETWORKS = 32


def _state_networks():
    """Networks as stored in state.json, or the defaults when it has none yet."""
    try:
        with open(os.path.join(CFG.get("configDir", "/etc/nixos"), CFG.get("stateFile", "state.json"))) as f:
            nets = json.load(f).get("networks")
    except (OSError, ValueError):
        nets = None
    return nets if nets else [dict(n) for n in CATALOG.get("defaults", [])]


def resolve_access(n):
    base = dict((ROLES.get(n.get("role")) or {}).get("access", {}))
    base.update(n.get("access") or {})
    return base


# name → {"id": VLAN id, "net": first three octets}, in the order the user set
VLANS = {n["name"]: {"id": n["vlan"], "net": n["prefix"]} for n in _state_networks()}
SUBNET_TO_VLAN = {v["id"]: k for k, v in VLANS.items()}
# Filtering profiles: state.json "profiles", else the defaults in pkgs/profiles.json
def _profile_catalog():
    if CFG.get("profileCatalog"):
        return CFG["profileCatalog"]
    try:
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "profiles.json")) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"settings": {}, "defaults": [{"name": "default", "label": "AdGuard defaults", "global": True}]}


PROFILE_CATALOG = _profile_catalog()
PROFILE_KEYS = ("global", "filtering", "safeBrowsing", "parental", "safeSearch", "youtubeRestricted")
NET_CLIENT_PREFIX = "net-"          # AdGuard clients HeliOS makes for a network's default profile


def state_profiles(s):
    return s.get("profiles") or [dict(x) for x in PROFILE_CATALOG.get("defaults", [])]


def profile_names(s):
    return [x["name"] for x in state_profiles(s)]
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}\Z")
MAC_RE = re.compile(r"^([0-9a-f]{2}:){5}[0-9a-f]{2}\Z")

SERVICES = [tuple(x) for x in CFG.get("services", [])]


# --------------------------------------------------------------- helpers
def out(obj):
    json.dump(obj, sys.stdout, indent=None, separators=(",", ":"))
    sys.stdout.write("\n")


def fail(msg, code=1):
    out({"ok": False, "error": msg})
    sys.exit(code)


def p(path):
    """Path with the optional fake root (tests)."""
    return FAKE + path if FAKE and path.startswith("/") else path


def run(cmd, check=False, timeout=30, **kw):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, **kw)
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        if check:
            raise
        return subprocess.CompletedProcess(cmd, 127, "", str(e))
    if check and r.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd)}: {r.stderr.strip() or r.stdout.strip()}")
    return r


def run_json(cmd, default=None):
    r = run(cmd)
    if r.returncode != 0 or not r.stdout.strip():
        return default
    try:
        return json.loads(r.stdout)
    except ValueError:
        return default


def read(path, default=""):
    try:
        with open(p(path)) as f:
            return f.read()
    except OSError:
        return default


def read_json(path, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def give_to(path, user):
    """Hand a file or folder to the one service account that reads it, so it can be 0400/0700
    instead of readable by a group or everyone. Only root can do it; if the account doesn't exist
    yet (that service is off), it stays root's and the module's tmpfiles rules fix it up later."""
    if os.geteuid() != 0:
        return
    try:
        pw = pwd.getpwnam(user)
    except KeyError:
        return
    os.chown(path, pw.pw_uid, pw.pw_gid)


def write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def now():
    return int(time.time())


def self_exe():
    """How to re-invoke ourselves from a systemd unit (which doesn't inherit our PATH)."""
    if os.environ.get("HELIOS_SELF"):
        return [os.environ["HELIOS_SELF"]]
    return [sys.executable, os.path.realpath(__file__)]


class Lock:
    def __enter__(self):
        os.makedirs(DATA_DIR, exist_ok=True)
        self.f = open(os.path.join(DATA_DIR, ".lock"), "w")
        fcntl.flock(self.f, fcntl.LOCK_EX)
        return self

    def __exit__(self, *a):
        fcntl.flock(self.f, fcntl.LOCK_UN)
        self.f.close()


# ------------------------------------------------------------------ git
def git(*args, check=True):
    cmd = ["git", "-C", CONFIG_DIR, "-c", "safe.directory=*",
           "-c", "user.name=helios", "-c", "user.email=helios@localhost", *args]
    return run(cmd, check=check)


def git_ready():
    if not os.path.isdir(os.path.join(CONFIG_DIR, ".git")):
        git("init", "-q")
        git("add", "-A")
        git("commit", "-q", "-m", "Initial snapshot of /etc/nixos", "--allow-empty")


def state_dirty():
    r = git("status", "--porcelain", "--", STATE_NAME, check=False)
    return bool(r.stdout.strip())


def head_state():
    r = git("show", f"HEAD:{STATE_NAME}", check=False)
    if r.returncode != 0:
        return None
    try:
        return json.loads(r.stdout)
    except ValueError:
        return None


# ---------------------------------------------------------------- state
def load_state():
    s = read_json(STATE)
    if s is None:
        fail(f"cannot read {STATE}")
    s.setdefault("devices", [])
    s.setdefault("qos", {"enabled": False, "down": None, "up": None})
    s.setdefault("upnp", {"enabled": True})
    return s


def save_state(s):
    s["devices"].sort(key=lambda d: (list(VLANS).index(d["vlan"]) if d["vlan"] in VLANS else 99,
                                     ipaddress.ip_address(d["ip"]) if d.get("ip") else ipaddress.ip_address("0.0.0.0")))
    write_json(STATE, s)


def next_free_ip(s, vlan, exclude_mac=None):
    used = {d.get("ip") for d in s["devices"] if d.get("mac") != exclude_mac}
    net = VLANS[vlan]["net"]
    for host in range(20, 100):
        ip = f"{net}.{host}"
        if ip not in used:
            return ip
    raise RuntimeError(f"no free fixed IPs left in {vlan} (.20–.99)")


def describe_change(old, new):
    """Human summary of what changed between two states (for banner + commit)."""
    if old is None:
        return ["Initial state"]
    msgs = []
    od = {d["mac"]: d for d in old.get("devices", [])}
    nd = {d["mac"]: d for d in new.get("devices", [])}
    for mac, d in nd.items():
        if mac not in od:
            msgs.append(f"Add {d['name']} ({d['vlan']}, {d.get('profile', 'default')})")
        elif od[mac] != d:
            o = od[mac]
            parts = [k for k in ("name", "vlan", "ip", "profile", "blocked", "media", "mediaServers", "mediaServer", "notes") if o.get(k) != d.get(k)]
            label = o["name"] if o["name"] == d["name"] else f"{o['name']} → {d['name']}"
            msgs.append(f"Edit {label}: {', '.join(parts)}")
    for mac, d in od.items():
        if mac not in nd:
            msgs.append(f"Remove {d['name']}")
    on = {n["name"]: n for n in old.get("networks") or CATALOG.get("defaults", [])}
    nn = {n["name"]: n for n in new.get("networks") or CATALOG.get("defaults", [])}
    for name, n in nn.items():
        if name not in on:
            msgs.append(f"Add network {name} (VLAN {n['vlan']}, {n['role']})")
        elif on[name] != n:
            parts = [k for k in ("label", "vlan", "prefix", "ipv6", "role", "color", "access", "defaultProfile") if on[name].get(k) != n.get(k)]
            msgs.append(f"Edit network {name}: {', '.join(parts)}")
    for name in on:
        if name not in nn:
            msgs.append(f"Remove network {name}")
    for key in ("qos", "upnp"):
        if old.get(key) != new.get(key):
            msgs.append(f"Change {key} settings")
    if bool(old.get("restrictUnnamed")) != bool(new.get("restrictUnnamed")):
        msgs.append("Restrict unnamed devices: " + ("on" if new.get("restrictUnnamed") else "off"))
    if old.get("maintenance") != new.get("maintenance"):
        w = maint_settings(new)["rebootWindow"]
        msgs.append(f"Reboot window: {', '.join(w['days'])} {w['time']}" if w["enabled"] else "Reboot window off")
    if old.get("logwatch") != new.get("logwatch"):
        msgs.append("Change AI log triage settings" if new.get("logwatch") else "AI log triage: back to defaults")
    for name in DRIVERS.get("drivers", {}):
        o, n = driver_state(old, name), driver_state(new, name)
        if o["enabled"] != n["enabled"]:
            msgs.append(f"{'Enable' if n['enabled'] else 'Disable'} driver {name}")
        elif o != n:
            msgs.append(f"Change driver {name} settings")
    oh = {d["id"]: d for d in old.get("hardware") or []}
    nh = {d["id"]: d for d in new.get("hardware") or []}
    for hid, d in nh.items():
        if hid not in oh:
            msgs.append(f"Add hardware {d.get('label') or hid}")
        elif oh[hid] != d:
            parts = [k for k in ("kind", "driver", "label", "notes", "match", "nutDriver", "ports", "ssids") if oh[hid].get(k) != d.get(k)]
            msgs.append(f"Edit hardware {d.get('label') or hid}: {', '.join(parts)}")
    for hid, d in oh.items():
        if hid not in nh:
            msgs.append(f"Remove hardware {d.get('label') or hid}")
    msgs += describe_remote(old, new)
    msgs += describe_profiles(old, new)
    msgs += describe_printing(old, new)
    msgs += describe_services(old, new)
    return msgs


# --------------------------------------------------------------- leases
def parse_leases():
    """Kea memfile CSV → latest record per IP."""
    rows = {}
    for path in ("/var/lib/kea/dhcp4.leases.2", "/var/lib/kea/dhcp4.leases"):
        text = read(path)
        if not text:
            continue
        lines = text.splitlines()
        header = None
        for line in lines:
            if not line.strip():
                continue
            if line.startswith("address,"):
                header = line.split(",")
                continue
            if header is None:
                continue
            vals = line.split(",")
            rec = dict(zip(header, vals))
            rows[rec.get("address")] = rec
    leases = []
    t = now()
    for ip, r in rows.items():
        try:
            expire = int(r.get("expire", "0"))
            state = int(r.get("state", "0") or 0)
            subnet = int(r.get("subnet_id", "0") or 0)
        except ValueError:
            continue
        if state != 0 or expire <= t:
            continue
        valid = int(r.get("valid_lifetime", "0") or 0)
        leases.append({
            "ip": ip,
            "mac": (r.get("hwaddr") or "").lower(),
            "hostname": (r.get("hostname") or "").replace("&#x2c", ",").rstrip("."),
            "vlan": SUBNET_TO_VLAN.get(subnet, str(subnet)),
            "expires": expire,
            "since": expire - valid if valid else None,
        })
    return leases


def neighbors():
    data = run_json(["ip", "-j", "neigh", "show"], default=[]) or []
    res = {}
    for n in data:
        mac = (n.get("lladdr") or "").lower()
        states = n.get("state") or []
        if not mac or any(s in ("FAILED", "INCOMPLETE") for s in states):
            continue
        res[mac] = {"ip": n.get("dst"), "dev": n.get("dev"), "state": (states or ["?"])[0]}
    return res


def cmd_devices(a):
    s = load_state()
    leases = parse_leases()
    neigh = neighbors()
    by_mac = {}
    for d in s["devices"]:
        e = dict(d)
        e["known"] = True
        e.setdefault("profile", "default")
        e.setdefault("blocked", False)
        e.setdefault("media", False)
        by_mac[d["mac"].lower()] = e
    for l in leases:
        e = by_mac.get(l["mac"])
        if e is None:
            e = {"mac": l["mac"], "name": "", "vlan": l["vlan"], "ip": None, "profile": "default",
                 "blocked": False, "media": False, "notes": "", "known": False}
            by_mac[l["mac"]] = e
        e["lease_ip"] = l["ip"]
        e["lease_vlan"] = l["vlan"]
        e["hostname"] = l["hostname"]
        e["lease_since"] = l["since"]
        e["lease_expires"] = l["expires"]
    for mac, e in by_mac.items():
        n = neigh.get(mac)
        e["online"] = bool(n and n["state"] in ("REACHABLE", "DELAY", "PROBE", "STALE", "PERMANENT"))
        e["neigh_state"] = n["state"] if n else None
        e["current_ip"] = (n or {}).get("ip") or e.get("lease_ip") or e.get("ip")
    devs = sorted(by_mac.values(), key=lambda e: (e["known"], e.get("name") or e["mac"]))
    ru = restrict_status(s)
    for e in devs:
        e["restricted"] = bool(ru["armed"] and not e["known"] and (e.get("lease_vlan") or e.get("vlan")) in ru["networks"])
    out({"ok": True, "devices": devs, "dirty": state_dirty(), "vlans": list(VLANS), "restrictUnnamed": ru,
         "profiles": [{k: x.get(k) for k in ("name", "label", "desc")} for x in state_profiles(s)]})


# ------------------------------------------------- unnamed-device restriction
def restrict_status(s):
    """restrictUnnamed: setting, whether it's armed (needs a named admin-network
    device), and which networks it covers (their "quarantine" access flag)."""
    nets = s.get("networks") or CATALOG.get("defaults", [])
    admin = {n["name"] for n in nets if resolve_access(n).get("admin")}
    enabled = bool(s.get("restrictUnnamed", False))
    has_admin = any(d.get("vlan") in admin for d in s["devices"])
    return {"enabled": enabled, "armed": enabled and has_admin, "waitingForAdmin": enabled and not has_admin,
            "networks": [n["name"] for n in nets if resolve_access(n).get("quarantine")]}


def cmd_restrict_unnamed(a):
    with Lock():
        s = load_state()
        s["restrictUnnamed"] = a.state == "on"
        save_state(s)
    out({"ok": True, "restrictUnnamed": restrict_status(s), "dirty": True})


# --------------------------------------------------------------- edits
def parse_media_server(m):
    """Validate a device's "mediaServer" block; None means "not a media server"."""
    if m is None or m is False:
        return None
    if not isinstance(m, dict):
        fail("mediaServer must be an object")
    clean = {}
    for proto in ("tcp", "udp"):
        ports = m.get(proto) or []
        if not isinstance(ports, list) or len(ports) > 16:
            fail(f"mediaServer.{proto} must be a list of at most 16 ports")
        try:
            ports = sorted({int(p) for p in ports})
        except (TypeError, ValueError):
            fail(f"mediaServer.{proto}: ports must be numbers")
        if any(not 1 <= p <= 65535 for p in ports):
            fail(f"mediaServer.{proto}: ports must be 1-65535")
        clean[proto] = ports
    if not clean["tcp"] and not clean["udp"]:
        fail("a media server needs at least one port")
    nets = m.get("networks") or []
    if not isinstance(nets, list):
        fail("mediaServer.networks must be a list of network names")
    unknown = [n for n in nets if n not in VLANS]
    if unknown:
        fail(f"mediaServer.networks: unknown network {unknown[0]!r}")
    clean["networks"] = sorted(set(nets), key=list(VLANS).index)
    return clean


def cmd_device_set(a):
    try:
        d = json.loads(a.json)
    except ValueError:
        fail("invalid JSON")
    mac = (d.get("mac") or "").lower().replace("-", ":")
    if not MAC_RE.match(mac):
        fail("invalid MAC address")
    name = (d.get("name") or "").strip().lower()
    if not NAME_RE.match(name):
        fail("name must be lowercase letters, digits or dashes (max 32)")
    vlan = d.get("vlan")
    if vlan not in VLANS:
        fail(f"unknown VLAN {vlan!r}")
    if name.startswith(NET_CLIENT_PREFIX):
        fail(f"names starting with {NET_CLIENT_PREFIX!r} are reserved for network defaults")
    with Lock():
        s = load_state()
        names_ = profile_names(s)
        profile = d.get("profile") or network_default_profile(s, vlan) or ("default" if "default" in names_ else names_[0])
        if profile not in profile_names(s):
            fail(f"unknown profile {profile!r}")
        others = [x for x in s["devices"] if x["mac"].lower() != mac]
        if any(x["name"] == name for x in others):
            fail(f"name {name!r} is already used")
        old = next((x for x in s["devices"] if x["mac"].lower() == mac), None)
        ip = d.get("ip") or (old or {}).get("ip")
        if not ip or not ip.startswith(VLANS[vlan]["net"] + ".") or (old and old.get("vlan") != vlan and not d.get("ip")):
            ip = next_free_ip(s, vlan, exclude_mac=mac)
        if any(x.get("ip") == ip for x in others):
            fail(f"IP {ip} is already reserved")
        new = {
            "mac": mac, "name": name, "vlan": vlan, "ip": ip, "profile": profile,
            "blocked": bool(d.get("blocked", (old or {}).get("blocked", False))),
            # Admin networks reach everything already; "media" matters only elsewhere
            "media": bool(d.get("media", False)) and not network_access(s, vlan).get("admin"),
            "notes": (d.get("notes") or "")[:200],
        }
        # Absent key keeps the old setting (CLI edits); null/false removes it
        ms = parse_media_server(d["mediaServer"]) if "mediaServer" in d else (old or {}).get("mediaServer")
        if ms:
            new["mediaServer"] = ms
        # Which media servers a media device may use: absent/null = all of them
        if new["media"]:
            sel = d["mediaServers"] if "mediaServers" in d else (old or {}).get("mediaServers")
            if sel is not None:
                if not isinstance(sel, list) or not all(isinstance(x, str) for x in sel):
                    fail("mediaServers must be a list of media server names, or null for all")
                servers = {x["name"] for x in others if x.get("mediaServer")}
                bad = [x for x in sel if x not in servers]
                if bad:
                    fail(f"{bad[0]!r} isn't a media server")
                if not sel:
                    fail("pick at least one media server, or allow all of them")
                new["mediaServers"] = sorted(set(sel))
        if old and old.get("mediaServer"):
            # A server renamed, or no longer a server: follow it in media devices' lists
            for x in others:
                if old["name"] in (x.get("mediaServers") or []):
                    rest_ = [y for y in x["mediaServers"] if y != old["name"]] + ([name] if ms else [])
                    if rest_:
                        x["mediaServers"] = sorted(set(rest_))
                    else:
                        x.pop("mediaServers", None)     # nothing left: back to "all servers"
        s["devices"] = others + [new]
        save_state(s)
    out({"ok": True, "device": new, "dirty": True})


def cmd_device_remove(a):
    mac = a.mac.lower()
    with Lock():
        s = load_state()
        gone = next((x for x in s["devices"] if x["mac"].lower() == mac), None)
        s["devices"] = [x for x in s["devices"] if x["mac"].lower() != mac]
        if gone is None:
            fail("no such device")
        for x in s["devices"]:
            if gone["name"] in (x.get("mediaServers") or []):
                rest_ = [y for y in x["mediaServers"] if y != gone["name"]]
                if rest_:
                    x["mediaServers"] = rest_
                else:
                    x.pop("mediaServers", None)
        save_state(s)
    out({"ok": True, "dirty": True})


# ------------------------------------------------------------- networks
def state_networks(s):
    """The state's network list, materialised from the defaults on first edit."""
    if not s.get("networks"):
        s["networks"] = [dict(n) for n in CATALOG.get("defaults", [])]
    return s["networks"]


def network_access(s, name):
    n = next((x for x in (s.get("networks") or CATALOG.get("defaults", [])) if x["name"] == name), None)
    return resolve_access(n) if n else {}


def network_view(n, devices):
    role = ROLES.get(n.get("role")) or {}
    return {"name": n["name"], "label": n.get("label") or n["name"], "vlan": n["vlan"], "prefix": n["prefix"],
            "ipv6": n.get("ipv6"), "role": n.get("role"), "color": n.get("color") or role.get("color", "#888888"),
            "desc": role.get("desc", ""), "access": resolve_access(n), "overrides": sorted((n.get("access") or {}).keys()),
            "devices": sum(1 for d in devices if d.get("vlan") == n["name"]),
            "defaultProfile": n.get("defaultProfile")}


def network_default_profile(s, name):
    n = next((x for x in (s.get("networks") or CATALOG.get("defaults", [])) if x["name"] == name), None)
    return (n or {}).get("defaultProfile")


def cmd_networks(a):
    s = load_state()
    nets = s.get("networks") or CATALOG.get("defaults", [])
    out({"ok": True, "networks": [network_view(n, s["devices"]) for n in nets], "roles": ROLES,
         "access": CATALOG.get("access", {}), "wanVlan": WAN_VLAN, "dirty": state_dirty()})


HS_PREFIXES = ("10.60.0", "10.61.0", "10.62.0")      # Heliosphere's: container, WireGuard and OpenVPN clients


def _valid_prefix(prefix):
    try:
        net = ipaddress.ip_network(prefix + ".0/24")
    except ValueError:
        return False
    return net.is_private and not any(net.overlaps(ipaddress.ip_network(x + ".0/24")) for x in HS_PREFIXES)


def clean_access(role, given):
    """Keep only the flags that differ from the role, so role updates still reach this network."""
    base = (ROLES.get(role) or {}).get("access", {})
    out_ = {}
    for k, v in (given or {}).items():
        if k not in ACCESS_KEYS:
            fail(f"unknown access flag {k!r}")
        if k == "reach":
            if v != "all" and not (isinstance(v, list) and all(isinstance(x, str) for x in v)):
                fail("reach must be \"all\" or a list of network names")
        elif not isinstance(v, bool):
            fail(f"access.{k} must be true or false")
        if v != base.get(k):
            out_[k] = v
    return out_


def cmd_network_set(a):
    try:
        d = json.loads(a.json)
    except ValueError:
        fail("invalid JSON")
    name = (d.get("name") or "").strip().lower()
    old_name = d.get("from") or name
    if not NET_NAME_RE.match(name) or name in NET_RESERVED or name.startswith("ve-"):
        fail("network name: 1-12 lowercase letters/digits, starting with a letter (it becomes the interface name)")
    role = d.get("role")
    if role not in ROLES:
        fail(f"unknown role {role!r}")
    try:
        vlan = int(d.get("vlan"))
    except (TypeError, ValueError):
        fail("VLAN id must be a number")
    if not 2 <= vlan <= 4094 or vlan == WAN_VLAN:
        fail(f"VLAN id must be 2-4094 and not {WAN_VLAN} (the WAN VLAN)")
    prefix = (d.get("prefix") or "").strip()
    if not re.match(r"^[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\Z", prefix) or not _valid_prefix(prefix):
        fail("subnet must be the first three octets of a private /24, e.g. 10.70.0 (10.60.0, 10.61.0 and 10.62.0 are Heliosphere's)")
    ipv6 = d.get("ipv6") or None
    if ipv6 is not None:
        if not re.match(r"^0x[0-9a-f]{1,2}\Z", str(ipv6).lower()):
            fail("IPv6 subnet id must be 0x0-0xff (from a /56)")
        ipv6 = str(ipv6).lower()
    label = (d.get("label") or name).strip()[:24]
    color = d.get("color") or None
    if color is not None and not re.match(r"^#[0-9a-fA-F]{6}\Z", color):
        fail("color must look like #3366ff")
    access = clean_access(role, d.get("access"))
    with Lock():
        s = load_state()
        nets = state_networks(s)
        old = next((n for n in nets if n["name"] == old_name), None)
        if d.get("from") and old is None:
            fail(f"no network {old_name!r}")
        others = [n for n in nets if n is not old]
        if old is None and len(nets) >= MAX_NETWORKS:
            fail(f"at most {MAX_NETWORKS} networks")
        for n in others:
            if n["name"] == name:
                fail(f"network {name!r} already exists")
            if n["vlan"] == vlan:
                fail(f"VLAN {vlan} is already used by {n['name']}")
            if n["prefix"] == prefix:
                fail(f"subnet {prefix}.0/24 is already used by {n['name']}")
            if ipv6 and n.get("ipv6") == ipv6:
                fail(f"IPv6 subnet id {ipv6} is already used by {n['name']}")
        reach = access.get("reach", (ROLES[role]["access"]).get("reach"))
        if isinstance(reach, list):
            valid = {n["name"] for n in others}
            access_reach = [r for r in reach if r in valid and r != name]
            if access_reach != reach:
                access["reach"] = access_reach
            if access.get("reach") == ROLES[role]["access"].get("reach"):
                access.pop("reach", None)
        new = {"name": name, "label": label, "vlan": vlan, "prefix": prefix, "ipv6": ipv6, "role": role}
        dp = d.get("defaultProfile") if "defaultProfile" in d else (old or {}).get("defaultProfile")
        if dp is not None:
            if dp not in profile_names(s):
                fail(f"unknown profile {dp!r}")
            new["defaultProfile"] = dp
        if color:
            new["color"] = color
        if access:
            new["access"] = access
        if not any(resolve_access(n).get("admin") for n in others + [new]):
            fail("at least one network must keep admin access, or you'd lock yourself out")
        if old is not None:
            # Renamed: follow the name everywhere. Renumbered: move fixed IPs, keeping the host part.
            for dev in s["devices"]:
                if dev.get("vlan") == old_name:
                    dev["vlan"] = name
                    if dev.get("ip") and old["prefix"] != prefix:
                        dev["ip"] = f"{prefix}.{dev['ip'].rsplit('.', 1)[1]}"
                ms = dev.get("mediaServer")
                if ms and old_name in ms.get("networks", []):
                    ms["networks"] = [name if x == old_name else x for x in ms["networks"]]
            for n in others:
                r = (n.get("access") or {}).get("reach")
                if isinstance(r, list) and old_name in r:
                    n["access"]["reach"] = [name if x == old_name else x for x in r]
            if old_name != name:
                hardware_rename_network(s, old_name, name)
            nets[nets.index(old)] = new
        else:
            nets.append(new)
        VLANS.clear()
        VLANS.update({n["name"]: {"id": n["vlan"], "net": n["prefix"]} for n in nets})
        save_state(s)
    out({"ok": True, "network": network_view(new, s["devices"]), "dirty": True})


def cmd_network_remove(a):
    with Lock():
        s = load_state()
        nets = state_networks(s)
        n = next((x for x in nets if x["name"] == a.name), None)
        if n is None:
            fail(f"no network {a.name!r}")
        used = [d["name"] for d in s["devices"] if d.get("vlan") == a.name]
        if used:
            fail(f"move or forget its devices first: {', '.join(used[:5])}{'…' if len(used) > 5 else ''}")
        rest = [x for x in nets if x is not n]
        if not any(resolve_access(x).get("admin") for x in rest):
            fail("that's the last admin network — removing it would lock you out")
        if not rest:
            fail("keep at least one network")
        for x in rest:
            r = (x.get("access") or {}).get("reach")
            if isinstance(r, list) and a.name in r:
                x["access"]["reach"] = [y for y in r if y != a.name]
        for d in s["devices"]:
            ms = d.get("mediaServer")
            if ms and a.name in ms.get("networks", []):
                ms["networks"] = [y for y in ms["networks"] if y != a.name]
        s["networks"] = rest
        dropped = hardware_drop_network(s, a.name)
        save_state(s)
    out({"ok": True, "dirty": True, "hardwareReferencesDropped": dropped})


def nft_set(op, setname, ip):
    return run(["nft", op, "element", "inet", FW_TABLE, setname, "{", ip, "}"])


def cmd_block(a):
    """Instant pause/unpause (Firewalla-style): live nft change + committed state, no rebuild."""
    mac = a.mac.lower()
    on = a.state == "on"
    with Lock():
        s = load_state()
        dev = next((x for x in s["devices"] if x["mac"].lower() == mac), None)
        if dev is None:
            fail("adopt the device first (give it a name), then pause it")
        dev["blocked"] = on
        save_state(s)
        op = "add" if on else "delete"
        # By IP (IPv4) and by MAC (covers the device's IPv6 addresses too)
        rs = [nft_set(op, "cutoff", dev["ip"]), nft_set(op, "cutoff_mac", mac)]
        live = all(r.returncode == 0 or (not on and "No such file" in r.stderr) for r in rs)
        # Commit just this toggle so it never shows up as a pending change
        git_ready()
        if not state_dirty_except_blocked(s):
            git("add", STATE_NAME)
            git("commit", "-q", "-m", f"{'Pause' if on else 'Resume'} {dev['name']}", check=False)
            dirty = False
        else:
            dirty = True
    out({"ok": True, "blocked": on, "live": live, "dirty": dirty,
         "note": None if live else (r.stderr.strip() or "nft unavailable")})


def state_dirty_except_blocked(s):
    """True if the working state differs from HEAD in anything besides blocked flags."""
    old = head_state()
    if old is None:
        return True
    strip = lambda st: json.dumps({**st, "devices": sorted(({k: v for k, v in d.items() if k != "blocked"}
                                                             for d in st.get("devices", [])), key=lambda d: d["mac"])},
                                  sort_keys=True)
    return strip(old) != strip(s)


def cmd_settings_set(a):
    with Lock():
        s = load_state()
        rate = RATE_RE
        if a.down is not None:
            if not rate.match(a.down):
                fail("download rate must look like 500mbit")
            s["qos"]["down"] = a.down
        if a.up is not None:
            if not rate.match(a.up):
                fail("upload rate must look like 50mbit")
            s["qos"]["up"] = a.up
        if a.qos is not None:
            s["qos"]["enabled"] = a.qos == "on"
        if a.upnp is not None:
            s["upnp"]["enabled"] = a.upnp == "on"
        save_state(s)
    out({"ok": True, "dirty": True, "state": {k: s[k] for k in ("qos", "upnp")}})


def cmd_pending(a):
    git_ready()
    s = load_state()
    changes = describe_change(head_state(), s) if state_dirty() else []
    st = read_json(APPLY_STATUS, {}) or {}
    out({"ok": True, "dirty": bool(changes), "changes": changes, "apply": st})


def cmd_discard(a):
    with Lock():
        git_ready()
        git("checkout", "--", STATE_NAME, check=False)
    out({"ok": True, "dirty": False})


def cmd_restore(a):
    """Load the state file from an older commit as a pending change (OPNsense config-history style)."""
    git_ready()
    r = git("show", f"{a.rev}:{STATE_NAME}", check=False)
    if r.returncode != 0:
        fail(f"that version has no {STATE_NAME}")
    with Lock():
        with open(STATE, "w") as f:
            f.write(r.stdout)
    out({"ok": True, "dirty": state_dirty()})


# ---------------------------------------------------------------- apply
def set_apply(**kw):
    st = read_json(APPLY_STATUS, {}) or {}
    st.update(kw)
    write_json(APPLY_STATUS, st)
    return st


def log(msg):
    with open(APPLY_LOG, "a") as f:
        f.write(f"[{dt.datetime.now().strftime('%H:%M:%S')}] {msg}\n")


def cmd_apply_start(a):
    st = read_json(APPLY_STATUS, {}) or {}
    if st.get("state") in ("running", "confirm"):
        fail(f"an apply is already {st['state']}")
    git_ready()
    if not state_dirty():
        fail("nothing to apply")
    os.makedirs(DATA_DIR, exist_ok=True)
    open(APPLY_LOG, "w").close()
    changes = describe_change(head_state(), load_state())
    set_apply(state="running", started=now(), finished=None, deadline=None, changes=changes, message="Starting…")
    self_cmd = self_exe() + ["apply-run"]
    if os.environ.get("HELIOS_NO_SYSTEMD"):
        subprocess.Popen(self_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    else:
        # Detached from the Cockpit session, so closing the browser can't kill a rebuild
        run(["systemd-run", "--unit=helios-apply", "--collect", "--quiet", "--property=Type=exec", *self_cmd], check=True)
    out({"ok": True, "state": "running", "changes": changes})


def cmd_apply_run(a):
    try:
        git("add", STATE_NAME)  # flakes only see tracked files
        log("nixos-rebuild switch …")
        rebuild = shlex.split(os.environ.get("HELIOS_REBUILD", "nixos-rebuild switch"))
        profile = os.environ.get("HELIOS_SYSTEM_PROFILE", "/nix/var/nix/profiles/system")
        before = os.path.realpath(profile)
        with open(APPLY_LOG, "a") as f:
            r = subprocess.run(rebuild, stdout=f, stderr=subprocess.STDOUT, cwd=CONFIG_DIR)
        if r.returncode != 0 and os.path.realpath(profile) != before:
            # switch-to-configuration failed after the new generation was made the
            # default (e.g. a unit didn't start): it IS running, so it gets the
            # same confirm-or-roll-back window as a clean apply.
            log(f"Rebuild finished with errors (exit {r.returncode}), but the new configuration is active.")
        elif r.returncode != 0:
            log(f"Rebuild failed (exit {r.returncode}). Nothing was changed; fix or discard the pending changes.")
            git("reset", "-q", "--", STATE_NAME, check=False)
            set_apply(state="failed", finished=now(), message="Rebuild failed — see log")
            return
        log("Syncing AdGuard client profiles …")
        try:
            log(adguard_sync())
        except Exception as e:  # noqa: BLE001
            log(f"AdGuard sync skipped: {e}")
        try:
            log(printers_cleanup())
        except Exception as e:  # noqa: BLE001
            log(f"Printer cleanup skipped: {e}")
        deadline = now() + CONFIRM_SECONDS
        if not os.environ.get("HELIOS_NO_SYSTEMD"):
            run(["systemd-run", f"--on-active={CONFIRM_SECONDS}", "--unit=helios-autorollback", "--collect", "--quiet",
                 *self_exe(), "rollback", "--auto"])
        log(f"Applied. Waiting for confirmation (auto-rollback in {CONFIRM_SECONDS}s).")
        set_apply(state="confirm", deadline=deadline, message="Applied — confirm to keep")
    except Exception as e:  # noqa: BLE001
        log(f"Error: {e}")
        set_apply(state="failed", finished=now(), message=str(e))


def cmd_confirm(a):
    st = read_json(APPLY_STATUS, {}) or {}
    if st.get("state") != "confirm":
        fail("nothing waiting for confirmation")
    run(["systemctl", "stop", "helios-autorollback.timer"])
    changes = st.get("changes") or ["Update"]
    msg = changes[0] if len(changes) == 1 else f"{changes[0]} (+{len(changes) - 1} more)"
    git("add", STATE_NAME)
    git("commit", "-q", "-m", msg, "-m", "\n".join(changes), check=False)
    log("Confirmed and committed.")
    set_apply(state="done", finished=now(), deadline=None, message="Applied")
    out({"ok": True, "state": "done"})


def cmd_rollback(a):
    log("Rolling back to the previous system generation …" + (" (not confirmed in time)" if a.auto else ""))
    rb = shlex.split(os.environ.get("HELIOS_REBUILD", "nixos-rebuild switch")) + ["--rollback"]
    with open(APPLY_LOG, "a") as f:
        subprocess.run(rb, stdout=f, stderr=subprocess.STDOUT, cwd=CONFIG_DIR)
    git("checkout", "HEAD", "--", STATE_NAME, check=False)
    try:
        log(adguard_sync())
    except Exception as e:  # noqa: BLE001
        log(f"AdGuard sync skipped: {e}")
    try:
        log(printers_cleanup())
    except Exception as e:  # noqa: BLE001
        log(f"Printer cleanup skipped: {e}")
    set_apply(state="rolledback", finished=now(), deadline=None,
              message="Rolled back automatically" if a.auto else "Rolled back")
    if not a.auto:
        run(["systemctl", "stop", "helios-autorollback.timer"])
    out({"ok": True, "state": "rolledback"})


def cmd_apply_status(a):
    st = read_json(APPLY_STATUS, {}) or {}
    try:
        with open(APPLY_LOG) as f:
            lines = f.read().splitlines()
    except OSError:
        lines = []
    if st.get("deadline"):
        st["remaining"] = max(0, st["deadline"] - now())
    out({"ok": True, "apply": st, "log": lines[-300:]})


def cmd_apply_dismiss(a):
    st = read_json(APPLY_STATUS, {}) or {}
    if st.get("state") in ("running", "confirm"):
        fail("still in progress")
    write_json(APPLY_STATUS, {})
    out({"ok": True})


# --------------------------------------------------------------- AdGuard
def profile_settings(p):
    """AdGuard per-client settings for a profile object (pkgs/profiles.json format)."""
    if p.get("global"):
        return {"use_global_settings": True, "use_global_blocked_services": True}
    services = list(p.get("blockedServices") or [])
    ss, yt = bool(p.get("safeSearch")), bool(p.get("youtubeRestricted"))
    return {"use_global_settings": False,
            "use_global_blocked_services": not services, "blocked_services": services,
            "filtering_enabled": bool(p.get("filtering")), "parental_enabled": bool(p.get("parental")),
            "safebrowsing_enabled": bool(p.get("safeBrowsing")),
            "safe_search": {"enabled": ss or yt, "bing": ss, "duckduckgo": ss, "google": ss,
                            "pixabay": ss, "yandex": ss, "youtube": yt}}


def adguard_api(conf, path, body=None):
    req = urllib.request.Request(conf.get("url", "http://127.0.0.1:3000").rstrip("/") + path,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 method="POST" if body is not None else "GET")
    req.add_header("Content-Type", "application/json")
    if conf.get("user"):
        tok = base64.b64encode(f"{conf['user']}:{conf.get('password', '')}".encode()).decode()
        req.add_header("Authorization", "Basic " + tok)
    with urllib.request.urlopen(req, timeout=10) as r:
        data = r.read()
        return json.loads(data) if data.strip().startswith(b"{") else {}


def adguard_sync():
    conf = read_json(ADGUARD_CONF)
    if not conf:
        return f"no {ADGUARD_CONF} (url/user/password) — skipping"
    s = read_json(STATE, {}) or {}
    managed = set(read_json(ADGUARD_MANAGED, []) or [])
    existing = {c["name"]: c for c in (adguard_api(conf, "/control/clients").get("clients") or [])}
    profs = {x["name"]: x for x in state_profiles(s)}
    fallback = profs.get("default") or {"global": True}
    wanted = {}
    for d in s.get("devices", []):
        ids = [x for x in (d.get("ip"), d.get("mac")) if x]
        wanted[d["name"]] = {"name": d["name"], "ids": ids, "tags": [], "blocked_services": [], "upstreams": [],
                             **profile_settings(profs.get(d.get("profile", "default"), fallback))}
    # A network's default profile: one client for its whole subnet. AdGuard prefers
    # an exact IP/MAC client, so named devices keep their own profile.
    for n in (s.get("networks") or CATALOG.get("defaults", [])):
        if n.get("defaultProfile") in profs:
            wanted[NET_CLIENT_PREFIX + n["name"]] = {"name": NET_CLIENT_PREFIX + n["name"], "ids": [f"{n['prefix']}.0/24"], "tags": [],
                                                     "blocked_services": [], "upstreams": [],
                                                     **profile_settings(profs[n["defaultProfile"]])}
    added = updated = removed = 0
    for name, body in wanted.items():
        if name in existing:
            adguard_api(conf, "/control/clients/update", {"name": name, "data": body})
            updated += 1
        else:
            adguard_api(conf, "/control/clients/add", body)
            added += 1
    for name in managed - set(wanted):
        if name in existing:
            adguard_api(conf, "/control/clients/delete", {"name": name})
            removed += 1
    write_json(ADGUARD_MANAGED, sorted(wanted))
    return f"Magnetosphere (AdGuard): {added} added, {updated} updated, {removed} removed"


# ---------------------------------------------------------- reboots
# Most updates take effect with the rebuild. A new kernel (or its modules/initrd)
# only runs after a reboot: compare what's booted with what's installed.
BOOTED = os.environ.get("HELIOS_BOOTED_SYSTEM", "/run/booted-system")
CURRENT = os.environ.get("HELIOS_CURRENT_SYSTEM", "/run/current-system")
REBOOT_NOTICE = os.path.join(DATA_DIR, "reboot-notice.json")
DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
MAINT_DEFAULTS = {"rebootWindow": {"enabled": False, "days": ["sun"], "time": "04:00"}}


def _target(system, part):
    try:
        return os.path.realpath(os.path.join(p(system), part))
    except OSError:
        return None


def _kernel_version(path):
    m = re.search(r"-linux-([\d.]+[\w.-]*?)/", (path or "") + "/")
    return m.group(1) if m else None


def reboot_status():
    reasons = []
    for part, label in (("kernel", "kernel"), ("kernel-modules", "kernel modules"), ("initrd", "boot image")):
        b, c = _target(BOOTED, part), _target(CURRENT, part)
        if b and c and b != c:
            reasons.append(label)
    return {"needed": bool(reasons), "reasons": reasons,
            "running": _kernel_version(_target(BOOTED, "kernel")), "installed": _kernel_version(_target(CURRENT, "kernel"))}


def maint_settings(s):
    m = json.loads(json.dumps(MAINT_DEFAULTS))
    m["rebootWindow"].update(((s.get("maintenance") or {}).get("rebootWindow")) or {})
    return m


def next_window(w, t=None):
    """Next time the window opens (epoch), or None when it's off."""
    if not w.get("enabled") or not w.get("days"):
        return None
    hh, mm = map(int, w["time"].split(":"))
    base = dt.datetime.fromtimestamp(t or now())
    for i in range(8):
        d = (base + dt.timedelta(days=i)).replace(hour=hh, minute=mm, second=0, microsecond=0)
        if DAYS[d.weekday()] in w["days"] and d > base:
            return int(d.timestamp())
    return None


def cmd_reboot_status(a):
    s = load_state()
    m = maint_settings(s)
    out({"ok": True, **reboot_status(), "maintenance": m, "nextWindow": next_window(m["rebootWindow"])})


def cmd_maintenance_set(a):
    d = parse_json_arg(a.json)
    w = d.get("rebootWindow")
    if set(d) - {"rebootWindow"} or not isinstance(w, dict) or set(w) - {"enabled", "days", "time"}:
        fail("expected {rebootWindow: {enabled, days, time}}")
    if "enabled" in w and not isinstance(w["enabled"], bool):
        fail("enabled must be true or false")
    if "days" in w and (not isinstance(w["days"], list) or not w["days"] or any(x not in DAYS for x in w["days"])):
        fail("days: one or more of mon, tue, wed, thu, fri, sat, sun")
    if "time" in w and (not isinstance(w["time"], str) or not re.match(r"^([01][0-9]|2[0-3]):[0-5][0-9]\Z", w["time"])):
        fail("time must look like 04:00")
    with Lock():
        s = load_state()
        m = maint_settings(s)
        m["rebootWindow"].update(w)
        m["rebootWindow"]["days"] = [x for x in DAYS if x in m["rebootWindow"]["days"]]
        s["maintenance"] = m
        save_state(s)
    out({"ok": True, "maintenance": m, "nextWindow": next_window(m["rebootWindow"]), "dirty": True})


def _apply_busy():
    st = read_json(APPLY_STATUS, {}) or {}
    return st.get("state") in ("running", "confirm")


def cmd_reboot(a):
    """Reboot now (from Photosphere) — refused while a change is being applied/confirmed."""
    if _apply_busy():
        fail("a change is being applied: confirm or roll it back first")
    notify("aurora", "Rebooting", "The router is restarting now" + (f" to run kernel {reboot_status()['installed']}." if reboot_status()["needed"] else "."))
    run(["systemctl", "reboot", "--no-block"], timeout=15)
    out({"ok": True})


def cmd_reboot_window(a):
    """Timer at the reboot window: reboot only if needed and nothing is mid-apply."""
    rs = reboot_status()
    if not rs["needed"]:
        out({"ok": True, "action": "none", "reason": "no reboot needed"})
        return
    if _apply_busy():
        notify("aurora", "Scheduled reboot skipped", "A change was being applied; HeliOS will try at the next window.")
        out({"ok": True, "action": "skipped", "reason": "apply in progress"})
        return
    notify("aurora", "Rebooting for an update", f"Scheduled reboot: kernel {rs['running']} → {rs['installed']}. Back in a minute or two.")
    run(["systemctl", "reboot", "--no-block"], timeout=15)
    out({"ok": True, "action": "reboot"})


def cmd_reboot_check(a):
    """Daily: tell the owner once per new kernel that a reboot is waiting."""
    rs = reboot_status()
    if not rs["needed"]:
        out({"ok": True, "needed": False})
        return
    seen = read_json(REBOOT_NOTICE, {}) or {}
    key = rs["installed"] or ",".join(rs["reasons"])
    if seen.get("for") != key:
        m = maint_settings(read_json(STATE, {}) or {})
        nxt = next_window(m["rebootWindow"])
        when = f" It will reboot at {dt.datetime.fromtimestamp(nxt).strftime('%a %H:%M')}." if nxt else " Reboot when it suits you (Overview → Reboot now)."
        notify("aurora", "Reboot needed", f"Kernel {rs['installed']} is installed; the router still runs {rs['running']}.{when}")
        write_json(REBOOT_NOTICE, {"for": key, "at": now()})
    out({"ok": True, "needed": True, **rs})


# ------------------------------------------------------------- recovery
RECOVERY = CFG.get("recovery") or {}
RECOVERY_STATE = os.path.join(DATA_DIR, "recovery.json")
UNIT_RE = re.compile(r"^[A-Za-z0-9@_.:\\-]{1,200}\Z")


def _unit_label(unit):
    base = unit[:-8] if unit.endswith(".service") else unit
    return next((label for u, label in SERVICES if u in (base, unit)), base)


def _journal_tail(unit, n=15):
    r = run(["journalctl", "-u", unit, "-n", str(n), "--no-pager", "-o", "cat"], timeout=15)
    return (r.stdout or "").strip()[-1500:]


def cmd_unit_failed(a):
    """OnFailure= hook: a watched unit ended up failed. Urgent notification + record."""
    if not UNIT_RE.match(a.unit):
        fail("bad unit name")
    unit = a.unit if "." in a.unit else a.unit + ".service"
    st = read_json(RECOVERY_STATE, {}) or {}
    rec = st.get(unit) or {"failedAt": now(), "attempts": [], "escalated": False}
    rec.setdefault("failedAt", now())
    st[unit] = rec
    write_json(RECOVERY_STATE, st)
    tail = _journal_tail(unit)
    notify("flare", f"{_unit_label(unit)} stopped",
           f"{unit} failed and its automatic restarts didn't help. HeliOS will retry every "
           f"{RECOVERY.get('cooldownMin', 30)} min.\n\n{tail}" if tail else f"{unit} failed.", priority="high", tags="warning")
    out({"ok": True, "unit": unit})


def cmd_recover(a):
    """Timer: retry failed watched services (cooldown, daily cap), report recoveries, escalate once."""
    units = {x["unit"] + ("" if "." in x["unit"] else ".service"): x.get("restart", True) for x in RECOVERY.get("units", [])}
    if not units:
        out({"ok": True, "checked": 0})
        return
    cooldown, cap = RECOVERY.get("cooldownMin", 30) * 60, RECOVERY.get("maxPerDay", 6)
    live = unit_states(list(units))
    st = read_json(RECOVERY_STATE, {}) or {}
    done = {"retried": [], "recovered": [], "escalated": []}
    for unit, restartable in units.items():
        state = (live.get(unit) or {}).get("ActiveState", "unknown")
        rec = st.get(unit)
        if state == "active" and rec:
            notify("aurora", f"{_unit_label(unit)} is back", f"{unit} is running again.")
            del st[unit]
            done["recovered"].append(unit)
            continue
        if state != "failed":
            continue
        rec = rec or {"failedAt": now(), "attempts": [], "escalated": False}
        rec["attempts"] = [t for t in rec.get("attempts", []) if t > now() - 86400]
        if restartable and len(rec["attempts"]) < cap and (not rec["attempts"] or now() - rec["attempts"][-1] >= cooldown):
            run(["systemctl", "reset-failed", unit], timeout=30)
            run(["systemctl", "start", "--no-block", unit], timeout=30)
            rec["attempts"].append(now())
            done["retried"].append(unit)
        elif not rec.get("escalated") and (not restartable or len(rec["attempts"]) >= cap):
            notify("flare", f"{_unit_label(unit)} is still down",
                   f"{unit} has been down since {dt.datetime.fromtimestamp(rec['failedAt']).strftime('%H:%M %d %b')}"
                   + (f" and {len(rec['attempts'])} restarts didn't fix it." if restartable else ". It needs a person to look at it.")
                   + " Check Advanced → Services or the logs.\n\n" + _journal_tail(unit, 10), priority="urgent", tags="rotating_light")
            rec["escalated"] = True
            done["escalated"].append(unit)
        st[unit] = rec
    write_json(RECOVERY_STATE, st)
    out({"ok": True, "checked": len(units), **done})


# ------------------------------------------------------------ AI log triage
LW = CFG.get("logwatch") if isinstance(CFG.get("logwatch"), dict) else {}


def _lw_catalog():
    if LW.get("catalog"):
        return LW["catalog"]
    try:
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "logwatch-models.json")) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"presets": {}, "defaults": {}}


LW_CATALOG = _lw_catalog()
LLM_DIR = os.environ.get("HELIOS_LLM_DIR", "/var/lib/llm")
LLM_USER = "llm"                   # llama-cpp runs as this account (read-only view of the system)
LW_PAUSE = os.path.join(DATA_DIR, "logwatch-pause.json")
LW_DOWNLOAD = os.path.join(DATA_DIR, "logwatch-download.json")
LW_BACKENDS = ("site", "vulkan", "cpu", "remote")


def lw_settings(s):
    d = json.loads(json.dumps(LW_CATALOG.get("defaults", {})))
    for k, v in (s.get("logwatch") or {}).items():
        if k == "guard" and isinstance(v, dict):
            d.setdefault("guard", {}).update(v)
        else:
            d[k] = v
    return d


def lw_hardware():
    mem = meminfo().get("total") or 0
    cpus = os.cpu_count() or 1
    gpu = os.path.exists(p("/dev/dri/renderD128"))
    return {"memoryMB": int(mem / 2**20), "cpus": cpus, "gpu": gpu}


def lw_recommend(hw):
    """Largest preset that fits in half of (RAM − 2 GB); without a GPU, no bigger than
    medium unless there are 8+ cores (CPU-only inference of big models is slow)."""
    budget = max(0, (hw["memoryMB"] - 2048) // 2)
    order = ["tiny", "small", "medium", "large", "xl"]
    cap = order.index("xl") if hw["gpu"] or hw["cpus"] >= 8 else order.index("medium")
    best = None
    for i, k in enumerate(order):
        pr = LW_CATALOG.get("presets", {}).get(k)
        if pr and i <= cap and pr["ramMB"] <= budget:
            best = k
    return best


def lw_pressure():
    """PSI 'some avg60' for cpu and memory, in percent (None if the kernel lacks PSI)."""
    res = {}
    for kind in ("cpu", "memory"):
        m = re.search(r"some avg10=[\d.]+ avg60=([\d.]+)", read(f"/proc/pressure/{kind}"))
        res[kind] = float(m.group(1)) if m else None
    return res


def cmd_logwatch(a):
    s = load_state()
    st = lw_settings(s)
    hw = lw_hardware()
    files = {}
    try:
        for fn in os.listdir(LLM_DIR):
            if fn.endswith(".gguf"):
                files[fn] = os.path.getsize(os.path.join(LLM_DIR, fn))
    except OSError:
        # no models directory yet: nothing downloaded
        pass
    budget = max(0, (hw["memoryMB"] - 2048) // 2)
    presets = [{"id": k, **v, "downloaded": files.get(v["file"]) == v["bytes"], "fits": v["ramMB"] <= budget}
               for k, v in LW_CATALOG.get("presets", {}).items()]
    pause = read_json(LW_PAUSE) or None
    if pause and pause.get("until", 0) < now():
        pause = None
    units = unit_states(["logwatch.timer", "logwatch.service", "llama-cpp.service", "helios-ai-guard.timer"])
    out({"ok": True, "enabled": bool(LW.get("enabled", False)), "settings": st, "site": LW.get("site") or {},
         "hardware": hw, "recommended": lw_recommend(hw), "presets": presets, "files": files,
         "download": read_json(LW_DOWNLOAD) or None, "paused": pause, "pressure": lw_pressure(),
         "units": {u: (units.get(u) or {}).get("ActiveState", "unknown") for u in units} if units else {},
         "dirty": state_dirty()})


def cmd_logwatch_set(a):
    d = parse_json_arg(a.json)
    allowed = set(LW_CATALOG.get("defaults", {}))
    bad = set(d) - allowed
    if bad:
        fail(f"unknown setting {sorted(bad)[0]!r}")
    def num(k, lo, hi, v):
        if not isinstance(v, int) or isinstance(v, bool) or not lo <= v <= hi:
            fail(f"{k} must be a whole number from {lo} to {hi}")
    for k, v in d.items():
        if k == "model":
            if v not in list(LW_CATALOG.get("presets", {})) + ["custom", "site"]:
                fail("model must be a preset, custom or site")
        elif k == "customPath":
            # Only root can write /var/lib/llm (and the llm account only reads it), so a model
            # there can't be swapped by anyone else; a path elsewhere could be
            if v is not None and (not isinstance(v, str) or not re.fullmatch(r"/var/lib/llm/[A-Za-z0-9_-][A-Za-z0-9._-]*\.gguf", v)):
                fail("customPath must be a .gguf file directly in /var/lib/llm, like /var/lib/llm/my-model.gguf")
        elif k == "backend":
            if v not in LW_BACKENDS:
                fail(f"backend must be one of {', '.join(LW_BACKENDS)}")
        elif k == "interval":
            num(k, 5, 240, v)
        elif k == "threads":
            num(k, 1, 64, v)
        elif k == "cpuPercent":
            num(k, 25, 6400, v)
        elif k == "memoryMB":
            if v is not None:
                num(k, 512, 262144, v)
        elif k == "keepLoaded":
            if not isinstance(v, bool):
                fail("keepLoaded must be true or false")
        elif k == "guard":
            if not isinstance(v, dict) or set(v) - {"enabled", "cpuPressure", "memoryPressure", "pauseHours"}:
                fail("guard takes enabled, cpuPressure, memoryPressure and pauseHours")
            if "enabled" in v and not isinstance(v["enabled"], bool):
                fail("guard.enabled must be true or false")
            for gk, lo, hi in (("cpuPressure", 5, 95), ("memoryPressure", 5, 95), ("pauseHours", 1, 72)):
                if gk in v:
                    num(f"guard.{gk}", lo, hi, v[gk])
    with Lock():
        s = load_state()
        cur = dict(s.get("logwatch") or {})
        for k, v in d.items():
            if k == "guard":
                cur["guard"] = {**(cur.get("guard") or {}), **v}
            else:
                cur[k] = v
        st = {**lw_settings({}), **cur}
        if st.get("model") == "custom" and not st.get("customPath"):
            fail("a custom model needs customPath")
        s["logwatch"] = cur
        save_state(s)
    out({"ok": True, "settings": lw_settings(s), "dirty": True})


def cmd_logwatch_reset(a):
    with Lock():
        s = load_state()
        s.pop("logwatch", None)
        save_state(s)
    out({"ok": True, "settings": lw_settings(s), "dirty": True})


def cmd_logwatch_model_get(a):
    """Start a background download (a transient systemd unit, so it survives the page)."""
    pr = LW_CATALOG.get("presets", {}).get(a.preset)
    if not pr:
        fail(f"no preset {a.preset!r}")
    os.makedirs(LLM_DIR, mode=0o700, exist_ok=True)
    # The module's tmpfiles rule gives the folder its owner and mode (root:llm 0750: llama-cpp
    # reads it, can't write it); apply it now rather than at the next boot
    if os.geteuid() == 0 and not os.environ.get("HELIOS_NO_SYSTEMD"):
        run(["systemd-tmpfiles", "--create", "--prefix=" + LLM_DIR])
    free = shutil.disk_usage(LLM_DIR).free
    if free < pr["bytes"] + 512 * 2**20:
        fail(f"not enough space in {LLM_DIR}: needs {pr['bytes'] // 2**20} MB, {free // 2**20} MB free")
    st = read_json(LW_DOWNLOAD) or {}
    if st.get("state") == "running" and st.get("pid") and os.path.exists(f"/proc/{st['pid']}"):
        fail(f"already downloading {st.get('preset')}")
    if os.environ.get("HELIOS_NO_SYSTEMD"):
        cmd_logwatch_model_fetch(a)
        return
    r = run(["systemd-run", "--unit", "helios-model-download", "--collect", "--property=Slice=helios-ai.slice",
             "--property=Nice=19", os.environ.get("HELIOS_SELF", sys.argv[0]), "logwatch-model-fetch", a.preset])
    if r.returncode != 0:
        fail((r.stderr or "couldn't start the download").strip()[:200])
    out({"ok": True, "started": a.preset})


def cmd_logwatch_model_fetch(a):
    """Download a preset over HTTPS to a temp file, check its pinned sha256, then move it in place."""
    import hashlib
    pr = LW_CATALOG["presets"][a.preset]
    url = f"{LW_CATALOG.get('baseUrl', 'https://huggingface.co')}/{pr['repo']}/resolve/main/{pr['file']}"
    dest = os.path.join(LLM_DIR, pr["file"])
    tmp = dest + ".part"
    status = {"preset": a.preset, "file": pr["file"], "bytes": pr["bytes"], "done": 0, "state": "running", "pid": os.getpid(), "started": now()}
    write_json(LW_DOWNLOAD, status)
    h = hashlib.sha256()
    try:
        with urllib.request.urlopen(os.environ.get("HELIOS_MODEL_URL", url), timeout=60) as r, open(tmp, "wb") as f:
            last = 0
            while True:
                b = r.read(1 << 20)
                if not b:
                    break
                f.write(b)
                h.update(b)
                status["done"] += len(b)
                if now() - last >= 2:
                    write_json(LW_DOWNLOAD, status)
                    last = now()
        if h.hexdigest() != pr["sha256"]:
            raise RuntimeError("checksum mismatch: the file isn't the published model; deleted it")
        os.chmod(tmp, 0o400)
        give_to(tmp, LLM_USER)
        os.replace(tmp, dest)
        status.update(state="done", finished=now())
    except Exception as e:  # noqa: BLE001
        try:
            os.remove(tmp)
        except OSError:
            # best effort: the temp file may not exist
            pass
        status.update(state="failed", error=str(e)[:200], finished=now())
    write_json(LW_DOWNLOAD, status)
    out({"ok": status["state"] == "done", **({"error": status.get("error")} if status["state"] != "done" else {}), "download": status})


def cmd_logwatch_model_remove(a):
    if "/" in a.file or not a.file.endswith(".gguf"):
        fail("give a .gguf file name from the list")
    s = load_state()
    st = lw_settings(s)
    in_use = LW_CATALOG.get("presets", {}).get(st.get("model"), {}).get("file") == a.file
    if in_use and lw_settings(s).get("model") != "site" and LW.get("enabled"):
        fail("that's the model AI log triage uses: pick another one first")
    try:
        os.remove(os.path.join(LLM_DIR, a.file))
    except FileNotFoundError:
        fail(f"no {a.file}")
    out({"ok": True})


def cmd_logwatch_guard(a):
    """--check: exit 1 while paused (used as ExecCondition). Otherwise: pause AI triage
    when CPU or memory pressure says routing is waiting, resume when the pause ends."""
    pause = read_json(LW_PAUSE) or {}
    if a.check:
        sys.exit(1 if pause.get("until", 0) > now() else 0)
    st = lw_settings(read_json(STATE, {}) or {})
    g = st.get("guard") or {}
    if pause and pause.get("until", 0) <= now():
        os.remove(p(LW_PAUSE)) if os.path.exists(p(LW_PAUSE)) else None
        notify("aurora", "AI log triage resumed", "The router has been calm; log triage is running again.")
        out({"ok": True, "action": "resumed"})
        return
    if pause or not g.get("enabled", True):
        out({"ok": True, "action": "none"})
        return
    pr = lw_pressure()
    over = [f"{k} pressure {v:.0f}%" for k, v, lim in (("CPU", pr["cpu"], g.get("cpuPressure", 40)), ("memory", pr["memory"], g.get("memoryPressure", 20)))
            if v is not None and v >= lim]
    if not over:
        out({"ok": True, "action": "none", "pressure": pr})
        return
    hours = g.get("pauseHours", 6)
    write_json(LW_PAUSE, {"since": now(), "until": now() + hours * 3600, "reason": ", ".join(over)})
    run(["systemctl", "stop", "logwatch.service", "llama-cpp.service", "helios-model-download.service"], timeout=30)
    notify("aurora", "AI log triage paused", f"The router was busy ({', '.join(over)}), so log triage stops for {hours} h to keep routing smooth.")
    out({"ok": True, "action": "paused", "reason": ", ".join(over), "hours": hours})


def cmd_logwatch_resume(a):
    try:
        os.remove(p(LW_PAUSE))
    except FileNotFoundError:
        # not paused
        pass
    out({"ok": True})


# ------------------------------------------------------------- services
def _service_catalog():
    if CFG.get("serviceCatalog"):
        return CFG["serviceCatalog"]
    try:
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "services.json")) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"core": [], "services": {}, "elsewhere": []}


SVC_CATALOG = _service_catalog()


def service_states(s):
    """Each switchable service: site.nix default, Photosphere override, and what applies."""
    defaults = CFG.get("serviceDefaults") or {}
    over = s.get("services") or {}
    res = {}
    for sid, meta in SVC_CATALOG.get("services", {}).items():
        dflt = defaults.get(sid, meta.get("group") == "default")
        o = (over.get(sid) or {}).get("enabled")
        res[sid] = {"default": dflt, "override": o, "enabled": dflt if o is None else o}
    return res


def cmd_services(a):
    s = load_state()
    st = service_states(s)
    units = sorted({u for c in SVC_CATALOG.get("core", []) for u in c.get("units", [])}
                   | {u for m in SVC_CATALOG.get("services", {}).values() for u in m.get("units", [])})
    live = unit_states(units)
    state_of = lambda u: (live.get(u if "." in u else u + ".service") or live.get(u) or {}).get("ActiveState", "unknown")
    out({"ok": True,
         "core": [{**c, "units": {u: state_of(u) for u in c.get("units", [])}} for c in SVC_CATALOG.get("core", [])],
         "services": [{"id": sid, **meta, **st[sid], "units": {u: state_of(u) for u in meta.get("units", [])}}
                      for sid, meta in SVC_CATALOG.get("services", {}).items()],
         "elsewhere": SVC_CATALOG.get("elsewhere", []), "dirty": state_dirty()})


def cmd_service_set(a):
    if a.id not in SVC_CATALOG.get("services", {}):
        fail(f"unknown service {a.id!r} (core services can't be switched off)")
    with Lock():
        s = load_state()
        over = s.setdefault("services", {})
        if a.state == "default":
            over.pop(a.id, None)
        else:
            over[a.id] = {"enabled": a.state == "on"}
        if not over:
            s.pop("services")
        save_state(s)
    out({"ok": True, "service": {"id": a.id, **service_states(s)[a.id]}, "dirty": True})


def describe_services(old, new):
    o, n = service_states(old), service_states(new)
    msgs = []
    for sid, x in n.items():
        label = SVC_CATALOG["services"][sid]["label"]
        if x["enabled"] != o[sid]["enabled"]:
            msgs.append(f"{'Turn on' if x['enabled'] else 'Turn off'} {label}")
        elif x["override"] != o[sid]["override"]:
            msgs.append(f"{label}: follow site.nix" if x["override"] is None else f"{label}: pin {'on' if x['enabled'] else 'off'}")
    return msgs


# ------------------------------------------------------------- printing
PRN = CFG.get("printing") if isinstance(CFG.get("printing"), dict) else {}
PRN_CATALOG = PRN.get("catalog") or {}
if not PRN_CATALOG:
    try:
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "printing.json")) as _f:
            PRN_CATALOG = json.load(_f)["drivers"]
    except (OSError, ValueError, KeyError):
        PRN_CATALOG = {}
PRINTERS_MANAGED = os.path.join(DATA_DIR, "printers-managed.json")
PRINTER_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}\Z")
URI_RE = re.compile(r"^(usb|ipp|ipps|dnssd|socket|lpd|http|https|hp|smb)://\S{1,500}\Z")
MODEL_RE = re.compile(r"^\S{1,300}\Z")


def printing_state(s):
    return {"drivers": [], "printers": [], **(s.get("printing") or {})}


def _lines(cmd):
    r = run(cmd, timeout=20)
    return r.stdout.splitlines() if r.returncode == 0 else None


def cups_status():
    """Live CUPS view: {printers: {name: {state, job}}, default, jobs: [...], uris: {name: uri}} or None."""
    lp = _lines(["lpstat", "-p"])
    if lp is None:
        return None
    printers = {}
    for line in lp:
        m = re.match(r"^printer (\S+) (?:is (idle)|now printing (\S+)|disabled)", line)
        if m:
            printers[m.group(1)] = {"state": "idle" if m.group(2) else "printing" if m.group(3) else "paused", "job": m.group(3)}
    dflt = next((l.split(":", 1)[1].strip() for l in (_lines(["lpstat", "-d"]) or []) if ":" in l), None)
    jobs = []
    for line in _lines(["lpstat", "-o"]) or []:
        f = line.split()
        if len(f) >= 3 and "-" in f[0]:
            name, _, jid = f[0].rpartition("-")
            jobs.append({"id": f[0], "printer": name, "user": f[1], "size": int(f[2]) if f[2].isdigit() else None,
                         "submitted": " ".join(f[3:])})
    return {"printers": printers, "default": dflt, "jobs": jobs}


def discover_printers():
    """Connected and network printers CUPS can see (lpinfo -v), marked driverless when they speak IPP Everywhere."""
    found = []
    for line in _lines(["lpinfo", "-v"]) or []:
        kind, _, uri = line.partition(" ")
        if "://" not in uri:
            continue
        label = urllib.parse.unquote(uri.split("://", 1)[1].split("?", 1)[0]).replace("/", " ")
        found.append({"uri": uri, "kind": kind, "label": label.strip()})
    everywhere = set(_lines(["driverless"]) or [])
    for x in found:
        x["driverless"] = x["uri"] in everywhere
    for uri in everywhere - {x["uri"] for x in found}:
        if "://" in uri:
            found.append({"uri": uri, "kind": "network", "label": urllib.parse.unquote(uri.split("://", 1)[1].split("/", 1)[0]), "driverless": True})
    return found


def cmd_printing(a):
    s = load_state()
    ps = printing_state(s)
    live = cups_status()
    used = {x["deviceUri"] for x in ps["printers"]}
    printers = []
    for x in ps["printers"]:
        lv = (live or {}).get("printers", {}).get(x["name"]) if live else None
        printers.append({**x, "live": lv, "jobs": [j for j in (live or {}).get("jobs", []) if j["printer"] == x["name"]]})
    out({"ok": True, "enabled": bool(PRN.get("enabled", True)), "running": live is not None,
         "printers": printers, "jobs": (live or {}).get("jobs", []),
         "discovered": [d for d in discover_printers() if d["uri"] not in used] if live is not None else [],
         "drivers": [{"id": k, **v, "enabled": k in ps["drivers"]} for k, v in PRN_CATALOG.items()],
         "siteDrivers": PRN.get("siteDrivers", []),
         "networks": [n["name"] for n in (s.get("networks") or CATALOG.get("defaults", [])) if resolve_access(n).get("printing")],
         "dirty": state_dirty()})


def cmd_printer_models(a):
    q = (a.query or "").lower()
    res = [{"model": "everywhere", "desc": "Driverless (IPP Everywhere / AirPrint) — best for most printers made since ~2015"}]
    for line in _lines(["lpinfo", "-m"]) or []:
        model, _, desc = line.partition(" ")
        if model and (not q or q in desc.lower() or q in model.lower()):
            res.append({"model": model, "desc": desc.strip()})
        if len(res) > 150:
            break
    out({"ok": True, "models": res})


def cmd_printer_set(a):
    d = parse_json_arg(a.json)
    name = d.get("name")
    if not isinstance(name, str) or not PRINTER_NAME_RE.match(name):
        fail("printer name: letters, digits, - and _ (max 64), no spaces")
    uri, model = d.get("deviceUri"), d.get("model")
    if not isinstance(uri, str) or not URI_RE.match(uri):
        fail("connection must be a printer address like usb://…, ipp://…, dnssd://… or socket://…")
    if not isinstance(model, str) or not MODEL_RE.match(model):
        fail("pick a driver (or “everywhere” for driverless)")
    new = {"name": name, "deviceUri": uri, "model": model,
           "description": str(d.get("description") or name)[:127], "location": str(d.get("location") or "")[:127]}
    if d.get("default"):
        new["default"] = True
    frm = d.get("from")
    with Lock():
        s = load_state()
        ps = printing_state(s)
        old = next((x for x in ps["printers"] if x["name"] == frm), None) if frm else None
        if frm and old is None:
            fail(f"no printer {frm!r}")
        if any(x["name"] == name and x is not old for x in ps["printers"]):
            fail(f"there is already a printer {name!r}")
        if new.get("default"):
            for x in ps["printers"]:
                x.pop("default", None)
        if old is None:
            ps["printers"].append(new)
        else:
            ps["printers"][ps["printers"].index(old)] = new
        s["printing"] = ps
        save_state(s)
    out({"ok": True, "printer": new, "dirty": True})


def cmd_printer_remove(a):
    with Lock():
        s = load_state()
        ps = printing_state(s)
        if not any(x["name"] == a.name for x in ps["printers"]):
            fail(f"no printer {a.name!r}")
        ps["printers"] = [x for x in ps["printers"] if x["name"] != a.name]
        s["printing"] = ps
        save_state(s)
    out({"ok": True, "dirty": True})


def cmd_printing_drivers(a):
    try:
        ids = json.loads(a.json)
    except ValueError:
        fail("invalid JSON")
    if not isinstance(ids, list) or any(x not in PRN_CATALOG for x in ids):
        fail(f"drivers must be a list of: {', '.join(PRN_CATALOG)}")
    with Lock():
        s = load_state()
        ps = printing_state(s)
        ps["drivers"] = sorted(set(ids))
        s["printing"] = ps
        save_state(s)
    out({"ok": True, "drivers": ps["drivers"], "dirty": True})


def _live_printer(name):
    if not PRINTER_NAME_RE.match(name or ""):
        fail("bad printer name")
    if not any(x["name"] == name for x in printing_state(load_state())["printers"]):
        fail(f"no printer {name!r} (apply first if you just added it)")


def cmd_printer_test(a):
    _live_printer(a.name)
    page = PRN.get("testPage") or "/run/current-system/sw/share/cups/data/testprint"
    r = run(["lp", "-d", a.name, "-t", "HeliOS test page", page], timeout=20)
    if r.returncode != 0:
        fail((r.stderr or "lp failed").strip()[:200])
    m = re.search(r"request id is (\S+)", r.stdout or "")
    out({"ok": True, "job": m.group(1) if m else None})


def cmd_printer_pause(a):
    _live_printer(a.name)
    r = run(["cupsdisable" if a.state == "on" else "cupsenable", a.name], timeout=20)
    if r.returncode != 0:
        fail((r.stderr or "failed").strip()[:200])
    out({"ok": True})


def cmd_print_cancel(a):
    if not re.match(r"^[A-Za-z0-9_-]{1,64}-[0-9]{1,9}\Z", a.job):
        fail("bad job id")
    r = run(["cancel", a.job], timeout=20)
    if r.returncode != 0:
        fail((r.stderr or "cancel failed").strip()[:200])
    out({"ok": True})


def printers_cleanup():
    """After an apply or rollback: delete CUPS printers HeliOS made that state.json no longer has.
    (NixOS's ensurePrinters creates and updates printers but never removes them.)"""
    s = read_json(STATE, {}) or {}
    want = {x["name"] for x in printing_state(s)["printers"]}
    managed = set(read_json(PRINTERS_MANAGED, []) or [])
    gone = sorted(managed - want)
    for name in gone:
        run(["lpadmin", "-x", name], timeout=20)
    write_json(PRINTERS_MANAGED, sorted(want))
    return f"Printers: {len(gone)} removed" if gone else "Printers: up to date"


def describe_printing(old, new):
    o, n = printing_state(old), printing_state(new)
    op, np_ = {x["name"]: x for x in o["printers"]}, {x["name"]: x for x in n["printers"]}
    msgs = [f"Add printer {k}" for k in np_ if k not in op]
    msgs += [f"Edit printer {k}" for k in np_ if k in op and op[k] != np_[k]]
    msgs += [f"Remove printer {k}" for k in op if k not in np_]
    added, dropped = set(n["drivers"]) - set(o["drivers"]), set(o["drivers"]) - set(n["drivers"])
    if added or dropped:
        msgs.append("Printer drivers: " + ", ".join([f"+{x}" for x in sorted(added)] + [f"−{x}" for x in sorted(dropped)]))
    return msgs


# ------------------------------------------------------------ profiles
PROFILE_NAME_RE = re.compile(r"^[a-z][a-z0-9-]{0,31}\Z")
SERVICE_RE = re.compile(r"^[a-z0-9_]{1,64}\Z")


def adguard_status():
    """{reachable, version, services: [{id, name}] | None}; never raises."""
    conf = read_json(ADGUARD_CONF)
    if not conf:
        return {"reachable": None, "configured": False, "version": None, "services": None}
    try:
        st = adguard_api(conf, "/control/status")
    except Exception:  # noqa: BLE001
        return {"reachable": False, "configured": True, "version": None, "services": None}
    services = None
    try:
        allsvc = adguard_api(conf, "/control/blocked_services/all")
        services = [{"id": x["id"], "name": x.get("name", x["id"])} for x in allsvc.get("blocked_services", []) if isinstance(x, dict) and "id" in x]
    except Exception:  # noqa: BLE001
        pass
    return {"reachable": True, "configured": True, "version": st.get("version"), "services": services,
            "protection": st.get("protection_enabled")}


def cmd_profiles(a):
    s = load_state()
    use = {}
    for d in s["devices"]:
        use.setdefault(d.get("profile", "default"), []).append(d["name"])
    nets = {}
    for n in (s.get("networks") or CATALOG.get("defaults", [])):
        if n.get("defaultProfile"):
            nets.setdefault(n["defaultProfile"], []).append(n["name"])
    out({"ok": True, "profiles": [{**p, "devices": use.get(p["name"], []), "networks": nets.get(p["name"], [])} for p in state_profiles(s)],
         "settings": PROFILE_CATALOG.get("settings", {}), "adguard": adguard_status(), "dirty": state_dirty()})


def cmd_profile_set(a):
    d = parse_json_arg(a.json)
    name = d.get("name")
    if not isinstance(name, str) or not PROFILE_NAME_RE.match(name):
        fail("profile id: lowercase letters, digits and dashes, starting with a letter (max 32)")
    label = d.get("label") or name
    if not isinstance(label, str) or len(label) > 32:
        fail("label: up to 32 characters")
    desc = d.get("desc") or ""
    if not isinstance(desc, str) or len(desc) > 160:
        fail("description: up to 160 characters")
    new = {"name": name, "label": label.strip(), "desc": desc.strip()}
    for k in PROFILE_KEYS:
        v = d.get(k, False)
        if not isinstance(v, bool):
            fail(f"{k} must be true or false")
        new[k] = v
    svc = d.get("blockedServices") or []
    if not isinstance(svc, list) or len(svc) > 100 or not all(isinstance(x, str) and SERVICE_RE.match(x) for x in svc):
        fail("blockedServices: a list of AdGuard service ids (up to 100)")
    new["blockedServices"] = sorted(set(svc))
    frm = d.get("from")
    with Lock():
        s = load_state()
        profs = state_profiles(s)
        # Editing names the profile in "from"; without it this is a new profile
        old = next((x for x in profs if x["name"] == frm), None) if frm else None
        if frm and old is None:
            fail(f"no profile {frm!r}")
        if any(x["name"] == name and x is not old for x in profs):
            fail(f"there is already a profile {name!r}")
        if old is None:
            if len(profs) >= 32:
                fail("at most 32 profiles")
            profs.append(new)
        else:
            profs[profs.index(old)] = new
            if old["name"] != name:      # renamed: devices and networks follow
                for dev in s["devices"]:
                    if dev.get("profile") == old["name"]:
                        dev["profile"] = name
                for n in state_networks(s):
                    if n.get("defaultProfile") == old["name"]:
                        n["defaultProfile"] = name
        s["profiles"] = profs
        save_state(s)
    out({"ok": True, "profile": new, "dirty": True})


def cmd_profile_remove(a):
    with Lock():
        s = load_state()
        profs = state_profiles(s)
        if not any(x["name"] == a.name for x in profs):
            fail(f"no profile {a.name!r}")
        devs = [d["name"] for d in s["devices"] if d.get("profile", "default") == a.name]
        nets = [n["name"] for n in (s.get("networks") or CATALOG.get("defaults", [])) if n.get("defaultProfile") == a.name]
        if devs or nets:
            who = ", ".join(devs[:5] + [f"network {x}" for x in nets]) + ("…" if len(devs) > 5 else "")
            fail(f"still in use by {who}: pick another profile for them first")
        if len(profs) == 1:
            fail("keep at least one profile")
        s["profiles"] = [x for x in profs if x["name"] != a.name]
        save_state(s)
    out({"ok": True, "dirty": True})


def describe_profiles(old, new):
    o = {x["name"]: x for x in state_profiles(old)}
    n = {x["name"]: x for x in state_profiles(new)}
    msgs = [f"Add filtering profile {k}" for k in n if k not in o]
    msgs += [f"Edit filtering profile {k}" for k in n if k in o and o[k] != n[k]]
    msgs += [f"Remove filtering profile {k}" for k in o if k not in n]
    return msgs


def cmd_adguard_sync(a):
    try:
        out({"ok": True, "message": adguard_sync()})
    except Exception as e:  # noqa: BLE001
        fail(f"AdGuard sync failed: {e}")


# ---------------------------------------------------------------- status
def unit_states(units):
    r = run(["systemctl", "show", "--no-pager", "-p", "Id,ActiveState,SubState,LoadState", *units])
    res, cur = {}, {}
    for line in (r.stdout or "").splitlines() + [""]:
        if not line.strip():
            if cur.get("Id"):
                res[cur["Id"]] = cur
            cur = {}
            continue
        k, _, v = line.partition("=")
        cur[k] = v
    return res


def wan_info():
    addrs = run_json(["ip", "-j", "addr", "show", "dev", "wan"], default=[]) or []
    v4 = v6 = None
    up = False
    for a in addrs:
        up = "UP" in (a.get("flags") or [])
        for ai in a.get("addr_info", []):
            if ai.get("family") == "inet" and not v4:
                v4 = f"{ai['local']}/{ai['prefixlen']}"
            if ai.get("family") == "inet6" and ai.get("scope") == "global" and not v6:
                v6 = f"{ai['local']}/{ai['prefixlen']}"
    routes = run_json(["ip", "-j", "route", "show", "default"], default=[]) or []
    gw = next((r.get("gateway") for r in routes if r.get("dev") == "wan"), None)
    return {"ifname": "wan", "up": up, "ipv4": v4, "ipv6": v6, "gateway": gw}


def meminfo():
    m = {}
    for line in read("/proc/meminfo").splitlines():
        k, _, v = line.partition(":")
        m[k] = int(v.split()[0]) * 1024 if v.split() else 0
    total = m.get("MemTotal", 0)
    avail = m.get("MemAvailable", 0)
    return {"total": total, "used": total - avail}


def cmd_status(a):
    git_ready()
    s = load_state()
    leases = parse_leases()
    counts = {v: 0 for v in VLANS}
    for l in leases:
        if l["vlan"] in counts:
            counts[l["vlan"]] += 1
    units = unit_states([u for u, _ in SERVICES])
    services = []
    for u, label in SERVICES:
        st = units.get(u if "." in u else u + ".service") or units.get(u) or {}
        services.append({"unit": u, "label": label, "active": st.get("ActiveState", "unknown"),
                         "sub": st.get("SubState", ""), "loaded": st.get("LoadState", "") == "loaded"})
    up = read("/proc/uptime").split()
    load = read("/proc/loadavg").split()
    ts = run_json(["tailscale", "status", "--json"], default={}) or {}
    self_ = ts.get("Self") or {}
    peers = ts.get("Peer") or {}
    uplink = read_json(UPLINK, {}) or {}
    outs = read_outages()
    known_macs = {d["mac"].lower() for d in s["devices"]}
    out({
        "ok": True,
        "hostname": CFG.get("hostName") or socket.gethostname(),
        "domain": CFG.get("domain"),
        "reservedNames": CFG.get("reservedNames", []),
        "solarWind": {"enabled": bool(SW.get("enable")), "exposure": SW.get("exposure"), "url": sw_public_url(),
                      "configured": os.path.exists(SW_ENV)},
        "uptime": float(up[0]) if up else None,
        "load": [float(x) for x in load[:3]] if load else [],
        "memory": meminfo(),
        "wan": wan_info(),
        "uplink": {"state": uplink.get("state", "unknown"), "since": uplink.get("since"),
                   "last_outage": outs[-1] if outs else None, "outages_24h": sum(1 for o in outs if o["down_at"] > now() - 86400)},
        "vlans": [{"name": k, "id": v["id"], "gateway": f"{v['net']}.1", "clients": counts[k]} for k, v in VLANS.items()],
        "devices": {"total": len(leases), "new": sum(1 for l in leases if l["mac"] not in known_macs),
                    "named": len(s["devices"]), "blocked": sum(1 for d in s["devices"] if d.get("blocked"))},
        "services": services,
        "qos": s["qos"],
        "upnp": {**s["upnp"], "mappings": len(parse_upnp())},
        "tailscale": {"online": self_.get("Online"), "name": self_.get("HostName"),
                      "ips": self_.get("TailscaleIPs") or [],
                      "peers_online": sum(1 for x in peers.values() if x.get("Online")),
                      "peers": len(peers)},
        "pending": state_dirty(),
        "apply": read_json(APPLY_STATUS, {}) or {},
    })


# --------------------------------------------------------------- traffic
IFACES = CFG.get("ifaces") or ["wan", *VLANS, "trunk0"]


def cmd_counters(a):
    res = {}
    for i in IFACES:
        base = f"/sys/class/net/{i}/statistics/"
        rx, tx = read(base + "rx_bytes").strip(), read(base + "tx_bytes").strip()
        if rx and tx:
            res[i] = {"rx": int(rx), "tx": int(tx)}
    out({"ok": True, "t": time.time(), "ifaces": res})


def cmd_vnstat(a):
    data = run_json(["vnstat", "--json"], default=None)
    if data is None:
        fail("vnstat has no data yet (it needs a few minutes after first start)")
    res = []
    for it in data.get("interfaces", []):
        name = it.get("name")
        if name not in IFACES:
            continue
        tr = it.get("traffic", {})
        res.append({"name": name,
                    "total": tr.get("total", {}),
                    "days": (tr.get("day") or [])[-14:],
                    "months": (tr.get("month") or [])[-12:]})
    out({"ok": True, "interfaces": res})


# ------------------------------------------------------------------- QoS
def cake_stats(dev):
    data = run_json(["tc", "-s", "-j", "qdisc", "show", "dev", dev], default=[]) or []
    for q in data:
        if q.get("kind") == "cake":
            opts = q.get("options", {})
            tins = []
            for t in (q.get("tins") or []):
                tins.append({k: t.get(k) for k in ("threshold_rate", "sent_bytes", "sent_packets", "drops", "ecn_mark",
                                                   "ack_drops", "peak_delay_us", "avg_delay_us", "base_delay_us",
                                                   "sparse_flows", "bulk_flows", "backlog_bytes")})
            return {"dev": dev, "bandwidth": opts.get("bandwidth"), "diffserv": opts.get("diffserv"),
                    "sent_bytes": q.get("bytes"), "drops": q.get("drops"), "backlog": q.get("backlog"),
                    "memory_used": q.get("memory_used"), "tins": tins}
    return None


# QoS settings (modules/voyager.nix reads the same keys and defaults)
QOS_DEFAULTS = {"enabled": False, "down": None, "up": None, "algorithm": "cake", "link": "ethernet",
                "overhead": 38, "mpu": 84, "atm": False, "fairness": "device", "priority": "diffserv3",
                "ackFilter": False, "rtt": "internet", "wash": True}
QOS_CHOICES = {"algorithm": ("cake", "fq_codel"), "link": ("docsis", "ethernet", "vdsl", "adsl", "custom"),
               "fairness": ("device", "triple", "flow"), "priority": ("diffserv4", "diffserv3", "besteffort"),
               "rtt": ("internet", "regional", "metro")}
RATE_RE = re.compile(r"^[0-9]+(\.[0-9]+)?(kbit|mbit|gbit)\Z")   # [0-9], not \d: no other scripts' digits
MODEM_URL = os.environ.get("HELIOS_MODEM_URL", "http://192.168.100.1/")   # cable modems answer here


def qos_resolve(s):
    return {**QOS_DEFAULTS, **(s.get("qos") or {})}


def cmd_qos(a):
    s = load_state()
    out({"ok": True, "settings": qos_resolve(s), "choices": QOS_CHOICES,
         "upload": cake_stats("wan"), "download": cake_stats("ifb4wan")})


def cmd_qos_set(a):
    d = parse_json_arg(a.json)
    unknown = set(d) - set(QOS_DEFAULTS)
    if unknown:
        fail(f"unknown QoS setting {sorted(unknown)[0]!r}")
    with Lock():
        s = load_state()
        q = qos_resolve(s)
        for k, v in d.items():
            if k in QOS_CHOICES:
                if v not in QOS_CHOICES[k]:
                    fail(f"{k} must be one of {', '.join(QOS_CHOICES[k])}")
            elif k in ("enabled", "atm", "ackFilter", "wash"):
                if not isinstance(v, bool):
                    fail(f"{k} must be true or false")
            elif k in ("down", "up"):
                if v is not None and (not isinstance(v, str) or not RATE_RE.match(v)):
                    fail(f"{k} rate must look like 500mbit or 50mbit")
            elif k in ("overhead", "mpu"):
                if not isinstance(v, int) or isinstance(v, bool) or not (-64 <= v <= 256 if k == "overhead" else 0 <= v <= 256):
                    fail(f"{k} must be a number of bytes (overhead -64 to 256, mpu 0 to 256)")
            q[k] = v
        if q["enabled"] and (not q["down"] or not q["up"]):
            fail("set both rates before turning QoS on (Suggest settings can measure them)")
        s["qos"] = q
        save_state(s)
    out({"ok": True, "settings": q, "dirty": True})


def probe_cable_modem():
    """Cable (DOCSIS) modems answer on 192.168.100.1 from the customer side."""
    try:
        with urllib.request.urlopen(urllib.request.Request(MODEM_URL, method="GET"), timeout=2) as r:
            return r.status < 500
    except urllib.error.HTTPError as e:
        return e.code < 500
    except Exception:  # noqa: BLE001
        return False


def _transfer(url, seconds, upload=False, streams=4):
    """Bytes moved in parallel streams within `seconds` (download, or POST uploads)."""
    import threading
    total, stop, lock = [0], time.time() + seconds, threading.Lock()
    chunk = os.urandom(1 << 20)

    def worker():
        while time.time() < stop:
            try:
                if upload:
                    body = chunk * 8
                    urllib.request.urlopen(urllib.request.Request(url, data=body, method="POST"), timeout=seconds + 5).read()
                    with lock:
                        total[0] += len(body)
                else:
                    with urllib.request.urlopen(url, timeout=seconds + 5) as r:
                        while time.time() < stop:
                            b = r.read(1 << 16)
                            if not b:
                                break
                            with lock:
                                total[0] += len(b)
            except Exception:  # noqa: BLE001
                return
    start = time.time()
    ts = [threading.Thread(target=worker, daemon=True) for _ in range(streams)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(seconds + 6)
    return total[0], max(time.time() - start, 0.1)


def _ping_ms(count=5):
    r = run(["ping", "-n", "-q", "-c", str(count), "-i", "0.3", "-W", "2", "1.1.1.1"], timeout=count + 5)
    m = re.search(r"= [\d.]+/([\d.]+)/", r.stdout or "")
    return float(m.group(1)) if m else None


def qos_measure(seconds=8):
    """Pause shaping, measure the raw line (download, upload, latency idle and under
    load), then restore shaping. Uses Cloudflare's public speed test endpoints."""
    import threading
    was = run(["systemctl", "is-active", "helios-sqm.service"]).stdout.strip() == "active"
    if was:
        run(["systemctl", "stop", "helios-sqm.service"], timeout=30)
    try:
        idle = _ping_ms()
        loaded = []
        pinger = threading.Thread(target=lambda: loaded.append(_ping_ms(8)), daemon=True)
        pinger.start()
        down, dt = _transfer("https://speed.cloudflare.com/__down?bytes=250000000", seconds)
        pinger.join(15)
        up, ut = _transfer("https://speed.cloudflare.com/__up", seconds, upload=True)
    finally:
        if was:
            run(["systemctl", "start", "helios-sqm.service"], timeout=30)
    return {"down": round(down * 8 / dt / 1e6, 1), "up": round(up * 8 / ut / 1e6, 1),
            "idleMs": idle, "loadedMs": loaded[0] if loaded else None}


def qos_recommend(measured, cable, link_mbps):
    """Suggested settings + one reason each. Pure: easy to test."""
    sug, why = {}, {}
    sug["link"] = "docsis" if cable else "ethernet"
    why["link"] = ("A cable modem answered at 192.168.100.1, so this looks like a cable (DOCSIS) line."
                   if cable else "No cable modem answered, so Ethernet overhead is assumed (right for fibre and most others; pick VDSL or ADSL if that's your line).")
    sug["fairness"], why["fairness"] = "device", "Share the line evenly between home devices first, then between each device's connections."
    sug["algorithm"], why["algorithm"] = "cake", "CAKE handles overhead, fairness and priorities in one place."
    if measured and measured.get("down") and measured.get("up"):
        down, up = measured["down"], measured["up"]
        sug["down"] = f"{max(1, int(down * 0.90))}mbit"
        sug["up"] = f"{max(1, int(up * 0.95))}mbit"
        why["down"] = f"90% of the measured {down:g} Mb/s, so the queue builds here, not at the ISP."
        why["up"] = f"95% of the measured {up:g} Mb/s."
        sug["ackFilter"] = down / up >= 8
        why["ackFilter"] = (f"Download is {down / up:.0f}× the upload: filtering redundant ACKs frees upload for everything else."
                            if sug["ackFilter"] else "Upload isn't much smaller than download, so the ACK filter wouldn't help.")
        if link_mbps and down > link_mbps * 0.9:
            why["down"] += f" Note: it's close to the trunk link's {link_mbps} Mb/s, which also carries your LAN traffic."
        if down > 2000:
            sug["algorithm"] = "fq_codel"
            why["algorithm"] = "Over 2 Gb/s, CAKE can run out of CPU on small routers; fq_codel is lighter (watch CPU and decide)."
    return sug, why


def cmd_qos_suggest(a):
    cable = probe_cable_modem()
    speed = None
    try:
        speed = int(read("/sys/class/net/trunk0/speed").strip() or 0) or None
    except ValueError:
        speed = None
    measured = qos_measure() if a.measure else None
    sug, why = qos_recommend(measured, cable, speed)
    grade = None
    if measured and measured.get("idleMs") is not None and measured.get("loadedMs") is not None:
        extra = measured["loadedMs"] - measured["idleMs"]
        grade = "A" if extra < 5 else "B" if extra < 30 else "C" if extra < 60 else "D" if extra < 200 else "F"
    out({"ok": True, "suggestions": sug, "reasons": why, "measured": measured, "bufferbloatGrade": grade,
         "detected": {"cableModem": cable, "trunkMbps": speed}})


# ------------------------------------------------------------------ UPnP
def parse_upnp():
    res = []
    for line in read("/run/miniupnpd.leases").splitlines():
        parts = line.split(":", 5)
        if len(parts) < 5:
            continue
        proto, ext, ip, intp = parts[0], parts[1], parts[2], parts[3]
        ts = parts[4] if len(parts) > 4 else "0"
        desc = parts[5] if len(parts) > 5 else ""
        try:
            res.append({"proto": proto, "ext_port": int(ext), "ip": ip, "int_port": int(intp),
                        "expires": int(ts) or None, "desc": desc})
        except ValueError:
            continue
    return res


UPNP_LEASES = "/run/miniupnpd.leases"
PORT_RANGE_RE = re.compile(r"^([0-9]{1,5})(?:-([0-9]{1,5}))?\Z")


def _port_range(text):
    m = PORT_RANGE_RE.match(str(text).strip())
    if not m:
        return None
    lo, hi = int(m.group(1)), int(m.group(2) or m.group(1))
    return (lo, hi) if 1 <= lo <= hi <= 65535 else None


def upnp_cfg(s):
    """state.upnp with the block lists filled in (written back only when they change)."""
    u = s["upnp"]
    u.setdefault("blockedDevices", [])
    u.setdefault("blockedPorts", [])
    return u


def upnp_blocked(s, mapping):
    """Why a current mapping is blocked by the saved block list (None if it isn't)."""
    u = {"blockedDevices": [], "blockedPorts": [], **s["upnp"]}
    ips = {d.get("ip"): d["name"] for d in s["devices"] if d["name"] in u["blockedDevices"]}
    if mapping["ip"] in ips:
        return f"device {ips[mapping['ip']]} is blocked"
    for p in u["blockedPorts"]:
        r = _port_range(p)
        if r and r[0] <= mapping["ext_port"] <= r[1]:
            return f"port {p} is blocked"
    return None


def upnp_remove(pred):
    """Remove mappings matching pred. miniupnpd recreates mappings from its lease
    file when it starts, so: stop it, drop those lines, start it again."""
    if run(["systemctl", "stop", "miniupnpd.service"], timeout=30).returncode != 0:
        fail("couldn't stop miniupnpd")
    removed = 0
    try:
        keep = []
        for line in read(UPNP_LEASES).splitlines():
            parts = line.split(":", 5)
            try:
                m = {"proto": parts[0], "ext_port": int(parts[1]), "ip": parts[2]}
            except (IndexError, ValueError):
                keep.append(line)
                continue
            if pred(m):
                removed += 1
            else:
                keep.append(line)
        if removed:
            path = p(UPNP_LEASES)                      # p() adds the test root, like read()
            with open(path + ".tmp", "w") as f:
                f.write("".join(x + "\n" for x in keep))
            os.replace(path + ".tmp", path)
    finally:
        run(["systemctl", "start", "miniupnpd.service"], timeout=30)
    return removed


def cmd_upnp(a):
    s = load_state()
    names = {d["ip"]: d["name"] for d in s["devices"] if d.get("ip")}
    maps = parse_upnp()
    for m in maps:
        m["device"] = names.get(m["ip"])
        m["blocked"] = upnp_blocked(s, m)
    out({"ok": True, "enabled": s["upnp"]["enabled"], "mappings": maps,
         "blockedDevices": s["upnp"].get("blockedDevices", []), "blockedPorts": s["upnp"].get("blockedPorts", []),
         "networks": [n["name"] for n in (s.get("networks") or CATALOG.get("defaults", [])) if resolve_access(n).get("upnp")]})


def cmd_upnp_clear(a):
    out({"ok": True, "removed": upnp_remove(lambda m: True)})


def cmd_upnp_close(a):
    proto = a.proto.upper()
    if proto not in ("TCP", "UDP"):
        fail("protocol must be TCP or UDP")
    removed = upnp_remove(lambda m: m["proto"].upper() == proto and m["ext_port"] == a.port)
    if not removed:
        fail(f"no open {proto} port {a.port}")
    out({"ok": True, "removed": removed})


def _upnp_block_args(d):
    dev, port = d.get("device"), d.get("port")
    if bool(dev) == bool(port):
        fail("block either a device or a port")
    if port is not None:
        r = _port_range(port)
        if not r:
            fail("port must be a number or a range like 6000-6100 (1-65535)")
        port = f"{r[0]}" if r[0] == r[1] else f"{r[0]}-{r[1]}"
    return dev, port


def cmd_upnp_block(a):
    """Add a device or port to the block list, and close what it already opened."""
    dev, port = _upnp_block_args(parse_json_arg(a.json))
    with Lock():
        s = load_state()
        u = upnp_cfg(s)
        if dev:
            d = next((x for x in s["devices"] if x["name"] == dev), None)
            if d is None or not d.get("ip"):
                fail(f"no named device {dev!r} (only named devices have a fixed address to block)")
            if dev not in u["blockedDevices"]:
                u["blockedDevices"].append(dev)
            pred = lambda m, ip=d["ip"]: m["ip"] == ip
        else:
            if port not in u["blockedPorts"]:
                u["blockedPorts"].append(port)
            lo, hi = _port_range(port)
            pred = lambda m: lo <= m["ext_port"] <= hi
        save_state(s)
    # Close what's already open now; the block itself takes effect when applied
    removed = upnp_remove(pred) if parse_upnp() else 0
    out({"ok": True, "closed": removed, "dirty": True})


def cmd_upnp_unblock(a):
    dev, port = _upnp_block_args(parse_json_arg(a.json))
    with Lock():
        s = load_state()
        u = upnp_cfg(s)
        key, val = ("blockedDevices", dev) if dev else ("blockedPorts", port)
        if val not in u[key]:
            fail(f"{val} isn't blocked")
        u[key] = [x for x in u[key] if x != val]
        save_state(s)
    out({"ok": True, "dirty": True})


# ------------------------------------------------------------ history
def read_outages():
    res = []
    try:
        with open(OUTAGES) as f:
            for line in f:
                try:
                    res.append(json.loads(line))
                except ValueError:
                    # skip a corrupt line
                    pass
    except OSError:
        # no outages recorded yet
        pass
    return res


def cmd_history(a):
    git_ready()
    r = git("log", "-n", "60", "--date=unix", "--pretty=format:%H%x1f%ad%x1f%an%x1f%s", check=False)
    commits = []
    for line in r.stdout.splitlines():
        h, t, author, subj = (line.split("\x1f") + ["", "", "", ""])[:4]
        commits.append({"rev": h, "short": h[:8], "time": int(t or 0), "author": author, "subject": subj})
    gens = []
    g = run(["nix-env", "--list-generations", "-p", "/nix/var/nix/profiles/system"])
    for line in g.stdout.splitlines():
        m = re.match(r"\s*(\d+)\s+(\S+ \S+)\s*(\(current\))?", line)
        if m:
            gens.append({"gen": int(m.group(1)), "date": m.group(2), "current": bool(m.group(3))})
    backup = read_json(os.path.join(DATA_DIR, "backup.json"), {}) or {}
    out({"ok": True, "commits": commits, "generations": gens[-30:][::-1],
         "backup": backup, "outages": read_outages()[-100:][::-1]})


# AdGuard keys whose values never go into the backup (the repo may be pushed to a
# remote): the admin password hash, an inline TLS private key, and credentials
# embedded in URLs. The copy is for restoring by hand: keep the live file's values
# for these lines when you do.
ADGUARD_SECRET_KEYS = ("password", "private_key")
_URL_CREDS = re.compile(r"(\w+://)[^/\s:@'\"]+:[^/\s@'\"]+@")


def redact_adguard(text):
    """Line-based redaction of AdGuardHome.yaml (no YAML library in the closure)."""
    out, skip_indent = [], None
    key_re = re.compile(r"^(\s*(?:-\s+)?)(%s):(.*)$" % "|".join(ADGUARD_SECRET_KEYS))
    for line in text.splitlines():
        indent = len(line) - len(line.lstrip())
        if skip_indent is not None:
            if line.strip() and indent <= skip_indent:
                skip_indent = None          # block scalar ended
            else:
                continue
        m = key_re.match(line)
        if m:
            value = m.group(3).strip()
            if value in ("", '""', "''"):
                out.append(line)            # empty: nothing to hide
                continue
            out.append(f"{m.group(1)}{m.group(2)}: REDACTED")
            if value[:1] in ("|", ">"):     # multi-line block scalar follows
                skip_indent = indent
            continue
        out.append(_URL_CREDS.sub(r"\1REDACTED@", line))
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def cmd_backup(a):
    """Snapshot settings that live outside Nix (AdGuard UI changes) into the git repo."""
    git_ready()
    dest = os.path.join(CONFIG_DIR, "backups", "adguard")
    os.makedirs(dest, exist_ok=True)
    copied = []
    src = "/var/lib/AdGuardHome/AdGuardHome.yaml"
    if os.path.exists(p(src)):
        with open(p(src)) as fh:
            text = redact_adguard(fh.read())
        with open(os.path.join(dest, os.path.basename(src)), "w") as fh:
            fh.write(text)
        copied.append(src)
    git("add", "-A", "--", "backups")
    changed = bool(git("diff", "--cached", "--quiet", "--", "backups", check=False).returncode)
    if changed:
        git("commit", "-q", "-m", "Backup: AdGuard settings", "--", "backups")
    pushed = None
    if git("remote", check=False).stdout.strip():
        pr = git("push", "-q", "origin", "HEAD", check=False)
        pushed = pr.returncode == 0
    info = {"time": now(), "changed": changed, "copied": copied, "pushed": pushed}
    write_json(os.path.join(DATA_DIR, "backup.json"), info)
    out({"ok": True, **info})


# ------------------------------------------------------- outage monitor
def ping(host):
    return run(["ping", "-n", "-q", "-c", "1", "-W", "2", host], timeout=5).returncode == 0


def notify(kind, title, body, priority="default", tags="sunny"):
    """Publish to Solar Wind. kind = "flare" (urgent) or "aurora" (informational)."""
    if not SW.get("enable"):
        return False
    topic = (SW.get("topics") or {}).get(kind, kind)
    req = urllib.request.Request(f"{SW.get('url', 'http://127.0.0.1:2586')}/{topic}", data=body.encode(), method="POST")
    req.add_header("Title", title)
    req.add_header("Priority", priority)
    req.add_header("Tags", tags)
    tok = read_plain(SW_TOKEN).strip()
    if tok:
        req.add_header("Authorization", "Bearer " + tok)
    try:
        urllib.request.urlopen(req, timeout=10).read()
        return True
    except Exception:  # noqa: BLE001
        return False


def read_plain(path):
    try:
        with open(path) as f:
            return f.read()
    except OSError:
        return ""


ALNUM = string.ascii_lowercase + string.digits


def cmd_notify_setup(a):
    out({"ok": True, "created": sw_setup(a.rotate)})


def sw_setup(rotate):
    """Generate Solar Wind's users/ACL/token once (or again with rotate). True if (re)generated."""
    a = argparse.Namespace(rotate=rotate)
    if os.path.exists(SW_ENV) and not a.rotate:
        return False
    if bcrypt is None:
        fail("python bcrypt module missing")
    topics = SW.get("topics") or {"flare": "flare", "aurora": "aurora"}
    hash_ = lambda pw: bcrypt.hashpw(pw.encode(), bcrypt.gensalt(10)).decode()
    token = "tk_" + "".join(secrets.choice(ALNUM) for _ in range(29))
    phone_pw = "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(24))
    # --rotate keeps a custom phone username (set via notify-credentials)
    phone_user = (read_json(SW_PHONE, {}) or {}).get("user") if a.rotate else None
    phone_user = phone_user if phone_user and SW_USER_RE.match(phone_user) else "phone"
    users = [f"helios:{hash_(secrets.token_urlsafe(24))}:user", f"{phone_user}:{hash_(phone_pw)}:user"]
    access = []
    for t in topics.values():
        access += [f"helios:{t}:write-only", f"{phone_user}:{t}:read-only"]
    os.makedirs(DATA_DIR, exist_ok=True)
    old = os.umask(0o077)
    try:
        with open(SW_ENV, "w") as f:
            f.write(f"NTFY_AUTH_USERS={','.join(users)}\n")
            f.write(f"NTFY_AUTH_ACCESS={','.join(access)}\n")
            f.write(f"NTFY_AUTH_TOKENS=helios:{token}:HeliOS router\n")
        with open(SW_TOKEN, "w") as f:
            f.write(token + "\n")
        write_json(SW_PHONE, {"user": phone_user, "password": phone_pw})
    finally:
        os.umask(old)
    if a.rotate and not os.environ.get("HELIOS_NO_SYSTEMD"):
        run(["systemctl", "restart", "ntfy-sh.service"])
    return True


SW_USER_RE = re.compile(r"^[a-z][a-z0-9_-]{2,31}\Z")


def cmd_notify_credentials(a):
    """Let the user choose the phone login. Password comes from stdin, never argv."""
    user = a.user
    if not SW_USER_RE.match(user):
        fail("Username must be 3-32 characters: lowercase letters, digits, - or _, starting with a letter")
    if user == "helios":
        fail("The username “helios” is reserved for the router")
    pw = sys.stdin.read().rstrip("\r\n")
    if not 12 <= len(pw) <= 128:
        fail("Password must be 12 to 128 characters")
    if bcrypt is None:
        fail("python bcrypt module missing")
    if not os.path.exists(SW_ENV):
        sw_setup(False)
    env = {}
    for line in read_plain(SW_ENV).splitlines():
        k, _, v = line.partition("=")
        env[k] = v
    topics = SW.get("topics") or {"flare": "flare", "aurora": "aurora"}
    old_user = (read_json(SW_PHONE, {}) or {}).get("user") or "phone"
    # keep helios's entries (and the token) byte-for-byte; replace everything else
    users = [u for u in env.get("NTFY_AUTH_USERS", "").split(",") if u.startswith("helios:")]
    access = [x for x in env.get("NTFY_AUTH_ACCESS", "").split(",") if x.startswith("helios:")]
    if not users:
        fail("solarwind.env has no helios user; run notify-setup --rotate")
    users.append(f"{user}:{bcrypt.hashpw(pw.encode(), bcrypt.gensalt(10)).decode()}:user")
    access += [f"{user}:{t}:read-only" for t in topics.values()]
    env["NTFY_AUTH_USERS"] = ",".join(users)
    env["NTFY_AUTH_ACCESS"] = ",".join(access)
    old = os.umask(0o077)
    try:
        tmp = SW_ENV + ".tmp"
        with open(tmp, "w") as f:
            for k, v in env.items():
                if k:
                    f.write(f"{k}={v}\n")
        os.replace(tmp, SW_ENV)
        write_json(SW_PHONE, {"user": user, "password": pw})
    finally:
        os.umask(old)
    if not os.environ.get("HELIOS_NO_SYSTEMD"):
        run(["systemctl", "restart", "ntfy-sh.service"])
    out({"ok": True, "user": user, "previous": old_user})


def sw_public_url():
    return SW.get("publicUrl") or SW.get("lanUrl") or ""


def cmd_notify_info(a):
    phone = read_json(SW_PHONE, {}) or {}
    out({"ok": True, "enabled": bool(SW.get("enable")), "exposure": SW.get("exposure"),
         "url": sw_public_url(), "topics": SW.get("topics") or {},
         "user": phone.get("user"), "password": phone.get("password") if a.reveal else None,
         "configured": os.path.exists(SW_ENV)})


def cmd_notify_test(a):
    ok = notify(a.kind, f"Test from {CFG.get('hostName', socket.gethostname())}",
                f"This is a test {a.kind} notification from HeliOS.", "high" if a.kind == "flare" else "default",
                "rotating_light" if a.kind == "flare" else "sunny")
    if not ok:
        fail("The notification server did not accept the message — is ntfy-sh running?")
    out({"ok": True})


def cmd_notify_recent(a):
    phone = read_json(SW_PHONE, {}) or {}
    topics = SW.get("topics") or {}
    if not phone or not topics:
        out({"ok": True, "messages": []})
        return
    auth = base64.b64encode(f"{phone['user']}:{phone['password']}".encode()).decode()
    msgs = []
    for kind, topic in topics.items():
        req = urllib.request.Request(f"{SW.get('url')}/{urllib.parse.quote(topic)}/json?poll=1&since=72h")
        req.add_header("Authorization", "Basic " + auth)
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                for line in r.read().decode().splitlines():
                    try:
                        m = json.loads(line)
                    except ValueError:
                        continue
                    if m.get("event") == "message":
                        msgs.append({"kind": kind, "time": m.get("time"), "title": m.get("title") or "",
                                     "message": m.get("message") or "", "priority": m.get("priority", 3)})
        except Exception:  # noqa: BLE001
            continue
    msgs.sort(key=lambda m: m["time"] or 0, reverse=True)
    out({"ok": True, "messages": msgs[:40]})


def fmt_dur(sec):
    sec = int(sec)
    if sec < 60:
        return f"{sec}s"
    if sec < 3600:
        return f"{sec // 60}m {sec % 60}s"
    return f"{sec // 3600}h {(sec % 3600) // 60}m"


def cmd_uplink_check(a):
    targets = a.targets.split(",")
    ok = any(ping(t) for t in targets)
    st = read_json(UPLINK, {"state": "up", "since": now(), "fails": 0}) or {}
    t = now()
    if ok:
        if st.get("state") == "down":
            dur = t - st.get("since", t)
            rec = {"down_at": st.get("since", t), "up_at": t, "duration": dur}
            with open(OUTAGES, "a") as f:
                f.write(json.dumps(rec) + "\n")
            notify("aurora", "Internet is back",
                   f"{CFG.get('hostName', 'router')}: internet was down for {fmt_dur(dur)} "
                   f"(from {dt.datetime.fromtimestamp(rec['down_at']).strftime('%H:%M')}).", "default", "satellite")
            st = {"state": "up", "since": t, "fails": 0}
        else:
            st["fails"] = 0
            st.setdefault("since", t)
            st["state"] = "up"
    else:
        st["fails"] = st.get("fails", 0) + 1
        if st.get("state") != "down" and st["fails"] >= 2:
            # Count the outage from the first failed check
            st = {"state": "down", "since": t - a.interval, "fails": st["fails"]}
    st["checked"] = t
    write_json(UPLINK, st)
    out({"ok": True, **st})


# ------------------------------------------------- drivers & hardware
HW_KINDS = ["router", "switch", "ap", "ups"]
PORT_MODES = ["access", "trunk", "wan", "unused"]
HW_ID_RE = re.compile(r"^[a-z][a-z0-9-]{0,31}\Z")
USB_ID_RE = re.compile(r"^[0-9a-f]{4}:[0-9a-f]{4}\Z")
IFNAME_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,15}\Z")
SERIAL_RE = re.compile(r"^[A-Za-z0-9._:-]{1,64}\Z")
NUT_DRIVER_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}\Z")
MAX_HARDWARE, MAX_PORTS, MAX_SSIDS = 32, 64, 16
UPS_URGENT = {"ONBATT", "LOWBATT", "FSD", "SHUTDOWN", "COMMBAD", "NOCOMM", "REPLBATT", "NOPARENT"}
UPS_PHRASE = {"ONLINE": "back on mains power", "ONBATT": "on battery", "LOWBATT": "battery low", "FSD": "forced shutdown",
              "COMMOK": "communication restored", "COMMBAD": "communication lost", "SHUTDOWN": "shutting down",
              "REPLBATT": "replace battery", "NOCOMM": "not reachable", "NOPARENT": "monitor stopped"}
UPS_SPOOL = os.environ.get("HELIOS_UPS_SPOOL", "/run/helios-ups")


def driver_state(s, name):
    """Effective {enabled, ...settings} of a driver in state s (catalog defaults for what's missing)."""
    meta = DRIVERS.get("drivers", {}).get(name, {})
    st = (s.get("drivers") or {}).get(name) or {}
    res = {"enabled": True if meta.get("always") else bool(st.get("enabled", meta.get("default", False)))}
    if name == "nut":
        base = json.loads(json.dumps(DRIVERS.get("nutDefaults") or {}))
        for k in ("shutdown", "server"):
            base.setdefault(k, {}).update(st.get(k) or {})
        res.update(base)
    return res


def net_names(s):
    return [n["name"] for n in (s.get("networks") or CATALOG.get("defaults", []))]


def net_label(s, name):
    n = next((x for x in (s.get("networks") or CATALOG.get("defaults", [])) if x["name"] == name), None)
    return (n.get("label") or name) if n else name


def hardware_rename_network(s, old, new):
    for d in s.get("hardware") or []:
        for pt in d.get("ports") or []:
            if pt.get("network") == old:
                pt["network"] = new
            if isinstance(pt.get("networks"), list):
                pt["networks"] = [new if x == old else x for x in pt["networks"]]
        for ss in d.get("ssids") or []:
            if ss.get("network") == old:
                ss["network"] = new
    srv = ((s.get("drivers") or {}).get("nut") or {}).get("server")
    if srv and old in srv.get("networks", []):
        srv["networks"] = [new if x == old else x for x in srv["networks"]]
    px = (((s.get("heliosphere") or {}).get("ingress") or {}).get("proxy") or {})
    if old in px.get("networks", []):
        px["networks"] = [new if x == old else x for x in px["networks"]]


def hardware_drop_network(s, name):
    """Remove references to a deleted network; returns how many were dropped."""
    n = 0
    for d in s.get("hardware") or []:
        for pt in d.get("ports") or []:
            if pt.get("network") == name:
                pt["mode"] = "unused"
                pt.pop("network", None)
                n += 1
            if isinstance(pt.get("networks"), list) and name in pt["networks"]:
                pt["networks"] = [x for x in pt["networks"] if x != name]
                n += 1
        if d.get("ssids"):
            keep = [x for x in d["ssids"] if x.get("network") != name]
            n += len(d["ssids"]) - len(keep)
            d["ssids"] = keep
    srv = ((s.get("drivers") or {}).get("nut") or {}).get("server")
    if srv and name in srv.get("networks", []):
        srv["networks"] = [x for x in srv["networks"] if x != name]
        n += 1
    px = (((s.get("heliosphere") or {}).get("ingress") or {}).get("proxy") or {})
    if name in px.get("networks", []):
        px["networks"] = [x for x in px["networks"] if x != name]
        n += 1
    return n


def parse_json_arg(text):
    try:
        d = json.loads(text)
    except ValueError:
        fail("invalid JSON")
    if not isinstance(d, dict):
        fail("expected a JSON object")
    return d


# ------------------------------------------------------------ detection
def _sys(path):
    return read(path).strip()


def _real_sys_path(path):
    r = os.path.realpath(p(path))
    return r[len(FAKE):] if FAKE and r.startswith(FAKE) else r


def _bus_of(path):
    segs = _real_sys_path(path).split("/")
    if any(x.startswith("usb") for x in segs):
        return "usb"
    if any(x.startswith("pci") for x in segs):
        return "pci"
    return "platform"


def detect_ports(state):
    router = next((d for d in state.get("hardware") or [] if d.get("kind") == "router"), {})
    mapped = {}
    for pt in router.get("ports") or []:
        m = pt.get("match") or {}
        mapped[("mac", (m.get("mac") or "").lower())] = pt.get("mode")
        mapped[("name", m.get("name"))] = pt.get("mode")
    res = []
    try:
        names = sorted(os.listdir(p("/sys/class/net")))
    except OSError:
        names = []
    for name in names:
        base = f"/sys/class/net/{name}"
        physical = os.path.exists(p(base + "/device"))
        dsa = not physical and os.path.exists(p(base + "/phys_switch_id"))
        if not physical and not dsa:
            continue
        mac = _sys(base + "/address").lower() or None
        try:
            speed = int(_sys(base + "/speed"))
        except ValueError:
            speed = None
        if speed is not None and speed <= 0:
            speed = None
        port = {"name": name, "mac": mac,
                "driver": os.path.basename(_real_sys_path(base + "/device/driver")) if physical and os.path.exists(p(base + "/device/driver")) else None,
                "bus": "dsa" if dsa else _bus_of(base + "/device"), "speed": speed, "carrier": _sys(base + "/carrier") == "1"}
        if not dsa and mac and mac == TRUNK_MAC:
            role = "trunk"
        elif not dsa and mac and mac == RESCUE_MAC:
            role = "rescue"
        elif ("name", name) in mapped or (not dsa and ("mac", mac) in mapped):
            role = "wan" if (mapped.get(("name", name)) or mapped.get(("mac", mac))) == "wan" else "mapped"
        else:
            role = "free"
        port["role"] = role
        res.append(port)
    return res


def detect_usb():
    res = []
    base = "/sys/bus/usb/devices"
    try:
        names = sorted(os.listdir(p(base)))
    except OSError:
        names = []
    for name in names:
        d = f"{base}/{name}"
        vid, pid = _sys(d + "/idVendor").lower(), _sys(d + "/idProduct").lower()
        if not vid or not pid or _sys(d + "/bDeviceClass") == "09":
            continue
        vendor = _sys(d + "/manufacturer") or DRIVERS.get("upsVendors", {}).get(vid) or None
        product = _sys(d + "/product") or None
        text = f"{vendor or ''} {product or ''}"
        if vid in DRIVERS.get("upsVendors", {}) or re.search(r"(?<![A-Za-z])UPS(?![A-Za-z])", text, re.I):
            kind = "ups"
        else:
            kind = "other"
            try:
                subs = os.listdir(p(d))
            except OSError:
                subs = []
            ifaces = [x for x in subs if ":" in x]
            if "ethernet" in text.lower() or any(_sys(f"{d}/{x}/bInterfaceClass").lower() in ("02", "0a") for x in ifaces):
                kind = "nic"
        res.append({"id": f"{vid}:{pid}", "vendor": vendor, "product": product, "serial": _sys(d + "/serial") or None, "kind": kind})
    res.sort(key=lambda x: x["id"])
    return res


def _as_list(v):
    if v is None:
        return []
    return v if isinstance(v, list) else [v]


def _id_value(v):
    if isinstance(v, dict):
        return v.get("value")
    return v if isinstance(v, str) else None


def detect_lldp():
    data = run_json(["lldpcli", "-f", "json", "show", "neighbors", "details"], None)
    if not isinstance(data, dict):
        return []
    ifs = (data.get("lldp") or {}).get("interface") if isinstance(data.get("lldp"), dict) else data.get("interface")
    entries = []
    for item in _as_list(ifs):
        if isinstance(item, dict):
            for local, v in item.items():
                if isinstance(v, dict):
                    entries.append((local, v))
    res = []
    for local, v in entries:
        chassis = v.get("chassis")
        if not isinstance(chassis, dict):
            continue
        if isinstance(chassis.get("id"), dict) and "value" in chassis["id"]:
            cname, c = _id_value(chassis["id"]), chassis      # no sysName: chassis isn't keyed by name
        else:
            cname, c = next(((k, x) for k, x in chassis.items() if isinstance(x, dict)), (None, None))
            if c is None:
                continue
        port = v.get("port") if isinstance(v.get("port"), dict) else {}
        caps = [x.get("type") for x in _as_list(c.get("capability")) if isinstance(x, dict) and x.get("enabled", True) and x.get("type")]
        mgmt = next((x for x in _as_list(c.get("mgmt-ip")) if isinstance(x, str)), None)
        res.append({"localPort": local, "chassis": cname, "mgmtIp": mgmt, "sysDescr": c.get("descr"),
                    "port": _id_value(port.get("id")), "portDescr": port.get("descr"), "capabilities": caps})
    return res


def cmd_detect(a):
    state = read_json(STATE, {}) or {}
    det = {"time": now(), "ports": detect_ports(state), "usb": detect_usb(),
           "lldp": detect_lldp() if driver_state(state, "lldp")["enabled"] else []}
    write_json(DETECT_FILE, det)
    out({"ok": True, **det})


# ------------------------------------------------------------- drivers
def cmd_driver_set(a):
    d = parse_json_arg(a.json)
    name = d.pop("name", None)
    meta = DRIVERS.get("drivers", {}).get(name)
    if meta is None:
        fail(f"unknown driver {name!r}")
    with Lock():
        s = load_state()
        cur = driver_state(s, name)
        enabled = d.pop("enabled", cur["enabled"])
        if not isinstance(enabled, bool):
            fail("enabled must be true or false")
        if meta.get("always") and not enabled:
            fail(f"driver {name} is always on")
        entry = {"enabled": enabled}
        if name == "nut":
            unknown = set(d) - {"shutdown", "server"}
            if unknown:
                fail(f"unknown setting {sorted(unknown)[0]!r}")
            sd, sv = dict(cur["shutdown"]), dict(cur["server"])
            if "shutdown" in d:
                g = d["shutdown"]
                if not isinstance(g, dict) or set(g) - {"charge", "runtime"}:
                    fail("shutdown takes charge and runtime")
                for k, lo, hi in (("charge", 5, 90), ("runtime", 0, 3600)):
                    if k in g:
                        if not isinstance(g[k], int) or isinstance(g[k], bool) or not lo <= g[k] <= hi:
                            fail(f"shutdown.{k} must be a whole number from {lo} to {hi}")
                        sd[k] = g[k]
            if "server" in d:
                g = d["server"]
                if not isinstance(g, dict) or set(g) - {"enabled", "networks"}:
                    fail("server takes enabled and networks")
                if "enabled" in g:
                    if not isinstance(g["enabled"], bool):
                        fail("server.enabled must be true or false")
                    sv["enabled"] = g["enabled"]
                if "networks" in g:
                    nets = g["networks"]
                    if not isinstance(nets, list) or not all(isinstance(x, str) for x in nets):
                        fail("server.networks must be a list of network names")
                    for x in nets:
                        if x not in net_names(s):
                            fail(f"unknown network {x!r}")
                    sv["networks"] = list(dict.fromkeys(nets))
            entry["shutdown"], entry["server"] = sd, sv
        elif d:
            fail(f"driver {name} has no settings")
        s.setdefault("drivers", {})[name] = entry
        save_state(s)
        view = driver_state(s, name)
    out({"ok": True, "name": name, **view, "dirty": True})


# ------------------------------------------------------------ hardware
def _text(d, key, limit, default=""):
    v = d.get(key, default)
    if v is None:
        v = default
    if not isinstance(v, str):
        fail(f"{key} must be text")
    v = v.strip()
    if len(v) > limit:
        fail(f"{key} can be at most {limit} characters")
    return v


def _check_nets(s, nets, what):
    if nets == "all":
        return "all"
    if not isinstance(nets, list) or not all(isinstance(x, str) for x in nets):
        fail(f"{what}: networks must be \"all\" or a list of network names")
    for x in nets:
        if x not in net_names(s):
            fail(f"{what}: unknown network {x!r}")
    return list(dict.fromkeys(nets))


def clean_ports(s, kind, ports):
    if not isinstance(ports, list):
        fail("ports must be a list")
    if len(ports) > MAX_PORTS:
        fail(f"at most {MAX_PORTS} ports per device")
    res, seen, wans = [], set(), 0
    for i, pt in enumerate(ports, 1):
        if not isinstance(pt, dict):
            fail(f"port {i} must be an object")
        o = {}
        if kind == "router":
            m = pt.get("match")
            if not isinstance(m, dict) or len(m) != 1 or not (("mac" in m) != ("name" in m)):
                fail(f"port {i}: match needs a mac or a name")
            if "mac" in m:
                mac = str(m["mac"]).strip().lower()
                if not MAC_RE.match(mac):
                    fail(f"port {i}: bad MAC address")
                if mac in (TRUNK_MAC, RESCUE_MAC):
                    fail(f"port {i}: that is the {'trunk' if mac == TRUNK_MAC else 'rescue'} port and can't be mapped")
                key, o["match"] = ("mac", mac), {"mac": mac}
            else:
                nm = str(m["name"]).strip()
                if not IFNAME_RE.match(nm):
                    fail(f"port {i}: bad interface name")
                if nm in NET_RESERVED:
                    fail(f"port {i}: {nm} can't be mapped")
                key, o["match"] = ("name", nm), {"name": nm}
        else:
            if "match" in pt:
                fail(f"port {i}: only router ports use match")
            nm = pt.get("port")
            if not isinstance(nm, str) or not 1 <= len(nm.strip()) <= 16:
                fail(f"port {i}: port needs a name or number (1-16 characters)")
            key, o["port"] = ("port", nm.strip()), nm.strip()
        if key in seen:
            fail(f"port {i}: duplicate port")
        seen.add(key)
        lbl = _text(pt, "label", 40)
        if lbl:
            o["label"] = lbl
        mode = pt.get("mode")
        if mode not in PORT_MODES:
            fail(f"port {i}: mode must be one of {', '.join(PORT_MODES)}")
        o["mode"] = mode
        if mode == "access":
            if pt.get("network") not in net_names(s):
                fail(f"port {i}: access ports need an existing network")
            o["network"] = pt["network"]
        elif mode == "trunk":
            o["networks"] = _check_nets(s, pt.get("networks", []), f"port {i}")
            w = pt.get("wan", False)
            if not isinstance(w, bool):
                fail(f"port {i}: wan must be true or false")
            o["wan"] = w
        elif mode == "wan" and kind == "router":
            wans += 1
            if wans > 1:
                fail("the router can have only one WAN port")
        note = _text(pt, "note", 100)
        if note:
            o["note"] = note
        res.append(o)
    return res


def clean_ssids(s, ssids):
    if not isinstance(ssids, list):
        fail("ssids must be a list")
    if len(ssids) > MAX_SSIDS:
        fail(f"at most {MAX_SSIDS} SSIDs per device")
    res = []
    for i, x in enumerate(ssids, 1):
        if not isinstance(x, dict) or not isinstance(x.get("ssid"), str) or not 1 <= len(x["ssid"]) <= 32:
            fail(f"SSID {i}: name must be 1-32 characters")
        if x.get("network") not in net_names(s):
            fail(f"SSID {i}: unknown network")
        res.append({"ssid": x["ssid"], "network": x["network"]})
    return res


def cmd_hardware_set(a):
    d = parse_json_arg(a.json)
    hid = d.get("id")
    old_id = d.get("from") or hid
    if not isinstance(hid, str) or not HW_ID_RE.match(hid):
        fail("id: lowercase letters, digits and -, starting with a letter (max 32)")
    kind = d.get("kind")
    if kind not in HW_KINDS:
        fail(f"kind must be one of {', '.join(HW_KINDS)}")
    if (kind == "router") != (hid == "router"):
        fail("the router device has kind router and id \"router\" (and nothing else uses them)")
    drivers = DRIVERS.get("drivers", {})
    driver = d.get("driver") or next((n for n, m in drivers.items() if kind in m.get("kinds", [])), None)
    if driver not in drivers or kind not in drivers[driver].get("kinds", []):
        fail(f"driver {driver!r} can't handle a {kind}")
    label = _text(d, "label", 40, hid) or hid
    if re.search(r'[\x00-\x1f\x7f"\\]', label):
        fail("label can't contain quotes, backslashes or control characters")
    notes = _text(d, "notes", 200)
    with Lock():
        s = load_state()
        hw = s.setdefault("hardware", [])
        old = next((x for x in hw if x["id"] == old_id), None)
        if d.get("from") and old is None:
            fail(f"no hardware {old_id!r}")
        if old is None and len(hw) >= MAX_HARDWARE:
            fail(f"at most {MAX_HARDWARE} devices")
        for x in hw:
            if x is not old and x["id"] == hid:
                fail(f"hardware {hid!r} already exists")
            if x is not old and kind == "router" and x.get("kind") == "router":
                fail("there is already a router device")
        new = {"id": hid, "kind": kind, "driver": driver, "label": label}
        if notes:
            new["notes"] = notes
        if kind == "ups":
            if d.get("ports") or d.get("ssids"):
                fail("a UPS has no ports or SSIDs")
            m = d.get("match")
            if not isinstance(m, dict) or not USB_ID_RE.match(str(m.get("usb", "")).strip().lower()):
                fail("a UPS needs match.usb, like 051d:0002")
            new["match"] = {"usb": m["usb"].strip().lower()}
            if m.get("serial") not in (None, ""):
                if not isinstance(m["serial"], str) or not SERIAL_RE.match(m["serial"]):
                    fail("match.serial: letters, digits and . _ : - only (max 64)")
                new["match"]["serial"] = m["serial"]
            nd = d.get("nutDriver") or "usbhid-ups"
            if not isinstance(nd, str) or not NUT_DRIVER_RE.match(nd):
                fail("nutDriver must be a NUT driver name such as usbhid-ups")
            new["nutDriver"] = nd
            for x in hw:
                if x is not old and x.get("kind") == "ups" and x["match"] == new["match"]:
                    fail(f"that UPS is already configured as {x['id']!r}")
        else:
            if d.get("match") or d.get("nutDriver"):
                fail("match and nutDriver are for UPS devices")
            new["ports"] = clean_ports(s, kind, d.get("ports") or [])
            if d.get("ssids") and kind != "ap":
                fail("only access points have SSIDs")
            if kind == "ap":
                new["ssids"] = clean_ssids(s, d.get("ssids") or [])
        enabled_driver = None
        if kind == "ups" and not driver_state(s, "nut")["enabled"]:
            s.setdefault("drivers", {}).setdefault("nut", {})["enabled"] = True
            enabled_driver = "nut"
        if old is not None:
            hw[hw.index(old)] = new
        else:
            hw.append(new)
        save_state(s)
    res = {"ok": True, "device": new, "dirty": True}
    if enabled_driver:
        res["enabledDriver"] = enabled_driver
    out(res)


def cmd_hardware_remove(a):
    with Lock():
        s = load_state()
        hw = s.get("hardware") or []
        if not any(x["id"] == a.id for x in hw):
            fail(f"no hardware {a.id!r}")
        s["hardware"] = [x for x in hw if x["id"] != a.id]
        save_state(s)
    out({"ok": True, "dirty": True})


def slug(text, taken):
    base = re.sub(r"[^a-z0-9-]+", "-", (text or "").lower()).strip("-")[:28] or "device"
    if not base[0].isalpha():
        base = "d" + base
    cand, i = base, 2
    while cand in taken:
        cand, i = f"{base}-{i}", i + 1
    taken.add(cand)
    return cand


def make_suggestions(s, det):
    res = []
    if not det:
        return res
    hw = s.get("hardware") or []
    ids = {x["id"] for x in hw}
    for u in det.get("usb") or []:
        if u.get("kind") != "ups":
            continue
        if any(x.get("kind") == "ups" and x["match"].get("usb") == u["id"]
               and (not x["match"].get("serial") or x["match"]["serial"] == u.get("serial")) for x in hw):
            continue
        m = {"usb": u["id"]}
        if u.get("serial"):
            m["serial"] = u["serial"]
        name = u.get("product") or "UPS"
        res.append({"kind": "ups", "driver": "nut", "reason": f"A UPS is plugged in: {name}.",
                    "device": {"id": slug("ups", ids), "kind": "ups", "driver": "nut", "label": name[:40],
                               "match": m, "nutDriver": "usbhid-ups"}})
    free = [x for x in det.get("ports") or [] if x.get("role") == "free"]
    if free and not any(x.get("kind") == "router" for x in hw):
        ports = [{"match": {"name": x["name"]} if x.get("bus") == "dsa" or not x.get("mac") else {"mac": x["mac"]},
                  "label": x["name"], "mode": "unused"} for x in free]
        res.append({"kind": "router", "driver": "local-ports",
                    "reason": f"The router has {len(free)} unused Ethernet port{'s' if len(free) != 1 else ''}.",
                    "device": {"id": "router", "kind": "router", "driver": "local-ports", "label": "This router", "ports": ports}})
    labels = {x.get("label") for x in hw}
    for n in det.get("lldp") or []:
        name = n.get("chassis")
        if not name or name in labels:
            continue
        caps = " ".join(n.get("capabilities") or []).lower()
        if "wlan" in caps or "access point" in caps:
            kind = "ap"
        elif "bridge" in caps or "switch" in caps:
            kind = "switch"
        else:
            continue
        labels.add(name)
        dev = {"id": slug(name, ids), "kind": kind, "driver": "manual", "label": name[:40], "ports": []}
        if kind == "ap":
            dev["ssids"] = []
        res.append({"kind": kind, "driver": "manual",
                    "reason": f"{name} announced itself on {n.get('localPort')}" + (f" ({n['mgmtIp']})" if n.get("mgmtIp") else "") + ".",
                    "device": dev})
    return res


def read_ups(hid):
    r = run(["upsc", f"{hid}@localhost"])
    raw = {}
    if r.returncode == 0:
        for line in r.stdout.splitlines():
            k, sep, v = line.partition(":")
            if sep:
                raw[k.strip()] = v.strip()

    def num(*keys):
        for k in keys:
            try:
                return float(raw[k]) if "." in raw[k] else int(raw[k])
            except (KeyError, ValueError):
                continue
        return None
    return {"status": raw.get("ups.status"), "charge": num("battery.charge"), "runtime": num("battery.runtime"),
            "load": num("ups.load"), "model": raw.get("ups.model") or raw.get("device.model"), "raw": raw}


def checklist(s, dev):
    wan = f"{WAN_VLAN} (WAN)"

    def vlan(n):
        v = next((x["vlan"] for x in (s.get("networks") or CATALOG.get("defaults", [])) if x["name"] == n), "?")
        return f"{v} ({net_label(s, n)})"
    lines = []
    for pt in dev.get("ports") or []:
        nm = pt.get("port", "")
        head = f"Port {nm}" if nm.isdigit() else nm
        if pt["mode"] == "trunk":
            nets = net_names(s) if pt.get("networks") == "all" else pt.get("networks") or []
            vl = ([wan] if pt.get("wan") else []) + [vlan(n) for n in nets]
            txt = f"trunk — tagged VLANs {', '.join(vl)}" if vl else "trunk — no VLANs"
        elif pt["mode"] == "access":
            txt = f"access — untagged VLAN {vlan(pt.get('network'))}"
        elif pt["mode"] == "wan":
            txt = f"wan — untagged VLAN {wan}"
        else:
            txt = "unused — disable it or leave it empty"
        lines.append(f"{head}: {txt}" + (f" ({pt['note']})" if pt.get("note") else ""))
    for ss in dev.get("ssids") or []:
        lines.append(f"SSID “{ss['ssid']}” → VLAN {vlan(ss['network'])}")
    return lines


def public_summary(sm):
    if not isinstance(sm, dict):
        return None
    return {k: v for k, v in sm.items() if k != "known"}


def cmd_hardware(a):
    s = load_state()
    hw = s.get("hardware") or []
    det = read_json(DETECT_FILE)
    drivers = []
    for name, m in DRIVERS.get("drivers", {}).items():
        st = driver_state(s, name)
        drivers.append({"name": name, "label": m.get("label", name), "desc": m.get("desc", ""), "enabled": st.pop("enabled"),
                        "always": bool(m.get("always")), "builtin": bool(m.get("builtin")), "capabilities": m.get("capabilities", []),
                        "kinds": m.get("kinds", []), "settings": st})
    out({"ok": True,
         "catalog": {"kinds": HW_KINDS, "modes": PORT_MODES, "upsVendors": DRIVERS.get("upsVendors", {}),
                     "nutDefaults": DRIVERS.get("nutDefaults", {}), "wanVlan": WAN_VLAN},
         "drivers": drivers, "devices": hw, "detected": det,
         "suggestions": make_suggestions(s, det),
         "ups": {x["id"]: read_ups(x["id"]) for x in hw if x.get("kind") == "ups"},
         "checklists": {x["id"]: checklist(s, x) for x in hw if x.get("driver") == "manual"},
         "security": public_summary(read_json(VULNIX_SUMMARY)), "dirty": state_dirty()})


# ---------------------------------------------------------- UPS events
def cmd_ups_event(a):
    """upsmon NOTIFYCMD, runs as the nut user: drop an event file in the spool; ups-forward sends it."""
    typ = os.environ.get("NOTIFYTYPE", "").strip()
    if not typ:
        fail("NOTIFYTYPE is not set (this runs from upsmon)")
    t = time.time_ns()
    os.makedirs(UPS_SPOOL, exist_ok=True)
    path = os.path.join(UPS_SPOOL, f"{t}-{os.getpid()}.json")
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump({"time": t // 10**9, "type": typ, "ups": os.environ.get("UPSNAME", ""), "message": " ".join(a.message)}, f)
    os.replace(tmp, path)
    out({"ok": True, "file": os.path.basename(path)})


def cmd_ups_forward(a):
    """Runs as root when the spool isn't empty: send each queued event as a notification."""
    try:
        names = sorted(x for x in os.listdir(UPS_SPOOL) if x.endswith(".json"))
    except OSError:
        names = []
    labels = {}
    for d in (read_json(STATE, {}) or {}).get("hardware") or []:
        if d.get("kind") == "ups":
            labels[d["id"]] = d.get("label") or d["id"]
    sent = failed = 0
    for n in names:
        path = os.path.join(UPS_SPOOL, n)
        ev = read_json(path)
        if not isinstance(ev, dict):
            os.unlink(path)       # unreadable junk would block the queue forever
            continue
        typ = str(ev.get("type") or "")
        ups = str(ev.get("ups") or "").split("@")[0]
        urgent = typ in UPS_URGENT
        title = f"UPS {labels.get(ups, ups or 'power')}: {UPS_PHRASE.get(typ, typ.lower() or 'event')}"
        body = str(ev.get("message") or "") or title
        ok = notify("flare" if urgent else "aurora", title, body, "high" if urgent else "default",
                    "rotating_light,battery" if urgent else "electric_plug")
        if ok:
            os.unlink(path)
            sent += 1
        else:
            failed += 1
    out({"ok": True, "sent": sent, "failed": failed})


# -------------------------------------------------------------- vulnix
def _vulnix_entries(data):
    if isinstance(data, dict):
        data = next((v for v in data.values() if isinstance(v, list)), [])
    return [e for e in data if isinstance(e, dict)] if isinstance(data, list) else []


def _score(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def cmd_vulnix_report(a):
    try:
        with open(a.file) as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        fail(f"cannot read vulnix report: {e}")
    found = []
    for e in _vulnix_entries(data):
        scores = e.get("cvssv3_basescore") if isinstance(e.get("cvssv3_basescore"), dict) else {}
        white = set(_as_list(e.get("whitelisted")))
        cves = [c for c in _as_list(e.get("affected_by")) if isinstance(c, str) and c not in white]
        if not cves and not e.get("affected_by"):
            cves = [c for c in scores if c not in white]
        if not cves:
            continue
        name = e.get("pname") or e.get("name") or "?"
        version = e.get("version")
        if not version and isinstance(e.get("name"), str):
            m = re.match(r"^(.*?)-(\d.*)\Z", e["name"])
            if m:
                name, version = m.group(1), m.group(2)
        worst = max(cves, key=lambda c: _score(scores.get(c)))
        found.append({"name": name, "version": version, "cve": worst, "score": _score(scores.get(worst))})
    found.sort(key=lambda x: -x["score"])
    sev = {"critical": 0, "high": 0, "medium": 0, "low": 0}
    for x in found:
        sc = x["score"]
        sev["critical" if sc >= 9 else "high" if sc >= 7 else "medium" if sc >= 4 else "low"] += 1
    bad = [f"{x['name']} {x['version']} {x['cve']}" for x in found if x["score"] >= 7]
    prev = read_json(VULNIX_SUMMARY)
    known = set(prev.get("known") or []) if isinstance(prev, dict) else set()
    new = [x for x in found if x["score"] >= 7 and f"{x['name']} {x['version']} {x['cve']}" not in known]
    summary = {"time": now(), "total": len(found), **sev, "top": found[:10]}
    write_json(VULNIX_SUMMARY, {**summary, "known": bad[:500]})
    notified = None
    if new:
        notified = "flare"
        lines = [f"{x['name']} {x['version'] or ''} — {x['cve']} ({x['score']})".replace("  ", " ") for x in new[:5]]
        notify("flare", f"Security: {len(new)} new critical/high vulnerabilit{'y' if len(new) == 1 else 'ies'}",
               "\n".join(lines) + (f"\n…and {len(new) - 5} more" if len(new) > 5 else ""), "high", "rotating_light,lock")
    elif found:
        notified = "aurora"
        notify("aurora", "Security scan finished",
               f"{len(found)} packages with known vulnerabilities: {sev['critical']} critical, {sev['high']} high, "
               f"{sev['medium']} medium, {sev['low']} low.", "default", "lock")
    out({"ok": True, **summary, "notified": notified})


# ---------------------------------------------------- remote access (Heliosphere)
_HS = CFG.get("heliosphere") if isinstance(CFG.get("heliosphere"), dict) else {}
HS_ENABLED = bool(_HS.get("enabled", CFG.get("heliosphere") is True))
HS_PUBLIC_HOST = _HS.get("publicHost") or None
HS_DIR = _HS.get("dir") or "/var/lib/heliosphere"
HS_CONTAINER = _HS.get("container") or "heliosphere"
HS_WG_SUBNET = _HS.get("wgSubnet") or "10.61.0"
HS_OVPN_SUBNET = _HS.get("ovpnSubnet") or "10.62.0"
HS_RESERVED_PORTS = {22, 53, 67, 123, 443, 2586, 3493, 5353, 9090, 41641}
HS_SERVER_CN = "heliosphere"
HS_OVPN_USER = "heliosphere-ovpn"      # OpenVPN drops to this account and re-reads crl.pem as it
HS_PROXY_USER = "heliosphere-proxy"    # 3proxy runs as this account and reads proxy/users at start
HS_EGRESS_AUTH_PATH = "/var/lib/heliosphere/egress/auth.txt"      # where the container sees it
HS_METHODS = {"tailscale": "Tailscale", "wireguard": "WireGuard", "openvpn": "OpenVPN", "proxy": "proxy"}
HS_MODES = ["direct", "wireguard", "openvpn", "proxy"]


def hs_path(*parts):
    return os.path.join(HS_DIR, *parts)


def hs_defaults():
    # "lan": home networks that clients of this way in may reach (none by default)
    return {"ingress": {"tailscale": {"enabled": True, "exitNode": True, "hostname": "heliosphere", "lan": []},
                        "wireguard": {"enabled": False, "port": 51820, "peers": [], "lan": [], "tunnel": "full",
                                      "keepalive": 25, "mtu": None},
                        "openvpn": {"enabled": False, "port": 1194, "proto": "udp", "clients": [], "lan": []},
                        "proxy": {"enabled": False, "socksPort": 1080, "httpPort": 3128, "networks": [], "users": []}},
            "egress": {"mode": "direct", "killswitch": True, "proxy": None}}


def hs_resolve(s):
    """state.heliosphere merged over the defaults (missing keys take the defaults)."""
    res = hs_defaults()
    st = s.get("heliosphere") or {}
    for k, v in (st.get("ingress") or {}).items():
        if k in res["ingress"] and isinstance(v, dict):
            res["ingress"][k].update({x: y for x, y in json.loads(json.dumps(v)).items() if not (k == "proxy" and x == "wan")})
    for k, v in (st.get("egress") or {}).items():
        if k in res["egress"]:
            res["egress"][k] = json.loads(json.dumps(v))
    return res


def hs_write(path, data, mode=0o600, dirmode=0o755, owner=None):
    os.makedirs(os.path.dirname(path), mode=dirmode, exist_ok=True)
    # Some of these dirs are writable from inside the container: never follow
    # or reuse a file planted there, always a fresh one of our own
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".helios-")
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "w") as f:
            f.write(data)
        if owner:
            give_to(tmp, owner)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            # best effort: re-raise the real error below
            pass
        raise


def tool(cmd, input=None):
    """Run a helper binary; raise RuntimeError (shown to the user) when it fails."""
    r = run(cmd, input=input)
    if r.returncode != 0:
        raise RuntimeError(f"{os.path.basename(cmd[0])} failed: {(r.stderr or '').strip()[:200] or 'no output'}")
    return r.stdout


def _wg_key(k):
    try:
        return len(base64.b64decode(k, validate=True)) == 32 and len(k) == 44
    except (ValueError, TypeError):
        return False


def hs_server_public_key():
    pub = read_plain(hs_path("wireguard", "public")).strip()
    if _wg_key(pub):
        return pub
    priv = read_plain(hs_path("wireguard", "private")).strip()
    if priv:
        r = run(["wg", "pubkey"], input=priv + "\n")
        if r.returncode == 0 and _wg_key(r.stdout.strip()):
            return r.stdout.strip()
    return None


def hs_endpoint():
    """(host, warning): the configured public host, else the current WAN IPv4."""
    if HS_PUBLIC_HOST:
        return HS_PUBLIC_HOST, None
    v4 = (wan_info().get("ipv4") or "").split("/")[0]
    if not v4:
        fail("No public address known: the WAN has no IPv4 address and helios.heliosphere.publicHost isn't set")
    return v4, ("No public host name is set, so client configs point at the router's current WAN address, "
                "which may change. Set helios.heliosphere.publicHost.")


def hostport(host, port):
    return f"[{host}]:{port}" if ":" in host else f"{host}:{port}"


def hs_check_name(name):
    if not isinstance(name, str) or not NAME_RE.match(name):
        fail("name: lowercase letters, digits and -, starting with a letter or digit (max 32)")


# ------------------------------------------------------------ settings
def _port(v, what):
    if not isinstance(v, int) or isinstance(v, bool) or not 1 <= v <= 65535:
        fail(f"{what} must be a port from 1 to 65535")
    return v


def cmd_remote_set(a):
    d = parse_json_arg(a.json)
    if set(d) - {"ingress", "egress"}:
        fail(f"unknown setting {sorted(set(d) - {'ingress', 'egress'})[0]!r}")
    ing, eg = d.get("ingress") or {}, d.get("egress") or {}
    if not isinstance(ing, dict) or not isinstance(eg, dict):
        fail("ingress and egress must be objects")
    allowed = {"tailscale": {"enabled", "exitNode", "hostname", "lan"},
               "wireguard": {"enabled", "port", "lan", "tunnel", "keepalive", "mtu"},
               "openvpn": {"enabled", "port", "proto", "lan"},
               "proxy": {"enabled", "socksPort", "httpPort", "networks"}}
    with Lock():
        s = load_state()
        hs = hs_resolve(s)
        for m, v in ing.items():
            if m not in allowed:
                fail(f"unknown way in {m!r}")
            if not isinstance(v, dict):
                fail(f"{m} must be an object")
            for k in v:
                if k not in allowed[m]:
                    fail(f"{m}: unknown setting {k!r}" + (" (manage peers, clients and users with their own commands)" if k in ("peers", "clients", "users") else ""))
            cur = hs["ingress"][m]
            for k in ("enabled", "exitNode"):
                if k in v:
                    if not isinstance(v[k], bool):
                        fail(f"{m}.{k} must be true or false")
                    cur[k] = v[k]
            for k in ("port", "socksPort", "httpPort"):
                if k in v:
                    cur[k] = _port(v[k], f"{m}.{k}")
            if "proto" in v:
                if v["proto"] not in ("udp", "tcp"):
                    fail("openvpn.proto must be udp or tcp")
                cur["proto"] = v["proto"]
            for key in ("networks", "lan"):
                if key in v:
                    nets = v[key]
                    if not isinstance(nets, list) or not all(isinstance(x, str) for x in nets):
                        fail(f"{m}.{key} must be a list of network names")
                    for x in nets:
                        if x not in net_names(s):
                            fail(f"unknown network {x!r}")
                    cur[key] = list(dict.fromkeys(nets))
            if "hostname" in v:
                if not isinstance(v["hostname"], str) or not re.match(r"^[a-z0-9][a-z0-9-]{0,62}\Z", v["hostname"]):
                    fail("tailscale.hostname: lowercase letters, digits and dashes (max 63)")
                cur["hostname"] = v["hostname"]
            if "tunnel" in v:
                if v["tunnel"] not in ("full", "split"):
                    fail("wireguard.tunnel must be full (everything through home) or split (only home networks)")
                cur["tunnel"] = v["tunnel"]
            if "keepalive" in v:
                k_ = v["keepalive"]
                if not isinstance(k_, int) or isinstance(k_, bool) or not 0 <= k_ <= 600:
                    fail("wireguard.keepalive must be 0 (off) to 600 seconds")
                cur["keepalive"] = k_
            if "mtu" in v:
                m_ = v["mtu"]
                if m_ is not None and (not isinstance(m_, int) or isinstance(m_, bool) or not 1280 <= m_ <= 1500):
                    fail("wireguard.mtu must be empty (automatic) or 1280 to 1500")
                cur["mtu"] = m_
        # ports: all distinct, none reserved, none in use by UPnP
        ig = hs["ingress"]
        ports = {"wireguard.port": ig["wireguard"]["port"], "openvpn.port": ig["openvpn"]["port"],
                 "proxy.socksPort": ig["proxy"]["socksPort"], "proxy.httpPort": ig["proxy"]["httpPort"]}
        upnp = {x["ext_port"] for x in parse_upnp()}
        seen = {}
        for k, v in ports.items():
            if v in HS_RESERVED_PORTS:
                fail(f"{k}: port {v} is used by the router itself")
            if v in upnp:
                fail(f"{k}: port {v} is in use by a UPnP mapping")
            if v in seen:
                fail(f"{k} and {seen[v]} can't both use port {v}")
            seen[v] = k
        px = ig["proxy"]
        if px["enabled"]:
            if not px["users"]:
                fail("the proxy needs at least one user (add one first)")
            if not px["networks"]:
                fail("the proxy needs at least one home network (it isn't offered on the internet: it has no encryption)")
        if "killswitch" in eg:
            if not isinstance(eg["killswitch"], bool):
                fail("egress.killswitch must be true or false")
            hs["egress"]["killswitch"] = eg["killswitch"]
        if "proxy" in eg:
            p_ = eg["proxy"]
            if not isinstance(p_, dict) or set(p_) - {"type", "host", "port"}:
                fail("egress.proxy takes type, host and port (credentials go through remote-egress-upload auth)")
            host = p_.get("host")
            if p_.get("type") not in ("socks5", "http"):
                fail("egress.proxy.type must be socks5 or http")
            if not isinstance(host, str) or not re.match(r"^[A-Za-z0-9]([A-Za-z0-9.-]{0,251}[A-Za-z0-9])?\Z", host):
                try:
                    ip_ = ipaddress.ip_address(host)
                except ValueError:
                    ip_ = None
                if ip_ is None or "%" in host or str(ip_) != host:
                    fail("egress.proxy.host must be a host name or IP address")
            hs["egress"]["proxy"] = {"type": p_["type"], "host": host, "port": _port(p_.get("port"), "egress.proxy.port")}
        if "mode" in eg:
            if eg["mode"] not in HS_MODES:
                fail(f"egress.mode must be one of {', '.join(HS_MODES)}")
            hs["egress"]["mode"] = eg["mode"]
        mode = hs["egress"]["mode"]
        need = {"wireguard": "wg-out.conf", "openvpn": "client.ovpn"}.get(mode)
        if need and not os.path.exists(hs_path("egress", need)):
            fail(f"upload the {mode} configuration first (egress/{need} doesn't exist)")
        ts_ = ig["tailscale"]
        if ts_["enabled"] and not ts_["exitNode"] and not ts_["lan"]:
            fail("Tailscale needs the exit node or at least one home network, or it gives devices nothing")
        if ig["wireguard"]["tunnel"] == "split" and not ig["wireguard"]["lan"]:
            fail("“Only home networks” needs at least one home network to reach")
        if mode == "proxy" and not hs["egress"]["proxy"]:
            fail("the proxy way out needs egress.proxy host and port")
        s["heliosphere"] = hs
        save_state(s)
    out({"ok": True, "settings": hs, "dirty": True})


# ------------------------------------------------------------- status
def hs_exec(*cmd, timeout=15):
    """Run a command inside the container; None on any failure."""
    r = run(["nixos-container", "run", HS_CONTAINER, "--", *cmd], timeout=timeout)
    return r.stdout if r.returncode == 0 else None


def hs_status(hs):
    st = {}
    ts = None
    txt = hs_exec("tailscale", "status", "--json")
    try:
        j = json.loads(txt) if txt else None
    except ValueError:
        j = None
    if isinstance(j, dict) and isinstance(j.get("Self"), dict):
        ips = [x for x in j["Self"].get("TailscaleIPs") or [] if isinstance(x, str) and ":" not in x]
        ts = {"online": bool(j["Self"].get("Online")), "ip": ips[0] if ips else None}
    st["tailscale"] = ts or {"online": None, "ip": None}
    # WireGuard
    dump = hs_exec("wg", "show", "wg-in", "dump")
    live, pub = {}, None
    if dump:
        lines = [x.split("\t") for x in dump.splitlines() if x.strip()]
        if lines and len(lines[0]) >= 2:
            pub = lines[0][1]
        for f in lines[1:]:
            if len(f) >= 7:
                try:
                    live[f[0]] = {"lastHandshake": int(f[4]) or None, "rx": int(f[5]), "tx": int(f[6])}
                except ValueError:
                    # a line we don't understand: no live data for it
                    pass
    peers = []
    for pr in hs["ingress"]["wireguard"]["peers"]:
        lv = live.get(pr.get("publicKey")) or {}
        peers.append({"name": pr["name"], "ip": pr.get("ip"), "lastHandshake": lv.get("lastHandshake"),
                      "rx": lv.get("rx"), "tx": lv.get("tx")})
    st["wireguard"] = {"publicKey": hs_server_public_key() or pub, "peers": peers}
    # OpenVPN: status file written by the server (csv)
    txt = hs_exec("cat", "/run/openvpn-ingress.status")   # written by the server (modules/heliosphere.nix)
    names = None
    if txt is not None:
        names = {f[1] for f in (re.split(r"[,\t]", x) for x in txt.splitlines()) if f and f[0] == "CLIENT_LIST" and len(f) > 1}
    st["openvpn"] = {"clients": [{"name": c["name"], "connected": (c["name"] in names) if names is not None and not c.get("revoked") else (False if names is not None else None)}
                                 for c in hs["ingress"]["openvpn"]["clients"] if not c.get("revoked")]}
    act = run(["nixos-container", "run", HS_CONTAINER, "--", "systemctl", "is-active", "3proxy"], timeout=15)
    word = (act.stdout or "").strip()
    st["proxy"] = {"running": None if act.returncode == 127 or not word else word == "active"}
    # way out
    mode = hs["egress"]["mode"]
    conf = {"direct": None, "wireguard": "wg-out.conf", "openvpn": "client.ovpn", "proxy": None}[mode]
    configured = True if mode == "direct" else (os.path.exists(hs_path("egress", conf)) if conf else bool(hs["egress"]["proxy"]))
    ip = None
    if mode != "direct":
        t = hs_exec("curl", "-s", "--max-time", "4", "https://api.ipify.org", timeout=8)
        t = (t or "").strip()
        ip = t if re.match(r"^[0-9a-fA-F.:]{3,45}\Z", t) else None
    st["egress"] = {"mode": mode, "configured": configured, "up": None if mode == "direct" else bool(ip), "publicIp": ip}
    return st


def cmd_remote(a):
    s = load_state()
    hs = hs_resolve(s)
    out({"ok": True, "enabled": HS_ENABLED, "publicHost": HS_PUBLIC_HOST, "settings": hs, "status": hs_status(hs), "dirty": state_dirty()})


# ----------------------------------------------------------- WireGuard
def cmd_remote_wg_add(a):
    hs_check_name(a.name)
    server_pub = hs_server_public_key()
    if not server_pub:
        fail("Remote access hasn't started WireGuard yet — enable it and apply first")
    host, warn = hs_endpoint()
    with Lock():
        s = load_state()
        hs = hs_resolve(s)
        wg = hs["ingress"]["wireguard"]
        if any(x["name"] == a.name for x in wg["peers"]):
            fail(f"there is already a WireGuard peer called {a.name!r}")
        used = {x.get("ip") for x in wg["peers"]}
        ip = next((f"{HS_WG_SUBNET}.{i}" for i in range(2, 255) if f"{HS_WG_SUBNET}.{i}" not in used), None)
        if ip is None:
            fail("no free WireGuard addresses left")
        priv, pub = hs_wg_keypair()
        config = hs_wg_client_config(s, wg, priv, ip, server_pub, host)
        wg["peers"].append({"name": a.name, "publicKey": pub, "ip": ip})
        s["heliosphere"] = hs
        save_state(s)
    out(hs_wg_result(a.name, ip, config, warn))


def hs_wg_keypair():
    priv = tool(["wg", "genkey"]).strip()
    return priv, tool(["wg", "pubkey"], input=priv + "\n").strip()


def hs_wg_client_config(s, wg, priv, ip, server_pub, host):
    """A peer's wg-quick config. Full tunnel sends everything home; split sends only
    the home networks this way in may reach (plus the router's DNS)."""
    if wg.get("tunnel") == "split":
        nets = {n["name"]: n for n in (s.get("networks") or CATALOG.get("defaults", []))}
        allowed = ", ".join([f"{nets[x]['prefix']}.0/24" for x in wg.get("lan", []) if x in nets] + ["10.60.0.1/32"])
    else:
        allowed = "0.0.0.0/0"
    lines = ["[Interface]", f"PrivateKey = {priv}", f"Address = {ip}/32", "DNS = 10.60.0.1"]
    if wg.get("mtu"):
        lines.append(f"MTU = {wg['mtu']}")
    lines += ["", "[Peer]", f"PublicKey = {server_pub}", f"Endpoint = {hostport(host, wg['port'])}", f"AllowedIPs = {allowed}"]
    if wg.get("keepalive"):
        lines.append(f"PersistentKeepalive = {wg['keepalive']}")
    return "\n".join(lines) + "\n"


def hs_wg_result(name, ip, config, warn):
    qr = run(["qrencode", "-t", "SVG", "-o", "-"], input=config)
    res = {"ok": True, "name": name, "ip": ip, "config": config,
           "qrSvg": qr.stdout if qr.returncode == 0 and qr.stdout.strip() else None, "dirty": True}
    if warn:
        res["endpointWarning"] = warn
    return res


def cmd_remote_wg_renew(a):
    """New key pair for an existing peer (lost phone, lost config). Same address."""
    server_pub = hs_server_public_key()
    if not server_pub:
        fail("Remote access hasn't started WireGuard yet — enable it and apply first")
    host, warn = hs_endpoint()
    with Lock():
        s = load_state()
        hs = hs_resolve(s)
        wg = hs["ingress"]["wireguard"]
        peer = next((x for x in wg["peers"] if x["name"] == a.name), None)
        if peer is None:
            fail(f"no WireGuard peer {a.name!r}")
        priv, pub = hs_wg_keypair()
        peer["publicKey"] = pub
        config = hs_wg_client_config(s, wg, priv, peer["ip"], server_pub, host)
        s["heliosphere"] = hs
        save_state(s)
    out(hs_wg_result(a.name, peer["ip"], config, warn))


def hs_wg_set_server_key(priv):
    """Store a new server private key and restart WireGuard in the container right away."""
    pub = tool(["wg", "pubkey"], input=priv + "\n").strip()
    old = hs_server_public_key()
    hs_write(hs_path("wireguard", "private"), priv + "\n", 0o600, 0o700)
    hs_write(hs_path("wireguard", "public"), pub + "\n", 0o600, 0o700)
    restarted = hs_exec("systemctl", "restart", "wireguard-wg-in.service", timeout=30) is not None
    return {"ok": True, "publicKey": pub, "previousPublicKey": old, "restarted": restarted}


def cmd_remote_wg_rotate(a):
    """New server key. Peers keep their own keys but must update the server's public key."""
    out(hs_wg_set_server_key(tool(["wg", "genkey"]).strip()))


def cmd_remote_wg_import(a):
    """Use an existing server private key (on stdin), e.g. when moving from another server."""
    priv = sys.stdin.read().strip()
    try:
        raw = base64.b64decode(priv, validate=True)
    except ValueError:
        raw = b""
    if len(raw) != 32:
        fail("that isn't a WireGuard private key (44 base64 characters)")
    out(hs_wg_set_server_key(priv))


def cmd_remote_wg_remove(a):
    with Lock():
        s = load_state()
        hs = hs_resolve(s)
        peers = hs["ingress"]["wireguard"]["peers"]
        if not any(x["name"] == a.name for x in peers):
            fail(f"no WireGuard peer {a.name!r}")
        hs["ingress"]["wireguard"]["peers"] = [x for x in peers if x["name"] != a.name]
        s["heliosphere"] = hs
        save_state(s)
    out({"ok": True, "dirty": True})


# ------------------------------------------------------------- OpenVPN
def hs_ca_dir(*parts):
    return hs_path("openvpn", "ca", *parts)


def hs_ca_cnf():
    return f"""[ca]
default_ca = CA_default
[CA_default]
dir = {hs_ca_dir()}
database = $dir/index.txt
new_certs_dir = $dir/newcerts
serial = $dir/serial
crlnumber = $dir/crlnumber
certificate = $dir/ca.crt
private_key = $dir/ca.key
default_md = sha256
default_days = 3650
default_crl_days = 3650
unique_subject = no
policy = pol
[pol]
commonName = supplied
[req]
distinguished_name = dn
prompt = no
[dn]
CN = Heliosphere CA
[v3_ca]
basicConstraints = critical,CA:TRUE
keyUsage = critical,keyCertSign,cRLSign
subjectKeyIdentifier = hash
[v3_server]
basicConstraints = CA:FALSE
keyUsage = critical,digitalSignature,keyAgreement
extendedKeyUsage = serverAuth
[v3_client]
basicConstraints = CA:FALSE
keyUsage = critical,digitalSignature
extendedKeyUsage = clientAuth
"""


def hs_ca(*args):
    return tool(["openssl", "ca", "-batch", "-config", hs_ca_dir("openssl.cnf"), *args])


def hs_sign(key_pem, cn, ext):
    """CSR from a key held in memory, signed by the CA; returns (cert PEM, serial hex)."""
    csr = tool(["openssl", "req", "-new", "-key", "/dev/stdin", "-subj", f"/CN={cn}", "-config", hs_ca_dir("openssl.cnf")], input=key_pem)
    with tempfile.TemporaryDirectory() as tmp:
        csr_f, crt_f = os.path.join(tmp, "c.csr"), os.path.join(tmp, "c.crt")
        with open(csr_f, "w") as f:
            f.write(csr)
        hs_ca("-in", csr_f, "-out", crt_f, "-extensions", ext, "-notext")
        with open(crt_f) as f:
            crt = f.read()
    serial = tool(["openssl", "x509", "-noout", "-serial"], input=crt).strip().split("=")[-1].upper()
    return crt, serial


def hs_gen_crl():
    """OpenVPN re-reads the CRL after dropping privileges, so it's 0400 and HS_OVPN_USER's (built
    beside the live one and swapped in, so a handshake never sees a half-written CRL)."""
    crl = hs_path("openvpn", "server", "crl.pem")
    hs_ca("-gencrl", "-out", crl + ".tmp")
    os.chmod(crl + ".tmp", 0o400)
    give_to(crl + ".tmp", HS_OVPN_USER)
    os.replace(crl + ".tmp", crl)


def hs_pki_init():
    if os.path.exists(hs_path("openvpn", "server", "server.crt")) and os.path.exists(hs_ca_dir("ca.key")):
        return
    ca, srv = hs_ca_dir(), hs_path("openvpn", "server")
    for dpath in (ca, os.path.join(ca, "newcerts")):
        os.makedirs(dpath, mode=0o700, exist_ok=True)
    os.chmod(ca, 0o700)
    os.makedirs(srv, mode=0o700, exist_ok=True)            # the container mounts it read-only
    os.chmod(srv, 0o700)
    give_to(srv, HS_OVPN_USER)
    hs_write(hs_ca_dir("openssl.cnf"), hs_ca_cnf(), 0o600, 0o700)
    for name, content in (("index.txt", ""), ("serial", "01\n"), ("crlnumber", "01\n")):
        if not os.path.exists(hs_ca_dir(name)):
            hs_write(hs_ca_dir(name), content, 0o600, 0o700)
    ca_key = tool(["openssl", "genpkey", "-algorithm", "EC", "-pkeyopt", "ec_paramgen_curve:P-256"])
    hs_write(hs_ca_dir("ca.key"), ca_key, 0o600, 0o700)
    tool(["openssl", "req", "-new", "-x509", "-key", hs_ca_dir("ca.key"), "-config", hs_ca_dir("openssl.cnf"),
          "-extensions", "v3_ca", "-days", "3650", "-out", hs_ca_dir("ca.crt")])
    os.chmod(hs_ca_dir("ca.crt"), 0o600)
    key = tool(["openssl", "genpkey", "-algorithm", "EC", "-pkeyopt", "ec_paramgen_curve:P-256"])
    crt, _ = hs_sign(key, HS_SERVER_CN, "v3_server")
    hs_write(os.path.join(srv, "server.key"), key, 0o600)
    # OpenVPN reads these (and server.key) as root before it drops privileges; persist-key keeps them
    hs_write(os.path.join(srv, "server.crt"), crt, 0o600)
    hs_write(os.path.join(srv, "ca.crt"), read_plain(hs_ca_dir("ca.crt")), 0o600)
    tlsf = os.path.join(srv, "tls-crypt.key")
    tool(["openvpn", "--genkey", "secret", tlsf])
    os.chmod(tlsf, 0o600)
    hs_gen_crl()


def cmd_remote_ovpn_add(a):
    hs_check_name(a.name)
    host, warn = hs_endpoint()
    with Lock():
        s = load_state()
        hs = hs_resolve(s)
        ov = hs["ingress"]["openvpn"]
        if any(x["name"] == a.name and not x.get("revoked") for x in ov["clients"]):
            fail(f"there is already an OpenVPN client called {a.name!r}")
        hs_pki_init()
        key = tool(["openssl", "genpkey", "-algorithm", "EC", "-pkeyopt", "ec_paramgen_curve:P-256"])
        crt, serial = hs_sign(key, a.name, "v3_client")
        ov["clients"].append({"name": a.name, "serial": serial, "revoked": False})
        s["heliosphere"] = hs
        save_state(s)
        config = (f"client\ndev tun\nproto {ov['proto']}\nremote {host} {ov['port']}\nnobind\npersist-key\npersist-tun\n"
                  f"remote-cert-tls server\nverify-x509-name {HS_SERVER_CN} name\ncipher AES-256-GCM\n"
                  "data-ciphers AES-256-GCM:CHACHA20-POLY1305\nauth-nocache\n"
                  f"<ca>\n{read_plain(hs_ca_dir('ca.crt')).strip()}\n</ca>\n<cert>\n{crt.strip()}\n</cert>\n"
                  f"<key>\n{key.strip()}\n</key>\n<tls-crypt>\n{read_plain(hs_path('openvpn', 'server', 'tls-crypt.key')).strip()}\n</tls-crypt>\n")
    res = {"ok": True, "name": a.name, "serial": serial, "config": config, "dirty": True}
    if warn:
        res["endpointWarning"] = warn
    out(res)


def cmd_remote_ovpn_revoke(a):
    with Lock():
        s = load_state()
        hs = hs_resolve(s)
        c = next((x for x in hs["ingress"]["openvpn"]["clients"] if x["name"] == a.name and not x.get("revoked")), None)
        if c is None:
            fail(f"no active OpenVPN client {a.name!r}")
        hs_ca("-revoke", hs_ca_dir("newcerts", c["serial"] + ".pem"))
        hs_gen_crl()
        c["revoked"] = True
        s["heliosphere"] = hs
        save_state(s)
    out({"ok": True, "dirty": True})


# --------------------------------------------------------------- proxy
def users_lines():
    return [x for x in read_plain(hs_path("proxy", "users")).splitlines() if x.strip()]


def cmd_remote_proxy_user(a):
    hs_check_name(a.name)
    pw = sys.stdin.read().rstrip("\r\n")
    if len(pw) < 16:
        fail("The password must be at least 16 characters")
    if len(pw) > 128 or any(ord(ch) < 32 or ord(ch) == 127 for ch in pw):
        fail("The password can be at most 128 characters, without control characters")
    with Lock():
        s = load_state()
        hs = hs_resolve(s)
        hashed = tool(["openssl", "passwd", "-1", "-stdin"], input=pw + "\n").strip()
        lines = [x for x in users_lines() if not x.startswith(a.name + ":")] + [f"{a.name}:CR:{hashed}"]
        hs_write(hs_path("proxy", "users"), "\n".join(lines) + "\n", 0o400, 0o700, HS_PROXY_USER)
        users = hs["ingress"]["proxy"]["users"]
        if a.name not in users:
            users.append(a.name)
        s["heliosphere"] = hs
        save_state(s)
    out({"ok": True, "dirty": True})


def cmd_remote_proxy_user_remove(a):
    with Lock():
        s = load_state()
        hs = hs_resolve(s)
        px = hs["ingress"]["proxy"]
        if a.name not in px["users"]:
            fail(f"no proxy user {a.name!r}")
        if px["enabled"] and px["users"] == [a.name]:
            fail("the proxy is on and needs at least one user — turn it off first")
        px["users"] = [x for x in px["users"] if x != a.name]
        if os.path.exists(hs_path("proxy", "users")):
            lines = [x for x in users_lines() if not x.startswith(a.name + ":")]
            hs_write(hs_path("proxy", "users"), "".join(x + "\n" for x in lines), 0o400, 0o700, HS_PROXY_USER)
        s["heliosphere"] = hs
        save_state(s)
    out({"ok": True, "dirty": True})


# -------------------------------------------------------------- egress
WG_DROP = {"dns", "postup", "preup", "postdown", "predown", "saveconfig", "table"}
OVPN_DROP = {"up", "down", "script-security", "route-up", "route-pre-down", "ipchange", "tls-verify", "auth-user-pass-verify",
             "client-connect", "client-disconnect", "learn-address", "plugin", "management", "up-restart", "down-pre",
             "dns-updown", "setenv", "setenv-safe", "push", "push-reset", "daemon", "user", "group",
             "engine", "tls-crypt-v2-verify", "client-crresponse"}
OVPN_PATHS = {"ca", "cert", "key", "dh", "pkcs12", "tls-auth", "tls-crypt", "tls-crypt-v2", "secret", "crl-verify", "extra-certs",
              "config", "cd", "chroot", "writepid", "log", "log-append", "status", "ifconfig-pool-persist", "askpass", "http-proxy-user-pass",
              "capath", "tmp-dir", "iproute", "pkcs11-providers", "replay-persist", "dev-node", "tls-export-cert",
              "client-config-dir", "auth-gen-token-secret", "port-share"}
# Options OpenVPN takes as an inline <tag>…</tag> block (file contents, never a path)
OVPN_INLINE = {"ca", "cert", "key", "dh", "pkcs12", "tls-auth", "tls-crypt", "tls-crypt-v2", "secret", "crl-verify",
               "extra-certs", "http-proxy-user-pass", "auth-user-pass"}
OVPN_LINE_MAX = 255          # OpenVPN reads lines into a 256-byte buffer and splits longer ones
UPLOAD_CTRL_RE = re.compile(r"[\x00-\x08\x0a-\x1f\x7f]")   # inside a line (the text is split on \n first)


def ovpn_tokens(line):
    """Split a config line the way OpenVPN's parse_line() does; None where OpenVPN would refuse it.

    Unquoted, "double" and 'single' quoted tokens; a backslash escapes \\, " or a space (OpenVPN
    rejects any other escape) except inside single quotes; # or ; starting a token ends the line."""
    toks, cur, state, esc = [], [], "init", False
    for c in line:
        if esc:
            if c not in "\\\" \t":
                return None
            cur.append(c)
            esc = False
            state = "bare" if state == "init" else state
            continue
        if c == "\\" and state != "squote":
            esc = True
            continue
        if state == "init":
            if c in " \t":
                continue
            if c in "#;":
                break
            if c in "\"'":
                state = "dquote" if c == '"' else "squote"
            else:
                cur.append(c)
                state = "bare"
        elif state == "bare":
            if c in " \t":
                toks.append("".join(cur))
                cur, state = [], "init"
            else:
                cur.append(c)
        elif c == ('"' if state == "dquote" else "'"):
            if not cur:
                return None                              # "" : leave the corner case to nobody
            toks.append("".join(cur))
            cur, state = [], "init"
        else:
            cur.append(c)
    if esc or state in ("dquote", "squote"):
        return None
    if state == "bare":
        toks.append("".join(cur))
    return toks


def ovpn_quote(tok):
    if re.fullmatch(r"[^\s\"'\\#;]+", tok):
        return tok
    return '"' + tok.replace("\\", "\\\\").replace('"', '\\"') + '"'


def upload_lines(text, what, limit=None):
    lines = text.split("\n")
    for n, line in enumerate(lines, 1):
        if UPLOAD_CTRL_RE.search(line):
            fail(f"line {n} of the {what} has a control character")
        if limit and len(line.encode()) > limit:
            fail(f"line {n} of the {what} is longer than {limit} bytes")
    return lines


def clean_wg_conf(text):
    section, keep, dropped, seen = None, [], [], {}
    for line in upload_lines(text, "WireGuard config"):
        # wg-quick strips "#…" before it looks at a line, so judge what it will see
        st = line.split("#", 1)[0].strip()
        m = re.match(r"^\[([A-Za-z]+)\]\Z", st)
        if m:
            section = m.group(1).lower()
            keep.append(line)
            continue
        if st.startswith("["):
            fail(f"unexpected line in the WireGuard config: {st[:60]!r}")
        k = st.split("=", 1)[0].strip().lower()        # wg-quick takes a line without "=" as a key too
        if k and not section:
            fail("the WireGuard config has settings before its [Interface] section")
        if section and k:
            if k in WG_DROP:
                dropped.append(k)
                continue
            seen[(section, k)] = True
        keep.append(line)
    for sec, k in (("interface", "privatekey"), ("interface", "address"), ("peer", "publickey"), ("peer", "endpoint"), ("peer", "allowedips")):
        if (sec, k) not in seen:
            fail(f"the WireGuard config needs {k} in its [{sec.capitalize()}] section")
    return "\n".join(keep).strip() + "\n", dropped


def clean_ovpn_conf(text):
    """Keep a provider's client config minus anything that runs programs or reads/writes files.

    Lines are judged the way OpenVPN reads them (ovpn_tokens), and every kept directive is
    written back canonically quoted, so what is stored means one thing to OpenVPN."""
    keep, dropped, flags = [], [], set()
    block, conn = None, False          # open inline <block>; inside a <connection> block
    for line in upload_lines(text, "OpenVPN config", OVPN_LINE_MAX):
        st = line.strip(" \t")
        if block:
            # OpenVPN ends an inline block at the first line that STARTS with </tag>
            if st.startswith(f"</{block}>"):
                keep.append(f"</{block}>")
                block = None
            else:
                keep.append(line)
            continue
        if conn and st.startswith("</connection>"):  # read like an inline block's end, too
            conn = False
            keep.append("</connection>")
            continue
        if not st or st[0] in "#;":
            keep.append(line)
            continue
        # A loose reading first: anything that might be a dropped directive goes, however it's spelled
        loose = re.sub(r"[\\\"']", "", st.split()[0]).lower()
        loose = loose[2:] if loose.startswith("--") else loose
        if loose in OVPN_DROP or loose.startswith("management"):
            dropped.append(loose)
            continue
        toks = ovpn_tokens(st)
        if toks is None:
            fail(f"can't read this line of the OpenVPN config (quotes or backslashes): {st[:60]!r}")
        if not toks:
            keep.append(line)                  # only a comment after all
            continue
        if toks[0].startswith("--"):
            toks[0] = toks[0][2:]
        k = toks[0].lower()
        if len(toks) == 1 and len(k) >= 2 and k[0] == "<" and k[-1] == ">":
            tag = toks[0][1:-1]
            if tag.startswith("/"):
                fail(f"unexpected {toks[0]!r} in the OpenVPN config")
            if tag == "connection" and not conn:
                conn = True
                keep.append("<connection>")
                continue
            if conn:
                fail("inline blocks inside a <connection> block aren't supported")
            if tag not in OVPN_INLINE:
                fail(f"inline <{tag[:40]}> blocks aren't supported")
            block = tag
            keep.append(f"<{tag}>")
            continue
        if k in OVPN_DROP or k.startswith("management"):
            dropped.append(k)
            continue
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", toks[0]):
            fail(f"not an OpenVPN option: {toks[0][:40]!r}")
        if k in ("http-proxy", "socks-proxy") and len(toks) > 3:
            fail(f"“{k}” with a credentials file isn't supported; remove the file argument")
        if k in OVPN_PATHS or loose in OVPN_PATHS:
            fail(f"“{k}” refers to a file on disk; paste its contents inline (<{k}>…</{k}>) or remove it")
        if k == "providers" and not all(re.fullmatch(r"[A-Za-z0-9_-]+", x) for x in toks[1:]):
            fail("“providers” takes OpenSSL provider names (like legacy default), not paths")
        flags.add(k)
        if k == "auth-user-pass" and len(toks) > 1:
            keep.append(f"auth-user-pass {HS_EGRESS_AUTH_PATH}")
            continue
        canon = " ".join(ovpn_quote(x) for x in toks)
        if len(canon.encode()) > OVPN_LINE_MAX:       # quoting made it longer: OpenVPN would split it
            fail(f"a line of the OpenVPN config is too long once quoted: {st[:60]!r}")
        keep.append(canon)
    if block:
        fail(f"unterminated <{block}> block")
    if conn:
        fail("unterminated <connection> block")
    if "client" not in flags:
        fail("the OpenVPN config must be a client config (a “client” line)")
    if "remote" not in flags:
        fail("the OpenVPN config needs a “remote” line")
    return "\n".join(keep).strip() + "\n", dropped


def egress_upload(kind, raw):
    """(file name, contents, dropped directives) for an uploaded way-out file; fail() on bad input."""
    if len(raw) > 64 * 1024 or b"\0" in raw:
        fail("That doesn't look like a configuration file (too big or binary)")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        fail("That doesn't look like a configuration file (not UTF-8 text)")
    text = text.replace("\r\n", "\n")
    if kind == "wireguard":
        body, dropped = clean_wg_conf(text)
        return "wg-out.conf", body, dropped
    if kind == "openvpn":
        body, dropped = clean_ovpn_conf(text)
        return "client.ovpn", body, dropped
    if text.strip().startswith("{"):                # proxy credentials
        try:
            j = json.loads(text)
        except ValueError:
            fail("invalid JSON")
        if not isinstance(j, dict) or set(j) - {"user", "password"}:
            fail("proxy credentials are {\"user\": …, \"password\": …}")
        if not all(isinstance(j.get(k), str) and 0 < len(j[k]) <= 255 and not UPLOAD_CTRL_RE.search(j[k].replace("\t", " "))
                   for k in ("user", "password")):
            fail("user and password must be non-empty text (max 255 characters, one line, no control characters)")
        return "proxy.json", json.dumps({"user": j["user"], "password": j["password"]}) + "\n", []
    lines = [x for x in upload_lines(text, "auth.txt") if x.strip()]
    if len(lines) != 2 or any(len(x) > 255 for x in lines):
        fail("auth.txt is two lines: the user name, then the password")
    return "auth.txt", "\n".join(x.rstrip() for x in lines) + "\n", []


def cmd_remote_egress_upload(a):
    name, body, dropped = egress_upload(a.kind, sys.stdin.buffer.read())
    hs_write(hs_path("egress", name), body)
    out({"ok": True, "stored": name, "removedDirectives": sorted(set(dropped))})


def cmd_remote_egress_clear(a):
    hs = hs_resolve(load_state())
    if hs["egress"]["mode"] == a.kind:
        fail(f"the way out is set to {a.kind} — switch it to direct first")
    files = {"wireguard": ["wg-out.conf"], "openvpn": ["client.ovpn"], "auth": ["auth.txt", "proxy.json"]}[a.kind]
    removed = []
    for f in files:
        try:
            os.unlink(hs_path("egress", f))
            removed.append(f)
        except OSError:
            # already gone
            pass
    out({"ok": True, "removed": removed})


def describe_remote(old, new):
    msgs = []
    o, n = hs_resolve(old), hs_resolve(new)
    for m, label in HS_METHODS.items():
        oi, ni = o["ingress"][m], n["ingress"][m]
        if oi["enabled"] != ni["enabled"]:
            msgs.append(f"Remote access: {'enable' if ni['enabled'] else 'disable'} {label}")
        rest = [k for k in ni if k not in ("enabled", "peers", "clients", "users") and oi.get(k) != ni.get(k)]
        if rest:
            msgs.append(f"Remote access: change {label} settings")
    for key, noun, m in (("peers", "WireGuard peer", "wireguard"), ("users", "proxy user", "proxy")):
        on = {x["name"] if isinstance(x, dict) else x for x in o["ingress"][m][key]}
        nn = {x["name"] if isinstance(x, dict) else x for x in n["ingress"][m][key]}
        msgs += [f"Remote access: add {noun} {x}" for x in sorted(nn - on)]
        msgs += [f"Remote access: remove {noun} {x}" for x in sorted(on - nn)]
    ok_ = {x["name"]: x.get("publicKey") for x in o["ingress"]["wireguard"]["peers"]}
    msgs += [f"Remote access: new key for WireGuard peer {x['name']}" for x in n["ingress"]["wireguard"]["peers"]
             if x["name"] in ok_ and ok_[x["name"]] != x.get("publicKey")]
    oc = {(c["name"], c["serial"]): c for c in o["ingress"]["openvpn"]["clients"]}
    for k, c in {(c["name"], c["serial"]): c for c in n["ingress"]["openvpn"]["clients"]}.items():
        if k not in oc:
            msgs.append(f"Remote access: add OpenVPN client {k[0]}")
        if c.get("revoked") and not (oc.get(k) or {}).get("revoked"):
            msgs.append(f"Remote access: revoke OpenVPN client {k[0]}")
    if o["egress"]["mode"] != n["egress"]["mode"]:
        msgs.append(f"Remote access: way out → {n['egress']['mode']}")
    if o["egress"]["killswitch"] != n["egress"]["killswitch"]:
        msgs.append(f"Remote access: kill switch {'on' if n['egress']['killswitch'] else 'off'}")
    if o["egress"]["proxy"] != n["egress"]["proxy"]:
        msgs.append("Remote access: change way-out proxy")
    return msgs


# ------------------------------------------------------------------ admin account
# The admin login's password (shadow, via chpasswd) and two-factor sign-in for
# Cockpit: a TOTP secret in pam_google_authenticator's file format, kept root-only
# in TOTP_DIR/<user> (airlock.nix points the cockpit PAM service there).
ADMIN = CFG.get("admin", {}) or {}
TOTP_DIR = ADMIN.get("totpDir") or os.path.join(DATA_DIR, "totp")
TOTP_PENDING_SECONDS = 15 * 60
TOTP_PERIOD = 30


def admin_user():
    user = ADMIN.get("user") or ""
    if not re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", user):
        fail("No admin user in the site configuration")
    return user


def totp_file(user):
    return os.path.join(TOTP_DIR, user)


def totp_pending_file(user):
    return os.path.join(TOTP_DIR, f".pending-{user}.json")


def totp_code(secret, counter):
    """RFC 6238 (SHA-1, 6 digits) for one 30-second step."""
    key = base64.b32decode(secret + "=" * (-len(secret) % 8))
    mac = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    off = mac[-1] & 0x0F
    return "%06d" % ((struct.unpack(">I", mac[off:off + 4])[0] & 0x7FFFFFFF) % 1000000)


def totp_ok(secret, code, at=None):
    """The code for now, or one step either side (clock drift)."""
    step = int((at if at is not None else time.time()) // TOTP_PERIOD)
    return any(hmac.compare_digest(totp_code(secret, step + d), code) for d in (-1, 0, 1))


def write_private(path, text):
    """Root-only file, written atomically."""
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text)
    os.replace(tmp, path)


def password_changed(user):
    """When the password was last set (shadow's day count), or None."""
    for line in read("/etc/shadow").splitlines():
        f = line.split(":")
        if len(f) > 2 and f[0] == user:
            return int(f[2]) * 86400 if f[2].isdigit() else None
    return None


def cmd_account(a):
    user = admin_user()
    pending = read_json(totp_pending_file(user), {}) or {}
    out({"ok": True, "user": user, "sshKeys": ADMIN.get("sshKeys", 0),
         "passwordChanged": password_changed(user),
         "totp": os.path.exists(totp_file(user)),
         "totpPending": bool(pending) and now() - pending.get("created", 0) < TOTP_PENDING_SECONDS})


def cmd_account_password(a):
    """New password on stdin (one line), never argv. Cockpit already asked for the
    current one: these commands only run with administrative access (sudo)."""
    user = admin_user()
    pw = sys.stdin.read().rstrip("\r\n")
    if not 12 <= len(pw) <= 128:
        fail("Password must be 12 to 128 characters")
    if any(c in pw for c in "\r\n\0"):
        fail("Password can't contain line breaks")
    if pw.lower() == user.lower():
        fail("Password can't be the login name")
    r = run(["chpasswd"], input=f"{user}:{pw}\n")
    if r.returncode != 0:
        fail(f"chpasswd failed: {r.stderr.strip() or r.stdout.strip()}")
    out({"ok": True, "user": user})


def cmd_account_2fa_begin(a):
    """Make a new secret and show it once (QR + text); it only takes effect after
    account-2fa-enable proves the authenticator app has it."""
    user = admin_user()
    secret = base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")
    write_private(totp_pending_file(user), json.dumps({"secret": secret, "created": now()}))
    label = f"HeliOS ({CFG.get('hostName', 'helios')})"
    uri = "otpauth://totp/{}:{}?{}".format(urllib.parse.quote(label), urllib.parse.quote(user), urllib.parse.urlencode(
        {"secret": secret, "issuer": label, "algorithm": "SHA1", "digits": 6, "period": TOTP_PERIOD}, quote_via=urllib.parse.quote))
    qr = run(["qrencode", "-t", "SVG", "-o", "-"], input=uri)
    out({"ok": True, "user": user, "secret": secret, "uri": uri, "qrSvg": qr.stdout if qr.returncode == 0 else None})


def cmd_account_2fa_enable(a):
    """Code from the app on stdin. On a match, the secret goes live with five
    one-use recovery codes, shown this once."""
    user = admin_user()
    code = re.sub(r"\s", "", sys.stdin.read())
    pending = read_json(totp_pending_file(user), {}) or {}
    if not pending.get("secret") or now() - pending.get("created", 0) >= TOTP_PENDING_SECONDS:
        fail("The setup expired: start again")
    if not re.fullmatch(r"\d{6}", code):
        fail("Enter the 6-digit code from the app")
    if not totp_ok(pending["secret"], code):
        fail("That code doesn't match. Check the phone's clock is right and try the next code")
    recovery = [f"{secrets.randbelow(10 ** 8):08d}" for _ in range(5)]
    write_private(totp_file(user), "\n".join([
        pending["secret"],
        '" RATE_LIMIT 3 30',
        '" WINDOW_SIZE 3',
        '" DISALLOW_REUSE',
        '" TOTP_AUTH',
        *recovery]) + "\n")
    os.chmod(totp_file(user), 0o400)
    os.remove(totp_pending_file(user))
    out({"ok": True, "user": user, "recoveryCodes": recovery})


def cmd_account_2fa_disable(a):
    user = admin_user()
    for f in (totp_file(user), totp_pending_file(user)):
        try:
            os.remove(f)
        except FileNotFoundError:
            pass
    out({"ok": True, "user": user})


# ------------------------------------------------------------------ main
NO_GIT = {"account", "account-password", "account-2fa-begin", "account-2fa-enable", "account-2fa-disable", "detect", "reboot-status", "reboot", "reboot-window", "reboot-check", "unit-failed", "recover", "logwatch", "logwatch-model-get", "logwatch-model-fetch", "logwatch-model-remove", "logwatch-guard", "logwatch-resume", "services", "printing", "printer-models", "printer-test", "printer-pause", "print-cancel", "upnp-close", "qos-suggest", "remote-wg-rotate", "remote-wg-import", "ups-event", "ups-forward", "vulnix-report", "notify-setup", "notify-credentials", "notify-info", "notify-test", "notify-recent", "uplink-check", "counters", "vnstat", "qos", "upnp-clear"}


def main():
    ap = argparse.ArgumentParser(prog="helios-ctl")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status").set_defaults(fn=cmd_status)
    sub.add_parser("devices").set_defaults(fn=cmd_devices)
    x = sub.add_parser("device-set")
    x.add_argument("json")
    x.set_defaults(fn=cmd_device_set)
    sub.add_parser("networks").set_defaults(fn=cmd_networks)
    x = sub.add_parser("network-set")
    x.add_argument("json")
    x.set_defaults(fn=cmd_network_set)
    x = sub.add_parser("network-remove")
    x.add_argument("name")
    x.set_defaults(fn=cmd_network_remove)
    x = sub.add_parser("device-remove")
    x.add_argument("mac")
    x.set_defaults(fn=cmd_device_remove)
    x = sub.add_parser("block")
    x.add_argument("mac")
    x.add_argument("state", choices=["on", "off"])
    x.set_defaults(fn=cmd_block)
    x = sub.add_parser("settings-set")
    x.add_argument("--qos", choices=["on", "off"])
    x.add_argument("--down")
    x.add_argument("--up")
    x.add_argument("--upnp", choices=["on", "off"])
    x.set_defaults(fn=cmd_settings_set)
    sub.add_parser("pending").set_defaults(fn=cmd_pending)
    sub.add_parser("discard").set_defaults(fn=cmd_discard)
    x = sub.add_parser("restore")
    x.add_argument("rev")
    x.set_defaults(fn=cmd_restore)
    sub.add_parser("apply-start").set_defaults(fn=cmd_apply_start)
    sub.add_parser("apply-run").set_defaults(fn=cmd_apply_run)
    sub.add_parser("apply-status").set_defaults(fn=cmd_apply_status)
    sub.add_parser("apply-dismiss").set_defaults(fn=cmd_apply_dismiss)
    sub.add_parser("confirm").set_defaults(fn=cmd_confirm)
    x = sub.add_parser("rollback")
    x.add_argument("--auto", action="store_true")
    x.set_defaults(fn=cmd_rollback)
    sub.add_parser("adguard-sync").set_defaults(fn=cmd_adguard_sync)
    sub.add_parser("counters").set_defaults(fn=cmd_counters)
    sub.add_parser("vnstat").set_defaults(fn=cmd_vnstat)
    sub.add_parser("qos").set_defaults(fn=cmd_qos)
    x = sub.add_parser("qos-set")
    x.add_argument("json")
    x.set_defaults(fn=cmd_qos_set)
    x = sub.add_parser("qos-suggest")
    x.add_argument("--measure", action="store_true", help="pause shaping ~20 s and measure the line")
    x.set_defaults(fn=cmd_qos_suggest)
    sub.add_parser("upnp").set_defaults(fn=cmd_upnp)
    sub.add_parser("upnp-clear").set_defaults(fn=cmd_upnp_clear)
    x = sub.add_parser("upnp-close")
    x.add_argument("proto")
    x.add_argument("port", type=int)
    x.set_defaults(fn=cmd_upnp_close)
    for nm, fn in (("upnp-block", cmd_upnp_block), ("upnp-unblock", cmd_upnp_unblock)):
        x = sub.add_parser(nm)
        x.add_argument("json")
        x.set_defaults(fn=fn)
    sub.add_parser("history").set_defaults(fn=cmd_history)
    sub.add_parser("backup").set_defaults(fn=cmd_backup)
    x = sub.add_parser("notify-setup")
    x.add_argument("--rotate", action="store_true")
    x.set_defaults(fn=cmd_notify_setup)
    x = sub.add_parser("notify-credentials")
    x.add_argument("--user", required=True)
    x.set_defaults(fn=cmd_notify_credentials)
    x = sub.add_parser("notify-info")
    x.add_argument("--reveal", action="store_true")
    x.set_defaults(fn=cmd_notify_info)
    x = sub.add_parser("notify-test")
    x.add_argument("kind", choices=["flare", "aurora"])
    x.set_defaults(fn=cmd_notify_test)
    sub.add_parser("notify-recent").set_defaults(fn=cmd_notify_recent)
    sub.add_parser("detect").set_defaults(fn=cmd_detect)
    sub.add_parser("hardware").set_defaults(fn=cmd_hardware)
    x = sub.add_parser("driver-set")
    x.add_argument("json")
    x.set_defaults(fn=cmd_driver_set)
    x = sub.add_parser("hardware-set")
    x.add_argument("json")
    x.set_defaults(fn=cmd_hardware_set)
    x = sub.add_parser("hardware-remove")
    x.add_argument("id")
    x.set_defaults(fn=cmd_hardware_remove)
    x = sub.add_parser("ups-event")
    x.add_argument("message", nargs="*")
    x.set_defaults(fn=cmd_ups_event)
    x = sub.add_parser("restrict-unnamed")
    x.add_argument("state", choices=["on", "off"])
    x.set_defaults(fn=cmd_restrict_unnamed)
    sub.add_parser("reboot-status").set_defaults(fn=cmd_reboot_status)
    x = sub.add_parser("maintenance-set")
    x.add_argument("json")
    x.set_defaults(fn=cmd_maintenance_set)
    for nm, fn in (("reboot", cmd_reboot), ("reboot-window", cmd_reboot_window), ("reboot-check", cmd_reboot_check)):
        sub.add_parser(nm).set_defaults(fn=fn)
    x = sub.add_parser("unit-failed")
    x.add_argument("unit")
    x.set_defaults(fn=cmd_unit_failed)
    sub.add_parser("recover").set_defaults(fn=cmd_recover)
    sub.add_parser("logwatch").set_defaults(fn=cmd_logwatch)
    x = sub.add_parser("logwatch-set")
    x.add_argument("json")
    x.set_defaults(fn=cmd_logwatch_set)
    sub.add_parser("logwatch-reset").set_defaults(fn=cmd_logwatch_reset)
    for nm, fn in (("logwatch-model-get", cmd_logwatch_model_get), ("logwatch-model-fetch", cmd_logwatch_model_fetch)):
        x = sub.add_parser(nm)
        x.add_argument("preset")
        x.set_defaults(fn=fn)
    x = sub.add_parser("logwatch-model-remove")
    x.add_argument("file")
    x.set_defaults(fn=cmd_logwatch_model_remove)
    x = sub.add_parser("logwatch-guard")
    x.add_argument("--check", action="store_true")
    x.set_defaults(fn=cmd_logwatch_guard)
    sub.add_parser("logwatch-resume").set_defaults(fn=cmd_logwatch_resume)
    sub.add_parser("services").set_defaults(fn=cmd_services)
    x = sub.add_parser("service-set")
    x.add_argument("id")
    x.add_argument("state", choices=["on", "off", "default"])
    x.set_defaults(fn=cmd_service_set)
    sub.add_parser("printing").set_defaults(fn=cmd_printing)
    x = sub.add_parser("printer-models")
    x.add_argument("query", nargs="?", default="")
    x.set_defaults(fn=cmd_printer_models)
    for nm, fn in (("printer-set", cmd_printer_set), ("printing-drivers", cmd_printing_drivers)):
        x = sub.add_parser(nm)
        x.add_argument("json")
        x.set_defaults(fn=fn)
    for nm, fn in (("printer-remove", cmd_printer_remove), ("printer-test", cmd_printer_test)):
        x = sub.add_parser(nm)
        x.add_argument("name")
        x.set_defaults(fn=fn)
    x = sub.add_parser("printer-pause")
    x.add_argument("name")
    x.add_argument("state", choices=["on", "off"])
    x.set_defaults(fn=cmd_printer_pause)
    x = sub.add_parser("print-cancel")
    x.add_argument("job")
    x.set_defaults(fn=cmd_print_cancel)
    sub.add_parser("profiles").set_defaults(fn=cmd_profiles)
    x = sub.add_parser("profile-set")
    x.add_argument("json")
    x.set_defaults(fn=cmd_profile_set)
    x = sub.add_parser("profile-remove")
    x.add_argument("name")
    x.set_defaults(fn=cmd_profile_remove)
    sub.add_parser("remote").set_defaults(fn=cmd_remote)
    x = sub.add_parser("remote-set")
    x.add_argument("json")
    x.set_defaults(fn=cmd_remote_set)
    sub.add_parser("remote-wg-rotate").set_defaults(fn=cmd_remote_wg_rotate)
    sub.add_parser("remote-wg-import").set_defaults(fn=cmd_remote_wg_import)
    for nm, fn in (("remote-wg-add", cmd_remote_wg_add), ("remote-wg-remove", cmd_remote_wg_remove), ("remote-wg-renew", cmd_remote_wg_renew),
                   ("remote-ovpn-add", cmd_remote_ovpn_add), ("remote-ovpn-revoke", cmd_remote_ovpn_revoke),
                   ("remote-proxy-user", cmd_remote_proxy_user), ("remote-proxy-user-remove", cmd_remote_proxy_user_remove)):
        x = sub.add_parser(nm)
        x.add_argument("name")
        x.set_defaults(fn=fn)
    for nm, fn in (("remote-egress-upload", cmd_remote_egress_upload), ("remote-egress-clear", cmd_remote_egress_clear)):
        x = sub.add_parser(nm)
        x.add_argument("kind", choices=["wireguard", "openvpn", "auth"])
        x.set_defaults(fn=fn)
    sub.add_parser("ups-forward").set_defaults(fn=cmd_ups_forward)
    for nm, fn in (("account", cmd_account), ("account-password", cmd_account_password),
                   ("account-2fa-begin", cmd_account_2fa_begin), ("account-2fa-enable", cmd_account_2fa_enable),
                   ("account-2fa-disable", cmd_account_2fa_disable)):
        sub.add_parser(nm).set_defaults(fn=fn)
    x = sub.add_parser("vulnix-report")
    x.add_argument("file")
    x.set_defaults(fn=cmd_vulnix_report)
    x = sub.add_parser("uplink-check")
    x.add_argument("--targets", default="1.1.1.1,9.9.9.9,8.8.8.8")
    x.add_argument("--interval", type=int, default=30)
    x.set_defaults(fn=cmd_uplink_check)
    a = ap.parse_args()
    try:
        if a.cmd not in NO_GIT:
            git_ready()   # first run: snapshot configDir so every later change is a diff
        a.fn(a)
    except RuntimeError as e:
        fail(str(e))


if __name__ == "__main__":
    main()
