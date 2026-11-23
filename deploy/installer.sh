#!/bin/bash
# Installe rpinode sur un boîtier (Raspberry Pi OS / Debian, Bookworm ou Trixie).
# Lancé en root par deploy/deployer.sh depuis la VM de dev, dans le dossier du
# kit copié sur le boîtier (/tmp/rpinode-deploy) qui contient :
#   rpinode.bundle        dépôt git (git bundle de la VM)
#   rpinode-supervisor    superviseur Rust compilé (aarch64, glibc >= 2.34)
#   cle_admin.pub         clé SSH publique autorisée pour le compte marc
#   systeme/, ...         copie de deploy/ du dépôt
# Les secrets (/etc/rpinode/fleet.env, notify.env, tls/) sont déjà en place.
#
# Usage : installer.sh [--lan eth0|br0]
# Idempotent : peut être relancé.
set -euo pipefail

KIT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LAN=eth0
while [ $# -gt 0 ]; do
    case "$1" in
        --lan) LAN="$2"; shift 2 ;;
        *) echo "Option inconnue : $1" >&2; exit 2 ;;
    esac
done

USER_APP=marc
HOME_APP=/home/$USER_APP
REPO=$HOME_APP/rpinode
VENV_BACNET=/opt/rpinode-bacnet/venv
SYS="$KIT/systeme"

etape() { echo; echo "==> $*"; }

[ "$(id -u)" -eq 0 ] || { echo "À lancer en root." >&2; exit 1; }
for f in /etc/rpinode/fleet.env /etc/rpinode/notify.env /etc/rpinode/tls/wildcard.dt.net.key; do
    [ -f "$f" ] || { echo "Secret manquant : $f (voir deploy/README.md)." >&2; exit 1; }
done

etape "Paquets"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq git mosquitto mosquitto-clients nginx nftables ser2net \
    python3-paho-mqtt python3-requests python3-serial python3-venv sqlite3 \
    ethtool fping curl rsync bluez-tools modemmanager usb-modeswitch >/dev/null

etape "Compte $USER_APP (comme sur rpi01 : sudo sans mot de passe, SSH par clé)"
if ! id "$USER_APP" >/dev/null 2>&1; then
    useradd -m -s /bin/bash "$USER_APP"
fi
GROUPES=""
for g in adm dialout cdrom sudo audio video plugdev games users input render netdev spi i2c gpio lpadmin; do
    getent group "$g" >/dev/null && GROUPES="$GROUPES,$g"
done
usermod -aG "${GROUPES#,}" "$USER_APP"
printf '%s ALL=(ALL) NOPASSWD:ALL\n' "$USER_APP" > /etc/sudoers.d/010_rpinode-marc
chmod 440 /etc/sudoers.d/010_rpinode-marc
visudo -cf /etc/sudoers.d/010_rpinode-marc >/dev/null
install -d -m 700 -o "$USER_APP" -g "$USER_APP" "$HOME_APP/.ssh"
touch "$HOME_APP/.ssh/authorized_keys"
if [ -f "$KIT/cle_admin.pub" ] && ! grep -qF "$(cut -d' ' -f2 "$KIT/cle_admin.pub")" "$HOME_APP/.ssh/authorized_keys"; then
    cat "$KIT/cle_admin.pub" >> "$HOME_APP/.ssh/authorized_keys"
fi
chown "$USER_APP:$USER_APP" "$HOME_APP/.ssh/authorized_keys"
chmod 600 "$HOME_APP/.ssh/authorized_keys"

etape "Code rpinode ($REPO)"
if [ ! -d "$REPO/.git" ]; then
    sudo -u "$USER_APP" git clone -q "$KIT/rpinode.bundle" "$REPO"
    sudo -u "$USER_APP" git -C "$REPO" remote set-url origin git@github.com:mfache/rpinode.git
else
    echo "Dépôt déjà présent : inchangé (mise à jour par git, pas par ce script)."
fi
sudo -u "$USER_APP" git -C "$REPO" log --oneline -1
install -d -o "$USER_APP" -g "$USER_APP" "$REPO/data" "$REPO/supervisor/target/release"
install -m 755 -o "$USER_APP" -g "$USER_APP" "$KIT/rpinode-supervisor" "$REPO/supervisor/target/release/rpinode-supervisor"

