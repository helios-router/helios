# Passkey sign-in

An optional sign-in page in front of Photosphere (and the rest of Cockpit) that asks for a **second factor** on top of the admin password. **Passkeys are HeliOS's recommended second factor**; a 6-digit code from an authenticator app works as a fallback.

This page explains how it works, walks through the setup, and covers what to do when something goes wrong. The short version of each step is also on the Passkey sign-in card in Photosphere → Advanced.

## Why passkeys

A passkey here is a FIDO2 credential on a security key (YubiKey, pico-fido, Nitrokey, SoloKey…) or on your phone. Compared with a code:

| | Passkey (recommended) | Code from an app (fallback) |
|---|---|---|
| What you do | PIN and a touch at the sign-in page, then the admin password in Cockpit | Admin password and the code at the sign-in page, then the admin password again in Cockpit |
| Admin password typed | once | twice |
| Phishing | can't be phished: the key only answers the router's real sign-in page | a fake page can ask for the code and use it within 30 seconds |
| Needs a trusted certificate | yes (see [step 2](#2-a-certificate-your-browsers-trust)) | no |

The key's PIN is checked on the key itself; the router never sees it. The sign-in page requires it (`user_verification: required`), so a stolen key alone isn't enough.

## How it fits together

```
browser ──https──▶ Caddy :9090 ──▶ Authelia (the sign-in page, /auth)
                          │
                          └─ signed in ──▶ Cockpit (localhost only) ──▶ admin password ──▶ Photosphere
```

* **Off by default.** Without it, Cockpit serves port 9090 itself, exactly as before.
* **Turning it on** (Advanced → Services → Passkey sign-in, or `helios.gate.enable = true;`) downloads Authelia and Caddy at the next Apply. Cockpit then listens on the router itself only, and Caddy serves 9090.
* **Nothing changes for sign-in until you finish the setup and press "Require it at sign-in".** Until then Cockpit is reached as before, so turning it on can't lock you out.
* **The rescue port** (if you have one) always skips the sign-in page: admin password only. It's recognised by the client's address, which the firewall only accepts on the rescue port.
* SSH (keys only), `sudo` and the console are not affected.

## Before you start

* A FIDO2 key **with a PIN set** (set one with the key's own tool, or your browser offers to when you register), or a phone that does passkeys. Register two if you can: a spare key, or key + phone.
* About 10 minutes.
* Photosphere's gear (advanced mode) on: Advanced is where the card lives.

## Setup

### 1. Turn it on

Advanced → **Passkey sign-in** card → **Turn on passkey sign-in** (or the switch in the Services card), then **Apply changes**. This needs internet: it downloads Authelia (about 50 MB). The installer offers to turn it on for you when it has internet.

### 2. A certificate your browsers trust

Browsers only allow passkeys on a page whose certificate they trust. Chrome, Edge and Brave refuse passkeys after you click through a certificate warning, and so do Firefox (since version 140) and, by all accounts, Safari. Pick one:

#### A. A name of your own (recommended: nothing to install on any device)

The router gets a free [Let's Encrypt](https://letsencrypt.org/) certificate for a name you control. It proves the name by writing a temporary DNS record through your DNS provider's API, so **nothing on the router is exposed to the internet**, and renews it by itself (certificates last 90 days today, getting shorter from 2027; renewal handles that). The router answers that name on your own networks.

| You have | Use |
|---|---|
| A domain whose DNS is on Cloudflare | `dnsProvider = "cloudflare"`, a name like `helios.example.com` |
| No domain | a free name from [deSEC](https://desec.io), a non-profit DNS host: `yourname.dedyn.io` |
| A domain at deSEC | `dnsProvider = "desec"` |

In `/etc/nixos/site.nix`:

```nix
helios.gate = {
  enable = true;
  domain = "helios.example.com";          # or "yourname.dedyn.io"
  acme.dnsProvider = "cloudflare";        # or "desec"
  # acme.email = "you@example.com";       # optional: Let's Encrypt expiry mails
};
```

Rebuild (`sudo nixos-rebuild switch`, or commit and Apply). Then make an API token:

**Cloudflare.** Dashboard → My Profile → API Tokens → **Create Token** → **Create Custom Token**:
* Permissions: **Zone · DNS · Edit** and **Zone · Zone · Read**.
* Zone Resources: **Include · Specific zone ·** your domain.
* Create it and copy the token (Cloudflare shows it once). Cloudflare can't narrow a token further than one zone.

**deSEC.** Register at desec.io (and, for a free name, create `yourname.dedyn.io` there). Then **Token management** → create a token and copy it. It's safer to restrict the token to the one record type the router needs, with token policies (deSEC's API; replace `TOKEN_ID`, your login token and the domain):

```bash
# 1. a default policy: no writing anything...
curl -X POST https://desec.io/api/v1/auth/tokens/TOKEN_ID/policies/rrsets/ \
  -H "Authorization: Token YOUR_LOGIN_TOKEN" -H "Content-Type: application/json" \
  -d '{"domain": null, "subname": null, "type": null, "perm_write": false}'
# 2. ...except the TXT record Let's Encrypt checks
curl -X POST https://desec.io/api/v1/auth/tokens/TOKEN_ID/policies/rrsets/ \
  -H "Authorization: Token YOUR_LOGIN_TOKEN" -H "Content-Type: application/json" \
  -d '{"domain": "yourname.dedyn.io", "subname": "_acme-challenge", "type": "TXT", "perm_write": true}'
```

(If your sign-in name is a subname, like `helios.yourname.dedyn.io`, the subname is `_acme-challenge.helios`.)

Back in Photosphere, the certificate step now says **Set Cloudflare token** / **Set deSEC token**: paste the token and the admin password.

* The token is encrypted on the router with `systemd-creds` and handed to Caddy as a credential. It's never stored in the clear, never in git or backups, and never shown again.
* If Caddy won't accept the token (a typo, the wrong kind of token), HeliOS puts the router's own certificate back straight away and says so; nothing is left broken.
* The certificate usually arrives within a minute with Cloudflare, about 4 minutes with deSEC (its nameservers take a while to agree; HeliOS waits for them). The step turns green and shows the expiry date.
* Until then, and if renewal ever keeps failing (a long internet outage, a revoked token), the router serves **its own certificate** instead. Devices that installed it (option B) keep working with passkeys; others see a warning and can't use passkeys until Let's Encrypt works again, but the code fallback works on them. The card warns you when this happens. Keeping option B on one or two devices is a good backup.
* Your passkeys are tied to this name. Changing it later means registering them again.
* Over Tailscale, use the same name: in your tailnet's DNS settings, add split DNS for your domain pointing at the router.
* The name appears in public certificate logs (all Let's Encrypt certificates do). That shows the name exists, not your address.

#### B. The router's own certificate (no internet needed)

The certificate step's **Download certificate** gives the router's own certificate authority. Install it **once on each device** you sign in from, as a trusted authority:

| Device | How |
|---|---|
| Windows | Open the `.crt` → Install Certificate → Local Machine → "Trusted Root Certification Authorities". |
| macOS | Open it (Keychain Access, System keychain) → double-click it → Trust → "Always Trust". |
| Linux | `sudo trust anchor helios-*.crt` (or your distribution's CA tool). |
| Firefox (any OS) | Firefox has its own list: Settings → Privacy & Security → Certificates → View Certificates → Authorities → Import → trust for websites. |
| iPhone / iPad | Open the file → Settings → "Profile Downloaded" → Install. **Then** Settings → General → About → Certificate Trust Settings → turn on full trust for it. Without this last step it doesn't count. |
| Android | Settings → Security → Encryption & credentials → Install a certificate → CA certificate. Android needs a screen lock for this. |

Then sign in at `https://<hostname>.<domain>:9090` (e.g. `https://helios.home.arpa:9090`).

### 3. Copy the admin password to the sign-in page

The sign-in page uses the same admin password as Cockpit. **Copy password** (asks for the current one) puts it there. Changing the password later in the Admin account card changes both.

### 4. Add a passkey

**Add passkey** opens a dialog with these steps (keep it open):

1. Open the sign-in page (link in the dialog) and sign in with your admin login name and the admin password.
2. **Settings** (top right) → **Two-Factor Authentication** → under **WebAuthn Credentials** (Authelia's name for passkeys) → **Add**, and give it a name like "pico-fido".
3. The sign-in page asks for a **one-time code** to confirm it's you. Press **Show latest code** in the Photosphere dialog and type it in. (Authelia would normally e-mail it; on HeliOS it's written to a file on the router, which Photosphere reads.) Codes last 5 minutes.
4. Your browser asks for the key: plug it in, tap it, or pick your phone. Enter the key's **PIN**, then **touch** it.
5. Press **Done** in Photosphere. The passkey is listed with when it was last used.

Repeat for a second key or your phone.

**pico-fido and other DIY keys** work: HeliOS doesn't restrict keys to certified models. The passkey is stored on the key ("discoverable"), which is what lets you sign in without typing a username; keys hold a limited number of these, but one per router is fine.

### 5. A code as fallback (optional)

**Set up code** shows a QR code for an authenticator app (Aegis, 2FAS, a password manager, or Yubico Authenticator, which keeps it on a YubiKey). Enter a code to check the app has it. With a code you type the admin password twice (at the sign-in page and in Cockpit).

### 6. Try it

Open the sign-in page in a private window → **Sign in with a passkey** → PIN and touch. You should land on a signed-in page. If you don't, see [troubleshooting](#troubleshooting) before going on.

### 7. Require it

**Require it at sign-in** (asks for the admin password). From now on:

* Go to `https://<your sign-in name>:9090`. Other names and addresses redirect there.
* Sign in with the passkey, then Cockpit asks for the admin password. That's it.
* No passkey to hand: on the sign-in page use your password and a code instead.
* If Cockpit itself was asking for a code (the older setting in the Admin account card), that's retired now; the sign-in page asks instead.

**Stop requiring** turns it off again (passkeys and code stay registered).

## Daily use and changes

* Remove a passkey (lost key): Advanced → Passkey sign-in → **Remove**. The last second factor can't be removed while sign-in is required.
* New phone for the code: **Remove code**, then **Set up code** again.
* Every change asks for the admin password, so an unlocked browser can't change your sign-in.

## Locked out

In order of convenience:

1. **Code fallback** on the sign-in page, if you set one up.
2. **Rescue port**: plug a laptop into it (address `.2` on its subnet) and open `https://<rescue address>:9090`. Admin password only.
3. **SSH or the console**: `sudo helios-ctl gate-off`. Sign-in goes back to the admin password only (passkeys stay registered; require it again when you're sorted).
4. **The installer's Recovery shell**: `helios-mount`, `nixos-enter --root /mnt`, `helios-ctl gate-off`.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| "Passkeys aren't available" / the browser doesn't offer the key | The page's certificate isn't trusted on this device: do [step 2](#2-a-certificate-your-browsers-trust). With your own name, check the certificate step is green. |
| Certificate warning on the sign-in page | Same. With your own name: the step's text says whether the router is waiting for Let's Encrypt or using its own certificate. |
| Token refused ("Caddy wouldn't start with that token…") | It wasn't the right kind of token: Cloudflare needs an API **token** (not the global API key) with the two permissions above; deSEC needs a token from Token management. The router's own certificate is back in use meanwhile. |
| Certificate step stays on "Waiting for Let's Encrypt" | Check the router has internet. deSEC takes about 4 minutes. With Cloudflare, check the token's zone is the one your name is in. `journalctl -u caddy` on the router says what Let's Encrypt answered. |
| No one-time code when adding a passkey | Ask for a new code on the sign-in page, then **Show latest code** again. |
| "Sign in with a passkey" doesn't find the key | The passkey wasn't stored on the key: remove it and add it again. Some keys need their PIN set first. |
| Redirected to a name that doesn't resolve | Your device isn't using the router's DNS (VPN, Private DNS on Android, a manual DNS server). Use the router's DNS, or the rescue port. |

## Under the hood

* `modules/gate.nix`: Caddy, Authelia and the switch between certificates. Authelia's rule is `two_factor` for the sign-in name; a passkey that checks its PIN satisfies it on its own (Authelia's `experimental_enable_passkey_uv_two_factors`). Authelia plans to replace that option; HeliOS pins Authelia and its VM test starts it with this setting, so an update that drops it is caught before it reaches a router.
* Authelia comes from stable nixpkgs unless stable lacks a security fix, then from HeliOS's pinned `nixpkgs-unstable` input (`autheliaSecure` in the module names the oldest release with every advisory fixed). See [Updates and Security](Updates-and-Security).
* `pkgs/caddy-dns.nix`: Caddy with the Cloudflare or deSEC DNS plugin, pinned by version and hash.
* `helios-ctl gate*` commands: what Photosphere calls; see [Development](Development).
* Files: `/var/lib/authelia-helios/` (Authelia's database, its copy of the password hash, its secrets), `/var/lib/helios-gate/` (the "required" flag, the encrypted DNS token, which certificate config Caddy uses). All root- or service-only.
