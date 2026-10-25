# SSH par clé uniquement

Ajouté le 25 octobre 2026, avec le pare-feu (`drivers/firewall`). SSH reste
ouvert sur le LAN du chantier et le point d'accès (secours `RPIRESCUE`) :
l'authentification par mot de passe y était acceptée, alors que le mot de
passe du compte `marc` a circulé.

`01-rpinode-cle-uniquement.conf` → `/etc/ssh/sshd_config.d/` :
`PasswordAuthentication no`, `KbdInteractiveAuthentication no`,
`PermitRootLogin prohibit-password`. Nom en `01-` pour passer avant
`50-cloud-init.conf`, qui force `PasswordAuthentication yes` (sshd retient
la première valeur lue).

## Accès qui restent possibles

- clé SSH déclarée dans `~/.ssh/authorized_keys` ;
- **Tailscale SSH** (`tailscale ssh marc@rpiXX`), géré par tailscaled selon
  l'ACL headscale (groupe fleet-admins → compte `marc`), sans clé ni mot de passe ;
- **terminal Bluetooth** (`drivers/bluetooth_terminal`) : passe par `login`,
  donc le mot de passe du compte reste valable là.

## Installation (retour arrière automatique)

```bash
./install.sh               # applique ; annulé automatiquement dans 5 min
# vérifier : connexion par clé OK, mot de passe refusé
./install.sh --confirmer
```
