# Accès au serveur `docs`

Ce document centralise les informations utiles pour intervenir sur le serveur
`docs.deltathermic.be`, utilisé par `rpinode` pour la synchronisation de flotte
et l’API distante.

## 1. Quand utiliser ce document

Consulte ce document si une tâche concerne :

- l’API centrale ;
- la base distante ;
- le redémarrage des services côté `docs` ;
- un comportement observé sur le serveur mais absent du dépôt local.

## 2. Accès SSH de référence

Commande de connexion :

```sh
ssh -p 9922 mariadb@docs.deltathermic.be
```

## 3. Rôle du serveur

Le serveur `docs.deltathermic.be` est le serveur central de synchronisation.
C’est une VM fournie par le service informatique.

Nous avons les pleins pouvoirs sur ce serveur pour y installer ce que nous
voulons, mais nous n’avons pas la main sur les ouvertures de ports.

## 4. Repères utiles une fois connecté

- accès MariaDB : `/etc/boitier-fleet/db.env`
- script API distant : `/var/www/reports/api.py`
- script de redémarrage des services : `/home/mariadb/bin/https`

## 5. Point important : le code de `reports` vit dans trois espaces distincts sur `docs`

**Mise à jour du 19 septembre 2026** : contrairement à ce que laissait
supposer l'ancien fichier `api.py` monolithique (1696 lignes), le code a
été refactoré le 10 septembre 2026 "sur le modèle rpinode" et est
désormais **versionné avec Git**, mais **pas dans le dépôt local `rpinode`**
(toujours hors de ce dépôt) et réparti sur trois emplacements sur le
serveur `docs`, à ne pas confondre :

- **`/opt/reports-dev`** : bac à sable de développement. C'est **ici qu'il
  faut travailler**, jamais directement en production. Dépôt Git local
  (remote `github-reports-dev:mfache/reports-dev.git`), base MariaDB
  isolée `dt_dev` (config `/etc/boitier-fleet/db-dev.env`), servi sous
  `https://docs.deltathermic.be/reports-dev` (même politique d'auth que la
  prod : `/reports-dev/api` ouvert, le reste derrière Google OAuth).
- **`/opt/docs-infra`** : dépôt Git "officiel", poussé sur GitHub
  (`github-reports:mfache/docs-infra.git`), synchronisé depuis
  `reports-dev` par `rsync` (pas de remote direct entre les deux). C'est
  la référence versionnée de l'état de la production.
- **`/var/www/reports`** : déploiement de production, servi sous
  `/reports`. **Ne jamais modifier directement** (incident vécu le
  9 septembre). On y copie le code une fois testé et validé dans
  `reports-dev`.

Code applicatif désormais sous `src/` dans les trois emplacements :
`src/core/`, `src/services/*.py` (un fichier par domaine métier, monté
sur l'`api_app` Bottle partagé par effet de bord d'import), `src/web/`
(assemblage, UI, réponses). Voir `/opt/reports-dev/CAHIER-DES-CHARGES-REFONTE.md`
et `/opt/docs-infra/OPERATIONS.md` sur le serveur pour le détail complet
de cette organisation (non dupliqués ici).

Workflow de déploiement type : coder et tester dans `reports-dev`
(`./run_tests.sh`, puis `./run.sh` qui fait tests + `kill -HUP` ciblé,
**jamais** `systemctl restart uwsgi` qui coupe toutes les apps — incident
vécu le 10 septembre) → `rsync` vers `docs-infra` → `git commit` + `git
push` → copie manuelle vers `/var/www/reports` → `./run.sh` en production.

Conséquences pour une intervention future :
- une modification locale du dépôt `rpinode` ne suffit pas toujours ;
- toujours vérifier dans `/opt/reports-dev` (pas directement en
  production) si le comportement attendu n'est pas visible dans ce dépôt ;
- toute intervention sur le schéma MariaDB (`dt`) doit être répliquée sur
  `dt_dev` pour garder les deux environnements cohérents.

## 6. Documentation liée

- Documentation API distante :
  [https://docs.deltathermic.be/reports/api/usage](https://docs.deltathermic.be/reports/api/usage)
- Historique des modifications hors dépôt :
  [../integrations/FLEET_API_CHANGES.md](../integrations/FLEET_API_CHANGES.md)
- Comptes techniques et ACL SSH Headscale (`fleet`/`docsadmin`) : résumé dans
  [HEADSCALE_SSH_ACL.md](HEADSCALE_SSH_ACL.md), référence canonique dans
  `docs:/var/www/reports/HEADSCALE-ACL.md`.

## 7. Redémarrage et précautions

Un script de redémarrage est disponible :

```sh
/home/mariadb/bin/https
```

Pour les détails opérationnels observés côté uWSGI et les limites de certaines
commandes de reload, voir aussi [../integrations/FLEET_API_CHANGES.md](../integrations/FLEET_API_CHANGES.md).

## 8. Règle pratique

Si une tâche touche la synchronisation distante, l’API centrale ou la base
MariaDB du serveur `docs`, ne suppose pas que tout se trouve dans le dépôt
local. Vérifie explicitement si l’intervention doit se faire en SSH sur le
serveur.
