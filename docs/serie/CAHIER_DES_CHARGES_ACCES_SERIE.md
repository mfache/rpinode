# Cahier des charges — Arbitrage des bus série (RS-485) et Modbus RTU direct

> **Statut : brouillon à valider par Marc — aucune ligne de code n'a été modifiée.**
> Rédigé le 2026-09-21, révisé le même jour avec les décisions de Marc sur D1, D3, D4 et D9
> (D2 laissée à l'appréciation : option A retenue par défaut, cf. section 9). Ce document est destiné à un développeur ou à une IA qui
> devra implémenter le sujet. Il distingue ce qui est **constaté dans le code**
> (avec références de fichiers), ce qui est **exigé** (section 5) et ce qui est
> **proposé mais à trancher** (section 9). Ne pas deviner les points ouverts :
> les trancher avec Marc avant de coder la partie correspondante.

## 1. Contexte et objectif

Un boîtier `rpinode` communique avec des équipements de terrain par RS-485
(adaptateur USB-série Moxa UPort 1150, cf. `drivers/moxa/`) en **BACnet MS/TP**
et en **Modbus RTU**. Un port série est une **ressource exclusive et
mono-maître** : si deux composants l'ouvrent en même temps, les octets sont
volés ou les trames corrompues, et l'anneau à jeton MS/TP peut être perturbé.

L'ancien projet (`boitier-telemaintenance`, dépôt GitHub `mfache/boitier-telemaintenance`,
branche `refactor/web-admin`) faisait du Modbus RTU direct sur port série, mais
**sans aucun arbitrage** : le port était ouvert puis refermé à chaque transaction,
sans verrou. Dans `rpinode`, ce code n'a jamais été porté : le commit `1dcb62a`
(2026-08-30) a remplacé un stub (`# return _rtu_transaction(...)`) par du
**RTU-over-TCP** vers une passerelle, et la liaison série directe renvoie
« non encore configurée » (`src/services/modbus_tools.py`, fonction `transaction`).

**Objectif :** (1) garantir qu'un port série n'est jamais utilisé par deux
composants à la fois, (2) rétablir le Modbus RTU direct sur port série
(outils interactifs d'abord ; l'**enregistrement est différé**, cf. D9), (3) isoler les activités de lecture pour
qu'un bus lent ne bloque pas les autres, (4) le tout **sans perdre les finitions
déjà réalisées** (section 3).

## 2. Périmètre

**Inclus :** verrou/arbitrage de port série ; Modbus RTU direct ; modèle de
données des bus série ; séparation des boucles d'enregistrement ; affichage de
l'état des ports ; tests.

