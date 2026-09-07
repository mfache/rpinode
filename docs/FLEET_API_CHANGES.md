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
