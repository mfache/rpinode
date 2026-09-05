## État d'avancement - 05/09/2026

- **Qualification matérielle des passerelles et synchronisation flotte (`src/services/device_mgr.py`, `src/core/schema.sql`, `templates/devices.html`, `src/services/fleet.py`)** :
  - Création de la table `device_qualifications` avec suivi `is_dirty` et `synced_at` pour persister le nom d'usage, le type de bus physique (RS-485, M-Bus, RS-232, Modem 4G/GPS), les protocoles éligibles et les notes terrain.
  - Ajout d'une modale interactive de déclaration et qualification sur `/devices` avec validation en direct sans rechargement de page.
  - Intégration de l'inventaire matériel qualifié dans le cycle `/sync` de `fleet.py` avec acquittement automatique.
  - Cahier des charges et architecture documentés dans `src/core/README_devices.md`.

## État d'avancement - 04/09/2026

- **Support matériel de la passerelle Moxa UPort 1150 (`drivers/moxa/`, `src/services/device_mgr.py`)** : Détection automatique de la passerelle USB-Série Moxa UPort 1150 (RS-232/422/485) attachée sur `/dev/ttyUSB5` via son lien stable `/dev/serial/by-id/`. Sauvegarde et documentation du pilote noyau personnalisé `ti_usb_3410_5052-moxa-uport1150.c` qui force le mode RS-485 2 fils.
- **Nouvelle page Périphériques & Passerelles (`/devices`, `templates/devices.html`)** : Remplacement de l'ancienne route `/storage/devices` par `/devices`. Inventaire exhaustif du matériel connecté (ports série RS-485, passerelles, modems 4G/GPS, hubs USB) avec carte héro dédiée pour la Moxa et raccourcis d'actions immédiates pour lancer l'investigation BACnet MS/TP ou Modbus RTU.
- **Intégration complète de BACnet MS/TP (`src/services/bacnet_mstp.py`, `src/web/stream.py`, `templates/bacnet_tools.html`)** :
  - Écoute passive du bus RS-485 via `pyserial` pour détecter les nœuds présents, contrôler la circulation du jeton (Token), les statistiques de trames, erreurs CRC et parasites.
  - Découverte active Who-Is via le binaire optimisé `bacwi` (`/opt/boitier-bacnet/mstp/bin/bacwi`, `Npoll=2`, émission I-Am temps réel).
  - Diffusion en direct des découvertes et de l'état de santé du bus via flux Server-Sent Events (`/api/bacnet/mstp/stream`).
  - Interface à onglets dans les outils BACnet permettant de basculer instantanément entre BACnet/IP et BACnet MS/TP avec pré-sélection de la passerelle.

## État d'avancement - 31/08/2026

- **Optimisation SQLite & Concurrence (`src/core/database.py`)** : Résolution des erreurs `database is locked`. Passage en mode **WAL (Write-Ahead Logging)** pour autoriser les lectures pendant les écritures. Implémentation d'un gestionnaire de contexte robuste garantissant la fermeture systématique de chaque connexion après usage (`close()`).
- **Préservation de la Carte SD (`src/core/paths.py`)** : Déplacement des journaux d'activité vers la RAM (`/tmp/rpinode/log/`). Aucune écriture disque n'est plus effectuée lors de la génération des logs applicatifs.
- **Nouvelle vue Diagnostic Système (`/monitor/system`)** : Création d'une page de santé globale. Surveillance live de la liaison WAN/4G (Ping 8.8.8.8), des infos de la cellule GSM (eNodeB), de l'état des services locaux, et de la télémétrie matérielle (CPU Temp, RAM, Disque, Uptime).
- **Visualiseur de Logs Interactif (`/monitor/logs`)** : Implémentation d'un terminal de log temps réel. Supporte le filtrage par niveau (Error, Warning, Info, Debug), par module, la recherche textuelle instantanée et le téléchargement du fichier brut.
- **Configuration DHCP Avancée (`src/services/network_config.py`, `src/services/wifi_mgr.py`)** : Ajout du support pour la méthode `shared` sur Ethernet et WiFi. Permet de configurer le boîtier en serveur DHCP avec définition personnalisée de la plage d'adresses (`dhcp_range`).
- **Synchronisation Flotte Étendue (`src/services/fleet.py`)** : Mise à jour du protocole de synchronisation pour inclure les paramètres `gateway` et `dhcp_range`. Patch du serveur central (`docs.deltathermic.be`) pour supporter et historiser ces nouveaux champs par chantier.
- **Modernisation Interface Réseau (`templates/network_interfaces.html`)** : Refonte complète de la gestion des interfaces. Ajout de badges de statut live (En ligne / Débranché), de notifications Toast (Feedback non bloquant), et de panneaux dynamiques pour la configuration IP.
- **Outils Modbus & UX (`templates/modbus_tools.html`)** : Amélioration de l'outil d'investigation. Intégration de l'auto-complétion intelligente sur les adresses IP (basée sur les scans du chantier courant) et persistance automatique des champs du formulaire dans la session (`sessionStorage`).
- **Correctifs PWA & Templates** : Migration du Service Worker en stratégie **Network-First** pour éviter le cache de pages HTML obsolètes. Restauration de la substitution des variables `user` et `widgets` sur le tableau de bord.

