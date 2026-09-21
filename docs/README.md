# Documentation de `rpinode`

Ce dossier centralise les points d’entrée documentaires du projet.
L’objectif n’est pas de dupliquer la documentation existante, mais de la rendre
plus facile à retrouver.

## Démarrage rapide

- Vue d’ensemble du projet : [`../README.md`](../README.md)
- Notes pratiques et avancement : [`../HOWTO.md`](../HOWTO.md)
- Index technique proche du code `core` : [`../src/core/README.md`](../src/core/README.md)
- Index technique proche du code `services` : [`../src/services/README.md`](../src/services/README.md)

## Documentation d’exploitation et d’intégration

- Exploitation et interventions serveur : [`operations/DOCS_SERVER_ACCESS.md`](operations/DOCS_SERVER_ACCESS.md)
- Cartographie d’architecture répartie `rpinode` / `docs` (Démons, MQTT, SSE, PWA) : [`integrations/ARCHITECTURE_REPARTIE_RPINODE_DOCS.md`](integrations/ARCHITECTURE_REPARTIE_RPINODE_DOCS.md)
- Intégrations et changements d’API flotte : [`integrations/FLEET_API_CHANGES.md`](integrations/FLEET_API_CHANGES.md)
- Enrôlement automatique Headscale (identité + réseau) : [`integrations/HEADSCALE_AUTO_ENROLL.md`](integrations/HEADSCALE_AUTO_ENROLL.md)
- Migration Headscale (historique) : [`operations/HEADSCALE_MIGRATION_STATUS.md`](operations/HEADSCALE_MIGRATION_STATUS.md)
- CA interne "Deltathermic" et HTTPS des boîtiers : [`operations/INTERNAL_CA_TLS.md`](operations/INTERNAL_CA_TLS.md)

## Projets

- Cahier des charges de l'application mobile native (Android) : [`mobile/CAHIER_DES_CHARGES_APP_MOBILE.md`](mobile/CAHIER_DES_CHARGES_APP_MOBILE.md)
- Cahier des charges de l'arbitrage des bus série (RS-485) et du Modbus RTU direct : [`serie/CAHIER_DES_CHARGES_ACCES_SERIE.md`](serie/CAHIER_DES_CHARGES_ACCES_SERIE.md)

## Incidents et investigations

- Conflit de processus et démon BACnet : [`incidents/INVESTIGATION_BACNET_DAEMON.md`](incidents/INVESTIGATION_BACNET_DAEMON.md)
- Investigation synchronisation flotte : [`incidents/INVESTIGATION_SYNC.md`](incidents/INVESTIGATION_SYNC.md)
- Récupération après perte de code non commité : [`incidents/RECOVERY_NOTES.md`](incidents/RECOVERY_NOTES.md)

## Règle de rangement proposée

- La documentation transverse, d’exploitation, d’intégration et d’incident va dans `docs/`.
- La documentation très technique et liée à un sous-système reste proche du code dans `src/...`.
- Lorsqu’un sujet concerne les deux, on garde une source principale et on ajoute des liens, pas une copie.
