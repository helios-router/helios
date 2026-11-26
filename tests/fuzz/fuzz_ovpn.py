"""Fuzz the OpenVPN client-config sanitizer (remote-egress-upload openvpn).

The stored file is read back with an independent model of how OpenVPN reads a
config (fgets into a 256-byte buffer, parse_line() tokens, inline blocks that end
at the first line *starting* with </tag>, <connection> blocks parsed as options).
A finding is any way the stored file could make OpenVPN run a program, touch a
file by path, or read something the sanitizer didn't look at.
"""
import sys

import atheris

with atheris.instrument_imports():
    import helios_fuzz

ctl = helios_fuzz.load()
atheris.instrument_func(ctl.clean_ovpn_conf)
atheris.instrument_func(ctl.ovpn_tokens)
atheris.instrument_func(ctl.egress_upload)

FORBIDDEN = ctl.OVPN_DROP | ctl.OVPN_PATHS
SPACE = b" \t\n\v\f\r"


def chunks(data):
    """Lines as OpenVPN's fgets(line, 256) hands them over: long lines arrive in pieces."""
    for line in data.split(b"\n"):
        while len(line) > 255:
            yield line[:255]
            line = line[255:]
        yield line


def model_tokens(line):
    """Tokens a permissive reading of parse_line() could give (no erroring on odd escapes)."""
    toks, cur, state, esc = [], bytearray(), 0, False      # 0 initial, 1 bare, 2 "…", 3 '…'
    for c in line:
        ch = bytes([c])
        if esc:
            esc = False
            cur += ch
            state = state or 1
            continue
        if ch == b"\\" and state != 3:
            esc = True
            continue
        if state == 0:
            if ch in SPACE:
                continue
            if ch in b"#;":
                break
            if ch == b'"':
                state = 2
            elif ch == b"'":
                state = 3
            else:
                cur, state = bytearray(ch), 1
        elif state == 1 and ch in SPACE or state == 2 and ch == b'"' or state == 3 and ch == b"'":
            toks.append(bytes(cur))
            cur, state = bytearray(), 0
        else:
            cur += ch
    if state:
        toks.append(bytes(cur))
    return toks


def check(lines, top=True):
    """Walk config lines like OpenVPN; assert nothing forbidden is reachable."""
    it = iter(lines)
    for line in it:
        toks = model_tokens(line)
        if not toks:
            continue
        if toks[0].startswith(b"--"):
            toks[0] = toks[0][2:]
        name = toks[0].decode("utf-8", "replace")
        if len(toks) == 1 and len(toks[0]) >= 2 and toks[0][:1] == b"<" and toks[0][-1:] == b">":
            tag = toks[0][1:-1]
            close = b"</" + tag + b">"
            body = []
            for inner in it:
                if inner.lstrip(SPACE).startswith(close):
                    break
                body.append(inner)
            else:
                raise AssertionError(f"stored config has an unterminated <{tag!r}> block")
            if tag == b"connection":
                assert top, "nested <connection> reached OpenVPN"
                check(body, top=False)
            else:
                assert tag.decode("utf-8", "replace") in ctl.OVPN_INLINE, f"inline <{tag!r}> block reached OpenVPN"
            continue
        low = name.lower()
        assert low not in FORBIDDEN or (low == "auth-user-pass"), f"forbidden directive {name!r} reached OpenVPN"
        assert not low.startswith("management"), f"forbidden directive {name!r} reached OpenVPN"
        if low == "auth-user-pass" and len(toks) > 1:
            assert toks[1:] == [ctl.HS_EGRESS_AUTH_PATH.encode()], f"auth-user-pass reads {toks[1:]!r}"
        if low in ("http-proxy", "socks-proxy"):
            assert len(toks) <= 3, f"{name} with a credentials file reached OpenVPN"
        if low == "providers":
            assert all(b"/" not in t and b"." not in t for t in toks[1:]), f"providers loads {toks[1:]!r}"


def TestOneInput(data):
    r = helios_fuzz.run_rejecting(ctl.egress_upload, "openvpn", data)
    if r is None:
        return
    name, body, _ = r
    assert name == "client.ovpn"
    stored = body.encode()
    check(list(chunks(stored)))
    # Sanitising what we stored changes nothing (one meaning, written canonically)
    again = helios_fuzz.run_rejecting(ctl.clean_ovpn_conf, body)
    assert again is not None and again[0] == body, "sanitiser isn't idempotent on its own output"


if __name__ == "__main__":
    atheris.Setup(sys.argv, TestOneInput)
    atheris.Fuzz()
