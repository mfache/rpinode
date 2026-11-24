# Déploiement d'un nouveau boîtier rpinode

Kit pour mettre en service un boîtier de la flotte (rpi02, rpi03, …) de façon
reproductible, à partir d'un boîtier de référence (rpi01). Premier essai : le
boîtier EDATEC devenu rpi02, le 23 novembre 2026.

## Principe

Tout se lance depuis la VM de dev :

```sh
deploy/deployer.sh <cible> [--source rpi01] [--lan eth0|br0] [--lan-internet]
```

`--lan-internet` : boîtier **sans carte SIM**, dont l'accès Internet (Tailscale,
docs) vient du réseau filaire du chantier. Sans cette option, rpinode applique
`ipv4.never-default yes` au LAN (comme sur rpi01, où Internet passe par la 4G
ou le Wi-Fi) et le boîtier n'aurait aucune sortie sur un chantier sans Wi-Fi.

| Étape | Ce qui se passe |
|---|---|
| 1 | Vérifications : accès SSH et sudo sur la cible, **Internet sur la cible**, boîtier source joignable |
| 2 | Kit copié dans `/tmp/rpinode-deploy` : dépôt (git bundle de `main`), superviseur Rust compilé pris sur la source, clé SSH de la VM, scripts |
| 3 | Secrets de flotte copiés de la source vers `/etc/rpinode` par un tuyau SSH, jamais affichés : `fleet.env` (secret d'adhésion, ntfy), `notify.env` (ntfy ; nom remis à « nouveau-boitier »), `tls/` (certificat et clé `*.dt.net`, CA interne) |
| 4 | Ancienne identité Tailscale effacée : `tailscale logout` sur la cible et suppression du nœud sur headscale (sinon rpinode ne refait pas l'enrôlement et garde l'ancien nom) |
| 5 | `installer.sh` sur la cible (voir ci-dessous) |
| 6 | Attente de l'enrôlement automatique (docs attribue le nom `rpiNN`, le boîtier rejoint headscale), puis `finaliser.sh` : nom d'hôte, pont MQTT `rpiNN-to-docs`, `RPINODE_NAME` et **canal ntfy propre au boîtier** (s'y abonner : `sudo grep NTFY_URL /etc/rpinode/notify.env`), `install-ca.bat`, `hostname` de config.json |
| 7 | SSH par clé uniquement et pare-feu (`drivers/ssh`, `drivers/firewall`), confirmés seulement si `marc@<IP Tailscale>` répond ; sinon retour arrière automatique en 5 minutes |

`--etape N` reprend à l'étape N après une interruption.

## Ce qu'installe `installer.sh`

- Paquets : git, mosquitto, nginx, nftables, ser2net, python3-paho-mqtt,
  python3-requests, python3-serial, python3-venv, sqlite3, ethtool, fping,
  bluez-tools, modemmanager, usb-modeswitch ; Tailscale (dépôt officiel).
- Compte **`marc`** comme sur rpi01 (sudo sans mot de passe, groupes matériels,
  clé SSH de la VM). Le compte **`docsadmin`** est créé par rpinode lui-même à
  l'enrôlement (accès de docs par Tailscale SSH).
- Dépôt dans `/home/marc/rpinode` (origine GitHub `mfache/rpinode`),
  superviseur dans `supervisor/target/release/` (binaire compilé sur rpi01 :
  aarch64, glibc 2.34 minimum, tourne sur Bookworm et Trixie).
- `data/config.json` : `lan_interface` (`eth0`, ou `br0` pour deux prises
  pontées) et `lan_connection` (`lan-pont` avec le pont).
  Facultatif : `lan_port_labels`, noms des prises du pont affichés sur
  `/network/interfaces` (EDATEC : `{"eth0": "prise 1000M", "eth1": "prise 100M"}`,
  à ajouter à la main).
- Venv BACnet `/opt/rpinode-bacnet/venv` (bacpypes3 0.0.106, bacpypes 0.19.0,
  ifaddr).
- Scripts `/usr/local/bin` et unités systemd de [`systeme/`](systeme/) (copiés
  de rpi01 le 2026-11-23) : rpinode, rpinode-supervisor, watchdog 4G,
  bascule Wi-Fi RPIRESCUE, notification au démarrage, UDP GRO pour Tailscale.
- nginx (portail HTTPS `*.dt.net`, CA interne en `/ca.crt`), mosquitto local.
- Terminal Bluetooth (`drivers/bluetooth_terminal`).
- Lance la suite de tests de rpinode sur le boîtier.

Non repris de rpi01 car propres à ce boîtier : `ccse-gpe2-log`,
`madman-logger`, `screenscrut-receiver`, `tailscale-4g` (ancien montage QMI),
`ser2net.yaml` (ports série du Pi 4).

## Avant de lancer

1. **Internet sur la cible** : Wi-Fi `RPIRESCUE` (partage du téléphone), ou LAN
   avec DHCP. Le profil Wi-Fi se crée sans afficher la clé :
   `sudo nmcli --ask dev wifi connect RPIRESCUE ifname wlan0 name rpirescue`.
2. **Accès SSH** à la cible avec un compte sudo sans mot de passe et la clé de
   la VM (image Raspberry Pi / EDATEC neuve : compte `pi`). En câble direct sans
   DHCP, voir « Premier accès » dans la procédure EDATEC ci-dessous.
3. Le boîtier source (rpi01) doit être joignable depuis la VM (`ssh rpi01`).

## Boîtier EDATEC ED-IPC2110 (CM4) : préparation du matériel

À faire **avant** `deployer.sh` sur une image d'usine EDATEC (Raspberry Pi OS
Bookworm 64 bits avec bureau). Procédure détaillée et éprouvée : dépôt
`mfache/lagotrend`, [`docs/INSTALLATION_BOITIER.md`](https://github.com/mfache/lagotrend/blob/main/docs/INSTALLATION_BOITIER.md),
sections 3 à 10. En résumé :

- prise **1000M** = eth0 natif ; prise **100M** = eth1 (USB) ;
- **pont `br0`** des deux prises (profils NM `lan-pont`, `lan-pont-eth0/1`,
  STP coupé, MAC d'eth0, DHCP en attente illimitée + lien local) → déployer avec
  `--lan br0` ;
- mise à jour complète (noyau 6.12) et correctif `rtc.service`
  (attendre `/dev/rtc0`) ;
- nettoyage du bureau (listes de purge), `config.txt` sans écran, swap coupé ;
  **garder `lvd-detect`** (baisse de tension) et les paquets `ed-*` ;
- antenne Wi-Fi externe (`dtparam=ant2`) : la visser.

Après le déploiement, supprimer le compte d'usine `pi` (rpi01 n'a que `marc`
et `docsadmin`) une fois l'accès par `marc` vérifié :
`sudo loginctl terminate-user pi; sudo userdel -r pi` (l'installeur a déjà retiré
sa connexion automatique sur tty1). Définir ensuite le mot de passe de `marc`
(`sudo passwd marc`) : il sert au terminal Bluetooth (login).

Mises à jour ultérieures du code : le kit ne pose pas de clé GitHub. Soit
passer par la VM (`git push <boîtier>:rpinode main:refs/heads/depuis-vm`, puis
sur le boîtier `git merge --ff-only depuis-vm && git branch -d depuis-vm`), soit
recopier la clé de déploiement de rpi01 (`~/.ssh/id_ed25519_rpinode` + `.pub`,
entrée `Host github.com` de `~/.ssh/config`, lignes github.com de known_hosts),
fait sur rpi02 le 2026-11-24 à la demande de Marc : `git pull` marche alors
directement. Cette clé a les droits de rpi01 sur le dépôt ; la révoquer sur
GitHub coupe tous les boîtiers qui la partagent.

## Journal des déploiements

| Boîtier | Date | Matériel | Notes |
|---|---|---|---|
| rpi02 | 2026-11-23 | EDATEC ED-IPC2110-12324US (CM4 2 Go, eMMC 32 Go, EC25) | premier essai du kit, `--lan br0`, sans carte SIM, Tailscale 100.64.0.12. Défauts trouvés et corrigés : contrôle de la source sans sudo ; nginx 1.22 de Bookworm (`http2 on;` inconnu, map `$connection_upgrade` absente) ; tables `mac_vendors`/`bacnet_vendors` absentes de `schema.sql` et `UnboundLocalError` dans `fleet.sync_location` (base neuve) ; tests lancés après le démarrage (bases en root) ; `safe.directory` git ; autologin `pi` sur tty1. Après coup (signalé par Marc) : « rpi01 » écrit en dur dans la barre latérale (`nav.html`), canal ntfy et `install-ca.bat` copiés de rpi01 → corrigés dans rpinode et `finaliser.sh`. Validation par 3 redémarrages : `lvd-detect` (EDATEC) ne démarrait jamais sans mode graphique (unité d'origine `After=` + `WantedBy=multi-user.target`) puis échouait tant que sa broche n'était pas prête → unité corrigée avec relance ; température et uptime absents de la page (coquille `mqtt.mqtt.Client()` dans le repli paho-mqtt 1.x de `stream.py`, Bookworm ayant paho 1.6.1) ; le pont MQTT se connecte ~1 min après le démarrage (attente de Tailscale), comme sur rpi01 |
