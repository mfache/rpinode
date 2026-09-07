CONCERNE LA CONFIGURATION WIFI

En cas de problème de connection au réseau wwan0 (4G), le boîtier doit scruter le ssid RPIRESCUE, si il est détecté, il doit se connecter en mode client, ceci pour permettre de récupérer une connection au rpinode et débloquer l'accès au réseau wwan0.
La connection wlan0 doit pouvoir être utilisée pour se connecter à un réseau WiFi tout comme il doit pouvoir se transformer en AP. Les deux n'étant pas possibles en même temps, il faut un mécanisme de switching entre les deux modes.
Si le wlan0 est configuré pour un chantier, et que donc il doit reprendre la configuration précedente du chantier, il doit le faire sauf perte de wwan0.
Si il n'y a pas de configuration de chantier, il doit se comporter en AP.
L'AP doit permettre l'accès à eth0 et à wwan0.
