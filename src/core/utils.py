"""
Fonctions utilitaires génériques pour le projet rpinode.
"""

def get_changed_items(old_data: dict, new_data: dict) -> dict:
    """
    Compare deux dictionnaires et retourne uniquement les couples clé/valeur
    de new_data qui sont différents de old_data.
    """
    diff = {}
    for key, value in new_data.items():
        if key not in old_data or old_data[key] != value:
            diff[key] = value
    return diff
