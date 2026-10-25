# Interface web derrière un reverse proxy avec préfixe

Ajouté le 2026-10-25, pour le portail d'accès aux boîtiers sur `docs`
(`https://docs.deltathermic.be/rpi/<boîtier>/`, sans sous-domaine).

## Principe

Le proxy retire le préfixe avant de transmettre la requête (le boîtier reçoit
toujours `/api/...`, `/static/...`, comme en accès direct), et l'annonce dans
l'en-tête :

```
X-Forwarded-Prefix: /rpi/rpi01
```

`WebAdminHandler._base_url()` (`src/web/server.py`) utilise cet en-tête pour
construire les URL des pages : `${base_url}` dans les templates,
`window.CONFIG.baseUrl` côté JavaScript. Sans l'en-tête, c'est le `base_url`
de `data/config.json` (vide par défaut) : l'accès direct (AP, LAN, Tailscale)
est inchangé.

- Format accepté : `(/[A-Za-z0-9._-]+)+` (barre finale tolérée). Tout autre
  valeur est ignorée, car le préfixe est injecté tel quel dans le HTML et le JS.
- Le cookie de session mobile est posé avec `Path=<préfixe>/` : plusieurs
  boîtiers partagent le domaine de `docs`.
- `static/manifest.json` et `static/sw.js` utilisent des chemins relatifs,
  valables avec ou sans préfixe.

## Exemple nginx (docs)

```nginx
location /rpi/rpi01/ {
    proxy_pass http://100.64.0.5:8081/;          # la barre finale retire le préfixe
    proxy_set_header X-Forwarded-Prefix /rpi/rpi01;
    proxy_http_version 1.1;
    proxy_buffering off;                         # SSE (/api/stream, ...)
    proxy_read_timeout 1h;
}
```

## Règle pour le code

Toute URL envoyée au navigateur doit commencer par `${base_url}` (templates,
HTML généré en Python) ou `window.CONFIG.baseUrl` (JavaScript). Une URL
`"/api/..."` sans préfixe fonctionne en direct mais casse derrière le proxy.
