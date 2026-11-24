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
# Usage : installer.sh [--lan eth0|br0] [--lan-internet]
#   --lan-internet : le LAN du chantier fournit l'accès Internet (boîtier sans carte SIM)
# Idempotent : peut être relancé.
set -euo pipefail

KIT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LAN=eth0
LAN_INTERNET=false
while [ $# -gt 0 ]; do
    case "$1" in
        --lan) LAN="$2"; shift 2 ;;
        --lan-internet) LAN_INTERNET=true; shift ;;
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

# Images Raspberry Pi / EDATEC : connexion automatique de « pi » sur la console tty1.
# rpi01 n'en a pas ; on la retire (le compte pi est supprimé après le déploiement).
if [ -f /etc/systemd/system/getty@tty1.service.d/autologin.conf ]; then
    rm -f /etc/systemd/system/getty@tty1.service.d/autologin.conf
    rmdir /etc/systemd/system/getty@tty1.service.d 2>/dev/null || true
    systemctl daemon-reload
fi

etape "Code rpinode ($REPO)"
if [ ! -d "$REPO/.git" ]; then
    sudo -u "$USER_APP" git clone -q "$KIT/rpinode.bundle" "$REPO"
    sudo -u "$USER_APP" git -C "$REPO" remote set-url origin git@github.com:mfache/rpinode.git
else
    echo "Dépôt déjà présent : inchangé (mise à jour par git, pas par ce script)."
fi
sudo -u "$USER_APP" git -C "$REPO" log --oneline -1
# rpinode tourne en root dans ce dépôt de marc : sans cette exception, git refuse
# de le lire (« dubious ownership ») et la version affichée est perdue.
git config --system --get-all safe.directory 2>/dev/null | grep -qx "$REPO" \
    || git config --system --add safe.directory "$REPO"
install -d -o "$USER_APP" -g "$USER_APP" "$REPO/data" "$REPO/supervisor/target/release"
install -m 755 -o "$USER_APP" -g "$USER_APP" "$KIT/rpinode-supervisor" "$REPO/supervisor/target/release/rpinode-supervisor"

etape "Configuration locale (interface LAN : $LAN)"
CONN=""
[ "$LAN" = "br0" ] && CONN="lan-pont"
sudo -u "$USER_APP" python3 - "$REPO/data/config.json" "$LAN" "$CONN" "$LAN_INTERNET" <<'PY'
import json, sys
path, lan, conn, internet = sys.argv[1:5]
try:
    cfg = json.load(open(path, encoding="utf-8"))
except (OSError, ValueError):
    cfg = {}
cfg["lan_interface"] = lan
cfg["lan_connection"] = conn
cfg["lan_default_route"] = internet == "true"
json.dump(cfg, open(path, "w", encoding="utf-8"), indent=4, ensure_ascii=False)
print("config.json :", {k: cfg[k] for k in ("lan_interface", "lan_connection", "lan_default_route")})
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
# Routage IP (raccourcis du chantier par Tailscale) : sans lui, les routes /32 sont
# publiées et approuvées mais le boîtier ne relaie rien (oubli du 1er déploiement, rpi02).
install -m 644 "$SYS/sysctl/99-rpinode.conf" /etc/sysctl.d/99-rpinode.conf
sysctl -q -p /etc/sysctl.d/99-rpinode.conf
# Sans modem 4G, le watchdog 4G ne fait que constater l'absence de wwan0 : sans risque.
# Boîtiers EDATEC : lvd-detect (baisse de tension) a un ordre impossible dans son unité
# d'origine et ne démarre jamais sans mode graphique ; on installe une version corrigée.
if [ -f /lib/systemd/system/lvd-detect.service ] && grep -q '^After=multi-user.target' /lib/systemd/system/lvd-detect.service; then
    install -m 644 "$SYS/units/lvd-detect.service" /etc/systemd/system/lvd-detect.service
    systemctl daemon-reload
    systemctl reenable -q lvd-detect.service
    systemctl start lvd-detect.service
fi
systemctl daemon-reload

etape "nginx (portail HTTPS du boîtier, certificat *.dt.net)"
install -m 644 "$SYS/nginx/rpinode" /etc/nginx/sites-available/rpinode
ln -sf /etc/nginx/sites-available/rpinode /etc/nginx/sites-enabled/rpinode
rm -f /etc/nginx/sites-enabled/default
if ! grep -rqs 'map \$http_upgrade \$connection_upgrade' /etc/nginx/nginx.conf /etc/nginx/conf.d/; then
    # Debian 12 : pas de map $connection_upgrade d'origine (Debian 13 l'a dans nginx.conf)
    install -m 644 "$SYS/nginx/rpinode-upgrade.conf" /etc/nginx/conf.d/rpinode-upgrade.conf
fi
if ! nginx -t -q 2>/dev/null; then
    # nginx < 1.25.1 (Bookworm : 1.22) ne connaît pas « http2 on; » : ancienne syntaxe
    sed -i -e 's/listen 443 ssl;/listen 443 ssl http2;/' -e '/^[[:space:]]*http2 on;/d' /etc/nginx/sites-available/rpinode
fi
nginx -t -q

etape "mosquitto (broker local ; le pont vers docs est posé par finaliser.sh)"
install -m 644 "$SYS/mosquitto/local.conf" /etc/mosquitto/conf.d/local.conf

etape "Bluetooth : terminal série et SSH de secours"
B="$REPO/drivers/bluetooth_terminal"
install -D -m 644 "$B/bluetooth-compat.conf" /etc/systemd/system/bluetooth.service.d/bluetooth-compat.conf
install -m 644 "$B/bt-agent.service" "$B/rfcomm-terminal.service" "$B/rfcomm-ssh.service" /etc/systemd/system/
install -m 755 "$B/rfcomm-pty-terminal" "$B/rfcomm-ssh-relay" /usr/local/bin/
# Visible et appairable en permanence, comme rpi01 (sinon BlueZ coupe la visibilité après 3 min)
for cle in DiscoverableTimeout PairableTimeout; do
    if grep -qE "^#? *$cle *=" /etc/bluetooth/main.conf; then
        sed -i -E "s/^#? *$cle *=.*/$cle = 0/" /etc/bluetooth/main.conf
    else
        sed -i "/^\[General\]/a $cle = 0" /etc/bluetooth/main.conf
    fi
done
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

etape "Tests de rpinode sur ce boîtier (avant démarrage : les bases sont encore à marc)"
sudo -u "$USER_APP" bash -c "cd $REPO && ./run_tests.sh 2>&1 | tail -3"

etape "Démarrage"
systemctl enable -q mosquitto nginx
systemctl restart mosquitto nginx
systemctl enable -q rpinode.service rpinode-supervisor.service rpinode-boot-notify.service \
    tailscale-udp-gro.service rpinode-watchdog.timer rpinode-wifi-rescue.timer
systemctl restart rpinode-supervisor rpinode
systemctl start rpinode-watchdog.timer rpinode-wifi-rescue.timer tailscale-udp-gro || true

echo
echo "Installation terminée. rpinode va s'enregistrer auprès de docs (nom rpiNN) puis rejoindre headscale."
echo "Ensuite : sudo $KIT/finaliser.sh"
