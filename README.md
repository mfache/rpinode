# Projet rpinode (Refonte de admin_boitier)

Ce dossier `rpinode` contient la nouvelle architecture repensée et modulaire, extraite des concepts de l'ancienne version `admin_boitier`.
L'objectif est d'offrir une meilleure maîtrise des emplacements de fichiers, du moteur de templates ("à la poupée russe") et des flux de données dynamiques, sans s'appuyer sur des frameworks lourds.

## Architecture & Maîtrise des Emplacements

Toute l'organisation du projet repose sur un fichier central : `src/core/paths.py`. Ce fichier calcule les chemins absolus pour garantir que, peu importe d'où est lancé le script, les dossiers sont trouvés.

- `src/` : Le code Python organisé par domaine métier (core, web, services).
- `data/` : C'est le **dossier de persistance**. Tous les fichiers de configuration (JSON, YAML), les bases de données (SQLite) et les logs sont stockés ici. Le système a été pensé pour ne jamais polluer d'autres dossiers avec de l'état local. (Cf: `src/core/config.py`).
- `templates/` : Les composants HTML purs avec marqueurs de variables.
- `static/` : Fichiers statiques (JS, CSS, images).

## Le système de Template "À la Poupée Russe"

Dans `src/web/templating.py`, un moteur ultra-léger basé sur `string.Template` a été encapsulé de manière propre et robuste.

### Le concept
Plutôt que d'avoir des balises de boucles ou de conditions complexes dans le HTML (comme Jinja2), la logique reste en Python. Les vues HTML sont de simples "coquilles" avec des variables `$nom`. 
Le côté "poupée russe" vient du fait que **le Python pré-rend les sous-composants, puis les injecte dans les composants de niveau supérieur.**

### Exemple de flux :
```python
# 1. Rendu d'un sous-composant (la plus petite poupée)
widget_html = render("widget.html", title="CPU", data="En charge...")

# 2. Rendu de la page de contenu (poupée moyenne)
#    On lui passe la petite poupée (widget_html) en paramètre
home_html = render("home.html", user="Admin", widgets=widget_html)

# 3. Rendu du Layout Global (la plus grande poupée)
final_html = render("layout.html", title="Accueil", content=home_html)
```
**Avantage :** Les templates HTML restent 100% lisibles et la logique métier reste totalement dans les contrôleurs Python.

## Mises à jour Dynamiques (SSE)

Pour remplacer les mécanismes d'attente (pooling ou rafraîchissements forcés) complexes de `admin_boitier`, cette architecture implémente les **Server-Sent Events (SSE)**.
C'est un protocole natif HTML5 unidirectionnel (Serveur vers Client) extrêmement léger.

- **Côté Serveur** : `src/web/stream.py` maintient la connexion HTTP ouverte et boucle pour faire des `handler.wfile.write("data: ...\n\n")`.
- **Côté Client (JS)** : Dans `static/app.js`, l'objet `new EventSource("/api/stream")` écoute le serveur en continu. L'événement `.onmessage` est déclenché automatiquement dès qu'une donnée arrive, permettant de cibler et remplacer le texte d'un `#id` dans le DOM (ex: `cpuUpdateZone.textContent = event.data`).

Cette méthode est excellente pour remonter le statut du daemon BACnet, le scan Modbus ou l'état de la 4G sans faire clignoter toute la page web et avec une empreinte CPU minime.

## Exécuter la Démonstration

```bash
cd rpinode
python src/main.py
```
Ouvrir votre navigateur sur `http://localhost:8080`. Vous verrez :
1. Le Layout englobant la Home englobant les Widgets.
2. Le Widget CPU mis à jour dynamiquement toutes les 2 secondes par l'EventSource.

## Tests et Qualité

Pour s'assurer du bon fonctionnement après une modification, une suite de tests est disponible.

### Exécuter les tests
```bash
./run_tests.sh
```

Ces tests vérifient :
- L'existence des chemins et dossiers requis (`paths`).
- Le bon fonctionnement du moteur de templates "poupée russe" (`templating`).
- Le chargement et la sauvegarde de la configuration (`config`).
- La réponse correcte du serveur HTTP (test fonctionnel sur `/`).

## Pratique

L'accès à sudo local est disponible.
Lorsqu'une adaptation a eu lieu et qu'elle est fonctionnelle, il faut redémarrer le service pour que les modifications (notamment Python) soient prises en compte, puis pousser les changements sur GitHub.
La commande suivante est ton amie :
```bash
find . -type f -iname "*.md"
```
Cette url est aussi ton amie : https://docs.deltathermic.be/reports/api/usage

### Redémarrage sécurisé du service
Un script est disponible pour vérifier le code et redémarrer proprement le processus en arrière-plan :
```bash
./run.sh
```
Ce script :
1. Lance automatiquement la suite de tests (`run_tests.sh`).
2. **Interrompt le redémarrage** en cas d'échec (pour rester sur la dernière version stable).
3. Tue l'ancienne instance et relance `main.py` avec `sudo` en cas de succès.

## Gestion des Logs Distants

Afin de préserver la durée de vie de la carte SD des boîtiers, les logs importants sont gérés de deux manières :
1. **Localement** : Limitation stricte de l'espace sur la carte SD grâce à un système de rotation des logs (4 Mo max. au total).
2. **À distance (Centralisation)** : Une tâche d'arrière-plan remonte par lots les journaux (logs) d'exécution vers le serveur maître.
   - Les envois sont **compressés avec GZIP**, ce qui réduit la consommation data sur le réseau cellulaire de plus de **90 %** (très utile pour des logs répétitifs).
   - Chaque log intègre dynamiquement : la date, le niveau (INFO, ERROR, etc.), le module concerné, le nom du `chantier` actuel, et la version du code Git exécuté, facilitant grandement le débogage de la flotte.
   - Une méthode `fleet.get_logs(limit=50, level="ERROR")` est disponible dans le client `FleetClient` pour récupérer ces logs depuis le serveur.

## Gestion de la flotte

Le serveur de synchronisation est docs.deltathermic.be.
Ce serveur est une VM fournie par le service informatique.
Nous avons les pleins pouvoir sur ce serveur pour y installer ce que nous voulons.
Nous n'avons pas la main sur les ouvertures de ports.
Un accès ssh est disponible avec le user mariadb derrière le port 9922.
