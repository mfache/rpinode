#!/bin/bash
# SSH par clé uniquement, avec retour arrière automatique : le fichier est
# retiré et sshd rechargé au bout de 5 minutes, sauf confirmation.
# Usage :
#   ./install.sh               # applique + programme le retour arrière
#   ./install.sh --confirmer   # annule le retour arrière
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONF=/etc/ssh/sshd_config.d/01-rpinode-cle-uniquement.conf
RETOUR=rpinode-ssh-rollback

if [ "${1:-}" = "--confirmer" ]; then
    sudo systemctl stop "${RETOUR}.timer" 2>/dev/null || true
    echo "SSH par clé uniquement : confirmé."
    exit 0
fi

sudo install -m 644 "$DIR/01-rpinode-cle-uniquement.conf" "$CONF"
if ! sudo sshd -t; then
    sudo rm -f "$CONF"
    echo "Configuration sshd invalide : fichier retiré, rien n'a changé." >&2
    exit 1
fi

sudo systemctl stop "${RETOUR}.timer" 2>/dev/null || true
sudo systemd-run --quiet --on-active=300 --unit="$RETOUR" \
    /bin/sh -c "rm -f $CONF && systemctl reload ssh"
# reload : les sessions ouvertes sont conservées.
sudo systemctl reload ssh

echo "SSH par clé uniquement appliqué. Retour arrière automatique dans 5 minutes."
echo "Après vérification (connexion par clé OK, mot de passe refusé) : $0 --confirmer"
