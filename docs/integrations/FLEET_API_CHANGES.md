# Modifications apportées au serveur `docs` (hors dépôt Git)

Le code de l'API centrale (`/var/www/reports/api.py` sur le serveur `docs`) n'est **pas**
versionné avec Git — seules des sauvegardes horodatées (`api.py.bak_*`) existent sur
place. Ce fichier documente les modifications apportées côté serveur pour garder une
trace côté `rpinode` de ce qui a changé hors dépôt.

## 1. Support du gzip sur `/sync`

`/sync` ne décompressait pas les corps de requête en gzip (contrairement à `/logs`),
et Bottle refuse toute requête non compressée dépassant `MEMFILE_MAX` (100 Ko) avec une
erreur 413. Ajout du même pattern de décompression que `/logs` :

```python
if request.headers.get('Content-Encoding') == 'gzip':
    try:
        data = json.loads(gzip.decompress(request.body.read()).decode('utf-8'))
    except Exception:
        return json_error(400, "Erreur de décompression GZIP")
else:
    data = request.json or {}
```

## 2. Dictionnaire de points BACnet (`bacnet_points_catalog`)

- Nouvelle table `chantier_bacnet_points` (chantier_id, network_address, device_instance,
  object_id, object_name, updated_by), clé unique `(chantier_id, device_instance, object_id)`.
- Nouvelle fonction `_push_bacnet_points_catalog(cur, boitier_id, hostname, points)`,
  enregistrée dans la liste `handlers` de `sync()` sous la clé `"bacnet_points_catalog"`.
- Chaque point envoyé porte son propre `chantier_id` (le boîtier peut changer de chantier
  entre deux lots), donc on ne retombe jamais sur le chantier du contexte de la requête.
- Côté rpinode, `fleet.sync_bacnet_points_catalog()` envoie ce payload compressé en gzip,
  par lots de 8000 points (voir `services/bacnet_catalog.py`).

## 3. Partage de templates BACnet (`bacnet_templates`)

Ce mécanisme existait déjà côté client (`fleet.py` : `sync_bacnet_templates` /
`get_remote_bacnet_templates`) mais **n'était jamais persisté ni renvoyé côté serveur** —
la clé était silencieusement ignorée par `/sync`. Ajout du même schéma que
`modbus_templates` :

- Nouvelle table `boitier_bacnet_templates` (copie conforme de `boitier_modbus_templates` :
  template_uuid, revision_uuid, parent_revision_uuid, name, manufacturer, version,
  definition_json, created_by_node, is_deprecated).
- Nouvelle fonction `_push_bacnet_templates(cur, boitier_id, boitier_hostname, templates)`,
  enregistrée dans `handlers` sous la clé `"bacnet_templates"`.
- La réponse de `/sync` inclut désormais `"bacnet_templates": {...}` (même requête SQL
  "dernière version non dépréciée par template_uuid" que pour Modbus).

## 4. Chantiers multi-antennes et documentation API (2026-09-07)

Le serveur stockait historiquement une seule antenne (`cell_mcc`, `cell_mnc`,
`cell_enodeb`) directement sur la table `chantiers`. Cela provoquait la création de
faux doublons (`H66-2`, `HORNU4-2`, etc.) dès qu'un même chantier était revu sur une
nouvelle antenne avec un `site_hint_name` déjà existant.

Modifications appliquées sur `docs` :

- Nouvelle table `chantier_antennes` pour gérer plusieurs eNodeB par chantier.
- Backfill automatique depuis les colonnes historiques de `chantiers`.
- Refactor de `_resolve_or_create_chantier(...)` pour :
  - privilégier une correspondance exacte par antenne ;
  - rattacher une nouvelle antenne à un chantier existant si `site_hint_name`
    correspond à un chantier réel ;
  - éviter la création de doublons `Nom-2` quand le nom existe déjà pour le même
    chantier métier.
- Ajout de l'endpoint `GET /chantier/<chantier_id:int>/antennes`.
- Enrichissement de `GET /chantiers` avec `antenna_count`.
- Mise à jour de `GET /usage` et passage de `api_version` à `1.1.0`.

Nettoyage des données effectué dans la foulée :

