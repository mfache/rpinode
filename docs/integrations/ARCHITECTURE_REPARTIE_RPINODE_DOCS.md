# Cartographie d’architecture répartie : `rpinode` vs `docs`
*(Démons, MQTT, SSE, PWA et flux temps réel)*

> **Date** : 20 septembre 2026  
> **Contexte** : Analyse suite à une interrogation sur l'état d'avancement des mécaniques temps réel (démon / MQTT / SSE / PWA) entre le boîtier embarqué (`rpinode`) et le serveur central (`docs.deltathermic.be`).  
> **Constat de départ** : Travailler sur deux machines distinctes pour un système communicant crée un risque d'asymétrie de perception (impression qu'une fonctionnalité est plus avancée sur l'une que sur l'autre, dispersion des scripts de test).

---

## 1. Synthèse globale : qui fait quoi ?

Contrairement à l'impression que la machinerie temps réel serait plus avancée sur `docs`, **l'infrastructure lourde (gestion de démons, bus IPC, bridge MQTT résilient, hub SSE backend, gestion avancée du cache PWA) se trouve sur `rpinode`**.

`docs` se concentre principalement sur :
- L'hébergement du broker Mosquitto central.
- L'interface Web uWSGI de supervision (avec une couche front-end JavaScript réactive très soignée).
- L'API d'authentification pour la future application mobile native.

---

## 2. Tableau comparatif détaillé

### A. Démons et processus d'arrière-plan

| Dimension | `rpinode` (Boîtier embarqué) | `docs` (`/opt/reports-dev` / serveur) |
| :--- | :--- | :--- |
| **Architecture** | **Multi-services & multi-processus orchestrés** par `src/main.py`. | **Aucun démon applicatif actif en tâche de fond**. Tourne sous **uWSGI** (workers Nginx). |
| **Services actifs** | 1. `bacnet_daemon.py` : sous-processus dédié dans `/opt/boitier-bacnet/venv`, cycle de vie protégé (`pkill`, `atexit`).<br>2. `LiveViewService` : thread dédié avec watchdog d'auto-stop (300 s / 5 min) pour préserver le forfait 4G.<br>3. `start_tracker` : boucle de localisation (GPS/cellulaire).<br>4. `wifi_mgr` : reconnexion WiFi automatique et robuste.<br>5. `logger` : historisation (Trends Modbus/BACnet).<br>6. `reporter` : télémétrie système périodique vers MQTT.<br>7. `headscale_enroll` : rattachement automatique au VPN. | 1. `reports-mqtt-publisher.service` : service systemd inactif/mort (arrêté le 14 sept., simple émetteur de faux heatbeats).<br>2. `sse_worker.py` : simple prototype non démonisé (POC avec mocks).<br>3. `live_view.py` : script de test autonome avec `bacpypes3` (utilisé ponctuellement pour prototyper). |

### B. MQTT et communication réseau

| Dimension | `rpinode` (Boîtier embarqué) | `docs` (`/opt/reports-dev` / serveur) |
| :--- | :--- | :--- |
| **Rôle principal** | **Bus IPC interne complet** + télémétrie distante. | **Broker relais central**. |
| **Pont réseau (Bridge)** | **Pont matériel Mosquitto** dans `/etc/mosquitto/conf.d/bridge.conf` :<br>- Connecté à `100.64.0.4:1883` (VPN Headscale) avec QoS 1.<br>- `topic dt/cmd/rpi01/# in 1 ""` (écoute des ordres)<br>- `topic reports/sse/updates out 1 ""` (envoi des données)<br>- Gère automatiquement pertes et reprises de liaison sans crash Python. | Héberge l'instance Mosquitto centrale sur le port `1883`. Pas de pont sortant. |
| **Intégration Python** | Centralisée dans `services/mqtt_service.py` (`MqttClient`). Utilisé pour le dialogue entre `main`, `bacnet_mgr`, `ipscan`, `reporter`, etc. | Appels unitaires `paho.mqtt.publish.single()` dans `web/ui.py` lors d'actions utilisateur (ex: clic Live View). |

### C. Server-Sent Events (SSE)

