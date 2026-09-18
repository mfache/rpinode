#!/usr/bin/env python3
import sys
import os
import subprocess
from pathlib import Path
import urwid

# Ajouter src/ au chemin Python pour importer la logique metier de rpinode
sys.path.append(str(Path(__file__).parent.parent / "src"))

from core.database import get_db_connection
from services.wifi_mgr import get_ap_config

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

palette = [
    ('body', 'black', 'light gray'),
    ('header', 'white', 'dark blue', 'bold'),
    ('button normal', 'light gray', 'dark blue'),
    ('button select', 'white', 'dark green', 'bold'),
    ('quit button normal', 'light red', 'dark blue'),
    ('quit button select', 'white', 'light red', 'bold'),
    ('dialog bg', 'black', 'light gray'),
    ('shortcut', 'light cyan', 'dark blue', 'bold'),
]

class TuiApp:
    def __init__(self):
        self.header_text = urwid.Text([u" RPINODE - Terminal de Maintenance\n"], align='center')
        self.header = urwid.AttrMap(self.header_text, 'header')

        self.footer_text = urwid.Text(u" [R]éseau  [P]ing  [L]ogs  [Q]uitter", align='center')
        self.footer = urwid.AttrMap(self.footer_text, 'header')

        self.menu_list = urwid.SimpleFocusListWalker([])
        self.listbox = urwid.ListBox(self.menu_list)

        self.refresh_infos()
        self.build_menu()

        self.main_view = urwid.Frame(urwid.AttrMap(self.listbox, 'body'), header=self.header, footer=self.footer)

        self.in_dialog = False
        self.dialog_command = None

    def refresh_infos(self):
        site_name = get_current_site_info()
        ap_info = get_ap_config()
        self.info_text = (
            f"Chantier : {site_name}\n"
            f"WiFi (AP): {ap_info.get('ssid')} / {ap_info.get('password')}\n"
        )

    def menu_button(self, caption, shortcut, callback, is_quit=False):
        parts = caption.split(shortcut, 1)
        if len(parts) == 2:
            formatted_caption = [parts[0], ('shortcut', shortcut), parts[1]]
        else:
            formatted_caption = caption

        button = urwid.Button("")
        urwid.connect_signal(button, 'click', callback)
        button.set_label(formatted_caption)
        
        attr_normal = 'quit button normal' if is_quit else 'button normal'
        attr_select = 'quit button select' if is_quit else 'button select'
        return urwid.AttrMap(button, attr_normal, focus_map=attr_select)

    def build_menu(self):
        self.menu_list.clear()
        
        info_widget = urwid.Padding(urwid.Text(self.info_text), left=2, right=2)
        self.menu_list.append(info_widget)
        self.menu_list.append(urwid.Divider("-"))
        
        self.menu_list.append(self.menu_button("Afficher les interfaces [r]éseau", "r", self.show_network))
        self.menu_list.append(self.menu_button("Lancer un test de [p]ing (8.8.8.8)", "p", self.show_ping))
        self.menu_list.append(self.menu_button("Voir le journal système ([l]ogs)", "l", self.show_logs))
        self.menu_list.append(urwid.Divider())
        self.menu_list.append(self.menu_button("[q]uitter", "q", self.exit_program, is_quit=True))

    def _run_dialog_command(self):
        if not self.dialog_command:
            return "(Aucune commande)"
        try:
            result = subprocess.run(self.dialog_command, shell=True, capture_output=True, text=True, timeout=5)
            output = result.stdout if result.stdout else result.stderr
        except Exception as e:
            output = str(e)
            
        if not output.strip():
            output = "(Aucun résultat ou sortie vide)"
        return output

    def show_dialog(self, title, command, allow_refresh=False):
        self.in_dialog = True
        self.dialog_command = command
        
        self.dialog_text_widget = urwid.Text(self._run_dialog_command())
        self.dialog_scroll = urwid.ListBox(urwid.SimpleFocusListWalker([self.dialog_text_widget]))
        
        buttons = []
        
        if allow_refresh:
            refresh_btn = urwid.Button("Rafraîchir [F5]")
            urwid.connect_signal(refresh_btn, 'click', self.refresh_dialog)
            buttons.append(('pack', urwid.AttrMap(refresh_btn, 'button normal', 'button select')))
            # Espace entre les boutons
            buttons.append(('weight', 1, urwid.Text("")))

        close_btn = urwid.Button("Fermer [Echap]")
        urwid.connect_signal(close_btn, 'click', self.close_dialog)
        buttons.append(('pack', urwid.AttrMap(close_btn, 'button normal', 'button select')))
        
        btn_columns = urwid.Columns(buttons)

        pile = urwid.Pile([
            ('pack', urwid.Text(title, align="center")),
            ('pack', urwid.Divider("-")),
            ('weight', 1, self.dialog_scroll),
            ('pack', urwid.Divider("-")),
            ('pack', btn_columns)
        ])
        
        box = urwid.LineBox(pile)
        overlay = urwid.Overlay(urwid.AttrMap(box, 'dialog bg'), self.main_view,
                                align='center', width=('relative', 80),
                                valign='middle', height=('relative', 80),
                                min_width=40, min_height=10)
        self.loop.widget = overlay

    def refresh_dialog(self, button=None):
        if self.in_dialog and self.dialog_command:
            self.dialog_text_widget.set_text(self._run_dialog_command())

    def close_dialog(self, button=None):
        self.in_dialog = False
        self.dialog_command = None
        self.loop.widget = self.main_view

    # --- Callbacks des boutons ---
    def show_network(self, button=None):
        self.show_dialog("Interfaces Réseau", "ip -br a && echo '\n--- Routes ---\n' && ip route", allow_refresh=True)

    def show_ping(self, button=None):
        self.show_dialog("Test Ping 8.8.8.8", "ping -c 4 8.8.8.8")

    def show_logs(self, button=None):
        log_file = "/tmp/rpinode/log/rpinode.log"
        if not os.path.exists(log_file):
            self.show_dialog("Erreur", "Le fichier de log n'existe pas.")
            return
        # Logs de base, avec possibilite de rafraichir manuellement
        self.show_dialog(f"Logs: {log_file}", f"tail -n 30 {log_file}", allow_refresh=True)

    def exit_program(self, button=None):
        raise urwid.ExitMainLoop()

    # --- Gestion globale des raccourcis clavier ---
    def unhandled_input(self, key):
        if key in ('q', 'Q'):
            self.exit_program()
        elif key == 'esc' and self.in_dialog:
            self.close_dialog()
        elif key == 'f5' and self.in_dialog:
            self.refresh_dialog()
            
        if not self.in_dialog:
            if key in ('r', 'R'):
                self.show_network()
            elif key in ('p', 'P'):
                self.show_ping()
            elif key in ('l', 'L'):
                self.show_logs()

    def run(self):
        self.loop = urwid.MainLoop(self.main_view, palette, unhandled_input=self.unhandled_input)
        self.loop.run()
        print("\033[2J\033[H", end="")
        print("Menu interactif fermé. Retour au shell système.")

if __name__ == '__main__':
    app = TuiApp()
    app.run()
