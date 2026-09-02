# Modifications apportées au serveur `docs` (hors dépôt Git)

Le code de l'API centrale (`/var/www/reports/api.py` sur le serveur `docs`) n'est **pas**
versionné avec Git — seules des sauvegardes horodatées (`api.py.bak_*`) existent sur
place. Ce fichier documente les modifications apportées le 2026-09-02, pour garder une
trace côté rpinode de ce qui a changé côté serveur.

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

## Validation effectuée

- Compression gzip testée : ~20x de réduction sur un lot réaliste de points BACnet.
- Persistance vérifiée directement en base MySQL après synchronisation.
- Cycle complet testé : partage d'un template BACnet local → apparition dans
  `boitier_bacnet_templates` → récupération via `get_templates_overview()` avec
  `is_installed: true` et bonne détection de version.

## Point d'attention

Le service tourne sous uWSGI, lancé manuellement (`--daemonize`), pas via systemd pour
l'app elle-même. Pour appliquer un changement de code : `kill -HUP <pid_master>` (trouver
le PID avec `ps aux | grep reports.ini`), ce qui recharge les workers sans interruption.
