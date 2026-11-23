#!/bin/bash
# Déploie rpinode sur un nouveau boîtier, depuis la VM de dev.
#
# Usage : deploy/deployer.sh CIBLE [--source rpi01] [--lan eth0|br0] [--etape N]
#   CIBLE   alias ou adresse SSH du boîtier, avec un compte sudo sans mot de
#           passe (au départ « pi » sur une image neuve ; « marc » ensuite)
#   --source  boîtier de référence d'où viennent les secrets de flotte et le
#             superviseur compilé (défaut rpi01)
#   --lan     interface du LAN filaire du chantier (br0 : prises pontées EDATEC)
#   --etape   reprendre à l'étape N (1 à 7) après une interruption
#
# Étapes :
#   1. vérifications (accès, Internet sur la cible)
#   2. kit : dépôt (git bundle), superviseur, clé SSH, scripts -> /tmp/rpinode-deploy
#   3. secrets de flotte copiés de la source vers /etc/rpinode, sans affichage
#   4. ancienne identité Tailscale effacée (cible + nœud headscale sur docs)
#   5. installer.sh sur la cible
#   6. attente de l'enrôlement automatique (nom rpiNN attribué par docs), finaliser.sh
#   7. SSH par clé uniquement et pare-feu, confirmés après vérification de l'accès
# Voir deploy/README.md.
set -euo pipefail

DEPOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CIBLE=""
SOURCE=rpi01
LAN=eth0
ETAPE=1
while [ $# -gt 0 ]; do
    case "$1" in
        --source) SOURCE="$2"; shift 2 ;;
        --lan) LAN="$2"; shift 2 ;;
        --etape) ETAPE="$2"; shift 2 ;;
        -*) echo "Option inconnue : $1" >&2; exit 2 ;;
        *) CIBLE="$1"; shift ;;
    esac
done
[ -n "$CIBLE" ] || { sed -n '2,20p' "$0"; exit 2; }

SSH="ssh -o BatchMode=yes -o ConnectTimeout=15"
KIT_DISTANT=/tmp/rpinode-deploy
etape() { echo; echo "=== Étape $1 : $2"; }
cible() { $SSH "$CIBLE" "$@"; }
faire() { [ "$ETAPE" -le "$1" ]; }

if faire 1; then
    etape 1 "vérifications"
    cible 'echo "cible : $(hostname), $(. /etc/os-release; echo $PRETTY_NAME), $(tr -d "\0" < /proc/device-tree/model)"; sudo -n true'
    cible 'curl -fsS -o /dev/null -m 15 https://deb.debian.org/ && echo "Internet : OK"' \
        || { echo "La cible n'a pas Internet (Wi-Fi RPIRESCUE, LAN…) : impossible d'installer." >&2; exit 1; }
    $SSH "$SOURCE" 'test -r /etc/rpinode/fleet.env && echo "source $(hostname) : OK"'
    git -C "$DEPOT" diff --quiet HEAD || echo "Attention : modifications non commitées dans $DEPOT (non déployées)."
fi

if faire 2; then
    etape 2 "kit de déploiement"
    T=$(mktemp -d)
    trap 'rm -rf "$T"' EXIT
    mkdir "$T/kit"
    cp -r "$DEPOT/deploy/." "$T/kit/"
    git -C "$DEPOT" bundle create -q "$T/kit/rpinode.bundle" HEAD main
    scp -q "$SOURCE:rpinode/supervisor/target/release/rpinode-supervisor" "$T/kit/rpinode-supervisor"
    cp ~/.ssh/id_ed25519.pub "$T/kit/cle_admin.pub"
    tar -C "$T" -czf "$T/kit.tgz" kit
    scp -q "$T/kit.tgz" "$CIBLE:/tmp/rpinode-deploy.tgz"
    cible "rm -rf $KIT_DISTANT && mkdir -p $KIT_DISTANT && tar -xzf /tmp/rpinode-deploy.tgz -C $KIT_DISTANT --strip-components=1 2>/dev/null; rm -f /tmp/rpinode-deploy.tgz; ls $KIT_DISTANT | tr '\n' ' '; echo"
