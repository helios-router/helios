# Runs on the client (headless Chromium through Playwright) for tests/vm/passkey.nix.
# A real WebAuthn ceremony against the sign-in gate, with Chromium's virtual
# authenticator. The test script on the driver talks to this one through files in
# /tmp/pk: this script writes <step>.json when a step is done, and waits for the
# files the driver writes (the elevation code, "go" markers) where the router has
# to do something in between.
import json
import os
import sys
import time
import traceback

from playwright.sync_api import sync_playwright

BASE = "https://helios.example.test:9090"
DIR = "/tmp/pk"
PASSWORD = sys.argv[1]


def done(step, data):
    tmp = f"{DIR}/{step}.json.tmp"
    with open(tmp, "w") as f:
        json.dump(data, f)
    os.replace(tmp, f"{DIR}/{step}.json")
    print(f"step {step}: {json.dumps(data)[:400]}", flush=True)


def wait_for(name, timeout=300):
    path = f"{DIR}/{name}"
    end = time.time() + timeout
    while not os.path.exists(path):
        if time.time() > end:
            raise TimeoutError(f"no {path} from the test driver")
        time.sleep(0.5)
    with open(path) as f:
        return f.read().strip()


API = """async ([method, path, body]) => {
  const opts = { method, credentials: "same-origin", headers: {} };
  if (body !== null) { opts.headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(body); }
  const r = await fetch(path, opts);
  const text = await r.text();
  let json = null;
  try { json = JSON.parse(text); } catch (e) {}
  return { status: r.status, json, text: json ? null : text.slice(0, 400) };
}"""

REGISTER = """async (pk) => {
  const cred = await navigator.credentials.create({ publicKey: PublicKeyCredential.parseCreationOptionsFromJSON(pk) });
  return cred.toJSON();
}"""

