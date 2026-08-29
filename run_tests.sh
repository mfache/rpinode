#!/bin/bash

# Script pour exécuter les tests de bon fonctionnement de rpinode

# Définition du PYTHONPATH pour inclure le dossier src
export PYTHONPATH=$PYTHONPATH:$(pwd)/src

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
