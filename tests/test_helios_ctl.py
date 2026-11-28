"""End-to-end tests for helios-ctl with stubbed system tools, a mock AdGuard API and a mock ntfy."""
import base64
import hashlib
import hmac
import struct
import json
import re
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
CTL = os.path.join(HERE, "..", "pkgs", "helios-ctl.py")
STUBS = os.path.join(HERE, "stubs")


def read_file(path, mode="r", **kw):
    with open(path, mode, **kw) as f:
        return f.read()


def write_file(path, data, mode="w"):
    with open(path, mode) as f:
        f.write(data)


def load_json(path):
    with open(path) as f:
        return json.load(f)


def write_json(path, obj):
    with open(path, "w") as f:
        json.dump(obj, f)

WG_OUT = """[Interface]
PrivateKey = abc=
Address = 10.2.0.2/32
DNS = 10.2.0.1
PostUp = iptables -A FORWARD -j ACCEPT
PostDown = iptables -D FORWARD -j ACCEPT

[Peer]
PublicKey = xyz=
Endpoint = 203.0.113.1:51820
AllowedIPs = 0.0.0.0/0
"""

ADGUARD = {"clients": [], "calls": []}
NTFY = {"posts": []}


class AG(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, obj):
        b = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if "/json" in self.path:
            msgs = [p for p in NTFY["posts"] if self.path.startswith("/" + p["topic"] + "/")]
            body = "\n".join(json.dumps({"event": "message", "time": 1764000000 + i, "title": m["title"], "message": m["body"], "priority": 3})
                             for i, m in enumerate(msgs))
            NTFY["auth"] = self.headers.get("Authorization")
            self.send_response(200)
            self.end_headers()
            self.wfile.write(body.encode())
            return
        ADGUARD["calls"].append(("GET", self.path, self.headers.get("Authorization")))
        self._send({"clients": ADGUARD["clients"]})

    def do_POST(self):
        raw = self.rfile.read(int(self.headers["Content-Length"]))
        if not self.path.startswith("/control/"):
            NTFY["posts"].append({"topic": self.path.strip("/"), "title": self.headers.get("Title"), "body": raw.decode(),
                                  "auth": self.headers.get("Authorization"), "priority": self.headers.get("Priority")})
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"{}")
            return
        body = json.loads(raw)
        ADGUARD["calls"].append(("POST", self.path, body))
        if self.path.endswith("/add"):
            ADGUARD["clients"].append(body)
        elif self.path.endswith("/update"):
            ADGUARD["clients"] = [body["data"] if c["name"] == body["name"] else c for c in ADGUARD["clients"]]
        elif self.path.endswith("/delete"):
            ADGUARD["clients"] = [c for c in ADGUARD["clients"] if c["name"] != body["name"]]
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")


