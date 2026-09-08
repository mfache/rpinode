# Note pour Marc — test réel changement de chantier (Headscale)

Contexte : mise en place le 8 septembre 2026 de l'enrôlement/synchronisation
automatique des routes Headscale (voir
`docs/integrations/HEADSCALE_AUTO_ENROLL.md`). Prochain test réel : premier
changement de chantier de `rpi01` depuis cette mise en place.

## Ce qui doit se passer automatiquement

Au changement de chantier, `publish_tailscale_routes()` se déclenche (nouvelle
IP `eth0`/`wlan0` détectée), republie les nouvelles routes sur Tailscale, **et**
appelle `POST /headscale/routes` sur `docs` pour les faire approuver côté
Headscale.

## Adresses Headscale (MagicDNS) actuelles

Domaine change le 8 septembre 2026 : `ts.deltathermic.be` -> `dt.net`
(plus court, purement interne a MagicDNS, aucun lien avec un vrai domaine
public).

| Nom | Adresse Headscale | IP |
| --- | --- | --- |
| `rpi01` | `rpi01.dt.net` | `100.64.0.5` |
| `docs` | `docs.dt.net` | `100.64.0.4` |
| PC de Marc (`desktop-h66`) | `desktop-h66.dt.net` | `100.64.0.3` |

Les futurs boitiers (`rpi02`, `rpi03`, ...) suivront le meme format :
`rpiNN.dt.net`. Liste a jour consultable sur l'interface admin :
https://docs.deltathermic.be/headscale-admin/ (comptes `marc` / `hsadmin`,
voir `docs/integrations/HEADSCALE_AUTO_ENROLL.md`).

Changement applique via `base_domain` dans `/etc/headscale/config.yaml`
sur `docs` (sauvegarde : `config.yaml.bak_20260908_before_dt.net`) +
`sudo systemctl restart headscale`. Propagation automatique confirmee sur
tous les noeuds sans action manuelle (`tailscale status --json`).

## Comment vérifier que ça a fonctionné

- `sudo tailscale status` sur `rpi01` (ou depuis `docs`/le PC) : `rpi01` doit
  rester `online`.
- `headscale nodes list-routes` sur `docs` (SSH `ssh -p 9922
  mariadb@docs.deltathermic.be`) : la nouvelle route doit apparaître en
  `Approved` / `Serving`.
- Logs : `grep "Publication des routes" /tmp/rpinode/log/rpinode.log` sur
  `rpi01`.

## HTTPS sans avertissement (CA interne "Deltathermic")

Depuis le 8 septembre 2026, `https://rpi01.dt.net` fonctionne (nginx en
reverse-proxy TLS vers le port 8081, le superviseur Rust). Le certificat
est signé par une CA interne (`Deltathermic Internal CA`, wildcard
`*.dt.net`, valable pour tous les futurs `rpiNN.dt.net`), pas par une CA
publique : le navigateur affichera un avertissement de sécurité **tant que
le certificat racine n'est pas installé** sur l'appareil qui s'y connecte.

### Installer la CA une fois par appareil (Windows) — methode rapide

1. Télécharger et exécuter (double-clic) :
  `http://rpi01.dt.net/install-ca.bat`
2. Une fenêter noire s'ouvre, installe le certificat automatiquement pour
  ton compte Windows (**aucune fenêtre d'administrateur/UAC**, ferme la
  fenêtre avec une touche quand c'est marqué "installe avec succes").
3. Redémarrer le navigateur (Chrome/Edge). `https://rpi01.dt.net` doit
  s'afficher sans avertissement, cadenas inclus.

Ca marche pour Chrome/Edge (ils utilisent le magasin de certificats
Windows). **Firefox a son propre magasin** et ne verra pas ce certificat
par défaut ; si besoin sur Firefox, dis-le moi et j'indiquerai le réglage
`about:config` correspondant.

### Méthode manuelle (si le `.bat` est bloqué par l'antivirus/SmartScreen)

1. Télécharger `http://rpi01.dt.net/ca.crt`.
2. Double-clic sur le fichier téléchargé -> **Installer un certificat**.
3. Choisir **Ordinateur local** (pas "Utilisateur actuel") -> Suivant.
4. **Placer tous les certificats dans le magasin suivant** -> Parcourir ->
  **Autorités de certification racines de confiance** -> OK -> Suivant ->
  Terminer.
5. Redémarrer le navigateur.

Detail technique complet (génération de la CA, émission de certificats
pour de futurs `rpiNN`, config nginx) : voir
`docs/operations/INTERNAL_CA_TLS.md`.

## TODO (à faire une autre fois) : procédure d'accès côté `docs`

Idée de Marc (8 septembre 2026) : les utilisateurs passeront **d'abord par
`docs.deltathermic.be`** pour rejoindre/accéder aux boitiers (`rpiNN`), pas
directement par ce dépôt Git (qu'ils n'ont probablement pas). Il faut donc
écrire une procédure d'accès utilisateur (pas seulement technique) et la
rendre accessible **depuis le serveur `docs` lui-même** (une page/section
sur `docs.deltathermic.be`, pas seulement dans `rpinode/docs/`).

Cette procédure devra couvrir, dans l'ordre attendu par un nouvel
utilisateur :

1. Comment rejoindre le réseau Headscale (profil Tailscale séparé /
  `tailscale switch`, clé de pré-authentification à obtenir auprès de qui,
  etc. — voir `docs/operations/HEADSCALE_MIGRATION_STATUS.md` côté
  dépôt pour le détail technique actuel).
2. Comment installer la CA interne "Deltathermic" pour éviter les
  avertissements HTTPS (voir section "HTTPS sans avertissement"
  ci-dessus et `docs/operations/INTERNAL_CA_TLS.md`).
3. Comment trouver/choisir l'adresse du bon boitier (`rpiNN.dt.net`).

À faire : écrire cette page côté `docs` (probablement dans l'app
`reports`/`ui.py`, ou une page statique servie par nginx — à décider),
et y renvoyer depuis un endroit visible (page d'accueil `reports`,
menu, ou lien direct communiqué aux utilisateurs).

## En cas de problème

Accès de secours SSH local : `192.168.1.253`, utilisateur `marc`.
