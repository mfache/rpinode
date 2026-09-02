# Incident du 02/09/2026 — Perte de code non commité et récupération

## Cause
Un `git reset --hard HEAD` a été exécuté par erreur pendant un dépannage, annulant
tout le travail non commité présent dans les fichiers déjà suivis par Git.

## Fichiers reconstruits (avec confiance élevée)
- `src/services/bacnet_mgr.py` — reconstruit depuis le bytecode `.pyc` compilé
  (sauvegardé dans `/tmp` avant la casse) + calqué sur la structure jumelle de
  `modbus_mgr.py`. Fonctions restaurées : `get_template`, `delete_template`,
  `normalize_fleet_definition_to_local`, `format_local_template_for_fleet`,
  `import_template_from_fleet`, `share_template_to_fleet`, `get_templates_overview`,
  `delete_device_from_site`.
- `src/services/fleet.py` — ajout de `get_remote_bacnet_templates()` et
  `sync_bacnet_templates()` (mirroir des méthodes Modbus existantes).
- `src/web/server.py` — routes et vues `serve_bacnet_devices`, `serve_bacnet_templates`,
  `serve_bacnet_tools`, `handle_bacnet_tools_discover`, `handle_bacnet_tools_whohas`,
  `handle_bacnet_device_delete`, `handle_bacnet_template_delete`,
  `handle_bacnet_template_import_from_fleet`, `handle_bacnet_template_share_to_fleet`.
  Reconstruites en suivant fidèlement le pattern déjà utilisé pour Modbus dans ce
  même fichier.
- `src/main.py` — relance du sous-processus `bacnet_daemon.py` (nécessaire au
  fonctionnement de Who-Has/Discover), avec nettoyage `atexit` pour éviter
  l'accumulation de processus orphelins au redémarrage.

## Fichiers dont le contenu non commité est DÉFINITIVEMENT PERDU
(aucune sauvegarde disponible ; contenu exact inconnu)
- `src/core/config.py`
- `src/core/database.py`
- `src/core/schema.sql`
- `src/services/ipscan.py` (dont probablement : colonne/table BACnet pour le scan IP,
  bouton "BACnet Who-Is" passif)
- `src/services/logger.py`
- `src/services/mqtt_service.py`
- `src/services/network_config.py`
- `src/services/tracker.py`
- `src/services/wifi_mgr.py`
- `src/web/stream.py`
- `static/app.js`
- `templates/ip_scan.html` (bouton "📡 BACnet Who-Is" et JS associé)
- `templates/modbus_suivi.html`
- `templates/trends.html`

Si l'un de ces fichiers contenait un correctif important (bug fix, nouvelle
fonctionnalité), il faudra le refaire manuellement — je n'ai aucun moyen de
retrouver son contenu exact.

## Ce qui a été validé après réparation
- Toutes les pages principales répondent en HTTP 200 (`/`, `/bacnet/devices`,
  `/bacnet/templates`, `/bacnet/tools`, `/modbus/devices`, `/modbus/templates`,
  `/scan/ip`, `/monitor/suivi`, `/network/overview`, `/configuration/logger`).
- Le démon `bacnet_daemon.py` démarre une seule fois par redémarrage du service
  (pas de doublon de processus).
- L'outil "Who-Has" (`/api/bacnet/tools/whohas`) fonctionne de bout en bout
  (HTTP → MQTT → bacpypes3 → réponse).
