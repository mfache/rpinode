# CA interne "Deltathermic" et HTTPS pour les boîtiers (`*.dt.net`)

**Mise en place : 8 septembre 2026.**

## 1. Objectif

Après la migration vers Headscale et le changement de domaine MagicDNS en
`dt.net` (voir
[`HEADSCALE_MIGRATION_STATUS.md`](HEADSCALE_MIGRATION_STATUS.md) et
[`../integrations/HEADSCALE_AUTO_ENROLL.md`](../integrations/HEADSCALE_AUTO_ENROLL.md)),
`https://rpi01.dt.net` ne fonctionnait plus : Headscale (auto-hébergé) ne
supporte pas l'émission automatique de certificats TLS via `tailscale
cert` (contrairement au SaaS Tailscale, qui utilise Let's Encrypt intégré
à son control-server) :

```
$ sudo tailscale cert rpi01.dt.net
500 Internal Server Error: your Tailscale account does not support getting TLS certs
```

`dt.net` n'étant pas un domaine public réellement possédé (utilisé
uniquement en interne par MagicDNS), aucune CA publique ne peut de toute
façon y émettre un certificat de confiance. La solution retenue : une
**CA interne "Deltathermic"**, dont le certificat racine est installé une
fois par appareil client (PC, etc.) dans le magasin de confiance du
système.

## 2. Emplacement de la CA : `docs`

La CA (clé privée + certificat racine) vit sur `docs.deltathermic.be`,
dans `/etc/deltathermic-ca/` :

- `ca.key` (clé privée root, `chmod 600`, **ne jamais copier sur un
  boîtier**).
- `ca.crt` (certificat racine public, `chmod 644`).
- `issued/` : certificats déjà émis (feuilles), avec leur clé privée.

Centraliser la CA sur `docs` suit la même logique que les clés de
pré-authentification Headscale : un seul endroit à protéger, plutôt que de
distribuer une capacité de signature sur chaque Pi.

### Génération de la CA (déjà faite, pour référence/reproduction)

```bash
sudo mkdir -p /etc/deltathermic-ca && sudo chmod 755 /etc/deltathermic-ca
sudo openssl genrsa -out /etc/deltathermic-ca/ca.key 4096
sudo chmod 600 /etc/deltathermic-ca/ca.key
sudo openssl req -x509 -new -nodes -key /etc/deltathermic-ca/ca.key -sha256 -days 3650 \
    -out /etc/deltathermic-ca/ca.crt \
    -subj "/O=Deltathermic/CN=Deltathermic Internal CA"
sudo chmod 644 /etc/deltathermic-ca/ca.crt
```

Validité : 10 ans (2026-09-08 → 2036-09-05).

## 3. Certificat émis : wildcard `*.dt.net`

Plutôt qu'un certificat par boîtier (`rpi01.dt.net`, puis `rpi02.dt.net`,
...), un **certificat wildcard unique** couvre tous les noms actuels et
futurs de la flotte :

```bash
NAME=wildcard.dt.net
openssl genrsa -out "$NAME.key" 2048
openssl req -new -key "$NAME.key" -out "$NAME.csr" -subj "/CN=*.dt.net"
cat > "$NAME.ext" <<EOF
subjectAltName = DNS:*.dt.net, DNS:dt.net
extendedKeyUsage = serverAuth
EOF
sudo openssl x509 -req -in "$NAME.csr" -CA /etc/deltathermic-ca/ca.crt \
    -CAkey /etc/deltathermic-ca/ca.key -CAcreateserial \
    -out "$NAME.crt" -days 730 -sha256 -extfile "$NAME.ext"
```

Validité : 2 ans (2026-09-08 → 2028-09-07). **À renouveler avant cette
date** (pas d'automatisation de renouvellement pour l'instant, voir
section 6).

Le même couple `wildcard.dt.net.crt` / `.key` est copié sur **chaque**
boîtier (`rpi01` aujourd'hui, futurs `rpiNN` demain) : voir section 4.

## 4. Côté boîtier (`rpi01`, à répéter pour les futurs `rpiNN`)

### Fichiers déployés dans `/etc/rpinode/tls/`

- `deltathermic-ca.crt` : certificat racine public (pour référence locale
  et pour être servi en téléchargement, voir section 5).
- `wildcard.dt.net.crt` / `wildcard.dt.net.key` : certificat feuille +
  clé privée, permissions `644` / `600`.
- `install-ca.bat` : script Windows d'installation automatique de la CA
  (voir section 5), servi tel quel par nginx.

Copiés depuis `docs` via SSH (`scp`/`cat` au travers d'un tunnel SSH), pas
de secret transmis en clair sur le réseau.

### `nginx` en reverse-proxy TLS

`nginx` (absent par défaut, installé pour l'occasion :
`sudo apt-get install -y nginx`) écoute sur `:443` et `:80`, et proxifie
vers **le port 8081** — le superviseur Rust (`supervisor/`), **pas**
directement le port 8082 (backend Python interne) — pour conserver la
page de démarrage/reconnexion automatique du superviseur pendant les
redémarrages de `rpinode` (voir `supervisor/README.md`).

Fichier `/etc/nginx/sites-available/rpinode` (activé via lien symbolique
dans `sites-enabled/`, site `default` désactivé) :

```nginx
server {
    listen 80;
    server_name _;

    location = /ca.crt {
        alias /etc/rpinode/tls/deltathermic-ca.crt;
        default_type application/x-x509-ca-cert;
    }

    location / {
        return 301 https://$host$request_uri;
    }
}