class HeliosCtl(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = HTTPServer(("127.0.0.1", 0), AG)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.cfg = os.path.join(self.tmp, "nixos")
        self.data = os.path.join(self.tmp, "data")
        self.fake = os.path.join(self.tmp, "root")
        os.makedirs(self.cfg)
        os.makedirs(self.data)
        shutil.copy(os.path.join(HERE, "fixtures", "state.json"), self.cfg)
        conf = load_json(os.path.join(HERE, "fixtures", "config.json"))
        conf["configDir"] = self.cfg
        conf["solarWind"]["url"] = f"http://127.0.0.1:{self.srv.server_port}"
        conf["heliosphere"] = {"enabled": True, "publicHost": None, "dir": os.path.join(self.tmp, "hs"), "container": "heliosphere",
                               "wgSubnet": "10.61.0", "ovpnSubnet": "10.62.0"}
        conf["detectFile"] = os.path.join(self.tmp, "detect", "hardware.json")
        conf["solarWind"]["tokenFile"] = os.path.join(self.data, "solarwind-token")
        conf["admin"] = {"user": "admin", "sshKeys": 1, "totpDir": os.path.join(self.data, "totp")}
        gate = os.path.join(self.tmp, "authelia")
        conf["gate"] = {"enabled": True, "fqdn": "helios.home.arpa", "url": "https://helios.home.arpa:9090", "rescue": True,
                        "gateDir": os.path.join(self.tmp, "gate"), "usersFile": os.path.join(gate, "users.yml"),
                        "storageKeyFile": os.path.join(gate, "key"), "authelia": os.path.join(STUBS, "authelia"),
                        "cliConfig": "/dev/null", "notifications": os.path.join(gate, "notifications.txt"),
                        "caCert": os.path.join(gate, "root.crt"), "autheliaVersion": "4.39.28",
                        "ownDomain": None, "dnsProvider": "cloudflare", "credName": "helios-dns-token",
                        "dnsCred": os.path.join(self.tmp, "gate", "dns-token.cred"), "tlsLink": os.path.join(self.tmp, "gate", "tls.caddy"),
                        "tlsMode": os.path.join(self.tmp, "gate", "tls-mode"), "tlsInternal": os.path.join(gate, "tls-internal.caddy"),
                        "tlsAcme": os.path.join(gate, "tls-acme.caddy"), "acmeCert": os.path.join(gate, "le.crt")}
        self.conf_path = os.path.join(self.tmp, "config.json")
        write_json(self.conf_path, conf)
        with open(os.path.join(self.cfg, "configuration.nix"), "w") as f:
            f.write("{ }\n")
        # Kea leases: one known (titan), two new, one expired
        t = int(time.time())
        leases = os.path.join(self.fake, "var/lib/kea")
        os.makedirs(leases)
        with open(os.path.join(leases, "dhcp4.leases"), "w") as f:
            f.write("address,hwaddr,client_id,valid_lifetime,expire,subnet_id,fqdn_fwd,fqdn_rev,hostname,state,user_context,pool_id\n")
            f.write(f"10.20.0.10,11:22:33:44:55:66,,7200,{t + 3000},20,0,0,titan,0,,0\n")
            f.write(f"10.30.0.101,aa:aa:aa:00:00:01,,7200,{t + 5000},30,0,0,Kids-PC,0,,0\n")
            f.write(f"10.40.0.100,bb:bb:bb:00:00:02,,7200,{t + 5000},40,0,0,LG-webOS-TV,0,,0\n")
            f.write(f"10.30.0.150,cc:cc:cc:00:00:03,,7200,{t - 50},30,0,0,old-phone,0,,0\n")
        for d, rx, tx in (("wan", 5_000_000_000, 400_000_000), ("family", 300_000_000, 3_000_000_000)):
            sp = os.path.join(self.fake, f"sys/class/net/{d}/statistics")
            os.makedirs(sp)
            write_file(os.path.join(sp, "rx_bytes"), str(rx))
            write_file(os.path.join(sp, "tx_bytes"), str(tx))
        os.makedirs(os.path.join(self.fake, "proc"))
        write_file(os.path.join(self.fake, "proc/uptime"), "93784.2 12345.0\n")
        write_file(os.path.join(self.fake, "proc/loadavg"), "0.31 0.22 0.18 1/200 999\n")
        write_file(os.path.join(self.fake, "proc/meminfo"), "MemTotal: 8000000 kB\nMemAvailable: 5200000 kB\n")
        os.makedirs(os.path.join(self.fake, "run"))
        write_file(os.path.join(self.fake, "run/miniupnpd.leases"), 
            f"UDP:3074:10.30.0.20:3074:{t + 3000}:Xbox\nTCP:51413:10.20.0.30:51413:0:Transmission\n")
        os.makedirs(os.path.join(self.fake, "var/lib/AdGuardHome"))
        write_file(os.path.join(self.fake, "var/lib/AdGuardHome/AdGuardHome.yaml"), "http:\n  address: 0.0.0.0:3000\n")
        with open(os.path.join(self.data, "adguard.json"), "w") as f:
            json.dump({"url": f"http://127.0.0.1:{self.srv.server_port}", "user": "admin", "password": "pw"}, f)
        ADGUARD["clients"] = [{"name": "someone-manual", "ids": ["10.20.0.99"]}]
        ADGUARD["calls"] = []
        NTFY["posts"] = []
        self.env = dict(os.environ, HELIOS_CONFIG=self.conf_path, HELIOS_DATA_DIR=self.data, HELIOS_FAKE_ROOT=self.fake,
                        HELIOS_NO_SYSTEMD="1", HELIOS_REBUILD=os.path.join(STUBS, "nixos-rebuild"),
                        PATH=STUBS + ":" + os.environ["PATH"], HELIOS_UPS_SPOOL=os.path.join(self.tmp, "spool"), STUB_LOG=os.path.join(self.tmp, "stub.log"),
                        STUB_AUTHELIA_DB=os.path.join(self.tmp, "authelia-db.json"))

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def helios(self, *args, ok=True, stdin=None):
        r = subprocess.run([sys.executable, CTL, *args], capture_output=True, text=True, env=self.env, input=stdin)
        try:
            data = json.loads(r.stdout)
        except ValueError:
            self.fail(f"non-JSON output for {args}: {r.stdout!r} {r.stderr!r}")
        if ok:
            self.assertTrue(data.get("ok"), f"{args}: {data} {r.stderr}")
        return data

    def stub_log(self):
        try:
            return read_file(self.env["STUB_LOG"])
        except OSError:
            return ""

    def wait_apply(self, want, timeout=10):
        for _ in range(timeout * 10):
            st = self.helios("apply-status")["apply"]
            if st.get("state") == want:
                return st
            time.sleep(0.1)
        raise self.failureException(f"apply never reached {want}: {st}")

    # ---------------------------------------------------------------- tests
    def test_devices_merge_leases_and_state(self):
        d = self.helios("devices")
        by = {x["mac"]: x for x in d["devices"]}
        self.assertTrue(by["11:22:33:44:55:66"]["known"])
        self.assertTrue(by["11:22:33:44:55:66"]["online"])          # neighbor stub says REACHABLE
        self.assertFalse(by["aa:aa:aa:00:00:01"]["known"])
        self.assertEqual(by["aa:aa:aa:00:00:01"]["hostname"], "Kids-PC")
        self.assertEqual(by["bb:bb:bb:00:00:02"]["lease_vlan"], "iot")
        self.assertNotIn("cc:cc:cc:00:00:03", by)                    # expired lease ignored

    def test_media_server_set_validate_keep_and_clear(self):
        base = {"mac": "cc:cc:cc:00:00:03", "name": "test-nas", "vlan": "trusted", "profile": "admin"}
        r = self.helios("device-set", json.dumps({**base, "mediaServer": {"tcp": [8920, "8096", 8096], "networks": ["iot", "family", "family"]}}))
        self.assertEqual(r["device"]["mediaServer"], {"tcp": [8096, 8920], "udp": [], "networks": ["family", "iot"]})
        self.assertIn("Add test-nas", self.helios("pending")["changes"][0])
        for ms, msg in (({"tcp": []}, "at least one port"), ({"tcp": [70000]}, "1-65535"),
                        ({"udp": ["x"]}, "numbers"), ([8096], "object"), ({"tcp": [1], "networks": ["nope"]}, "unknown network")):
            bad = self.helios("device-set", json.dumps({**base, "mediaServer": ms}), ok=False)
            self.assertIn(msg, bad["error"])
        # key absent (CLI edit) keeps it; null clears it
        r = self.helios("device-set", json.dumps({**base, "notes": "NAS"}))
        self.assertEqual(r["device"]["mediaServer"]["tcp"], [8096, 8920])
        r = self.helios("device-set", json.dumps({**base, "mediaServer": None}))
        self.assertNotIn("mediaServer", r["device"])

    def test_networks_add_rename_renumber_remove(self):
        r = self.helios("networks")
        self.assertEqual([n["name"] for n in r["networks"]], ["trusted", "family", "iot", "strict", "mgmt"])
        self.assertEqual(r["networks"][0]["access"]["reach"], "all")
        guest = {"name": "guest", "label": "Guests", "vlan": 70, "prefix": "10.70.0", "role": "guest",
                 "access": {**r["roles"]["guest"]["access"], "cast": True}}
        n = self.helios("network-set", json.dumps(guest))["network"]
        self.assertEqual(n["overrides"], ["cast"])          # only the flag that differs from the role is stored
        self.assertIn("Add network guest (VLAN 70, guest)", self.helios("pending")["changes"])
        for bad, msg in (({"vlan": 10}, "WAN VLAN"), ({"vlan": 20, "name": "x"}, "already used by trusted"),
                         ({"prefix": "8.8.8", "name": "x", "vlan": 71}, "private"), ({"name": "wan"}, "interface name"),
                         ({"name": "x", "vlan": 72, "prefix": "10.72.0", "ipv6": "0x100"}, "IPv6"),
                         ({"name": "x", "vlan": 73, "prefix": "10.73.0", "role": "nope"}, "unknown role")):
            self.assertIn(msg, self.helios("network-set", json.dumps({**guest, **bad}), ok=False)["error"])
        # A media server lets the guest network in; a device lives on it
        self.helios("device-set", json.dumps({"mac": "cc:cc:cc:00:00:04", "name": "tv1", "vlan": "guest", "profile": "default", "media": True}))
        self.helios("device-set", json.dumps({"mac": "11:22:33:44:55:66", "name": "titan", "vlan": "trusted", "profile": "admin",
                                           "mediaServer": {"tcp": [8096], "networks": ["guest"]}}))
        # Rename + renumber: the device follows, keeping its host octet; references follow the name
        self.helios("network-set", json.dumps({**guest, "from": "guest", "name": "visitors", "prefix": "10.71.0", "vlan": 71}))
        st = load_json(os.path.join(self.cfg, "state.json"))
        tv = next(d for d in st["devices"] if d["name"] == "tv1")
        self.assertEqual((tv["vlan"], tv["ip"]), ("visitors", "10.71.0.20"))
        self.assertTrue(tv["media"])
        titan = next(d for d in st["devices"] if d["name"] == "titan")
        self.assertEqual(titan["mediaServer"]["networks"], ["visitors"])
        # Can't remove a network with devices, or the last admin network
        self.assertIn("move or forget", self.helios("network-remove", "visitors", ok=False)["error"])
        self.helios("device-remove", "cc:cc:cc:00:00:04")
        self.helios("network-remove", "visitors")
        self.assertNotIn("visitors", [n["name"] for n in self.helios("networks")["networks"]])
        st = load_json(os.path.join(self.cfg, "state.json"))
        self.assertEqual(next(d for d in st["devices"] if d["name"] == "titan")["mediaServer"]["networks"], [])
        mg = next(n for n in self.helios("networks")["networks"] if n["name"] == "mgmt")
        self.helios("network-set", json.dumps({**{k: mg[k] for k in ("name", "label", "vlan", "prefix", "ipv6", "role")},
                                            "from": "mgmt", "access": {**mg["access"], "admin": False}}))
        tr = next(n for n in self.helios("networks")["networks"] if n["name"] == "trusted")
        lock = self.helios("network-set", json.dumps({**{k: tr[k] for k in ("name", "label", "vlan", "prefix", "ipv6", "role")},
                                                   "from": "trusted", "access": {**tr["access"], "admin": False}}), ok=False)
        self.assertIn("lock yourself out", lock["error"])

    def test_media_device_server_choice(self):
        srv = {"vlan": "trusted", "profile": "admin"}
        self.helios("device-set", json.dumps({**srv, "mac": "11:22:33:44:55:66", "name": "titan", "mediaServer": {"tcp": [8096]}}))
        self.helios("device-set", json.dumps({**srv, "mac": "cc:00:00:00:00:02", "name": "plexbox", "mediaServer": {"tcp": [32400]}}))
        tv = {"mac": "cc:00:00:00:00:03", "name": "tv", "vlan": "iot", "profile": "default", "media": True}
        self.assertNotIn("mediaServers", self.helios("device-set", json.dumps(tv))["device"])          # default: every server
        r = self.helios("device-set", json.dumps({**tv, "mediaServers": ["plexbox"]}))
        self.assertEqual(r["device"]["mediaServers"], ["plexbox"])
        for bad, msg in ((["nope"], "isn't a media server"), ([], "at least one media server"), ("plexbox", "list")):
            self.assertIn(msg, self.helios("device-set", json.dumps({**tv, "mediaServers": bad}), ok=False)["error"])
        dev = lambda n: next(d for d in load_json(os.path.join(self.cfg, "state.json"))["devices"] if d["name"] == n)
        # Server renamed: the TV's list follows; server stops serving: the TV falls back to "all"
        self.helios("device-set", json.dumps({**srv, "mac": "cc:00:00:00:00:02", "name": "plex", "mediaServer": {"tcp": [32400]}}))
        self.assertEqual(dev("tv")["mediaServers"], ["plex"])
        self.helios("device-set", json.dumps({**srv, "mac": "cc:00:00:00:00:02", "name": "plex", "mediaServer": None}))
        self.assertNotIn("mediaServers", dev("tv"))
        self.helios("device-set", json.dumps({**tv, "mediaServers": ["titan"]}))
        self.helios("device-remove", "11:22:33:44:55:66")
        self.assertNotIn("mediaServers", dev("tv"))

    def test_profiles_crud_network_default_and_adguard_mapping(self):
        r = self.helios("profiles")
        self.assertEqual([p["name"] for p in r["profiles"]], ["admin", "adult", "kid", "default"])
        self.assertIn("reachable", r["adguard"])                          # the tests run a mock AdGuard
        staff = {"name": "staff", "label": "Staff", "desc": "Office PCs", "filtering": True, "safeBrowsing": True,
                 "blockedServices": ["tiktok", "youtube", "tiktok"]}
        self.assertEqual(self.helios("profile-set", json.dumps(staff))["profile"]["blockedServices"], ["tiktok", "youtube"])
        self.assertIn("Add filtering profile staff", self.helios("pending")["changes"])
        for bad, msg in (({"name": "Bad Id"}, "profile id"), ({"filtering": "yes"}, "true or false"),
                         ({"blockedServices": ["no spaces"]}, "service ids"), ({"name": "kid"}, "already a profile")):
            self.assertIn(msg, self.helios("profile-set", json.dumps({**staff, **bad}), ok=False)["error"])
        # Network default; devices on it start with it; rename follows; removal refused while in use
        fam = next(n for n in self.helios("networks")["networks"] if n["name"] == "family")
        self.helios("network-set", json.dumps({**{k: fam[k] for k in ("name", "label", "vlan", "prefix", "ipv6", "role")}, "from": "family", "defaultProfile": "staff"}))
        d = self.helios("device-set", json.dumps({"mac": "cc:00:00:00:00:09", "name": "desk", "vlan": "family"}))["device"]
        self.assertEqual(d["profile"], "staff")
        self.helios("profile-set", json.dumps({**staff, "from": "staff", "name": "office"}))
        st = load_json(os.path.join(self.cfg, "state.json"))
        self.assertEqual(next(x for x in st["devices"] if x["name"] == "desk")["profile"], "office")
        self.assertEqual(next(n for n in st["networks"] if n["name"] == "family")["defaultProfile"], "office")
        self.assertIn("still in use", self.helios("profile-remove", "office", ok=False)["error"])
        self.helios("profile-remove", "kid")                                 # unused default: removable
        self.assertNotIn("kid", [p["name"] for p in self.helios("profiles")["profiles"]])
        # AdGuard mapping of the profile switches
        sys.path.insert(0, os.path.dirname(CTL))
        import importlib.util
        spec = importlib.util.spec_from_file_location("helios_ctl_mod", CTL)
        mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
        ag = mod.profile_settings({"filtering": True, "youtubeRestricted": True, "blockedServices": ["tiktok"]})
        self.assertEqual((ag["use_global_settings"], ag["use_global_blocked_services"], ag["blocked_services"]), (False, False, ["tiktok"]))
        self.assertEqual((ag["safe_search"]["enabled"], ag["safe_search"]["google"], ag["safe_search"]["youtube"]), (True, False, True))
        self.assertEqual(mod.profile_settings({"global": True}), {"use_global_settings": True, "use_global_blocked_services": True})

    def test_qos_settings_and_recommendations(self):
        q = self.helios("qos")["settings"]
        self.assertEqual((q["algorithm"], q["fairness"], q["priority"]), ("cake", "device", "diffserv3"))
        r = self.helios("qos-set", json.dumps({"enabled": True, "down": "500mbit", "up": "50mbit", "link": "vdsl",
                                            "fairness": "flow", "priority": "diffserv4", "ackFilter": True, "algorithm": "fq_codel"}))
        self.assertEqual((r["settings"]["link"], r["settings"]["algorithm"]), ("vdsl", "fq_codel"))
        self.assertIn("Change qos settings", self.helios("pending")["changes"])
        for bad, msg in (({"link": "dialup"}, "link must be one of"), ({"down": "fast"}, "rate"), ({"wash": "yes"}, "true or false"),
                         ({"overhead": 999}, "overhead"), ({"nope": 1}, "unknown QoS setting"),
                         ({"down": "\u0665mbit"}, "rate"),             # fuzzing: \d took other scripts' digits
                         ({"enabled": True, "down": None}, "set both rates")):
            self.assertIn(msg, self.helios("qos-set", json.dumps(bad), ok=False)["error"])
        # Detection without measuring: the modem probe fails here, so Ethernet; no rates suggested
        env = dict(self.env, HELIOS_MODEM_URL="http://127.0.0.1:9/")
        out = json.loads(subprocess.run([sys.executable, CTL, "qos-suggest"], capture_output=True, text=True, env=env).stdout)
        self.assertFalse(out["detected"]["cableModem"])
        self.assertEqual(out["suggestions"]["link"], "ethernet")
        self.assertNotIn("down", out["suggestions"])
        # The recommendation rules themselves
        import importlib.util
        spec = importlib.util.spec_from_file_location("helios_ctl_q", CTL)
        mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
        sug, why = mod.qos_recommend({"down": 940.0, "up": 38.0}, True, 2500)
        self.assertEqual((sug["link"], sug["down"], sug["up"], sug["ackFilter"]), ("docsis", "846mbit", "36mbit", True))
        sug, _ = mod.qos_recommend({"down": 500.0, "up": 500.0}, False, 1000)
        self.assertEqual((sug["link"], sug["ackFilter"], sug["algorithm"]), ("ethernet", False, "cake"))
        sug, why = mod.qos_recommend({"down": 2400.0, "up": 2400.0}, False, 2500)
        self.assertEqual(sug["algorithm"], "fq_codel")
        self.assertIn("close to the trunk", why["down"])

    def test_upnp_close_block_unblock(self):
        leases = os.path.join(self.fake, "run/miniupnpd.leases")
        self.helios("device-set", json.dumps({"mac": "cc:00:00:00:00:30", "name": "seedbox", "vlan": "trusted", "ip": "10.20.0.30"}))
        write_file(leases, "TCP:3389:10.30.0.20:3389:0:RDP\n", "a")
        # Close one mapping: miniupnpd is stopped, the lease line dropped, then started again
        self.assertEqual(self.helios("upnp-close", "udp", "3074")["removed"], 1)
        self.assertNotIn("3074", read_file(leases))
        log = self.stub_log()
        self.assertLess(log.index("systemctl stop miniupnpd.service"), log.index("systemctl start miniupnpd.service"))
        self.assertIn("no open TCP port 1", self.helios("upnp-close", "tcp", "1", ok=False)["error"])
        # Block a device: its mapping closes now, the block shows as a pending change
        r = self.helios("upnp-block", json.dumps({"device": "seedbox"}))
        self.assertEqual(r["closed"], 1)
        self.assertNotIn("51413", read_file(leases))
        self.assertIn("Change upnp settings", self.helios("pending")["changes"])
        # Block a port range; a mapping inside it is reported as blocked
        write_file(leases, "TCP:6001:10.30.0.20:6001:0:Game\n", "a")
        self.helios("upnp-block", json.dumps({"port": "6000-6100"}))
        u = self.helios("upnp")
        self.assertEqual((u["blockedDevices"], u["blockedPorts"]), (["seedbox"], ["6000-6100"]))
        self.assertNotIn("6001", read_file(leases))
        for bad, msg in (({"port": "70000"}, "1-65535"), ({"device": "ghost"}, "no named device"),
                         ({"device": "seedbox", "port": "1"}, "either a device or a port"), ({}, "either a device or a port")):
            self.assertIn(msg, self.helios("upnp-block", json.dumps(bad), ok=False)["error"])
        self.helios("upnp-unblock", json.dumps({"port": "6000-6100"}))
        self.assertEqual(self.helios("upnp")["blockedPorts"], [])
        self.assertIn("isn't blocked", self.helios("upnp-unblock", json.dumps({"port": "22"}), ok=False)["error"])
        # Close all really empties the lease file
        self.assertGreaterEqual(self.helios("upnp-clear")["removed"], 1)
        self.assertEqual(read_file(leases).strip(), "")

    def test_printing_status_printers_and_cleanup(self):
        r = self.helios("printing")
        self.assertTrue(r["running"])
        self.assertEqual({d["uri"]: d["driverless"] for d in r["discovered"]}["ipp://10.30.0.50/ipp/print"], True)
        self.assertFalse(next(d for d in r["discovered"] if d["uri"].startswith("usb://"))["driverless"])
        self.assertIn("ipps://Canon%20TS5300._ipps._tcp.local/", [d["uri"] for d in r["discovered"]])   # only driverless knew it
        self.assertIn("brlaser", [d["id"] for d in r["drivers"]])
        models = self.helios("printer-models", "brother")["models"]
        self.assertEqual(models[0]["model"], "everywhere")
        self.assertEqual([m["model"] for m in models[1:]], ["drv:///brlaser.drv/brl2350.ppd"])
        # Add two printers; the default moves; a configured printer leaves "discovered"
        usb = "usb://Brother/HL-L2350DW%20series?serial=E78123"
        self.helios("printer-set", json.dumps({"name": "office", "deviceUri": usb, "model": "drv:///brlaser.drv/brl2350.ppd", "default": True}))
        self.helios("printer-set", json.dumps({"name": "photo", "deviceUri": "ipp://10.30.0.50/ipp/print", "model": "everywhere", "location": "Den", "default": True}))
        st = load_json(os.path.join(self.cfg, "state.json"))["printing"]
        self.assertEqual([x.get("default", False) for x in st["printers"]], [False, True])
        r = self.helios("printing")
        self.assertNotIn(usb, [d["uri"] for d in r["discovered"]])
        self.assertEqual(next(x for x in r["printers"] if x["name"] == "photo")["live"]["state"], "printing")
        self.assertIn("Add printer office", self.helios("pending")["changes"])
        for bad, msg in (({"name": "has space"}, "printer name"), ({"deviceUri": "file:///etc/passwd"}, "connection"),
                         ({"model": ""}, "driver"), ({"name": "photo"}, "already a printer")):
            self.assertIn(msg, self.helios("printer-set", json.dumps({"name": "x", "deviceUri": usb, "model": "everywhere", **bad}), ok=False)["error"])
        # Live actions go straight to CUPS
        self.assertEqual(self.helios("printer-test", "office")["job"], "office-42")
        self.helios("printer-pause", "office", "on"); self.helios("printer-pause", "office", "off")
        self.helios("print-cancel", "photo-7")
        log = self.stub_log()
        for line in ("cupsdisable office", "cupsenable office", "cancel photo-7"):
            self.assertIn(line, log)
        self.assertIn("bad job id", self.helios("print-cancel", "photo-7; rm -rf /", ok=False)["error"])
        # Drivers; removal; post-apply cleanup deletes only printers HeliOS made and dropped
        self.assertEqual(self.helios("printing-drivers", json.dumps(["brlaser", "gutenprint"]))["drivers"], ["brlaser", "gutenprint"])
        self.assertIn("drivers must be", self.helios("printing-drivers", json.dumps(["nope"]), ok=False)["error"])
        import importlib.util
        spec = importlib.util.spec_from_file_location("helios_ctl_p", CTL)
        mod = importlib.util.module_from_spec(spec)
        os.environ.update({k: v for k, v in self.env.items() if k.startswith(("HELIOS_", "STUB_")) or k == "PATH"})
        spec.loader.exec_module(mod)
        self.assertIn("up to date", mod.printers_cleanup())
        self.helios("printer-remove", "photo")
        self.assertIn("1 removed", mod.printers_cleanup())
        self.assertIn("lpadmin -x photo", self.stub_log())
        # CUPS down: status still answers
        env = dict(self.env, STUB_CUPS="down")
        r = json.loads(subprocess.run([sys.executable, CTL, "printing"], capture_output=True, text=True, env=env).stdout)
        self.assertFalse(r["running"])
        self.assertIsNone(r["printers"][0]["live"])

    def test_services_defaults_overrides_and_messages(self):
        r = self.helios("services")
        svc = {x["id"]: x for x in r["services"]}
        self.assertEqual((svc["printing"]["group"], svc["printing"]["enabled"], svc["printing"]["override"]), ("optional", False, None))
        self.assertTrue(svc["notifications"]["enabled"])                     # default-on group
        self.assertIn("network", [c["id"] for c in r["core"]])
        self.helios("service-set", "printing", "on")
        self.assertIn("Turn on Print server", self.helios("pending")["changes"])
        self.assertTrue({x["id"]: x for x in self.helios("services")["services"]}["printing"]["enabled"])
        self.helios("service-set", "notifications", "off")
        self.assertIn("Turn off Notifications", self.helios("pending")["changes"])
        self.helios("service-set", "printing", "default"); self.helios("service-set", "notifications", "default")
        st = load_json(os.path.join(self.cfg, "state.json"))
        self.assertNotIn("services", st)                                       # nothing left to override
        self.assertIn("core services can't", self.helios("service-set", "firewall", "off", ok=False)["error"])
        # site.nix defaults come from config.json
        conf = load_json(self.conf_path); conf["serviceDefaults"] = {"printing": True}; write_json(self.conf_path, conf)
        self.assertTrue({x["id"]: x for x in self.helios("services")["services"]}["printing"]["enabled"])

    def test_logwatch_settings_recommend_guard_and_download(self):
        r = self.helios("logwatch")
        self.assertEqual((r["settings"]["model"], r["settings"]["keepLoaded"], r["settings"]["guard"]["enabled"]), ("small", False, True))
        r = self.helios("logwatch-set", json.dumps({"model": "medium", "interval": 30, "guard": {"pauseHours": 2}}))
        self.assertEqual((r["settings"]["model"], r["settings"]["interval"], r["settings"]["guard"]["pauseHours"], r["settings"]["guard"]["cpuPressure"]), ("medium", 30, 2, 40))
        self.assertIn("Change AI log triage settings", self.helios("pending")["changes"])
        for bad, msg in (({"model": "huge"}, "model must be"), ({"interval": 1}, "interval"), ({"backend": "tpu"}, "backend"),
                         ({"customPath": "../x.gguf"}, "/var/lib/llm"), ({"customPath": "/tmp/x.gguf"}, "/var/lib/llm"), ({"customPath": "/var/lib/llm/../x.gguf"}, "/var/lib/llm"), ({"customPath": "/var/lib/llm/sub/x.gguf"}, "/var/lib/llm"), ({"customPath": "/var/lib/llm/.x.gguf"}, "/var/lib/llm"), ({"model": "custom"}, "needs customPath"), ({"guard": {"cpuPressure": 0}}, "cpuPressure"),
                         ({"nope": 1}, "unknown setting")):
            self.assertIn(msg, self.helios("logwatch-set", json.dumps(bad), ok=False)["error"])
        r = self.helios("logwatch-set", json.dumps({"model": "custom", "customPath": "/var/lib/llm/my-model.gguf"}))
        self.assertEqual(r["settings"]["customPath"], "/var/lib/llm/my-model.gguf")
        self.assertEqual(self.helios("logwatch-reset")["settings"]["model"], "small")
        import importlib.util
        spec = importlib.util.spec_from_file_location("helios_ctl_lw", CTL)
        mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
        rec = lambda mb, cpus, gpu: mod.lw_recommend({"memoryMB": mb, "cpus": cpus, "gpu": gpu})
        self.assertEqual((rec(4096, 4, False), rec(8192, 4, True), rec(16384, 4, False), rec(20480, 8, True), rec(32768, 8, True), rec(65536, 4, False)),
                         ("tiny", "small", "medium", "large", "xl", "medium"))           # no GPU and few cores: capped at medium
        self.assertIsNone(rec(2048, 2, False))
        # Guard: high CPU pressure pauses; --check then says "skip"; an expired pause resumes
        os.makedirs(os.path.join(self.fake, "proc/pressure"), exist_ok=True)
        write_file(os.path.join(self.fake, "proc/pressure/cpu"), "some avg10=80.00 avg60=55.00 avg300=20.00 total=1\n")
        write_file(os.path.join(self.fake, "proc/pressure/memory"), "some avg10=0.00 avg60=1.00 avg300=0.00 total=1\n")
        r = self.helios("logwatch-guard")
        self.assertEqual((r["action"], r["hours"]), ("paused", 6))
        self.assertIn("CPU pressure 55%", r["reason"])
        self.assertIn("systemctl stop logwatch.service llama-cpp.service", self.stub_log())
        self.assertEqual(subprocess.run([sys.executable, CTL, "logwatch-guard", "--check"], env=self.env).returncode, 1)
        pause = os.path.join(self.data, "logwatch-pause.json")
        d = load_json(pause); d["until"] = 1; write_json(pause, d)
        self.assertEqual(self.helios("logwatch-guard")["action"], "resumed")
        self.assertEqual(subprocess.run([sys.executable, CTL, "logwatch-guard", "--check"], env=self.env).returncode, 0)
        # Download: verified against the pinned sha256; a mismatch leaves nothing behind
        import hashlib
        blob = os.path.join(self.tmp, "m.gguf"); write_file(blob, b"GGUF" + b"x" * 1000, "wb")
        good = hashlib.sha256(read_file(blob, "rb")).hexdigest()
        conf = load_json(self.conf_path)
        conf["logwatch"] = {"catalog": {"baseUrl": "https://example.invalid", "defaults": mod.LW_CATALOG["defaults"],
                            "presets": {"t": {"label": "T", "repo": "r", "file": "t.gguf", "bytes": 1004, "sha256": good, "ramMB": 512}}}}
        write_json(self.conf_path, conf)
        env = dict(self.env, HELIOS_MODEL_URL="file://" + blob, HELIOS_LLM_DIR=os.path.join(self.tmp, "llm"), HELIOS_NO_SYSTEMD="1")
        r = json.loads(subprocess.run([sys.executable, CTL, "logwatch-model-get", "t"], capture_output=True, text=True, env=env).stdout)
        self.assertTrue(r["ok"], r)
        self.assertTrue(os.path.exists(os.path.join(self.tmp, "llm/t.gguf")))
        self.assertEqual(oct(os.stat(os.path.join(self.tmp, "llm/t.gguf")).st_mode & 0o777), "0o400")
        self.assertEqual(oct(os.stat(os.path.join(self.tmp, "llm")).st_mode & 0o777), "0o700")   # tmpfiles widens it to root:llm 0750 on a router
        conf["logwatch"]["catalog"]["presets"]["t"]["sha256"] = "0" * 64; write_json(self.conf_path, conf)
        os.remove(os.path.join(self.tmp, "llm/t.gguf"))
        r = json.loads(subprocess.run([sys.executable, CTL, "logwatch-model-get", "t"], capture_output=True, text=True, env=env).stdout)
        self.assertFalse(r["ok"]); self.assertIn("checksum", r["error"])
        self.assertEqual(os.listdir(os.path.join(self.tmp, "llm")), [])

    def test_recovery_alert_retry_recover_escalate(self):
        conf = load_json(self.conf_path)
        conf["recovery"] = {"units": [{"unit": "kea-dhcp4-server", "restart": True}, {"unit": "nftables", "restart": False}], "cooldownMin": 30, "maxPerDay": 2}
        write_json(self.conf_path, conf)
        state = os.path.join(self.data, "recovery.json")
        run = lambda *a, failed="": json.loads(subprocess.run([sys.executable, CTL, *a], capture_output=True, text=True,
                                                               env=dict(self.env, STUB_FAILED=failed)).stdout)
        # OnFailure hook: urgent notification, recorded
        self.assertTrue(self.helios("unit-failed", "kea-dhcp4-server.service")["ok"])
        self.assertIn("kea-dhcp4-server.service", load_json(state))
        self.assertIn("bad unit name", self.helios("unit-failed", "x; rm -rf /", ok=False)["error"])
        # Timer: still failed → one retry (reset-failed + start), then the cooldown holds it back
        both = "kea-dhcp4-server.service nftables.service"
        r = run("recover", failed=both)
        self.assertEqual(r["retried"], ["kea-dhcp4-server.service"])
        self.assertEqual(r["escalated"], ["nftables.service"])            # alert-only unit: escalate, never restart
        self.assertIn("systemctl reset-failed kea-dhcp4-server.service", self.stub_log())
        self.assertNotIn("systemctl start --no-block nftables", self.stub_log())
        self.assertEqual(run("recover", failed=both)["retried"], [])       # cooldown
        # Daily cap reached → escalate once, not again
        st = load_json(state); st["kea-dhcp4-server.service"]["attempts"] = [int(time.time()) - 7200, int(time.time()) - 3600]
        write_json(state, st)
        self.assertEqual(run("recover", failed=both)["escalated"], ["kea-dhcp4-server.service"])
        self.assertEqual(run("recover", failed=both)["escalated"], [])
        # Back up → "recovered", record cleared
        r = run("recover", failed="")
        self.assertEqual(sorted(r["recovered"]), ["kea-dhcp4-server.service", "nftables.service"])
        self.assertEqual(load_json(state), {})

    def test_reboot_status_window_and_notices(self):
        def system(name, kernel):
            d = os.path.join(self.fake, "nix/store", name); os.makedirs(d, exist_ok=True)
            for part in ("kernel", "kernel-modules", "initrd"):
                tgt = os.path.join(self.fake, "nix/store", f"x-linux-{kernel}", part); os.makedirs(os.path.dirname(tgt), exist_ok=True)
                open(tgt, "a").close()
                if not os.path.lexists(os.path.join(d, part)):
                    os.symlink(tgt, os.path.join(d, part))
            return d
        os.makedirs(os.path.join(self.fake, "run"), exist_ok=True)
        os.symlink(system("sys-a", "6.12.93"), os.path.join(self.fake, "run/booted-system"))
        os.symlink(system("sys-a", "6.12.93"), os.path.join(self.fake, "run/current-system"))
        r = self.helios("reboot-status")
        self.assertEqual((r["needed"], r["running"], r["nextWindow"]), (False, "6.12.93", None))
        self.assertEqual(self.helios("reboot-window")["action"], "none")
        # A new kernel installed: needed, both versions reported, one notice per kernel
        os.remove(os.path.join(self.fake, "run/current-system"))
        os.symlink(system("sys-b", "6.12.95"), os.path.join(self.fake, "run/current-system"))
        r = self.helios("reboot-status")
        self.assertEqual((r["needed"], r["running"], r["installed"]), (True, "6.12.93", "6.12.95"))
        self.assertIn("kernel", r["reasons"])
        self.assertTrue(self.helios("reboot-check")["needed"])
        notice = os.path.join(self.data, "reboot-notice.json")
        self.assertEqual(load_json(notice)["for"], "6.12.95")
        # Window settings: validation, ordering of days, next window
        for bad, msg in (({"rebootWindow": {"days": []}}, "days"), ({"rebootWindow": {"time": "25:00"}}, "time"),
                         ({"rebootWindow": {"enabled": "yes"}}, "true or false"), ({"nope": 1}, "expected"),
                         ({"rebootWindow": {"time": "\u0660\u0664:\u0660\u0660"}}, "time")):
            self.assertIn(msg, self.helios("maintenance-set", json.dumps(bad), ok=False)["error"])
        r = self.helios("maintenance-set", json.dumps({"rebootWindow": {"enabled": True, "days": ["sun", "wed"], "time": "04:30"}}))
        self.assertEqual(r["maintenance"]["rebootWindow"]["days"], ["wed", "sun"])
        self.assertIsNotNone(r["nextWindow"])
        self.assertIn("Reboot window: wed, sun 04:30", self.helios("pending")["changes"])
        # In the window: skipped while a change is being confirmed, otherwise reboots
        write_json(os.path.join(self.data, "apply.json"), {"state": "confirm"})
        self.assertEqual(self.helios("reboot-window")["action"], "skipped")
        self.assertIn("being applied", self.helios("reboot", ok=False)["error"])
        write_json(os.path.join(self.data, "apply.json"), {})
        self.assertEqual(self.helios("reboot-window")["action"], "reboot")
        self.assertIn("systemctl reboot --no-block", self.stub_log())
        # A refused reboot (polkit: not an admin) is reported, not claimed done
        env = dict(self.env, STUB_REBOOT_DENIED="1")
        r = json.loads(subprocess.run([sys.executable, CTL, "reboot"], capture_output=True, text=True, env=env).stdout)
        self.assertFalse(r["ok"])
        self.assertIn("reboot refused", r["error"])

    def test_restrict_unnamed_devices(self):
        r = self.helios("devices")
        self.assertEqual((r["restrictUnnamed"]["enabled"], r["restrictUnnamed"]["armed"]), (False, False))
        self.assertIn("trusted", r["restrictUnnamed"]["networks"])
        self.assertNotIn("iot", r["restrictUnnamed"]["networks"])
        self.assertFalse(any(d["restricted"] for d in r["devices"]))
        r = self.helios("restrict-unnamed", "on")
        self.assertTrue(r["restrictUnnamed"]["armed"])                       # the fixture has a named Trusted device
        self.assertIn("Restrict unnamed devices: on", self.helios("pending")["changes"])
        devs = self.helios("devices")["devices"]
        unnamed_family = [d for d in devs if not d["known"] and d.get("lease_vlan") == "family"]
        self.assertTrue(unnamed_family and all(d["restricted"] for d in unnamed_family))
        self.assertFalse(any(d["restricted"] for d in devs if d["known"]))
        # No named admin-network device: waits instead of locking everyone out
        st = load_json(os.path.join(self.cfg, "state.json"))
        st["devices"] = [d for d in st["devices"] if d["vlan"] not in ("trusted", "mgmt")]
        write_json(os.path.join(self.cfg, "state.json"), st)
        ru = self.helios("devices")["restrictUnnamed"]
        self.assertEqual((ru["armed"], ru["waitingForAdmin"]), (False, True))

    def test_adopt_assigns_ip_and_shows_pending(self):
        r = self.helios("device-set", json.dumps({"mac": "BB-BB-BB-00-00-02", "name": "pluto", "vlan": "iot",
                                               "profile": "default", "media": True}))
        self.assertEqual(r["device"]["ip"], "10.40.0.20")
        self.assertTrue(r["device"]["media"])
        p = self.helios("pending")
        self.assertTrue(p["dirty"])
        self.assertIn("Add pluto (iot, default)", p["changes"])
        # name clash + bad input
        bad = self.helios("device-set", json.dumps({"mac": "aa:aa:aa:00:00:01", "name": "pluto", "vlan": "family"}), ok=False)
        self.assertIn("already used", bad["error"])
        bad = self.helios("device-set", json.dumps({"mac": "aa:aa:aa:00:00:01", "name": "Bad Name", "vlan": "family"}), ok=False)
        self.assertIn("lowercase", bad["error"])
        # media is meaningless on admin networks (they reach everything), allowed elsewhere
        r = self.helios("device-set", json.dumps({"mac": "aa:aa:aa:00:00:01", "name": "ceres", "vlan": "family",
                                               "profile": "kid", "media": True}))
        self.assertTrue(r["device"]["media"])
        t = self.helios("device-set", json.dumps({"mac": "11:22:33:44:55:66", "name": "titan", "vlan": "trusted", "media": True}))
        self.assertFalse(t["device"]["media"])
        self.assertEqual(r["device"]["ip"], "10.30.0.20")

    def test_apply_confirm_commits_and_syncs_adguard(self):
        self.helios("device-set", json.dumps({"mac": "aa:aa:aa:00:00:01", "name": "ceres", "vlan": "family", "profile": "kid"}))
        self.helios("apply-start")
        st = self.wait_apply("confirm")
        self.assertGreater(st["deadline"], time.time())
        self.assertIn("nixos-rebuild switch", self.stub_log())
        names = {c["name"]: c for c in ADGUARD["clients"]}
        self.assertTrue(names["ceres"]["parental_enabled"])
        self.assertTrue(names["ceres"]["safe_search"]["youtube"])
        self.assertTrue(names["titan"]["use_global_settings"] is False and names["titan"]["filtering_enabled"] is False)
        self.assertIn("someone-manual", names)                       # never touch clients we don't manage
        self.assertTrue(any(c[2] == "Basic YWRtaW46cHc=" for c in ADGUARD["calls"] if c[0] == "GET"))
        self.helios("confirm")
        self.assertFalse(self.helios("pending")["dirty"])
        hist = self.helios("history")
        self.assertEqual(hist["commits"][0]["subject"], "Add ceres (family, kid)")

    def test_failed_rebuild_keeps_changes_pending(self):
        self.env["STUB_FAIL"] = "1"
        self.helios("device-set", json.dumps({"mac": "aa:aa:aa:00:00:01", "name": "ceres", "vlan": "family"}))
        self.helios("apply-start")
        self.wait_apply("failed")
        self.assertTrue(self.helios("pending")["dirty"])
        self.assertIn("Rebuild failed", "\n".join(self.helios("apply-status")["log"]))

    def test_rollback_reverts_state(self):
        self.helios("device-set", json.dumps({"mac": "aa:aa:aa:00:00:01", "name": "ceres", "vlan": "family"}))
        self.helios("apply-start")
        self.wait_apply("confirm")
        self.helios("rollback", "--auto")
        self.assertIn("--rollback", self.stub_log())
        self.assertFalse(self.helios("pending")["dirty"])
        self.assertNotIn("ceres", {d["name"] for d in load_json(os.path.join(self.cfg, "state.json"))["devices"]})
        self.assertEqual(self.helios("apply-status")["apply"]["state"], "rolledback")

    def test_block_is_instant_and_committed(self):
        self.helios("device-set", json.dumps({"mac": "aa:aa:aa:00:00:01", "name": "ceres", "vlan": "family"}))
        self.helios("apply-start")
        self.wait_apply("confirm")
        self.helios("confirm")
        r = self.helios("block", "aa:aa:aa:00:00:01", "on")
        self.assertTrue(r["live"])
        self.assertFalse(r["dirty"])
        self.assertIn("nft add element inet heliopause cutoff { 10.30.0.20 }", self.stub_log())
        self.assertIn("nft add element inet heliopause cutoff_mac { aa:aa:aa:00:00:01 }", self.stub_log())
        self.assertEqual(self.helios("history")["commits"][0]["subject"], "Pause ceres")
        self.helios("block", "aa:aa:aa:00:00:01", "off")
        self.assertIn("nft delete element inet heliopause cutoff { 10.30.0.20 }", self.stub_log())
        self.assertIn("nft delete element inet heliopause cutoff_mac { aa:aa:aa:00:00:01 }", self.stub_log())
        # unknown device can't be paused before adoption
        self.assertIn("adopt", self.helios("block", "bb:bb:bb:00:00:02", "on", ok=False)["error"])

    def test_restore_and_discard(self):
        self.helios("status")  # initialises git
        first = self.helios("history")["commits"][0]["rev"]
        self.helios("device-set", json.dumps({"mac": "aa:aa:aa:00:00:01", "name": "ceres", "vlan": "family"}))
        self.helios("apply-start")
        self.wait_apply("confirm")
        self.helios("confirm")
        self.helios("restore", first)
        self.assertIn("Remove ceres", self.helios("pending")["changes"])
        self.helios("discard")
        self.assertFalse(self.helios("pending")["dirty"])

    def test_settings(self):
        self.helios("settings-set", "--down", "850mbit", "--up", "32mbit", "--upnp", "off")
        p = self.helios("pending")
        self.assertIn("Change qos settings", p["changes"])
        self.assertIn("Change upnp settings", p["changes"])
        self.assertIn("rate", self.helios("settings-set", "--down", "fast", ok=False)["error"])

    def test_status_and_readers(self):
        s = self.helios("status")
        self.assertEqual(s["wan"]["ipv4"], "98.42.17.201/22")
        self.assertEqual(s["wan"]["gateway"], "98.42.16.1")
        self.assertEqual({v["name"]: v["clients"] for v in s["vlans"]}["family"], 1)
        self.assertEqual(s["devices"]["new"], 2)
        self.assertEqual(s["upnp"]["mappings"], 2)
        self.assertEqual(s["tailscale"]["peers_online"], 2)
        svc = {x["unit"]: x for x in s["services"]}
        self.assertEqual(svc["kea-dhcp4-server"]["active"], "active")
        self.assertEqual(svc["miniupnpd"]["active"], "failed")
        c = self.helios("counters")
        self.assertEqual(c["ifaces"]["wan"]["rx"], 5_000_000_000)
        q = self.helios("qos")
        self.assertEqual(q["upload"]["bandwidth"], 6250000)
        self.assertEqual(len(q["download"]["tins"]), 3)
        u = self.helios("upnp")
        self.assertEqual(u["mappings"][0]["ext_port"], 3074)
        v = self.helios("vnstat")
        self.assertEqual(v["interfaces"][0]["name"], "wan")

    def test_backup_commits_adguard_yaml(self):
        with open(os.path.join(self.fake, "var/lib/AdGuardHome/AdGuardHome.yaml"), "w") as f:
            f.write(
                "http:\n  address: 0.0.0.0:3000\nusers:\n  - name: admin\n    password: $2a$10$secrethashsecrethash\n"
                "tls:\n  private_key: |-\n    -----BEGIN PRIVATE KEY-----\n    c2VjcmV0\n    -----END PRIVATE KEY-----\n"
                "  certificate_chain: \"\"\ndns:\n  upstream_dns:\n    - https://me:hunter2@dns.example.com/dns-query\n")
        r = self.helios("backup")
        self.assertTrue(r["changed"])
        with open(os.path.join(self.cfg, "backups/adguard/AdGuardHome.yaml")) as f:
            saved = f.read()
        for secret in ("secrethash", "PRIVATE KEY", "c2VjcmV0", "hunter2"):
            self.assertNotIn(secret, saved)
        self.assertIn("password: REDACTED", saved)
        self.assertIn("private_key: REDACTED", saved)
        self.assertIn("https://REDACTED@dns.example.com/dns-query", saved)
        self.assertIn("address: 0.0.0.0:3000", saved)        # the rest is kept
        self.assertIn("certificate_chain: \"\"", saved)
        self.assertFalse(self.helios("backup")["changed"])   # idempotent
        self.assertEqual(self.helios("history")["commits"][0]["subject"], "Backup: AdGuard settings")

    def test_solar_wind_setup_send_and_read(self):
        import bcrypt
        self.assertTrue(self.helios("notify-setup")["created"])
        self.assertFalse(self.helios("notify-setup")["created"])          # idempotent
        env = dict(line.split("=", 1) for line in read_file(os.path.join(self.data, "solarwind.env")).splitlines())
        users = env["NTFY_AUTH_USERS"].split(",")
        self.assertEqual([u.split(":")[0] for u in users], ["helios", "phone"])
        self.assertIn("helios:flare:write-only", env["NTFY_AUTH_ACCESS"].split(","))
        self.assertIn("phone:aurora:read-only", env["NTFY_AUTH_ACCESS"].split(","))
        token = read_file(os.path.join(self.data, "solarwind-token")).strip()
        self.assertRegex(token, r"^tk_[a-z0-9]{29}$")
        self.assertEqual(env["NTFY_AUTH_TOKENS"], f"helios:{token}:HeliOS router")
        self.assertEqual(oct(os.stat(os.path.join(self.data, "solarwind.env")).st_mode & 0o777), "0o600")
        self.assertIsNone(self.helios("notify-info")["password"])
        pw = self.helios("notify-info", "--reveal")["password"]
        phone_hash = users[1].split(":", 1)[1].rsplit(":", 1)[0]
        self.assertTrue(bcrypt.checkpw(pw.encode(), phone_hash.encode()))
        self.helios("notify-test", "flare")
        post = NTFY["posts"][-1]
        self.assertEqual((post["topic"], post["auth"], post["priority"]), ("flare", f"Bearer {token}", "high"))
        recent = self.helios("notify-recent")["messages"]
        self.assertEqual(recent[0]["kind"], "flare")
        self.assertTrue(NTFY["auth"].startswith("Basic "))              # reads with the phone login
        self.helios("notify-setup", "--rotate")
        self.assertNotEqual(read_file(os.path.join(self.data, "solarwind-token")).strip(), token)

    def test_solar_wind_custom_phone_login(self):
        import bcrypt
        envf = os.path.join(self.data, "solarwind.env")
        read_env = lambda: dict(l.split("=", 1) for l in read_file(envf).splitlines())
        self.helios("notify-setup")
        before = read_env()
        helios_user = before["NTFY_AUTH_USERS"].split(",")[0]
        # bad input is rejected and changes nothing
        for user, pw in [("Bad Name", "correct-horse-1"), ("ab", "correct-horse-1"), ("helios", "correct-horse-1"),
                         ("anna", "short"), ("anna", "x" * 129)]:
            r = self.helios("notify-credentials", "--user", user, stdin=pw + "\n", ok=False)
            self.assertFalse(r["ok"], (user, pw))
            self.assertNotIn(pw, json.dumps(r))
        self.assertEqual(read_env(), before)
        r = self.helios("notify-credentials", "--user", "anna-phone", stdin="correct-horse-1\n")
        self.assertEqual(r["user"], "anna-phone")
        self.assertNotIn("correct-horse-1", json.dumps(r))
        env = read_env()
        users = env["NTFY_AUTH_USERS"].split(",")
        self.assertEqual(users[0], helios_user)                                # helios hash untouched
        self.assertEqual([u.split(":")[0] for u in users], ["helios", "anna-phone"])
        self.assertTrue(bcrypt.checkpw(b"correct-horse-1", users[1].split(":", 1)[1].rsplit(":", 1)[0].encode()))
        access = env["NTFY_AUTH_ACCESS"].split(",")
        self.assertIn("anna-phone:flare:read-only", access)
        self.assertIn("helios:aurora:write-only", access)
        self.assertFalse([a for a in access if a.startswith("phone:")])
        self.assertEqual(env["NTFY_AUTH_TOKENS"], before["NTFY_AUTH_TOKENS"])  # token unchanged
        self.assertEqual(oct(os.stat(envf).st_mode & 0o777), "0o600")
        info = self.helios("notify-info", "--reveal")
        self.assertEqual((info["user"], info["password"]), ("anna-phone", "correct-horse-1"))
        self.helios("notify-recent")
        self.assertTrue(NTFY["auth"].startswith("Basic "))
        # rotate keeps the chosen username, new password
        self.helios("notify-setup", "--rotate")
        info = self.helios("notify-info", "--reveal")
        self.assertEqual(info["user"], "anna-phone")
        self.assertNotEqual(info["password"], "correct-horse-1")
        self.assertIn("anna-phone:flare:read-only", read_env()["NTFY_AUTH_ACCESS"])

    def test_solar_wind_credentials_run_setup_first(self):
        self.helios("notify-credentials", "--user", "mine", stdin="a-long-enough-pw")
        self.assertTrue(os.path.exists(os.path.join(self.data, "solarwind-token")))
        self.assertEqual(self.helios("notify-info")["user"], "mine")

    def test_uplink_outage_cycle(self):
        self.env["STUB_PING"] = "fail"
        self.assertEqual(self.helios("uplink-check")["state"], "up")      # one miss isn't an outage
        self.assertEqual(self.helios("uplink-check")["state"], "down")
        self.env["STUB_PING"] = "ok"
        self.assertEqual(self.helios("uplink-check")["state"], "up")
        self.assertEqual(NTFY["posts"][-1]["topic"], "aurora")          # "internet is back"
        outs = self.helios("history")["outages"]
        self.assertEqual(len(outs), 1)
        self.assertGreaterEqual(outs[0]["duration"], 30)


    # ------------------------------------------------- drivers & hardware
    def make_sysfs(self):
        net = os.path.join(self.fake, "sys/class/net")
        dev = os.path.join(self.fake, "sys/devices")

        def nic(name, mac, bus_path, driver, speed="1000", carrier="1"):
            d = os.path.join(dev, bus_path)
            os.makedirs(d, exist_ok=True)
            os.makedirs(os.path.join(dev, "drivers", driver), exist_ok=True)
            os.symlink(os.path.join(dev, "drivers", driver), os.path.join(d, "driver"))
            n = os.path.join(net, name)
            os.makedirs(n)
            os.symlink(d, os.path.join(n, "device"))
            for k, v in (("address", mac), ("speed", speed), ("carrier", carrier)):
                write_file(os.path.join(n, k), v + "\n")
        nic("trunk0", "02:00:00:00:00:01", "pci0000:00/0000:00:1c.0/0000:01:00.0", "igb")
        nic("enp3s0", "aa:bb:cc:dd:ee:01", "pci0000:00/0000:00:1c.1/0000:03:00.0", "r8169", speed="-1", carrier="0")
        nic("enx001", "aa:bb:cc:dd:ee:09", "pci0000:00/0000:00:14.0/usb2/2-1/2-1:1.0", "r8152")
        os.makedirs(os.path.join(net, "lan1"))             # DSA user port: no device link
        write_file(os.path.join(net, "lan1/phys_switch_id"), "00112233\n")
        write_file(os.path.join(net, "lan1/address"), "aa:bb:cc:dd:ee:77\n")
        write_file(os.path.join(net, "lan1/carrier"), "1\n")
        write_file(os.path.join(net, "lan1/speed"), "100\n")
        # "family" (from setUp) has no device link: virtual, skipped
        usb = os.path.join(self.fake, "sys/bus/usb/devices")

        def usbdev(name, vid, pid, cls="00", **files):
            d = os.path.join(usb, name)
            os.makedirs(d)
            for k, v in dict(idVendor=vid, idProduct=pid, bDeviceClass=cls, **files).items():
                write_file(os.path.join(d, k), v + "\n")
            return d
        usbdev("usb1", "1d6b", "0002", "09", product="xHCI Host Controller")
        usbdev("1-1", "05e3", "0610", "09", product="USB Hub")
        usbdev("1-2", "051d", "0002", manufacturer="American Power Conversion", product="Back-UPS ES 600M1", serial="3B1234X56789")
        d = usbdev("2-1", "0bda", "8152", manufacturer="Realtek", product="USB 10/100 LAN")
        os.makedirs(os.path.join(d, "2-1:1.0"))
        write_file(os.path.join(d, "2-1:1.0/bInterfaceClass"), "0a\n")
        d = usbdev("1-3", "1234", "5678", product="Gadget")
        os.makedirs(os.path.join(d, "1-3:1.0"))
        write_file(os.path.join(d, "1-3:1.0/bInterfaceClass"), "03\n")

    def test_detect_reads_sysfs_and_lldp(self):
        self.make_sysfs()
        det = self.helios("detect")
        self.assertEqual(load_json(os.path.join(self.tmp, "detect", "hardware.json"))["ports"], det["ports"])
        ports = {x["name"]: x for x in det["ports"]}
        self.assertEqual(sorted(ports), ["enp3s0", "enx001", "lan1", "trunk0"])
        self.assertEqual((ports["trunk0"]["role"], ports["trunk0"]["bus"], ports["trunk0"]["driver"], ports["trunk0"]["speed"]),
                         ("trunk", "pci", "igb", 1000))
        self.assertEqual((ports["enp3s0"]["role"], ports["enp3s0"]["speed"], ports["enp3s0"]["carrier"]), ("free", None, False))
        self.assertEqual((ports["enx001"]["bus"], ports["enx001"]["driver"]), ("usb", "r8152"))
        self.assertEqual((ports["lan1"]["bus"], ports["lan1"]["driver"], ports["lan1"]["speed"]), ("dsa", None, 100))
        usb = {x["id"]: x for x in det["usb"]}
        self.assertEqual(sorted(usb), ["051d:0002", "0bda:8152", "1234:5678"])        # hubs skipped
        self.assertEqual((usb["051d:0002"]["kind"], usb["051d:0002"]["serial"]), ("ups", "3B1234X56789"))
        self.assertEqual((usb["0bda:8152"]["kind"], usb["1234:5678"]["kind"]), ("nic", "other"))
        self.assertEqual(det["lldp"], [{"localPort": "trunk0", "chassis": "switch-1", "mgmtIp": "10.99.0.2",
                                        "sysDescr": "Test Switch 24G", "port": "Ethernet1/0/8", "portDescr": "uplink to router",
                                        "capabilities": ["Bridge"]}])

    def test_detect_without_lldp_or_sysfs(self):
        self.env["PATH"] = STUBS + "/none:" + "/usr/bin:/bin"       # no lldpcli
        det = self.helios("detect")
        self.assertEqual((det["ports"], det["usb"], det["lldp"]), ([], [], []))

    def test_detect_roles_follow_hardware(self):
        self.make_sysfs()
        self.helios("hardware-set", json.dumps({"id": "router", "kind": "router", "ports": [
            {"match": {"mac": "aa:bb:cc:dd:ee:01"}, "mode": "wan"}, {"match": {"name": "lan1"}, "mode": "access", "network": "trusted"}]}))
        ports = {x["name"]: x["role"] for x in self.helios("detect")["ports"]}
        self.assertEqual(ports, {"enp3s0": "wan", "enx001": "free", "lan1": "mapped", "trunk0": "trunk"})

    def test_hardware_suggestions_and_dismissal(self):
        self.assertIsNone(self.helios("hardware")["detected"])
        self.assertEqual(self.helios("hardware")["suggestions"], [])
        self.make_sysfs()
        self.helios("detect")
        sug = {x["kind"]: x for x in self.helios("hardware")["suggestions"]}
        self.assertEqual(sorted(sug), ["router", "switch", "ups"])
        self.assertEqual(sug["ups"]["device"]["match"], {"usb": "051d:0002", "serial": "3B1234X56789"})
        self.assertEqual(sug["switch"]["device"]["label"], "switch-1")
        self.assertEqual({tuple(x["match"].items()) for x in sug["router"]["device"]["ports"]},
                         {(("mac", "aa:bb:cc:dd:ee:01"),), (("mac", "aa:bb:cc:dd:ee:09"),), (("name", "lan1"),)})
        for k in ("ups", "switch", "router"):                    # every suggestion is accepted as-is
            self.helios("hardware-set", json.dumps(sug[k]["device"]))
        self.helios("detect")
        self.assertEqual(self.helios("hardware")["suggestions"], [])

    def test_hardware_drivers_listing_and_ups(self):
        h = self.helios("hardware")
        drv = {x["name"]: x for x in h["drivers"]}
        self.assertEqual(sorted(drv), ["lldp", "local-ports", "manual", "nut"])
        self.assertTrue(drv["manual"]["enabled"] and drv["manual"]["always"] and drv["lldp"]["enabled"])
        self.assertFalse(drv["nut"]["enabled"])
        self.assertEqual(drv["nut"]["settings"]["shutdown"], {"charge": 20, "runtime": 180})
        r = self.helios("hardware-set", json.dumps({"id": "ups", "kind": "ups", "label": "Office UPS", "match": {"usb": "051D:0002"}}))
        self.assertEqual(r["enabledDriver"], "nut")
        self.assertEqual(r["device"]["nutDriver"], "usbhid-ups")
        h = self.helios("hardware")
        self.assertTrue({x["name"]: x for x in h["drivers"]}["nut"]["enabled"])
        self.assertEqual(h["ups"]["ups"]["status"], "OL")
        self.assertEqual((h["ups"]["ups"]["charge"], h["ups"]["ups"]["runtime"], h["ups"]["ups"]["load"]), (97, 1380, 23))
        self.assertEqual(h["ups"]["ups"]["model"], "Back-UPS ES 600M1")
        self.assertNotIn("enabledDriver", self.helios("hardware-set", json.dumps({"id": "ups", "kind": "ups", "label": "Office UPS", "match": {"usb": "051d:0002"}})))
        self.env["STUB_UPS"] = "fail"
        u = self.helios("hardware")["ups"]["ups"]
        self.assertEqual((u["status"], u["charge"], u["raw"]), (None, None, {}))
        msgs = self.helios("pending")["changes"]
        self.assertIn("Enable driver nut", msgs)
        self.assertIn("Add hardware Office UPS", msgs)

    def test_driver_set_validation(self):
        bad = lambda d, msg: self.assertIn(msg, self.helios("driver-set", json.dumps(d), ok=False)["error"])
        bad({"name": "nope", "enabled": True}, "unknown driver")
        bad({"name": "manual", "enabled": False}, "always on")
        bad({"name": "local-ports", "enabled": False}, "always on")
        bad({"name": "nut", "enabled": "yes"}, "true or false")
        bad({"name": "nut", "enabled": True, "shutdown": {"charge": 4}}, "shutdown.charge")
        bad({"name": "nut", "enabled": True, "shutdown": {"charge": 91}}, "shutdown.charge")
        bad({"name": "nut", "enabled": True, "shutdown": {"charge": True}}, "shutdown.charge")
        bad({"name": "nut", "enabled": True, "shutdown": {"runtime": 3601}}, "shutdown.runtime")
        bad({"name": "nut", "enabled": True, "shutdown": {"runtime": "5"}}, "shutdown.runtime")
        bad({"name": "nut", "enabled": True, "server": {"enabled": 1}}, "server.enabled")
        bad({"name": "nut", "enabled": True, "server": {"networks": ["nope"]}}, "unknown network")
        bad({"name": "nut", "enabled": True, "server": {"networks": "trusted"}}, "list")
        bad({"name": "nut", "enabled": True, "bogus": 1}, "unknown setting")
        bad({"name": "lldp", "enabled": True, "x": 1}, "no settings")
        self.assertEqual(self.helios("pending")["changes"], [])
        self.assertTrue(self.helios("driver-set", json.dumps({"name": "manual", "enabled": True}))["enabled"])
        r = self.helios("driver-set", json.dumps({"name": "nut", "enabled": True, "shutdown": {"charge": 30},
                                               "server": {"enabled": True, "networks": ["trusted", "trusted"]}}))
        self.assertEqual((r["shutdown"], r["server"]), ({"charge": 30, "runtime": 180}, {"enabled": True, "networks": ["trusted"]}))
        st = load_json(os.path.join(self.cfg, "state.json"))
        self.assertEqual(st["drivers"]["nut"], {"enabled": True, "shutdown": {"charge": 30, "runtime": 180},
                                                 "server": {"enabled": True, "networks": ["trusted"]}})
        self.helios("driver-set", json.dumps({"name": "nut", "enabled": False}))
        self.helios("driver-set", json.dumps({"name": "lldp", "enabled": False}))
        changes = self.helios("pending")["changes"]
        self.assertIn("Disable driver lldp", changes)
        self.assertNotIn("Enable driver nut", changes)

    def test_driver_change_messages(self):
        self.helios("driver-set", json.dumps({"name": "nut", "enabled": True}))
        subprocess.run(["git", "-C", self.cfg, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qam", "x"], check=True)
        self.helios("driver-set", json.dumps({"name": "nut", "shutdown": {"runtime": 60}}))
        self.assertEqual(self.helios("pending")["changes"], ["Change driver nut settings"])

    def test_hardware_set_validation(self):
        net = {"id": "sw", "kind": "switch", "label": "Sw", "ports": [{"port": "1", "mode": "access", "network": "trusted"}]}
        bad = lambda d, msg: self.assertIn(msg, self.helios("hardware-set", json.dumps(d), ok=False)["error"])
        bad({**net, "id": "Bad_Id"}, "id:")
        bad({**net, "id": "1abc"}, "id:")
        bad({**net, "kind": "toaster"}, "kind")
        bad({**net, "driver": "nut"}, "can't handle")
        bad({**net, "driver": "nope"}, "can't handle")
        bad({**net, "label": "x" * 41}, "label")
        bad({**net, "notes": "x" * 201}, "notes")
        bad({**net, "id": "router"}, "router")
        bad({**net, "kind": "router", "driver": "local-ports"}, "router")
        bad({**net, "ports": [{"port": "1", "mode": "access"}]}, "network")
        bad({**net, "ports": [{"port": "1", "mode": "access", "network": "nope"}]}, "network")
        bad({**net, "ports": [{"port": "1", "mode": "weird"}]}, "mode")
        bad({**net, "ports": [{"port": "1", "mode": "trunk", "networks": ["nope"]}]}, "unknown network")
        bad({**net, "ports": [{"port": "1", "mode": "unused"}, {"port": "1", "mode": "unused"}]}, "duplicate")
        bad({**net, "ports": [{"port": str(i), "mode": "unused"} for i in range(65)]}, "at most 64")
        bad({**net, "ssids": [{"ssid": "x", "network": "iot"}]}, "only access points")
        ap = {"id": "ap1", "kind": "ap", "ports": []}
        bad({**ap, "ssids": [{"ssid": "x", "network": "iot"}] * 17}, "at most 16")
        bad({**ap, "ssids": [{"ssid": "", "network": "iot"}]}, "1-32")
        bad({**ap, "ssids": [{"ssid": "y" * 33, "network": "iot"}]}, "1-32")
        bad({**ap, "ssids": [{"ssid": "ok", "network": "nope"}]}, "unknown network")
        ups = {"id": "ups", "kind": "ups"}
        bad(ups, "match.usb")
        bad({**ups, "match": {"usb": "51d:2"}}, "match.usb")
        bad({**ups, "match": {"usb": "051d:0002", "serial": "a b"}}, "serial")
        bad({**ups, "match": {"usb": "051d:0002"}, "nutDriver": "../x"}, "nutDriver")
        bad({**ups, "match": {"usb": "051d:0002"}, "ports": [{"port": "1", "mode": "unused"}]}, "no ports")
        self.assertEqual(self.helios("hardware")["devices"], [])
        self.helios("hardware-set", json.dumps({**ups, "match": {"usb": "051d:0002"}}))
        bad({**ups, "id": "ups2", "match": {"usb": "051d:0002"}}, "already configured")
        self.helios("hardware-set", json.dumps(net))
        bad({**net, "id": "ups", "from": "sw"}, "already exists")
        bad({**net, "from": "ghost"}, "no hardware")

    def test_router_port_rules(self):
        def router(*ports):
            return json.dumps({"id": "router", "kind": "router", "ports": list(ports)})
        bad = lambda *ports: self.helios("hardware-set", router(*ports), ok=False)["error"]
        self.assertIn("trunk port", bad({"match": {"mac": "02:00:00:00:00:01"}, "mode": "unused"}))
        self.assertIn("rescue port", bad({"match": {"mac": "02:00:00:00:00:02"}, "mode": "unused"}))
        self.assertIn("rescue port", bad({"match": {"mac": "02:00:00:00:00:02".upper()}, "mode": "unused"}))
        self.assertIn("can't be mapped", bad({"match": {"name": "trunk0"}, "mode": "unused"}))
        self.assertIn("MAC", bad({"match": {"mac": "nope"}, "mode": "unused"}))
        self.assertIn("match", bad({"mode": "unused"}))
        self.assertIn("match", bad({"match": {"mac": "aa:bb:cc:dd:ee:01", "name": "x"}, "mode": "unused"}))
        self.assertIn("only one WAN", bad({"match": {"mac": "aa:bb:cc:dd:ee:01"}, "mode": "wan"}, {"match": {"name": "lan2"}, "mode": "wan"}))
        self.assertIn("duplicate", bad({"match": {"name": "lan2"}, "mode": "unused"}, {"match": {"name": "lan2"}, "mode": "unused"}))
        r = self.helios("hardware-set", router({"match": {"mac": "AA:BB:CC:DD:EE:01"}, "label": "Port 2", "mode": "access", "network": "trusted"},
                                            {"match": {"name": "lan2"}, "mode": "trunk", "networks": ["family", "iot"]},
                                            {"match": {"mac": "aa:bb:cc:dd:ee:03"}, "mode": "wan"}))
        ports = r["device"]["ports"]
        self.assertEqual(ports[0]["match"], {"mac": "aa:bb:cc:dd:ee:01"})
        self.assertEqual((ports[1]["networks"], ports[1]["wan"]), (["family", "iot"], False))
        self.assertEqual(r["device"]["driver"], "local-ports")

    def test_hardware_edit_rename_remove_and_checklists(self):
        sw = {"id": "main-switch", "kind": "switch", "label": "Main switch", "ports": [
            {"port": "1", "mode": "trunk", "networks": "all", "wan": True, "note": "to router"},
            {"port": "2", "mode": "wan", "note": "modem"},
            {"port": "3", "mode": "access", "network": "trusted"},
            {"port": "4", "mode": "trunk", "networks": ["iot"]},
            {"port": "5", "mode": "unused"}]}
        self.helios("hardware-set", json.dumps(sw))
        ap = {"id": "living-ap", "kind": "ap", "label": "Living room AP",
              "ports": [{"port": "uplink", "mode": "trunk", "networks": ["family", "iot"]}],
              "ssids": [{"ssid": "Home", "network": "family"}, {"ssid": "Guests", "network": "iot"}]}
        self.helios("hardware-set", json.dumps(ap))
        cl = self.helios("hardware")["checklists"]
        self.assertEqual(cl["main-switch"], [
            "Port 1: trunk — tagged VLANs 10 (WAN), 20 (Trusted), 30 (Family), 40 (IoT), 50 (Strict), 99 (Mgmt) (to router)",
            "Port 2: wan — untagged VLAN 10 (WAN) (modem)",
            "Port 3: access — untagged VLAN 20 (Trusted)",
            "Port 4: trunk — tagged VLANs 40 (IoT)",
            "Port 5: unused — disable it or leave it empty"])
        self.assertEqual(cl["living-ap"], ["uplink: trunk — tagged VLANs 30 (Family), 40 (IoT)",
                                           "SSID “Home” → VLAN 30 (Family)", "SSID “Guests” → VLAN 40 (IoT)"])
        self.assertEqual(sorted(cl), ["living-ap", "main-switch"])
        changes = self.helios("pending")["changes"]
        self.assertIn("Add hardware Main switch", changes)
        # edit: ports + ssids changed; rename via from
        subprocess.run(["git", "-C", self.cfg, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qam", "x"], check=True)
        ap["ssids"] = ap["ssids"][:1]
        ap["ports"] = []
        self.helios("hardware-set", json.dumps(ap))
        self.assertEqual(self.helios("pending")["changes"], ["Edit hardware Living room AP: ports, ssids"])
        self.helios("hardware-set", json.dumps({**ap, "id": "ap-2", "from": "living-ap"}))
        self.assertEqual([x["id"] for x in self.helios("hardware")["devices"]], ["main-switch", "ap-2"])
        self.helios("hardware-remove", "main-switch")
        self.assertIn("Remove hardware Main switch", self.helios("pending")["changes"])
        self.assertIn("no hardware", self.helios("hardware-remove", "main-switch", ok=False)["error"])

    def test_network_rename_and_remove_propagate_to_hardware(self):
        self.helios("hardware-set", json.dumps({"id": "sw", "kind": "switch", "ports": [
            {"port": "1", "mode": "access", "network": "iot"}, {"port": "2", "mode": "trunk", "networks": ["iot", "family"]},
            {"port": "3", "mode": "trunk", "networks": "all"}]}))
        self.helios("hardware-set", json.dumps({"id": "ap", "kind": "ap", "ports": [], "ssids": [
            {"ssid": "A", "network": "iot"}, {"ssid": "B", "network": "family"}]}))
        self.helios("driver-set", json.dumps({"name": "nut", "enabled": True, "server": {"enabled": True, "networks": ["iot", "trusted"]}}))
        iot = next(n for n in self.helios("networks")["networks"] if n["name"] == "iot")
        self.helios("network-set", json.dumps({"from": "iot", "name": "things", "role": iot["role"], "vlan": iot["vlan"], "prefix": iot["prefix"]}))
        hw = {d["id"]: d for d in self.helios("hardware")["devices"]}
        self.assertEqual(hw["sw"]["ports"][0]["network"], "things")
        self.assertEqual(hw["sw"]["ports"][1]["networks"], ["things", "family"])
        self.assertEqual(hw["sw"]["ports"][2]["networks"], "all")
        self.assertEqual(hw["ap"]["ssids"][0]["network"], "things")
        nut = {d["name"]: d for d in self.helios("hardware")["drivers"]}["nut"]
        self.assertEqual(nut["settings"]["server"]["networks"], ["things", "trusted"])
        r = self.helios("network-remove", "things")
        self.assertEqual(r["hardwareReferencesDropped"], 4)
        h = self.helios("hardware")
        hw = {d["id"]: d for d in h["devices"]}
        self.assertEqual(hw["sw"]["ports"][0], {"port": "1", "mode": "unused"})
        self.assertEqual(hw["sw"]["ports"][1]["networks"], ["family"])
        self.assertEqual(hw["ap"]["ssids"], [{"ssid": "B", "network": "family"}])
        nut = {d["name"]: d for d in h["drivers"]}["nut"]
        self.assertEqual(nut["settings"]["server"]["networks"], ["trusted"])

    def test_ups_event_spools_and_forward_sends(self):
        spool = self.env["HELIOS_UPS_SPOOL"]
        self.env.update(NOTIFYTYPE="ONBATT", UPSNAME="ups")
        self.helios("ups-event", "UPS", "ups", "on", "battery")
        self.env.update(NOTIFYTYPE="ONLINE")
        self.helios("ups-event", "back")
        files = sorted(os.listdir(spool))
        self.assertEqual(len(files), 2)
        self.assertTrue(all(f.endswith(".json") for f in files))
        ev = load_json(os.path.join(spool, files[0]))
        self.assertEqual((ev["type"], ev["ups"], ev["message"]), ("ONBATT", "ups", "UPS ups on battery"))
        self.assertIsInstance(ev["time"], int)
        self.assertEqual(NTFY["posts"], [])                  # the event hook sends nothing itself
        del self.env["NOTIFYTYPE"]
        self.assertIn("NOTIFYTYPE", self.helios("ups-event", ok=False)["error"])
        self.helios("notify-setup")
        self.helios("hardware-set", json.dumps({"id": "ups", "kind": "ups", "label": "Office UPS", "match": {"usb": "051d:0002"}}))
        r = self.helios("ups-forward")
        self.assertEqual((r["sent"], r["failed"]), (2, 0))
        self.assertEqual([x["topic"] for x in NTFY["posts"]], ["flare", "aurora"])
        self.assertEqual(NTFY["posts"][0]["title"], "UPS Office UPS: on battery")
        self.assertEqual(NTFY["posts"][0]["body"], "UPS ups on battery")
        self.assertTrue(NTFY["posts"][0]["auth"].startswith("Bearer tk_"))
        self.assertEqual(os.listdir(spool), [])
        self.assertEqual(self.helios("ups-forward")["sent"], 0)

    def test_ups_forward_urgency_and_failure_keeps_files(self):
        spool = self.env["HELIOS_UPS_SPOOL"]
        for t in ("LOWBATT", "COMMOK", "REPLBATT", "SHUTDOWN", "NOCOMM", "WEIRD"):
            self.env.update(NOTIFYTYPE=t, UPSNAME="other@localhost")
            self.helios("ups-event")
        self.helios("ups-forward")
        self.assertEqual([x["topic"] for x in NTFY["posts"]], ["flare", "aurora", "flare", "flare", "flare", "aurora"])
        self.assertEqual(NTFY["posts"][0]["title"], "UPS other: battery low")
        self.assertEqual(NTFY["posts"][5]["title"], "UPS other: weird")
        # server unreachable → files stay for the next run
        conf = load_json(self.conf_path)
        conf["solarWind"]["url"] = "http://127.0.0.1:1"
        write_json(self.conf_path, conf)
        self.env.update(NOTIFYTYPE="ONBATT")
        self.helios("ups-event")
        r = self.helios("ups-forward")
        self.assertEqual((r["sent"], r["failed"]), (0, 1))
        self.assertEqual(len(os.listdir(spool)), 1)

    def vulnix_file(self, entries):
        path = os.path.join(self.tmp, "vulnix.json")
        write_json(path, entries)
        return path

    def test_vulnix_report_summary_and_notifications(self):
        entries = [
            {"name": "openssl-3.0.1", "pname": "openssl", "version": "3.0.1", "affected_by": ["CVE-1", "CVE-2"],
             "whitelisted": [], "cvssv3_basescore": {"CVE-1": 9.8, "CVE-2": 5.0}},
            {"name": "curl-8.0", "pname": "curl", "version": "8.0", "affected_by": ["CVE-3"], "cvssv3_basescore": {"CVE-3": 7.5}},
            {"name": "bash-5.1", "affected_by": ["CVE-4"], "cvssv3_basescore": {"CVE-4": 4.0}},
            {"name": "zlib-1.2", "affected_by": ["CVE-5"]},                                 # no score → low
            {"name": "ignored-1.0", "affected_by": ["CVE-6"], "whitelisted": ["CVE-6"], "cvssv3_basescore": {"CVE-6": 9.9}},
            {"name": "clean-1.0", "affected_by": []},
            "garbage"]
        self.helios("notify-setup")
        r = self.helios("vulnix-report", self.vulnix_file(entries))
        self.assertEqual((r["total"], r["critical"], r["high"], r["medium"], r["low"]), (4, 1, 1, 1, 1))
        self.assertEqual(r["top"][0], {"name": "openssl", "version": "3.0.1", "cve": "CVE-1", "score": 9.8})
        self.assertEqual([x["name"] for x in r["top"]], ["openssl", "curl", "bash", "zlib"])
        self.assertEqual(r["top"][2]["version"], "5.1")
        self.assertEqual([x["topic"] for x in NTFY["posts"]], ["flare"])
        self.assertIn("2 new", NTFY["posts"][0]["title"])
        sec = self.helios("hardware")["security"]
        self.assertEqual((sec["total"], sec["critical"]), (4, 1))
        self.assertNotIn("known", sec)
        # same findings again: informational only
        self.helios("vulnix-report", self.vulnix_file(entries))
        self.assertEqual([x["topic"] for x in NTFY["posts"]], ["flare", "aurora"])
        self.assertIn("4 packages", NTFY["posts"][1]["body"])
        # a new high one → urgent again, naming just that one
        entries.append({"name": "git-2.40", "affected_by": ["CVE-7"], "cvssv3_basescore": {"CVE-7": 8.1}})
        self.helios("vulnix-report", self.vulnix_file(entries))
        self.assertEqual(NTFY["posts"][2]["topic"], "flare")
        self.assertIn("1 new", NTFY["posts"][2]["title"])
        self.assertIn("CVE-7", NTFY["posts"][2]["body"])
        self.assertNotIn("CVE-1", NTFY["posts"][2]["body"])
        # clean report → nothing sent
        n = len(NTFY["posts"])
        r = self.helios("vulnix-report", self.vulnix_file([]))
        self.assertEqual((r["total"], len(NTFY["posts"])), (0, n))
        self.assertIn("cannot read", self.helios("vulnix-report", os.path.join(self.tmp, "missing"), ok=False)["error"])


    # ------------------------------------------------------ remote access
    def hs(self, *p):
        return os.path.join(self.tmp, "hs", *p)

    def test_remote_defaults_and_settings_validation(self):
        r = self.helios("remote")
        self.assertTrue(r["settings"]["ingress"]["tailscale"]["enabled"])
        self.assertFalse(r["settings"]["ingress"]["wireguard"]["enabled"])
        self.assertEqual(r["settings"]["egress"], {"mode": "direct", "killswitch": True, "proxy": None})
        bad = lambda d, msg: self.assertIn(msg, self.helios("remote-set", json.dumps(d), ok=False)["error"])
        bad({"bogus": 1}, "unknown setting")
        bad({"ingress": {"nope": {}}}, "unknown way in")
        bad({"ingress": {"wireguard": {"port": 0}}}, "1 to 65535")
        bad({"ingress": {"wireguard": {"port": "51820"}}}, "1 to 65535")
        bad({"ingress": {"wireguard": {"port": 53}}}, "used by the router")
        bad({"ingress": {"wireguard": {"port": 1194}}}, "can't both use")        # openvpn's default
        bad({"ingress": {"proxy": {"socksPort": 3128}}}, "can't both use")        # http's default
        bad({"ingress": {"wireguard": {"peers": []}}}, "own commands")
        bad({"ingress": {"openvpn": {"proto": "sctp"}}}, "udp or tcp")
        bad({"ingress": {"wireguard": {"enabled": "yes"}}}, "true or false")
        bad({"ingress": {"proxy": {"networks": ["nope"]}}}, "unknown network")
        bad({"ingress": {"proxy": {"enabled": True, "networks": ["iot"]}}}, "at least one user")
        write_file(os.path.join(self.fake, "run/miniupnpd.leases"), "TCP:8888:10.20.0.30:8888:0:x\n", "a")
        bad({"ingress": {"wireguard": {"port": 8888}}}, "UPnP")
        self.helios("remote-proxy-user", "tv", stdin="a-long-enough-password")
        bad({"ingress": {"proxy": {"enabled": True}}}, "at least one home network")
        bad({"ingress": {"proxy": {"wan": True}}}, "unknown setting")   # never offered on the internet
        bad({"egress": {"mode": "wireguard"}}, "upload the wireguard")
        bad({"egress": {"mode": "openvpn"}}, "upload the openvpn")
        bad({"egress": {"mode": "proxy"}}, "host and port")
        bad({"egress": {"mode": "tor"}}, "egress.mode")
        bad({"egress": {"proxy": {"type": "ftp", "host": "a.b", "port": 1}}}, "socks5 or http")
        bad({"egress": {"proxy": {"type": "socks5", "host": "bad host!", "port": 1}}}, "host name")
        bad({"egress": {"proxy": {"type": "socks5", "host": "fe80::1%$(reboot)", "port": 1}}}, "host name")
        bad({"egress": {"proxy": {"type": "socks5", "host": "proxy.example.net\n", "port": 1}}}, "host name")
        bad({"egress": {"proxy": {"type": "socks5", "host": "a.b", "port": 99999}}}, "1 to 65535")
        bad({"egress": {"proxy": {"type": "socks5", "host": "a.b", "port": 1, "user": "x"}}}, "type, host and port")
        bad({"egress": {"killswitch": 1}}, "true or false")
        self.helios("remote-set", json.dumps({"ingress": {"proxy": {"enabled": True, "networks": ["iot"]}}}))
        self.helios("remote-set", json.dumps({"ingress": {"wireguard": {"enabled": True, "port": 51821}, "openvpn": {"proto": "tcp"}},
                                               "egress": {"mode": "proxy", "killswitch": False,
                                                          "proxy": {"type": "socks5", "host": "proxy.example.net", "port": 1080}}}))
        st = load_json(os.path.join(self.cfg, "state.json"))["heliosphere"]
        self.assertEqual(st["ingress"]["wireguard"]["port"], 51821)
        self.assertEqual(st["ingress"]["openvpn"]["proto"], "tcp")
        self.assertEqual(st["egress"]["proxy"]["host"], "proxy.example.net")
        ch = self.helios("pending")["changes"]
        for m in ("Remote access: enable WireGuard", "Remote access: enable proxy", "Remote access: way out → proxy",
                  "Remote access: kill switch off", "Remote access: change OpenVPN settings", "Remote access: add proxy user tv",
                  "Remote access: change way-out proxy"):
            self.assertIn(m, ch)

    def test_remote_egress_mode_needs_file_and_clear_guard(self):
        self.helios("remote-egress-upload", "wireguard", stdin=WG_OUT)
        self.helios("remote-set", json.dumps({"egress": {"mode": "wireguard"}}))
        self.assertIn("switch it to direct", self.helios("remote-egress-clear", "wireguard", ok=False)["error"])
        self.helios("remote-set", json.dumps({"egress": {"mode": "direct"}}))
        self.assertEqual(self.helios("remote-egress-clear", "wireguard")["removed"], ["wg-out.conf"])
        self.assertFalse(os.path.exists(self.hs("egress", "wg-out.conf")))

    def test_remote_network_prefix_reserved(self):
        for pfx in ("10.60.0", "10.61.0", "10.62.0"):
            r = self.helios("network-set", json.dumps({"name": "extra", "role": "iot", "vlan": 77, "prefix": pfx}), ok=False)
            self.assertIn("Heliosphere", r["error"])

    def test_remote_wg_add_remove(self):
        self.assertIn("hasn't started WireGuard", self.helios("remote-wg-add", "pixel", ok=False)["error"])
        os.makedirs(self.hs("wireguard"))
        write_file(self.hs("wireguard", "public"), "U0VSVkVSUFVCX19fX19fX19fX19fX19fX19fX19fX18=\n")
        r = self.helios("remote-wg-add", "pixel")
        self.assertEqual(r["ip"], "10.61.0.2")
        cfg = r["config"]
        self.assertIn("PrivateKey = cHJpdmF0ZWtleS1mb3ItdGVzdHMtb25seS0wMDAwMDA=", cfg)
        self.assertIn("Address = 10.61.0.2/32\nDNS = 10.60.0.1", cfg)
        self.assertIn("PublicKey = U0VSVkVSUFVCX19fX19fX19fX19fX19fX19fX19fX18=", cfg)
        self.assertIn("Endpoint = 98.42.17.201:51820", cfg)
        self.assertIn("AllowedIPs = 0.0.0.0/0\nPersistentKeepalive = 25", cfg)
        self.assertIn("<svg", r["qrSvg"])
        self.assertIn("WAN address", r["endpointWarning"])
        self.assertEqual(self.helios("remote-wg-add", "tablet")["ip"], "10.61.0.3")
        self.assertIn("already", self.helios("remote-wg-add", "pixel", ok=False)["error"])
        self.assertIn("name", self.helios("remote-wg-add", "Bad Name", ok=False)["error"])
        self.helios("remote-wg-remove", "pixel")
        self.assertEqual(self.helios("remote-wg-add", "again")["ip"], "10.61.0.2")           # freed address is reused
        self.assertIn("no WireGuard peer", self.helios("remote-wg-remove", "ghost", ok=False)["error"])
        # the private key is only in the command output: not in state, git or the data dir
        blob = ""
        for root in (self.cfg, self.tmp + "/hs", self.data):
            for dp, _, fs in os.walk(root):
                for fn in fs:
                    blob += read_file(os.path.join(dp, fn), errors="ignore")
        self.assertNotIn("cHJpdmF0ZWtleS", blob)
        peers = load_json(os.path.join(self.cfg, "state.json"))["heliosphere"]["ingress"]["wireguard"]["peers"]
        self.assertEqual([x["name"] for x in peers], ["tablet", "again"])
        self.assertTrue(base64.b64decode(peers[0]["publicKey"]).startswith(b"pub-of-"))
        self.assertIn("Remote access: add WireGuard peer tablet", self.helios("pending")["changes"])

    def test_remote_wg_endpoint_uses_public_host(self):
        conf = load_json(self.conf_path)
        conf["heliosphere"]["publicHost"] = "vpn.example.org"
        write_json(self.conf_path, conf)
        os.makedirs(self.hs("wireguard"))
        write_file(self.hs("wireguard", "private"), "priv\n")                 # public derived through `wg pubkey`
        r = self.helios("remote-wg-add", "pixel")
        self.assertIn("Endpoint = vpn.example.org:51820", r["config"])
        self.assertIn("PublicKey = " + base64.b64encode(b"pub-of-priv".ljust(32)).decode(), r["config"])
        self.assertNotIn("endpointWarning", r)

    def test_remote_lan_tailscale_and_wireguard_options(self):
        # LAN access per way in, Tailscale options, WireGuard tunnel/keepalive/MTU
        r = self.helios("remote-set", json.dumps({"ingress": {
            "tailscale": {"exitNode": False, "hostname": "home-exit", "lan": ["family", "iot", "family"]},
            "wireguard": {"tunnel": "split", "lan": ["trusted"], "keepalive": 0, "mtu": 1380},
            "openvpn": {"lan": ["iot"]}}}))
        ig = r["settings"]["ingress"]
        self.assertEqual((ig["tailscale"]["exitNode"], ig["tailscale"]["hostname"], ig["tailscale"]["lan"]), (False, "home-exit", ["family", "iot"]))
        self.assertEqual((ig["wireguard"]["tunnel"], ig["wireguard"]["mtu"], ig["openvpn"]["lan"]), ("split", 1380, ["iot"]))
        self.assertIn("Remote access: change Tailscale settings", self.helios("pending")["changes"])
        for bad, msg in (({"tailscale": {"lan": ["nope"]}}, "unknown network"),
                         ({"tailscale": {"hostname": "Bad Name"}}, "hostname"),
                         ({"tailscale": {"lan": []}}, "exit node or at least one home network"),
                         ({"wireguard": {"tunnel": "half"}}, "full"),
                         ({"wireguard": {"lan": []}}, "at least one home network"),
                         ({"wireguard": {"keepalive": 9999}}, "keepalive"),
                         ({"wireguard": {"mtu": 9000}}, "mtu")):
            self.assertIn(msg, self.helios("remote-set", json.dumps({"ingress": bad}), ok=False)["error"])
        # Split tunnel: only the chosen home networks + the router's DNS; no keepalive line when 0; MTU set
        os.makedirs(self.hs("wireguard"))
        write_file(self.hs("wireguard", "public"), "U0VSVkVSUFVCX19fX19fX19fX19fX19fX19fX19fX18=\n")
        cfg = self.helios("remote-wg-add", "pixel")["config"]
        self.assertIn("AllowedIPs = 10.20.0.0/24, 10.60.0.1/32", cfg)
        self.assertIn("MTU = 1380", cfg)
        self.assertNotIn("PersistentKeepalive", cfg)

    def test_remote_wg_server_key_and_renew(self):
        os.makedirs(self.hs("wireguard"))
        write_file(self.hs("wireguard", "public"), "T0xEUFVCX19fX19fX19fX19fX19fX19fX19fX19fX18=\n")
        old = self.helios("remote-wg-add", "pixel")
        # Renew: same address, new key pair, a change in the pending list; unknown peer refused
        st0 = load_json(os.path.join(self.cfg, "state.json"))
        write_file(os.path.join(self.cfg, "state.json"), json.dumps(st0).replace(
            st0["heliosphere"]["ingress"]["wireguard"]["peers"][0]["publicKey"], "STALE="))
        r = self.helios("remote-wg-renew", "pixel")
        self.assertEqual(r["ip"], old["ip"])
        self.assertIn("PrivateKey = ", r["config"])
        st1 = load_json(os.path.join(self.cfg, "state.json"))
        self.assertNotEqual(st1["heliosphere"]["ingress"]["wireguard"]["peers"][0]["publicKey"], "STALE=")
        self.assertIn("no WireGuard peer", self.helios("remote-wg-renew", "ghost", ok=False)["error"])
        # Rotate: new private + public key files, previous key reported; files private
        r = self.helios("remote-wg-rotate")
        self.assertEqual(r["previousPublicKey"], "T0xEUFVCX19fX19fX19fX19fX19fX19fX19fX19fX18=")
        self.assertEqual(read_file(self.hs("wireguard", "public")).strip(), r["publicKey"])
        self.assertEqual(os.stat(self.hs("wireguard", "private")).st_mode & 0o777, 0o600)
        self.assertEqual(os.stat(self.hs("wireguard", "public")).st_mode & 0o777, 0o600)
        # Import: must be a 32-byte base64 key
        self.assertIn("isn't a WireGuard private key", self.helios("remote-wg-import", stdin="not-a-key", ok=False)["error"])
        key = base64.b64encode(bytes(range(32))).decode()
        self.helios("remote-wg-import", stdin=key + "\n")
        self.assertEqual(read_file(self.hs("wireguard", "private")).strip(), key)

    def test_give_to_only_as_root_and_only_if_the_account_exists(self):
        import importlib.util, types
        import unittest.mock as mock
        spec = importlib.util.spec_from_file_location("helios_ctl_mod", CTL)
        mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
        f = os.path.join(self.tmp, "crl.pem"); open(f, "w").close()
        with mock.patch.object(mod.os, "chown") as chown:
            with mock.patch.object(mod.os, "geteuid", return_value=1000):
                mod.give_to(f, "heliosphere-ovpn")                       # not root: leave it alone
            chown.assert_not_called()
            with mock.patch.object(mod.os, "geteuid", return_value=0), \
                 mock.patch.object(mod.pwd, "getpwnam", side_effect=KeyError("llm")):
                mod.give_to(f, "llm")                                    # service off: no account yet
            chown.assert_not_called()
            with mock.patch.object(mod.os, "geteuid", return_value=0), \
                 mock.patch.object(mod.pwd, "getpwnam", return_value=types.SimpleNamespace(pw_uid=1194, pw_gid=1194)):
                mod.give_to(f, "heliosphere-ovpn")
            chown.assert_called_once_with(f, 1194, 1194)
        # hs_write hands the new file over before it replaces the old one (proxy/users → 3proxy)
        with mock.patch.object(mod, "give_to") as give:
            mod.hs_write(os.path.join(self.tmp, "p", "users"), "tv:CR:x\n", 0o400, 0o700, "heliosphere-proxy")
        self.assertEqual(give.call_args.args[1], "heliosphere-proxy")
        # ...through its descriptor (an int), never a name the container could swap for a link
        self.assertIsInstance(give.call_args.args[0], int)
        self.assertEqual(oct(os.stat(os.path.join(self.tmp, "p", "users")).st_mode & 0o777), "0o400")

    def test_remote_ovpn_add_revoke_with_real_openssl(self):
        if not shutil.which("openssl"):
            self.skipTest("no openssl")
        r = self.helios("remote-ovpn-add", "laptop")
        cfg = r["config"]
        for line in ("client", "dev tun", "proto udp", "remote 98.42.17.201 1194", "remote-cert-tls server",
                     "verify-x509-name heliosphere name", "data-ciphers AES-256-GCM:CHACHA20-POLY1305"):
            self.assertIn(line + "\n", cfg)
        block = lambda tag: cfg.split(f"<{tag}>\n")[1].split(f"\n</{tag}>")[0] + "\n"
        for tag in ("ca", "cert", "key", "tls-crypt"):
            self.assertIn(f"</{tag}>", cfg)
        self.assertIn("WAN address", r["endpointWarning"])
        w = os.path.join(self.tmp, "w")
        os.makedirs(w)
        for tag, fn in (("ca", "ca.crt"), ("cert", "c.crt")):
            write_file(os.path.join(w, fn), block(tag))
        srv = self.hs("openvpn", "server")
        self.assertEqual(sorted(os.listdir(srv)), ["ca.crt", "crl.pem", "server.crt", "server.key", "tls-crypt.key"])
        modes = {fn: oct(os.stat(os.path.join(srv, fn)).st_mode & 0o777) for fn in os.listdir(srv)}
        # Owner-only: OpenVPN reads all but the CRL as root; the CRL (and folder) are its own account's
        self.assertEqual(modes, {"ca.crt": "0o600", "server.crt": "0o600", "crl.pem": "0o400", "server.key": "0o600", "tls-crypt.key": "0o600"})
        self.assertEqual(oct(os.stat(srv).st_mode & 0o777), "0o700")
        self.assertEqual(oct(os.stat(self.hs("openvpn", "ca")).st_mode & 0o777), "0o700")
        self.assertEqual(oct(os.stat(self.hs("openvpn", "ca", "ca.crt")).st_mode & 0o777), "0o600")
        ssl = lambda *a: subprocess.run(["openssl", *a], capture_output=True, text=True)
        self.assertEqual(ssl("verify", "-CAfile", os.path.join(w, "ca.crt"), os.path.join(w, "c.crt")).returncode, 0)
        self.assertEqual(ssl("verify", "-CAfile", os.path.join(w, "ca.crt"), srv + "/server.crt").returncode, 0)
        self.assertIn("CN=heliosphere", ssl("x509", "-noout", "-subject", "-in", srv + "/server.crt").stdout.replace(" ", ""))
        self.assertIn("CN=laptop", ssl("x509", "-noout", "-subject", "-in", os.path.join(w, "c.crt")).stdout.replace(" ", ""))
        txt = ssl("x509", "-noout", "-text", "-in", os.path.join(w, "c.crt")).stdout
        self.assertIn("prime256v1", txt)
        self.assertIn("TLS Web Client Authentication", txt)
        self.assertEqual(ssl("crl", "-in", srv + "/crl.pem", "-noout", "-text").stdout.count("Serial Number"), 0)
        # a second client reuses the PKI; revoking the first lists only its serial in the CRL
        r2 = self.helios("remote-ovpn-add", "phone")
        self.assertNotEqual(r["serial"], r2["serial"])
        self.assertIn("already", self.helios("remote-ovpn-add", "phone", ok=False)["error"])
        self.helios("remote-ovpn-revoke", "laptop")
        self.assertEqual(oct(os.stat(srv + "/crl.pem").st_mode & 0o777), "0o400")
        self.assertNotIn("crl.pem.tmp", os.listdir(srv))
        crl = ssl("crl", "-in", srv + "/crl.pem", "-noout", "-text").stdout
        revoked = set(re.findall(r"Serial Number:\s*([0-9A-Fa-f]+)", crl))   # not a substring search: hex shows up in the signature
        self.assertIn(r["serial"].upper(), {x.upper() for x in revoked})
        self.assertNotIn(r2["serial"].upper(), {x.upper() for x in revoked})
        self.assertNotEqual(ssl("verify", "-crl_check", "-CAfile", os.path.join(w, "ca.crt"), "-CRLfile", srv + "/crl.pem",
                                os.path.join(w, "c.crt")).returncode, 0)
        self.assertIn("no active", self.helios("remote-ovpn-revoke", "laptop", ok=False)["error"])
        clients = load_json(os.path.join(self.cfg, "state.json"))["heliosphere"]["ingress"]["openvpn"]["clients"]
        self.assertEqual([(c["name"], c["revoked"]) for c in clients], [("laptop", True), ("phone", False)])
        self.assertIn("Remote access: revoke OpenVPN client laptop", self.helios("pending")["changes"])
        # client keys are not stored anywhere: only the CA and server keys exist on disk
        keys = [os.path.join(dp, fn) for dp, _, fs in os.walk(self.hs()) for fn in fs if "PRIVATE KEY" in read_file(os.path.join(dp, fn), errors="ignore")]
        self.assertEqual(sorted(os.path.basename(k) for k in keys), ["ca.key", "server.key"])

    def test_remote_proxy_users(self):
        # openssl wrapper that records argv, then runs the real one
        real = shutil.which("openssl")
        if not real:
            self.skipTest("no openssl")
        wrap = os.path.join(self.tmp, "wrap")
        os.makedirs(wrap)
        with open(os.path.join(wrap, "openssl"), "w") as f:
            f.write(f'#!/bin/sh\necho "openssl $*" >> "{self.tmp}/argv.log"\nexec {real} "$@"\n')
        os.chmod(os.path.join(wrap, "openssl"), 0o700)
        self.env["PATH"] = wrap + ":" + self.env["PATH"]
        pw = "correct-horse-battery"
        self.assertIn("16", self.helios("remote-proxy-user", "tv", stdin="short", ok=False)["error"])
        self.helios("remote-proxy-user", "tv", stdin=pw + "\n")
        self.helios("remote-proxy-user", "radio", stdin="another-long-password-1")
        users = read_file(self.hs("proxy", "users")).splitlines()
        self.assertEqual(len(users), 2)
        self.assertRegex(users[0], r"^tv:CR:\$1\$[./0-9A-Za-z]{1,8}\$[./0-9A-Za-z]{22}$")
        self.assertEqual(oct(os.stat(self.hs("proxy", "users")).st_mode & 0o777), "0o400")   # 3proxy's own account reads it
        h = users[0].split(":", 2)[2]
        chk = subprocess.run([real, "passwd", "-1", "-salt", h.split("$")[2], "-stdin"], input=pw, capture_output=True, text=True)
        self.assertEqual(chk.stdout.strip(), h)
        argv = read_file(os.path.join(self.tmp, "argv.log"))
        self.assertIn("openssl passwd -1 -stdin", argv)
        self.assertNotIn(pw, argv)
        self.assertNotIn(pw, json.dumps(load_json(os.path.join(self.cfg, "state.json"))))
        # replacing a password keeps one line per user
        self.helios("remote-proxy-user", "tv", stdin="a-brand-new-password")
        self.assertEqual(len(read_file(self.hs("proxy", "users")).splitlines()), 2)
        self.helios("remote-set", json.dumps({"ingress": {"proxy": {"enabled": True, "networks": ["iot"]}}}))
        self.helios("remote-proxy-user-remove", "tv")
        self.assertEqual([x.split(":")[0] for x in read_file(self.hs("proxy", "users")).splitlines()], ["radio"])
        self.assertIn("at least one user", self.helios("remote-proxy-user-remove", "radio", ok=False)["error"])
        self.assertIn("no proxy user", self.helios("remote-proxy-user-remove", "ghost", ok=False)["error"])

    def test_remote_egress_upload_sanitises(self):
        up = lambda kind, text, ok=True: self.helios("remote-egress-upload", kind, stdin=text, ok=ok)
        r = up("wireguard", WG_OUT)
        self.assertEqual(r["removedDirectives"], ["dns", "postdown", "postup"])
        stored = read_file(self.hs("egress", "wg-out.conf"))
        for gone in ("DNS", "PostUp", "PostDown", "iptables"):
            self.assertNotIn(gone, stored)
        self.assertIn("PrivateKey = abc=", stored)
        self.assertEqual(oct(os.stat(self.hs("egress", "wg-out.conf")).st_mode & 0o777), "0o600")
        for missing in ("PrivateKey", "Address", "PublicKey", "Endpoint", "AllowedIPs"):
            bad = "\n".join(x for x in WG_OUT.splitlines() if not x.startswith(missing))
            self.assertIn(missing.lower(), up("wireguard", bad, ok=False)["error"])
        ovpn = ("client\nremote vpn.example.com 1194\nscript-security 2\nup /etc/up.sh\ndown /etc/down.sh\n"
                "auth-user-pass /home/me/pass.txt\n<ca>\nCERT\nup not-a-directive\n</ca>\n")
        r = up("openvpn", ovpn)
        self.assertEqual(r["removedDirectives"], ["down", "script-security", "up"])
        stored = read_file(self.hs("egress", "client.ovpn"))
        self.assertIn("auth-user-pass /var/lib/heliosphere/egress/auth.txt\n", stored)
        self.assertNotIn("/home/me", stored)
        self.assertNotIn("up.sh", stored)
        self.assertIn("up not-a-directive", stored)                  # inside an inline block: untouched
        self.assertIn("refers to a file", up("openvpn", "client\nremote a 1\nca /etc/ca.crt\n", ok=False)["error"])
        self.assertIn("refers to a file", up("openvpn", "client\nremote a 1\nkey /k.pem\n", ok=False)["error"])
        self.assertIn("client", up("openvpn", "remote a 1\n", ok=False)["error"])
        self.assertIn("remote", up("openvpn", "client\n", ok=False)["error"])
        # Spellings OpenVPN accepts for the same directives
        r = up("openvpn", 'client\nremote a 1\n--script-security 2\n"up" /x\nu\\p /y\nhttp-proxy p 8080 /etc/shadow\n', ok=False)
        self.assertIn("credentials file", r["error"])
        r = up("openvpn", 'client\nremote a 1\n--script-security 2\n"up" /x\nu\\p /y\n')
        self.assertNotIn("/x", read_file(self.hs("egress", "client.ovpn")))
        self.assertEqual(r["removedDirectives"], ["script-security", "up"])
        # wg-quick drops "#…" before reading a line, so a comment can't hide a key or a section
        sneaky = WG_OUT.replace("[Interface]", "[Interface] # hi").replace("PostUp =", "PostUp = x #")
        up("wireguard", sneaky)
        self.assertNotIn("PostUp", read_file(self.hs("egress", "wg-out.conf")))
        self.assertIn("before its [Interface]", up("wireguard", "#\nPostUp = evil\n" + WG_OUT, ok=False)["error"])
        self.assertIn("two lines", up("auth", "just-one", ok=False)["error"])
        up("auth", "alice\nsecret\n")
        self.assertEqual(read_file(self.hs("egress", "auth.txt")), "alice\nsecret\n")
        self.assertEqual(oct(os.stat(self.hs("egress", "auth.txt")).st_mode & 0o777), "0o600")
        up("auth", json.dumps({"user": "bob", "password": "pw"}))
        self.assertEqual(load_json(self.hs("egress", "proxy.json")), {"user": "bob", "password": "pw"})
        self.assertIn("proxy credentials", up("auth", '{"user": "x", "extra": 1}', ok=False)["error"])
        self.assertEqual(sorted(self.helios("remote-egress-clear", "auth")["removed"]), ["auth.txt", "proxy.json"])
        self.assertNotIn("abc=", json.dumps(self.helios("pending")))      # secrets never reach the pending output

    def test_remote_egress_upload_reads_like_openvpn(self):
        """Regressions found by fuzzing (tests/fuzz): the sanitiser must read a file the way OpenVPN does."""
        up = lambda kind, text, ok=True: self.helios("remote-egress-upload", kind, stdin=text, ok=ok)
        stored = lambda: read_file(self.hs("egress", "client.ovpn"))
        head = "client\nremote vpn.example.com 1194\n"
        # OpenVPN ends <ca> at the first line STARTING with </ca>, so "</ca>junk" closed it and "up" ran
        self.assertIn("unexpected", up("openvpn", head + "<ca>\nX\n</ca>junk\nscript-security 2\nup /bin/sh\n</ca>\n", ok=False)["error"])
        r = up("openvpn", head + "<ca>\nX\n  </ca> trailing\nscript-security 2\nup /bin/sh\n")
        self.assertEqual(r["removedDirectives"], ["script-security", "up"])
        self.assertTrue(stored().endswith("<ca>\nX\n</ca>\n"))
        # OpenVPN reads 256-byte lines: the rest of a longer line arrives as a line of its own
        self.assertIn("longer than 255", up("openvpn", head + "<ca>\n" + "A" * 255 + "</ca>\nconfig /etc/x\n</ca>\n", ok=False)["error"])
        self.assertIn("too long once quoted", up("openvpn", head + "verb '" + '"' * 200 + "'\n", ok=False)["error"])
        # <connection> blocks are options, not data; quoting a tag doesn't hide it
        self.assertIn("credentials file", up("openvpn", "client\n<connection>\nremote a 1\nhttp-proxy h 8080 /etc/shadow\n</connection>\n", ok=False)["error"])
        self.assertIn("credentials file", up("openvpn", head + '"<connection>"\nhttp-proxy h 8080 /etc/shadow\n</connection>\n', ok=False)["error"])
        self.assertIn("inside a <connection>", up("openvpn", "client\n<connection>\nremote a 1\n<ca>\n</connection>\n</ca>\n</connection>\n", ok=False)["error"])
        r = up("openvpn", "client\n<connection>\nremote a 1 udp\n--up /x\n</connection>\n")
        self.assertEqual((r["removedDirectives"], stored()), (["up"], "client\n<connection>\nremote a 1 udp\n</connection>\n"))
        self.assertIn("aren't supported", up("openvpn", head + "<up>\n/bin/sh\n</up>\n", ok=False)["error"])
        # More directives that load code or name a file
        for bad in ("iproute /tmp/x", "tmp-dir /etc", "capath /etc", "pkcs11-providers /tmp/p.so", "providers /tmp/evil"):
            self.assertIn("“", up("openvpn", head + bad + "\n", ok=False)["error"], bad)
        self.assertEqual(up("openvpn", head + "engine dynamic\nmanagement-hold\nproviders legacy default\n")["removedDirectives"],
                         ["engine", "management-hold"])
        self.assertIn("providers legacy default\n", stored())
        # What is stored is written back the way OpenVPN would read it
        up("openvpn", head + 'verify-x509-name "C=US, O=Ex" subject # note\n--pull-filter ignore \'route\'\n')
        self.assertIn('verify-x509-name "C=US, O=Ex" subject\npull-filter ignore route\n', stored())
        self.assertIn("not an OpenVPN option", up("openvpn", head + "---up x\n", ok=False)["error"])
        self.assertIn("quotes or backslashes", up("openvpn", head + 'verb "3\n', ok=False)["error"])
        self.assertIn("control character", up("openvpn", head + "verb 3\x0bup /x\n", ok=False)["error"])
        # wg-quick reads a line without "=" as a key too
        r = up("wireguard", WG_OUT.replace("[Peer]", "Table\nPostUp\n[Peer]"))
        self.assertEqual(r["removedDirectives"], ["dns", "postdown", "postup", "table"])
        self.assertNotIn("Table", read_file(self.hs("egress", "wg-out.conf")))
        # Not UTF-8: a clean refusal, not a traceback
        p = subprocess.run([sys.executable, CTL, "remote-egress-upload", "openvpn"], capture_output=True, env=self.env, input=b"client\n\xff\n")
        self.assertIn("not UTF-8", json.loads(p.stdout)["error"])
        self.assertIn("control characters", up("auth", json.dumps({"user": "a", "password": "p\u0000w"}), ok=False)["error"])
        self.assertIn("control character", up("auth", "alice\nse\x7fcret\n", ok=False)["error"])

    def test_remote_status_live_and_failing(self):
        self.helios("remote-set", json.dumps({"ingress": {"wireguard": {"enabled": True}}}))
        os.makedirs(self.hs("wireguard"))
        write_file(self.hs("wireguard", "public"), "U0VSVkVSUFVCX19fX19fX19fX19fX19fX19fX19fX18=\n")
        os.makedirs(self.hs("openvpn", "server"))
        st = load_json(os.path.join(self.cfg, "state.json"))
        st["heliosphere"]["ingress"]["wireguard"]["peers"] = [{"name": "pixel", "publicKey": "clientpub-pixel=", "ip": "10.61.0.2"},
                                                               {"name": "idle", "publicKey": "other=", "ip": "10.61.0.3"}]
        st["heliosphere"]["ingress"]["openvpn"]["clients"] = [{"name": "laptop", "serial": "01", "revoked": False},
                                                               {"name": "phone", "serial": "02", "revoked": False}]
        write_json(os.path.join(self.cfg, "state.json"), st)
        r = self.helios("remote")
        self.assertTrue(r["enabled"])
        self.assertIsNone(r["publicHost"])
        s = r["status"]
        self.assertEqual(s["tailscale"], {"online": True, "ip": "100.64.0.9"})
        self.assertEqual(s["wireguard"]["publicKey"], "U0VSVkVSUFVCX19fX19fX19fX19fX19fX19fX19fX18=")
        self.assertEqual(s["wireguard"]["peers"][0], {"name": "pixel", "ip": "10.61.0.2", "lastHandshake": 1700000000, "rx": 1000, "tx": 2000})
        self.assertEqual(s["wireguard"]["peers"][1]["lastHandshake"], None)
        self.assertEqual(s["openvpn"]["clients"], [{"name": "laptop", "connected": True}, {"name": "phone", "connected": False}])
        self.assertEqual(s["proxy"], {"running": True})
        self.assertEqual(s["egress"], {"mode": "direct", "configured": True, "up": None, "publicIp": None})
        # tools failing: nulls, never an error
        self.env["STUB_HS"] = "fail"
        s = self.helios("remote")["status"]
        self.assertEqual(s["tailscale"], {"online": None, "ip": None})
        self.assertEqual([x["lastHandshake"] for x in s["wireguard"]["peers"]], [None, None])
        self.assertEqual(s["wireguard"]["publicKey"], "U0VSVkVSUFVCX19fX19fX19fX19fX19fX19fX19fX18=")
        self.assertEqual([x["connected"] for x in s["openvpn"]["clients"]], [None, None])
        self.assertEqual(s["proxy"], {"running": None})
        # a way out that's configured but not passing traffic
        self.helios("remote-egress-upload", "wireguard", stdin=WG_OUT)
        self.helios("remote-set", json.dumps({"egress": {"mode": "wireguard"}}))
        self.assertEqual(self.helios("remote")["status"]["egress"], {"mode": "wireguard", "configured": True, "up": False, "publicIp": None})
        del self.env["STUB_HS"]
        self.assertEqual(self.helios("remote")["status"]["egress"], {"mode": "wireguard", "configured": True, "up": True, "publicIp": "203.0.113.9"})

    def test_remote_network_rename_follows_proxy_networks(self):
        self.helios("remote-proxy-user", "tv", stdin="a-long-enough-password")
        self.helios("remote-set", json.dumps({"ingress": {"proxy": {"enabled": True, "networks": ["iot", "family"]}}}))
        iot = next(n for n in self.helios("networks")["networks"] if n["name"] == "iot")
        self.helios("network-set", json.dumps({"from": "iot", "name": "things", "role": iot["role"], "vlan": iot["vlan"], "prefix": iot["prefix"]}))
        self.assertEqual(self.helios("remote")["settings"]["ingress"]["proxy"]["networks"], ["things", "family"])
        self.helios("network-remove", "things")
        self.assertEqual(self.helios("remote")["settings"]["ingress"]["proxy"]["networks"], ["family"])

    # ------------------------------------------------------------ admin account
    @staticmethod
    def totp(secret, at=None):
        key = base64.b32decode(secret + "=" * (-len(secret) % 8))
        mac = hmac.new(key, struct.pack(">Q", int((at or time.time()) // 30)), hashlib.sha1).digest()
        off = mac[-1] & 0x0F
        return "%06d" % ((struct.unpack(">I", mac[off:off + 4])[0] & 0x7FFFFFFF) % 1000000)

    def set_shadow(self, password, changed=20000):
        """A real sha512-crypt hash for admin in the fake /etc/shadow."""
        import ctypes
        import ctypes.util
        lib = ctypes.CDLL(ctypes.util.find_library("crypt"))
        lib.crypt.argtypes = [ctypes.c_char_p, ctypes.c_char_p]
        lib.crypt.restype = ctypes.c_char_p
        h = lib.crypt(password.encode(), b"$6$helios-test$").decode()
        os.makedirs(os.path.join(self.fake, "etc"), exist_ok=True)
        write_file(os.path.join(self.fake, "etc/shadow"), f"root:!:1::::::\nadmin:{h}:{changed}:0:99999:7:::\n")

    def enable_2fa(self):
        secret = self.helios("account-2fa-begin")["secret"]
        codes = self.helios("account-2fa-enable", stdin=self.totp(secret))["recoveryCodes"]
        return secret, codes

    def test_account_status(self):
        self.set_shadow("old-password-123")
        os.makedirs(os.path.join(self.fake, "etc/pam.d"))
        r = self.helios("account")
        self.assertEqual(r["user"], "admin")
        self.assertEqual(r["sshKeys"], 1)
        self.assertEqual(r["passwordChanged"], 20000 * 86400)
        self.assertTrue(r["mutableUsers"])
        self.assertFalse(r["totp"])
        self.assertFalse(r["totpPending"])
        # PAM without the module (an older generation): two-factor isn't enforced
        self.assertFalse(r["totpEnforced"])
        totp_dir = os.path.join(self.data, "totp")
        write_file(os.path.join(self.fake, "etc/pam.d/cockpit"),
                   f"auth required /nix/store/x/lib/security/pam_google_authenticator.so no_increment_hotp nullok secret={totp_dir}/${{USER}} user=root\n")
        self.assertTrue(self.helios("account")["totpEnforced"])

    def test_account_password(self):
        self.set_shadow("old-password-123")
        good = {"current": "old-password-123", "new": "correct-horse-battery"}
        for bad in ({**good, "new": "short"}, {**good, "new": "x" * 129}, {**good, "new": "two\nlines-of-password"},
                    {**good, "current": "wrong-password-1"}, {**good, "current": ""}):
            r = self.helios("account-password", stdin=json.dumps(bad), ok=False)
            self.assertFalse(r["ok"], bad)
        self.assertIn("current password is wrong", self.helios("account-password", stdin=json.dumps({**good, "current": "nope-nope-nope"}), ok=False)["error"])
        self.assertFalse(self.helios("account-password", stdin="correct-horse-battery\n", ok=False)["ok"])   # not JSON
        self.assertNotIn("chpasswd", self.stub_log())
        self.helios("account-password", stdin=json.dumps(good))
        self.assertIn("chpasswd admin:correct-horse-battery", self.stub_log())

    def test_account_password_locked_or_immutable(self):
        os.makedirs(os.path.join(self.fake, "etc"), exist_ok=True)
        write_file(os.path.join(self.fake, "etc/shadow"), "admin:!:20000:0:99999:7:::\n")
        r = self.helios("account-password", stdin=json.dumps({"current": "", "new": "correct-horse-battery"}), ok=False)
        self.assertIn("current password is wrong", r["error"])
        conf = load_json(self.conf_path)
        conf["admin"]["mutableUsers"] = False
        write_json(self.conf_path, conf)
        self.set_shadow("old-password-123")
        r = self.helios("account-password", stdin=json.dumps({"current": "old-password-123", "new": "correct-horse-battery"}), ok=False)
        self.assertIn("mutableUsers", r["error"])
        self.assertFalse(self.helios("account")["mutableUsers"])
        self.assertNotIn("chpasswd", self.stub_log())

    def test_account_2fa(self):
        b = self.helios("account-2fa-begin")
        secret = b["secret"]
        self.assertRegex(secret, r"^[A-Z2-7]{32}$")
        self.assertIn(f"secret={secret}", b["uri"])
        self.assertTrue(b["uri"].startswith("otpauth://totp/HeliOS%20%28helios%29:admin?"))
        self.assertIn("<svg", b["qrSvg"])
        self.assertTrue(self.helios("account")["totpPending"])
        # Nothing is live until a code proves the app has the secret
        self.assertFalse(os.path.exists(os.path.join(self.data, "totp", "admin")))
        wrong = "%06d" % ((int(self.totp(secret)) + 500000) % 1000000)
        self.assertFalse(self.helios("account-2fa-enable", stdin=wrong, ok=False)["ok"])
        self.assertFalse(self.helios("account-2fa-enable", stdin="12ab56", ok=False)["ok"])
        r = self.helios("account-2fa-enable", stdin=self.totp(secret) + "\n")
        self.assertEqual(len(r["recoveryCodes"]), 5)
        for c in r["recoveryCodes"]:
            self.assertRegex(c, r"^[1-9]\d{7}$")   # PAM ignores scratch codes below 10000000
        f = os.path.join(self.data, "totp", "admin")
        self.assertEqual(os.stat(f).st_mode & 0o777, 0o400)
        lines = read_file(f).splitlines()
        self.assertEqual(lines[0], secret)
        self.assertIn('" TOTP_AUTH', lines)
        self.assertEqual(lines[-5:], r["recoveryCodes"])
        self.assertEqual([x for x in os.listdir(os.path.join(self.data, "totp"))], ["admin"])   # no temp files left
        st = self.helios("account")
        self.assertTrue(st["totp"])
        self.assertFalse(st["totpPending"])

    def test_account_2fa_changes_need_a_current_code(self):
        secret, codes = self.enable_2fa()
        f = os.path.join(self.data, "totp", "admin")
        wrong = "%06d" % ((int(self.totp(secret)) + 500000) % 1000000)
        for cmd in ("account-2fa-disable", "account-2fa-begin"):
            for code in ("", wrong, "99999999", "abc"):
                r = self.helios(cmd, stdin=code, ok=False)
                self.assertFalse(r["ok"], (cmd, code))
        self.assertTrue(os.path.exists(f))
        self.assertFalse(self.helios("account")["totpPending"])
        # Moving to a new phone: a current code starts it; the old key stays live until the new one is confirmed
        new = self.helios("account-2fa-begin", stdin=self.totp(secret))["secret"]
        self.assertNotEqual(new, secret)
        self.assertEqual(read_file(f).splitlines()[0], secret)
        self.helios("account-2fa-enable", stdin=self.totp(new))
        self.assertEqual(read_file(f).splitlines()[0], new)

    def test_account_2fa_recovery_code_is_used_up(self):
        secret, codes = self.enable_2fa()
        f = os.path.join(self.data, "totp", "admin")
        self.helios("account-2fa-begin", stdin=codes[2])        # a recovery code works once
        self.assertNotIn(codes[2], read_file(f).splitlines())
        self.assertEqual(os.stat(f).st_mode & 0o777, 0o400)
        self.assertEqual(read_file(f).splitlines()[0], secret)
        self.assertFalse(self.helios("account-2fa-disable", stdin=codes[2], ok=False)["ok"])
        self.helios("account-2fa-disable", stdin=codes[0])
        self.assertFalse(os.path.exists(f))
        self.assertFalse(self.helios("account")["totp"])

    def test_account_2fa_setup_expires(self):
        secret = self.helios("account-2fa-begin")["secret"]
        pending = os.path.join(self.data, "totp", ".pending-admin.json")
        write_json(pending, {"secret": secret, "created": int(time.time()) - 3600})
        r = self.helios("account-2fa-enable", stdin=self.totp(secret), ok=False)
        self.assertIn("expired", r["error"])
        self.assertFalse(os.path.exists(os.path.join(self.data, "totp", "admin")))

    # ------------------------------------------------------------ sign-in gate
    def gate_conf(self, **kw):
        conf = load_json(self.conf_path)
        conf["gate"].update(kw)
        write_json(self.conf_path, conf)

    def add_passkey(self, kid="k1", user="admin"):
        p = self.env["STUB_AUTHELIA_DB"]
        db = load_json(p) if os.path.exists(p) else {"webauthn": [], "totp": []}
        db["webauthn"].append({"username": user, "kid": kid, "description": f"pico-fido {kid}", "created_at": "2026-11-27T10:00:00Z",
                               "last_used_at": None, "discoverable": True, "verified": True})
        write_json(p, db)

    def gate_state(self):
        return self.helios("gate")

    def test_gate_off_service(self):
        self.gate_conf(enabled=False)
        r = self.gate_state()
        self.assertFalse(r["enabled"])
        self.assertEqual(r["passkeys"], [])
        self.assertIn("Services", self.helios("gate-sync", stdin="{}", ok=False)["error"])
        self.assertNotIn("authelia", self.stub_log())

    def test_gate_sync_password_and_keep_in_step(self):
        self.set_shadow("old-password-123")
        self.assertFalse(self.gate_state()["synced"])
        self.assertIn("wrong", self.helios("gate-sync", stdin=json.dumps({"current": "nope-nope-nope"}), ok=False)["error"])
        self.helios("gate-sync", stdin=json.dumps({"current": "old-password-123"}))
        users = read_file(os.path.join(self.tmp, "authelia", "users.yml"))
        self.assertIn("  admin:\n", users)
        h = re.search(r'password: "(\$6\$rounds=100000\$[^"]+)"', users).group(1)
        import ctypes, ctypes.util
        lib = ctypes.CDLL(ctypes.util.find_library("crypt"))
        lib.crypt.argtypes = [ctypes.c_char_p, ctypes.c_char_p]
        lib.crypt.restype = ctypes.c_char_p
        self.assertEqual(lib.crypt(b"old-password-123", h.encode()).decode(), h)
        self.assertEqual(os.stat(os.path.join(self.tmp, "authelia", "users.yml")).st_mode & 0o777, 0o600)
        self.assertTrue(self.gate_state()["synced"])
        # Changing the admin password changes the gate's too
        self.helios("account-password", stdin=json.dumps({"current": "old-password-123", "new": "new-password-456"}))
        h2 = re.search(r'password: "([^"]+)"', read_file(os.path.join(self.tmp, "authelia", "users.yml"))).group(1)
        self.assertEqual(lib.crypt(b"new-password-456", h2.encode()).decode(), h2)

    def test_gate_enforce_needs_password_copy_and_a_factor(self):
        self.set_shadow("old-password-123")
        pw = {"current": "old-password-123", "on": True}
        self.assertIn("Copy the admin password", self.helios("gate-enforce", stdin=json.dumps(pw), ok=False)["error"])
        self.helios("gate-sync", stdin=json.dumps(pw))
        self.assertIn("passkey", self.helios("gate-enforce", stdin=json.dumps(pw), ok=False)["error"])
        self.assertIn("wrong", self.helios("gate-enforce", stdin=json.dumps({**pw, "current": "x"}), ok=False)["error"])
        self.add_passkey()
        # The older code check in Cockpit is retired when the gate takes over
        self.enable_2fa()
        r = self.helios("gate-enforce", stdin=json.dumps(pw))
        self.assertTrue(r["enforced"] and r["retiredCockpitCode"])
        self.assertTrue(os.path.exists(os.path.join(self.tmp, "gate", "enforced")))
        self.assertFalse(os.path.exists(os.path.join(self.data, "totp", "admin")))
        st = self.gate_state()
        self.assertTrue(st["enforced"])
        self.assertEqual([k["kid"] for k in st["passkeys"]], ["k1"])
        self.assertFalse(st["legacyTotp"])
        # Off: with the password, or gate-off from a shell
        self.helios("gate-enforce", stdin=json.dumps({**pw, "on": False}))
        self.assertFalse(self.gate_state()["enforced"])
        self.helios("gate-enforce", stdin=json.dumps(pw))
        self.helios("gate-off")
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "gate", "enforced")))

    def test_gate_last_factor_cant_be_removed_while_enforced(self):
        self.set_shadow("old-password-123")
        pw = {"current": "old-password-123"}
        self.helios("gate-sync", stdin=json.dumps(pw))
        self.add_passkey("k1")
        self.helios("gate-enforce", stdin=json.dumps({**pw, "on": True}))
        self.assertIn("last second factor", self.helios("gate-passkey-remove", stdin=json.dumps({**pw, "kid": "k1"}), ok=False)["error"])
        self.assertIn("No such", self.helios("gate-passkey-remove", stdin=json.dumps({**pw, "kid": "zz"}), ok=False)["error"])
        self.add_passkey("k2")
        self.helios("gate-passkey-remove", stdin=json.dumps({**pw, "kid": "k1"}))
        self.assertIn("authelia storage user webauthn delete --kid k1", self.stub_log())
        self.assertEqual([k["kid"] for k in self.gate_state()["passkeys"]], ["k2"])
        # A code as the fallback; then the passkey can go, but not the code too
        b = self.helios("gate-totp-begin", stdin=json.dumps(pw))
        self.assertTrue(b["uri"].startswith("otpauth://totp/"))
        self.assertEqual(b["secret"], "JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP")
        self.assertFalse(self.helios("gate-totp-check", stdin="000000" if self.totp(b["secret"]) != "000000" else "111111", ok=False)["ok"])
        self.helios("gate-totp-check", stdin=self.totp(b["secret"]))
        self.assertTrue(self.gate_state()["totp"])
        self.helios("gate-passkey-remove", stdin=json.dumps({**pw, "kid": "k2"}))
        self.assertIn("last second factor", self.helios("gate-totp-remove", stdin=json.dumps(pw), ok=False)["error"])
        self.assertIn("wrong", self.helios("gate-totp-begin", stdin=json.dumps({"current": "bad"}), ok=False)["error"])

    def test_gate_code_and_ca(self):
        d = os.path.join(self.tmp, "authelia")
        os.makedirs(d, exist_ok=True)
        # As Authelia 4.39's file notifier writes it (no "Body:" line; digits in the Date line)
        def mail(date, code):
            return (f"Date: {date}\nRecipient: {{admin admin@helios.home.arpa}}\nSubject: Confirm your identity\n"
                    "A ONE-TIME CODE HAS BEEN GENERATED TO COMPLETE A REQUESTED ACTION\n\nHi admin,\n\n"
                    "The following one-time code should only be used in the prompt displayed in your browser.\n\n"
                    f"----------------------------------------\n\n{code}\n\n----------------------------------------\n\n"
                    "To revoke the code, click the link below:\n\n"
                    "https://helios.home.arpa:9090/auth/revoke/one-time-code?id=B9fQOPJhQbyM4QS4hPoPWQ\n\n"
                    "Powered by Authelia https://www.authelia.com\n\n")
        write_file(os.path.join(d, "notifications.txt"),
                   mail("2026-11-27 10:00:00.84246168 +0000 UTC m=+10.021033679", "OLDC0DE1")
                   + mail("2026-11-27 10:05:00.545209096 +0000 UTC m=+310.5", "ZW4ZRAEN"))
        r = self.helios("gate-code")
        self.assertEqual(r["code"], "ZW4ZRAEN")
        self.assertEqual(r["sent"], "2026-11-27 10:05:00.545209096 +0000 UTC m=+310.5")
        self.assertIn("isn't made yet", self.helios("gate-ca", ok=False)["error"])
        write_file(os.path.join(d, "root.crt"), "-----BEGIN CERTIFICATE-----\nAAAA\n-----END CERTIFICATE-----\n")
        r = self.helios("gate-ca")
        self.assertIn("BEGIN CERTIFICATE", r["pem"])
        self.assertTrue(r["filename"].endswith(".crt"))


    def test_gate_own_domain_token_and_certificate(self):
        self.set_shadow("old-password-123")
        pw = {"current": "old-password-123"}
        fake = "fake-dns-token-for-tests-only-000000"   # not a real token format, so secret scanning leaves it alone
        self.assertIn("helios.gate.domain", self.helios("gate-dns-token", stdin=json.dumps({**pw, "token": fake}), ok=False)["error"])
        self.assertEqual(self.gate_state()["certificate"]["issuer"], "router")
        self.gate_conf(ownDomain="helios.example.com", dnsProvider="desec")
        cert = self.gate_state()["certificate"]
        self.assertEqual((cert["domain"], cert["provider"], cert["tokenSet"], cert["issuer"]), ("helios.example.com", "desec", False, "router"))
        self.assertIn("wrong", self.helios("gate-dns-token", stdin=json.dumps({"current": "x", "token": fake}), ok=False)["error"])
        for bad in ("short", "has space in it 1234567", "semi;colon-0123456789abcdef"):
            self.assertFalse(self.helios("gate-dns-token", stdin=json.dumps({**pw, "token": bad}), ok=False)["ok"], bad)
        os.makedirs(os.path.join(self.tmp, "gate"), exist_ok=True)
        self.helios("gate-dns-token", stdin=json.dumps({**pw, "token": fake}))
        cred = os.path.join(self.tmp, "gate", "dns-token.cred")
        self.assertEqual(read_file(cred), f"ENC(helios-dns-token):{fake}")   # only ever handed to systemd-creds
        self.assertEqual(os.stat(cred).st_mode & 0o777, 0o400)
        self.assertNotIn(fake, self.stub_log())                                # never on a command line
        self.assertEqual(os.readlink(os.path.join(self.tmp, "gate", "tls.caddy")), os.path.join(self.tmp, "authelia", "tls-acme.caddy"))
        self.assertTrue(self.gate_state()["certificate"]["tokenSet"])
        r = subprocess.run([sys.executable, CTL, "gate"], capture_output=True, text=True, env=self.env)
        self.assertNotIn(fake, r.stdout + r.stderr)   # the token is never echoed back
        # A Let's Encrypt certificate Caddy got (made here with openssl)
        le = os.path.join(self.tmp, "authelia", "le.crt")
        os.makedirs(os.path.dirname(le), exist_ok=True)
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", os.devnull, "-out", le,
                        "-days", "60", "-subj", "/CN=helios.example.com"], check=True, capture_output=True)
        cert = self.gate_state()["certificate"]
        self.assertEqual(cert["issuer"], "letsencrypt")
        self.assertGreater(cert["expires"], time.time() + 50 * 86400)
        # Clearing the token
        self.helios("gate-dns-token", stdin=json.dumps({**pw, "token": ""}))
        self.assertEqual(read_file(cred), "ENC(helios-dns-token):")
        self.assertEqual(os.readlink(os.path.join(self.tmp, "gate", "tls.caddy")), os.path.join(self.tmp, "authelia", "tls-internal.caddy"))
        self.assertFalse(self.gate_state()["certificate"]["tokenSet"])


    def test_authelia_export_parser(self):
        import importlib.util
        saved = dict(os.environ)
        os.environ["HELIOS_CONFIG"] = self.conf_path
        try:
            spec = importlib.util.spec_from_file_location("helios_ctl_mod", CTL)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
        finally:
            os.environ.clear()
            os.environ.update(saved)
        sample = "\n".join([
            "# yaml-language-server: $schema=https://www.authelia.com/schemas/v4.39/json-schema/exports.webauthn.json",
            "webauthn_credentials:",
            "  - id: 1",
            "    created_at: 2026-11-27T10:00:00Z",
            "    last_used_at: null",
            "    username: admin",
            "    description: 'pico-fido ''spare'' key'",
            '    kid: "AbC_d-1"',
            "    transports:",
            "      - usb",
            "      - nfc",
            "    discoverable: true",
            "    verified: false",
            "  - id: 2",
            "    username: other",
            "    description: Pixel",
            "    kid: Zz9",
        ]) + "\n"
        rows = mod.export_rows(sample)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["username"], "admin")
        self.assertEqual(rows[0]["description"], "pico-fido 'spare' key")
        self.assertEqual(rows[0]["kid"], "AbC_d-1")
        self.assertIsNone(rows[0]["last_used_at"])
        self.assertIs(rows[0]["discoverable"], True)
        self.assertIs(rows[0]["verified"], False)
        self.assertNotIn("transports", rows[0])   # nested values are skipped
        self.assertEqual((rows[1]["username"], rows[1]["kid"]), ("other", "Zz9"))
        self.assertEqual(mod.export_rows("totp_configurations: []\n"), [])


    def test_account_needs_admin_user(self):
        conf = load_json(self.conf_path)
        conf["admin"] = {"user": "../etc"}
        write_json(self.conf_path, conf)
        self.assertFalse(self.helios("account", ok=False)["ok"])

    def load_module(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("helios_ctl_sec", CTL)
        mod = importlib.util.module_from_spec(spec)
        os.environ.update({k: v for k, v in self.env.items() if k.startswith(("HELIOS_", "STUB_")) or k == "PATH"})
        spec.loader.exec_module(mod)
        return mod

    def test_security_write_json_never_follows_a_planted_link(self):
        # A service account's folder (helios-detect's StateDirectory) holding a
        # "<name>.tmp" link to a file root must not overwrite
        mod = self.load_module()
        victim = os.path.join(self.tmp, "victim")
        with open(victim, "w") as f:
            f.write("precious\n")
        folder = os.path.join(self.tmp, "svc")
        os.makedirs(folder)
        target = os.path.join(folder, "hardware.json")
        os.symlink(victim, target + ".tmp")
        mod.write_json(target, {"ok": 1})
        self.assertEqual(read_file(victim), "precious\n")
        self.assertEqual(json.loads(read_file(target)), {"ok": 1})
        self.assertFalse(os.path.islink(target))
        # A link at the final name is replaced, never written through
        os.unlink(target)
        os.symlink(victim, target)
        mod.write_json(target, {"ok": 2})
        self.assertEqual(read_file(victim), "precious\n")
        self.assertFalse(os.path.islink(target))
        self.assertEqual([n for n in os.listdir(folder) if n.startswith(".helios-")], [])

    def test_security_private_files_get_their_owner_by_descriptor(self):
        # write_private / hs_write must hand the file over through its descriptor:
        # chown by name would follow a link the folder's owner swapped in
        mod = self.load_module()
        seen = []
        mod.give_to = lambda target, user: seen.append(target)
        p = os.path.join(self.tmp, "gate", "users.yml")
        mod.write_private(p, "users: {}\n", 0o600, owner="authelia-helios")
        self.assertEqual(len(seen), 1)
        self.assertIsInstance(seen[0], int, "write_private chowned by path")
        self.assertEqual(read_file(p), "users: {}\n")
        seen.clear()
        mod.hs_write(os.path.join(self.tmp, "hs", "users"), "u:CR:x\n", 0o400, owner="heliosphere-proxy")
        self.assertEqual(len(seen), 1)
        self.assertIsInstance(seen[0], int, "hs_write chowned by path")

    def test_security_argument_injection(self):
        # restore: only a commit id reaches git (not "--output=/etc/x")
        for rev in ("--output=/tmp/x", "-p", "HEAD~1", "abc def", ""):
            self.assertIn("bad revision", self.helios("restore", "--", rev, ok=False)["error"])
        # Printer names and job ids never start with "-" (an option to lp / cancel)
        self.assertIn("bad job id", self.helios("print-cancel", "--", "-hevil-3", ok=False)["error"])
        mod = self.load_module()
        self.assertIsNone(mod.PRINTER_NAME_RE.match("-hevil"))
        self.assertIsNotNone(mod.PRINTER_NAME_RE.match("office_laser-2"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
