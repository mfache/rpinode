#!/bin/bash
# Installe l'acces "terminal de secours" via Bluetooth (SPP -> login systeme).
# Voir README.md dans ce dossier pour le detail et les implications de securite.
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "==> Installation de bluez-tools (bt-agent, bt-network, ...)"
sudo apt-get update
sudo apt-get install -y bluez-tools

echo "==> Activation du mode --compat de bluetoothd (sdptool / vieux rfcomm)"
sudo install -D -m 644 "$DIR/bluetooth-compat.conf" \
    /etc/systemd/system/bluetooth.service.d/bluetooth-compat.conf

echo "==> Installation des unites systemd"
sudo install -D -m 644 "$DIR/bt-agent.service" /etc/systemd/system/bt-agent.service
sudo install -D -m 644 "$DIR/rfcomm-terminal.service" /etc/systemd/system/rfcomm-terminal.service

echo "==> Deblocage rfkill (au cas ou le Bluetooth soit bloque logiciellement)"
sudo rfkill unblock bluetooth || true

echo "==> Rechargement systemd et activation des services"
sudo systemctl daemon-reload
sudo systemctl restart bluetooth
sudo systemctl enable --now bt-agent.service
sudo systemctl enable --now rfcomm-terminal.service

echo "==> Statut"
systemctl --no-pager status bluetooth bt-agent.service rfcomm-terminal.service || true

echo
echo "Termine. Le boitier est visible en Bluetooth sous le nom de sa machine (hostname)."
echo "Connexion depuis un client: appairage SPP (Just Works, sans code), puis ouverture"
echo "d'un terminal serie sur le port associe -> prompt 'login:' du systeme (compte Unix reel)."
