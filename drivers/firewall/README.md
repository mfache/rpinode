# Pare-feu du boîtier (nftables)

Ajouté le 25 octobre 2026. Avant, aucun filtrage : SSH (mot de passe
accepté), l'interface (8081/8082, sans authentification) et nginx étaient
joignables sur toutes les interfaces, y compris la 4G.

## Règles (`rpinode-firewall.nft`, table `inet rpinode_pf`)

| Interface | Entrées autorisées |
|---|---|
| toutes | réponses (ct established), ICMP/ICMPv6, Tailscale UDP 41641, réponses DHCP |
| `tailscale0` | tout : administration, et proxy du portail docs (`/rpi/<boîtier>/`) |
| `wwan0` (4G) | rien d'autre |
| réseaux du chantier (`eth0`, `wlan0` client) | BACnet UDP 47808, SSH 22 (secours `RPIRESCUE`) |
| transfert | tout, sauf les nouvelles connexions venant de `wwan0` |

L'interface n'est joignable que par Tailscale : portail de docs (Azure +
droits reports) ou administration. Le point d'accès WiFi de secours
(`RPINODE-<boîtier>`, mot de passe partagé) a été supprimé le 27 octobre
2026 : sur place sans Internet, on passe par `RPIRESCUE` (SSH) ou par le
terminal Bluetooth.

## Pourquoi une table et un service à part

`/etc/nftables.conf` commence par `flush ruleset` : relancer
`nftables.service` effacerait aussi les règles de Tailscale. On ne le
modifie donc pas ; `rpinode-firewall.service` charge cette table après lui
au démarrage. Priorité `filter - 10` : un `drop` ici est définitif, un
`accept` laisse ensuite Tailscale appliquer ses propres chaînes.

## Installation (retour arrière automatique)

```bash
./install.sh               # applique ; la table est supprimée dans 5 min
# vérifier : ssh par Tailscale, portail docs, Internet du boîtier, BACnet
./install.sh --confirmer   # annule le retour arrière, active au démarrage
```

Désactiver : `sudo systemctl disable --now rpinode-firewall`.
