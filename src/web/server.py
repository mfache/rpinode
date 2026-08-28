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
        logger.debug(f"GET Request: {path}")

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
        elif path == "/scan/modbus":
            return self.serve_modbus_mgr()
        elif path == "/scan/bacnet":
            return self.serve_bacnet_mgr()
        elif path == "/monitor/suivi":
            return self.serve_trends_view()
            
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
        from services.wifi_mgr import get_ap_config
        site_name = get_current_site_name()
        ap_config = get_ap_config()

        # Poupée 1 : Rendu des widgets
        widget_cpu = render("widget.html", widget_id="cpu", title="Statut Système", data="Chargement...")
        
        wifi_info = "Chargement..."
        widget_net_data = f"""
            <div style='font-size: 1.1em; margin-bottom: 5px;' id="subt_site_name_html">
                <b>{site_name}</b>
            </div>
            <div style='font-size: 0.85em; opacity: 0.8;'>
                WiFi: <span id="subt_wifi_mode">{wifi_info}</span>
            </div>
            <div id="ap-info-zone" style="display:none; margin-top: 8px; padding-top: 8px; border-top: 1px solid rgba(0,0,0,0.05); font-size: 0.8em;">
                <div>SSID: <b id="subt_wifi_ap_ssid">{ap_config['ssid']}</b></div>
                <div class="hover-pass">Pass: <span class="masked-pass">********</span><span class="real-pass" id="subt_wifi_ap_pass">{ap_config['password']}</span></div>
            </div>
        """
        widget_net = render("widget.html", widget_id="net", title="Réseau / Chantier", data=widget_net_data)

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
        
    def serve_modbus_mgr(self):
        config = load_config()
        base_url = config.get("base_url", "")
        hostname = socket.gethostname()
        version = str(int(time.time()))

        from services.presence import get_current_site_name
        from services.modbus_mgr import get_all_templates, get_site_devices
        from core.database import get_db_connection
        
        site_name = get_current_site_name()
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id FROM sites WHERE name = ?", (site_name,))
            site_row = cursor.fetchone()
            site_id = site_row["id"] if site_row else None

        templates = get_all_templates()
        devices = get_site_devices(site_id) if site_id else []

        # Rendu des lignes de templates
        templates_html = ""
        for t in templates:
            t_json = json.dumps(dict(t)).replace("'", "\\'")
            templates_html += f"""
                <tr>
                    <td>{t['name']}</td>
                    <td>{t['manufacturer']}</td>
                    <td>
                        <button class='btn-blue' onclick='showEditTemplateModal({t_json})'>Modifier</button>
                    </td>
                </tr>
            """
        if not templates: templates_html = "<tr><td colspan='3'>Aucun template disponible</td></tr>"

        # Rendu des devices
        devices_html = ""
        for d in devices:
            devices_html += f"<div class='status-card'><strong>{d['name']}</strong><br>{d['template_name']} ({d['protocol']}://{d['address']})</div>"
        if not devices: devices_html = "<p>Aucun appareil configuré sur ce site.</p>"

        # Options pour le select
        options_html = "".join([f"<option value='{t['id']}'>{t['name']}</option>" for t in templates])

        content = render(
            "modbus.html", 
            site_name=site_name,
            templates_list_html=templates_html,
            devices_list_html=devices_html,
            templates_options_html=options_html
        )
        
        nav_html = render("nav.html", base_url=base_url)
        final_html = render(
            "layout.html",
            title="Gestion Modbus",
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

    def serve_bacnet_mgr(self):
        config = load_config()
        base_url = config.get("base_url", "")
        hostname = socket.gethostname()
        version = str(int(time.time()))

        from services.presence import get_current_site_name
        from services.bacnet_mgr import get_all_templates, get_site_devices
        from core.database import get_db_connection
        
        site_name = get_current_site_name()
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id FROM sites WHERE name = ?", (site_name,))
            site_row = cursor.fetchone()
            site_id = site_row["id"] if site_row else None

        templates = get_all_templates()
        devices = get_site_devices(site_id) if site_id else []

        # Rendu des lignes de templates
        templates_html = ""
        for t in templates:
            t_json = json.dumps(dict(t)).replace("'", "\\'")
            templates_html += f"""
                <tr>
                    <td>{t['name']}</td>
                    <td>{t['manufacturer']}</td>
                    <td>
                        <button class='btn-blue' onclick='showEditTemplateModal({t_json})'>Modifier</button>
                    </td>
                </tr>
            """
        if not templates: templates_html = "<tr><td colspan='3'>Aucun template disponible</td></tr>"

        devices_html = "".join([f"<div class='status-card'><strong>{d['name']}</strong><br>{d['template_name']} (Inst: {d['device_instance']} @ {d['network_address']})</div>" for d in devices])
        if not devices: devices_html = "<p>Aucun appareil configuré sur ce site.</p>"

        options_html = "".join([f"<option value='{t['id']}'>{t['name']}</option>" for t in templates])

        content = render(
            "bacnet.html",
            site_name=site_name,
            templates_list_html=templates_html,
            devices_list_html=devices_html,
            templates_options_html=options_html
        )
        
        nav_html = render("nav.html", base_url=base_url)
        final_html = render("layout.html", title="Gestion BACnet", hostname=escape(hostname), base_url=escape(base_url), version=version, nav=nav_html, content=content)
        
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(final_html.encode("utf-8"))

    def serve_trends_view(self):
        config = load_config()
        base_url = config.get("base_url", "")
        hostname = socket.gethostname()
        version = str(int(time.time()))

        from services.presence import get_current_site_name
        from core.database import get_db_connection
        
        site_name = get_current_site_name()
        
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT * FROM trends 
                WHERE site_id = (SELECT id FROM sites WHERE name = ?)
                ORDER BY timestamp DESC LIMIT 50
                """,
                (site_name,)
            )
            rows = cursor.fetchall()

        trends_html = ""
        for r in rows:
            time_str = time.strftime("%H:%M:%S", time.localtime(r["timestamp"]))
            sync_class = "sync-ok" if r["is_synced"] else "sync-pending"
            sync_text = "OK" if r["is_synced"] else "Attente"
            
            trends_html += f"""
                <tr>
                    <td>{time_str}</td>
                    <td>{r['protocol'].upper()}</td>
                    <td>{r['device_id']}</td>
                    <td>{r['object_id']}</td>
                    <td><strong>{r['value']}</strong></td>
                    <td><span class='sync-status {sync_class}'>{sync_text}</span></td>
                </tr>
            """
        if not rows: trends_html = "<tr><td colspan='6'>Aucune donnée enregistrée pour le moment.</td></tr>"

        content = render("trends.html", site_name=site_name, trends_rows_html=trends_html)
        nav_html = render("nav.html", base_url=base_url)
        final_html = render("layout.html", title="Suivi des Points", hostname=escape(hostname), base_url=escape(base_url), version=version, nav=nav_html, content=content)
        
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(final_html.encode("utf-8"))
        config = load_config()
        base_url = config.get("base_url", "")
        hostname = socket.gethostname()
        version = str(int(time.time()))

        from services.presence import get_current_site_name
        from services.bacnet_mgr import get_all_templates, get_site_devices
        from core.database import get_db_connection
        
        site_name = get_current_site_name()
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id FROM sites WHERE name = ?", (site_name,))
            site_row = cursor.fetchone()
            site_id = site_row["id"] if site_row else None

        templates = get_all_templates()
        devices = get_site_devices(site_id) if site_id else []

        # Rendu des lignes de templates
        templates_html = ""
        for t in templates:
            t_json = json.dumps(dict(t)).replace("'", "\\'")
            templates_html += f"""
                <tr>
                    <td>{t['name']}</td>
                    <td>{t['manufacturer']}</td>
                    <td>
                        <button class='btn-blue' onclick='showEditTemplateModal({t_json})'>Modifier</button>
                    </td>
                </tr>
            """
        if not templates: templates_html = "<tr><td colspan='3'>Aucun template disponible</td></tr>"

        devices_html = "".join([f"<div class='status-card'><strong>{d['name']}</strong><br>{d['template_name']} (Inst: {d['device_instance']} @ {d['network_address']})</div>" for d in devices])
        if not devices: devices_html = "<p>Aucun appareil configuré sur ce site.</p>"

        options_html = "".join([f"<option value='{t['id']}'>{t['name']}</option>" for t in templates])

        content = render(
            "bacnet.html",
            site_name=site_name,
            templates_list_html=templates_html,
            devices_list_html=devices_html,
            templates_options_html=options_html
        )
        
        nav_html = render("nav.html", base_url=base_url)
        final_html = render("layout.html", title="Gestion BACnet", hostname=escape(hostname), base_url=escape(base_url), version=version, nav=nav_html, content=content)
        
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
        elif path == "/api/modbus/device/add":
            self.handle_modbus_device_add()
        elif path == "/api/modbus/template/save":
            self.handle_modbus_template_save()
        elif path == "/api/bacnet/device/add":
            self.handle_bacnet_device_add()
        elif path == "/api/bacnet/template/save":
            self.handle_bacnet_template_save()
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

    def handle_bacnet_device_add(self):
        """Ajoute un appareil BACnet au chantier actuel."""
        content_length = int(self.headers['Content-Length'])
        post_data = self.rfile.read(content_length)
        try:
            data = json.loads(post_data)
            from services.presence import get_current_site_name
            from services.bacnet_mgr import add_device_to_site
            from core.database import get_db_connection
            
            site_name = get_current_site_name()
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT id FROM sites WHERE name = ?", (site_name,))
                site_id = cursor.fetchone()["id"]
            
            add_device_to_site(
                site_id=site_id,
                template_id=data.get("template_id"),
                name=data.get("name"),
                device_instance=int(data.get("device_instance")),
                network_address=data.get("network_address")
            )
            self.send_json({"status": "ok", "message": "Appareil BACnet ajouté"})
        except Exception as e:
            self.send_error(400, str(e))

    def handle_bacnet_template_save(self):
        """Crée ou met à jour un template BACnet."""
        content_length = int(self.headers['Content-Length'])
        post_data = self.rfile.read(content_length)
        try:
            data = json.loads(post_data)
            from services.bacnet_mgr import save_template
            
            objects = []
            ids = data.get("obj_id[]", [])
            names = data.get("obj_name[]", [])
            
            if isinstance(ids, str):
                ids = [ids]
                names = [names]

            for i in range(len(ids)):
                if ids[i] and names[i]:
                    objects.append({"obj": ids[i], "name": names[i]})

            template_id = data.get("template_id")
            if template_id == "": template_id = None
            
            save_template(
                name=data.get("name"),
                manufacturer=data.get("manufacturer"),
                objects=objects,
                template_id=template_id
            )
            self.send_json({"status": "ok", "message": "Template BACnet enregistré"})
        except Exception as e:
            self.send_error(400, str(e))
        """Crée ou met à jour un template Modbus."""
        content_length = int(self.headers['Content-Length'])
        post_data = self.rfile.read(content_length)
        try:
            data = json.loads(post_data)
            
            from services.modbus_mgr import save_template
            
            # Reconstruction du JSON des registres
            registers = []
            addrs = data.get("reg_addr[]", [])
            names = data.get("reg_name[]", [])
            types = data.get("reg_type[]", [])
            
            # Si un seul registre, ce ne sont pas des listes
            if isinstance(addrs, str):
                addrs = [addrs]
                names = [names]
                types = [types]

            for i in range(len(addrs)):
                if addrs[i] and names[i]:
                    registers.append({
                        "reg": int(addrs[i]),
                        "name": names[i],
                        "type": types[i]
                    })

            template_id = data.get("template_id")
            if template_id == "": template_id = None
            
            save_template(
                name=data.get("name"),
                manufacturer=data.get("manufacturer"),
                registers=registers,
                template_id=template_id
            )
            
            self.send_json({"status": "ok", "message": "Template enregistré"})
        except Exception as e:
            logger.error(f"Erreur save template: {e}")
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
