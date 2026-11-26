"""Fuzz the way-out credentials upload (remote-egress-upload auth -> proxy.json or auth.txt).

proxy.json is read by jq in helios-egress-proxy and auth.txt by OpenVPN's
auth-user-pass: a finding is a stored file that isn't exactly one user and one
password, each a single non-empty line.
"""
import json
import sys

import atheris

with atheris.instrument_imports():
    import helios_fuzz

ctl = helios_fuzz.load()
atheris.instrument_func(ctl.egress_upload)


def TestOneInput(data):
    r = helios_fuzz.run_rejecting(ctl.egress_upload, "auth", data)
    if r is None:
        return
    name, body, dropped = r
    assert not dropped
    if name == "proxy.json":
        j = json.loads(body)
        assert set(j) == {"user", "password"}, j
        vals = [j["user"], j["password"]]
    else:
        assert name == "auth.txt", name
        assert body.endswith("\n")
        vals = body[:-1].split("\n")
        assert len(vals) == 2, vals
    for v in vals:
        assert isinstance(v, str) and v and not any(c < " " and c != "\t" or c == "\x7f" for c in v), repr(v)
        if name == "auth.txt":                       # OpenVPN reads the file line by line
            assert v.strip() and "\r" not in v, repr(v)
        assert len(v) <= 255


if __name__ == "__main__":
    atheris.Setup(sys.argv, TestOneInput)
    atheris.Fuzz()
