# Index technique `src/core`

Ce fichier sert d’aiguillage pour la documentation liée au noyau du projet.
La logique est de garder ici les documents proches des modules qu’ils décrivent,
plutôt que de les recopier ailleurs.

## Modules clés

- `paths.py` : calcul centralisé des chemins du projet
- `config.py` : chargement et sauvegarde de la configuration
- `database.py` : accès SQLite et initialisation du schéma
- `schema.sql` : schéma de base de données
- `utils.py`, `sys.py` : utilitaires transverses

## Documentation locale

- Localisation — notes : [`localisation-notes.md`](localisation-notes.md)
- Chantiers et données : [`chantiers-donnees.md`](chantiers-donnees.md)
- Périphériques : [`devices.md`](devices.md)
- Localisation — stratégie : [`localisation.md`](localisation.md)
- Réseau : [`network.md`](network.md)
- Table et colonnes : [`table-engine.md`](table-engine.md)
- Templates : [`templates.md`](templates.md)
- Wi‑Fi : [`wifi.md`](wifi.md)

## Voir aussi

- Index documentaire global : [`../../docs/README.md`](../../docs/README.md)
- Vue d’ensemble du projet : [`../../README.md`](../../README.md)
