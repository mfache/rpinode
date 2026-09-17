"""
Patch ponctuel : complète le template Modbus "Groupe froid" (AERMEC, chantier
CCSE) avec les tables "Variables entières" et "Variables numériques" du
manuel AERMEC BMS pCO5 (voir modbus_mf_fr.pdf / modbus_mf_fr.txt à la racine
du dépôt), qui n'avaient pas été saisies lors de la création initiale du
template (seule la table "Variables analogiques" avait été intégrée).

Usage : python tools/patches/patch_aermec_groupe_froid_template.py
"""
import sys
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parent.parent.parent / "src"
sys.path.insert(0, str(SRC_DIR))

from services import modbus_mgr  # noqa: E402

TEMPLATE_ID = 1277
TEMPLATE_NAME = "Groupe froid"

# --- Variables entières (page 18-19 du manuel) --------------------------
# R (lecture) = code Modbus 3, R/W (lecture/ecriture) = code Modbus 6
# Adresses = colonne "Adresse Modbus (BMS1)" du manuel.
INTEGER_VARS = [
    (209, "Mode On/Off Installation (1=ON,2=Point de consigne2,3=PAR TRANCHES)", "uint16", 1.0, ""),
    (210, "Mode On/Off Récupération (1=ON,2=Point de consigne2,3=PAR TRANCHES)", "uint16", 1.0, ""),
    (211, "Sélection été hiver (0=ÉTÉ,1=HIVER,2=Par T.extérieure,3=Par DIN,4=Par BMS,5=Par Calendrier)", "uint16", 1.0, ""),
    (214, "Puissance active installation (0..100)", "uint16", 1.0, "%"),
    (215, "Puissance active récupération (0..100)", "uint16", 1.0, "%"),
    (216, "Compte-heures partie haute, pompes installation 1", "uint16", 1.0, ""),
    (217, "Compte-heures partie basse, pompes installation 1", "uint16", 1.0, "h"),
    (218, "Compte-heures partie haute, pompes installation 2", "uint16", 1.0, ""),
    (219, "Compte-heures partie basse, pompes installation 2", "uint16", 1.0, "h"),
    (220, "Compte-heures partie haute, comp.1 circ.1", "uint16", 1.0, ""),
    (221, "Compte-heures partie basse, comp.1 circ.1", "uint16", 1.0, "h"),
    (222, "Compte-heures partie haute, comp.2 circ.1", "uint16", 1.0, ""),
    (223, "Compte-heures partie basse, comp.2 circ.1", "uint16", 1.0, "h"),
    (224, "Compte-heures partie haute, comp.3 circ.1", "uint16", 1.0, ""),
    (225, "Compte-heures partie basse, comp.3 circ.1", "uint16", 1.0, "h"),
    (226, "Compte-heures partie haute, comp.1 circ.2", "uint16", 1.0, ""),
    (227, "Compte-heures partie basse, comp.1 circ.2", "uint16", 1.0, "h"),
    (228, "Compte-heures partie haute, comp.2 circ.2", "uint16", 1.0, ""),
    (229, "Compte-heures partie basse, comp.2 circ.2", "uint16", 1.0, "h"),
    (230, "Compte-heures partie haute, comp.3 circ.2", "uint16", 1.0, ""),
    (231, "Compte-heures partie basse, comp.3 circ.2", "uint16", 1.0, "h"),
    (234, "Vitesse ventilateurs 1 (0..100 %)", "uint16", 0.1, "%"),
    (235, "Vitesse ventilateurs 2 (0..100 %)", "uint16", 0.1, "%"),
    (236, "Vitesse ventilateurs 3 (0..100 %)", "uint16", 0.1, "%"),
    (237, "Demande de puissance côté installation (0..100)", "uint16", 0.1, "%"),
    (238, "Demande de puissance côté sanitaire (0..100)", "uint16", 0.1, "%"),
    (239, "Compte-heures partie haute, pompes récupération 1", "uint16", 1.0, ""),
    (240, "Compte-heures partie basse, pompes récupération 1", "uint16", 1.0, "h"),
    (241, "Compte-heures partie haute, pompes récupération 2", "uint16", 1.0, ""),
    (242, "Compte-heures partie basse, pompes récupération 2", "uint16", 1.0, "h"),
    (243, "Compteur de démarrages partie haute, pompe installation 1", "uint16", 1.0, ""),
    (244, "Compteur de démarrages partie basse, pompe installation 1", "uint16", 1.0, ""),
    (245, "Compteur de démarrages partie haute, pompe installation 2", "uint16", 1.0, ""),
    (246, "Compteur de démarrages partie basse, pompe installation 2", "uint16", 1.0, ""),
    (247, "Compteur de démarrages partie haute, pompe de récupération 1", "uint16", 1.0, ""),
    (248, "Compteur de démarrages partie basse, pompe de récupération 1", "uint16", 1.0, ""),
    (249, "Compteur de démarrages partie haute, pompe de récupération 2", "uint16", 1.0, ""),
    (250, "Compteur de démarrages partie basse, pompe de récupération 2", "uint16", 1.0, ""),
    (251, "Compteur de démarrages partie haute, CP1 circuit 1", "uint16", 1.0, ""),
    # 252 non visible sur le scan (chevauchement de page) : déduit par la
    # symétrie haute/basse systématique de cette table. À vérifier sur site.
    (252, "Compteur de démarrages partie basse, CP1 circuit 1", "uint16", 1.0, ""),
    (253, "Compteur de démarrages partie haute, CP1A circuit 1", "uint16", 1.0, ""),
    (254, "Compteur de démarrages partie basse, CP1A circuit 1", "uint16", 1.0, ""),
    (255, "Compteur de démarrages partie haute, CP1B circuit 1", "uint16", 1.0, ""),
    (256, "Compteur de démarrages partie basse, CP1B circuit 1", "uint16", 1.0, ""),
    (257, "Compteur de démarrages partie haute, CP1 circuit 2", "uint16", 1.0, ""),
    (258, "Compteur de démarrages partie basse, CP1 circuit 2", "uint16", 1.0, ""),
    (259, "Compteur de démarrages partie haute, CP1A circuit 2", "uint16", 1.0, ""),
    (260, "Compteur de démarrages partie basse, CP1A circuit 2", "uint16", 1.0, ""),
    (261, "Compteur de démarrages partie haute, CP1B circuit 2", "uint16", 1.0, ""),
    (262, "Compteur de démarrages partie basse, CP1B circuit 2", "uint16", 1.0, ""),
    (268, "NRL code - Taille", "uint16", 1.0, ""),
    (269, "NRL code - Type de Comp.", "uint16", 1.0, ""),
    (270, "NRL code - Vanne", "uint16", 1.0, ""),
    (271, "NRL code - modèle", "uint16", 1.0, ""),
    (272, "NRL code - récupération", "uint16", 1.0, ""),
    (273, "NRL code - Version", "uint16", 1.0, ""),
    (274, "NRL code - Batteries", "uint16", 1.0, ""),
    (275, "NRL code - Ventilateur", "uint16", 1.0, ""),
    (276, "NRL code - Alimentation", "uint16", 1.0, ""),
    (277, "NRL code - Réservoir d'accumulation", "uint16", 1.0, ""),
    (278, "Code d'identification de la gamme de l'unité (0=NRL)", "uint16", 1.0, ""),
    (408, "Puissance Circ 1", "uint16", 1.0, "%"),
    (409, "Puissance Circ 2", "uint16", 1.0, "%"),
    (410, "État de dégivrage circuit 1", "uint16", 1.0, ""),
    (411, "Évènements dégivrage circuit 1", "uint16", 1.0, ""),
    (412, "État de dégivrage circuit 2", "uint16", 1.0, ""),
    (413, "Évènements dégivrage circuit 2", "uint16", 1.0, ""),
]

