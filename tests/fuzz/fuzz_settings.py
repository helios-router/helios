"""Fuzz helios-ctl's JSON settings validators: qos-set, logwatch-set, maintenance-set, remote-set.

The web UI sends these as JSON; what passes ends up in state.json, which Nix
reads with builtins.fromJSON and turns into tc, systemd and container config.
A finding is an uncaught exception, a stored state that isn't plain JSON (NaN,
Infinity), a stored string that isn't printable ASCII, or a stored setting the
same validator would refuse when sent back.
"""
import copy
import json
import sys

import atheris

with atheris.instrument_imports():
    import helios_fuzz

ctl = helios_fuzz.load()
for fn in (ctl.cmd_qos_set, ctl.cmd_logwatch_set, ctl.cmd_maintenance_set, ctl.cmd_remote_set, ctl.parse_json_arg, ctl._port):
    atheris.instrument_func(fn)

BASE = {"devices": [], "qos": {"enabled": False, "down": None, "up": None}, "upnp": {"enabled": True}}
STATE = {}


class NoLock:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class Args:
    def __init__(self, text):
        self.json = text


def load_state():
    return copy.deepcopy(STATE["cur"])


def save_state(s):
    STATE["saved"] = copy.deepcopy(s)


ctl.Lock = NoLock
ctl.load_state = load_state
ctl.save_state = save_state
ctl.out = lambda obj: None
ctl.parse_upnp = lambda: []
ctl.state_dirty = lambda: False

KEYS = sorted({*ctl.QOS_DEFAULTS, *ctl.LW_CATALOG.get("defaults", {}), "cpuPressure", "memoryPressure", "pauseHours",
               "rebootWindow", "days", "time", "ingress", "egress", "tailscale", "wireguard", "openvpn", "proxy", "enabled",
               "exitNode", "hostname", "lan", "port", "tunnel", "keepalive", "mtu", "proto", "socksPort", "httpPort",
               "networks", "mode", "killswitch", "type", "host", "peers", "users"})
WORDS = sorted({x for v in ctl.QOS_CHOICES.values() for x in v} | set(ctl.LW_CATALOG.get("presets", {})) | set(ctl.LW_BACKENDS)
               | set(ctl.HS_MODES) | set(ctl.DAYS) | set(ctl.net_names({})) | {"custom", "site", "socks5", "http", "full", "split",
               "udp", "tcp", "500mbit", "50mbit", "1.5gbit", "04:00", "/var/lib/llm/m.gguf", "vpn.example.com", "10.0.0.1", "::1",
               "heliosphere", "٥mbit", "٠٤:٠٠", "a\nb", ""})
TARGETS = [("qos", ctl.cmd_qos_set), ("logwatch", ctl.cmd_logwatch_set), ("maintenance", ctl.cmd_maintenance_set),
           ("heliosphere", ctl.cmd_remote_set)]


def value(fdp, depth):
    kind = fdp.ConsumeIntInRange(0, 9 if depth < 4 else 5)
    if kind == 0:
        return None
    if kind == 1:
        return fdp.ConsumeBool()
    if kind == 2:
        return fdp.PickValueInList([0, 1, -1, 5, 24, 25, 64, 65, 255, 256, 512, 1194, 1280, 1500, 65535, 65536, 2**31, 2**63, -64])
    if kind == 3:
        return fdp.ConsumeIntInRange(-2**40, 2**40)
    if kind == 4:
        return fdp.PickValueInList([0.5, 1.0, float("nan"), float("inf"), -float("inf"), 1e308])
    if kind == 5:
        return fdp.PickValueInList(WORDS) if fdp.ConsumeBool() else fdp.ConsumeUnicodeNoSurrogates(fdp.ConsumeIntInRange(0, 40))
    if kind in (6, 7):
        return obj(fdp, depth + 1)
    return [value(fdp, depth + 1) for _ in range(fdp.ConsumeIntInRange(0, 4))]


def obj(fdp, depth=0):
    d = {}
    for _ in range(fdp.ConsumeIntInRange(0, 5)):
        k = fdp.PickValueInList(KEYS) if fdp.ConsumeIntInRange(0, 7) else fdp.ConsumeUnicodeNoSurrogates(8)
        d[k] = value(fdp, depth)
    return d


def ascii_strings(x, where):
    if isinstance(x, str):
        assert all(" " <= c <= "~" for c in x), f"{where}: stored a non-ASCII or control string {x!r}"
    elif isinstance(x, dict):
        for k, v in x.items():
            ascii_strings(k, where)
            ascii_strings(v, f"{where}.{k}")
    elif isinstance(x, list):
        for v in x:
            ascii_strings(v, where)


def resend(section, stored):
    """What a UI would send back for the stored section."""
    if section == "maintenance":
        return {"rebootWindow": stored["rebootWindow"]}
    if section == "heliosphere":
        ing = {m: {k: v for k, v in c.items() if k not in ("peers", "clients", "users")} for m, c in stored["ingress"].items()}
        return {"ingress": ing, "egress": {k: v for k, v in stored["egress"].items() if v is not None}}   # no "proxy": null
    return stored


def TestOneInput(data):
    fdp = atheris.FuzzedDataProvider(data)
    section, fn = TARGETS[fdp.ConsumeIntInRange(0, len(TARGETS) - 1)]
    text = fdp.ConsumeUnicodeNoSurrogates(4096) if fdp.ConsumeIntInRange(0, 9) == 0 else json.dumps(obj(fdp))
    STATE["cur"], STATE["saved"] = copy.deepcopy(BASE), None
    try:
        fn(Args(text))
    except helios_fuzz.Rejected:
        return
    saved = STATE["saved"]
    assert saved is not None, f"{section}: accepted but saved nothing"
    json.dumps(saved, allow_nan=False)                 # Nix's fromJSON has no NaN / Infinity
    ascii_strings(saved.get(section), section)
    # Sending the stored settings back must be accepted and change nothing
    STATE["cur"], STATE["saved"] = copy.deepcopy(saved), None
    try:
        fn(Args(json.dumps(resend(section, saved[section]))))
    except helios_fuzz.Rejected as e:
        raise AssertionError(f"{section}: stored {saved[section]!r}, which it then refuses: {e}") from None
    assert STATE["saved"][section] == saved[section], f"{section}: re-sending the stored settings changed them"


if __name__ == "__main__":
    atheris.Setup(sys.argv, TestOneInput)
    atheris.Fuzz()
