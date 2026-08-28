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
