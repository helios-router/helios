# The sign-in gate with a real passkey: a client on the trusted VLAN runs headless
# Chromium (Playwright) with Chromium's virtual authenticator (WebAuthn over CDP),
# trusts the router's own CA (no certificate errors: Chromium refuses WebAuthn
# then), and through Authelia's API, as its sign-in page uses it:
#  - registers a passkey (password, then elevation with the emailed code);
#  - helios-ctl lists it (Authelia's YAML export) and can then require the gate;
#  - with the gate required, no session = no Cockpit;
#  - the passkey alone, with user verification, gives two-factor and reaches
#    Cockpit (experimental_enable_passkey_uv_two_factors);
#  - without user verification it doesn't;
#  - the last passkey can't be removed while the gate is required; gate-off lets
#    the browser straight in.
# The browser runs as its own process (passkey.py) and meets this script at files
# in /tmp/pk, where the router has to act in between.
{ self }:

let
  common = import ./common.nix;
  password = "passkey-test-pass-1";   # test value only
in
{
  name = "helios-passkey";

  nodes = {
    isp = common.isp;
    client = { pkgs, ... }: {
      imports = [ (common.client 20) ];
      virtualisation.memorySize = 2048;
      environment.systemPackages = [ pkgs.nss.tools ];
      # The router's DNS (AdGuard) only answers once its setup wizard is done
      # (wiki/Installation.md, first boot), so the name comes from here: the
      # router's trusted-network address, where the gate's own /etc/hosts entry points
      networking.hosts."10.20.0.1" = [ "helios.example.test" ];
      # passkey.py; the test starts it once the router is ready
      systemd.services.passkey-browser = {
        environment = { HOME = "/root"; PLAYWRIGHT_BROWSERS_PATH = "${pkgs.playwright-driver.browsers}"; };
        serviceConfig.ExecStart = "${pkgs.python3.withPackages (ps: [ ps.playwright ])}/bin/python3 ${./passkey.py} ${password}";
      };
    };

    router = { lib, nodes, ... }: {
      imports = [ (common.router self) ];
      helios.gate.enable = lib.mkOverride 40 true;
      helios.gate.domain = "helios.example.test";
      helios.state = lib.mkOverride 40 (lib.recursiveUpdate
        (builtins.fromJSON (builtins.readFile ../../templates/state.json))
        {
          # Named, so it isn't quarantined as an unknown device (those only reach the internet)
          devices = [ {
            mac = common.nodeMac lib nodes.client.virtualisation.test.nodeNumber;
            name = "laptop"; vlan = "trusted"; profile = "default";
          } ];
        });
    };
  };

  testScript = common.leaseHelper + ''
      import json

      fqdn = "helios.example.test"
      ca = "/var/lib/caddy/.local/share/caddy/pki/authorities/local/root.crt"

      def ctl(cmd, stdin=""):
          # helios-ctl exits 1 when it refuses (JSON "ok": false), so read the answer either way
          status, output = router.execute(f"printf '%s' '{stdin}' | helios-ctl {cmd}")
          res = json.loads(output)
          assert (status == 0) == bool(res.get("ok")), (cmd, status, output)
          return res

      def dump():
          print(client.execute("journalctl -u passkey-browser --no-pager -o cat | tail -n 80; ls -la /tmp/pk")[1])
          print(router.execute("journalctl -u authelia-helios -u caddy --no-pager -o cat | tail -n 60; tail -n 40 /var/lib/authelia-helios/notifications.txt")[1])

      def step(name, timeout=180):
          # Wait for passkey.py to finish a step and return what it saw
          try:
              client.wait_until_succeeds(f"test -e /tmp/pk/{name}.json || test -e /tmp/pk/error.json", timeout=timeout)
          except Exception:
              dump()
              raise
          if client.execute("test -e /tmp/pk/error.json")[0] == 0:
              dump()
              raise Exception("passkey.py failed: " + client.succeed("cat /tmp/pk/error.json"))
          res = json.loads(client.succeed(f"cat /tmp/pk/{name}.json"))
          print(name, json.dumps(res, indent=1))
          return res

      start_all()
      router.wait_for_unit("multi-user.target")
      router.wait_for_unit("authelia-helios.service")
      router.wait_for_unit("caddy.service")
      lease(client, "vlan20", "10.20.0")

      with subtest("the client finds the sign-in page by name and trusts the router's CA"):
          # The same address the router gives its own name (its /etc/hosts)
          assert "10.20.0.1" in router.succeed(f"getent hosts {fqdn}"), "the gate's name isn't the trusted gateway"
          assert "10.20.0.1" in client.succeed(f"getent hosts {fqdn}")
          # Caddy makes its CA with the first certificate
          router.wait_until_succeeds(f"curl -sk --resolve {fqdn}:9090:127.0.0.1 -o /dev/null https://{fqdn}:9090/auth/ && test -s {ca}", timeout=60)
          pem = ctl("gate-ca")["pem"]
          assert pem.strip() == router.succeed(f"cat {ca}").strip(), "gate-ca isn't Caddy's root"
          client.succeed(f"cat > /tmp/helios-ca.crt <<'EOF'\n{pem.strip()}\nEOF")
          client.succeed("mkdir -p /root/.pki/nssdb && certutil -N -d sql:/root/.pki/nssdb --empty-password")
          client.succeed("certutil -d sql:/root/.pki/nssdb -A -t 'C,,' -n helios -i /tmp/helios-ca.crt")
          # The real path: curl checks the certificate against the CA too
          client.wait_until_succeeds(f"curl -fsS --cacert /tmp/helios-ca.crt -o /tmp/auth.html https://{fqdn}:9090/auth/", timeout=60)

      with subtest("password to the gate, then the browser registers a passkey"):
          router.succeed("echo 'admin:${password}' | chpasswd")
          assert ctl("gate-sync", json.dumps({"current": "${password}"}))["ok"]
          client.succeed("mkdir -p /tmp/pk")
          client.succeed("systemctl start --no-block passkey-browser")
          r = step("loaded")
          assert r["status"] == 200 and r["secure"], r
          r = step("need-code")
          assert r["firstfactor"]["json"]["status"] == "OK", r
          assert r["elevation"]["status"] == 200, r
          code = ctl("gate-code")["code"]
          assert code, "Authelia sent no one-time code"
          client.succeed(f"echo '{code}' > /tmp/pk/code")
          r = step("registered")
          assert r["elevate"]["json"]["status"] == "OK", r
          assert r["register"] and r["register"]["json"]["status"] == "OK", r
          assert r["onAuthenticator"] == 1 and r["resident"] == [True], r

      with subtest("helios-ctl lists it (Authelia's export) and can require the gate"):
          g = ctl("gate")
          assert g["error"] is None, g
          assert len(g["passkeys"]) == 1, g
          key = g["passkeys"][0]
          assert key["description"] == "virtual" and key["kid"], key
          assert key["discoverable"], key
          assert ctl("gate-enforce", json.dumps({"current": "${password}", "on": True}))["enforced"]
          client.succeed("touch /tmp/pk/go-enforced")

      with subtest("no session: no Cockpit"):
          r = step("no-session")
          assert r["url"].startswith(f"https://{fqdn}:9090/auth") and not r["cockpit"], r

      with subtest("the passkey alone (user verified) is two-factor and reaches Cockpit"):
          r = step("passkey")
          assert r["ceremony"], r
          assert r["signin"]["json"]["status"] == "OK", r
          assert r["level"] == 2, f"a user-verified passkey gave authentication level {r['level']}, not 2 (two-factor)"
          assert r["after"]["cockpit"], r

      with subtest("without user verification: not two-factor, no Cockpit"):
          r = step("no-uv")
          for case in ("asked", "tampered"):
              assert r[case]["level"] != 2 and not r[case]["after"]["cockpit"], (case, r[case])
          # The tampered case must have got an assertion (UV=0) to Authelia, or it checks nothing
          assert r["tampered"]["ceremony"], r["tampered"]

      with subtest("the last passkey stays while the gate is required; gate-off lets everyone in"):
          r = ctl("gate-passkey-remove", json.dumps({"current": "${password}", "kid": key["kid"]}))
          assert not r["ok"] and "last second factor" in r["error"], r
          assert len(ctl("gate")["passkeys"]) == 1
          assert ctl("gate-off")["ok"]
          client.succeed("touch /tmp/pk/go-off")
          r = step("off")
          assert r["cockpit"], r
    '';
}
