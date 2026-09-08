# Migration vers Headscale — Terminée

**Dernière mise à jour : 8 septembre 2026, 17:45 CEST**

**Statut : ✅ migration terminée et vérifiée.** Les sections ci-dessous
reflètent l'état au moment de la reprise de session ; elles sont conservées
comme historique de la migration. Le résumé de clôture (état final,
commandes exécutées, résultat des vérifications) est en section 6 et dans
`~/HEADSCALE_SUMMARY.md` (section 11) sur le serveur `docs`.

Ce document a initialement servi de point de reprise pendant une coupure
de session (connectivité Tailscale vers `rpi01` perdue pendant la
migration).

**Important (8 septembre 2026, mise à jour ultérieure)** : les étapes
manuelles de la section 4 (génération de clé en SSH, `tailscale up`
manuel, approbation des routes) ne sont plus nécessaires pour les
**futurs** boitiers de la flotte. Un mécanisme d'enrôlement automatique a
été mis en place : voir
[`../integrations/HEADSCALE_AUTO_ENROLL.md`](../integrations/HEADSCALE_AUTO_ENROLL.md).
Ce document reste pertinent comme historique de la bascule de `rpi01` et
comme référence des commandes `headscale`/`tailscale` sous-jacentes.

## 1. Objectif de la tâche

Remplacer l'usage du Tailscale SaaS (limité en nombre de postes) par le
Headscale auto-hébergé sur `docs.deltathermic.be`, pour le parc de
Raspberry Pi (actuellement un seul appareil : `rpi01`).

Le serveur `docs` doit lui-même être un nœud du réseau Headscale (nom de
nœud : **`docs`**), pour pouvoir communiquer directement avec `rpi01` (et
les futurs Pi) via leurs adresses Tailscale internes.