etape "Configuration locale (interface LAN : $LAN)"
CONN=""
[ "$LAN" = "br0" ] && CONN="lan-pont"
sudo -u "$USER_APP" python3 - "$REPO/data/config.json" "$LAN" "$CONN" <<'PY'
import json, sys
path, lan, conn = sys.argv[1:4]
try:
    cfg = json.load(open(path, encoding="utf-8"))
except (OSError, ValueError):
    cfg = {}
cfg["lan_interface"] = lan
cfg["lan_connection"] = conn
json.dump(cfg, open(path, "w", encoding="utf-8"), indent=4, ensure_ascii=False)
print("config.json :", {k: cfg[k] for k in ("lan_interface", "lan_connection")})
PY

etape "Venv BACnet ($VENV_BACNET)"
if [ ! -x "$VENV_BACNET/bin/python" ]; then
    python3 -m venv "$VENV_BACNET"
fi
"$VENV_BACNET/bin/pip" install -q --disable-pip-version-check bacpypes3==0.0.106 bacpypes==0.19.0 ifaddr==0.2.0

etape "Scripts et services systemd"
install -m 755 "$SYS"/bin/* /usr/local/bin/
install -m 644 "$SYS"/units/*.service "$SYS"/units/*.timer /etc/systemd/system/
install -D -m 644 "$SYS/units/tailscaled-attendre-reseau.conf" /etc/systemd/system/tailscaled.service.d/attendre-reseau.conf
# Sans modem 4G, le watchdog 4G ne fait que constater l'absence de wwan0 : sans risque.
systemctl daemon-reload

etape "nginx (portail HTTPS du boîtier, certificat *.dt.net)"
install -m 644 "$SYS/nginx/rpinode" /etc/nginx/sites-available/rpinode
ln -sf /etc/nginx/sites-available/rpinode /etc/nginx/sites-enabled/rpinode
rm -f /etc/nginx/sites-enabled/default
nginx -t -q

etape "mosquitto (broker local ; le pont vers docs est posé par finaliser.sh)"
install -m 644 "$SYS/mosquitto/local.conf" /etc/mosquitto/conf.d/local.conf

etape "Bluetooth : terminal série et SSH de secours"
B="$REPO/drivers/bluetooth_terminal"
install -D -m 644 "$B/bluetooth-compat.conf" /etc/systemd/system/bluetooth.service.d/bluetooth-compat.conf
install -m 644 "$B/bt-agent.service" "$B/rfcomm-terminal.service" "$B/rfcomm-ssh.service" /etc/systemd/system/
install -m 755 "$B/rfcomm-pty-terminal" "$B/rfcomm-ssh-relay" /usr/local/bin/
rfkill unblock bluetooth 2>/dev/null || /usr/sbin/rfkill unblock bluetooth 2>/dev/null || true
systemctl daemon-reload
systemctl restart bluetooth
systemctl enable -q --now bt-agent rfcomm-terminal rfcomm-ssh

etape "Tailscale"
if ! command -v tailscale >/dev/null; then
    . /etc/os-release
    curl -fsSL "https://pkgs.tailscale.com/stable/debian/${VERSION_CODENAME}.noarmor.gpg" > /usr/share/keyrings/tailscale-archive-keyring.gpg
    curl -fsSL "https://pkgs.tailscale.com/stable/debian/${VERSION_CODENAME}.tailscale-keyring.list" > /etc/apt/sources.list.d/tailscale.list
    apt-get update -qq && apt-get install -y -qq tailscale >/dev/null
fi
systemctl enable -q --now tailscaled

etape "Démarrage"
systemctl enable -q mosquitto nginx
systemctl restart mosquitto nginx
systemctl enable -q rpinode.service rpinode-supervisor.service rpinode-boot-notify.service \
    tailscale-udp-gro.service rpinode-watchdog.timer rpinode-wifi-rescue.timer
systemctl restart rpinode-supervisor rpinode
systemctl start rpinode-watchdog.timer rpinode-wifi-rescue.timer tailscale-udp-gro || true

etape "Tests de rpinode sur ce boîtier"
sudo -u "$USER_APP" bash -c "cd $REPO && ./run_tests.sh 2>&1 | tail -3"

echo
echo "Installation terminée. rpinode va s'enregistrer auprès de docs (nom rpiNN) puis rejoindre headscale."
echo "Ensuite : sudo $KIT/finaliser.sh"
