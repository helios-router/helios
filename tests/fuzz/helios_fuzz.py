"""Shared setup for the helios-ctl fuzz harnesses (Atheris / ClusterFuzzLite).

Loads pkgs/helios-ctl.py as a module with a throwaway config, data dir and
heliosphere dir, and turns fail() into an exception: a rejection is the
expected answer to bad input, not a finding. Anything else that escapes a
harness (an uncaught exception, a hang, a broken invariant) is a finding.
"""
import importlib.util
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
# In a ClusterFuzzLite build the harness is a PyInstaller bundle and helios-ctl
# rides along as data (.clusterfuzzlite/build.sh); in a checkout it is in pkgs/.
_BUNDLE = getattr(sys, "_MEIPASS", None)
CTL = os.environ.get("HELIOS_FUZZ_CTL") or (  # override: replay findings against another copy
    os.path.join(_BUNDLE, "helios", "helios-ctl.py") if _BUNDLE else os.path.join(HERE, "..", "..", "pkgs", "helios-ctl.py"))


class Rejected(Exception):
    """helios-ctl refused the input through fail()."""


def load():
    tmp = tempfile.mkdtemp(prefix="helios-fuzz-")
    hs = os.path.join(tmp, "heliosphere")
    os.makedirs(os.path.join(hs, "egress"))
    for f in ("wg-out.conf", "client.ovpn"):          # so remote-set may pick either way out
        open(os.path.join(hs, "egress", f), "w").close()
    cfg = os.path.join(tmp, "config.json")
    with open(cfg, "w") as f:
        json.dump({"configDir": os.path.join(tmp, "etc"), "heliosphere": {"enabled": True, "dir": hs}}, f)
    os.environ.update(HELIOS_CONFIG=cfg, HELIOS_DATA_DIR=os.path.join(tmp, "data"), HELIOS_FAKE_ROOT=os.path.join(tmp, "root"))
    spec = importlib.util.spec_from_file_location("helios_ctl", CTL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    def fail(msg, code=1):
        raise Rejected(msg)
    mod.fail = fail
    return mod


def run_rejecting(fn, *args):
    """fn(*args), or None when helios-ctl rejected the input."""
    try:
        return fn(*args)
    except Rejected:
        return None