fi

if faire 3; then
    etape 3 "secrets de flotte ($SOURCE -> cible, sans affichage)"
    $SSH "$SOURCE" 'sudo tar -C /etc/rpinode -czf - fleet.env notify.env tls' \
        | cible 'sudo install -d -m 755 /etc/rpinode && sudo tar -C /etc/rpinode -xzf - \
                 && sudo chmod 600 /etc/rpinode/fleet.env /etc/rpinode/notify.env /etc/rpinode/tls/wildcard.dt.net.key \
                 && sudo sed -i "s/^RPINODE_NAME=.*/RPINODE_NAME=nouveau-boitier/" /etc/rpinode/notify.env \
                 && sudo ls /etc/rpinode /etc/rpinode/tls | tr "\n" " "; echo'
fi

if faire 4; then
    etape 4 "ancienne identité Tailscale"
    ANCIEN=$(cible 'tailscale status --json 2>/dev/null | python3 -c "import json,sys; d=json.load(sys.stdin); print(d[\"Self\"][\"HostName\"] if d.get(\"BackendState\")==\"Running\" else \"\")" 2>/dev/null' || true)
    if [ -n "$ANCIEN" ]; then
        echo "La cible est connectée sous le nom « $ANCIEN » : déconnexion."
        cible 'sudo tailscale logout' || true
        ID=$(ssh docs "sudo headscale nodes list -o json" | python3 -c "
import json, sys
for n in json.load(sys.stdin):
    if n.get('given_name') == '$ANCIEN' or n.get('name') == '$ANCIEN':
        print(n['id'])" | head -1)
        if [ -n "$ID" ]; then
            echo "Suppression du nœud headscale $ID ($ANCIEN)."
            ssh docs "sudo headscale nodes delete -i $ID --force" >/dev/null
        fi
    else
        echo "Aucune identité Tailscale active."
    fi
fi

if faire 5; then
    etape 5 "installation"
    cible "sudo $KIT_DISTANT/installer.sh --lan $LAN"
fi

if faire 6; then
    etape 6 "enrôlement automatique (jusqu'à 10 minutes)"
    NOM=""
    for _ in $(seq 1 60); do
        NOM=$(cible "python3 -c \"import json; print(json.load(open('/home/marc/rpinode/data/config.json')).get('fleet_assigned_hostname') or '')\"" 2>/dev/null || true)
        TS=$(cible 'tailscale ip -4 2>/dev/null' || true)
        [ -n "$NOM" ] && [ -n "$TS" ] && break
        sleep 10
    done
    [ -n "$NOM" ] && [ -n "$TS" ] || { echo "Pas d'enrôlement après 10 minutes : voir journalctl -u rpinode sur la cible." >&2; exit 1; }
    echo "Enrôlé : $NOM, Tailscale $TS"
    cible "sudo $KIT_DISTANT/finaliser.sh"
fi

if faire 7; then
    etape 7 "SSH par clé uniquement et pare-feu"
    TS=$(cible 'tailscale ip -4')
    cible 'sudo /home/marc/rpinode/drivers/ssh/install.sh && sudo /home/marc/rpinode/drivers/firewall/install.sh'
    sleep 5
    if $SSH -o StrictHostKeyChecking=accept-new "marc@$TS" 'echo "accès marc@$(hostname) par Tailscale : OK"'; then
        $SSH "marc@$TS" 'sudo /home/marc/rpinode/drivers/ssh/install.sh --confirmer && sudo /home/marc/rpinode/drivers/firewall/install.sh --confirmer'
    else
        echo "Accès par Tailscale impossible : SSH et pare-feu seront retirés automatiquement dans 5 minutes." >&2
        exit 1
    fi
    echo
    echo "Déploiement terminé : $(  $SSH "marc@$TS" hostname ) ($TS)."
    echo "Ajouter un alias dans ~/.ssh/config :  Host <nom> / HostName $TS / User marc"
fi
