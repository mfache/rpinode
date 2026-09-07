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

## 5. Point important : une partie du travail est hors du dépôt Git local

Le code de l’API centrale situé dans `/var/www/reports/api.py` n’est pas dans le
dépôt Git local `rpinode`.

Conséquences :
- une modification locale du dépôt ne suffit pas toujours ;
- certaines corrections doivent être faites directement sur le serveur `docs`
  via SSH ;
- si le comportement attendu n’est pas visible dans le dépôt, vérifie d’abord
  si la logique concernée vit côté serveur.

## 6. Documentation liée

- Documentation API distante :
  [https://docs.deltathermic.be/reports/api/usage](https://docs.deltathermic.be/reports/api/usage)
- Historique des modifications hors dépôt :
  [../integrations/FLEET_API_CHANGES.md](../integrations/FLEET_API_CHANGES.md)

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