**Exclus (V1) :** enregistrement automatique des points Modbus RTU série (différé tant qu'aucun appareil réel n'est branché, cf. phase 6) ; M-Bus (déclaré comme capacité dans `device_mgr.py`, non
implémenté) ; lecture/écriture/enregistrement des points BACnet MS/TP (phase 4,
ancien chantier #15) ; réécriture du démon BACnet/IP ; modification du schéma
MariaDB de `docs` sans accord préalable (impact flotte, cf. section 7.3).

## 3. Existant à préserver (finitions acquises)

Cette section est la **liste de non-régression**. Chaque ligne doit rester vraie
après la refonte.

### 3.1 Matériel et détection (rpinode)

| Fonction | Où | À préserver |
|---|---|---|
| Pilote Moxa UPort 1150 forcé en RS-485 2 fils | `drivers/moxa/` | Inchangé. |
| Détection par `/dev/serial/by-id` (chemin stable) et pilote noyau | `src/services/device_mgr.py` : `list_serial_ports`, `_get_tty_driver` | Le port est identifié par sa clé matérielle, pas par `/dev/ttyUSBx`. |
| Qualification par l'utilisateur (nom, type physique, capacités) | table `device_qualifications`, `device_mgr.py`, `src/core/devices.md` | Conservée, avec `is_dirty`/`synced_at` et synchro flotte. |
| Filtrage des menus de port par capacité (`bacnet_mstp`, `modbus_rtu`…) | `list_serial_ports(filter_capability=…)` | Le Modbus RTU doit n'offrir que les ports qualifiés `modbus_rtu`. |
| Page Périphériques `/devices` avec mise à jour SSE | `templates/devices.html`, `/api/devices/stream` | Étendue (état d'occupation), pas remplacée. |

### 3.2 BACnet MS/TP (rpinode)

| Fonction | Où | À préserver |
|---|---|---|
| Session de surveillance continue en arrière-plan (thread + sous-processus), une seule à la fois | `src/services/bacnet_mstp.py` : `_MstpSession`, `start_mstp_session` | Comportement identique. Refus si session déjà active. |
| Deux phases par cycle : écoute passive (`pyserial`) puis Who-Is actif (`bacwi`) | `_sniff`, `_whois` | Chaque phase ré-ouvre le port : le verrou doit couvrir **tout le cycle**, sinon une autre activité peut s'intercaler entre les deux phases. |
| Décodage des trames, CRC d'en-tête, santé du bus (jeton, PFM, erreurs, octets parasites) | `parse_mstp_stream`, `_bus_health`, `format_health_stats` | Inchangé. |
| Diffusion SSE et persistance de l'état | `/api/bacnet/mstp/stream`, `data/bacnet_mstp_state.json` | Inchangé. |
| `bacwi` patché : `Npoll` configurable (`BACNET_MSTP_NPOLL`), ligne `IAM;…` flushée | `drivers/mstp/`, `build_mstp.sh` | Inchangé (~15 s au lieu de 90-140 s d'intégration à l'anneau). |
| Bornage des paramètres (baud, MAC, Max Master, Npoll, cycle, écoute) | `start_mstp_session` | Inchangé. |

### 3.3 Modbus (rpinode)

| Fonction | Où | À préserver |
|---|---|---|
| Un mutex par passerelle, espacement inter-trame de 60 ms, purge de 150 ms, 2 réessais, pas de réessai sur exception applicative | `modbus_tools.py` : `transaction`, `_get_gateway_lock` | La même politique doit s'appliquer au RTU série direct. |
| RTU-over-TCP (passerelle Waveshare RS485-ETH) avec CRC16 | `modbus_tools.py` : `_rtu_over_tcp_transaction` | Conservé (ce n'est pas du série local). |
| Outils : lecture FC01-04, écriture FC05/06, scan des Unit ID, recherche brute (probe) | `modbus_tools.py`, routes `/api/modbus/tools/*`, `templates/modbus_tools.html` | Doivent fonctionner en RTU série. L'option « RTU/Série » est aujourd'hui commentée (`modbus_tools.html:13`, « À réactiver plus tard »). |
| Bibliothèque de templates, versionnée, partageable à la flotte | `modbus_templates`, `modbus_mgr.py` | Inchangé. |
| Enregistrement toutes les 60 s dans l'historique commun | `src/services/logger.py` | Voir exigence EXG-40. |

### 3.4 Ancien projet — code et savoir-faire récupérables

Sur GitHub, branche `refactor/web-admin` de `boitier-telemaintenance`. **À relire
avant d'implémenter** ; ne pas recopier à l'aveugle (l'ancien code n'avait pas
d'arbitrage).

| Élément | Fichier ancien | Intérêt |
|---|---|---|
| Transport série Modbus RTU (`pyserial`), CRC16, assemblage de trame, ouverture/fermeture | `webadmin/modbus.py` : `_rtu_open`, `_rtu_recv_frame`, `_rtu_transaction`, `_crc16` | Base du transport RTU direct. |
| Scan d'Unit ID en série | `_rtu_scan_units` | À reprendre. |
| Cible capturée **par point** (mode, port, baud, parité, stop, unité) | `read_suivi_point_value`, `_save_template_points` | Permettait d'enregistrer un point RTU sans état de connexion ambiant : à traduire dans le nouveau modèle (section 7). |
| Mémoire du dernier port/baud/parité/stop utilisés | `load_last_rtu`, `save_last_rtu` | Confort d'usage à conserver. |
| Choix des paramètres (bauds, parité N/E/O, 1/2 bits de stop) et validation du chemin (`/dev/ttyUSB*`, `ttyACM*`, `ttyS*`) | `_parse_conn`, `_valid_device`, `_baud_select`… | Règles de validation à reprendre. |
| Classification des erreurs (exception esclave vs absence de réponse) | `_classify_modbus_error`, `render_probe_result` | Utile au diagnostic terrain. |
| Note RS-485 : `pyserial` ne pilote pas le RTS ; les dongles à détection de direction câblée n'en ont pas besoin | commentaire de `_rtu_open` | À traiter explicitement (EXG-22). |
| Notes de chantier MS/TP, mesures et validations en suspens | `NOTES-evolutions.md` (#11-#15), `A_REFAIRE_AVEC_BACNET_MSTP_SAINT.md` | Les validations « bus sain » restent à faire ; le #15 (points MS/TP) est hors périmètre V1. |

## 4. Constats (problèmes à résoudre)

| Réf. | Constat | Preuve |
|---|---|---|
| C1 | **Aucun verrou inter-composants sur un port série.** Seuls existent des `threading.Lock` en mémoire : un par passerelle Modbus, un pour la session MS/TP. Aucun `flock`, aucun `TIOCEXCL`. | `modbus_tools.py`, `bacnet_mstp.py` (`_SESSION_LOCK`), recherche `flock`/`LOCK` sans résultat dans `src/`. |
| C2 | **Un verrou en mémoire ne protège pas entre processus.** Le démon BACnet est un sous-processus ; `bacwi` en est un autre ; un second `main.py` peut exister (incident du 2026-09-03). | `docs/incidents/INVESTIGATION_BACNET_DAEMON.md`. |
| C3 | **Le Modbus RTU série direct est absent** et son option d'interface est masquée. | `modbus_tools.py`, `modbus_tools.html:13`. |
| C4 | **Nom ambigu dans le modèle de données** : `modbus_devices.protocol` vaut `'tcp'` ou `'mstp'`, où `'mstp'` désigne en réalité du Modbus RTU/RS-485 (libellé UI : « Modbus RTU / RS485 »). Il entre en collision avec le BACnet MS/TP. | `schema.sql` (table `modbus_devices`), `modbus_devices.html:71`. |
| C5 | **Le modèle ne stocke aucun paramètre série** (port, baud, parité, stop). Pour un appareil `'mstp'`, `address` contient le Slave ID. | `schema.sql`. |
| C6 | **Une seule boucle pour tout** : Modbus, BACnet (timeout configurable, 45 s par défaut) et synchro flotte s'enchaînent dans `start_data_logger`. Un bus lent ou un appel réseau bloqué retarde le reste. | `logger.py` (`start_data_logger`). |
| C7 | **Aucune priorité** entre l'enregistrement continu et les outils interactifs : un scan Modbus lancé depuis l'interface peut affamer ou perturber l'enregistrement, et inversement. | Absence de mécanisme. |
| C8 | **Numérotation `/dev/ttyUSBx` instable** au rebranchement ; le chemin `by-id` existe déjà mais n'est pas utilisé comme clé de verrou ni de configuration. | `device_mgr.py`. |
| C9 | Une activité qui se **bloque sans planter** n'est détectée par personne (`Restart=always` ne couvre que le crash). | `rpinode.service`. |

## 5. Exigences

Priorités : **M** = obligatoire, **S** = souhaitable. Chaque exigence doit pouvoir
être vérifiée par un test (section 8).

### 5.1 Arbitrage du port série

- **EXG-01 (M)** — Un port série ne peut être ouvert que par un seul détenteur à la fois, **tous processus confondus** (thread web, logger, démon BACnet, `bacwi`).
- **EXG-02 (M)** — Le verrou est identifié par la **clé matérielle stable** du port (`by-id` / `hardware_key`), pas par `/dev/ttyUSBx`.
- **EXG-03 (M)** — Le verrou survit à un crash propre du détenteur sans intervention : il doit être **libéré automatiquement** à la mort du processus (verrou noyau de type `flock`, pas un simple fichier PID).
- **EXG-04 (M)** — Le verrou d'une session MS/TP couvre **tout le cycle** (écoute + Who-Is) et toute la durée de vie du sous-processus `bacwi`.
- **EXG-05 (M)** — Un composant qui trouve le port pris **attend un délai borné puis échoue avec un message explicite** indiquant qui le détient, depuis quand et pour quoi (ex. « occupé par la session MS/TP démarrée par marc à 14:32 »). Jamais de blocage indéfini.
- **EXG-06 (M)** — Le détenteur d'un verrou est **visible** : `/devices` et le flux SSE affichent l'état de chaque port (libre / occupé par X / depuis quand).
- **EXG-07 (S)** — Deux niveaux de priorité : les outils interactifs peuvent **préempter poliment** l'enregistrement continu (celui-ci se met en pause et reprend), avec une durée maximale de préemption.
- **EXG-08 (M)** — Les chemins du verrou viennent de `src/core/paths.py` (nouvelle constante, dossier en RAM type `/run/lock/rpinode`). Aucun chemin en dur ailleurs. Ce sont des fichiers d'exécution, pas des données persistantes : ils n'ont pas leur place dans `data/`.
- **EXG-09 (M)** — Un port série est **affecté à un seul protocole à la fois** (`modbus_rtu` ou `bacnet_mstp`, voir 7.2). L'interface refuse de démarrer l'autre protocole sur un port affecté au premier.
- **EXG-10 (M)** — **Réaffectation manuelle explicite** d'un bus (cas terrain : on retire le Modbus RTU, on recâble, on passe en BACnet MS/TP, ou l'inverse). Séquence imposée : (1) arrêt du rafraîchissement automatique et de toute lecture des points de l'ancien protocole sur ce bus ; (2) attente de la libération du verrou, avec délai borné et message clair sinon ; (3) changement d'affectation ; (4) le démarrage du nouveau protocole reste une **action séparée** de l'utilisateur (le recâblage peut ne pas être terminé).
- **EXG-11 (M)** — Les appareils et points d'un bus qui n'est plus affecté à leur protocole passent à l'état **« bus indisponible / en pause »** : pas de lectures, pas d'erreurs répétées, pas d'alarme. Ce statut est visible dans l'interface (suivi, appareils, `/devices`). Le rafraîchissement automatique côté navigateur (`/api/modbus/suivi/values`, flux SSE) ne lit pas les points en pause. Reprise à la réaffectation du bus.
- **EXG-12 (M)** — La réaffectation ne modifie ni ne supprime l'historique existant ; les trous de mesure sont normaux et aucune valeur fictive n'est écrite.

### 5.2 Modbus RTU sur port série direct

- **EXG-20 (M)** — Transport RTU série via `pyserial` (CRC16, trame, timeouts), intégré à `modbus_tools.transaction`, sous le verrou de EXG-01.
- **EXG-21 (M)** — Paramètres : port (clé stable), vitesse (`_BAUD_CHOICES` de l'ancien code), parité N/E/O, 1 ou 2 bits de stop, timeout. Validation du chemin comme dans l'ancien `_valid_device`.
- **EXG-22 (M)** — Gestion du sens RS-485 documentée et testée sur le Moxa (le pilote force le mode 2 fils). Si un adaptateur exige le pilotage RTS, l'option est configurable par port.
- **EXG-23 (M)** — Silence inter-trame Modbus RTU (T3.5) respecté, en plus de la politique existante (60 ms d'espacement, purge 150 ms, 2 réessais, pas de réessai sur exception applicative).
- **EXG-24 (M)** — Outils interactifs opérationnels en série : lecture FC01-04, écriture FC05/06, scan des Unit ID, recherche brute. Option « RTU/Série » réactivée dans `modbus_tools.html`.
- **EXG-25 (S, différé — décision D9)** — Enregistrement des points Modbus RTU série dans l'historique commun, avec la même cadence et la même tolérance aux erreurs que le TCP. **Non livré dans les premières phases** : aucun appareil n'est branché pour valider. En attendant, le logger **ignore** les appareils `rtu_serial` avec un message explicite (DEBUG), et l'option d'activation est désactivée par défaut dans la configuration centrale (`src/core/config.py`). Le développement se fait sur port virtuel ; la livraison est conditionnée à une validation terrain (phase 6).
- **EXG-26 (M)** — Disparition du port en cours d'utilisation (débranchement USB) : erreur claire, libération du verrou, reprise automatique au retour du port (même clé `by-id`).
- **EXG-27 (S)** — Mémoire des derniers réglages série utilisés dans l'interface (comportement de l'ancien `load_last_rtu`).

### 5.2 bis Les trois transports Modbus (décision D4)

- **EXG-60 (M)** — Trois transports nommés sans ambiguïté, dans le code, la base et l'interface :
  - `tcp` — Modbus TCP natif (MBAP) ;
  - `rtu_over_tcp` — trames RTU (avec CRC) encapsulées dans un flux TCP vers une passerelle Ethernet/RS-485 (ex. Waveshare RS485-ETH) ;
  - `rtu_serial` — RTU sur port série local (Moxa, USB-RS485).
  Les libellés d'interface distinguent explicitement les trois, sans réutiliser « MS/TP ».
- **EXG-61 (M)** — `rtu_over_tcp` est traité comme un **canal lent par nature** : timeout, espacement inter-trame et nombre de réessais réglables **par passerelle**, avec des valeurs par défaut plus tolérantes que le TCP natif. Un nouveau cycle de lecture d'un canal ne démarre pas tant que le précédent n'est pas terminé (pas d'empilement de requêtes).
- **EXG-62 (S)** — Connexion TCP persistante vers la passerelle (aujourd'hui, `_rtu_over_tcp_transaction` ouvre une connexion par transaction), avec reconnexion et détection de connexion morte. À **mesurer avant** de décider, car le gain dépend de la passerelle.
- **EXG-63 (M)** — L'arbitrage (5.1) est généralisé en **« canal »** : clé = port série stable, ou `tcp:<ip>:<port>` pour une passerelle. L'affectation exclusive à un protocole (EXG-09) ne concerne que les ports série locaux.
- **EXG-64 (S)** — Latence mesurée et affichée par canal (voir EXG-53), pour calibrer les délais des passerelles lentes.

### 5.3 Modèle de données

- **EXG-30 (M)** — Lever l'ambiguïté du champ `protocol` (constat C4). Valeurs retenues (décision D4, détail en 5.2 bis) : `tcp`, `rtu_over_tcp`, `rtu_serial`. La valeur historique `mstp` doit rester **lue** pendant la transition et migrée.
- **EXG-31 (M)** — Les paramètres série sont stockés **au niveau du bus** et non de chaque appareil (plusieurs esclaves partagent un bus) : voir 7.1.
- **EXG-32 (M)** — Migration des données existantes sans perte, idempotente, testée (ex. les templates et appareils déjà migrés depuis l'ancien projet).
- **EXG-33 (M)** — Tout changement touchant ce que `fleet.py` envoie à `docs` est **coordonné** avec le serveur (section 7.3) ; sans accord, la synchro doit continuer à fonctionner avec l'ancien format.

### 5.4 Isolation des activités

- **EXG-40 (M)** — Modbus, BACnet et synchro flotte ne partagent plus une même boucle séquentielle : un cycle lent ou bloqué sur l'un ne retarde pas les autres.
- **EXG-41 (M)** — Un thread de lecture par bus (ou par passerelle), pour qu'un bus lent ne pénalise pas les autres bus.
- **EXG-42 (M)** — Aucun appel réseau sans timeout dans une boucle d'enregistrement.
- **EXG-43 (S)** — Détection d'un blocage silencieux (constat C9) : surveillance des threads critiques et alerte ou redémarrage contrôlé (watchdog systemd avec `sd_notify`, conditionné à des battements des threads).
- **EXG-44 (S)** — Le démon BACnet, aujourd'hui lancé et tué par `main.py`, devient une unité systemd propre (`Restart=on-failure`), ce qui supprime les `pkill -9` de `run.sh` (cause de l'incident du 2026-09-03).

### 5.5 Interface et exploitation

- **EXG-50 (M)** — `/devices` montre pour chaque port : libre/occupé, détenteur, protocole affecté, derniers réglages.
- **EXG-51 (M)** — Messages d'erreur en français, actionnables (qui détient le port, comment le libérer).
- **EXG-52 (M)** — Journalisation de chaque acquisition/libération/refus de verrou (niveau DEBUG), et des échecs (WARNING), dans `LOG_DIR`.
- **EXG-53 (S)** — Compteurs par bus : transactions, erreurs, latence moyenne, dernier succès.

## 6. Architecture proposée

### 6.1 Module unique d'arbitrage

Nouveau module `src/services/serial_bus.py`, **seul point d'entrée** pour ouvrir
un port série. Interface indicative :

```python
with serial_bus.acquire(port_key, owner="modbus-logger", purpose="poll",
                        priority=NORMAL, timeout=5.0) as bus:
    ...   # bus.path = chemin réel résolu depuis la clé stable
```

- Verrou noyau (`fcntl.flock`) sur un fichier `paths.SERIAL_LOCK_DIR / <clé>.lock`,
  complété par un état en mémoire pour l'attente, la priorité et l'affichage.
- Le fichier de verrou contient, pour l'information, propriétaire/objet/horodatage
  (écrits par le détenteur ; le verrou lui-même reste le `flock`).
- MS/TP : la session acquiert le verrou pour la durée du cycle ; `bacwi` s'exécute
  pendant que le parent le détient (le sous-processus n'a pas besoin de le connaître).
- Modbus : `transaction` acquiert le verrou par transaction (ou par rafale de
  lectures d'un même appareil, pour éviter de réouvrir le port à chaque registre).

### 6.2 Deux options d'implémentation (voir décision D2)

| | Option A — verrou partagé (recommandée pour démarrer) | Option B — un propriétaire par port |
|---|---|---|
| Principe | Chaque composant ouvre le port lui-même après acquisition du `flock`. | Un processus dédié possède le port et sérialise les requêtes reçues (MQTT local ou socket). |
| + | Changement limité, pas de nouveau protocole interne, compatible avec `bacwi` tel quel. | Aucune possibilité de double ouverture, ordonnancement et priorités centralisés. |
| − | Repose sur la discipline de tous les appelants (d'où le module unique). | `bacwi` ouvre lui-même le port : il faudrait le passer derrière le propriétaire ou l'arrêter pendant l'usage. Plus de code, plus de latence. |

### 6.3 Séparation des boucles

`start_data_logger` est découpé en tâches indépendantes : enregistrement Modbus
(un thread par bus/passerelle), enregistrement BACnet, synchro flotte. Chaque
tâche a son propre intervalle, ses propres timeouts et son propre compteur de
santé. Le partage d'état SQLite reste via `get_db_connection` (`src/core/database.py`, mode WAL déjà activé).

## 7. Modèle de données proposé

### 7.1 Harmonisation avec le modèle BACnet (décision D3)

Aujourd'hui, les deux protocoles ont déjà une structure parallèle :

| Notion | BACnet (`schema.sql`) | Modbus (`schema.sql`) |
|---|---|---|
| Modèle d'appareil | `bacnet_templates` (versionné, partageable flotte) | `modbus_templates` (idem) |
| Appareil installé | `bacnet_devices` : `network_address` = « IP **ou** MAC MS/TP » | `modbus_devices` : `address` = « IP **ou** Slave ID » |
| Point suivi/enregistré | `bacnet_points` (`is_monitored`, `is_recorded`, `cadence`) | `modbus_points` (mêmes champs) |
| Nature du transport | **déduite** du format de l'adresse (IP ou MAC) | **explicite** : `protocol` (`tcp` / `mstp`) |
| Réglages du bus série | **hors base** : paramètres de session MS/TP et `data/bacnet_mstp_state.json` | **absents** |

Constat pour l'harmonisation : le transport Modbus ne peut pas être déduit de
l'adresse (TCP natif et RTU-over-TCP sont tous deux des IP), donc `protocol` reste
explicite mais reçoit les valeurs de EXG-60. Pour le reste, on **aligne les
conventions** : mêmes noms de colonnes de suivi, même logique modèle → appareil →
points, même identité de port (clé `by-id`).

Proposition :

- Une table `serial_buses`, **commune** aux deux protocoles pour le vocabulaire
  et l'arbitrage : clé stable du port, protocole affecté, réglages.
- `modbus_devices` reçoit `bus_id` (nullable : vide pour `tcp` et `rtu_over_tcp`,
  renseigné pour `rtu_serial`) ; `address` reste le Slave ID pour `rtu_serial`.
- **BACnet MS/TP reste inchangé en V1** (ses paramètres de session et son fichier
  d'état, cf. section 3.2) : il ne fait que lire la clé du port et l'affectation via
  le module d'arbitrage. Faire migrer ses réglages vers `serial_buses` est une
  évolution optionnelle (décision D10).
- Limite connue, hors V1 : `bacnet_points.network_address` (MAC MS/TP) n'est pas
  qualifié par un bus ; avec deux bus MS/TP, deux MAC identiques seraient ambiguës.

```sql
CREATE TABLE IF NOT EXISTS serial_buses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    hardware_key TEXT NOT NULL UNIQUE,   -- clé stable du port (cf. device_qualifications)
    assigned_protocol TEXT NOT NULL,     -- 'modbus_rtu' | 'bacnet_mstp'
    baud INTEGER NOT NULL DEFAULT 9600,
    parity TEXT NOT NULL DEFAULT 'N',    -- 'N' | 'E' | 'O'
    stopbits INTEGER NOT NULL DEFAULT 1, -- 1 | 2
    timeout_ms INTEGER,
    options_json TEXT,                   -- RTS, T3.5 spécifique, etc.
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
-- modbus_devices : + bus_id INTEGER NULL REFERENCES serial_buses(id)
-- modbus_devices.protocol : 'tcp' | 'rtu_over_tcp' | 'rtu_serial'   ('mstp' lu puis migré)
```

Le schéma exact reste à valider avec Marc à l'implémentation. Il doit respecter la
règle « persistance dans `data/`, configuration via `src/core/config.py` ».

### 7.2 Affectation port → protocole et réaffectation

Un port série a **un seul** protocole affecté à la fois (EXG-09). Raison : MS/TP et
Modbus RTU n'ont ni les mêmes réglages ni la même logique de maître (jeton vs
maître/esclave) et ne cohabitent pas sur un même segment électrique. En pratique,
passer un bus d'un protocole à l'autre est une **opération physique manuelle**
(retirer le bus, recâbler) : le logiciel l'accompagne par la réaffectation
explicite de EXG-10 et l'état « en pause » de EXG-11, il ne la déclenche jamais
tout seul. Le verrou reste nécessaire pour protéger contre la double ouverture
(outil de diagnostic, second processus, session oubliée).

### 7.3 Impact flotte

Ni `docs` ni l'app mobile ne dépendent aujourd'hui de `serial_buses`. Avant de
faire remonter ces réglages ou de changer la valeur de `protocol` dans les
payloads de synchro, vérifier `fleet.py` et l'API `docs` (`docs/integrations/FLEET_API_CHANGES.md`,
`docs/operations/DOCS_SERVER_ACCESS.md`). Le schéma MariaDB de `docs` n'est
**pas** modifié sans accord.

## 8. Tests et validation

- **Unitaires** (dans `tests/`, via `./run_tests.sh`) : acquisition/libération,
  timeout, détenteur affiché, libération à la mort du processus, priorité ;
  transport RTU contre un **port série virtuel** (paire de pty) avec un esclave
  Modbus simulé ; CRC, trames tronquées, T3.5, exceptions applicatives.
- **Concurrence** : N threads et 2 processus qui contestent le même port ; aucune
  trame corrompue, aucun deadlock, refus propre au-delà du délai.
- **Non-régression MS/TP** : session sous verrou, cycle complet, arrêt propre,
  reprise après débranchement.
- **Réaffectation** : bus Modbus en lecture automatique, réaffecté à BACnet MS/TP : les lectures s'arrêtent avant le changement, les points passent « en pause » sans erreurs répétées, l'historique est intact, retour en arrière possible.
- **Migration** : base contenant des `modbus_devices.protocol = 'mstp'` migrée
  sans perte ; relance idempotente.
- **Terrain** (Moxa UPort 1150 + bus réel) : Modbus RTU lecture/écriture/scan ;
  MS/TP découverte ; tentative volontaire de double usage (doit être refusée avec
  le bon message). Reprendre les validations « bus sain » de
  `A_REFAIRE_AVEC_BACNET_MSTP_SAINT.md` (ancien projet).
- **Robustesse** : bus qui ne répond pas (timeouts) sans retarder la synchro
  flotte ; débranchement à chaud ; démarrage sans le port.
- **Règles du projet** : aucun chemin en dur (`paths.py`), aucune logique dans les
  templates HTML, SSE pour le temps réel, `./run_tests.sh` avant conclusion.

## 9. Décisions

### 9.1 Tranchées (2026-09-21)

| # | Décision | Conséquence dans ce document |
|---|---|---|
| D1 | Affectation stricte d'un port à un protocole. Le passage de Modbus RTU à BACnet MS/TP est **manuel** (recâblage) et doit **arrêter le rafraîchissement automatique** des points de l'ancien protocole. | EXG-09 à EXG-12, 7.2. |
| D2 | Pas d'avis de Marc : **option A retenue** (verrou partagé, module unique). L'option B n'est reconsidérée que si des collisions persistent. | Section 6. |
| D3 | Harmoniser le modèle Modbus avec celui du BACnet. | 7.1 : conventions alignées, BACnet inchangé en V1. |
| D4 | Nommer correctement les **trois** transports ; `rtu_over_tcp` a des contraintes de lenteur. | 5.2 bis (EXG-60 à EXG-64). |
| D9 | **Pas d'enregistrement Modbus RTU série pour l'instant** (aucun appareil branché). Outils interactifs d'abord. | EXG-25 différé, phase 6. |

### 9.2 Restent ouvertes

| # | Question | Recommandation de départ |
|---|---|---|
| D5 | Faire remonter les réglages de bus à `docs` (flotte) ? | Non en V1 ; local uniquement. |
| D6 | Préemption des enregistrements par les outils interactifs (EXG-07) : oui, et jusqu'à quelle durée ? | Oui, plafonnée (ordre de 30 s), à confirmer. |
| D7 | Le démon BACnet devient-il une unité systemd propre (EXG-44) dans cette V1 ? | Oui, si l'on veut supprimer les `pkill -9` ; sinon reporter en phase 5. |
| D8 | Watchdog systemd sur `rpinode.service` (EXG-43) ? | À décider après la séparation des boucles. |
| D10 | Les réglages de session BACnet MS/TP migrent-ils un jour vers `serial_buses` ? | Non en V1 ; à reconsidérer une fois le Modbus RTU stabilisé. |

## 10. Phasage proposé

| Phase | Contenu | Critère d'acceptation |
|---|---|---|
| 1 | Module d'arbitrage `serial_bus.py` + MS/TP sous verrou. Aucun changement fonctionnel visible hors état des ports. | Tests de concurrence verts ; session MS/TP inchangée ; `/devices` affiche l'occupation. |
| 2 | Modbus RTU série direct, **outils interactifs uniquement** (lecture, écriture, scan, recherche brute), réglages série, mémoire des derniers. Nommage des trois transports. | Lecture/écriture/scan sur port virtuel ; le logger ignore `rtu_serial` avec un message clair. |
| 3 | Modèle de données (`serial_buses`, `protocol` clarifié, migration) + affectation et **réaffectation** (EXG-10 à EXG-12). | Migration idempotente ; changer l'affectation arrête proprement les lectures automatiques de l'ancien protocole. |
| 4 | Séparation des boucles du logger ; timeouts ; calibrage des passerelles `rtu_over_tcp` (EXG-61, EXG-62 après mesure) ; compteurs de santé. | Un bus arrêté ou lent ne retarde ni le BACnet ni la synchro flotte (test chronométré). |
| 5 | Démon BACnet en unité systemd ; watchdog (si D7/D8). Points MS/TP hors V1. | Plus de `pkill -9` dans `run.sh` ; blocage simulé détecté. |
| 6 | **Conditionnelle** : enregistrement Modbus RTU série (EXG-25), quand un appareil réel est branché. | Validation terrain sur le Moxa avec un appareil réel ; historique alimenté. |

Chaque phase est livrée séparément, testée (`./run_tests.sh`), puis validée sur
le boîtier via `./run.sh` avant la suivante.

## 11. Risques

- **Régression MS/TP** : c'est la partie la plus aboutie ; toute modification doit garder le comportement de la section 3.2 (tests de non-régression avant/après).
- **Bus réel indisponible pour valider** : les deux chantiers de l'ancien projet avaient un défaut d'installation (`A_REFAIRE_AVEC_BACNET_MSTP_SAINT.md`). Prévoir un banc avec port virtuel et, si possible, un bus sain.
- **Verrou mal utilisé** : la discipline repose sur un point d'entrée unique ; interdire l'ouverture directe de `serial.Serial` hors de `serial_bus.py` (contrôle dans les tests).
- **Lectures automatiques oubliées** : après une réaffectation, un rafraîchissement du navigateur ou un thread de lecture qui continue de sonder un bus repris par l'autre protocole. À couvrir par EXG-10/EXG-11 et par un test dédié.
- **Sens RS-485** : dépend de l'adaptateur ; à qualifier sur chaque modèle réellement déployé.
- **Impact flotte** : un seul boîtier est déployé aujourd'hui, ce qui limite le risque, mais toute modification de payload de synchro doit rester rétro-compatible pour le jour où d'autres boîtiers seront ajoutés.

## 12. Références

- Constats et code : `src/services/bacnet_mstp.py`, `src/services/modbus_tools.py`, `src/services/logger.py`, `src/services/device_mgr.py`, `src/core/schema.sql`, `templates/modbus_tools.html`, `templates/modbus_devices.html`.
- Matériel : `drivers/moxa/README.md`, `drivers/mstp/README.md`, `src/core/devices.md`.
- Incident lié : `docs/incidents/INVESTIGATION_BACNET_DAEMON.md`.
- Ancien projet (GitHub `mfache/boitier-telemaintenance`, branche `refactor/web-admin`) : `webadmin/modbus.py`, `webadmin/bacnet_mstp.py`, `webadmin/usbdev.py`, `modbus_recorder.py`, `NOTES-evolutions.md`, `A_REFAIRE_AVEC_BACNET_MSTP_SAINT.md`.
