# rpinode Superviseur & Proxy de Boot (Rust)

Ce composant est un reverse-proxy ultra-léger (< 5 Mo RAM, 0% CPU) écrit en **Rust**. Il est conçu pour assurer une disponibilité HTTP continue (zéro coupure réseau) et diffuser les logs de démarrage en direct via Server-Sent Events (SSE) lors des redémarrages de `rpinode`.

---

## 🏗️ Architecture

```text
       Navigateur Web (Port public : 8081)
                       │
                       ▼
    ┌──────────────────────────────────────┐
    │     Superviseur / Proxy RUST         │
    │  (Toujours actif, zéro coupure)      │
    └──────┬────────────────────────┬──────┘
           │ (Opération normale)    │ (Pendant le redémarrage Python)
           ▼                        ▼
    ┌───────────────┐      ┌───────────────────────────────┐
    │  rpinode.py   │      │ Page de Boot + Terminal Live  │
    │  (Port 8082)  │      │ Tailing SSE de rpinode.log    │
    └───────────────┘      └───────────────────────────────┘
```

### Fonctionnement :
1. **Mode Normal** : Le proxy transmet toutes les requêtes (GET, POST, SSE `/api/stream`, etc.) de façon transparente au serveur Python sur le port interne (par exemple `8082`).
2. **Mode Redémarrage / Maintenance** :
   - Dès que le serveur Python coupe pour se relancer (`os.execve` ou `systemctl restart`), le proxy Rust intercepte instantanément les requêtes HTTP.
   - Au lieu d'afficher une erreur réseau `ERR_CONNECTION_REFUSED` ou un 502, il sert une interface de démarrage avec un terminal temps réel.
   - La page se connecte à `/supervisor/stream` (SSE) : le proxy lit `rpinode.log` en continu et pousse chaque nouvelle ligne de log dans le terminal du navigateur au fur et à mesure de l'initialisation du réseau, du Wi-Fi, de Tailscale et des démons.
3. **Reconnexion automatique** : Une routine d'arrière-plan teste `/api/status` sur le port Python toutes les 600 ms. Dès que Python est prêt, un événement SSE ordonne au navigateur de recharger la page automatiquement.

---

## ⚙️ Variables d'environnement / Configuration

| Variable | Valeur par défaut | Description |
|---|---|---|
| `PORT` | `8081` | Port public exposé au navigateur |
| `BACKEND_PORT` | `8082` | Port d'écoute interne du serveur Python `rpinode` |
| `LOG_FILE` | `/tmp/rpinode/log/rpinode.log` | Chemin du fichier de log surveillé |

---

## 🔨 Compilation

### 1. Sur le Raspberry Pi directement (si Rust/Cargo est installé)
```bash
cd supervisor
cargo build --release
# Le binaire se trouve dans target/release/rpinode-supervisor
```

### 2. Cross-compilation depuis un PC (x86_64 vers ARM Raspberry Pi)
Pour Raspberry Pi OS 64-bit (aarch64) :
```bash
# Installer le compilateur croisé
cargo install cross

# Compiler pour ARM64
cross build --target aarch64-unknown-linux-gnu --release
```

Pour Raspberry Pi OS 32-bit (armv7) :
```bash
cross build --target armv7-unknown-linux-gnueabihf --release
```

---

## 🚀 Intégration Systemd (Optionnelle)

Exemple d'unité systemd `/etc/systemd/system/rpinode-supervisor.service` :

```ini
[Unit]
Description=rpinode Rust Supervisor & Live Boot Proxy
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/home/marc/rpinode
Environment="PORT=8081"
Environment="BACKEND_PORT=8082"
Environment="LOG_FILE=/tmp/rpinode/log/rpinode.log"
ExecStart=/home/marc/rpinode/supervisor/target/release/rpinode-supervisor
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

Dans la configuration Python `data/config.json`, il suffit alors de définir `"port": 8082` pour que le serveur Python écoute sur le port interne.
