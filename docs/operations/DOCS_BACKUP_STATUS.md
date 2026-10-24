# Sauvegarde et structuration de `docs` — point de reprise

**Dernière mise à jour : 9 septembre 2026, ~15h10 CEST.**

**Statut : 🟡 en cours.** Ce document sert de point de reprise après une
coupure de session volontaire. Dépôt `rpinode` propre (`git status` clean,
tout ce qui est versionné a déjà été committé et poussé — voir section 5).
Rien n'est cassé ni en attente côté serveur : ce qui suit, c'est la suite du
travail, pas une réparation d'urgence.

## 1. Point de départ de cette tâche

Constat de Marc : la situation sur `docs` (config, code hors dépôt, absence
de sauvegarde) résulte d'une progression de plusieurs années sans plan ni
structure. Décision : **d'abord un premier backup local, ensuite une
énumération complète des services, puis seulement une proposition de
structure.**

## 2. Fait : premier backup local sur `docs`

Backup manuel (pas encore automatisé) réalisé le 9 septembre 2026 à
15h05, stocké dans `/var/backups/docs-app/` (permissions `600 root:root`) :

- `dt_20260909_150538.sql.gz` (4,4 Mo) — dump complet MariaDB `dt`
  (`mysqldump --single-transaction --routines --triggers`).
- `docs_infra_20260909_150538.tar.gz` (360 Ko) — contient :
  - `/var/www/reports/`, `/var/www/headscale-admin/` (code hors Git)
  - `/etc/headscale/` (config.yaml, acl.hujson — pas de clés)
  - `/var/lib/headscale/` (db.sqlite + `noise_private.key` +
    `derp_server_private.key` — identité du tailnet)
  - `/etc/nginx/`
  - `/etc/ntfy/` + `/var/lib/ntfy/auth.db`
  - `/etc/boitier-fleet/db.env` (secrets)
  - `/etc/deltathermic-ca/ca.crt` + `issued/` (certificats émis, **PAS**
    `ca.key`)

**Exclusion volontaire et vérifiée** : `/etc/deltathermic-ca/ca.key` (clé
privée de la CA) est absente de l'archive — confirmé par
`tar tzf ... | grep ca.key` (aucun résultat). Cette clé ne doit **jamais**
quitter `docs` (voir `INTERNAL_CA_TLS.md`).

Espace disque sur `docs` : 77 Go libres sur 96 Go — aucune contrainte.

**Ce backup est un instantané ponctuel, pas encore répété/automatisé.**

## 3. Fait : énumération complète des services sur `docs`

> **Écarts constatés le 24 octobre 2026** (le tableau ci-dessous date du
> 9 septembre) :
>
> - **mosquitto** n'écoute plus seulement en local : `0.0.0.0:1883`,
>   toujours anonyme (`/etc/mosquitto/conf.d/local.conf`). UFW n'autorise
>   `1883` que sur `tailscale0`, pour le bridge MQTT des boîtiers vers
>   `100.64.0.4:1883`. Attention : la chaîne `ts-input` de tailscale
>   accepte tout le trafic de `tailscale0` **avant** UFW, donc MariaDB
>   (`3306`) est aussi joignable depuis tout le tailnet.
> - **`/bottle` et `/bottledev`** ont disparu de la config nginx.
> - **Azure AD est de nouveau actif** sur `/reports` (voir
>   `README_FOR_HUMAN_ONLY.md`), et `/headscale-admin` passe aussi par
>   `oauth2-proxy`.
> - **SSH** : en plus du port public `9922` (redirigé vers `22`), le port
>   `22` est lui aussi joignable depuis Internet, avec l'authentification
>   par mot de passe active. Environ 15 600 tentatives de connexion
>   échouées en 24 h avant l'installation de fail2ban.
> - **fail2ban installé** (`/etc/fail2ban/jail.local`) : jail `sshd`, et
>   jail `nginx-scan` (filtre `/etc/fail2ban/filter.d/nginx-scan.conf`,
>   requêtes 4xx sur `.env`, `.git/`, `wp-`, `.php`…, jamais sur
>   `/reports`, `/pdf`, `/ntfy-fleet` ni headscale). Le réseau local
>   `10.10.10.0/24`, le tailnet et `10.70.1.45` ne sont jamais bannis.
>   Pour débannir : `sudo fail2ban-client set <jail> unbanip <IP>`.
> - Le `error_log` du vhost nginx est passé de `debug` à `warn` (il
>   faisait environ 34 Mo par jour).

### Entrée réseau
- UFW actif, seuls `22` et `443` autorisés en entrée. `MariaDB:3306` écoute
  sur `0.0.0.0` mais n'est pas exposé par le pare-feu.
- nginx (443) est le point d'entrée unique, qui distribue vers :

| Chemin nginx | Backend | Rôle |
|---|---|---|
| `/` (défaut) | `127.0.0.1:8080` = **headscale lui-même** | Protocole de contrôle Tailscale (pas un webadmin) |
| `/reports/api` | uwsgi `reports.socket` | API flotte (Bearer token) — `api.py` |
| `/reports` + `/reports/*_sse` | uwsgi `reports.socket` | UI humaine + SSE, protégée par **Azure AD** (`oauth2-proxy`, tenant Entra ID) |
| `/reports/webdav` | `/var/www/reports/webdav` (DAV) | Photos/plans, basic auth (`/etc/nginx/.auth.allow`) |
| `/pdf` | `/var/www/pdf/` (DAV) | Écriture protégée basic auth, lecture publique |
| `/headscale-admin` | uwsgi `headscale-admin.socket` | Gestion Headscale, basic auth (`/etc/nginx/htpasswd`, **différent** de Azure AD) |
| `/ntfy-fleet/` | `127.0.0.1:2586` (ntfy) | Notifications flotte |
| `/bottle` | uwsgi `bottledemo.socket` | App de démo, sans auth visible — **statut à clarifier** |
| `/bottledev` | `127.0.0.1:8081` | **Rien n'écoute sur ce port** — config nginx morte |
| `/oauth2/` | `127.0.0.1:4180` (oauth2-proxy) | SSO Azure AD |

