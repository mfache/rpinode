# Raccourcis du chantier

Ajouté le 27 octobre 2026. Un raccourci est l'adresse web d'un équipement du
LAN du chantier (GTC, automate…), par exemple `https://10.103.40.7/UI/` pour la
GTC Johnson Controls de CCSE.

## Sur le boîtier

Menu Réseau > Raccourcis (`/network/shortcuts`), ou le bouton « ＋ » à côté des
ports 80/443 dans Inventaire > Scanner le réseau. Les raccourcis sont rangés
par chantier (table `site_shortcuts`) et suivent le boîtier.

L'adresse doit contenir l'IPv4 privée de l'équipement : le boîtier annonce une
route `/32` par équipement (`services/network_config.publish_tailscale_routes`)
et docs l'approuve dans Headscale. **Seuls les équipements ayant un raccourci
sont joignables par Tailscale** : les réseaux entiers du chantier ne sont plus
annoncés. Les routes sont recalculées à chaque modification et à chaque
passage du tracker (30 s), donc aussi après un changement de chantier.

## Sur docs

La liste complète est envoyée dans `/sync` (clé `shortcuts`, seulement quand
elle change) et remplace celle du chantier sur docs (table
`chantier_raccourcis`). La page « Boîtiers » (`/reports/rpi`) affiche les
raccourcis sous le boîtier de chaque chantier, avec les mêmes droits.

Le clic ouvre l'adresse réelle : **il faut Tailscale sur le poste** (le client
Windows accepte les routes par défaut). L'équipement sert en général un
certificat auto-signé, à accepter une fois dans le navigateur.
