# Outil de Découverte BACnet MS/TP (`bacwi`)

## Contexte
`bacpypes3` n'implémente pas la couche de liaison série MS/TP (RS-485 à jeton tournant). L'outil utilise le binaire `bacwi` issu de la référence C `bacnet-stack`.

## Correctifs appliqués (`bacnet-stack-mstp.patch`)
1. **Npoll configurable** via `BACNET_MSTP_NPOLL` (accélère l'intégration dans l'anneau à jeton en mise en service : ~15s avec Npoll=2 au lieu de 90-140s avec Npoll=50).
2. **Sortie I-Am en temps réel** : émission d'une ligne `IAM;<device>;<mac>;<apdu>` dès réception d'une réponse avec flush immédiat pour le rafraîchissement live dans l'interface web.

## Compilation et Installation
```bash
sudo bash drivers/mstp/build_mstp.sh
```
Le binaire est installé dans `/opt/boitier-bacnet/mstp/bin/bacwi`.
