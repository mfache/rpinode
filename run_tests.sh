#!/bin/bash

# Change to the directory where the script is located
cd "$(dirname "$0")"

# Script pour exécuter les tests de bon fonctionnement de rpinode

# Définition du PYTHONPATH pour inclure le dossier src
export PYTHONPATH=$PYTHONPATH:$(pwd)/src

# Isolation du fichier de session de chantier (voir core/paths.py) : sans ça,
# des tests qui appellent services.presence.label_current_location(...)
# écriraient dans le VRAI fichier utilisé en production par le tracker pour
# savoir sur quel chantier se trouve le boîtier, ce qui pourrait polluer des
# enregistrements en cours si les tests sont lancés sur un boîtier en service.
export RPINODE_CURRENT_SITE_FILE="$(mktemp -u /tmp/rpinode_test_current_site.XXXXXX.json)"
cleanup_test_site_file() {
    rm -f "$RPINODE_CURRENT_SITE_FILE"
}
trap cleanup_test_site_file EXIT

echo "--- Démarrage des tests de rpinode ---"

# Exécution des tests via unittest
python3 -m unittest discover tests

if [ $? -eq 0 ]; then
    echo ""
    echo "✅ Tous les tests sont passés avec succès !"
else
    echo ""
    echo "❌ Certains tests ont échoué."
    exit 1
fi
