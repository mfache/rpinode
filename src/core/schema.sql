-- Table des rpinodes (la flotte)
-- Permet de connaître les autres boîtiers et leur état.
CREATE TABLE IF NOT EXISTS nodes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    hostname TEXT NOT NULL UNIQUE,      -- Identifiant technique (ex: "rpi-01")
    display_name TEXT,                  -- Nom lisible (ex: "Boîtier Maintenance Nord")
    last_seen DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Table des chantiers
-- Les noms sont communs et peuvent être renommés via docs.deltathermic.be.
-- L'external_id est la clé stable pour les renommages.
CREATE TABLE IF NOT EXISTS sites (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,          -- Nom affiché, peut changer.
    external_id TEXT UNIQUE,            -- Identifiant stable sur le serveur maître.
    is_provisional BOOLEAN DEFAULT 0,   -- 1 si créé automatiquement (bloque les services)
    is_dirty BOOLEAN DEFAULT 1,         -- 1 si modifié localement et non synchronisé.
    description TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    sync_updated_at DATETIME            -- Date de dernière synchro réussie.
);

-- Table des antennes GSM
-- Identifiant physique de l'antenne (eNodeB pour la 4G).
CREATE TABLE IF NOT EXISTS antennas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mcc TEXT,      -- Mobile Country Code
    mnc TEXT,      -- Mobile Network Code
    enodeb TEXT,   -- ID de l'antenne physique (CID // 256)
    lac_tac TEXT,  -- Location Area Code / Tracking Area Code
    cid TEXT,      -- Cell ID complet (ECI)
    lat REAL,      -- Latitude estimée
    lon REAL,      -- Longitude estimée
    last_seen DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(mcc, mnc, enodeb) -- Clé stable même si on change de secteur sur le pylône
);

-- Table de liaison (Plusieurs-à-Plusieurs)
-- Gère "Plusieurs chantiers par localisation" et "Plusieurs localisations par chantier"
CREATE TABLE IF NOT EXISTS site_antennas (
    site_id INTEGER,
    antenna_id INTEGER,
    linked_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (site_id, antenna_id),
    FOREIGN KEY (site_id) REFERENCES sites(id) ON DELETE CASCADE,
    FOREIGN KEY (antenna_id) REFERENCES antennas(id) ON DELETE CASCADE
);

-- Historique des présences des rpinodes sur les chantiers
-- Permet de savoir quel rpinode était où et quand.
CREATE TABLE IF NOT EXISTS node_presence (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id INTEGER NOT NULL,
    site_id INTEGER NOT NULL,
    first_seen DATETIME DEFAULT CURRENT_TIMESTAMP,
    last_seen DATETIME DEFAULT CURRENT_TIMESTAMP,
    is_current BOOLEAN DEFAULT 1,       -- 1 si le rpinode est actuellement sur ce site
    FOREIGN KEY (node_id) REFERENCES nodes(id) ON DELETE CASCADE,
    FOREIGN KEY (site_id) REFERENCES sites(id) ON DELETE CASCADE
);

-- Profils réseau (IP fixe ou DHCP) associés à un chantier.
-- Permet de restaurer la configuration IP lors d'un déplacement.
CREATE TABLE IF NOT EXISTS site_network_profiles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    site_id INTEGER NOT NULL,
    interface TEXT NOT NULL,            -- 'eth0' ou 'wlan0'
    method TEXT DEFAULT 'auto',         -- 'auto' (DHCP) ou 'manual'
    addresses TEXT,                     -- Adresses CIDR (ex: "192.168.1.10/24")
    gateway TEXT,
    ssid TEXT,                          -- Pour le WiFi (wlan0)
    psk TEXT,                           -- Mot de passe WiFi (optionnel)
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (site_id) REFERENCES sites(id) ON DELETE CASCADE,
    UNIQUE(site_id, interface)
);

-- Templates Modbus génériques (communs à la flotte)
CREATE TABLE IF NOT EXISTS modbus_templates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    external_id TEXT UNIQUE,            -- ID sur le serveur maître
    name TEXT NOT NULL,                 -- Nom du modèle d'appareil
    manufacturer TEXT,
    registers_json TEXT,                -- Définition des registres (JSON)
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Appareils Modbus installés sur un chantier
CREATE TABLE IF NOT EXISTS modbus_devices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    site_id INTEGER NOT NULL,
    template_id INTEGER NOT NULL,
    name TEXT,                          -- Nom donné par l'utilisateur (ex: "VMC local 101")
    protocol TEXT NOT NULL,             -- 'tcp' ou 'mstp'
    address TEXT NOT NULL,              -- IP (pour TCP) ou Slave ID (pour MSTP)
    port INTEGER,                       -- Port (pour TCP, défaut 502)
    is_dirty BOOLEAN DEFAULT 1,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (site_id) REFERENCES sites(id) ON DELETE CASCADE,
    FOREIGN KEY (template_id) REFERENCES modbus_templates(id) ON DELETE CASCADE
);

-- Templates BACnet génériques
CREATE TABLE IF NOT EXISTS bacnet_templates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    external_id TEXT UNIQUE,
    name TEXT NOT NULL,
    manufacturer TEXT,
    objects_json TEXT,                  -- Définition des objets (JSON)
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Appareils BACnet installés sur un chantier
CREATE TABLE IF NOT EXISTS bacnet_devices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    site_id INTEGER NOT NULL,
    template_id INTEGER NOT NULL,
    name TEXT,
    device_instance INTEGER,            -- ID d'instance BACnet
    network_address TEXT,               -- IP ou adresse MAC MSTP
    is_dirty BOOLEAN DEFAULT 1,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (site_id) REFERENCES sites(id) ON DELETE CASCADE,
    FOREIGN KEY (template_id) REFERENCES bacnet_templates(id) ON DELETE CASCADE
);

-- Historique des relevés (Trends)
CREATE TABLE IF NOT EXISTS trends (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    site_id INTEGER NOT NULL,
    protocol TEXT NOT NULL,             -- 'modbus' ou 'bacnet'
    timestamp INTEGER NOT NULL,         -- Unix timestamp
    device_id TEXT NOT NULL,            -- Identifiant de l'appareil
    object_id TEXT NOT NULL,            -- Registre Modbus ou Objet BACnet
    value TEXT,                         -- Valeur relevée
    is_synced BOOLEAN DEFAULT 0,        -- 1 si envoyé au serveur maître
    FOREIGN KEY (site_id) REFERENCES sites(id) ON DELETE CASCADE
);

-- Équipements découverts sur le réseau (Scanner IP)
-- On utilise la MAC comme clé unique pour persister les annotations (pièce, usage, etc.)
CREATE TABLE IF NOT EXISTS discovered_devices (
    mac TEXT PRIMARY KEY,
    vendor TEXT,
    last_ip TEXT,                       -- Dernière IP connue
    last_ports TEXT,                    -- Ports ouverts (JSON: [80, 502, ...])
    last_iface TEXT,                    -- Interface (eth0, wlan0)
    bacnet_instance INTEGER,            -- Instance BACnet découverte
    bacnet_name TEXT,                   -- Nom BACnet découvert
    modbus_info TEXT,                   -- Infos Modbus découvertes (Unit IDs, etc.)
    annotations_json TEXT,              -- JSON des champs personnalisés {"Pièce": "Local Tech", ...}
    is_dirty BOOLEAN DEFAULT 1,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    sync_updated_at DATETIME,
    last_seen DATETIME DEFAULT CURRENT_TIMESTAMP
);