- `H66` distant (`id=9`) a été remis sur sa seule antenne valide : `403905`.
- L'ancien artefact `AUTO-403869` (`id=10`) a été supprimé après archivage préalable.
- Les faux doublons `HIECS-2` (`id=20`) et `HORNU4-2` (`id=17`) ont été supprimés.
- Les artefacts orphelins `AUTO-400246` (`id=12`) et `AUTO-123456` (`id=18`) ont aussi été supprimés.
- Les enregistrements associés aux chantiers `*-2` et aux `AUTO-*` orphelins ont été supprimés en même temps.
- `AUTO-900529` (`id=11`) a ensuite été supprimé après migration de ses données vers
  `HIECS` (`id=19`) et réalignement du `external_id` local de `HIECS` vers `19`.
- L'antenne historique erronée `24538` a finalement été retirée de `HIECS`, qui ne garde
  plus que `900529` côté serveur comme côté boîtier.

## 5. Enrôlement automatique Headscale (2026-09-08)

Nouveaux endpoints `POST /register/auto`, `POST /headscale/enroll` et
`POST /headscale/routes` (ce dernier resynchronise les routes approuvées à
chaque changement de chantier, pas seulement à l'enrôlement initial), plus
une colonne ajoutée à `boitier_registre` (`cpu_serial`). `API_VERSION` →
`1.2.0`.

Documenté en détail (schéma, séquence, commandes `headscale` utilisées,
validation) dans
[HEADSCALE_AUTO_ENROLL.md](HEADSCALE_AUTO_ENROLL.md) plutôt que dupliqué
ici, le sujet étant assez conséquent pour mériter son propre document.

## 6. Schéma pour l'app mobile — catégorie d'utilisateur, activation, restriction chantier (2026-09-19)

Première étape (schéma DB uniquement, aucune route `api.py` modifiée) du
chantier décrit dans
[`../mobile/CAHIER_DES_CHARGES_APP_MOBILE.md`](../mobile/CAHIER_DES_CHARGES_APP_MOBILE.md).

**Sauvegarde préalable** : `mysqldump --single-transaction --triggers` des
tables `utilisateurs`, `utilisateurs_emails`, `chantiers`, stockée dans
`/var/backups/docs-app/mobile_migration_backup_20260919_112937.sql`
(permissions `600 root:root`, même convention que le backup existant
décrit dans `../operations/DOCS_BACKUP_STATUS.md`).

**Colonnes ajoutées à `utilisateurs`** :

- `externe` (`TINYINT(1)`, défaut `0`) : compte externe / sous-traitant
  sans compte Deltathermic.
- `mobile_valide` (`TINYINT(1)`, défaut `0`) : provisionné et validé par
  un admin pour l'accès à l'application mobile (distinct des flags
  existants `adm`/`wrk`/`cas`/`rot`, dont la sémantique exacte n'a pas été
  documentée pendant le cadrage — voir point ouvert correspondant dans le
  cahier des charges mobile).

**Nouvelle table `utilisateurs_activation_mobile`** (clé d'activation à
usage unique envoyée par email) :

```sql
CREATE TABLE utilisateurs_activation_mobile (
  id INT(10) UNSIGNED NOT NULL AUTO_INCREMENT,
  utilisateur_id INT(10) UNSIGNED NOT NULL,
  cle_hash CHAR(64) NOT NULL,        -- sha256 hex, jamais la clé en clair
  expire_at DATETIME NOT NULL,
  utilisee_at DATETIME DEFAULT NULL,
  date_creation TIMESTAMP NOT NULL DEFAULT current_timestamp(),
  PRIMARY KEY (id),
  UNIQUE KEY cle_hash_UNIQUE (cle_hash),
  KEY utilisateur_id (utilisateur_id),
  CONSTRAINT fk_activation_mobile_utilisateur
    FOREIGN KEY (utilisateur_id) REFERENCES utilisateurs (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;
```

**Nouvelle table `utilisateurs_chantiers`** (restriction d'accès mobile par
chantier, utilisée notamment pour les sous-traitants) :

```sql
CREATE TABLE utilisateurs_chantiers (
  id INT(10) UNSIGNED NOT NULL AUTO_INCREMENT,
  utilisateur_id INT(10) UNSIGNED NOT NULL,
  chantier_id INT(10) UNSIGNED NOT NULL,
  date_creation TIMESTAMP NOT NULL DEFAULT current_timestamp(),
  PRIMARY KEY (id),
  UNIQUE KEY uniq_utilisateur_chantier (utilisateur_id, chantier_id),
  KEY chantier_id (chantier_id),
  CONSTRAINT fk_uc_utilisateur FOREIGN KEY (utilisateur_id) REFERENCES utilisateurs (id) ON DELETE CASCADE,
  CONSTRAINT fk_uc_chantier FOREIGN KEY (chantier_id) REFERENCES chantiers (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;
```

Aucune ligne existante modifiée (`externe`/`mobile_valide` par défaut à
`0` pour les 9 utilisateurs déjà en base). Aucun impact sur `api.py` ni sur
les endpoints existants — étape purement additive.

**Reste à faire** (voir cahier des charges mobile, section 5.3) : nouvelle
API dédiée mobile (émission des jetons, activation par clé, gestion des
listes blanches), extension du payload `/sync` pour pousser la liste
blanche par boîtier, et flux d'envoi d'email d'onboarding (relais SMTP
existant identifié : `ssmtp` configuré vers `smtp.office365.com`, expéditeur
`marc.fache@deltathermic.be`).

