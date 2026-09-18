"""
Isolation globale de la suite de tests vis-a-vis du fichier de session de
chantier (`core.paths.CURRENT_SITE_FILE`, normalement `/tmp/rpinode_current_site.json`).

Pourquoi : plusieurs tests (ex. `test_presence.py::test_auto_site_is_provisional`,
`test_tracker.py::TestPresenceExternalId::test_un_nom_existant_ne_perd_pas_son_external_id`)
appellent directement `services.presence.label_current_location(...)`. Sans cette
redirection, ces tests ecrivent pour de vrai dans le fichier utilise en
PRODUCTION par le boitier pour savoir sur quel chantier il se trouve.

Lancer `./run_tests.sh` sur un boitier en service pouvait alors faire croire au
tracker (et a toute lecture/enregistrement BACnet/Modbus en cours) que
l'appareil se trouvait temporairement sur un chantier de test ("H66",
"AUTO-999999", "Chantier Test Alpha", ...), avec un risque de polluer des
enregistrements jusqu'a ce que le tracker corrige la situation (jusqu'a 60s
plus tard).

Ce module s'execute une seule fois, avant l'import de tous les `test_*.py`,
car `unittest discover` importe le package `tests` avant d'en decouvrir le
contenu. Toute la suite herite donc automatiquement de cette isolation, sans
qu'aucun test individuel n'ait besoin de penser a mocker `CURRENT_SITE_FILE`.
"""
import atexit
import tempfile
from pathlib import Path

import core.paths as paths
import services.presence as presence

_tmp_dir = tempfile.TemporaryDirectory(prefix="rpinode_test_site_")
_fake_current_site_file = Path(_tmp_dir.name) / "current_site.json"

# `core.paths.CURRENT_SITE_FILE` est importe par valeur dans `services.presence`
# (`from core.paths import CURRENT_SITE_FILE`) : il faut rediriger les deux
# references pour que plus aucun code de test n'atteigne le vrai fichier.
paths.CURRENT_SITE_FILE = _fake_current_site_file
presence.CURRENT_SITE_FILE = _fake_current_site_file

atexit.register(_tmp_dir.cleanup)