server {
    listen 443 ssl;
    http2 on;
    server_name _;

    ssl_certificate     /etc/rpinode/tls/wildcard.dt.net.crt;
    ssl_certificate_key /etc/rpinode/tls/wildcard.dt.net.key;

    location / {
        proxy_pass http://127.0.0.1:8081;
        proxy_http_version 1.1;

        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # Support SSE (/api/stream, /supervisor/stream)
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection $connection_upgrade;
        proxy_buffering off;
        proxy_read_timeout 3600s;
    }
}
```

Le `map $http_upgrade $connection_upgrade { default upgrade; "" close; }`
nécessaire au support SSE a été ajouté dans le bloc `http {}` de
`/etc/nginx/nginx.conf` (même pattern que sur `docs`, voir
[`../integrations/FLEET_API_CHANGES.md`](../integrations/FLEET_API_CHANGES.md)).

### Piège rencontré : port 443 déjà utilisé

Au premier démarrage de `nginx`, `bind() to 0.0.0.0:443 failed (98:
Address already in use)`. Cause : `tailscaled` gardait un **listener
résiduel** sur `443` lié à l'ancienne IP Tailscale SaaS
(`100.66.68.35:443`, plus valide depuis la bascule vers Headscale) —
probablement un reliquat lié à la fonctionnalité `tailscale
serve`/certificats HTTPS, jamais nettoyé lors du changement de réseau.
Résolu par un simple `sudo systemctl restart tailscaled` (ne casse pas la
session Headscale, contrairement à `logout`) avant de relancer `nginx`.

## 5. Installer la CA sur un poste client (une fois par appareil)

Instructions pas à pas pour Marc : voir `README_FOR_HUMAN_ONLY.md`
(section "HTTPS sans avertissement").

Aucun "lien magique" côté navigateur n'est possible : les navigateurs
refusent volontairement qu'une page web installe une CA de confiance en
un clic (sinon n'importe quel site pourrait faire confiance à sa propre
CA). Deux options sont proposées :

### Script automatique (recommandé)

`http://<nom-du-rpi>.dt.net/install-ca.bat` (servé en clair, comme
`/ca.crt`, même table `location =` dédiée dans la config nginx) :
download le certificat puis l'installe via
`certutil -user -addstore -f ROOT` — **dans le magasin de l'utilisateur
courant**, donc **sans élévation UAC**. Suffisant pour Chrome/Edge (ils
utilisent le magasin de certificats Windows). Firefox a son propre
magasin indépendant et n'est pas couvert par cette méthode.

### Manuel (fallback)

Télécharger `http://<nom-du-rpi>.dt.net/ca.crt`, puis l'installer via
l'assistant Windows dans le magasin **Autorités de certification racines
de confiance** (nécessite de choisir "Ordinateur local", donc une
élévation UAC, mais fonctionne aussi pour Firefox si configuré pour
utiliser le magasin système).

## 6. Limites connues / pistes d'amélioration

- **Pas d'automatisation pour les futurs `rpiNN`** : contrairement à
  l'enrôlement Headscale (voir `HEADSCALE_AUTO_ENROLL.md`), la copie du
  certificat wildcard sur un nouveau boîtier reste manuelle (SSH). Le
  certificat étant un wildcard `*.dt.net`, il pourrait être distribué
  automatiquement à l'étape d'enrôlement Headscale existante,
  potentiellement dans une prochaine itération.
- **Renouvellement du certificat wildcard non automatisé** : expire le
  2028-09-07. Pas de rappel automatique actuellement — à suivre
  manuellement, ou à ajouter comme tâche planifiée plus tard.
- **Pas de révocation (CRL/OCSP)** : si une clé privée de boîtier était
  compromise, il n'y a aujourd'hui aucun mécanisme pour invalider
  spécifiquement ce certificat côté clients (il faudrait régénérer un
  nouveau wildcard et le redéployer partout, ou passer à des certificats
  par boîtier si ce risque devient significatif).
- La clé privée de la CA (`/etc/deltathermic-ca/ca.key`) ne doit **jamais**
  quitter `docs`. Sa compromission permettrait de signer un certificat
  valide pour n'importe quel `*.dt.net`.
- **Procédure d'accès utilisateur pas encore écrite ni hébergée sur
  `docs`** : les utilisateurs passeront d'abord par
  `docs.deltathermic.be` pour rejoindre/accéder aux boitiers, pas par ce
  dépôt Git. Une page dédiée (rejoindre Headscale + installer la CA +
  trouver l'adresse du bon `rpiNN`) reste à écrire et à publier côté
  `docs`. Voir `README_FOR_HUMAN_ONLY.md` (section TODO) pour le détail
  de cette tâche.

## 7. Validation effectuée

- `curl --cacert /etc/rpinode/tls/deltathermic-ca.crt https://rpi01.dt.net/`
  → `200 OK` (chaîne de confiance valide).
- `curl http://rpi01.dt.net/` → `301` vers `https://rpi01.dt.net/`.
- `curl http://rpi01.dt.net/ca.crt` → `200 OK`, `Content-Type:
  application/x-x509-ca-cert`.
- `curl http://rpi01.dt.net/install-ca.bat` → `200 OK`,
  `Content-Type: application/octet-stream`.
- `rpi01` toujours `online` sur Headscale après le `restart` de
  `tailscaled` (`tailscale status`).
