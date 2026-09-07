# Rapport d'Investigation : Synchronisation RPINODE <> Server (docs)

## 1. Problème Actuel
L'interface affiche une **pastille orange**, indiquant un échec de synchronisation (Erreur 500) lors des appels à l'API sur `docs.deltathermic.be`.

### Symptômes :
- Erreur de décodage JSON lors de la réception des réponses du serveur.
- Blocage potentiel lors du "push" des profils réseau ou des colonnes d'inventaire.

## 2. Investigations & État des Lieux
L'analyse précédente a révélé les points suivants :

### A. Permissions Base de Données (Corrigé ?)
La table `boitier_table_columns` a été ajoutée sur MariaDB, mais l'utilisateur `boitier_app` n'avait pas les droits `SELECT/INSERT` dessus.
*   **Action effectuée** : `GRANT ALL PRIVILEGES ON dt.boitier_table_columns TO 'boitier_app'@'%';`

### B. Format des Données (Incohérence `addresses`)
Il existe une divergence de format pour la colonne `addresses` dans `site_network_profiles` :
- **Local (SQLite)** : Actuellement stocké sous forme de chaîne (ex: `"192.168.1.1, 192.168.1.2"`).
- **Serveur (MariaDB)** : Attendu au format JSON.
*   **Risque** : Erreurs de sérialisation `pymysql` ou échec du `json.loads()` côté Python.

### C. Importations manquantes
Une erreur `NameError: name 'load_ipscan_results' is not defined` a été identifiée dans `reporter.py`. Cela empêchait le cycle de statut de se terminer correctement.

## 3. Plan d'Action (Priorités)

### Étape 1 : Unification du format JSON
Modifier `src/services/network.py` et les schémas SQLite locaux pour traiter les listes d'adresses IP comme du JSON pur, assurant la compatibilité avec le serveur MariaDB.

### Étape 2 : Monitoring de la stabilité
Surveiller les logs de `docs.deltathermic.be` (API Flask) pour vérifier si l'erreur 500 persiste après la correction des permissions.

### Étape 3 : Gestion des Conflits
Affiner la logique `updated_by` et `last_synced` pour éviter que deux boîtiers n'écrasent leurs annotations respectives sur un même équipement réseau (conflit de fabricant/annotation).

### Étape 4 : UI Mobile
Optimiser le sélecteur de colonnes (`Column Picker`) pour qu'il reste utilisable sur smartphone, permettant de masquer les colonnes moins prioritaires sans perdre les données.

---
*Date : 2026-08-29*
*Statut : En cours d'observation*
