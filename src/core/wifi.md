CONCERNE LA CONFIGURATION WIFI

En cas de problème de connection au réseau wwan0 (4G), le boîtier doit scruter le ssid RPIRESCUE, si il est détecté, il doit se connecter en mode client, ceci pour permettre de récupérer une connection au rpinode et débloquer l'accès au réseau wwan0.
La connection wlan0 doit pouvoir être utilisée pour se connecter à un réseau WiFi tout comme il doit pouvoir se transformer en AP. Les deux n'étant pas possibles en même temps, il faut un mécanisme de switching entre les deux modes.
Si le wlan0 est configuré pour un chantier, et que donc il doit reprendre la configuration précedente du chantier, il doit le faire sauf perte de wwan0.
Si il n'y a pas de configuration de chantier, il doit se comporter en AP.
L'AP doit permettre l'accès à eth0 et à wwan0.

ADAPTATION DU 2026-10-25 (à réévaluer)

Contexte : la SIM de rpi01 est rejetée par l'opérateur, donc wwan0 est hors ligne en permanence. Avec la règle ci-dessus, le boîtier ne reprenait jamais le WiFi du chantier : il alternait entre RPIRESCUE et son propre AP, et restait sans Internet (donc sans Tailscale) alors que le WiFi du chantier H66 était à portée.
Changement (services/wifi_mgr.py, règle A) : en cas de perte de wwan0, l'ordre est désormais
1. RPIRESCUE s'il est visible (intervention volontaire, inchangé) ;
2. le WiFi du chantier courant s'il est configuré ET visible ;
3. sinon l'AP.
Le chantier courant vient de la base (node_presence.is_current). Sans 4G il n'y a pas d'antenne pour le déterminer : il a été désigné à la main avec `python3 src/services/presence.py H66` (effacé au redémarrage du boîtier, conservé lors d'une simple relance de rpinode depuis le 2026-10-25).
À revoir quand la 4G fonctionnera : garder cet ordre, ou revenir à « sauf perte de wwan0 ».
