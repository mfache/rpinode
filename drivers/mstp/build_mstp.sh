#!/bin/bash
# Compile l'outil de decouverte BACnet MS/TP (RS-485) a partir des sources
# officielles de bacnet-stack (C, protocole MS/TP mature et tres repandu).
#
# Pourquoi pas du Python comme le reste du boitier-bacnet (bacpypes3) ?
# bacpypes3 n'a jamais implemente le data-link MS/TP (serie), et l'ancien
# bacpypes (classique) a retire son module MS/TP des versions recentes tout
# en dependant du module stdlib "asyncore", supprime en Python 3.12+ : aucune
# des deux librairies Python ne convient. bacnet-stack (C) reste la reference
# du marche pour MS/TP (utilise par de nombreuses passerelles du commerce).
#
# Deux correctifs maison sont appliques (bacnet-stack-mstp.patch), sans quoi
# l'outil est inutilisable pour une mise en service confortable :
#
#  1. Npoll configurable a l'execution (BACNET_MSTP_NPOLL). En amont c'est une
#     constante figee a 50 (valeur de la norme) : le nombre de jetons qui
#     s'ecoulent avant de sonder une nouvelle adresse. Sur un bus charge, notre
#     noeud met alors 90 a 140 s a etre integre a l'anneau avant de pouvoir
#     seulement emettre son Who-Is. Mesure sur site : ~20 s avec Npoll=2.
#
#  2. Affichage temps reel des I-Am. En amont, bacwi accumule silencieusement
#     les reponses et n'imprime son tableau qu'une fois, juste avant de sortir
#     (aucun fflush) : impossible d'afficher les equipements au fil de l'eau.
#     Le correctif ajoute une ligne "IAM;<device>;<mac>;<apdu>" flushee des
#     reception, en plus du tableau final laisse intact.
#
# Usage : sudo bash build_mstp.sh
# Resultat : /opt/boitier-bacnet/mstp/bin/bacwi (Who-Is / decouverte MS/TP)
set -e

SRC_DIR="/opt/boitier-bacnet/bacnet-stack-src"
OUT_DIR="/opt/boitier-bacnet/mstp/bin"
PATCH_FILE="$(cd "$(dirname "$0")" && pwd)/bacnet-stack-mstp.patch"

# Version epinglee : un build reproductible vaut mieux qu'un "toujours a jour"
# sur un boitier de production, et le patch ci-dessus est ecrit contre cet
# arbre precis (une mise a jour amont pourrait le faire echouer).
PIN_COMMIT="63ff839"

if [ ! -f "$PATCH_FILE" ]; then
    echo "ERREUR : patch introuvable ($PATCH_FILE)" >&2
    exit 1
fi

if [ ! -d "$SRC_DIR" ]; then
    git clone https://github.com/bacnet-stack/bacnet-stack.git "$SRC_DIR"
fi

# Remet l'arbre a l'etat amont exact avant d'appliquer le patch : le script
# doit pouvoir etre relance sans empiler les correctifs ni echouer.
# Le commit epingle peut manquer si le depot local est un clone superficiel
# (--depth 1 des versions precedentes de ce script) : on le recupere alors.
if ! git -C "$SRC_DIR" cat-file -e "$PIN_COMMIT^{commit}" 2>/dev/null; then
    git -C "$SRC_DIR" fetch --unshallow origin || git -C "$SRC_DIR" fetch origin
fi
git -C "$SRC_DIR" checkout --force "$PIN_COMMIT"
git -C "$SRC_DIR" reset --hard "$PIN_COMMIT"
git -C "$SRC_DIR" clean -fdx

git -C "$SRC_DIR" apply --verbose "$PATCH_FILE"

cd "$SRC_DIR"
make BACDL=mstp whois

mkdir -p "$OUT_DIR"
cp bin/bacwi "$OUT_DIR/bacwi"
chmod 755 "$OUT_DIR/bacwi"

echo "OK : $OUT_DIR/bacwi (patch applique : Npoll configurable + I-Am temps reel)"