| Dimension | `rpinode` (Boîtier embarqué) | `docs` (`/opt/reports-dev` / serveur) |
| :--- | :--- | :--- |
| **Backend** | **`SSEMonitorHub` complet** dans `src/web/stream.py` (540 lignes) :<br>- Suivi des connexions par IP et par client UUID.<br>- Enregistrement des statistiques d'événements.<br>- Multiples flux spécialisés (`/api/stream`, `/api/devices/stream`, `/api/bacnet/mstp/stream`, `/api/mqtt/stream`, `/api/sse/stream`). | Routes légères dans `src/web/stream.py` :<br>- `/reports_sse` et `/chantier/<id>/reports_sse`.<br>- Chaque requête uWSGI instancie un client Paho éphémère qui écoute `reports/sse/updates` et le pousse dans une `Queue`. |
| **Frontend / UI** | Tableaux de bord d'administration et d'inspection temps réel du hub SSE. | **Très abouti visuellement** :<br>- `SSEPointManager` dans `templates/points_scripts.tpl`.<br>- Animations de valeurs dynamiques (indicateurs de tendance, clignotement).<br>- Compte à rebours 5 min côté UI aligné avec le watchdog du boîtier. |

### D. PWA vs Mobile

| Dimension | `rpinode` (Boîtier embarqué) | `docs` (`/opt/reports-dev` / serveur) |
| :--- | :--- | :--- |
| **PWA (Service Worker)** | **Service Worker très élaboré (90 lignes)** dans `static/sw.js` :<br>- Stratégie **Stale-While-Revalidate** sur `/static/`.<br>- Stratégie **Network-First** sur les pages HTML.<br>- Exclusion explicite des flux SSE, scans et APIs.<br>- Gestion de cache propre (`rpinode-v10`) et nettoyage à l'activation.<br>- Page d'erreur hors-ligne (503). | `static/sw.js` minimal (25 lignes) :<br>- Network-First générique.<br>- Pas de pré-mise en cache.<br>- Simple exclusion pour ne pas bloquer OAuth Google. |
| **Orientation Mobile** | PWA installable autonome sur le boîtier. | Recentré sur une **application mobile native Android** (`src/services/mobile.py`) : activation par QR code/clé, tokens de session, tokens d'accès WebView courte durée. |

---

## 3. Analyse du piège : pourquoi cette confusion ?

1. **L'effet "vitrine" de l'interface `docs`** : L'expérience utilisateur sur `docs` est très vivante (bouton Live View réactif, valeurs qui défilent en direct, compte à rebours interactif). On a facilement tendance à penser que le "moteur" est sur la machine où l'effet visuel est le plus spectaculaire.
2. **Les commits récents sur `docs`** : Du code étiqueté `Live view`, des scripts de tests (`live_view.py`, `test_live_view.py`) et la documentation `API_LIVE_VIEW.md` ont été écrits sur `docs`. Mais ces scripts étaient des bancs d'essai ou des spécifications d'API : l'implémentation opérationnelle finale réside dans `rpinode` (`src/services/live_view.py`).
3. **Le risque inhérent aux deux machines** :
   - Difficulté à garder une vue synchronisée du code.
   - Tendance à laisser des scripts exploratoires sur une machine (ex: le `mosquitto_sub` résiduel ou `sse_worker.py` sur `docs`).
   - Risque de réécrire sur un projet ce qui existe déjà sur l'autre.

---

## 4. Recommandations pour la reprise du travail

Lors de la prochaine session sur ces sujets :

1. **Garder `rpinode` comme maître pour l'embarqué** :
   - Tout ce qui concerne la lecture de bus (BACnet, Modbus), l'auto-stop 4G et la production de données appartient à `rpinode`.
2. **Garder `docs` comme concentrateur et affichage** :
   - `docs` ne doit pas réinventer de daemon complexe : uWSGI + Mosquitto + son routeur SSE actuel suffisent largement pour son rôle de concentrateur.
3. **Méthode de travail multi-machines** :
   - Consulter ce document et [DOCS_SERVER_ACCESS.md](../operations/DOCS_SERVER_ACCESS.md) avant d'entamer une modification sur l'un ou l'autre.
   - Travailler de préférence depuis la même session en pilotant le serveur distant via SSH (`ssh -p 9922 mariadb@docs.deltathermic.be`) ou en ayant un clone local de `reports-dev`.
4. **Petite maintenance à prévoir sur `docs` à l'occasion** :
   - Tuer le processus orphelin `mosquitto_sub` (PID 1961808 lancé le 14/09).
   - Supprimer ou officialiser les prototypes (`live_view.py` et `test_live_view.py` à la racine de `/opt/reports-dev`) pour éviter qu'ils ne soient pris pour le code de production.

