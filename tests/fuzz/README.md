# Fuzzing helios-ctl

`helios-ctl` runs as root and parses what the web UI uploads. These
[Atheris](https://github.com/google/atheris) harnesses call its parsers and
validators directly (no subprocess, no files written) and check what they
accept, not only that they don't crash:

| Harness | Target | A finding is… |
|---|---|---|
| `fuzz_ovpn.py` | `egress_upload("openvpn")` → `clean_ovpn_conf` | anything in the stored `client.ovpn` that OpenVPN would run, load or open by path, read with an independent model of OpenVPN's reader (256-byte lines, `parse_line()` tokens, inline blocks ending at the first line *starting* with `</tag>`, `<connection>` blocks) |
| `fuzz_wireguard.py` | `egress_upload("wireguard")` → `clean_wg_conf` | a key wg-quick would act on (`PostUp`, `DNS`, `Table`…) left in `wg-out.conf`, read the way wg-quick's `parse_options()` reads it |
| `fuzz_egress_auth.py` | `egress_upload("auth")` (`proxy.json`, `auth.txt`) | a stored file that isn't one user and one password, each a single clean line |
| `fuzz_settings.py` | `qos-set`, `logwatch-set`, `maintenance-set`, `remote-set` | stored state Nix can't read (NaN/Infinity), a stored string that isn't printable ASCII, or a stored setting the same command refuses when sent back |

A rejection through `fail()` is the expected answer to bad input
(`helios_fuzz.Rejected`); any other exception, a hang, or a failed assertion is
a finding. Both sanitizers must also be idempotent on their own output.

## Running locally

```sh
python3 -m venv ~/.venvs/atheris
~/.venvs/atheris/bin/pip install --require-hashes -r tests/fuzz/requirements.txt
cd tests/fuzz
mkdir -p /tmp/corpus-ovpn
~/.venvs/atheris/bin/python fuzz_ovpn.py -dict=ovpn.dict -max_total_time=300 /tmp/corpus-ovpn corpus/fuzz_ovpn
```

Pass a crash file instead of corpus directories to replay it. Set
`HELIOS_FUZZ_CTL=/path/to/helios-ctl.py` to run a harness against another copy
(an older release, say).

## CI

ClusterFuzzLite (`.clusterfuzzlite/`) builds every `fuzz_*.py` with its
`corpus/<name>` seeds and `<name>.dict`. `.github/workflows/cflite-pr.yml`
fuzzes for 5 minutes on pull requests that touch helios-ctl or the harnesses;
`cflite-batch.yml` runs for an hour every week and keeps the grown corpus.
A new harness only needs to be named `fuzz_*.py`.