# uv: null keeps the request as Authelia sent it; a string overrides userVerification
# (a tampered request, to see what Authelia does with an assertion that has UV=0).
# allow: a credential ID (base64) to name in allowCredentials: authenticators want
# user verification for a discoverable (empty allowCredentials) request
ASSERT = """async ([pk, uv, allow]) => {
  const opts = PublicKeyCredential.parseRequestOptionsFromJSON(pk);
  if (uv !== null) opts.userVerification = uv;
  if (allow !== null) opts.allowCredentials = [{ type: "public-key", id: Uint8Array.from(atob(allow), c => c.charCodeAt(0)) }];
  try {
    const cred = await navigator.credentials.get({ publicKey: opts });
    return { ok: true, cred: cred.toJSON() };
  } catch (e) {
    return { ok: false, error: `${e.name}: ${e.message}` };
  }
}"""


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context()
        page = context.new_page()

        def api(method, path, body=None):
            return page.evaluate(API, [method, path, body])

        def level():
            r = api("GET", "/auth/api/state")
            return (r.get("json") or {}).get("data", {}).get("authentication_level"), r

        def visit(path):
            # Where the browser ends up, and whether that's Cockpit's login page
            resp = page.goto(BASE + path)
            body = page.content().lower()
            return {"url": page.url, "status": resp.status if resp else None,
                    "cockpit": "cockpit" in body and not page.url.startswith(BASE + "/auth")}

        def passkey_signin(uv=None, allow=None):
            opts = api("GET", "/auth/api/firstfactor/passkey")
            pk = ((opts.get("json") or {}).get("data") or {}).get("publicKey")
            if not pk:
                return {"options": opts}
            got = page.evaluate(ASSERT, [pk, uv, allow])
            res = {"ceremony": got.get("ok"), "error": got.get("error")}
            if got.get("ok"):
                res["signin"] = api("POST", "/auth/api/firstfactor/passkey",
                                    {"response": got["cred"], "keepMeLoggedIn": False, "targetURL": BASE + "/"})
            res["level"], _ = level()
            return res

        # The page's certificate must be trusted (the router's CA in NSS), or WebAuthn is refused
        resp = page.goto(BASE + "/auth/")
        done("loaded", {"url": page.url, "status": resp.status if resp else None, "secure": page.evaluate("window.isSecureContext")})

        cdp = context.new_cdp_session(page)
        cdp.send("WebAuthn.enable", {"enableUI": False})
        auth_id = cdp.send("WebAuthn.addVirtualAuthenticator", {"options": {
            "protocol": "ctap2", "ctap2Version": "ctap2_1", "transport": "usb",
            "hasResidentKey": True, "hasUserVerification": True, "isUserVerified": True,
            "automaticPresenceSimulation": True}})["authenticatorId"]

        # 1. Password, then elevation with the code Authelia "sends", then register
        ff = api("POST", "/auth/api/firstfactor", {"username": "admin", "password": PASSWORD, "keepMeLoggedIn": False})
        elev = api("POST", "/auth/api/user/session/elevation")
        done("need-code", {"firstfactor": ff, "elevation": elev})
        code = wait_for("code")
        elev2 = api("PUT", "/auth/api/user/session/elevation", {"otc": code})
        opts = api("PUT", "/auth/api/secondfactor/webauthn/credential/register", {"description": "virtual"})
        pk = ((opts.get("json") or {}).get("data") or {}).get("publicKey")
        reg = None
        if pk:
            cred = page.evaluate(REGISTER, pk)
            reg = api("POST", "/auth/api/secondfactor/webauthn/credential/register", cred)
        creds = cdp.send("WebAuthn.getCredentials", {"authenticatorId": auth_id})["credentials"]
        done("registered", {"elevate": elev2, "options": opts if not pk else "ok", "register": reg,
                            "onAuthenticator": len(creds), "resident": [c.get("isResidentCredential") for c in creds]})

        # 2. The gate is required now: a fresh browser session must not reach Cockpit
        wait_for("go-enforced")
        context.clear_cookies()
        done("no-session", visit("/"))

        # 3. Passkey alone (it verified the user): two-factor, and Cockpit
        res = passkey_signin()
        res["after"] = visit("/")
        done("passkey", res)

        # 4. Without user verification. As Authelia asks (UV required), Chromium itself
        #    refuses. So the same credential (its key copied over CDP) goes on a second
        #    authenticator that can't verify the user, and the request is changed to
        #    "discouraged": an assertion with UV=0 then reaches Authelia, which must not
        #    count it as two-factor
        cdp.send("WebAuthn.setUserVerified", {"authenticatorId": auth_id, "isUserVerified": False})
        context.clear_cookies()
        page.goto(BASE + "/auth/")
        noUv = passkey_signin()
        noUv["after"] = visit("/")
        cred = cdp.send("WebAuthn.getCredentials", {"authenticatorId": auth_id})["credentials"][0]
        cdp.send("WebAuthn.removeVirtualAuthenticator", {"authenticatorId": auth_id})
        no_uv_id = cdp.send("WebAuthn.addVirtualAuthenticator", {"options": {
            "protocol": "ctap2", "ctap2Version": "ctap2_1", "transport": "usb",
            "hasResidentKey": True, "hasUserVerification": False,
            "automaticPresenceSimulation": True}})["authenticatorId"]
        cdp.send("WebAuthn.addCredential", {"authenticatorId": no_uv_id, "credential": cred})
        context.clear_cookies()
        page.goto(BASE + "/auth/")
        tampered = passkey_signin(uv="discouraged", allow=cred["credentialId"])
        tampered["after"] = visit("/")
        done("no-uv", {"asked": noUv, "tampered": tampered})

        # 5. gate-off: straight in, no session
        wait_for("go-off")
        context.clear_cookies()
        done("off", visit("/"))
        browser.close()


try:
    os.makedirs(DIR, exist_ok=True)
    main()
except Exception:
    done("error", {"traceback": traceback.format_exc()})
    raise
