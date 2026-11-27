# One Wi-Fi name, a password per network

You can have a single Wi-Fi name where **the password decides the network**. Join with the admin password and you're on the admin network. Join with the everyday password and you're on a network without admin access. Access points call this **PPSK** (per-device or "private" pre-shared keys). Ruckus calls it **DPSK**.

HeliOS doesn't configure access points. You set this up on the access point, and HeliOS does the rest. Admin access in HeliOS comes from the **network** a device is on, never from its MAC address or name. A device that joined with the everyday password can't become admin, whatever it claims to be.

## How it maps onto HeliOS

With the default networks (Photosphere → [Networks](Page-Networks)):

| Password | VLAN | Network | What the device gets |
|---|---|---|---|
| Admin password | 20 | trusted | Manage the router (Photosphere, SSH), reach every network, internet |
| Everyday password | 30 | family | Internet, filtered DNS; no router admin pages |
| Gadgets password (optional) | 40 | iot | Internet only; no other networks |

Use your own networks if they differ. What makes a network an admin network is its **admin** access flag on the Networks page.

The admin network only gets you as far as the sign-in page. Photosphere still asks for the admin password, plus a passkey or code if [Passkey Sign-In](Passkey-Sign-In) is on. A leaked admin Wi-Fi password exposes the sign-in page, not the router.

## What the router side needs

Nothing new. The access point's switch port must be **tagged** with every VLAN its passwords use (20, 30 and 40 in the table above). See [Installation → Set up the switch and access points](Installation#set-up-the-switch-and-access-points). On the [Hardware](Page-Hardware) page you can list the access point's Wi-Fi network once per VLAN to keep its checklist accurate.

## Setting it up on the access point

Menu names change between firmware versions; look for "PPSK", "Private Pre-Shared Keys", "DPSK" or "per-key VLAN".

**UniFi**
1. Create a Wi-Fi network with **WPA2** security.
2. Turn on **Private Pre-Shared Keys**.
3. Add one key per network, each with its VLAN/network (admin key → VLAN 20, everyday key → VLAN 30, and so on).

**TP-Link Omada**
1. Create a **PPSK** profile (the variant without RADIUS is enough at home).
2. Add one password per network, each with its VLAN ID.
3. Attach the profile to the SSID.

**OpenWrt** (needs the full `wpad` package, not `wpad-basic`). In `/etc/config/wireless`, on a `psk2` network:

```
config wifi-vlan
	option name 'trusted'
	option network 'trusted'   # an interface bridged to VLAN 20
	option vid '20'

config wifi-vlan
	option name 'family'
	option network 'family'    # bridged to VLAN 30
	option vid '30'

config wifi-station
	option key 'the-admin-password'
	option vid '20'

config wifi-station
	option key 'the-everyday-password'
	option vid '30'
```

Recent OpenWrt releases can do the same with WPA3 (SAE). Check the documentation for your release.

**No PPSK on your access point?** Use **separate Wi-Fi names** instead, such as "Home" (everyday, VLAN 30) and "Home-Admin" (VLAN 20), each with its own password. Every VLAN-aware access point can do this, and HeliOS treats it the same way. Hiding the admin network's name adds nothing: hidden names are easy to find.

## Check that it works

1. Join with the everyday password. In Photosphere → [Devices](Page-Devices), the device shows on the everyday network. From it, `https://<router>:9090` and SSH must **not** answer: Heliopause drops them and logs a trap warning.
2. Join with the admin password (forget the network on that device first). It shows on the admin network, and the sign-in page answers.

## Good to know

- **WPA2 only on many access points.** Most vendors' PPSK only works with WPA2 security. If you want WPA3, use separate Wi-Fi names, or check whether your access point supports per-password VLANs with SAE.
- **The admin password is a key.** Everyone who knows it is on the admin network. Give it only to devices you manage the router from. Change it if one of them is lost: on the access point, then on your other admin devices.
- **One password per device is stronger still.** Most PPSK setups let you add several keys for the same VLAN. Give each admin device its own key, and you can revoke one device without re-keying the rest.
- **Changing a password's network takes effect immediately on HeliOS's side.** When a network loses a permission, Heliopause also cuts connections that are already open. The device itself still has to rejoin to move to a new VLAN.
- **Private (random) MAC addresses** don't matter here: the password decides the network. HeliOS may still list the device once per Wi-Fi network (see [Devices](Page-Devices)).
- **Enterprise Wi-Fi (802.1X, a sign-in or certificate per device)** is the strongest option, but it needs a RADIUS server. HeliOS doesn't run one yet. See "per-device Wi-Fi VLANs (RADIUS)" in `docs/drivers.md`.
