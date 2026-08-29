#!/bin/bash
cd /home/marc/rpinode

echo "--- 🔍 Vérification des tests avant redémarrage ---"
./run_tests.sh
if [ $? -ne 0 ]; then
    echo "❌ Échec des tests. Le redémarrage est annulé pour préserver la version stable."
    exit 1
fi

echo "--- 🚀 Redémarrage du serveur ---"
export PYTHONPATH=src
pkill -9 -f "src/main.py" || true
sleep 1
nohup sudo python3 -u src/main.py > log/stdout.log 2>&1 &

echo "✅ Serveur redémarré avec succès en arrière-plan."
