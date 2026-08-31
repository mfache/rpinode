CONCERNE LES RESEAUX

Le rpinode est un boitier qui doit rester accessible à distance, le wwan0 doit donc être configuré pour se connecter à un réseau 4G.

Il peut également agir comme :
- **Client Ethernet (eth0)** ou **Client WiFi (wlan0)** avec adressage Statique ou DHCP.
- **Serveur DHCP local (Shared)** sur l'interface filaire (eth0) pour piloter des automates en direct.
- **Point d'Accès WiFi (AP)** de secours ou de configuration locale, avec distribution d'adresses IP (DHCP) personnalisable.

Toutes ces configurations sont persistées par chantier (site) et synchronisées avec le serveur de flotte.
