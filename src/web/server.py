import json
import logging
import os
import sys
import subprocess
import socket
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from socketserver import ThreadingMixIn

from core.paths import STATIC_DIR
from core.config import load_config
from web.templating import render, escape
from web.stream import handle_sse_stream

logger = logging.getLogger(__name__)

class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True

class WebAdminHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        logger.info(f"{self.address_string()} - - [{self.log_date_time_string()}] {format%args}")

    def log_error(self, format, *args):
        logger.error(f"{self.address_string()} - - [{self.log_date_time_string()}] {format%args}")

    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()

    def do_GET(self):
        parsed_url = urlparse(self.path)
        path = parsed_url.path

        # Tolérer un proxy qui n'enlève pas le préfixe
        if path.startswith("/rpinode/"):
            path = path[len("/rpinode"):]
        elif path == "/rpinode":
            path = "/"

        # Routage spécial PWA (servir sw.js et manifest depuis la racine)
        if path == "/sw.js":
            return self.serve_static("/static/sw.js")
        if path == "/manifest.json":
            return self.serve_static("/static/manifest.json")

        # Routage statique
        if path.startswith("/static/"):
            return self.serve_static(path)

        # Routage SSE (Server-Sent Events) pour les mises à jour dynamiques
        if path == "/api/stream":
            return handle_sse_stream(self)

        # Routage API (JSON)
        if path == "/api/status":
            return self.send_json({"status": "ok", "message": "Le serveur rpinode tourne."})
        if path == "/api/gsm":
            from services.gsm import get_gsm_info
            return self.send_json(get_gsm_info())
        if path == "/api/site/search":
            return self.handle_site_search_external()
        if path == "/api/fleet/status":
            return self.handle_fleet_status()

        # Routage Pages HTML
        if path == "/":
            return self.serve_home()
        elif path == "/network/overview":
            return self.serve_network_overview()
            
        self.send_error(404, "Page non trouvée")

    def serve_static(self, path):
        # path ressembles to "/static/app.js"
        # Security: prevent directory traversal
        filename = path.replace("/static/", "").replace("/", "")
        file_path = STATIC_DIR / filename

        if not file_path.exists():
            self.send_error(404, "Fichier statique non trouvé")
            return

        content_type = "text/plain"
        if filename.endswith(".css"): content_type = "text/css"
        elif filename.endswith(".js"): content_type = "application/javascript"
        elif filename.endswith(".json"): content_type = "application/json"
        elif filename.endswith(".png"): content_type = "image/png"
        elif filename.endswith(".ico"): content_type = "image/x-icon"

        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.end_headers()

        with open(file_path, "rb") as f:
            self.wfile.write(f.read())

    def serve_home(self):
        config = load_config()
        base_url = config.get("base_url", "")
        
        # Version pour forcer le refresh du cache (timestamp)
        import time
        version = str(int(time.time()))

        # Récupération dynamique du hostname
        hostname = socket.gethostname()
        
        from services.presence import get_current_site_name
        site_name = get_current_site_name()

        # Poupée 1 : Rendu des widgets
        widget_cpu = render("widget.html", widget_id="cpu", title="Statut Système", data="Chargement...")
        widget_net = render("widget.html", widget_id="net", title="Réseau / Chantier", data=site_name)

        # Concaténation des widgets
        all_widgets = widget_cpu + "\n" + widget_net

        # Poupée 2 : Rendu de la vue (home)
        home_content = render("home.html", user="Admin", widgets=all_widgets)

        # Poupée 3 : Rendu de la Navigation
        nav_html = render("nav.html", base_url=base_url)

        # Poupée 4 : Injection dans le Layout
        final_html = render(
            "layout.html",
            title="Accueil rpinode",
            hostname=escape(hostname),
            base_url=escape(base_url),
            version=version,
            nav=nav_html,
            content=home_content
        )
        
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(final_html.encode("utf-8"))
        
    def serve_network_overview(self):
        config = load_config()
        base_url = config.get("base_url", "")
        hostname = socket.gethostname()
        version = str(int(time.time()))

        # Contenu spécifique : vue d'ensemble
        content = render("network_overview.html", base_url=base_url)
        nav_html = render("nav.html", base_url=base_url)

        final_html = render(
            "layout.html",
            title="Vue d'ensemble Réseau",
            hostname=escape(hostname),
            base_url=escape(base_url),
            version=version,
            nav=nav_html,
            content=content
        )
        
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(final_html.encode("utf-8"))

    def send_json(self, data):
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode("utf-8"))

    def do_POST(self):
        parsed_url = urlparse(self.path)
        path = parsed_url.path

        # Tolérer un proxy qui n'enlève pas le préfixe
        if path.startswith("/rpinode/"):
            path = path[len("/rpinode"):]

        if path == "/api/restart":
            self.handle_restart()
        elif path == "/api/reboot":
            self.handle_system_action("reboot")
        elif path == "/api/shutdown":
            self.handle_system_action("shutdown")
        elif path == "/api/site/rename":
            self.handle_site_rename()
        elif path == "/api/fleet/register":
            self.handle_fleet_register()
        else:
            self.send_error(404, "Action non trouvée")

    def handle_fleet_status(self):
        """Retourne l'état de connexion à la flotte."""
        from services.fleet import fleet
        self.send_json({
            "registered": fleet.is_registered(),
            "hostname": fleet.hostname,
            "url": fleet.base_url
        })

    def handle_fleet_register(self):
        """Tente d'enregistrer le boîtier (nécessite fleet_secret en config)."""
        from services.fleet import fleet
        if fleet.register():
            self.send_json({"status": "ok", "message": "Enregistrement réussi"})
        else:
            self.send_error(403, "Échec de l'enregistrement (vérifiez le secret)")

    def handle_site_search_external(self):
        """Recherche un chantier sur le serveur maître."""
        parsed_url = urlparse(self.path)
        query = parse_qs(parsed_url.query).get('q', [''])[0]
        
        if not query:
            return self.send_json([])

        from services.fleet import fleet
        try:
            # Récupération réelle depuis docs.deltathermic.be
            chantiers = fleet.get_chantiers(query=query)
            
            # Formatage pour l'UI (on mappe 'ref' sur 'name' et 'id' sur 'external_id')
            results = [
                {"external_id": str(c["id"]), "name": c["ref"]}
                for c in chantiers
            ]
            
            self.send_json(results)
        except Exception as e:
            logger.error(f"Erreur recherche site fleet: {e}")
            self.send_error(500, str(e))

    def handle_site_rename(self):
        """Renomme le chantier actuel (enlève le flag provisoire)."""
        content_length = int(self.headers['Content-Length'])
        post_data = self.rfile.read(content_length)
        try:
            data = json.loads(post_data)
            new_name = data.get("name")
            external_id = data.get("external_id") # Optionnel, fourni si sélectionné
            
            if not new_name:
                raise ValueError("Nom manquant")
            
            from services.presence import label_current_location
            from services.fleet import fleet
            
            # 1. Mise à jour locale
            if label_current_location(new_name, is_provisional=False, external_id=external_id):
                # 2. Notification au serveur maître
                if fleet.is_registered():
                    if external_id:
                        # Cas d'un renommage d'un site déjà connu du serveur
                        fleet.rename_chantier(external_id, new_name)
                    else:
                        # Cas d'une création de nouveau site : on utilise /sync avec le hint
                        from services.gsm import get_gsm_info
                        gsm = get_gsm_info()
                        res = fleet.sync_location(gsm, site_hint_name=new_name)
                        if res and res.get("id"):
                            # On récupère l'external_id généré par le serveur
                            label_current_location(new_name, is_provisional=False, external_id=str(res["id"]))

                self.send_json({"status": "ok", "message": f"Chantier renommé en {new_name}"})
            else:
                self.send_error(500, "Erreur lors du renommage")
        except Exception as e:
            self.send_error(400, str(e))

    def handle_restart(self):
        logger.info("Requête de redémarrage reçue. Redémarrage du processus...")

        # Réponse rapide avant de couper
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"Redemarrage en cours... La page va se recharger.")

        # Petit délai pour laisser la réponse partir
        import time
        time.sleep(0.5)

        # Redémarrage propre du processus Python
        # On s'assure de garder l'environnement (PYTHONPATH)
        python = sys.executable
        env = os.environ.copy()
        if 'PYTHONPATH' not in env:
            env['PYTHONPATH'] = os.getcwd()
            
        logger.info(f"Exécution de : {python} {sys.argv}")
        os.execve(python, [python] + sys.argv, env)

    def handle_system_action(self, action):
        """Gère le reboot ou le shutdown du système (OS)."""
        logger.info(f"Action système demandée : {action}")

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"status": "ok", "message": f"{action} initié"}).encode())

        try:
            if action == "reboot":
                subprocess.run(["sudo", "systemctl", "reboot", "--no-block"], check=True)
            elif action == "shutdown":
                subprocess.run(["sudo", "systemctl", "poweroff", "--no-block"], check=True)
        except Exception as e:
            logger.error(f"Erreur lors de l'action {action} : {e}")

def start_server():
    config = load_config()
    port = config.get("port", 8080)
    server_address = ('', port)

    httpd = ThreadingHTTPServer(server_address, WebAdminHandler)
    logger.info(f"Serveur HTTP (rpinode) démarré sur le port {port}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        logger.info("Arrêt du serveur...")
        httpd.server_close()
