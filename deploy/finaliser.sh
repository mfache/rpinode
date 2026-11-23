#!/bin/bash
# Finalise un boîtier après son enrôlement automatique dans la flotte : reprend
# le nom attribué par docs (fleet_assigned_hostname, ex. rpi02) et l'applique
#   - au nom d'hôte du système (+ /etc/hosts) ;
#   - au nom utilisé par les notifications (RPINODE_NAME de notify.env) ;
#   - au pont MQTT vers docs (rpiNN-to-docs, topic dt/cmd/rpiNN/#).
# Lancé en root sur le boîtier (deploy/deployer.sh s'en charge). Idempotent.
set -euo pipefail

KIT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG=/home/marc/rpinode/data/config.json

[ "$(id -u)" -eq 0 ] || { echo "À lancer en root." >&2; exit 1; }
NOM=$(python3 -c "import json; print(json.load(open('$CONFIG')).get('fleet_assigned_hostname') or '')")
if ! [[ "$NOM" =~ ^[a-z][a-z0-9-]{1,30}$ ]]; then
    echo "Pas encore de nom attribué par docs (fleet_assigned_hostname vide ou invalide : '$NOM')." >&2
    exit 1
fi
echo "Nom attribué : $NOM"

hostnamectl set-hostname "$NOM"
if grep -q '^127\.0\.1\.1' /etc/hosts; then
    sed -i "s/^127\.0\.1\.1\s.*/127.0.1.1\t$NOM/" /etc/hosts
else
    printf '127.0.1.1\t%s\n' "$NOM" >> /etc/hosts
fi

if grep -q '^RPINODE_NAME=' /etc/rpinode/notify.env; then
    sed -i "s/^RPINODE_NAME=.*/RPINODE_NAME=$NOM/" /etc/rpinode/notify.env
else
    echo "RPINODE_NAME=$NOM" >> /etc/rpinode/notify.env
fi

# Canal ntfy propre au boîtier : celui copié de la source porte le nom d'un autre
# boîtier (boitier-rpi01-…). Le sujet ntfy vaut mot de passe : il n'est pas affiché.
if grep -q '^NTFY_URL=.*boitier-' /etc/rpinode/notify.env && ! grep -q "^NTFY_URL=.*boitier-$NOM-" /etc/rpinode/notify.env; then
    BASE=$(sed -n 's|^NTFY_URL="\{0,1\}\(https\?://[^/]*\)/.*|\1|p' /etc/rpinode/notify.env)
    SUJET="boitier-$NOM-$(head -c 8 /dev/urandom | od -An -tx1 | tr -d ' \n')"
    sed -i "s|^NTFY_URL=.*|NTFY_URL=\"$BASE/$SUJET\"|" /etc/rpinode/notify.env
    echo "Nouveau canal ntfy pour $NOM (s'y abonner : sudo grep NTFY_URL /etc/rpinode/notify.env)."
fi

# Script d'installation du certificat (servi par nginx en /install-ca.bat) : nom du boîtier
[ -f /etc/rpinode/tls/install-ca.bat ] && sed -i "s/^set HOST=.*/set HOST=$NOM.dt.net/" /etc/rpinode/tls/install-ca.bat

# Nom dans la configuration de rpinode
python3 - "$CONFIG" "$NOM" <<'PY'
import json, sys
path, nom = sys.argv[1:3]
cfg = json.load(open(path, encoding="utf-8"))
cfg["hostname"] = nom
json.dump(cfg, open(path, "w", encoding="utf-8"), indent=4, ensure_ascii=False)
PY

sed "s/__NOM__/$NOM/g" "$KIT/systeme/mosquitto/bridge.conf.modele" > /etc/mosquitto/conf.d/bridge.conf
systemctl restart mosquitto
systemctl restart rpinode

echo "Boîtier finalisé : $(hostname), pont MQTT $(grep '^connection' /etc/mosquitto/conf.d/bridge.conf)"
