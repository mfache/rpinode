Par rapport à la localisation du rpinode.

Nous récupérons les informations de l'antenne GSM.
Un nom de chantier doit être associé à la localisation.
Si il n'y a pas de nom de chantier associé, nous en créons un temporairement à partir de la localisation.
Plusieurs chantiers peuvent être associés à une même localisation.
Plusieurs localisations peuvent être associées à un même chantier.
Plusieurs rpinodes peuvent être associés à un même chantier.
Il faut donc songer à une clé unique en fonction de ces contraintes.
Nous devons pouvoir renommer un chantier.
Nous devons pouvoir associer un chantier à un autre chantier.
Le nom de chantier est unique.
Il faut aussi penser à la synchronisation des chantiers avec le serveur "maître" docs.deltathermic.be.
Il faut prendre en compte que différents rpinodes peuvent se synchroniser avec docs.deltathermic.be.
Les noms de chantiers sont communs entre les rpinodes.
Un nom de chantier peut être renommer sur le serveur "maître" docs.deltathermic.be ou sur un autre rpinode.
Lorsque le rpinode démarre OU est déplacé en étant allumé, il faut qu'un changement d'antenne entraîne un changement de chantier.
Lorsque un nom de chantier provisoire est créé, il faut peut-être forcer l'utilisateur à le nommer, sans quoi d'autres services doivent être désactivés.
