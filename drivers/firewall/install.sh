#!/bin/bash
# Installe le pare-feu du boîtier, avec retour arrière automatique : la table
# est supprimée au bout de 5 minutes sauf si l'on confirme avec --confirmer.
# Usage :
#   ./install.sh               # applique + programme le retour arrière
#   ./install.sh --confirmer   # annule le retour arrière et active au démarrage
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RETOUR=rpinode-firewall-rollback

if [ "${1:-}" = "--confirmer" ]; then
    sudo systemctl stop "${RETOUR}.timer" 2>/dev/null || true
    sudo systemctl enable rpinode-firewall.service
    echo "Pare-feu confirmé et activé au démarrage."
    exit 0
fi

sudo install -D -m 644 "$DIR/rpinode-firewall.nft" /etc/rpinode/rpinode-firewall.nft
sudo install -D -m 644 "$DIR/rpinode-firewall.service" /etc/systemd/system/rpinode-firewall.service
sudo systemctl daemon-reload
sudo nft -c -f /etc/rpinode/rpinode-firewall.nft

sudo systemctl stop "${RETOUR}.timer" 2>/dev/null || true
sudo systemd-run --quiet --on-active=300 --unit="$RETOUR" \
    /usr/sbin/nft delete table inet rpinode_pf
sudo nft -f /etc/rpinode/rpinode-firewall.nft

echo "Pare-feu appliqué. Retour arrière automatique dans 5 minutes."
echo "Après vérification des accès : $0 --confirmer"
