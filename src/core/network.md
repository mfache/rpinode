CONCERNE LES RESEAUX

Le rpinode est un boitier qui doit rester accessible à distance, le wwan0 doit donc être configuré pour se connecter à un réseau 4G.

Il peut également agir comme :
- **Client Ethernet (eth0)** ou **Client WiFi (wlan0)** avec adressage Statique ou DHCP.
- **Serveur DHCP local (Shared)** sur l'interface filaire (eth0) pour piloter des automates en direct.
- **Point d'Accès WiFi (AP)** de secours ou de configuration locale, avec distribution d'adresses IP (DHCP) personnalisable.

Toutes ces configurations sont persistées par chantier (site) et synchronisées avec le serveur de flotte.

### Fonctionnement de la connexion 4G (Modem SIM7600E)

La connexion cellulaire est gérée par la combinaison de **ModemManager** (`mmcli`) et **NetworkManager** (`nmcli`).

> **Constat du 24 octobre 2026 sur `rpi01`** : la description PPP
> ci-dessous ne correspond pas à ce qui tourne. ModemManager utilise le
> port **QMI `cdc-wdm0`** comme port principal (`mmcli -m 0` →
> `primary port: cdc-wdm0`), donc la session de données passe par
> **`wwan0`**, pas par `ppp0`. C'est aussi l'interface que surveille
> `rpinode-watchdog` (`ping -I wwan0`). Les scripts `qmicli` manuels du
> rapport de juin (`~/RAPPORT_ECHEC_4G.md`) restent abandonnés : c'est
> ModemManager qui pilote le QMI, via le profil NetworkManager
> `proximus-4g` (APN `internet.proximus.be`).
>
> **Incident en cours (24 octobre 2026)** : 4G hors service sur `rpi01`.
> ModemManager affiche `failed / sim-missing`, mais la carte est bien lue
> (`AT+CPIN?` → `READY`, ICCID lisible). L'enregistrement échoue :
> `AT+CEER` → `EMM attach failed`, et `AT+COPS=1,2,"20601",7` →
> `+CME ERROR: 13` (SIM failure). Le scan voit bien Proximus, Orange et
> BASE en LTE et GSM : l'antenne et la couverture sont correctes. Cause
> probable : la SIM elle-même, une nano-SIM utilisée dans un adaptateur
> qui perd le contact au moindre choc. Pendant le diagnostic, le modem a
> été mis en sélection manuelle sur Proximus
> (`qmicli -p -d /dev/cdc-wdm0 --nas-set-system-selection-preference="gsm|umts|lte,manual=20601"`,
> retour en automatique avec `…,automatic`) et le profil `proximus-4g` a
> reçu `gsm.network-id 20601`.

#### Évolution (Abandon de wwan0 / QMI au profit de PPP)
Sur les modules Waveshare SIM7600E-H, le modem expose à la fois une interface série (AT via `/dev/ttyUSB2`) et une interface réseau haut débit (QMI via `/dev/cdc-wdm0` et l'interface réseau `wwan0`).

Auparavant (dans l'ancien système), la connexion s'effectuait via des scripts personnalisés utilisant `qmicli` sur `wwan0`. Ce fonctionnement a été abandonné au profit d'une intégration 100% native avec NetworkManager et ModemManager pour des raisons de stabilité.

**Le comportement actuel (Normal) :**
- ModemManager détecte le modem SIM7600.
- Il ignore souvent l'interface `wwan0` (`wwan0 (ignored)`) car le protocole QMI natif peut être instable avec certains firmwares SIMCOM.
- Il utilise le port de contrôle principal `ttyUSB2` (commandes AT).
- NetworkManager monte la connexion de données (APN `internet.proximus.be`) en établissant une session point-à-point classique sur le port série, créant ainsi l'interface réseau **`ppp0`**.

#### Configuration IPv4 vs IPv6
Le réseau de l'opérateur (Mobile Vikings / Proximus) distribue nativement des adresses IPv6. La session PPP négocie souvent le Dual-Stack (IPv4v6).
- `ppp0` recevra une adresse IPv6 globale (ex: `2a02:a020:...`).
- Si l'IPv4 manque sur `ppp0` mais que la connexion externe fonctionne (via IPv6 ou via un fallback Tailscale sur `eth0`), c'est un comportement du réseau cellulaire.
- L'interface d'administration ne compte plus exclusivement sur `wwan0` mais vérifie les routes par défaut globales du système.
