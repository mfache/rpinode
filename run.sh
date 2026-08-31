#!/bin/bash
# Désactive le job control pour éviter les messages système parasites
set +m

cd /home/marc/rpinode

# Couleurs
GREEN='\033[0;32m'
BLUE='\033[0;34m'
RED='\033[0;31m'
NC='\033[0m'

function step() {
    printf "${BLUE}  > %s...${NC}" "$1"
    step_start=$(date +%s%3N)
}

function step_done() {
    step_end=$(date +%s%3N)
    diff=$((step_end - step_start))
    printf " ${GREEN}OK${NC} (%dms)\n" "$diff"
}

total_start=$(date +%s%3N)

# Utilisation de %s pour éviter que "---" ne soit pris pour une option
printf "%s\n" "--- 🚀 ${BLUE}Libération des ressources${NC} ---"
step "Arrêt du serveur"
export PYTHONPATH=src
# On lance le pkill et on attend un peu pour éviter que le message de fin ne pollue l'affichage
sudo pkill -9 -f "src/main.py" > /dev/null 2>&1
sleep 0.5
step_done

printf "%s\n" "--- 🔍 ${BLUE}Vérification des tests${NC} ---"
step "Exécution des tests"
TEST_OUT=$(./run_tests.sh 2>&1 | grep "Ran [0-9]\+ tests")
if [ ${PIPESTATUS[0]} -ne 0 ]; then
    printf "\n❌ ${RED}Échec des tests !${NC}\n"
    exit 1
fi
printf " %s" "$TEST_OUT"
step_done

printf "%s\n" "--- ⚙️  ${BLUE}Initialisation${NC} ---"
step "Configuration environnement"
sudo mkdir -p /tmp/rpinode/log
sudo chmod 777 /tmp/rpinode/log
step_done

step "Lancement serveur"
nohup sudo python3 -u src/main.py > /tmp/rpinode/log/stdout.log 2>&1 &
step_done

total_end=$(date +%s%3N)
total_diff=$((total_end - total_start))
printf "\n✅ ${GREEN}Serveur opérationnel !${NC} (%dms total)\n" "$total_diff"
