#!/bin/bash
# Compile et installe une version modifiee du pilote noyau mainline
# ti_usb_3410_5052 qui force le Moxa UPort 1150/1150I (et 1151/1151I) en
# RS-485 2 fils, comme le fait deja ce pilote pour les UPort 1130/1130I.
#
# Pourquoi : le pilote UPort 1150 sait piloter le mode RS-232/422/485 de
# l'appareil (firmware Moxa + commande vendor TI_SET_CONFIG), mais code en
# dur le mode RS-232 pour ce modele (contrairement au 1130, RS-485/422
# uniquement, deja force). Aucun reglage cote OS (setserial, etc.) n'expose
# ce choix pour le 1150 en amont. Voir le commentaire en tete de
# ti_usb_3410_5052-moxa-uport1150.c pour le detail des deux modifications
# (le patch RS-485 lui-meme + une compatibilite de macro kzalloc_obj/
# kmalloc_obj selon le noyau).
#
# Usage : sudo bash build_moxa_rs485_driver.sh
set -e

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC_FILE="$SRC_DIR/ti_usb_3410_5052-moxa-uport1150.c"
KVER="$(uname -r)"
KDIR="/lib/modules/$KVER/build"
MODDIR="/lib/modules/$KVER/kernel/drivers/usb/serial"
BUILD_DIR="$(mktemp -d)"

if [ ! -d "$KDIR" ]; then
    echo "Headers du noyau introuvables ($KDIR)." >&2
    echo "Installez-les : sudo apt-get install linux-headers-$KVER" >&2
    exit 1
fi

cp "$SRC_FILE" "$BUILD_DIR/ti_usb_3410_5052.c"
echo "obj-m := ti_usb_3410_5052.o" > "$BUILD_DIR/Makefile"

make -C "$KDIR" M="$BUILD_DIR" modules

if [ ! -f "$MODDIR/ti_usb_3410_5052.ko.xz.orig" ] && [ -f "$MODDIR/ti_usb_3410_5052.ko.xz" ]; then
    cp "$MODDIR/ti_usb_3410_5052.ko.xz" "$MODDIR/ti_usb_3410_5052.ko.xz.orig"
    echo "Module d'origine sauvegarde : $MODDIR/ti_usb_3410_5052.ko.xz.orig"
fi

rm -f "$MODDIR/ti_usb_3410_5052.ko.xz" "$MODDIR/ti_usb_3410_5052.ko"
cp "$BUILD_DIR/ti_usb_3410_5052.ko" "$MODDIR/ti_usb_3410_5052.ko"
depmod -a

rm -rf "$BUILD_DIR"

echo ""
echo "OK : $MODDIR/ti_usb_3410_5052.ko installe (RS-485 force pour l'UPort 1150/1151)."
echo "Debranchez/rebranchez le Moxa (ou 'udevadm trigger') pour charger le nouveau module."
echo "Pour revenir en arriere : voir README § 16.7 (restaurer le .ko.xz.orig + depmod -a)."
