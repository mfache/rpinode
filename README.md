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

## Pratique

L'accès à sudo local est disponible.

## Gestion de la flotte

Le serveur de synchronisation est docs.deltathermic.be.
Ce serveur est une VM fournie par le service informatique.
Nous avons les pleins pouvoir sur ce serveur pour y installer ce que nous voulons.
Nous n'avons pas la main sur les ouvertures de ports.
Un accès ssh est disponible avec le user mariadb derrière le port 9922.