## 7. API mobile — activation, sessions, liste des boîtiers (2026-09-19)

Deuxième étape (suite de la section 6) : implémentation des routes pour
l'application mobile décrites dans
[`../mobile/CAHIER_DES_CHARGES_APP_MOBILE.md`](../mobile/CAHIER_DES_CHARGES_APP_MOBILE.md)
section 14.3.

**Découverte d'infrastructure importante** : `/var/www/reports/api.py`
n'existe plus tel quel. Le code a été refactoré le 10 septembre 2026
("sur le modèle rpinode") en trois espaces distincts sur le serveur
`docs` :

- `/opt/reports-dev` : bac à sable de développement, dépôt Git local
  (remote `github-reports-dev:mfache/reports-dev.git`), base isolée
  `dt_dev` (`/etc/boitier-fleet/db-dev.env`), servi sous `/reports-dev`.
- `/opt/docs-infra` : dépôt Git "officiel" versionné et poussé sur GitHub
  (`github-reports:mfache/docs-infra.git`), contient une copie
  synchronisée par `rsync` de `reports-dev`.
- `/var/www/reports` : déploiement de production servi sous `/reports`,
  jamais modifié directement.

Code applicatif désormais sous `src/` (comme `rpinode`) :
`src/core/`, `src/services/*.py` (un fichier par domaine métier, monté sur
`api_app` par effet de bord d'import), `src/web/api.py` (assemblage).
Voir `/opt/reports-dev/CAHIER-DES-CHARGES-REFONTE.md` sur le serveur pour
le détail de cette refonte (non versionné dans `rpinode`).

**Contrainte nginx découverte** : seul le préfixe littéral `/reports/api`
échappe à l'`auth_request` Google OAuth qui protège tout le reste de
`/reports`. Le préfixe `/mobile-api/` initialement prévu dans le cahier
des charges aurait donc été bloqué par une authentification interactive
Google — inutilisable pour les sous-traitants sans compte Deltathermic.
**Décision** : les routes mobiles vivent dans `src/services/mobile.py`,
un domaine de plus monté sur le même `api_app`, sous
`/reports/api/mobile/*`.

**Nouvelle table `utilisateurs_sessions_mobile`** (créée sur `dt` et
`dt_dev`, absente de la section 6 initiale) :

```sql
CREATE TABLE utilisateurs_sessions_mobile (
  id INT(10) UNSIGNED NOT NULL AUTO_INCREMENT,
  utilisateur_id INT(10) UNSIGNED NOT NULL,
  token_hash CHAR(64) NOT NULL,
  date_creation TIMESTAMP NOT NULL DEFAULT current_timestamp(),
  derniere_utilisation DATETIME DEFAULT NULL,
  revoque TINYINT(1) NOT NULL DEFAULT 0,
  PRIMARY KEY (id),
  UNIQUE KEY token_hash_UNIQUE (token_hash),
  KEY utilisateur_id (utilisateur_id),
  CONSTRAINT fk_sessions_mobile_utilisateur
    FOREIGN KEY (utilisateur_id) REFERENCES utilisateurs (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;
```

**Nouvelle table `utilisateurs_acces_boitier`** (jetons courte durée par
boîtier, lus plus tard par l'extension de `/sync`) :

```sql
CREATE TABLE utilisateurs_acces_boitier (
  id INT(10) UNSIGNED NOT NULL AUTO_INCREMENT,
  utilisateur_id INT(10) UNSIGNED NOT NULL,
  boitier_id INT(10) UNSIGNED NOT NULL,
  token_hash CHAR(64) NOT NULL,
  expire_at DATETIME NOT NULL,
  date_creation TIMESTAMP NOT NULL DEFAULT current_timestamp(),
  PRIMARY KEY (id),
  UNIQUE KEY token_hash_UNIQUE (token_hash),
  KEY utilisateur_id (utilisateur_id),
  KEY boitier_id (boitier_id),
  CONSTRAINT fk_ab_utilisateur FOREIGN KEY (utilisateur_id) REFERENCES utilisateurs (id) ON DELETE CASCADE,
  CONSTRAINT fk_ab_boitier FOREIGN KEY (boitier_id) REFERENCES boitier_registre (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;
```

**Code** : `src/services/mobile.py` (4 routes : `POST /mobile/activate`,
`GET /mobile/boitiers`, `POST /mobile/boitiers/<hostname>/access-token`,
`POST /mobile/logout`), réutilise `hash_token()` de `services/fleet.py`.
`API_VERSION` `1.2.1` → `1.3.0`.

**Sauvegardes avant déploiement** :
- Schéma : mêmes tables ajoutées sur `dt` et `dt_dev` en parallèle.
- Fichiers modifiés en production (`src/web/api.py`,
  `tests/test_wsgi_mount.py`) sauvegardés dans
  `/var/backups/docs-app/reports_prod_20260919/` avant écrasement.

**Déploiement** : développé et testé dans `/opt/reports-dev` (39/39 tests
pytest, `run.sh` : compilation + tests + `kill -HUP` ciblé), synchronisé
vers `/opt/docs-infra` (commit `79aaf30`, poussé sur
`github-reports:mfache/docs-infra.git`), puis copié vers
`/var/www/reports` et rechargé via son propre `run.sh` (39/39 tests,
reload ciblé).

**Point annexe** : l'environnement virtuel de production
(`/opt/venv/reports`) ne contenait pas `pytest`, contrairement à
`reports-dev` — installé (`pytest==9.1.1`) pour que le garde-fou de
`run.sh` fonctionne réellement en production, pas seulement en dev.

## 8. Interface admin — flags externe/mobile_valide et invitation email (2026-09-19)

Troisième étape : extension de la vue existante `/admin/utilisateurs`
(`src/web/admin_users.py` + `templates/admin_users.tpl`), qui gérait déjà
les flags de rôle `cas`/`adm`/`wrk`/`rot` (chargé d'affaires / admin /
ouvrier / root) sous forme de checkboxes et badges. Réponse au point
ouvert n°1 de la section 6 : ces flags existants n'ont pas été réutilisés
pour la catégorie mobile, les colonnes dédiées `externe`/`mobile_valide`
(section 6) le sont à la place, avec leurs propres checkboxes/badges
("Ext"/"M") ajoutés au même endroit.

**Nouveau module `src/services/email_sender.py`** : envoi d'email texte
simple via `smtplib`, configuration lue depuis les mêmes fichiers
`/etc/boitier-fleet/db.env` / `db-dev.env` que la base de données (clés
`SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM`,
`MOBILE_APP_PLAY_STORE_URL` ajoutées à ces deux fichiers, avec sauvegarde
préalable `*.bak_20260919_smtp`). Choix motivé par le fait que le process
uwsgi tourne en utilisateur `mariadb`, qui n'a pas accès à
`/etc/ssmtp/ssmtp.conf` (`root` uniquement) — réutilisation du mécanisme
`_ENV` existant plutôt que de toucher aux permissions système.

**Nouvelle route `POST /admin/utilisateurs/<id>/mobile-invite`** :
 génère une clé d'activation à usage unique (48h, table
`utilisateurs_activation_mobile` de la section 6), tente l'envoi par
email. Garde-fous : refuse si `mobile_valide=0` ou si l'utilisateur n'a
aucun email enregistré.

**Problème rencontré et non résolu à ce stade** : l'envoi échoue
systématiquement avec `535 5.7.3 Authentication unsuccessful` sur
`smtp.office365.com`, y compris après mise à jour du mot de passe par
Marc. Cause probable : authentification SMTP désactivée pour ce compte
côté Microsoft 365, ou MFA actif nécessitant un mot de passe
d'application dédié (à vérifier dans le centre d'administration M365).
Voir point ouvert correspondant dans le cahier des charges mobile.

