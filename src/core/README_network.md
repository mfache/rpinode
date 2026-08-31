CONCERNE LES RESEAUX

Le rpinode est un boitier qui doit rester accessible à distance, le wwan0 doit donc être configuré pour se connecter à un réseau 4G.

Il peut également agir comme :
- **Client Ethernet (eth0)** ou **Client WiFi (wlan0)** avec adressage Statique ou DHCP.
- **Serveur DHCP local (Shared)** sur l'interface filaire (eth0) pour piloter des automates en direct.
- **Point d'Accès WiFi (AP)** de secours ou de configuration locale, avec distribution d'adresses IP (DHCP) personnalisable.

Toutes ces configurations sont persistées par chantier (site) et synchronisées avec le serveur de flotte.

### Fonctionnement de la connexion 4G (Modem SIM7600E)

La connexion cellulaire est gérée par la combinaison de **ModemManager** (`mmcli`) et **NetworkManager** (`nmcli`).

#### Évolution (Abandon de wwan0 / QMI au profit de PPP)
Sur les modules Waveshare SIM7600E-H, le modem expose à la fois une interface série (AT via `/dev/ttyUSB2`) et une interface réseau haut débit (QMI via `/dev/cdc-wdm0` et l'interface réseau `wwan0`).

Auparavant (dans l'ancien système `admin_boitier`), la connexion s'effectuait via des scripts personnalisés utilisant `qmicli` sur `wwan0`. Ce fonctionnement a été abandonné au profit d'une intégration 100% native avec NetworkManager et ModemManager pour des raisons de stabilité.

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