## État d'avancement - 30/08/2026

- **Intégration Passerelle Modbus TCP/RTU (`src/services/modbus_tools.py`)** : Détection et intégration de la passerelle Waveshare RS485 TO ETH (B) à l'adresse `192.168.1.254`. Ajout du support pour les trames RTU-over-TCP avec calcul et vérification CRC16.
- **Support des Slave IDs / Unit IDs (`schema.sql`, `modbus_mgr.py`, `server.py`)** : Ajout de la colonne `slave_unit` dans la table `modbus_devices` et dans l'interface web pour permettre la gestion d'esclaves multiples derrière une même passerelle IP.
- **Nouveaux Templates Modbus** : Création des modèles pour le compteur d'énergie `EASTRON SDM630` (FC04, float32 Big Endian), le module de sorties `WS 8CH Analog Output (0-10V)` (FC03/FC06) et le module d'entrées `WS 8CH Analog Input (0-10V)` (FC04).
- **Interface Suivi Modbus (`templates/modbus_suivi.html`)** : Ajout des boutons "Tout cocher" et "Tout décocher" pour l'historisation des points en un clic.
- **Acquisition & Diffusion temps réel via MQTT Local + SSE (`src/services/logger.py`, `src/web/stream.py`, `static/app.js`)** : Le cycle d'acquisition publie chaque mesure fraîche sur le broker MQTT local Mosquitto (`rpinode/modbus/point/{id}`). Le flux SSE diffuse l'événement `modbus_point` directement au navigateur.
- **Robustesse & Tolérance aux erreurs du bus RS485** : Implémentation d'un verrou d'accès (Mutex) par passerelle, espacement inter-trame de 60 ms, délai de purge de 150 ms avec 2 réessais, et politique de rétention de la dernière valeur valide sur 3 cycles consécutifs avant de déclarer une erreur.

## État d'avancement - 28/08/2026

- **Refonte de `src/core/database.py`** : Le script lit maintenant `schema.sql` et exécute chaque commande séparément. Cela permet de modifier le schéma (ex: ajouter une colonne) directement dans le SQL et de relancer l'application pour que les changements soient appliqués (les erreurs "déjà existant" sont ignorées).
- **Service GSM (`src/services/gsm.py`)** : Implémenté en reprenant la robustesse de l'ancienne version. Supporte maintenant l'activation séparée du 3GPP et du GPS, la récupération des coordonnées GPS, et le décodage correct du CID (hex/dec) pour extraire l'eNodeB et le secteur.
- **Service de Présence (`src/services/presence.py`)** : Mis à jour pour enregistrer les coordonnées GPS dans la table `antennas` lorsqu'elles sont disponibles, en utilisant `ON CONFLICT` pour mettre à jour les données existantes.
- **Scanner IP (`src/services/ipscan.py`)** : Nouvel outil de découverte réseau asynchrone. Utilise `fping` pour la rapidité et scanne les ports critiques (Modbus, HTTP, SSH, etc.). Intégration complète dans l'interface avec mise à jour temps réel via SSE (Server-Sent Events) et redirection intelligente vers les autres outils (ex: clic sur port 502 ouvre l'outil Modbus).
