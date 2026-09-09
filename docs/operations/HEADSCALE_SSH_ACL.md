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

**Pour un futur `rpiNN`** : poser le tag `tag:fleet` sur son nœud
(`headscale nodes tag -i <ID> -t tag:fleet`) et créer le compte `docsadmin`
dessus (avec le même `/etc/sudoers.d/docsadmin`) fait partie de son
provisioning, au même titre que l'enrôlement Headscale automatique
(voir [`../integrations/HEADSCALE_AUTO_ENROLL.md`](../integrations/HEADSCALE_AUTO_ENROLL.md)).
Ce n'est pas encore automatisé.

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