# --- Variables numériques (page 20-22 du manuel) -------------------------
# R = code Modbus 1 (coil), R/W = code Modbus 5
DIGITAL_VARS = [
    (1, "On/Off Unité", "on/off"),
    (2, "Demande Été/Hiver par Superviseur", ""),
    (3, "Reset alarmes (1=réinitialisation)", ""),
    (4, "Demande allumage installation par entr.numérique", "on/off"),
    (5, "Demande froid/chaud installation par entr.numérique (fermé=Froid)", ""),
    (6, "Demande allumage récupération par entr.numérique", "on/off"),
    (9, "On/Off Récupération par Superv.", "on/off"),
    (10, "On/Off Installation par Superv.", "on/off"),
    (12, "Réglage froid sur point de consigne fixe (0) ou courbe climatique (1)", ""),
    (13, "Réglage hiver sur point de consigne fixe (0) ou courbe climatique (1)", ""),
    (14, "Réglage récupération sur point de consigne fixe (0) ou courbe climatique (1)", ""),
    (15, "Activer pompe installation", "on/off"),
    (16, "Activer pompe récupération", "on/off"),
    (18, "On/Off General System", "on/off"),
    (21, "Act. Calcul auto différentiel récupération", ""),
    (28, "Fonct. charge basse récupération active", "on/off"),
    (29, "Fonct. charge basse installation active", "on/off"),
    (30, "Pompe 1 évaporateur", "on/off"),
    (31, "Pompe 2 évaporateur", "on/off"),
    (34, "Pompe 1 récupération", "on/off"),
    (35, "Pompe 2 récupération", "on/off"),
    (36, "CCP1 - Compresseur 1 circ.1", "on/off"),
    (37, "CP1A - Compresseur 2 circ.1", "on/off"),
    (38, "CCP1B - Compresseur 3 circ.1", "on/off"),
    (39, "CCP2 - Compresseur 1 circ.2", "on/off"),
    # Le manuel AERMEC indique littéralement "Compresseur 1 circ.2" pour le
    # point 40 également (probable coquille du fabricant, non corrigée ici).
    (40, "CCP2A - Compresseur 1 circ.2", "on/off"),
    (41, "CCP2B - Compresseur 3 circ.2", "on/off"),
    (42, "CV - Ventilateur 1", "on/off"),
    (43, "CV1 - Ventilateur 2", "on/off"),
    (44, "VIC - vanne inversion cycle, Circ 1", "on/off"),
    (45, "VIC - vanne inversion cycle, Circ 2", "on/off"),
    (46, "VSL - soupape solénoïde liquide 1 circ.1", "on/off"),
    (47, "VSL - soupape solénoïde liquide 1 circ.2", "on/off"),
    (56, "VSBP - Vanne Bypass dégivrage circ.1", "on/off"),
    (57, "VSBP - Vanne Bypass dégivrage circ.2", "on/off"),
    (100, "Somme de toutes les alarmes", "Alm/Ok"),
    (101, "AL38 - alarme fluxostat évaporateur", "Alm/Ok"),
    (102, "AL39 - alarme fluxostat récupération", "Alm/Ok"),
    (103, "AL24 - Alarme thermique pompe évaporateur 1", "Alm/Ok"),
    (104, "AL25 - Alarme thermique pompe évaporateur 2", "Alm/Ok"),
    (107, "AL26 - Alarme thermique pompe récupération 1", "Alm/Ok"),
    (108, "AL27 - Alarme thermique pompe récupération 2", "Alm/Ok"),
    (115, "AL28 - Alarme thermique ventilateur 1", "Alm/Ok"),
    (116, "AL29 - Alarme thermique ventilateur 2", "Alm/Ok"),
    (117, "AL40 - Alarme antigel évap.", "Alm/Ok"),
    (121, "AL31 - Alarme basse pression circ.1", "Alm/Ok"),
    (122, "AL65 - Alarme basse pression circ.2", "Alm/Ok"),
    (123, "AL34 - Alarme basse pression grave circ.1", "Alm/Ok"),
    (124, "AL35 - Alarme basse pression grave circ.2", "Alm/Ok"),
    (125, "AL32 - Alarme pressostat haute circ.1", "Alm/Ok"),
    (126, "AL66 - Alarme pressostat haute circ.2", "Alm/Ok"),
    (127, "AL33 - Alarme haute pression circ.1", "Alm/Ok"),
    (128, "AL67 - Alarme haute pression circ.2", "Alm/Ok"),
    (129, "AL03 - Alarme moniteur de phase", "Alm/Ok"),
    (130, "AL10 - Alarme sonde en panne sortie évap.1", "Alm/Ok"),
    (131, "AL09 - Alarme sonde en panne entrée évap.1", "Alm/Ok"),
    (134, "AL13 - Alarme sonde en panne sortie récup.1", "Alm/Ok"),
    (135, "AL12 - Alarme sonde en panne entrée récup.1", "Alm/Ok"),
    (136, "AL05 - Alarme sonde en panne haute press.circ.1", "Alm/Ok"),
    (137, "AL07 - Alarme sonde en panne basse press.circ.1", "Alm/Ok"),
    (138, "AL06 - Alarme sonde en panne haute press.circ.2", "Alm/Ok"),
    (139, "AL08 - Alarme sonde en panne basse press.circ.2", "Alm/Ok"),
    (140, "AL16 - Alarme sonde en panne température extérieure", "Alm/Ok"),
    (141, "AL48 - Alarme sonde en panne temp.gaz de refoulement 1", "Alm/Ok"),
    (142, "AL49 - Alarme sonde en panne temp.gaz de refoulement 2", "Alm/Ok"),
    (143, "AL17 - Alarme sonde en panne temp.liquide circ.1", "Alm/Ok"),
    (144, "AL18 - Alarme sonde en panne temp.liquide circ.2", "Alm/Ok"),
    (147, "AL01 - Alarme batterie horloge déchargée", "Alm/Ok"),
    (148, "AL02 - Alarme erreur mémoire pCO", "Alm/Ok"),
    (149, "AL14 - Alarme sonde en panne sortie récup.2", "Alm/Ok"),
    (150, "AL15 - Alarme sonde en panne sortie récup.com.", "Alm/Ok"),
    (160, "AL41 - Alarme antigel évap.com.", "Alm/Ok"),
    (161, "AL42 - Alarme antigel récup.1", "Alm/Ok"),
    (162, "AL43 - Alarme antigel récup.2", "Alm/Ok"),
    (163, "AL44 - Alarme antigel récup.com.", "Alm/Ok"),
    (164, "AL45 - Alarme offline détente uPC", "Alm/Ok"),
    (169, "AL23 - Alarme thermique compresseur 1 circ.1", "Alm/Ok"),
    (170, "AL59 - Alarme thermique compresseur 2 circ.1", "Alm/Ok"),
    (171, "AL60 - Alarme thermique compresseur 3 circ.1", "Alm/Ok"),
    (172, "AL61 - Alarme thermique compresseur 1 circ.2", "Alm/Ok"),
    (173, "AL62 - Alarme thermique compresseur 2 circ.2", "Alm/Ok"),
    (174, "AL63 - Alarme thermique compresseur 3 circ.2", "Alm/Ok"),
    (176, "AL11 - Alarme sonde en panne sortie évap.com.", "Alm/Ok"),
    (184, "AL75 - Alarme haute temp.gaz de refoulement circ.1", "Alm/Ok"),
    (185, "AL76 - Alarme haute temp.gaz de refoulement circ.2", "Alm/Ok"),
    (190, "AL85 - Haute température installation", "Alm/Ok"),
    (191, "AL84 - Haute température récupération", "Alm/Ok"),
]


