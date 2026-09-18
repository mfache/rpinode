#!/usr/bin/env python3
import sys
import os
import subprocess
from pathlib import Path

# Ajouter src/ au chemin Python pour importer la logique metier
sys.path.append(str(Path(__file__).parent.parent / "src"))

from core.database import get_db_connection
from services.wifi_mgr import get_ap_config

def clear_screen():
    print("\033[2J\033[H", end="")

def get_current_site_info():
    try:
        import socket
        hostname = socket.gethostname()
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT s.name 
                FROM sites s
                JOIN node_presence p ON s.id = p.site_id
                JOIN nodes n ON p.node_id = n.id
                WHERE n.hostname = ? AND p.is_current = 1
                LIMIT 1
            """, (hostname,))
            row = cur.fetchone()
            return row['name'] if row else "Aucun chantier actif"
    except Exception as e:
        return f"Erreur DB: {e}"

def show_network_status():
    print("\n--- Interfaces Reseau ---")
    subprocess.run(["ip", "-br", "a"])
    print("\n--- Route par defaut ---")
    subprocess.run("ip route | grep default", shell=True)

def main_menu():
    while True:
        clear_screen()
        site_name = get_current_site_info()
        ap_info = get_ap_config()
        
        print("=" * 60)
        print("   RPINODE - TERMINAL DE SECURITE / MAINTENANCE (ASCII)")
        print("=" * 60)
        print(f" [ Chantier actuel  ] {site_name}")
        print(f" [ WiFi Secours (AP)] SSID: {ap_info.get('ssid')} | Pass: {ap_info.get('password')}")
        print("-" * 60)
        print("  1. Afficher l'etat des interfaces reseau (IPs)")
        print("  2. Lancer un test de connexion (ping 8.8.8.8)")
        print("  3. Voir les dernieres lignes du journal rpinode")
        print("  Q. Quitter le menu et revenir au shell Linux")
        print("=" * 60)
        
        try:
            choix = input("Votre choix > ").strip().upper()
        except (KeyboardInterrupt, EOFError):
            break

        if choix == 'Q':
            clear_screen()
            print("Vous etes maintenant dans le shell systeme standard.")
            break
        elif choix == '1':
            show_network_status()
            input("\n[Appuyez sur Entree pour revenir au menu...]")
        elif choix == '2':
            print("\n--- Ping ---")
            subprocess.run(["ping", "-c", "4", "8.8.8.8"])
            input("\n[Appuyez sur Entree pour revenir au menu...]")
        elif choix == '3':
            log_file = "/tmp/rpinode/log/rpinode.log"
            print(f"\n--- Logs: {log_file} ---")
            if os.path.exists(log_file):
                subprocess.run(["tail", "-n", "20", log_file])
            else:
                print("Fichier de log introuvable.")
            input("\n[Appuyez sur Entree pour revenir au menu...]")

if __name__ == "__main__":
    main_menu()