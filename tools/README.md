# Outils annexes de `rpinode`

Ce dossier regroupe les scripts qui ne font pas partie du runtime principal de
l’application.

## Sous-dossiers

- `migrations/` : scripts de migration ou d’import ponctuel
- `debug/` : scripts de diagnostic et d’observation locale
- `patches/` : scripts temporaires de patch ou de correction ciblée
- `local/` : essais locaux non versionnés (ignorés par Git)

## Règle pratique

Si un script n’est pas appelé par `src/main.py`, `run.sh` ou `run_tests.sh`, il a
probablement sa place ici plutôt qu’à la racine du dépôt.
