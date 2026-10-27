# Terminal de secours via Bluetooth (SPP + login système)

Ce module ajoute un canal d'accès de secours au boîtier : un terminal série
Bluetooth (profil SPP - Serial Port Profile) qui donne directement un prompt
`login:` du système, authentifié avec un **compte Unix réel** (mot de passe
système, PAM standard). Aucune logique d'authentification custom : c'est le
binaire `login` de la distribution qui s'en charge, exactement comme sur un
port série physique ou un TTY local.

Ce mécanisme est indépendant de l'application Python `rpinode` : il fonctionne
même si le serveur web/`src/main.py` est arrêté ou plante. C'est un complément
au mode secours WiFi (`RPIRESCUE`, voir `src/core/wifi.md`), pas un
remplacement. Depuis la suppression du point d'accès WiFi (2026-10-27), c'est
le seul accès sur place quand ni la 4G, ni le WiFi du chantier, ni
`RPIRESCUE` ne sont disponibles.

## Architecture

```
Client (PC / téléphone)
   │  1. Appairage Bluetooth (Just Works, sans code — bt-agent NoInputNoOutput)
   ▼
bluetoothd (mode --compat)
   │  2. Connexion SPP sur le canal RFCOMM 1 (annoncé en SDP)
   ▼
rfcomm-pty-terminal (systemd: rfcomm-terminal.service)
   │  3. Accepte la socket RFCOMM et lance agetty sur un pseudo-terminal
   ▼
agetty pts/N → /bin/login
   │  4. Prompt "login:" / "Password:" — comptes système réels (ex: marc)
   ▼
Shell système normal (bash), droits Unix habituels de l'utilisateur
```

## Fichiers

- `bluetooth-compat.conf` : drop-in systemd pour `bluetooth.service`, ajoute
  le flag `--compat` à `bluetoothd` (nécessaire pour `sdptool` et l'ancien
  socket SDP utilisé par `rfcomm`).
- `bt-agent.service` : agent d'appairage headless (`bt-agent` du paquet
  `bluez-tools`), capacité `NoInputNoOutput` → pairing "Just Works" (aucun
  code à saisir, pas d'écran nécessaire). S'occupe aussi de rendre l'adaptateur
  `powered`, `discoverable` et `pairable` au démarrage.
- `rfcomm-terminal.service` : enregistre le profil Serial Port (SDP, canal 1)
  et lance `rfcomm-pty-terminal`, qui attend une connexion et exécute `agetty` sur
  un pseudo-terminal à chaque connexion (boucle automatique après déconnexion).
- `rfcomm-pty-terminal` : relais socket RFCOMM <-> pty (Python). Remplace
  `rfcomm watch`, qui libère la liaison au moindre hangup du tty
  (`RELEASE_ONHUP`) : comme `login` fait toujours un `vhangup()`, la connexion
  tombait juste après la saisie du nom d'utilisateur.
- `rfcomm-ssh-relay` + `rfcomm-ssh.service` : SSH par Bluetooth. Relaie le
  canal RFCOMM 2 vers le sshd local (127.0.0.1:22) ; le client parle SSH
  directement sur la socket RFCOMM, avec l'authentification SSH habituelle.
  Côté PC Windows : `bt-ssh-bridge.py` (TCP 127.0.0.1:2222 -> RFCOMM canal 2).
- `install.sh` : installe le paquet `bluez-tools`, pose les 3 fichiers
  ci-dessus dans `/etc/systemd/...` et active les services.

## Installation sur un boîtier

```bash
cd drivers/bluetooth_terminal
sudo ./install.sh
```

Vérification :

```bash
systemctl status bluetooth bt-agent.service rfcomm-terminal.service
sudo sdptool records local | grep -A6 "Serial Port"
```

## Utilisation côté client

1. Chercher le boîtier en Bluetooth (nom = hostname du boîtier, ex: `rpi01`).
2. S'appairer : aucune confirmation ni code PIN n'est demandé (Just Works).
3. Ouvrir un terminal série sur le port Bluetooth SPP associé :
   - **Linux** : `sudo rfcomm connect 0 <MAC_boitier> 1` puis
     `sudo screen /dev/rfcomm0 115200` (ou `minicom`/`picocom`).
   - **Windows** : après appairage, un port `COMx` apparaît (Bluetooth Serial
     Port) → PuTTY en mode série sur ce port, 115200 bauds.
   - **Android** : une appli type "Serial Bluetooth Terminal" (supporte SPP).
   - **iOS** : non supporté (Apple ne permet pas le SPP classique côté iOS).
4. Se logguer avec un compte système réel (utilisateur + mot de passe Unix).

## ⚠️ Implications de sécurité — à valider avant déploiement flotte

- **Le pairing est "Just Works"** : n'importe quel appareil à portée Bluetooth
  (~10 m) peut s'appairer sans code secret : il n'y a aucun mot de passe au
  niveau de l'appairage : **la seule barrière réelle est le login système** (utilisateur
  + mot de passe Unix).
- Le compte `root` est verrouillé (`passwd -S root` → `L`) sur les boîtiers
  actuels : une tentative de login `root` échouera même via ce canal.
- Aucune restriction PAM spécifique à ce canal n'est mise en place par défaut
  (pas de `pam_access` limitant les comptes autorisés). Si besoin, ajouter une
  règle dans `/etc/security/access.conf` limitant les logins autorisés sur
  `rfcomm0` à des comptes/groupes précis.
- Le rayon Bluetooth étant court, le risque est nettement plus faible qu'un
  service exposé sur IP, mais reste un point d'entrée physique à portée de
  proximité (parking, atelier, etc.). À évaluer selon le contexte de
  déploiement (chantier public vs. local technique fermé).

## Désinstallation

```bash
sudo systemctl disable --now rfcomm-terminal.service bt-agent.service
sudo rm -f /etc/systemd/system/rfcomm-terminal.service /etc/systemd/system/bt-agent.service
sudo rm -f /etc/systemd/system/bluetooth.service.d/bluetooth-compat.conf
sudo systemctl daemon-reload
sudo systemctl restart bluetooth
```
