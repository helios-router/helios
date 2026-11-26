"""Fuzz the WireGuard egress-config sanitizer (remote-egress-upload wireguard -> wg-out.conf).

The stored file is read back the way wg-quick's parse_options() reads it (bash
`read -r` lines, "#…" cut, key trimmed of whitespace). A finding is a hook or
setting wg-quick would act on (PostUp, DNS, Table, SaveConfig…) surviving.
"""
import sys

import atheris

with atheris.instrument_imports():
    import helios_fuzz

ctl = helios_fuzz.load()
atheris.instrument_func(ctl.clean_wg_conf)
atheris.instrument_func(ctl.egress_upload)

WG_SPACE = " \t\n\v\f\r"          # bash [[:space:]] in the C locale


def wg_quick_keys(text):
    """(section-or-None, key) pairs as wg-quick sees them."""
    interface = False
    for line in text.split("\n"):
        stripped = line.split("#", 1)[0]
        key = stripped.split("=", 1)[0].strip(WG_SPACE)
        if key.startswith("["):
            interface = key == "[Interface]"
            continue
        yield interface, key


def TestOneInput(data):
    r = helios_fuzz.run_rejecting(ctl.egress_upload, "wireguard", data)
    if r is None:
        return
    name, body, _ = r
    assert name == "wg-out.conf"
    for interface, key in wg_quick_keys(body):
        # wg-quick matches case-sensitively; be stricter and also catch other spellings
        assert key.lower() not in ctl.WG_DROP, f"wg-quick would act on {key!r} (interface section: {interface})"
        assert "\0" not in key
    again = helios_fuzz.run_rejecting(ctl.clean_wg_conf, body)
    assert again is not None and again[0] == body and not again[1], "sanitiser isn't idempotent on its own output"


if __name__ == "__main__":
    atheris.Setup(sys.argv, TestOneInput)
    atheris.Fuzz()
