# Pilote personnalisé Moxa UPort 1150 (RS-485 2 fils)

## Contexte et Problématique
Le boîtier utilise un adaptateur USB-Série Moxa UPort 1150 (ID `110a:1150`) pour communiquer avec des bus de terrain RS-485 (BACnet MS/TP et Modbus RTU).

Le pilote noyau Linux mainline `ti_usb_3410_5052` sait piloter les modes RS-232, RS-422 et RS-485 de l'appareil (firmware Moxa + commande vendor TI_SET_CONFIG), mais code en dur le mode RS-232 pour le modèle 1150/1151 (contrairement au 1130 qui est RS-485 uniquement). Aucun réglage standard côté OS (`setserial`, `stty`) n'expose ce choix pour le 1150.

## Solution apportée
Le fichier `ti_usb_3410_5052-moxa-uport1150.c` est une version modifiée du pilote mainline qui force le mode RS-485 2 fils (`td_rs485_only = true`) lors de l'initialisation du Moxa UPort 1150/1151.

## Compilation et Installation
Pour recompiler et installer le module noyau :
```bash
sudo bash drivers/moxa/build_moxa_rs485_driver.sh
```

Le script installe le module `.ko` dans `/lib/modules/$(uname -r)/kernel/drivers/usb/serial/ti_usb_3410_5052.ko`.
Au branchement du Moxa, le périphérique est reconnu et attaché à un port `/dev/ttyUSB*` (et symlinké dans `/dev/serial/by-id/`).
