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
    method TEXT DEFAULT 'auto',         -- 'auto' (DHCP) ou 'manual' ou 'shared'
    addresses TEXT,                     -- Adresses CIDR JSON (ex: '["192.168.1.10/24"]')
    gateway TEXT,
    dhcp_range TEXT,                    -- Plage DHCP si method='shared' (ex: '192.168.1.10,192.168.1.254')
    ssid TEXT,                          -- Pour le WiFi (wlan0)
    psk TEXT,                           -- Mot de passe WiFi (optionnel)
    is_dirty BOOLEAN DEFAULT 0,         -- 1 si modifié localement et non synchronisé
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (site_id) REFERENCES sites(id) ON DELETE CASCADE,
    UNIQUE(site_id, interface)
);

-- Équipements découverts sur le réseau (Scanner IP)
-- On lie les découvertes à un chantier car les IPs/Ports peuvent changer.
-- La MAC reste la clé pour persister les annotations globales.
CREATE TABLE IF NOT EXISTS discovered_devices (
    site_id INTEGER NOT NULL,
    mac TEXT NOT NULL,
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
    last_seen DATETIME DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (site_id, mac),
    FOREIGN KEY (site_id) REFERENCES sites(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS modbus_templates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    template_uuid TEXT NOT NULL,         -- UUID racine du template (conservé à travers les versions)
    revision_uuid TEXT NOT NULL UNIQUE,  -- UUID spécifique de cette version/révision
    parent_revision_uuid TEXT,           -- Révision parente (historique)
    name TEXT NOT NULL,                  -- Nom du modèle (ex: SWEGON HR Série)
    manufacturer TEXT,                  -- Fabricant (ex: Swegon)
    version INTEGER DEFAULT 1,           -- Numéro de version séquentiel
    is_shared BOOLEAN DEFAULT 0,         -- 1 si publié/partagé avec la flotte
    is_local_hidden BOOLEAN DEFAULT 0,   -- 1 si masqué/supprimé localement par l'utilisateur
    created_by_node TEXT,                -- Hostname du boîtier d'origine
    registers_json TEXT NOT NULL,        -- JSON de définition des registres
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
    slave_unit INTEGER DEFAULT 1,       -- Numéro d'esclave (Slave ID / Unit ID)
    is_dirty BOOLEAN DEFAULT 1,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (site_id) REFERENCES sites(id) ON DELETE CASCADE,
    FOREIGN KEY (template_id) REFERENCES modbus_templates(id) ON DELETE CASCADE
);

-- Templates BACnet génériques
CREATE TABLE IF NOT EXISTS bacnet_templates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    template_uuid TEXT,                  -- UUID racine du template (conservé à travers les versions)
    revision_uuid TEXT UNIQUE,           -- UUID spécifique de cette version/révision
    parent_revision_uuid TEXT,           -- Révision parente (historique)
    name TEXT NOT NULL,                  -- Nom du modèle
    manufacturer TEXT,                  -- Fabricant
    version INTEGER DEFAULT 1,           -- Numéro de version séquentiel
    is_shared BOOLEAN DEFAULT 0,         -- 1 si publié/partagé avec la flotte
    is_local_hidden BOOLEAN DEFAULT 0,   -- 1 si masqué/supprimé localement par l'utilisateur
    created_by_node TEXT,                -- Hostname du boîtier d'origine
    objects_json TEXT,                  -- Définition des objets (JSON)
    external_id TEXT UNIQUE,
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

-- Table des définitions de colonnes personnalisées
CREATE TABLE IF NOT EXISTS custom_column_definitions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    table_id TEXT NOT NULL,            -- ex: 'ip_scan'
    column_key TEXT NOT NULL,          -- ex: 'room'
    column_label TEXT NOT NULL,        -- ex: 'Pièce'
    is_mandatory BOOLEAN DEFAULT 0,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(table_id, column_key)
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

-- Points Modbus sélectionnés pour le Suivi (Live) et/ou l'Enregistrement (Historique)
CREATE TABLE IF NOT EXISTS modbus_points (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    site_id INTEGER NOT NULL,
    device_id INTEGER NOT NULL,
    reg INTEGER NOT NULL,
    function INTEGER DEFAULT 3,
    base INTEGER DEFAULT 0,             -- 0 ou 1 (offset d'adressage Modbus)
    slave_unit INTEGER DEFAULT 1,       -- Slave Unit ID
    name TEXT,
    type TEXT DEFAULT 'int16',
    scale REAL DEFAULT 1.0,
    unit TEXT,
    is_monitored BOOLEAN DEFAULT 1,
    is_recorded BOOLEAN DEFAULT 0,
    cadence TEXT DEFAULT '1m',
    last_value TEXT,
    last_read_ts INTEGER,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (site_id) REFERENCES sites(id) ON DELETE CASCADE,
    FOREIGN KEY (device_id) REFERENCES modbus_devices(id) ON DELETE CASCADE,
    UNIQUE(device_id, function, reg)
);

-- Points BACnet sélectionnés pour le Suivi (Live) et/ou l'Enregistrement (Historique)
CREATE TABLE IF NOT EXISTS bacnet_points (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    site_id INTEGER NOT NULL,
    device_id INTEGER,                  -- Lien vers bacnet_devices (optionnel)
    network_address TEXT NOT NULL,      -- IP ou adresse MAC MSTP
    device_instance INTEGER NOT NULL,   -- ID de l'équipement BACnet
    object_id TEXT NOT NULL,            -- ex: "analog-input:1"
    name TEXT,
    is_monitored BOOLEAN DEFAULT 1,
    is_recorded BOOLEAN DEFAULT 0,
    cadence TEXT DEFAULT '1m',
    last_value TEXT,
    last_read_ts INTEGER,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (site_id) REFERENCES sites(id) ON DELETE CASCADE,
    FOREIGN KEY (device_id) REFERENCES bacnet_devices(id) ON DELETE SET NULL
);

-- Dictionnaire local de tous les points BACnet connus (construit à la demande),
-- utilisé pour la recherche globale par nom/joker sans avoir à re-scanner le réseau.
-- Rattaché au chantier (site_id) : si le rpinode change de chantier, ces points ne
-- doivent plus apparaître dans les recherches. is_dirty/sync_updated_at permettent
-- de synchroniser le dictionnaire vers le serveur central (docs).
CREATE TABLE IF NOT EXISTS bacnet_points_catalog (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    site_id INTEGER NOT NULL,            -- Chantier propriétaire de ce point
    network_address TEXT NOT NULL,      -- IP (ou adresse routable) de l'appareil
    device_instance INTEGER NOT NULL,   -- Instance BACnet de l'appareil
    object_id TEXT NOT NULL,            -- ex: "analog-value:55"
    object_name TEXT,                   -- Libellé du point (object-name)
    is_dirty BOOLEAN DEFAULT 1,         -- 1 si non encore synchronisé vers docs
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    sync_updated_at DATETIME,           -- Date de dernière synchro réussie
    FOREIGN KEY (site_id) REFERENCES sites(id) ON DELETE CASCADE,
    UNIQUE(site_id, device_instance, object_id)
);

-- État de la construction du dictionnaire BACnet (ligne unique)
CREATE TABLE IF NOT EXISTS bacnet_catalog_status (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    status TEXT DEFAULT 'idle',         -- idle | scheduled | running | done | error
    scheduled_at DATETIME,
    started_at DATETIME,
    finished_at DATETIME,
    total_devices INTEGER DEFAULT 0,
    done_devices INTEGER DEFAULT 0,
    failed_devices INTEGER DEFAULT 0,
    last_error TEXT
);
INSERT OR IGNORE INTO bacnet_catalog_status (id) VALUES (1);
