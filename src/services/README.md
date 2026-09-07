# Index technique `src/services`

Ce fichier référence les services métier du projet et la documentation qui leur
est associée, afin de garder l’information près du code sans multiplier les
copies.

## Documentation locale

- SSE et mises à jour temps réel : [`README_sse.md`](README_sse.md)

## Sous-systèmes principaux

### BACnet

- `bacnet_daemon.py`
- `bacnet_mgr.py`
- `bacnet_mstp.py`
- `bacnet_reader.py`
- `bacnet_catalog.py`

### Modbus

- `modbus_mgr.py`
- `modbus_tools.py`

### Réseau et présence

- `network.py`
- `network_config.py`
- `wifi_mgr.py`
- `ipscan.py`
- `presence.py`
- `gsm.py`

### Synchronisation et remontées distantes

- `fleet.py`
- `reporter.py`
- `remote_log.py`
- `tracker.py`
- `logger.py`
- `mqtt_service.py`

### Inventaire matériel

- `device_mgr.py`

## Voir aussi

- Index documentaire global : [`../../docs/README.md`](../../docs/README.md)
- Documentation d’accès au serveur `docs` : [`../../docs/operations/DOCS_SERVER_ACCESS.md`](../../docs/operations/DOCS_SERVER_ACCESS.md)
- Changements d’API flotte : [`../../docs/integrations/FLEET_API_CHANGES.md`](../../docs/integrations/FLEET_API_CHANGES.md)
- Vue d’ensemble du projet : [`../../README.md`](../../README.md)
