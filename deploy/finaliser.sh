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

sed "s/__NOM__/$NOM/g" "$KIT/systeme/mosquitto/bridge.conf.modele" > /etc/mosquitto/conf.d/bridge.conf
systemctl restart mosquitto
systemctl restart rpinode

echo "Boîtier finalisé : $(hostname), pont MQTT $(grep '^connection' /etc/mosquitto/conf.d/bridge.conf)"
