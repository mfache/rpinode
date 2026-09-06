### Erreur d'exécution des tests (ImportError: 'tests')

Lors de l'exécution initiale du script `run_tests.sh`, une erreur `ImportError: Start directory is not importable: 'tests'` est survenue. Cela était dû au fait que le script `unittest discover tests` était exécuté depuis un répertoire incorrect, ne permettant pas à Python d'importer le module `tests`.

**Correction:**
Le script `run_tests.sh` a été modifié pour inclure `cd "$(dirname "$0")"` au début. Cette commande permet au script de se placer dans son propre répertoire (`rpinode/`) avant d'exécuter les tests, assurant ainsi que `tests` est correctement importable.