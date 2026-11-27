# helios-install — a small text installer for HeliOS (run as root from the ISO).
# Steps: disk → partitions → hostname/admin → trunk NIC → services → nixos-install.
# Nothing is written to a disk until you type its name to confirm.

[ "$(id -u)" = 0 ] || { echo "Run as root: sudo helios-install"; exit 1; }

ask() {  # ask <prompt> <default> → answer on stdout (answers can also be piped in, one per line)
  local a; read -r -p "$1 [$2]: " a || true; echo "${a:-$2}"
}
yesno() {  # yesno <prompt> <y|n> → status 0 for yes
  local a; a=$(ask "$1 (y/n)" "$2"); [[ "$a" =~ ^[Yy] ]]
}
valid_name() { [[ "$1" =~ ^[a-z][a-z0-9-]{0,30}$ ]]; }

echo "== HeliOS installer =="
online=1
getent hosts github.com >/dev/null || online=""
if [ -z "$online" ] && [ -z "$HELIOS_OFFLINE" ]; then
  echo "No internet, and this build of the installer carries no install files:"
  echo "plug in a cable with internet and retry."
  exit 1
fi
# The image has everything for a standard install; with internet, the newest
# HeliOS and NixOS can be fetched instead (what `nix flake update` gives later)
updates=""
if [ -z "$online" ]; then
  echo "No internet: installing from this image (everything for a standard install is on it)."
elif [ -z "$HELIOS_OFFLINE" ]; then
  updates=1
elif yesno "Internet found. Download updates during the install (newest HeliOS and NixOS)" y; then
  updates=1
fi

# ---------------------------------------------------------------- disk
echo; echo "Disks:"; lsblk -dno NAME,SIZE,MODEL,TRAN -e 7,11
disk=$(ask "Install to which disk (name from the list, e.g. sda or nvme0n1)" "")
[ -b "/dev/$disk" ] || { echo "No such disk: $disk"; exit 1; }
fs=$(ask "Root filesystem: ext4 or btrfs" "ext4")
[[ "$fs" =~ ^(ext4|btrfs)$ ]] || { echo "Pick ext4 or btrfs"; exit 1; }
swap=$(ask "Swap partition size in GiB (0 for none)" "0")
[[ "$swap" =~ ^[0-9]+$ ]] || { echo "Swap must be a number"; exit 1; }

# ---------------------------------------------------------------- system
while :; do host=$(ask "Router hostname" "helios"); valid_name "$host" && break; echo "lowercase letters, digits, dashes"; done
domain=$(ask "Local DNS domain" "home.arpa")
while :; do user=$(ask "Admin login name" "admin"); valid_name "$user" && [ "$user" != root ] && break; echo "lowercase letters, digits, dashes; not root"; done
sshkey=$(ask "Admin SSH public key (paste one line, or leave empty)" "")

echo; echo "Network ports:"
ip -o link show | awk -F': ' '$2 != "lo" {print "  " $2}' | while read -r n; do
  printf '  %-14s %s\n' "$n" "$(cat "/sys/class/net/$n/address")"; done
trunk=$(ask "Port cabled to the switch trunk (name from the list)" "")
[ -e "/sys/class/net/$trunk/address" ] || { echo "No such port: $trunk"; exit 1; }
trunkMac=$(cat "/sys/class/net/$trunk/address")
wanVlan=$(ask "Switch VLAN the modem's port is in" "10")

