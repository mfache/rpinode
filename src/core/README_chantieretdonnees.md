PAR RAPPORT AUX DONNEES ASSOCIEES A UN CHANTIER ET
AUX DONNEES COMMUNES.

Lorsque le rpinode démarre OU est déplacé en étant allumé sur un autre chantier, il doit récupérer la situation précédente sur le nouveau chantier. A commencer par la configuration IP de eth0 et wlan0.
Nous allons communiquer avec des appareils modbus.
Les modules pourront être en MSTP ou Modbus TCP.
Nous devons utiliser des templates génériques.
Ces appareils pouvant être identiques à d'autres sur d'autres chantiers, les registres peuvent être identiques. Seuls les adresses mstp ou les adresses ip sont propres à un chantier donné.
Ces templates doivent être synchronisés avec le serveur docs.deltathermic.be.
L'utilisateur du module pourra choisir un template existant, en créer un nouveau sur base d'un existant ou créer un template vierge.
Nous allons aussi communiquer avec des appareils bacnet, le principe est le même.
