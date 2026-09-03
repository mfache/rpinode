# Rapport d'incident : Conflit de processus et démon BACnet — 03/09/2026

## 1. Contexte & Symptômes
- Impossibilité de lire les points BACnet en direct depuis l'interface web et les outils.
- Journalisation de déconnexions/reconnexions MQTT intempestives en boucle dans les logs :
  `services.mqtt_service - WARNING - Déconnecté du broker MQTT` toutes les 3 à 5 secondes.
- Blocage et timeouts sur les requêtes BACnet (`/api/bacnet/tools/read`, `/api/bacnet/suivi/values`).

---

## 2. Processus en conflit identifiés

Lors de l'inspection de la table des processus (`ps aux` / `ps -ef`), **plusieurs instances de l'application tournaient en simultané** :

| PID | Utilisateur | Commande | Origine |
|---|---|---|---|
| `2667883` | `root` | `sudo python3 -u src/main.py` | Lancé manuellement en tâche de fond (`nohup` / terminal) à 07:59 |
| `2667888` | `root` | `python3 -u src/main.py` | Processus enfant du PID `2667883` |
| `2718782` | `root` | `/usr/bin/python3 -u /home/marc/rpinode/src/main.py` | Service systemd `rpinode.service` (redémarré en boucle par `Restart=always`) |
| *plusieurs* | `root` | `/opt/boitier-bacnet/venv/bin/python .../bacnet_daemon.py` | Démons BACnet concurrents spawned par chaque `main.py` |

### Conséquences des doublons :
1. **Conflit MQTT (Client ID unique) :** Les deux instances se connectaient au broker Mosquitto local (`127.0.0.1:1883`) avec le même identifiant client (`rpinode_local_broker`). Le broker expulsait continuellement la connexion précédente à chaque reconnexion.
2. **Conflit UDP BACnet (Port 47808) :** Les instances concurrentes de `bacnet_daemon.py` tentaient de lier le port UDP `47808` sur l'interface réseau `172.31.12.55/23`.
3. **Conflit HTTP (Port 8081) :** L'instance `rpinode.service` échouait à démarrer son serveur web car le port `8081` était monopolisé par le processus orphelin `2667888`.
4. **Conflits SQLite :** Verrous `database is locked` intermittents sur `data/app.db`.

---

## 3. Commandes exécutées pour diagnostiquer et résoudre le problème

### A. Diagnostic des processus et ports
```bash
# 1. Recherche des processus Python et BACnet en cours d'exécution
ps -ef | grep -E "python|bacnet|main.py"

# 2. Vérification des sockets UDP (port BACnet 47808)
sudo ss -ulpn | grep 47808

# 3. Vérification de l'état des services systemd
sudo systemctl list-units --type=service | grep -E "rpinode|boitier|bacnet"
sudo systemctl is-active rpinode.service

# 4. Lecture des journaux systemd
sudo journalctl -u rpinode.service -n 30 --no-pager
```

### B. Arrêt forcé et nettoyage des processus orphelins
```bash
# 1. Arrêt du service systemd
sudo systemctl stop rpinode.service

# 2. Tuer tous les processus main.py et bacnet_daemon orphelins
sudo pkill -9 -f "main.py"
sudo pkill -9 -f "bacnet_daemon"
sudo pkill -9 -f "points.py"

# 3. Vérification que la table des processus est propre
ps -ef | grep -E "python|bacnet|main.py"
```

### C. Relance propre du service
```bash
# 1. Redémarrage propre via systemd
sudo systemctl restart rpinode.service

# 2. Vérification du statut actif
sudo systemctl is-active rpinode.service
sudo journalctl -u rpinode.service -n 25 --no-pager
```

### D. Validation du fonctionnement BACnet
```bash
# 1. Test de lecture BACnet via bus MQTT local
python3 -c "
import json, queue, paho.mqtt.client as mqtt
q = queue.Queue()
c = mqtt.Client()
c.on_message = lambda cl, u, m: q.put(json.loads(m.payload.decode()))
c.connect('127.0.0.1', 1883)
c.loop_start()
c.subscribe('rpinode/bacnet/res/read/test1')
c.publish('rpinode/bacnet/cmd/read', json.dumps({'job_id': 'test1', 'points': [{'address': '172.31.12.145', 'object_id': 'analog-input:104', 'device_id': 1200}]}))
print('RESULT:', q.get(timeout=4))
c.loop_stop()
"
# Résultat obtenu : [{'device_id': 1200, 'address': '172.31.12.145', 'object_id': 'analog-input:104', 'value': '24.01999855041504', 'error': None}]

# 2. Test direct via l'API HTTP du serveur
curl -s -X POST http://localhost:8081/api/bacnet/tools/read \
     -H "Content-Type: application/json" \
     -d '{"address": "172.31.12.145", "object_id": "analog-input:104", "device_id": 1200}'
# Résultat obtenu : {"status": "ok", "value": "24.01999855041504"}
```

---

## 4. Correctifs pérennes apportés dans le code

1. **`src/main.py` (`run_bacnet_daemon`) :**
   - Remplacement de `pkill -f` simple par `subprocess.run(["pkill", "-9", "-f", "bacnet_daemon.py"], check=False)` pour garantir l'élimination systématique de tout démon orphelin avant de démarrer le nouveau sous-processus.

2. **`run.sh` :**
   - Mise à jour pour vérifier si `rpinode.service` est activé sous systemd.
   - Si systemd est présent : exécute `sudo systemctl stop rpinode.service`, nettoie tous les processus résiduels via `pkill -9`, puis relance via `sudo systemctl restart rpinode.service` au lieu de lancer un doublon en `nohup`.
