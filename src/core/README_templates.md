# Architecture et Cahier des Charges : Système de Templates et Gestion de Flotte

## 1. Vision et Objectifs
Les templates d'équipements (Modbus et BACnet) permettent de standardiser la découverte, la lecture et l'enregistrement de données pour des modèles d'équipements récurrents sur les chantiers (ex: centrales de traitement d'air Swegon, compteurs d'énergie Schneider, modules Waveshare, etc.).

L'objectif est d'assurer :
- **L'autonomie locale sur le terrain** : Un technicien peut créer ou adapter un template sur son `rpinode` immédiatement, même hors connexion.
- **Le partage maîtrisé** : Un template n'est poussé vers le référentiel central de la flotte (`docs.deltathermic.be`) que si l'utilisateur décide explicitement de le partager.
- **L'immutabilité et le versioning sans collision** : Toute modification d'un template partagé engendre une nouvelle version identifiée de manière unique (UUID / révision par nœud) pour éviter les écrasements de configuration entre plusieurs boîtiers.
- **L'observabilité et la maintenance sur `docs`** : Une interface dédiée sur `docs` permet de visualiser les versions, de cartographier quel boîtier utilise quelle version, et de nettoyer/fusionner en toute sécurité.

---

## 2. Cycle de Vie d'un Template

```
[Création Locale] (Brouillon / Isolé)
        │
        ├──> [Utilisé sur chantier local]
        │
        └──> [Case "Partager à la flotte" cochée]
                    │
                    ▼  (Push /sync)
             [Référentiel Flotte (docs)]
                    │
                    ├──> [Attribution Version officielle / UUID de révision]
                    │
                    ├──> [Disponible au téléchargement pour les autres rpinodes]
                    │
                    └──> [Cartographie d'usage dans la zone Maintenance]
```

### A. Création et Édition Locale (rpinode)
1. **Statut initial (`is_shared = 0`)** :
   - Tout nouveau template créé sur un boîtier reste **local par défaut**.
   - Il n'est pas envoyé aux autres boîtiers et ne pollue pas la flotte.
2. **Publication (`is_shared = 1`)** :
   - Dès que le template est testé et validé sur le terrain, le technicien peut cocher l'option **« Partager à la flotte »**.
   - Le template est alors envoyé au serveur maître `docs` lors de la synchronisation suivante.
3. **Modification d'un template partagé (Versioning)** :
   - Si un template partagé (`is_shared = 1` ou déjà reçu de `docs`) est modifié :
     - Soit il crée une **nouvelle révision/version** (ex: `v1` -> `v2` avec identifiant unique `tpl_uuid` + `revision_uuid` + `node_author`).
     - Soit il est cloné en tant que template local dérivé pour préserver l'intégrité des appareils existants.

### B. Gestion des Collisions Distribuées
Pour éviter que deux nœuds distincts (ex: `rpi01` et `rpi02`) ne créent simultanément une révision concurrente "v2" du même template :
- Chaque template possède un `template_uuid` universel unique et permanent.
- Chaque révision possède un `revision_uuid` unique et trace son auteur : `created_by_node` (hostname du boîtier).
- `docs` enregistre les révisions sous forme d'arbre de versions. En cas de fork simultané par deux nœuds, la zone de maintenance de `docs` permet de comparer les registres et de fusionner/sélectionner la version de référence.

### C. Déclaration d'Usage (Télémétrie)
- Lors de chaque cycle de synchronisation `/sync`, chaque `rpinode` transmet la liste des templates qu'il utilise actuellement sur ses chantiers actifs (`template_uuid`, `version`).
- Cette information permet à `docs` de tenir à jour une matrice d'usage en temps réel.

---

## 3. Synchronisation : Pull & Push

### A. Copie Locale des Templates depuis `docs` (Pull)
* **Comportement par défaut** :
  - Lors du `sync`, `rpinode` interroge la bibliothèque globale de `docs`.
  - Les templates officiels partagés (`is_shared = 1`) sont mis à disposition localement dans la base SQLite du `rpinode`.
* **Suppression Locale** :
  - Si un technicien supprime localement un template :
    - **Contrôle préalable** : Impossible de supprimer si le template est actuellement assigné à un équipement (`modbus_devices`) sur un chantier configuré dans le boîtier.
    - **Persistance** : Le boîtier mémorise que ce template a été masqué/supprimé localement pour éviter qu'un `pull` passif ne le réinjecte automatiquement à chaque minute.
    - Le template reste disponible sur `docs` pour les autres boîtiers.

### B. Suppression Globale (Zone Maintenance sur `docs`)
* Un administrateur peut archiver ou supprimer définitivement un template depuis `docs.deltathermic.be`.
* **Règle de sécurité stricte** : Un template ne peut être supprimé globalement **que si son compteur d'usage est à 0** (aucun rpinode ne l'utilise activement).

---

## 4. Modèle de Données

### A. Schéma Local (`rpinode` SQLite)
```sql
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
```

### B. Schéma Distant (`docs.deltathermic.be` MariaDB)
```sql
CREATE TABLE IF NOT EXISTS boitier_modbus_templates (
    id INT AUTO_INCREMENT PRIMARY KEY,
    template_uuid VARCHAR(64) NOT NULL,
    revision_uuid VARCHAR(64) NOT NULL UNIQUE,
    parent_revision_uuid VARCHAR(64) NULL,
    name VARCHAR(128) NOT NULL,
    manufacturer VARCHAR(128) NULL,
    version INT DEFAULT 1,
    definition_json LONGTEXT NOT NULL,
    created_by_node VARCHAR(64) NOT NULL,
    is_deprecated BOOLEAN DEFAULT 0,
    date_creation DATETIME DEFAULT CURRENT_TIMESTAMP,
    date_modification DATETIME ON UPDATE CURRENT_TIMESTAMP,
    INDEX (template_uuid),
    INDEX (name)
);

CREATE TABLE IF NOT EXISTS boitier_template_usage (
    boitier_id VARCHAR(64) NOT NULL,
    chantier_id INT NOT NULL,
    template_uuid VARCHAR(64) NOT NULL,
    revision_uuid VARCHAR(64) NOT NULL,
    device_name VARCHAR(128) NOT NULL,
    last_reported_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (boitier_id, chantier_id, template_uuid, device_name)
);
```

---

## 5. Interface Maintenance sur `docs.deltathermic.be`
Dans la section d'administration / maintenance :
1. **Tableau des Templates & Versions** :
   - Colonnes : Nom, Fabricant, Version, Auteur, Nombre de boîtiers actifs, Nombre d'appareils liés, Statut.
2. **Visualisation des Diffs** :
   - Comparaison visuelle des registres entre deux versions (ex: ajout d'un registre de température, correction d'échelle ou de fonction).
3. **Actions Administrateur** :
   - Supprimer (si 0 boîtier actif).
   - Fusionner (réassigner les boîtiers d'une version `v1.1-rpi02` vers une version officielle `v2.0`).
   - Déprécier (affiche un avertissement sur les boîtiers incitant à passer sur la version recommandée).
