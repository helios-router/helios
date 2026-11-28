# HeliOS security audit baseline

This file is the documented security baseline for HeliOS. It records each security boundary, what has been checked, every finding with its severity and status, and the test that keeps each fix from regressing.

**Use it when you change something security-relevant.** Find the boundary your change touches (below). Re-check the findings and the "verified by design" decisions listed for it. Run the tests named there. If your change adds, fixes or reopens a finding, update this file in the same pull request.

To report a vulnerability, see [SECURITY.md](SECURITY.md). Don't open a public issue.

| | |
|---|---|
| Last full audit | 2026-11 (PR #14); remote access re-audited 2026-11 (HA-23 to HA-26); connection revocation, ICMP errors and authorization 2026-11 (HA-27 to HA-31) |
| Scope | Every module in `modules/`, `pkgs/helios-ctl.py`, `pkgs/photosphere/`, `pkgs/logwatch.sh`, `installer/`, `flake.nix` / `flake.lock`, `.github/` |
| Method | Static review of the generated configuration and execution paths (not the comments), plus runtime tests where possible: real kernel conntrack and nftables in network namespaces, NixOS VM tests, helios-ctl unit tests |
| Not covered | Behaviour on physical hardware (see [Manual verification](#manual-verification)), third-party firmware on switches and access points, the security of Tailscale, Authelia, Caddy, Cockpit and nixpkgs themselves |

Line numbers are as of the commit that last edited this file. Each fix also names a function or rule you can search for, in case the lines have moved.

## Severity scale

| Severity | Meaning |
|---|---|
| Critical | An attacker on the internet, or on any network without admin rights, gets root or admin access, or defeats isolation between networks, without user interaction |
| High | Isolation or authentication can be defeated by a device on the LAN, or root can be obtained from a service account |
| Medium | A partial bypass, or root-level damage that needs an already compromised service account or an unusual configuration |
| Low | Needs admin rights or physical access, or the impact is limited (e.g. availability, alert spoofing) |
| Info | Hardening, or a documented trade-off |

## Security boundaries

| Boundary | Where it is enforced | Trusted side |
|---|---|---|
| Internet ↔ home | Heliopause `input`/`forward` (`modules/heliopause.nix`), NAT in `heliopause-nat` | Nothing from the WAN, except DHCPv6 replies, Tailscale UDP 41641, and DNAT'd port forwards |
| Network ↔ network (VLANs) | Heliopause `forward`: access flags from `pkgs/networks.json`, `reach` rules, source-address checks | Admin networks (`admin` flag), `tailscale0` |
| Network → router | Heliopause `input`: trap ports, per-flag services | Admin networks, `rescue0`, `tailscale0` |
| Unnamed devices | `quarantine_in` / `quarantine_fwd`, matched by MAC | Named devices (by MAC; see HA-14) |
| Remote access | Container `heliosphere` (its own nftables) **and**, independently, the host's Heliopause (`ve-heliosphere`: one source address, IPv4 only, union of allowed networks) | Neither: the host firewall assumes the container may be compromised |
| Photosphere → root | Cockpit `superuser: "require"` → sudo (wheel only, `execWheelOnly`) → `helios-ctl` | Members of wheel who have signed in |
| Sign-in gate | Caddy (`modules/gate.nix`) → Authelia forward-auth → Cockpit on localhost | Caddy's source-address match for the rescue subnet |
| Service accounts → root | Files that root writes or reads in folders that service accounts own (helios-ctl) | root only |
| Pull requests → `main` | PR guard (run from the base branch), CodeQL, dependency review, CODEOWNERS | The owner's review |

## Findings

Status: **Fixed** = fixed and covered by a regression test. **Open** = known and not fixed. **Accepted** = a deliberate trade-off, documented here.

| ID | Sev | Finding | Status |
|---|---|---|---|
| HA-01 | High | Source checks ran after connection tracking, so spoofed packets rode other networks' open connections | Fixed |
| HA-02 | High | No IPv6 source-address checks on networks without admin rights | Fixed |
| HA-03 | High | Removing a permission didn't affect connections that were already open | Fixed |
| HA-04 | High | The Authelia account could make root chown any file | Fixed |
| HA-05 | Medium | `write_json` followed a link planted by a service account | Fixed |
| HA-06 | Medium | Local accounts could bypass the sign-in gate (and fake the rescue port) | Fixed |
| HA-07 | Medium | Any WAN DNAT was trusted | Fixed |
| HA-08 | Medium | The PR guard skipped files with quoted names | Fixed |
| HA-09 | Low | `restore` passed the revision to git unchecked | Fixed |
| HA-10 | Low | Printer names and job ids could start with `-` | Fixed |
| HA-11 | Low | Pausing the Log AI didn't stop it | Fixed |
| HA-12 | Low–Medium | Installer: disk choice has weak safeguards | Open |
| HA-13 | Medium (trust) | Routers update from the unpinned `main` branch | Open |
| HA-14 | Low | Known MACs count as known on every quarantine network | Open |
| HA-15 | Low | Log text from network devices can forge or hide alerts | Open |
| HA-16 | Low | Root reads UPS and vulnix files that service accounts control | Open |
| HA-17 | Low | Smaller helios-ctl validation gaps | Open |
| HA-18 | Low | Installer answers go into `site.nix` unvalidated | Open |
| HA-19 | Info | Heliosphere: per-way-in network limits only inside the container; egress DNS leaves the router's WAN directly | Accepted / Open |
| HA-20 | Info | `tailscale0` is fully trusted | Accepted |
| HA-21 | Info | MAC addresses can be copied | Accepted |
| HA-22 | Info | Weaker CI and release hygiene items | Open |
| HA-23 | Medium | Remote-access clients' IPv6 got round the container's IPv4-only filtering | Fixed |
| HA-24 | Info | Container accepted open connections before the kill switch (not reproducible: masquerade already stops them) | Hardened |
| HA-25 | Low | Per-way-in home-network revocation relied on the container restarting | Fixed |
| HA-26 | Info (functional) | Proxy egress sent replies for WireGuard / OpenVPN clients back into the tunnel | Fixed |
| HA-27 | Medium | Open connections kept flowing the other way after a permission was taken away | Fixed (silent devices: see text) |
| HA-28 | Medium | Forged ICMP errors crossed between networks | Fixed |
| HA-29 | Info | OpenVPN's CRL was chowned by a fixed name in OpenVPN's folder | Fixed (hardening) |
| HA-30 | Low (functional) | `helios-ctl reboot` claimed success when the reboot was refused | Fixed |
| HA-31 | Info | Remote-access clients can reach each other through the container | Open (a decision) |
| HA-32 | Info (functional) | WireGuard / OpenVPN ways in got no answers with a VPN way out | Fixed |
| HA-33 | Info (functional) | The proxy way out never worked (host name mangled, tunnel never set up) | Fixed |

### HA-01: Source checks ran after connection tracking (High, Fixed)

- **What:** `ct state established accept` came before the per-network source-address check. Conntrack matches a packet by addresses and ports, not by the interface it arrived on. A device on a network without admin rights could send packets carrying another network's address and ports. Those packets matched that network's open connection, were accepted as "established", and were NATed out (or delivered to the router).
- **Attacker:** any device on a network without admin rights. UDP flows (DNS, QUIC, games, VoIP) are open to it. TCP flows need the right sequence numbers.
- **Fix:** source-address checks now come before conntrack, in `forward` (`modules/heliopause.nix:185-186`) and `input` (`:132-137`). The input chain exempts `0.0.0.0` for DHCP requests.
- **Tests:**
  - `tests/netns/conntrack.py` (checks 1 and 5) runs against the real kernel and shows the old ordering vulnerable and the new one fixed.
  - `tests/vm/router.nix`, subtest "anti-spoofing comes before connection tracking".

### HA-02: No IPv6 source-address checks (High, Fixed)

- **What:** the check was IPv4-only (`ip saddr != <subnet>`). Over IPv6, a device on a network without admin rights could send from any address.
- **Fix:** `fib saddr . iif oif missing drop` (strict reverse-path check against the routing table) in `forward` (`:185`) and `input` (`:137`, ICMPv6 exempt for neighbour discovery). Prefixes delegated by the ISP keep working, because the routing table is the source of truth.
- **Test:** `tests/netns/conntrack.py`, check 2.
- **Still to verify:** a real ISP prefix delegation, including a prefix change (see Manual verification).

### HA-03: Removing a permission didn't affect open connections (High, Fixed)

- **What:** every established packet was accepted up front. Taking away `admin`, `reach`, `internet`, quarantine status or a port forward left connections that were already open working indefinitely. That included SSH and Cockpit sessions from a network that just lost admin access.
- **Fix:** only reply-direction packets are accepted up front (`ct direction reply ct state established accept`, `:143`, `:196`). The side that opened a connection goes through the rules on every packet. NAT state is kept, so legitimate connections survive a rebuild.
- **Tests:**
  - `tests/netns/conntrack.py`, checks 3 and 4, including "trusted's open internet connection survives a reload".
  - `tests/vm/router.nix`, subtest "taking a permission away cuts connections that are already open".
- **Remaining:** a UDP flow that only sends replies (the other side keeps sending after a single request) lives until conntrack times it out. When miniupnpd removes a mapping, no new connections can use it. Connections already open through it keep their DNAT status and still go to a `upnp` network, so they also last until conntrack times them out. Deleting the matching conntrack entries when a mapping is removed would close this.

### HA-04: The Authelia account could make root chown any file (High, Fixed)

- **What:**
  - `write_private` created its temp file in `/var/lib/authelia-helios`, a 0700 folder owned by `authelia-helios`. It then called `chown` on the temp file's name, and `chown` follows symlinks. Between the write and the chown, that account could replace the name with a symlink to `/etc/shadow` and gain root.
  - The trigger is an admin changing the password or running `gate-sync`.
  - `hs_write` had the same pattern. It wasn't exploitable there, because the container mounts those folders read-only.
- **Fix:** `give_to` accepts a file descriptor. `write_private` (`pkgs/helios-ctl.py:4381`) and `hs_write` (`:3491`) chown through it before the file is published.
- **Tests:** `test_security_private_files_get_their_owner_by_descriptor` and `test_give_to_only_as_root_and_only_if_the_account_exists` (both in `tests/test_helios_ctl.py`).

### HA-05: `write_json` followed a planted link (Medium, Fixed)

- **What:** `write_json` wrote through a fixed `<name>.tmp` with a plain `open()`. The hardware-detection file lives in the `helios-detect` DynamicUser's state folder. Code running as that account could plant `hardware.json.tmp` as a symlink, and the next "Scan again" would make root overwrite any file.
- **Fix:** a fresh `mkstemp` file, then an atomic replace (`write_json`, `pkgs/helios-ctl.py:226`).
- **Test:** `test_security_write_json_never_follows_a_planted_link`.

### HA-06: Local accounts could bypass the sign-in gate (Medium, Fixed)

- **What:** with the gate on, Cockpit listens on `127.0.0.1:9089` and trusts `X-Forwarded-For`. Any local account (for example the Log AI's `llm` account) could connect directly. That skipped Authelia (passkey, code, rate limiting). Claiming a rescue-subnet address in the header also skipped the TOTP code, through the `pam_access` rescue rule in `modules/airlock.nix`.
- **Fix:** an nftables `output` chain lets only Caddy and root connect to that port (`modules/heliopause.nix:168`). Caddy's uid is numeric, because the ruleset is checked at build time.
- **Test:** `tests/vm/hardening.nix` (`nobody` refused; `caddy` and root allowed).
- **Better long term:** move Cockpit to a unix socket that only Caddy can open.

### HA-07: Any WAN DNAT was trusted (Medium, Fixed)

- **What:** `iifname "wan" ct status dnat accept` accepted every DNAT'd connection. A stray DNAT rule, from a miniupnpd bug or a hand edit, could expose an admin network to the internet.
- **Fix:** accepted only towards `upnp` networks, and towards Heliosphere's address for its VPN ports (`:217-219`).
- **Test:** `tests/vm/router.nix` (miniupnpd still starts). There's no WAN-side mapping test yet; see Manual verification.

### HA-08: The PR guard skipped files with quoted names (Medium, Fixed)

- **What:** git writes a quoted `+++ "b/…"` header for file names with non-ASCII or special characters. The guard only parsed `+++ b/`, so it skipped every line rule for those files, and its binary check passed them as empty text. This was reproduced: a `fetchurl` and a pipe-to-shell in `modules/évil.nix` produced only "unusual file type".
- **Fix:** `core.quotepath=off`. File names outside `[A-Za-z0-9._/@+-]` are blocked (`.github/scripts/pr-guard.py:108`), and so is any diff header the guard can't parse (`:134`).
- **Test:** none automated yet. To check by hand, run the guard on a scratch repo containing such a file. The guard runs from the base branch, so the fix applies to pull requests opened after it merged.

### HA-09: `restore` revision passed to git unchecked (Low, Fixed)

- **What:** a revision such as `--output=/etc/x` reached `git show` as an option.
- **Who:** an admin only.
- **Fix:** the revision must be hex, 4–64 characters (`pkgs/helios-ctl.py:891`).
- **Test:** `test_security_argument_injection`.

### HA-10: Printer names and job ids could start with `-` (Low, Fixed)

- **What:** `lp`, `lpadmin`, `cupsdisable` and `cancel` read a leading `-` as an option.
- **Who:** an admin only.
- **Fix:** `PRINTER_NAME_RE` (`:1640`) and the job-id check (`:1813`) require a first character other than `-`.
- **Test:** `test_security_argument_injection`.

### HA-11: Pausing the Log AI didn't stop it (Low, Fixed)

- **What:** the pause check (`ExecCondition`) ran as the service's unprivileged account. That account couldn't read the root-only pause file, so it treated the AI as not paused.
- **Fix:** `+${ctl} logwatch-guard --check` (`modules/discovery.nix`).
- **Test:** none automated; check it in a VM or on the router.

### HA-12: Installer disk safeguards (Low–Medium, Open)

The live USB is listed as a target. Disks are picked by kernel name (`sda`), which can change between boots. A partition name is accepted as a disk.

- **Existing safeguard:** typing the disk name to confirm.
- **Recommended:** refuse disks with mounted partitions, validate the name against the list, and show the model, size and `/dev/disk/by-id` in the confirmation.
- **Where:** `installer/helios-install.sh`.

### HA-13: Updates follow unpinned `main` (Medium trust issue, Open)

`templates/flake.nix` and the installer point at the default branch. A compromised `main` means root on every router that updates. After an update, Nix pins the revision and hash.

- **Recommended:** release tags, with signed-tag verification documented.

### HA-14: Known MACs count on every quarantine network (Low, Open)

`known_macs` is one set for all networks. A MAC named on one network counts as known on another. Copying it gets a device past quarantine there.

- **Recommended:** a set of `iifname . ether_addr` pairs.
- **Related:** HA-21, and the Wi-Fi Passwords and VLANs page in `wiki/`.

### HA-15: Log text from network devices can forge or hide alerts (Low, Open)

Strings that devices control, such as DHCP hostnames and SSH user names, reach the journal. From there the Log AI (`pkgs/logwatch.sh`):

- **Forged alerts:** text that matches a critical pattern, or contains embedded newlines, raises an alert.
- **Hidden lines:** the noise patterns aren't anchored, so a line containing their text is dropped.
- **Unanchored "seen" marking:** the hash is recorded before the model's verdict, so a steered "no alert" hides that pattern for 7 days.

The model can't run commands: its output is constrained to JSON and is only ever used as notification text.

Related hardening:
- The ntfy token is passed on curl's command line, where other local users can read it.
- The logwatch unit has a weaker sandbox than the services in `rosetta.nix`.
- With `backend = remote`, there's no `https://` check on the URL.

### HA-16: Root reads files service accounts control (Low, Open)

`ups-forward` reads `/run/helios-ups/*.json`, a folder the `nut` account owns. `vulnix-report` reads a DynamicUser's `report.json`. Both follow symlinks and have no size limit, so a compromised service account can make root hang (FIFO) or use up memory (`/dev/zero`).

- **Recommended:** open with `O_NOFOLLOW|O_NONBLOCK`, require a regular file under 1 MB, and strip CR/LF from notification titles.

### HA-17: Smaller helios-ctl validation gaps (Low, Open)

- **Device IP:** `cmd_device_set` validates the address only by accident. It allows `.0`, `.1` (the gateway) and `.255`.
- **`gate-totp-begin --force`:** replaces the live TOTP before the new code is confirmed, so an admin can lock themselves out of the gate. The rescue port and `gate-off` still work.
- **Reused codes:** `check_current_code` accepts a code already used to sign in.
- **No `.gitignore` in configDir:** any secret dropped into `/etc/nixos` is committed, pushed by the nightly backup, and copied into the Nix store.

### HA-18: Installer answers go into `site.nix` unvalidated (Low, Open)

The domain, SSH key and WAN VLAN are spliced into `site.nix` with `sed`, escaping only `/ & |`. Only the person at the console (already root) can exploit this, so it's a correctness problem. Validate each answer with a regex.

### HA-19: Heliosphere containment (Info)

- **Accepted:** the host sees every remote client as `10.60.0.2`. It enforces only the union of the networks the ways in allow. The per-way-in split (Tailscale vs WireGuard vs OpenVPN) is enforced only inside the container. If the container is compromised, its clients reach that union, and nothing more: IPv4 only, no router services except DNS.
- **Open:** in VPN egress mode, clients' DNS still resolves through the router's AdGuard over the router's own WAN, not through the tunnel. That's a privacy leak, if hiding traffic from the local ISP is the point of the egress VPN. It is also why DNS keeps working when the tunnel is down. Fixing it means AdGuard sending 10.60.0.2's questions to a resolver inside the container that only uses the tunnel. That's a design change, not done yet.
- **Since HA-25:** the container re-checks open connections itself, so the per-way-in split holds for connections that are already open too.

### HA-20: `tailscale0` is fully trusted (Info, Accepted)

Heliopause accepts all input and forward traffic from `tailscale0`. The tailnet policy is the only gate. `templates/tailnet-policy.hujson` restricts it correctly, but Tailscale's default policy lets every node in, and the README warns about this. `--accept-routes` is off.

- **Re-check:** whenever `modules/tailscale.nix` or the template changes.
- **Since 2026-11:** the installer explains this and asks for an explicit yes before turning Tailscale on (`installer/helios-install.sh`). The admin model itself is unchanged.

### HA-21: MAC addresses can be copied (Info, Accepted)

Quarantine and pausing a device match by MAC. A device can copy a known MAC, or invent a new one, which counts as unknown and is quarantined once "restrict unnamed devices" is on. Admin rights don't depend on MACs: they come from the network, i.e. the VLAN.

- **Mitigation:** per-password VLANs on the access point (PPSK), 802.1X, or switch port security. See `wiki/Wi-Fi-Passwords-and-VLANs.md`.

### HA-22: CI and release hygiene (Info, Open)

- The ISO is published without a checksum, signature or build attestation.
- `playwright install --with-deps` and `pipx run zizmor` aren't hash-pinned.
- The wiki workflow puts its token in the clone URL. The runner is ephemeral.

### HA-23: Remote-access clients' IPv6 got round the container's filtering (Medium, Fixed)

- **What:** the container forwarded IPv6, and nothing in it filtered IPv6. Tailscale's routing features turn on IPv6 forwarding. The DNS redirect (`dnat ip to`), the DoH and WARP sets and the kill switch (`ip daddr != host`) only match IPv4. With an egress tunnel that carries IPv6 (many VPN providers' WireGuard configs have `::/0`), clients' IPv6 left through the tunnel unfiltered: DNS to any IPv6 resolver and DoH to IPv6 addresses got round Magnetosphere. The host can't see inside the tunnel, so its own IPv6 drop for the container doesn't help there. The container's own IPv6, such as the proxy connecting to an AAAA address, could do the same.
- **Attacker:** a remote-access client (for example a Tailscale exit-node user) who wants unfiltered DNS. It needs an egress tunnel with IPv6.
- **Fix:** the container refuses to forward IPv6 (`meta nfproto ipv6 reject`, the first forward rule in `modules/heliosphere.nix`), and refuses its own IPv6 on eth0 and the tunnel (output chain). Clients fall back to IPv4.
- **Tests:** `tests/netns/heliosphere.py` ("client IPv6 forwarded", "client DNS over IPv6", for each way out); `tests/vm/hardening.nix` (IPv6 refused first in the real ruleset).

### HA-24: Container accepted open connections before the kill switch (Info, not reproducible, hardened)

- **Suspected:** the container's forward chain accepted every established packet first, so the kill switch only ever saw new connections. If the tunnel's routes went away while its device stayed up (for example an OpenVPN reconnect with `persist-tun`), open client connections might follow the main table's default route out of eth0.
- **Measured:** it doesn't happen. `tests/netns/heliosphere.py` runs the old ordering with each way out (WireGuard, OpenVPN, proxy) and both failure modes (routes gone, device gone). No open or new client connection left the container's eth0. A control in the same run, with the kill switch off, does show traffic on eth0 and on the router's WAN, so the counters would have seen a leak.
- **Why (our reading of the kernel):** all client traffic through the tunnel is masqueraded on the tunnel interface, either by the container's postrouting rule or by Tailscale's own. Linux kills a masqueraded connection and drops the packet when that connection's output interface changes (`nf_nat_oif_changed`), and it discards masquerade state when the device goes down. The next packet is then a new connection, and the kill switch drops it.
- **Hardening kept:** only reply-direction packets are accepted up front, and the side that opened a connection goes through the kill switch on every packet. The kill switch then holds without relying on masquerade, for example if a tunnel used SNAT or no NAT. Separately, the host only accepts 10.60.0.2 from the container, and tunnel-NATed packets don't have that address.
- **Tests:** `tests/netns/heliosphere.py` (for each way out and failure mode: open TCP and UDP connections, new connections, DNS during the outage, reaching the host, recovery, plus the control). `tests/vm/hardening.nix` asserts the order in the real ruleset.

### HA-25: Per-way-in revocation relied on the container restarting (Low, Fixed)

- **What:** the same "established first" ordering meant that removing a home network from one way in (say WireGuard) left that way's open connections to it running, as far as the container's rules went. The host couldn't step in when another way in (say Tailscale) still had that network, because the host only sees the container as a whole. In practice an Apply restarts the container: nixpkgs' `restartIfChanged` defaults to true, and the container's system is a restart trigger. A restart empties its connection table, so this was latent.
- **Fix:** the same re-check as HA-24, so revocation doesn't depend on a restart.
- **Tests:** `tests/netns/heliosphere.py` (each way in has its access taken away while another way in keeps it: the open connection is cut, no new ones, the other way in is unaffected).

### HA-26: Proxy egress misrouted client replies (Info, functional, Fixed)

- **What:** tun2socks' routing sent everything without fwmark 0x2 to table 100 (the tunnel), with no main-table rule before it. Replies to WireGuard and OpenVPN clients (10.61/10.62) went back into the proxy instead of to the client. Tailscale clients were unaffected, because Tailscale's own routing rules come first.
- **Fix:** `lookup main suppress_prefixlength 0` at priority 190, as wg-quick does. This only lets specific main-table routes win, never the default route, so it doesn't weaken the kill switch.
- **Test:** `tests/netns/heliosphere.py` prints what the old routing does ("INFO: proxy egress with tun2socks' old routing"), and its proxy-egress checks run with the fix.

### HA-27: Open connections kept flowing the other way after a permission was taken away (Medium, Fixed)

- **What:** since HA-03, the side that opened a connection is checked against the rules on every packet, but replies were accepted up front (`ct direction reply ct state established accept`, in `forward` and in the container). Taking a permission away stopped the client, not the server: the far end could keep sending on the open connection. With UDP it could do so indefinitely, since reply traffic keeps the conntrack entry alive. With TCP it could send at least one window of data, which the client's kernel delivers. The router's own answers (output) were never checked, so a network that lost admin access kept receiving its open SSH or Cockpit session's output.
- **Measured** on main's rules (`tests/netns/firewall.py`, CI run 46092814400, job 140817466673). After the permission went, the server's next packet arrived over TCP and UDP for every kind of revocation tested:
  - another network's reach (family to iot),
  - a network's internet (iot),
  - admin access (iot),
  - a device forgotten and so quarantined,
  - a device paused, over IPv6 (paused by MAC).

  That's 10 failing checks. IPv4 pause was already cut both ways (`ip daddr @cutoff drop`).
- **Attacker:** whoever is on the far side of a connection that was open when the permission went. For example, a compromised camera's command server keeps sending commands to it after its internet is turned off, or an IoT device keeps feeding data into a family device after the family network loses its reach to IoT.
- **Fix:** `modules/heliopause.nix` ("revocation"), the container's forward chain in `modules/heliosphere.nix`.
  - **Tags:** every accepting rule tags its connection with a hash of its own text (`ct mark set 0x8…`).
  - **Replies:** they pass only while their tag is one of the current ruleset's. An Apply that drops a rule therefore stops the other side at once, and connections whose rule didn't change keep their tag and are untouched.
  - **The opening side:** each of its packets is first marked "revoked" (0x7fffffff), then re-tagged by whichever rule still allows it.
  - **The router's answers:** a new output check drops them on connections the router may no longer accept.
  - **Per-destination rules:** rules that named only a source interface (reach "all", Tailscale) are now one rule per destination interface, plus one for the rest, so each pair has its own tag.
- **Still open:** a change no rule text shows can only take effect when the device sends something. These are a device forgotten (only the known-device set changes) and a device paused by MAC on IPv6 (only a live set changes; the router doesn't know the device's IPv6 addresses). A device that stays completely silent keeps receiving on connections it already had. UDP push to a silent device is the only realistic case. A removed UPnP mapping still doesn't end connections already open through it (its rule doesn't change). Connections open before this change carry no tag (mark 0) and are treated as before until their client sends a packet.
- **Tests:**
  - `tests/netns/firewall.py` loads the real generated rulesets (`tests/netns/rulesets.nix`, `nix build .#heliopauseRulesets`) into namespaces. For each kind of revocation it checks both directions, the server first, then that the permission given back allows new connections. It also checks that an unrelated change leaves an open connection alone in both directions.
  - `tests/netns/heliosphere.py`: each way in loses a home network while another keeps it, and the home server pushes first (the model of the container's rules: old, #15, new).
  - `tests/vm/hardening.nix` asserts the order in the real container ruleset.

### HA-28: Forged ICMP errors crossed between networks (Medium, Fixed)

- **What:** `ct state related accept` came before every policy rule, on the host and in the container. Conntrack decides "related" from the header an ICMP error quotes, not from where the error comes from. Linux checks only that the outer destination matches the connection. So a device on any network could send an ICMP error, quoting another network's open connection, to that connection's own endpoint, and it was forwarded across the isolation.
  - Over IPv4 the endpoint can even be the router's WAN address: NAT then hands the error to the inside host.
  - Over IPv6 the attacker aims straight at the host.
- **Measured** on main's rules (same run). All three passed when they shouldn't have:
  - iot into trusted's internet flow (IPv4, through NAT),
  - family into trusted's internet flow (IPv6),
  - family into trusted's flow to iot (IPv4).
- **Attacker:** any device on any network, including guest and IoT, that knows or can guess a victim connection's addresses and ports. Through NAT that's the WAN port: about 28,000 guesses, each a single packet. **Impact:**
  - ICMP "port unreachable" ends a victim's UDP sessions (DNS, QUIC, calls, games).
  - "Fragmentation needed" lowers its path MTU.
  - The packet itself is traffic that isolation should have stopped.
- **Fix:** related packets now need the parent connection's tag and its interface pair, either way round (`ct mark . iifname . oifname`); anything else related is dropped. The router's own connections are tagged by the interface they leave on (output chain), so errors to the router are checked the same way. The container checks the same way, with internet connections tagged per way in.
- **Tests:** `tests/netns/firewall.py` (`related`) forges all three variants, and checks that real errors (from the internet, and from iot about trusted's flow to it) still arrive.

### HA-29: OpenVPN's CRL chowned by a fixed name (Info, Fixed)

`hs_gen_crl` had `openssl` write `crl.pem.tmp` into `/var/lib/heliosphere/openvpn/server`, a folder owned by OpenVPN's account (uid 1194). It then chowned and replaced that file by name: the HA-04 pattern. It wasn't exploitable: no host process runs as uid 1194, and the container mounts the folder read-only. The CRL is now built in the CA's root-only folder and handed over with `hs_write`, which chowns by file descriptor. The real-OpenSSL unit test covers its mode and contents.

### HA-30: `helios-ctl reboot` claimed success when refused (Low, functional, Fixed)

`cmd_reboot` ignored `systemctl reboot`'s exit status, so an account polkit refuses (anyone but root) got `{"ok": true}`. It now reports the refusal, and sends the "rebooting" notification only once systemd has accepted. Test: `test_maintenance_and_reboot` (a refused reboot), and the non-admin VM subtest (below).

### HA-31: Remote-access clients can reach each other (Info, Open)

The container forwards between its ways in (WireGuard, OpenVPN, Tailscale) and between clients on the same way in. `docs/heliosphere.md` says clients can't reach home, which holds, but says nothing about each other. That suits one owner's phone and laptop; it doesn't suit giving remote access to guests. If clients should be isolated from each other, add `oifname { <client interfaces> } drop` before the internet rules in the container's forward chain. Not changed here: it's a policy decision.

### HA-32: WireGuard and OpenVPN ways in got no answers with a VPN way out (Info, functional, Fixed)

Found by `tests/vm/killswitch.nix`. The container answered the phone's WireGuard handshake, but the answer went into the egress tunnel. Answers to connections that came in on eth0 were routed by `fwmark 0x1 lookup main` at priority 90, and there were two problems:
- wg-quick adds its rules without a priority, and the kernel then puts them in front of every existing rule (78/79), ahead of 90.
- With an OpenVPN way out, `main` itself holds the `redirect-gateway` routes.

Not a leak: the kill switch held, so the answers went nowhere. But remote access over WireGuard or OpenVPN didn't work with a VPN way out. Fix (`modules/heliosphere.nix`):
- The answers have a table of their own (90: default via the host).
- wg-quick's rules are moved to 190/200 once it's up.

Test: the kill-switch VM test connects the phone over WireGuard with each way out.

### HA-33: The proxy way out never worked (Info, functional, Fixed)

Found by `tests/vm/killswitch.nix`. There were two problems in `helios-egress-proxy` (`modules/heliosphere.nix`):
- **The proxy address was mangled.** `-proxy "socks5://$creds<host>:<port>"`: bash read `$creds` plus the start of the host name as one, empty, variable. A host of `192.0.2.1` became `.0.2.1`, and the same happens to any host name.
- **The tunnel was never set up.** tun2socks runs `-tun-post-up` without a shell, so the `;`-joined commands failed: tun-out stayed down, with no address, route or rules.

The kill switch held, so client traffic went nowhere and nothing leaked. Fixed with `''${creds}`, and a script file for post-up that also replaces its rules instead of adding duplicates. Test: the kill-switch VM test's SOCKS5 run.

## Verified by design

These were checked in the 2026-11 audit and found sound. Re-check the relevant row when you change the code it names.

| Area | What holds |
|---|---|
| Photosphere XSS | All DOM is built with `h()` (`createTextNode` / `setAttribute`). There's no `innerHTML`, `eval` or `new Function`. Device names from DHCP, mDNS, UPnP and LLDP, notifications, and AI output are rendered as text. |
| Nix / nftables injection | Every state.json field that reaches a Nix or nft template is validated by regex, `ipaddress` or a type check (`NAME_RE`, `MAC_RE`, `NET_NAME_RE`, ports, UPS labels, serials). Free text (notes, labels, SSIDs) never reaches a module. |
| Secrets in argv | Passwords, TOTP, keys and tokens reach helios-ctl through stdin (`apiInput`), never on the command line. |
| Unsafe parsing | No `pickle`, `yaml.load`, `eval` or `shell=True` in helios-ctl. |
| Who runs helios-ctl as root | Only Cockpit users who can sudo (wheel, `execWheelOnly`). `NO_GIT` only skips the git step; it grants nothing. Run as another user, it only has that user's own file rights. |
| Egress uploads | WireGuard and OpenVPN uploads are stripped of hooks, scripts, plugins and management options. Fuzzers: `tests/fuzz/`. |
| Rescue bypass | Caddy matches the rescue port by `remote_ip` (the TCP peer), never by Host or `X-Forwarded-For`. Since HA-01, the rescue subnet is also only accepted on `rescue0`. |
| Gate fails closed | If Authelia is down, `forward_auth` fails and Cockpit isn't reached. If Caddy is down, Cockpit stays on localhost. The gate only enforces once Photosphere writes `enforced` (so upgrades can't lock anyone out). |
| Heliosphere host side | `ve-heliosphere` may only send from `10.60.0.2`, IPv6 is dropped, and router input from it is DNS only. Bind mounts are read-only except `wireguard/` and the Tailscale state. Files it can write are written with `mkstemp`. |
| UPnP | Only `upnp` networks can ask. `secure_mode=yes`, blocked devices and ports are denied first, and the WAN DNAT is limited (HA-07). |
| Log AI models | The catalog pins sha256 and size. The download is verified before it's used. llama-cpp runs as a fixed unprivileged account with `IPAddressDeny=any` except localhost. |
| CI workflows | All actions are pinned to commit SHAs, permissions are minimal at top level, there's no `pull_request_target`, no `${{ github.event.* }}` in `run:`, and `persist-credentials: false`. |
| Supply chain | `flake.lock` pins rev and narHash. Test dependencies are hash-pinned. Dependabot updates them after a 7-day cooldown. |

## Regression tests

| Test | Runs | Covers |
|---|---|---|
| `tests/netns/conntrack.py` | CI as root (all checks). Rootless anywhere with user namespaces (input checks only). | HA-01, HA-02, HA-03, plus legitimate traffic, NAT, and a connection kept open across a reload. It runs the old and new chain orderings side by side. |
| `tests/netns/heliosphere.py` | CI as root. Rootless: `HELIOS_NETNS_DRY=1` only builds the namespaces and loads every ruleset. | HA-23 to HA-27: kill switch for each way out and each failure mode, per-way-in revocation in both directions (the home server pushes first), IPv6, DNS redirect and DoT/DoH blocking for every way in, host reachability, replies, recovery. It runs the container's old, #15 and current orderings side by side (a model). |
| `tests/netns/firewall.py` | CI as root, on the **real** rulesets (`nix build .#heliopauseRulesets`) | HA-27, HA-28 on the host: both directions of revocation (reach, internet, admin, quarantine, pause v4/v6), forged ICMP errors (IPv4 through NAT, IPv6, between networks) and real ones, an unrelated reload, spoofing |
| `tests/vm/killswitch.nix` | CI (`nix build .#vmTests.killswitch`) | HA-24 end to end: WireGuard, OpenVPN and SOCKS5 ways out stopped mid-session with TCP and UDP running; tcpdump on the router's WAN; a kill-switch-off control; new connections and DNS during the outage; recovery; routing rules unchanged across restarts |
| `tests/vm/hardening.nix` (non-admin subtest) | CI | An account outside wheel runs the privileged helios-ctl commands; credentials, keys, the gate and the boot are unchanged (docs/authorization.md) |
| `tests/vm/router.nix` | CI (`nix build .#vmTests.router`) | HA-01, HA-03 on the real generated ruleset; isolation; SSH only from admin networks |
| `tests/vm/hardening.nix` | CI | HA-06; MAC-based pausing; the gate; TOTP; recovery codes; service accounts |
| `tests/vm/passkey.nix` | CI | WebAuthn passkey at the gate |
| `tests/test_helios_ctl.py` | CI and locally (needs `bcrypt`) | HA-04, HA-05, HA-09, HA-10; input validation |
| `tests/fuzz/` | ClusterFuzzLite | Egress config and settings parsing |

**Note:** the namespace tests model Heliopause's and Heliosphere's chains by hand. If you change rule order or the connection-tracking rules in `modules/heliopause.nix` or `modules/heliosphere.nix`, change `ruleset()` / `container_rules()` in the tests to match. The VM tests cover the real generated ruleset.

## Manual verification

These can't be tested in CI. Check them on real hardware after a security-relevant change.

1. **IPv6:** with a real delegated prefix, devices on networks without admin rights keep IPv6 internet, and a prefix change from the ISP doesn't strand them (HA-02).
2. **An Apply with sessions open:** an open Cockpit session from an admin network survives an Apply. A network that loses `admin` loses its open sessions (HA-03).
3. **UPnP:** a mapping requested from a `upnp` network works from the WAN, and stops once it's removed (HA-07).
4. **Rescue port with the gate on:** a laptop on the rescue port gets Cockpit with the password only. A device on any other network can't (HA-01, HA-06).
5. **Tailnet:** the tailnet policy from the template is applied, and a node that isn't an admin can't reach the router (HA-20).
6. **Remote access kill switch with a real provider** (the VM test covers it against test tunnels, not a provider's): with a WireGuard, OpenVPN and proxy way out in turn, keep a client streaming, then stop the tunnel (`systemctl stop wg-quick-wg-out` / `openvpn-egress` / `helios-egress-proxy` inside the container), and restart OpenVPN with the provider's own `persist-tun`. Nothing from the client may appear on the router's WAN (`tcpdump -ni wan host <client's destination>`), and traffic resumes through the tunnel once it's back (HA-24).
7. **Remote access IPv6:** a Tailscale exit-node client with a way out that has IPv6 gets no IPv6 through Heliosphere, and its DNS still goes through Magnetosphere (HA-23).

## Keeping this file current

- **A new finding:** add the next HA-number to the table and a section for it. Give the severity, attacker, prerequisites and the files involved.
- **A fix:** set the status to Fixed, and name the regression test in the finding and in the test table.
- **A change in a boundary's code:** re-check that boundary's findings and its "verified by design" row, and update the line numbers you touched.
- **After a full re-audit:** update "Last full audit".