Le PC Windows de Marc (`desktop-h66`) reste sur le Tailscale SaaS pour ses
appareils personnels (`homeassistant`, `pixel-marc`, `laptop-delta`, `vm`),
mais doit pouvoir **aussi** joindre le réseau Headscale via un second
profil (`tailscale switch`), sans accès simultané aux deux réseaux (choix
assumé par l'utilisateur : pas besoin des deux en même temps).

## 2. Pré-requis déjà validés (avant cette tâche)

- Headscale fonctionne pleinement sur `docs.deltathermic.be` : l'accès
  Internet sortant du serveur et le blocage NetScaler de l'en-tête
  `Upgrade` (protocole TS2021) ont été résolus et confirmés par un test de
  bout en bout le 8 septembre 2026.
- Détails complets de cette résolution : voir `~/HEADSCALE_SUMMARY.md` sur
  le serveur `docs` (accessible via `ssh -p 9922 mariadb@docs.deltathermic.be`),
  section 10. Ce fichier n'existe que sur le serveur, pas dans ce dépôt.

## 3. État actuel détaillé (au moment de la coupure)

### 3.1. Serveur `docs` (docs.deltathermic.be)

- Headscale `v0.29.2` opérationnel, service `systemd` actif.
- Utilisateur Headscale existant : `delta` (ID numérique **1**), c'est
  l'utilisateur à utiliser pour créer les clés de pré-authentification des
  nœuds `docs` et `rpi01`.
- `headscale nodes list` est **vide** actuellement (aucun nœud enregistré).
- Client Tailscale **installé** sur le serveur (`apt` : `tailscale
  1.102.3`), service `tailscaled` **activé** au démarrage, mais **`sudo
  tailscale status` renvoie `Logged out.`** : le serveur n'a pas encore
  rejoint son propre réseau Headscale.
- **Étape interrompue ici** : l'utilisateur a stoppé la commande juste
  avant/pendant la vérification post-installation. Il ne reste donc plus
  qu'à lancer `tailscale up` (voir section 4, étape A) — pas besoin de
  refaire l'installation du paquet.

### 3.2. `rpi01` (ce projet, machine actuelle)

- **Aucun changement effectué.** Toujours enregistré sur le Tailscale
  SaaS (tailnet `tailb49c55`), inchangé.
- Nom Tailscale actuel : `rpi01.tailb49c55.ts.net`
- IP Tailscale actuelle (SaaS) : `100.66.68.35` /
  `fd7a:115c:a1e0::7837:4424`
- Routes actuellement annoncées (`PrimaryRoutes` dans `tailscale status
  --json`) : **`10.42.0.0/24`, `192.168.1.0/24`** — à ré-annoncer après
  bascule avec `sudo tailscale set --advertise-routes=10.42.0.0/24,192.168.1.0/24`
  (ou laisser `services/network_config.py::publish_tailscale_routes()`
  s'en charger au prochain changement de profil réseau sur `eth0`).
- **IP locale de secours (LAN, `eth0`) : `192.168.1.253`**, utilisateur
  `marc`. C'est par cette adresse que la reconnexion doit se faire si
  l'accès Tailscale est perdu pendant la migration.
- Le code du projet (`src/services/network.py`,
  `src/services/network_config.py`) est **agnostique du control-server** :
  il utilise uniquement la commande `tailscale` en CLI, sans URL codée en
  dur. Aucune modification de code n'est nécessaire pour cette migration,
  uniquement des commandes `tailscale`/`headscale`.

### 3.3. PC Windows de Marc (`desktop-h66`)

- Toujours connecté au Tailscale SaaS au moment de la coupure.
- Une clé de pré-authentification à usage unique a été émise pour ce PC :
  ```
  hskey-auth-2av4BmrNF94F-vy5fAYaYxBnUbiiyalIxdS8q_lhlL8moGKbAP68FJy7Vf5P-mF8cpPY834t91XYy
  ```
  Créée le 8 septembre 2026 à ~17:00 CEST, **expiration 30 minutes**
  (donc probablement expirée si cette reprise a lieu plus tard — vérifier
  et en régénérer une si besoin, voir section 5).
- Instruction donnée à l'utilisateur (**statut d'exécution inconnu** au
  moment de la coupure — à confirmer avec lui) :
  ```powershell
  tailscale login --login-server=https://docs.deltathermic.be --authkey=<clé>
  ```
  puis, pour revenir au tailnet personnel plus tard :
  ```powershell
  tailscale switch
  ```
  (liste les profils disponibles, puis `tailscale switch <nom>`).
- **C'est cette action côté PC qui a probablement causé la coupure de la
  session actuelle** : en basculant de profil, le PC perd sa connexion
  Tailscale SaaS vers `rpi01` (`100.66.68.35`), d'où la nécessité de
  reconnexion via l'IP locale `192.168.1.253`.

## 4. Prochaines étapes (dans l'ordre, à valider avec l'utilisateur avant d'exécuter les étapes risquées)

**A. Faire rejoindre `docs` à son propre Headscale (sans risque, à faire en premier)**

Sur `docs` (SSH `ssh -p 9922 mariadb@docs.deltathermic.be`) :
```bash
KEY=$(headscale preauthkeys create --user 1 --expiration 10m --reusable=false)
sudo tailscale up --login-server=https://docs.deltathermic.be --authkey=$KEY --hostname=docs
headscale nodes list   # doit montrer "docs" online
```

**B. Confirmer l'état du PC Windows**

Demander à l'utilisateur de lancer `tailscale status` sur son PC. S'il voit
bien `docs` comme pair (une fois l'étape A faite), le profil Headscale du
PC fonctionne. Si la clé a expiré, en régénérer une (`headscale
preauthkeys create --user 1 --expiration 30m --reusable=false`) et
redonner la commande `tailscale login --login-server=... --authkey=...`.

**C. Basculer `rpi01` du SaaS vers Headscale (étape sensible)**

⚠️ Confirmer avec l'utilisateur juste avant, et s'assurer qu'il a bien un
moyen de se reconnecter en SSH via `192.168.1.253` si besoin (déjà
confirmé par lui).

Sur `docs`, générer une clé pour `rpi01` :
```bash
headscale preauthkeys create --user 1 --expiration 15m --reusable=false
```

Sur `rpi01` (terminal local, cette machine) :
```bash
sudo tailscale down
sudo tailscale logout
sudo tailscale up --login-server=https://docs.deltathermic.be --authkey=<clé> --hostname=rpi01 --advertise-routes=10.42.0.0/24,192.168.1.0/24
tailscale status   # vérifier IP attribuée et statut online
```

**D. Vérifier la connectivité complète**

Depuis `docs` : `sudo tailscale ping rpi01` (ou l'IP Headscale attribuée).
Depuis le PC (profil Headscale actif) : idem vers `docs` et `rpi01`.

**E. Documenter la fin de la migration**

- Mettre à jour `~/HEADSCALE_SUMMARY.md` sur `docs` (nouvelle section
  "migration rpi01 terminée").
- Marquer ce fichier (`HEADSCALE_MIGRATION_STATUS.md`) comme **terminé**
  ou le supprimer une fois la migration confirmée stable, pour ne pas
  laisser un document de suivi obsolète dans le dépôt.

## 5. Accès de référence

- SSH `docs` : `ssh -p 9922 mariadb@docs.deltathermic.be`
- SSH `rpi01` (fallback LAN) : IP `192.168.1.253`, utilisateur `marc`
- Utilisateur Headscale à utiliser pour toutes les clés : `delta` (ID `1`)
- Documentation générale d'accès au serveur `docs` :
  [DOCS_SERVER_ACCESS.md](DOCS_SERVER_ACCESS.md)

## 6. Clôture — état final vérifié (8 septembre 2026, 17:45 CEST)

Toutes les étapes de la section 4 ont été exécutées avec succès, dans
l'ordre, lors de la reprise de session (utilisateur connecté en SSH local
via `eth0`, donc sans risque de coupure pendant la bascule de `rpi01`).

**A. `docs` a rejoint Headscale** : nœud `docs` online, IP `100.64.0.4`.

**B. PC Windows** : déjà connecté au moment de la vérification, nœud
`desktop-bureau` online, IP `100.64.0.3` (le `tailscale login` avait donc
bien été exécuté côté utilisateur avant la reprise).

**C. `rpi01` basculé du SaaS vers Headscale** :
```bash
sudo tailscale down
sudo tailscale logout
sudo tailscale up --login-server=https://docs.deltathermic.be \
  --authkey=<clé> --hostname=rpi01 \
  --advertise-routes=10.42.0.0/24,192.168.1.0/24
```
Nœud `rpi01` online, IP `100.64.0.5`.

**Étape supplémentaire non prévue dans le plan initial** : les routes
annoncées par `rpi01` n'étaient pas actives automatiquement
(`headscale nodes list-routes` les montrait en `Available` mais pas
`Approved`). Approbation nécessaire :
```bash
headscale nodes approve-routes --identifier 5 \
  --routes 10.42.0.0/24,192.168.1.0/24
```
Confirmé ensuite en `Approved` + `Serving (Primary)`.

**D. Connectivité vérifiée** : `sudo tailscale ping rpi01` depuis `docs`
répond (`pong` via DERP puis via connexion directe `109.143.88.27:41641`).

**État final du tailnet Headscale :**

| ID | Hostname | Nom | IP | Statut |
|----|----------|-----|----|--------|
| 3 | DESKTOP-BUREAU | desktop-bureau | 100.64.0.3 | online |
| 4 | docs | docs | 100.64.0.4 | online |
| 5 | rpi01 | rpi01 | 100.64.0.5 | online (routes `10.42.0.0/24`, `192.168.1.0/24` approuvées) |

**E. Documentation** : section 11 ajoutée à `~/HEADSCALE_SUMMARY.md` sur
`docs` (détail complet des commandes et vérifications). Ce fichier local
est conservé comme historique de la migration plutôt que supprimé, son
contenu (contexte, décisions, points d'attention) restant utile en cas de
problème futur avec le réseau Headscale.

**Aucune modification de code n'a été nécessaire** dans ce dépôt : le
code réseau (`src/services/network.py`,
`src/services/network_config.py`) est agnostique du control-server.
