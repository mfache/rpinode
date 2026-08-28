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
from core.database import get_db_connection
from services.ipscan import load_ipscan_results, is_ipscan_running, start_ip_scan_in_background
from services.fleet import fleet
from services.gsm import get_gsm_info
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
            
        # Normalisation : on enlève le slash final (sauf pour la racine)
        if len(path) > 1 and path.endswith("/"):
            path = path[:-1]

        logger.info(f"GET Request: {path} (original: {self.path})")
        
        # Routage spécial PWA (servir sw.js et manifest depuis la racine)
        if path == "/sw.js":
            return self.serve_static("/static/sw.js")
        if path == "/manifest.json":
            return self.serve_static("/static/manifest.json")

        # Routage statique
        if path.startswith("/static/"):
            return self.serve_static(path)

        # Routage SSE (Server-Sent Events)
        if path == "/api/stream":
            return handle_sse_stream(self)

        # Routage API (JSON)
        if path == "/api/status":
            return self.send_json({"status": "ok", "message": "Le serveur rpinode tourne."})
        elif path == "/api/gsm":
            from services.gsm import get_gsm_info
            return self.send_json(get_gsm_info())
        elif path == "/api/site/search":
            return self.handle_site_search_external()
        elif path == "/api/fleet/status":
            return self.handle_fleet_status()
        
        # Routage Pages HTML
        if path == "/":
            return self.serve_home()
        elif path == "/network/overview":
            return self.serve_network_overview()
        elif path == "/network/interfaces":
            return self.serve_network_interfaces()
        elif path == "/scan/modbus":
            return self.serve_modbus_mgr()
        elif path == "/scan/bacnet":
            return self.serve_bacnet_mgr()
        elif path == "/monitor/suivi":
            return self.serve_trends_view()
        elif path == "/scan/ip":
            return self.serve_ip_scan()
            
        self.send_error(404, "Page non trouvée")

    def do_POST(self):
        parsed_url = urlparse(self.path)
        path = parsed_url.path

        if path.startswith("/rpinode/"):
            path = path[len("/rpinode"):]
        elif path == "/rpinode":
            path = "/"
            
        if len(path) > 1 and path.endswith("/"):
            path = path[:-1]

        logger.info(f"POST Request: {path} (original: {self.path})")

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
        elif path == "/api/network/profile/save":
            self.handle_network_profile_save()
        elif path == "/api/fleet/register":
            self.handle_fleet_register()
        elif path == "/api/scan/ip/start":
            self.handle_ip_scan_start()
        elif path == "/api/scan/ip/annotate":
            self.handle_ip_annotate()
        else:
            self.send_error(404, "Action non trouvée")

    def serve_static(self, path):
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
        hostname = socket.gethostname()
        version = str(int(time.time()))
        
        from services.presence import get_current_site_name
        from services.wifi_mgr import get_ap_config
        site_name = get_current_site_name()
        ap_config = get_ap_config()

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

        all_widgets = widget_cpu + "\n" + widget_net
        home_content = render("home.html", user="Admin", widgets=all_widgets)
        nav_html = render("nav.html", base_url=base_url)
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

    def serve_network_interfaces(self):
        config = load_config()
        base_url = config.get("base_url", "")
        hostname = socket.gethostname()
        version = str(int(time.time()))

        from services.presence import get_current_site_name
        from services.network import get_interface_status
        from services.network_config import get_site_network_profile
        from core.database import get_db_connection
        
        site_name = get_current_site_name()
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id FROM sites WHERE name = ?", (site_name,))
            site_row = cursor.fetchone()
            site_id = site_row["id"] if site_row else None

        profile = get_site_network_profile(site_id, "eth0") if site_id else None
        status = get_interface_status("eth0")
        method = profile["method"] if profile else "auto"
        
        addresses = profile["addresses"] if profile and profile["addresses"] else ""
        addresses_list = [a.strip() for a in addresses.split(",") if a.strip()]
        if not addresses_list:
            addresses_list = [""]
            
        addresses_rows = ""
        for addr in addresses_list:
            addresses_rows += f"""
            <div class="address-row">
                <input type="text" name="addresses" value="{escape(addr)}" placeholder="192.168.1.10/24">
                <button type="button" class="btn-remove" onclick="removeAddressRow(this)">×</button>
            </div>
            """
        
        content = render(
            "network_interfaces.html",
            site_name=site_name,
            method_auto_selected='selected' if method == "auto" else '',
            method_manual_selected='selected' if method == "manual" else '',
            manual_fields_display='block' if method == "manual" else 'none',
            addresses_rows=addresses_rows,
            gateway=profile["gateway"] if profile and profile["gateway"] else "",
            current_ip=status["ip"],
            mac=status["mac"],
            active_class="active" if status["active"] else "inactive",
            active_text="Connecté" if status["active"] else "Coupé/Absent"
        )
        
        nav_html = render("nav.html", base_url=base_url)
        final_html = render(
            "layout.html",
            title="Configuration Interfaces",
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

    def handle_network_profile_save(self):
        content_length = int(self.headers['Content-Length'])
        post_data = self.rfile.read(content_length)
        try:
            data = json.loads(post_data)
            from services.presence import get_current_site_name
            from services.network_config import save_site_network_profiles, apply_site_network_profiles
            from core.database import get_db_connection
            
            site_name = get_current_site_name()
            if site_name == "Inconnu":
                 return self.send_json({"status": "error", "message": "Chantier non identifié. Labellisez-le d'abord."})

            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT id FROM sites WHERE name = ?", (site_name,))
                row = cursor.fetchone()
                if not row:
                    return self.send_json({"status": "error", "message": f"Le chantier '{site_name}' n'est pas en base."})
                site_id = row["id"]
            
            save_site_network_profiles(site_id, [data])
            apply_site_network_profiles(site_id)
            
            self.send_json({"status": "ok", "message": "Profil enregistré et appliqué"})
        except Exception as e:
            logger.error(f"Erreur save network profile: {e}")
            self.send_json({"status": "error", "message": str(e)})

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

        devices_html = ""
        for d in devices:
            devices_html += f"<div class='status-card'><strong>{d['name']}</strong><br>{d['template_name']} ({d['protocol']}://{d['address']})</div>"
        if not devices: devices_html = "<p>Aucun appareil configuré sur ce site.</p>"

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

    def serve_ip_scan(self):
        config = load_config()
        base_url = config.get("base_url", "")
        
        results = load_ipscan_results()
        running = is_ipscan_running()
        
        devices_html = ""
        if results and results.get("devices"):
            for d in results["devices"]:
                mac = d.get('mac', '').lower()
                vendor = d.get("vendor") or "Inconnu"
                annots = json.loads(d.get("annotations_json")) if d.get("annotations_json") else {}
                is_dirty = d.get("is_dirty", 0)
                
                # Formatage des ports
                ports = d.get("ports", [])
                formatted_ports = []
                for p in ports:
                    if p == 502:
                        modbus_info = ""
                        if d.get("modbus_info"):
                            modbus_info = f" <small style='color:#2ecc71'>({d['modbus_info']})</small>"
                        formatted_ports.append(f"<a href='{base_url}/scan/modbus?ip={d['ip']}' title='Gérer en Modbus' style='color: #2ecc71; font-weight: bold;'>502 (Modbus)</a>{modbus_info}")
                    elif p == 47808:
                        bacnet_info = ""
                        if d.get("bacnet_instance"):
                            bacnet_info = f" <small style='color:#e67e22'>(Inst: {d['bacnet_instance']})</small>"
                        formatted_ports.append(f"<a href='{base_url}/scan/bacnet?ip={d['ip']}' title='Gérer en BACnet' style='color: #e67e22; font-weight: bold;'>47808 (BACnet)</a>{bacnet_info}")
                    elif p == 80:
                        formatted_ports.append(f"<a href='http://{d['ip']}' target='_blank' style='color: #3498db;'>80 (HTTP)</a>")
                    elif p == 443:
                        formatted_ports.append(f"<a href='https://{d['ip']}' target='_blank' style='color: #3498db;'>443 (HTTPS)</a>")
                    else:
                        formatted_ports.append(str(p))
                
                ports_str = ", ".join(formatted_ports) if formatted_ports else "<span style='opacity:0.4;'>Aucun</span>"
                
                # Formatage des annotations pour l'affichage
                annots_str = ""
                if annots:
                    annots_str = " ".join([f"<span class='annot-tag'>{escape(k)}: {escape(v)}</span>" for k, v in annots.items()])
                
                sync_indicator = ""
                if is_dirty:
                    sync_indicator = "<span class='sync-pending' title='En attente de synchronisation'>☁️</span>"
                
                devices_html += f"""
                <tr>
                    <td style='font-weight:bold;'>{escape(d.get('ip'))}</td>
                    <td><span style='font-family:monospace; font-size:0.9em; opacity:0.8;'>{escape(d.get('mac'))}</span></td>
                    <td>
                        <div style='display: flex; align-items: center; gap: 5px;'>
                            <span class='vendor-tag' id='vendor-{mac}' onclick="editDevice('{mac}', '{escape(d.get('ip'))}')" style='cursor:pointer;'>{escape(vendor)}</span>
                            {sync_indicator}
                        </div>
                    </td>
                    <td>{ports_str}</td>
                    <td><div id='annots-{mac}'>{annots_str}</div></td>
                    <td><small style='color:#999;'>{escape(d.get('iface'))}</small></td>
                </tr>
                """
        else:
            devices_html = "<tr><td colspan='6' style='text-align:center; padding: 40px; color: #999;'>Aucun résultat. Lancez un scan pour découvrir les équipements.</td></tr>"

        scan_content = render(
            "ip_scan.html",
            running="true" if running else "false",
            devices_rows=devices_html,
            last_scan=results.get("scanned_at", "Jamais") if results else "Jamais"
        )
        
        nav_html = render("nav.html", base_url=base_url)
        final_html = render(
            "layout.html",
            title="Scanner IP Local",
            hostname=escape(socket.gethostname()),
            base_url=escape(base_url),
            version=str(int(time.time())),
            nav=nav_html,
            content=scan_content
        )
        
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(final_html.encode("utf-8"))

    def handle_ip_scan_start(self):
        if is_ipscan_running():
            return self.send_json({"status": "error", "message": "Scan déjà en cours"})
        
        start_ip_scan_in_background()
        self.send_json({"status": "ok", "message": "Scan lancé"})

    def handle_fleet_status(self):
        from services.fleet import fleet
        self.send_json({
            "registered": fleet.is_registered(),
            "hostname": fleet.hostname,
            "url": fleet.base_url
        })

    def handle_fleet_register(self):
        from services.fleet import fleet
        if fleet.register():
            self.send_json({"status": "ok", "message": "Enregistrement réussi"})
        else:
            self.send_error(403, "Échec de l'enregistrement (vérifiez le secret)")

    def handle_site_search_external(self):
        parsed_url = urlparse(self.path)
        query = parse_qs(parsed_url.query).get('q', [''])[0]
        if not query: return self.send_json([])
        from services.fleet import fleet
        try:
            chantiers = fleet.get_chantiers(query=query)
            results = [{"external_id": str(c["id"]), "name": c["ref"]} for c in chantiers]
            self.send_json(results)
        except Exception as e:
            logger.error(f"Erreur recherche site fleet: {e}")
            self.send_error(500, str(e))

    def handle_bacnet_device_add(self):
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
            add_device_to_site(site_id=site_id, template_id=data.get("template_id"), name=data.get("name"),
                               device_instance=int(data.get("device_instance")), network_address=data.get("network_address"))
            self.send_json({"status": "ok", "message": "Appareil BACnet ajouté"})
        except Exception as e: self.send_error(400, str(e))

    def handle_bacnet_template_save(self):
        content_length = int(self.headers['Content-Length'])
        post_data = self.rfile.read(content_length)
        try:
            data = json.loads(post_data)
            from services.bacnet_mgr import save_template
            objects = []
            ids, names = data.get("obj_id[]", []), data.get("obj_name[]", [])
            if isinstance(ids, str): ids, names = [ids], [names]
            for i in range(len(ids)):
                if ids[i] and names[i]: objects.append({"obj": ids[i], "name": names[i]})
            template_id = data.get("template_id")
            if template_id == "": template_id = None
            save_template(name=data.get("name"), manufacturer=data.get("manufacturer"), objects=objects, template_id=template_id)
            self.send_json({"status": "ok", "message": "Template BACnet enregistré"})
        except Exception as e: self.send_error(400, str(e))

    def handle_modbus_template_save(self):
        content_length = int(self.headers['Content-Length'])
        post_data = self.rfile.read(content_length)
        try:
            data = json.loads(post_data)
            from services.modbus_mgr import save_template
            registers = []
            addrs, names, types = data.get("reg_addr[]", []), data.get("reg_name[]", []), data.get("reg_type[]", [])
            if isinstance(addrs, str): addrs, names, types = [addrs], [names], [types]
            for i in range(len(addrs)):
                if addrs[i] and names[i]: registers.append({"reg": int(addrs[i]), "name": names[i], "type": types[i]})
            template_id = data.get("template_id")
            if template_id == "": template_id = None
            save_template(name=data.get("name"), manufacturer=data.get("manufacturer"), registers=registers, template_id=template_id)
            self.send_json({"status": "ok", "message": "Template enregistré"})
        except Exception as e: self.send_error(400, str(e))

    def handle_site_rename(self):
        content_length = int(self.headers['Content-Length'])
        post_data = self.rfile.read(content_length)
        try:
            data = json.loads(post_data)
            from services.presence import label_current_location
            if label_current_location(data.get("name"), external_id=data.get("external_id")):
                self.send_json({"status": "ok", "message": "Chantier renommé"})
            else: self.send_error(500, "Échec du renommage")
        except Exception as e: self.send_error(400, str(e))

    def handle_modbus_device_add(self):
        content_length = int(self.headers['Content-Length'])
        post_data = self.rfile.read(content_length)
        try:
            data = json.loads(post_data)
            from services.presence import get_current_site_name
            from services.modbus_mgr import add_device_to_site
            from core.database import get_db_connection
            site_name = get_current_site_name()
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT id FROM sites WHERE name = ?", (site_name,))
                site_id = cursor.fetchone()["id"]
            add_device_to_site(site_id=site_id, template_id=data.get("template_id"), name=data.get("name"), protocol=data.get("protocol"),
                               address=data.get("address_ip") if data.get("protocol") == "tcp" else data.get("slave_id"),
                               port=int(data.get("port", 502)) if data.get("protocol") == "tcp" else None)
            self.send_json({"status": "ok", "message": "Appareil Modbus ajouté"})
        except Exception as e: self.send_error(400, str(e))

    def handle_restart(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"Redemarrage en cours...")
        time.sleep(0.5)
        os.execve(sys.executable, [sys.executable] + sys.argv, os.environ)

    def handle_system_action(self, action):
        self.send_json({"status": "ok", "message": f"{action} initié"})
        try:
            if action == "reboot": subprocess.run(["sudo", "systemctl", "reboot", "--no-block"], check=True)
            elif action == "shutdown": subprocess.run(["sudo", "systemctl", "poweroff", "--no-block"], check=True)
        except Exception as e: logger.error(f"Erreur lors de l'action {action} : {e}")

    def handle_ip_annotate(self):
        content_length = int(self.headers.get('Content-Length', 0))
        post_data = self.rfile.read(content_length)
        try:
            data = json.loads(post_data.decode('utf-8'))
            mac = data.get('mac', '').lower()
            vendor = data.get('vendor')
            annotations = data.get('annotations')
            annotations_json = None
            
            if not mac:
                return self.send_json({"status": "error", "message": "MAC manquante"})
                
            with get_db_connection() as conn:
                if annotations is not None:
                    if isinstance(annotations, dict):
                        annotations_json = json.dumps(annotations)
                    else:
                        annotations_json = annotations
                        
                conn.execute("""
                    INSERT INTO discovered_devices (mac, vendor, annotations_json, last_seen, updated_at, is_dirty)
                    VALUES (?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 1)
                    ON CONFLICT(mac) DO UPDATE SET
                        vendor = COALESCE(?, discovered_devices.vendor),
                        annotations_json = COALESCE(?, discovered_devices.annotations_json),
                        updated_at = CURRENT_TIMESTAMP,
                        is_dirty = 1,
                        last_seen = CURRENT_TIMESTAMP
                """, (mac, vendor, annotations_json if annotations is not None else None, vendor, annotations_json if annotations is not None else None))
                conn.commit()
            
            # Déclenchement immédiat d'une synchronisation si le boîtier est enregistré
            if fleet.is_registered():
                logger.info(f"Déclenchement d'une synchronisation immédiate pour {mac}")
                # On lance ça dans un thread pour ne pas bloquer la réponse UI
                import threading
                gsm = get_gsm_info()
                threading.Thread(target=fleet.sync_location, args=(gsm,), daemon=True).start()
            
            self.send_json({"status": "ok"})
        except Exception as e:
            logger.error(f"Erreur annotation : {e}")
            self.send_json({"status": "error", "message": str(e)})

    def send_json(self, data):
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode("utf-8"))

def start_server():
    config = load_config()
    port = config.get("port", 8081)
    server_address = ('', port)
    httpd = ThreadingHTTPServer(server_address, WebAdminHandler)
    logger.info(f"Serveur HTTP rpinode sur port {port}")
    try: httpd.serve_forever()
    except KeyboardInterrupt: httpd.server_close()
