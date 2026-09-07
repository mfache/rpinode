**Cahier des Charges pour le Système de Gestion des Tableaux Dynamiques (rpinode Table Engine)**.

---

# Cahier des Charges : Système de Tableaux Dynamiques (rpinode)

## 1. Objectifs
Créer un composant réutilisable pour l'affichage et la manipulation de données tabulaires permettant une personnalisation poussée par l'utilisateur final, tout en assurant la synchronisation avec le serveur central (`docs`).

## 2. Fonctionnalités de l'Interface (UI/UX)
*   **Recherche Globale** : Un champ de recherche unique filtrant instantanément toutes les lignes du tableau sur toutes les colonnes visibles.
*   **Tri Multi-colonnes** : Possibilité de trier par ordre croissant/décroissant en cliquant sur l'en-tête de n'importe quelle colonne.
*   **Gestion des Colonnes** :
    *   **Masquage/Affichage** : Un menu permettant de cocher les colonnes à afficher.
    *   **Ajout Dynamique** : Bouton "+" pour créer une nouvelle colonne (ex: "Numéro de série", "Date d'installation").
    *   **Suppression** : Possibilité de supprimer une colonne personnalisée précédemment créée.
*   **Édition Cellulaire** :
    *   Distinction claire entre les **Champs Systèmes** (ex: MAC, IP, Fabricant OUI) qui sont en lecture seule ou suggérés.
    *   **Champs Utilisateur** (Annotations) qui sont librement éditables.
    *   L'édition d'une valeur ne doit pas interférer avec les autres colonnes.

## 3. Structure des Données (Base de données)
Actuellement, nous utilisons un champ `annotations_json`. Pour supporter la gestion des colonnes, nous devons faire évoluer le schéma :

### A. Table des Définitions de Colonnes (`table_columns`)
Permet de savoir quelles colonnes existent pour quel type de tableau.
*   `table_id` : Identifiant du tableau (ex: `ip_scan`, `modbus_devices`).
*   `name` : Identifiant technique du champ.
*   `label` : Nom affiché à l'utilisateur.
*   `is_mandatory` : Boolean (les colonnes systèmes ne peuvent pas être supprimées).
*   `is_visible` : Boolean (état d'affichage par défaut).
*   `type` : Type de donnée (text, number, date, select).

### B. Table des Valeurs (`discovered_devices` évoluée)
On conserve la structure actuelle mais on clarifie l'usage de `annotations_json`.
*   Le `vendor` (Fabricant) doit être séparé des annotations d'usage.
*   `annotations_json` contiendra uniquement les colonnes personnalisées définies dans `table_columns`.

## 4. Synchronisation avec `docs`
*   **Pousse (Push)** : Lorsqu'une colonne est ajoutée ou une valeur modifiée, l'objet `is_dirty` passe à 1.
*   **Tirage (Pull)** : Le serveur `docs` peut imposer des colonnes standards ou fournir des annotations globales basées sur la MAC.
*   **Format d'échange** : Utilisation d'un dictionnaire d'annotations typé pour ne pas écraser les données inconnues du serveur.

---

## 5. Plan d'Action pour `rpinode`

### Étape 1 : Refonte du Modèle de Données
Je propose d'ajouter une table `custom_columns_def` pour mémoriser les colonnes que vous ajoutez manuellement.

### Étape 2 : Nouveau Composant JavaScript
Développer dans `static/app.js` une classe `TableManager` qui gère :
1.  Le rendu du `<thead>` avec les boutons de tri.
2.  Le filtrage des lignes en fonction du champ de recherche.
3.  La persistance locale (localStorage) des préférences d'affichage (colonnes masquées).

### Étape 3 : Nettoyage de l'IP Scan
*   Séparer le bouton "Éditer Fabricant" du bouton "Ajouter Annotation".
*   Remplacer les `prompt()` par des modales plus propres permettant de choisir la colonne à éditer.
