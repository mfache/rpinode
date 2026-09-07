---
name: rpinode-patterns
description: Règles d’architecture et de travail pour modifier le projet rpinode (chemins, templates, SSE, persistance, validation, serveur docs).
---

# rpinode — règles de travail

Utilise ce skill chaque fois que tu modifies le projet `rpinode`.

Objectif : garder une architecture cohérente, éviter les régressions, et suivre
les conventions du projet.

## 1. Langue et communication

- Marc parle français.
- Réponds en français, sauf demande explicite contraire.

## 2. Règle absolue sur les chemins

Le projet source est dans `/home/marc/rpinode/`.
Le dépôt Git de base est `/home/marc/rpinode/`.
L’URL locale de référence est `http://localhost:8082/`.

Ne code jamais un chemin en dur dans le projet.

Tous les chemins doivent venir de `src/core/paths.py`.

Utilise uniquement les constantes exposées par ce module, par exemple :

- `paths.SRC_DIR`
- `paths.DATA_DIR`
- `paths.TEMPLATES_DIR`

Exemple :

```python
config_file = paths.DATA_DIR / "config.json"
```

## 3. Templates HTML : logique en Python

Les templates dans `templates/` doivent rester simples.

Règle :
- pas de logique métier dans le HTML ;
- le HTML ne sert qu’à insérer des variables avec des placeholders ;
- la composition se fait en Python.

Utilise `render(template_name, **context)` depuis
`src/web/templating.py`.

Méthode attendue :
1. rendre les petits composants ;
2. injecter ces composants dans une vue plus grande ;
3. injecter la vue dans le layout final.

Exemple :

```python
widget = render("widget.html", title="Status", value="OK")
page = render("home.html", content=widget)
layout = render("layout.html", body=page)
```

## 4. Mises à jour dynamiques : utiliser SSE

Pour les mises à jour en temps réel, utilise les Server-Sent Events (SSE).

Ne mets pas en place de polling si une mise à jour temps réel est nécessaire.

- Côté serveur : `src/web/stream.py`
- Côté client : `EventSource` dans `static/app.js`

## 5. Persistance et stockage

Toutes les données persistantes doivent aller dans `data/` :

- JSON
- SQLite
- logs
- autres fichiers d’état

N’écris pas de données persistantes ailleurs.

Utilise aussi le système de configuration central dans
`src/core/config.py`.

## 6. Validation obligatoire

Avant de considérer une tâche comme terminée, lance :

```sh
./run_tests.sh
```

Ne dis pas que c’est validé si les tests n’ont pas été exécutés.

## 7. Redémarrage du service

Pour redémarrer le service, utilise :

```sh
./run.sh
```

Ce script gère :
- les tests ;
- le redémarrage ;
- le basculement sécurisé avec `sudo`.

Ne remplace pas ce flux par une méthode ad hoc sans raison.

## 8. Documentation à consulter

Avant une modification importante, vérifie la documentation du dépôt.

Cherche en priorité :
- `README.md`
- `HOWTO.md`
- [docs/DOCS_SERVER_ACCESS.md](../../../docs/DOCS_SERVER_ACCESS.md) si une tâche touche le serveur central `docs`
- tout autre fichier `.md` utile.

But :
- comprendre le contexte métier ;
- éviter de contredire une convention déjà documentée.

## 9. Documentation API externe

Pour l’API distante, la référence est :

- [https://docs.deltathermic.be/reports/api/usage](https://docs.deltathermic.be/reports/api/usage)

Si une tâche touche cette API, appuie-toi sur cette documentation.

## 10. Accès SSH au serveur `docs`

Le serveur central de synchronisation est `docs.deltathermic.be`.

Accès SSH de référence :

```sh
ssh -p 9922 mariadb@docs.deltathermic.be
```

Informations utiles sur ce serveur :

- accès MariaDB : `/etc/boitier-fleet/db.env`
- script API distant : `/var/www/reports/api.py`
- script de redémarrage des services : `/home/mariadb/bin/https`
- documentation locale dédiée : [docs/DOCS_SERVER_ACCESS.md](../../../docs/DOCS_SERVER_ACCESS.md)

Si une tâche concerne l’API centrale ou la base distante, n’essaie pas de la
traiter uniquement dans le dépôt local : l’intervention peut devoir être faite
sur le serveur `docs` via SSH.

## 11. Raccourcis de commande

Si l’utilisateur dit `git` ou `pousse`, il faut :
1. préparer les opérations Git nécessaires ;
2. effectuer le `push`.

## 12. Résumé opérationnel

Quand tu travailles sur `rpinode`, applique toujours ces règles :

1. répondre en français ;
2. ne jamais coder de chemins en dur ;
3. garder la logique en Python, pas dans les templates ;
4. utiliser SSE pour le temps réel ;
5. stocker la persistance dans `data/` ;
6. utiliser la config centrale ;
7. lancer `./run_tests.sh` avant de conclure ;
8. utiliser `./run.sh` pour redémarrer ;
9. lire la documentation du dépôt si le contexte n’est pas clair ;
10. utiliser l’accès SSH à `docs` si une tâche touche le serveur central.