### Services applicatifs
- `headscale.service`, `mariadb.service`, `nginx.service`, `uwsgi.service`,
  `tailscaled.service`, `ntfy.service` — cœur connu.
- `mosquitto` (MQTT local `127.0.0.1:1883`, anonyme) — bus interne pour le
  SSE de `reports`.
- `reports-mqtt-publisher.service` (`mqtt_publisher.py`) — publie un
  heartbeat toutes les 5s. **Le code lui-même dit "données fictives pour
  tester, à remplacer" — probablement un prototype non finalisé.**
- `oauth2-proxy` — binaire installé à la main (`/usr/local/bin/`, hors apt),
  config `/etc/oauth2-proxy/oauth2-proxy.cfg` (Azure AD).
- `ModemManager` — installé, **aucun modem détecté** (`mmcli -L` vide) :
  probablement résiduel/inutile.

### Paquets installés manuellement (hors base Ubuntu)
`headscale`, `mariadb-server`, `mosquitto`(+clients), `nginx`(+extras,
+dav-ext), `ntfy`, `tailscale`, `uwsgi`(+plugin python3), `python3-pip`/
`pymysql`/`virtualenv`, `nodejs`, `openssh-server`, `ufw`, `apache2-utils`,
`lynx`, `ssmtp`.

## 4. Pas fait / décisions en attente

Rien n'est urgent ici — reprendre dans cet ordre suggéré :

1. **Clarifier avec Marc le statut de** :
   - `/bottle` (app de démo, uwsgi `bottledemo.socket`) — garder ou
     supprimer ?
   - `/bottledev` dans nginx — config morte (rien n'écoute sur `:8081`),
     à nettoyer ou à réactiver ?
   - `reports-mqtt-publisher.service` — prototype heartbeat non finalisé,
     à terminer, remplacer, ou supprimer ?
2. **Automatiser le backup** (script + timer systemd, sur le modèle du
   premier backup manuel section 2) :
   - Rétention **pas encore décidée** (proposition en discussion : 14
     quotidiennes + 8 hebdomadaires).
   - Copie hors serveur **pas encore décidée** (options évoquées : `rsync`
     vers `rpi01`, ou dépôt Git poussé en off-site — voir point 3).
3. **Dépôt Git pour le code/config** (proposé, pas créé) :
   - Repo local (ex. `/opt/docs-infra/`) versionnant `reports/`,
     `headscale-admin/`, `etc/headscale/` (sans clés), `etc/nginx/`,
     `etc/ntfy/` (sans `auth.db`).
   - Destination de la copie distante **pas encore choisie** : nouveau
     dépôt privé GitHub (`mfache/docs-infra`, avec clé de déploiement
     dédiée — pas la clé perso de Marc) **ou** dépôt bare sur `rpi01`.
   - Les données/secrets (dump SQL, `db.env`, `auth.db`, clés
     headscale/ntfy) restent **hors Git**, dans le mécanisme
     tar/mysqldump de la section 2/point 2.
4. Proposition, faite mais pas actée : ajouter dans
   `~/.ssh/config` (sur `rpi01`) un alias `Host docs-ts` →
   `HostName docs.dt.net`, distinct de l'alias `docs` existant (classique,
   port 9922), pour tester le SSH Headscale sans collision.
5. Proposition, faite mais pas actée : committer dans le dépôt `rpinode`
   une copie de référence du point d'entrée WSGI `/var/www/reports/app.py`
   (reconstruit le 9 septembre après l'incident, voir section 6), pour ne
   plus jamais en être totalement dépourvu de sauvegarde.

## 5. Rappel : ce qui est déjà committé et poussé (rien à refaire)

Sur `origin/main` du dépôt `rpinode` (SSH `git@github.com:mfache/rpinode.git`) :

- `71425e7` — Renommage Tailscale → Headscale (UI, logs, MagicDNS).
- `92a040d` — Documentation comptes/ACL SSH Headscale
  (`docs/operations/HEADSCALE_SSH_ACL.md`).
- `09abda9` — Automatisation `docsadmin` + tag `tag:fleet` côté client
  (`headscale_enroll.py`, `tests/test_headscale_enroll.py`).

Comptes et ACL déjà en place et fonctionnels (testés dans les deux sens) :
`fleet` (sur `docs`), `docsadmin` (sur `rpi01`), policy `acl.hujson` avec 4
règles SSH (voir `HEADSCALE_SSH_ACL.md`).

## 6. Rappel : incident survenu et déjà résolu (9 septembre, après-midi)

`/var/www/reports/app.py` avait été accidentellement écrasé par une copie
de `/var/www/headscale-admin/app.py`, cassant l'API `/reports/api/*`
(HTTP 500) le temps de la réparation. Corrigé en reconstruisant le point
d'entrée WSGI correct. Détail complet dans
`docs:/var/www/reports/NOTES-evolutions.md` (section du 9 septembre
après-midi) et dans `HEADSCALE_SSH_ACL.md` section 6.

## 7. Pour reprendre

Relire ce document, puis reprendre à la section 4 dans l'ordre suggéré.
Aucune action corrective urgente n'est nécessaire : `rpinode` (sur `rpi01`)
et toute la chaîne applicative sur `docs` sont opérationnels au moment de
la rédaction.