echo; echo "Optional software (all can be changed later in site.nix / Photosphere):"
b() { if yesno "$1" "$2"; then echo true; else echo false; fi; }
o_tailscale=$(b "  Tailscale (remote admin, exit nodes)" y)
login=""
if [ "$o_tailscale" = true ]; then
  login=$(ask "  Your own Headscale server URL (https://…, empty for Tailscale's)" "")
  [ -z "$login" ] || [[ "$login" =~ ^https://[^[:space:]\"]+$ ]] || { echo "Must start with https://"; exit 1; }
fi
o_notify=$(b "  Notifications (self-hosted ntfy push alerts)" y)
o_print=$(b "  Print server (CUPS)" n)
o_remote=$(b "  Remote access / Heliosphere (WireGuard, OpenVPN, proxy)" n)
if [ -n "$online" ]; then
  o_logai=$(b "  Log AI (local LLM log triage; needs a model file later)" n)
else
  o_logai=false
  echo "  Log AI: needs a download, so turn it on later (Photosphere → Advanced) once online."
fi

cat <<SUMMARY

== Summary ==
  disk      /dev/$disk  (ERASED: 1 GiB EFI + ${swap} GiB swap + $fs root)
  hostname  $host.$domain     admin  $user
  trunk     $trunk ($trunkMac), WAN VLAN $wanVlan
  tailscale $o_tailscale${login:+ via $login}  notifications $o_notify  printing $o_print  remote $o_remote  logAI $o_logai
  software  $(if [ -n "$updates" ]; then echo "newest HeliOS and NixOS (downloaded)"; else echo "the versions on this image"; fi)
SUMMARY
confirm=$(ask "Type the disk name ($disk) to erase it and install" "")
[ "$confirm" = "$disk" ] || { echo "Cancelled; nothing written."; exit 1; }

# ---------------------------------------------------------------- partition
dev=/dev/$disk
part() { if [[ "$disk" =~ [0-9]$ ]]; then echo "${dev}p$1"; else echo "${dev}$1"; fi; }
umount -R /mnt 2>/dev/null || true
mkdir -p /mnt
swapoff -a || true
wipefs -af "$dev"
sgdisk --zap-all "$dev"
sgdisk -n1:0:+1G -t1:ef00 -c1:ESP "$dev"
n=2
if [ "$swap" -gt 0 ]; then sgdisk -n2:0:+"${swap}"G -t2:8200 -c2:swap "$dev"; n=3; fi
sgdisk -n"$n":0:0 -t"$n":8300 -c"$n":nixos "$dev"
partprobe "$dev"; udevadm settle
mkfs.vfat -F32 -n ESP "$(part 1)"
if [ "$swap" -gt 0 ]; then mkswap -L swap "$(part 2)"; swapon "$(part 2)"; fi
root=$(part "$n")
if [ "$fs" = btrfs ]; then
  mkfs.btrfs -f -L nixos "$root"
  mount "$root" /mnt
  for sv in @ @home @nix @log; do btrfs subvolume create "/mnt/$sv"; done
  umount /mnt
  o=compress=zstd,noatime
  mount -o "subvol=@,$o" "$root" /mnt
  mkdir -p /mnt/{home,nix,var/log,boot}
  mount -o "subvol=@home,$o" "$root" /mnt/home
  mount -o "subvol=@nix,$o" "$root" /mnt/nix
  mount -o "subvol=@log,$o" "$root" /mnt/var/log
else
  mkfs.ext4 -F -L nixos "$root"
  mount "$root" /mnt
  mkdir -p /mnt/boot
fi
mount -o umask=077 "$(part 1)" /mnt/boot

# ---------------------------------------------------------------- config
cfg=/mnt/etc/nixos
nixos-generate-config --root /mnt
rm -f "$cfg/configuration.nix"
install -m644 "$HELIOS_TEMPLATES"/{flake.nix,site.nix,state.json,tailnet-policy.hujson} "$cfg/"
# Pin HeliOS and nixpkgs to the exact sources on this image (replaced below
# when downloading updates)
pinned=""
if [ -n "$HELIOS_LOCK" ]; then
  pinned=1
  sed "s|@HELIOS_NARHASH@|$(nix --extra-experimental-features nix-command hash path "$HELIOS_SRC")|" "$HELIOS_LOCK" > "$cfg/flake.lock"
  chmod 644 "$cfg/flake.lock"
fi
if [ -n "$updates" ] && [ -n "$HELIOS_LOCK" ]; then
  echo; echo "Fetching the newest HeliOS and NixOS versions…"
  if nix --extra-experimental-features 'nix-command flakes' flake update --flake "path:$cfg"; then
    pinned=""
  else
    echo "Couldn't fetch updates; installing the versions on this image instead."
    updates=""
    sed "s|@HELIOS_NARHASH@|$(nix --extra-experimental-features nix-command hash path "$HELIOS_SRC")|" "$HELIOS_LOCK" > "$cfg/flake.lock"
  fi
fi
esc() { printf '%s' "$1" | sed 's/[\/&|]/\\&/g'; }
sed -i -e "s|nixosConfigurations\.helios|nixosConfigurations.\"$host\"|" \
       -e "s|github:helios-router/helios|$(esc "$HELIOS_URL")|" "$cfg/flake.nix"
s=$cfg/site.nix
sed -i -e "s|hostName = \"[^\"]*\";|hostName = \"$host\";|" \
       -e "s|domain = \"[^\"]*\";|domain = \"$(esc "$domain")\";|" \
       -e "s|user = \"CHANGE-ME\";|user = \"$user\";|" \
       -e "s|trunkMac = \"CHANGE-ME\";|trunkMac = \"$trunkMac\";|" \
       -e "s|wanVlan = [0-9]*;|wanVlan = $wanVlan;|" "$s"
if [ -n "$sshkey" ]; then
  sed -i "s|^\(\s*\)sshKeys = \[|&\n\1  \"$(esc "$sshkey")\"|" "$s"
fi
toggle() {  # toggle <block> <true|false>: the first `enable =` inside `<block> = {`
  sed -i "/^\s*$1 = {/,/};/ s/enable = \(true\|false\);/enable = $2;/" "$s"
}
[ -z "$login" ] || sed -i "s|loginServer = null;|loginServer = \"$(esc "$login")\";|" "$s"
toggle tailscale "$o_tailscale"; toggle solarWind "$o_notify"; toggle printing "$o_print"; toggle logwatch "$o_logai"
sed -i "s/heliosphere\.enable = \(true\|false\);/heliosphere.enable = $o_remote;/" "$s"

# configDir is a git checkout (Photosphere commits state.json changes)
git -C "$cfg" init -q
git -C "$cfg" add -A
git -C "$cfg" -c user.name=helios-install -c user.email=root@localhost commit -qm "HeliOS install: $host"

# ---------------------------------------------------------------- install
# Small generated files (unit files, configs) are marked "build locally, don't
# substitute"; always-allow-substitutes copies them from this image instead of
# rebuilding them and their build tools. nixos-install itself adds this image's
# store as a substituter ("auto").
opts=(--option always-allow-substitutes true)
if [ -n "$updates" ]; then
  echo; echo "Installing (downloading what changed since this image was made)…"
elif [ -n "$online" ]; then
  echo; echo "Installing from this image (anything not on it is downloaded)…"
else
  echo; echo "Installing from this image…"
  # Only this image's store, so nothing waits on network caches. Note: an empty
  # substituters setting would also drop nixos-install's own local copy and make
  # Nix build everything from source.
  opts+=(--option substituters "auto?trusted=1")
fi
nixos-install --root /mnt --flake "$cfg#$host" --no-root-passwd "${opts[@]}"
if [ -n "$pinned" ] && [ -n "$HELIOS_SOURCES" ]; then
  # The sources flake.lock points at, so the router can rebuild before it's online
  # shellcheck disable=SC2086
  nix copy --no-check-sigs --to "local?root=/mnt" $HELIOS_SOURCES
  if [ -n "$HELIOS_PIN_SOURCES" ]; then
    # flake.lock names these store paths directly: keep them until the first update
    mkdir -p /mnt/nix/var/nix/gcroots/helios-install
    for src in $HELIOS_SOURCES; do ln -sfn "$src" "/mnt/nix/var/nix/gcroots/helios-install/$(basename "$src")"; done
  fi
fi
echo; echo "Set the password for $user (used for sudo and Cockpit):"
if [ -t 0 ]; then
  until nixos-enter --root /mnt -c "passwd $user"; do echo "Try again."; done
else
  read -r pw
  printf '%s:%s\n' "$user" "$pw" | nixos-enter --root /mnt -c chpasswd
fi

cat <<DONE

Done. Remove the USB stick and reboot. Then:
  - Plug the trunk port ($trunk) into your switch's trunk; the modem's port goes in VLAN $wanVlan.
  - Open Cockpit on https://$host.$domain:9090 from a trusted-network device.
  - Your config is in /etc/nixos (a git repo). Edit site.nix for anything not in Photosphere.
  - Photosphere → Advanced → Admin account: change the password, turn on two-factor sign-in.
DONE
if [ -t 0 ] && yesno "Reboot now (remove the USB stick first)" y; then
  umount -R /mnt 2>/dev/null || true
  reboot
fi
