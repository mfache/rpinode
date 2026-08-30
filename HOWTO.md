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