**Repli implémenté en attendant** : si l'envoi échoue, la clé reste
valide en base et l'admin voit une modale avec le contenu complet de
l'invitation, copiable pour transmission manuelle (autre canal).

**Tests** : nouveau fichier `tests/test_admin_users_mobile.py` (3 tests :
création avec flags, refus d'invitation sans `mobile_valide`, génération
de clé avec succès ou échec SMTP géré proprement). Garde-fou
`test_nombre_de_routes_ui_inchange` : `37` → `38`.

**Déploiement** : même procédure que la section 7 (test dans
`reports-dev`, 42/42 tests, sauvegarde des fichiers de prod dans
`/var/backups/docs-app/reports_prod_20260919_admin/`, copie vers
`/var/www/reports`, rechargement via `run.sh`, archivage vers
`docs-infra` **depuis la production** (pas depuis `reports-dev`, pour
garder l'état archivé fidèle à ce qui tourne réellement), commit
`fb7ae89` poussé sur GitHub.

## 9. Libellé des points via le template partagé, sans duplication (2026-09-20)

Besoin : afficher un nom lisible (colonne « Description ») sur
`https://docs.deltathermic.be/reports/chantier/*` pour les points Modbus/BACnet
dont le template est partagé avec la flotte (`is_shared=1` côté `rpinode`),
sans dupliquer ce libellé (le point a déjà un nom hérité du registre/objet du
template, et le template lui-même est déjà synchronisé via
`sync_modbus_templates`/`sync_bacnet_templates`).

**Source de vérité retenue** : `boitier_template_usage` (associe déjà
`device_name` → `revision_uuid` par chantier, alimente par
`fleet.py::sync_location`) + la définition du template
(`boitier_modbus_templates`/`boitier_bacnet_templates`). Un point est
identifié par `(device, obj)` dans les trends ; `obj` encode déjà le
registre (`FC04_200` = fonction 4 / adresse 200) ou l'objet BACnet
(`analog-input:1`), ce qui suffit à retrouver le libellé correspondant
dans `definition_json` sans qu'aucune donnée supplémentaire ne soit
envoyée par le boîtier. Un endpoint `/points-config` +
table `boitier_points_config` existaient déjà pour porter un libellé
explicite par point (construit pour l'app mobile puis abandonné, voir
`docs/mobile/CAHIER_DES_CHARGES_APP_MOBILE.md` §14.1) : conservé tel
quel comme repli, mais plus alimenté par `rpinode` pour ce besoin.

**Constat** : `boitier_template_usage` ne couvrait jusqu'ici que Modbus
(le côté BACnet n'avait jamais été étendu). Ajout du support BACnet en
parallèle, avec un champ `protocol` pour distinguer les deux espaces de
noms (un appareil Modbus et un appareil BACnet peuvent théoriquement
partager le même nom sur un chantier).

**Migration de schéma** (`dt` et `dt_dev`) :

```sql
ALTER TABLE boitier_template_usage
  ADD COLUMN protocol VARCHAR(16) NOT NULL DEFAULT 'modbus' AFTER chantier_id;
ALTER TABLE boitier_template_usage
  DROP PRIMARY KEY,
  ADD PRIMARY KEY (boitier_id, chantier_id, protocol, template_uuid, device_name);
```

Note : juste après le premier `ALTER ADD COLUMN` combiné avec le
changement de clé primaire en une seule commande, une lecture immediate
de la colonne via une connexion pymysql distincte a échoué ("Unknown
column 'protocol'") alors que `SHOW CREATE TABLE` via le client `mysql`
la montrait déjà — très probablement un délai de propagation du cache de
métadonnées InnoDB entre connexions. Résolu en séparant l'ajout de
colonne et le changement de clé primaire en deux commandes espacées de
quelques secondes, avec vérification croisée (mysql-cli + pymysql) entre
les deux avant de continuer.

**Côté `rpinode`** : `fleet.py::sync_location` pousse désormais l'usage
de template pour les appareils BACnet en plus des appareils Modbus, avec
`protocol`. Pour BACnet, la clé `device_name` transmise est en réalité le
`device_instance` (entier converti en chaîne), car c'est ce identifiant
— et non le nom convivial — qui sert de `d` dans `/trends` pour ce
protocole (voir `services/logger.py::run_bacnet_logging_cycle`).

**Côté serveur** (`src/services/sync.py`, `src/web/ui.py`) :

- `_push_template_usage` accepte et persiste le champ `protocol`
  (`"modbus"` par défaut pour les boîtiers pas encore mis à jour).
- Nouvelles fonctions `_load_template_point_labels` et
  `_resolve_point_label` dans `ui.py` : résolvent le libellé d'un point à
  partir de `boitier_template_usage` + `definition_json` du template
  (reparsing de `FC04_200` en `(fonction, adresse)` pour Modbus,
  correspondance directe sur `obj` pour BACnet).
- Utilisées dans `chantier_boitiers` (tableau des points) et
  `chantier_chart_data` (graphiques), en complément de
  `boitier_points_config` (qui reste prioritaire s'il contient déjà un
  libellé, pour compatibilité future).

**Déploiement** : testé dans `reports-dev`/`dt_dev` (script fonctionnel
de bout en bout incluant Modbus et BACnet, puis `run_tests.sh` 42/42),
puis migration appliquée sur `dt` (11 lignes, sans risque), rsync
`/opt/reports-dev` → `/opt/docs-infra/var/www/reports` (commit
`824e1cb`, poussé sur GitHub), copie vers `/var/www/reports`, rechargement
via `run.sh` (exécuté en tant qu'utilisateur `marc`, pas `mariadb` :
`__pycache__` appartient à `marc` et n'est pas inscriptible par
`mariadb`). Vérifié en conditions réelles après coup : nouveaux PID de
workers uwsgi correspondant à l'heure du rechargement, requête réelle
`GET /chantier/9/boitiers` renvoyant `200` sans trace d'erreur dans
`/var/log/uwsgi/app/reports.log`.

## Validation effectuée

- Compression gzip testée : ~20x de réduction sur un lot réaliste de points BACnet.
- Persistance vérifiée directement en base MySQL après synchronisation.
- Cycle complet testé : partage d'un template BACnet local → apparition dans
  `boitier_bacnet_templates` → récupération via `get_templates_overview()` avec
  `is_installed: true` et bonne détection de version.
- `GET https://docs.deltathermic.be/reports/api/version` retourne maintenant `1.1.0`.
- `GET /chantiers` expose `antenna_count`.
- `GET /chantier/9/antennes` retourne bien l'unique eNodeB valide de `H66` (`403905`).
- Les endpoints `GET /chantier/19/antennes` et `GET /chantier/16/antennes` ne
  retournent plus de chantiers `*-2` distincts.
- `GET /chantiers` ne contient plus aucun `AUTO-*` actif.
- `GET /chantier/19/antennes` retourne désormais uniquement `900529` pour `HIECS`.

## Point d'attention

Le service tourne sous uWSGI, lancé manuellement (`--daemonize`), pas via systemd pour
l'app elle-même. En pratique :

- `systemctl reload` demande une authentification interactive et n'est pas exploitable
  depuis une session SSH non privilégiée ;
- `kill -HUP <pid_master>` peut relancer les workers sans forcément recharger le code
  Python préchargé par le master ;
- la procédure fiable observée est donc :
  1. `pkill -f '/etc/uwsgi/apps-enabled/reports.ini'`
  2. `/usr/bin/uwsgi --ini /usr/share/uwsgi/conf/default.ini --ini /etc/uwsgi/apps-enabled/reports.ini --daemonize /var/log/uwsgi/app/reports.log`

La version effective se vérifie ensuite via `GET /reports/api/version`.