def build_new_registers():
    regs = []
    for reg, name, rtype, scale, unit in INTEGER_VARS:
        regs.append({
            "reg": reg, "function": 3, "base": 1,
            "name": name, "type": rtype, "scale": scale, "unit": unit,
        })
    for reg, name, unit in DIGITAL_VARS:
        regs.append({
            "reg": reg, "function": 1, "base": 1,
            "name": name, "type": "int16", "scale": 1.0, "unit": unit,
        })
    return regs


def main():
    tpl = modbus_mgr.get_template(TEMPLATE_ID)
    if not tpl or tpl.get("name") != TEMPLATE_NAME:
        print(f"ERREUR : template {TEMPLATE_ID} introuvable ou nom inattendu ({tpl and tpl.get('name')!r}).")
        sys.exit(1)

    import json
    existing_regs = json.loads(tpl["registers_json"] or "[]")
    existing_keys = {(r.get("function", 3), int(r["reg"])) for r in existing_regs}

    new_regs = build_new_registers()
    added = [r for r in new_regs if (r["function"], r["reg"]) not in existing_keys]

    all_regs = existing_regs + added
    modbus_mgr.save_template(
        name=tpl["name"],
        manufacturer=tpl["manufacturer"],
        registers=all_regs,
        template_id=TEMPLATE_ID,
    )
    print(f"Template '{tpl['name']}' (id={TEMPLATE_ID}) mis à jour : "
          f"{len(existing_regs)} points existants + {len(added)} points ajoutés "
          f"= {len(all_regs)} points au total.")


if __name__ == "__main__":
    main()
