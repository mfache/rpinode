# Tailscale SSH sur le tailnet Headscale — comptes et ACL

**Mise en place : 9 septembre 2026.**

Ce document résume la mise en place de Tailscale SSH entre `docs` et la
flotte de boîtiers (`rpi01`, futurs `rpiNN`). **La référence canonique et à
jour est le fichier tenu sur le serveur `docs` lui-même** :

```
docs:/var/www/reports/HEADSCALE-ACL.md
```

(accessible en SSH via `ssh -p 9922 mariadb@docs.deltathermic.be`, voir
[DOCS_SERVER_ACCESS.md](DOCS_SERVER_ACCESS.md)). Ce fichier local n'est
qu'un résumé pour donner le contexte sans avoir à se connecter — **toute
modification de `acl.hujson` doit être documentée côté `docs`, pas ici**.

## 1. Pourquoi

Éviter que l'administration de la flotte et l'automatisation entre boîtiers
et `docs` reposent sur des comptes personnels (`marc`, `mariadb` utilisés à
titre individuel). Deux comptes techniques dédiés ont été créés, sans mot de
passe ni clé SSH classique : leur seule porte d'entrée est l'identité réseau
Tailscale/Headscale, autorisée par la policy ACL (`ssh` block).

## 2. Comptes créés

| Compte | Machine | Sens | Sudo |
|---|---|---|---|
| `fleet` | `docs` | boîtier → `docs` | aucun |
| `docsadmin` | chaque Pi (`rpi01`, futurs `rpiNN`) | `docs` → boîtier | restreint, voir `/etc/sudoers.d/docsadmin` sur chaque Pi (status/restart de `rpinode.service`/`rpinode-supervisor.service`, `journalctl`, `tailscale status`) |

Les deux comptes sont verrouillés (`passwd -l`) et n'ont pas de
`~/.ssh/authorized_keys` : impossibles à utiliser en SSH classique, y
compris si le port SSH classique venait à être exposé par erreur.

## 3. Tags Headscale

- `tag:fleet` : posé sur les nœuds de la flotte (`rpi01`, futurs `rpiNN`).
- `tag:docs` : posé sur `docs`.
- Un nœud taggé change de « propriétaire » Headscale : il passe de
  l'utilisateur humain `delta` à l'utilisateur virtuel `tagged-devices`. Ne
  pas s'étonner de le voir dans `headscale nodes list`.
- Seul le groupe `group:fleet-admins` (= `delta@`) peut poser/retirer ces
  tags (`tagOwners` dans `acl.hujson`).

**Pour un futur `rpiNN`, c'est désormais automatique** (9 septembre 2026,
après-midi) :

- le tag `tag:fleet` est posé **côté serveur** par `docs` (`api.py`,
  fonction `_ensure_fleet_tag()`, appelée depuis `POST /headscale/routes`
  à chaque synchronisation de routes, y compris la première) ;
- le compte `docsadmin` + son sudoers restreint + l'activation de
  `tailscale set --ssh` sont posés **côté boîtier** par
  `src/services/headscale_enroll.py::ensure_docs_admin_access()`, appelée
  à chaque démarrage du service `rpinode` (idempotente).

Détail complet : [`../integrations/HEADSCALE_AUTO_ENROLL.md`](../integrations/HEADSCALE_AUTO_ENROLL.md).

## 4. Incident du 09/09/2026 — leçon retenue

L'activation de `tailscale set --ssh` sur `rpi01` a coupé la session SSH
admin en cours, car la policy ne contenait alors **que** les règles
d'automatisation (`fleet`→`docs`, `docs`→`rpi01`), aucune n'autorisant un
accès humain (`marc`/`mariadb`) une fois Tailscale SSH actif — **dès
l'activation, `sshd` classique et les `authorized_keys` sont ignorés pour
les connexions qui transitent par le réseau Tailscale.**

Correctif : deux règles supplémentaires ajoutées à `acl.hujson`, autorisant
`group:fleet-admins` (postes persos `desktop-h66`/`laptop-marc`, utilisateur
`delta`) à se connecter en `marc` sur la flotte et en `mariadb` sur `docs`.

**Règle à respecter pour tout nouveau nœud** : ne jamais activer
`tailscale set --ssh` sans avoir d'abord vérifié qu'une règle `ssh` de la
policy couvre un accès admin humain vers ce nœud. Détail complet et
procédure de récupération : voir `HEADSCALE-ACL.md` sur `docs` (section 4).

## 5. État vérifié le 9 septembre 2026

- `rpi01` (`tag:fleet`) : `RunSSH: true`.
- `docs` (`tag:docs`) : `RunSSH: true`.
- `ssh fleet@docs` depuis `rpi01` : ✅ fonctionne.
- `ssh docsadmin@rpi01` depuis `docs` : ✅ fonctionne, sudo restreint validé
  (`systemctl status rpinode.service` sans mot de passe, commande hors liste
  refusée comme attendu).
- Connectivité générale du tailnet (`acls` ouvert) : non affectée par ce
  changement, vérifiée par `tailscale ping` avant/après.

## 6. Automatisation du taggage et du provisioning (9 septembre 2026, après-midi)

- Ajout de `_ensure_fleet_tag()` côté serveur (`api.py`, appelée depuis
  `POST /headscale/routes`) : pose automatiquement `tag:fleet` sur le nœud
  d'un boîtier qui ne l'a pas encore. Testé en conditions réelles avec le
  jeton de `rpi01` (déjà taggué : opération sans effet, comme attendu) et
  par un test unitaire isolé (mock de `_run_headscale`).
- Ajout de `ensure_docs_admin_access()` côté boîtier
  (`headscale_enroll.py`) : crée `docsadmin`, dépose son sudoers restreint
  et active `tailscale set --ssh`, à chaque démarrage du service. Couvert
  par `tests/test_headscale_enroll.py`.
- **Incident survenu pendant ces travaux** (sans rapport avec l'ACL ou les
  comptes techniques) : un `systemctl reload uwsgi` a révélé que
  `/var/www/reports/app.py` avait été accidentellement écrasé par une copie
  de `/var/www/headscale-admin/app.py`, cassant temporairement toute l'API
  de flotte (`/reports/api/*`, HTTP 500). Corrigé en reconstruisant le
  point d'entrée WSGI correct (`application = Bottle(); application.mount('/reports/api', api_app)`).
  Détail dans les notes du serveur (`docs:/var/www/reports/NOTES-evolutions.md`,
  section du 9 septembre après-midi).
