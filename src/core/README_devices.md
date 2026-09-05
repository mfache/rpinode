# Architecture et Cahier des Charges : Qualification des Passerelles & Périphériques et Synchronisation Flotte (docs)

## 1. Contexte et Problématique

### A. Constat Matériel & Terrain
Sur un boîtier `rpinode`, de multiples périphériques USB et passerelles de communication peuvent être connectés simultanément :
- Passerelles **RS-485** (ex: Moxa UPort 1150, adaptateurs USB-RS485 industriels) utilisées pour **BACnet MS/TP** et **Modbus RTU**.
- Passerelles **M-Bus maître** (ex: micro-convertisseurs USB/M-Bus basés sur CP2102/FTDI + étage de puissance M-Bus TSS721) utilisées pour la relève de compteurs d'énergie, de chaleur et d'eau.
- Passerelles **RS-232** point-à-point (ex: consoles d'automates, centrales incendie).
- Modems cellulaires **4G / GPS** (ex: cartes SIM7600 / Quectel) exposant plusieurs ports série virtuels pour les commandes AT, le flux data et le flux NMEA GPS.

### B. La Limite de l'Auto-détection Noyau Linux
Le noyau Linux identifie uniquement la puce de conversion USB-UART (ex: Silicon Labs `cp210x`, FTDI `ftdi_sio`, Prolific `pl2303`, WCH `ch341`).
Or, **un même convertisseur `CP2102` peut indifféremment piloter une interface RS-485, une passerelle M-Bus physique ou un simple câble RS-232**. Le noyau et l'OS ne peuvent pas deviner la couche électrique branchée en aval.

### C. Objectifs du Cahier des Charges
1. **Qualification Locale par l'Utilisateur** : Permettre au technicien de nommer et de déclarer précisément les capacités physiques et protocolaires de chaque passerelle depuis l'interface `/devices`.
2. **Filtrage Intelligent dans les Outils Métier** : Restreindre les menus déroulants de sélection de port (BACnet MS/TP, Modbus RTU, M-Bus) aux seules passerelles déclarées compatibles pour éliminer les erreurs de manipulation sur site.
3. **Persistance Matérielle Stable** : Associer chaque configuration à l'empreinte matérielle unique du composant (`/dev/serial/by-id`, numéro de série USB ou bus USB physique) pour conserver l'affectation même en cas de changement de port USB ou de redémarrage.
4. **Synchronisation avec le Référentiel Flotte (`docs.deltathermic.be`)** :
   - Remonter l'inventaire matériel et les qualifications vers le serveur central `docs` lors de chaque synchronisation.
   - Offrir une vision centralisée du parc de passerelles déployées par boîtier.
   - Partager une base de connaissances globale des modèles de passerelles (ex: modèle X = M-Bus natif).

---

## 2. Typologie des Passerelles et Protocoles Supportés

| Type Physique Déclaré | Couche Électrique | Protocoles Éligibles dans l'UI | Exemples Typiques |
| :--- | :--- | :--- | :--- |
| `rs485` | Différentiel 2 fils (Half-Duplex) / 4 fils | **BACnet MS/TP**, **Modbus RTU** | Moxa UPort 1150 (patché 2 fils), DSD TECH SH-U10, USB-RS485 Waveshare |
| `mbus` | Maître M-Bus (Modulation courant/tension EN 13757-2) | **M-Bus Filaire** | Adaptateur USB-MBus Relay / Micro-Master, convertisseurs M-Bus CP2102 |
| `rs232` | Point-à-point V.24/RS-232 (±12V) | **Modbus RTU (Série)**, Console AT | Câbles console FTDI RS-232, Moxa UPort (mode 232) |
| `modem_4g_gps` | Ports virtuels USB CDC/Option | **Modem 4G**, **Flux GPS NMEA** | SimTech SIM7600E-H, Quectel EC25 |
| `generic_serial` | UART / Série non qualifié | Tous (avec avertissement) | Adaptateur USB-Série non encore qualifié |

---

## 3. Workflow Utilisateur & Cycle de Vie

```
[Connexion USB de la Passerelle sur rpinode]
                   │
                   ▼
       [Détection Noyau (lsusb & /dev/serial/by-id)]
                   │
                   ▼
     [Vérification de l'Empreinte en Base SQLite]
        ├──> Profil Existant : Restauration immédiate des capacités & du nom
        │
        └──> Nouveau Matériel :
                 ├── Pré-qualification automatique (si Moxa ou Modem identifié)
                 └── Statut "À qualifier" avec badge orange sur l'interface /devices
                                │
                                ▼
                 [Technicien qualifie la passerelle]
                 (Nom, Bus physique, Protocoles autorisés)
                                │
                                ├──> Persistance locale immédiate (SQLite)
                                ├──> Mise à jour dynamique SSE sur la page
                                └──> Envoi lors du prochain /sync vers docs.deltathermic.be
```

---

## 4. Modèle de Données

### A. Schéma Local (`rpinode` SQLite : `src/core/schema.sql`)

```sql
CREATE TABLE IF NOT EXISTS device_qualifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    hardware_key TEXT NOT NULL UNIQUE,      -- Empreinte stable : by_id_name ou "VID:PID:SERIAL"
    vendor_id TEXT,                         -- Ex: "10c4"
    product_id TEXT,                        -- Ex: "ea60"
    serial_number TEXT,                     -- Numéro de série matériel USB si disponible
    by_id_name TEXT,                        -- Ex: "usb-Silicon_Labs_CP2102_USB_to_UART_Bridge_0001-if00-port0"
    user_label TEXT NOT NULL,               -- Ex: "Passerelle M-Bus Compteurs Chaufferie"
    physical_type TEXT NOT NULL,            -- "rs485", "mbus", "rs232", "modem_4g_gps", "generic_serial"
    capabilities_json TEXT NOT NULL,        -- JSON : ["bacnet_mstp", "modbus_rtu", "mbus", "gps_nmea"]
    notes TEXT,                             -- Notes libres du technicien (ex: "Débit 2400 bauds fixe")
    is_dirty BOOLEAN DEFAULT 1,             -- 1 = modifié localement / non acquitté par docs, 0 = synchronisé
    synced_at DATETIME,                     -- Date/heure du dernier acquittement réussi par docs
    is_shared_model BOOLEAN DEFAULT 0,      -- 1 si l'utilisateur souhaite partager cette signature matérielle à la flotte
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

### B. Schéma Distant (`docs.deltathermic.be` MariaDB)

```sql
-- 1. Inventaire du matériel connecté par boîtier (Télémétrie en temps réel)
CREATE TABLE IF NOT EXISTS boitier_devices_inventory (
    id INT AUTO_INCREMENT PRIMARY KEY,
    boitier_uid VARCHAR(64) NOT NULL,          -- Hostname / UUID du rpinode
    hardware_key VARCHAR(191) NOT NULL,
    vendor_id VARCHAR(8),
    product_id VARCHAR(8),
    serial_number VARCHAR(128),
    by_id_name VARCHAR(255),
    user_label VARCHAR(128),
    physical_type VARCHAR(32) NOT NULL,
    capabilities_json JSON NOT NULL,
    current_tty_path VARCHAR(64),               -- Ex: "/dev/ttyUSB0"
    is_connected BOOLEAN DEFAULT 1,             -- Statut de branchement actuel
    last_seen_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uq_boitier_device (boitier_uid, hardware_key),
    FOREIGN KEY (boitier_uid) REFERENCES boitiers(uid) ON DELETE CASCADE
);

-- 2. Base de connaissances partagée des modèles de passerelles (Référentiel Flotte)
CREATE TABLE IF NOT EXISTS fleet_device_catalog (
    id INT AUTO_INCREMENT PRIMARY KEY,
    vendor_id VARCHAR(8) NOT NULL,
    product_id VARCHAR(8) NOT NULL,
    device_model_name VARCHAR(128) NOT NULL,    -- Ex: "Relay PadPuls M2 USB"
    recommended_physical_type VARCHAR(32),      -- "mbus"
    default_capabilities JSON NOT NULL,         -- ["mbus"]
    description TEXT,
    created_by_node VARCHAR(64),
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

### C. Rôle et Cycle de Vie du flag `is_dirty`

Le champ `is_dirty` est fondamental pour le fonctionnement déconnecté / résilient sur le terrain :
1. **À la création ou modification locale** : Dès que l'utilisateur édite un nom, un type physique ou des capacités, l'enregistrement passe à `is_dirty = 1` et `updated_at = CURRENT_TIMESTAMP`.
2. **Pendant le push `/sync`** : Le `rpinode` envoie prioritairement les enregistrements ayant `is_dirty = 1` (ou l'ensemble de l'inventaire actif).
3. **À l'acquittement par `docs`** : Dès que le serveur `docs` confirme la bonne réception de la synchronisation (HTTP 200 `{"ok": true}`), le boîtier met à jour `is_dirty = 0` et renseigne `synced_at = CURRENT_TIMESTAMP`.
4. **Protection contre les écrasements (Pull)** : Si `docs` envoie des suggestions ou des mises à jour globales, un enregistrement local avec `is_dirty = 1` ne sera **jamais écrasé passivement** par le serveur tant que les modifications locales n'ont pas été réconciliées.
5. **Indication visuelle UI** : L'interface `/devices` peut afficher un statut clair :
   - `🟢 Synchronisé avec docs` (`is_dirty = 0`)
   - `🟡 Modification locale en attente de sync` (`is_dirty = 1`)

---

## 5. Protocole de Synchronisation (`/sync` & Reporter)

### A. Payload envoyé par `rpinode` au serveur `docs`
Lors du push périodique (géré par `src/services/reporter.py` / `src/services/sync.py`), un bloc `devices` est intégré :

```json
{
  "node_id": "rpi01",
  "timestamp": "2026-09-05T14:30:00Z",
  "devices": [
    {
      "hardware_key": "usb-MOXA_Technologies_Co.__Ltd._UPort_1150_0-if00-port0",
      "vendor_id": "110a",
      "product_id": "1150",
      "serial_number": "0",
      "by_id_name": "usb-MOXA_Technologies_Co.__Ltd._UPort_1150_0-if00-port0",
      "current_tty_path": "/dev/ttyUSB5",
      "user_label": "Moxa RS-485 Principal (CTA & Chaufferie)",
      "physical_type": "rs485",
      "capabilities": ["bacnet_mstp", "modbus_rtu"],
      "is_connected": true,
      "notes": "Patch noyau 2 fils actif"
    },
    {
      "hardware_key": "usb-Silicon_Labs_CP2102_USB_to_UART_Bridge_0001-if00-port0",
      "vendor_id": "10c4",
      "product_id": "ea60",
      "serial_number": "0001",
      "by_id_name": "usb-Silicon_Labs_CP2102_USB_to_UART_Bridge_0001-if00-port0",
      "current_tty_path": "/dev/ttyUSB0",
      "user_label": "Passerelle M-Bus Compteurs Énergie",
      "physical_type": "mbus",
      "capabilities": ["mbus"],
      "is_connected": true,
      "notes": "Liaison M-Bus vers 12 compteurs eau/élec"
    }
  ]
}
```

### B. Réponse de `docs` vers `rpinode` (Suggestions de qualification)
Si un périphérique inconnu est branché, `docs` peut renvoyer dans la réponse de synchronisation des suggestions issues de la base de connaissances partagée (`fleet_device_catalog`) :

```json
{
  "status": "ok",
  "device_suggestions": {
    "10c4:ea60": {
      "model": "Passerelle M-Bus USB Relay",
      "suggested_type": "mbus",
      "suggested_capabilities": ["mbus"]
    }
  }
}
```

---

## 6. Actions Requises sur le Serveur Central `docs` (`docs.deltathermic.be`)

> **Note importante** : Le code de l'API centrale (`/var/www/reports/api.py` sur le serveur `docs`) n'étant pas dans le dépôt Git local, ces actions doivent être exécutées directement sur le serveur `docs` via connexion SSH.

### A. Connexion SSH au serveur `docs`
Se connecter au serveur maître (directement ou via le réseau Tailscale) :
```bash
ssh user@docs.deltathermic.be
# ou via l'IP / hostname Tailscale du serveur docs
```

### B. Création des tables dans MariaDB / MySQL
Exécuter le script SQL suivant sur la base de données `reports` / `boitiers` :
```sql
-- Table de l'inventaire matériel par boîtier
CREATE TABLE IF NOT EXISTS boitier_devices_inventory (
    id INT AUTO_INCREMENT PRIMARY KEY,
    boitier_uid VARCHAR(64) NOT NULL,
    hardware_key VARCHAR(191) NOT NULL,
    vendor_id VARCHAR(8),
    product_id VARCHAR(8),
    serial_number VARCHAR(128),
    by_id_name VARCHAR(255),
    user_label VARCHAR(128),
    physical_type VARCHAR(32) NOT NULL,
    capabilities_json JSON NOT NULL,
    current_tty_path VARCHAR(64),
    is_connected BOOLEAN DEFAULT 1,
    is_dirty_synced BOOLEAN DEFAULT 1,
    last_seen_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uq_boitier_device (boitier_uid, hardware_key),
    FOREIGN KEY (boitier_uid) REFERENCES boitiers(uid) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Table du catalogue / base de connaissances partagée des passerelles
CREATE TABLE IF NOT EXISTS fleet_device_catalog (
    id INT AUTO_INCREMENT PRIMARY KEY,
    vendor_id VARCHAR(8) NOT NULL,
    product_id VARCHAR(8) NOT NULL,
    device_model_name VARCHAR(128) NOT NULL,
    recommended_physical_type VARCHAR(32),
    default_capabilities JSON NOT NULL,
    description TEXT,
    created_by_node VARCHAR(64),
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_vendor_product (vendor_id, product_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

### C. Mise à jour de `/var/www/reports/api.py`
1. Créer une sauvegarde horodatée du fichier :
   ```bash
   cp /var/www/reports/api.py /var/www/reports/api.py.bak_$(date +%Y%m%d_%H%M%S)
   ```
2. Ajouter la fonction de traitement `_push_devices_inventory(cur, boitier_id, boitier_hostname, devices)` dans `api.py` :
   - Insertion / mise à jour (`ON DUPLICATE KEY UPDATE`) dans `boitier_devices_inventory`.
   - Enregistrement dans le catalogue `fleet_device_catalog` si `is_shared_model = 1`.
3. Déclarer le handler `"devices"` dans la boucle `handlers` de la route `/sync`.
4. Renvoyer dans la réponse JSON de `/sync` les suggestions éventuelles issues de `fleet_device_catalog`.

### D. Rechargement du service uWSGI sur `docs`
Le service tournant sous uWSGI avec fichier de configuration `/etc/uwsgi/...` ou `reports.ini` :
```bash
# Rechargement à chaud (Graceful reload sans interruption)
kill -HUP $(pgrep -f reports.ini)
```
Vérifier ensuite les logs dans `/var/log/uwsgi/` ou `/var/log/nginx/` pour s'assurer que l'API a bien rechargé.

---

## 7. Impacts sur l'Interface Utilisateur (`/devices`)

### A. Composants Visuels
1. **Badge d'état clair par passerelle** :
   - `🟢 Qualifié : M-Bus Filaire`
   - `🔵 Qualifié : Bus RS-485 (BACnet MS/TP & Modbus RTU)`
   - `🟠 Non qualifié (Configuration requise)`
2. **Bouton d'action « ✏️ Qualifier la passerelle »** :
   - Ouvre une modale ou un panneau de configuration inline.
   - Champs :
     - Nom d'usage personnalisé (ex: *Passerelle M-Bus Bâtiment A*).
     - Type de bus physique (Radio / Sélecteur : *RS-485*, *M-Bus*, *RS-232*, *Modem 4G/GPS*, *Autre*).
     - Protocoles autorisés (Cases à cocher pré-cochées selon le type de bus).
     - Notes / Repérage terrain.
3. **Liaison temps réel (SSE)** :
   - Lors de la sauvegarde ou du branchement/débranchement d'un câble USB, la page `/devices` se met à jour immédiatement via le flux SSE `stream.py`.

### B. Cohérence Inter-Pages
- **BACnet MS/TP (`/bacnet/tools?tab=mstp`)** : Le sélecteur de port n'affiche **que** les passerelles qualifiées avec la capacité `bacnet_mstp` (ou RS-485).
- **Modbus RTU (`/modbus/tools`)** : Le sélecteur de port n'affiche **que** les passerelles qualifiées avec `modbus_rtu` ou `rs485` / `rs232`.
- **M-Bus Tools (`/mbus/tools`)** : Le sélecteur n'affiche **que** les passerelles qualifiées `mbus`.

---

## 8. Plan de Mise en Œuvre par Étapes

1. **Étape 1 : Base de données locale & Services (`device_mgr.py` + `schema.sql`)**
   - Création de la table SQLite `device_qualifications`.
   - Ajout des méthodes CRUD dans `src/services/device_mgr.py` (`get_qualification`, `save_qualification`, `get_all_qualifications`).
   - Fusion des informations système réelles (`/dev/serial/by-id`) avec les qualifications enregistrées.

2. **Étape 2 : API Web & Interface `/devices` (`server.py`, `templates/devices.html`, `stream.py`)**
   - Route POST `/api/devices/qualify` pour enregistrer la qualification d'un périphérique.
   - Ajout de la modale / formulaire d'édition dans `templates/devices.html`.
   - Émission de l'événement SSE lors de la mise à jour pour rafraîchir les badges sans rechargement.

3. **Étape 3 : Filtrage sur les interfaces applicatives**
   - Mise à jour des routes BACnet et Modbus pour consommer la liste filtrée des ports selon leurs capacités déclarées.

4. **Étape 4 : Télémétrie et Synchronisation avec `docs`**
   - Intégration de l'inventaire matériel qualifié dans le payload périodique de `src/services/reporter.py` / `src/services/sync.py`.
   - Documentation de l'API côté `docs.deltathermic.be` dans `docs/FLEET_API_CHANGES.md`.
