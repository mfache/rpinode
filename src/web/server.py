import json
import logging
import os
import socket
import subprocess
import sys
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
from urllib.parse import parse_qs, urlparse

from core.config import load_config
from core.database import get_db_connection
from core.paths import STATIC_DIR
from services.fleet import fleet
from services.gsm import get_gsm_info
from services.ipscan import (is_ipscan_running, load_ipscan_results,
                             start_ip_scan_in_background)
from web.stream import handle_sse_stream
from web.templating import escape, render

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
        
        if path.startswith("/rpinode/"):
            path = path[len("/rpinode"):]
        elif path == "/rpinode":
            path = "/"
            
        if len(path) > 1 and path.endswith("/"):
            path = path[:-1]

        logger.info(f"GET Request: {path} (original: {self.path})")
        
        if path == "/sw.js":
            return self.serve_static("/static/sw.js")
        if path == "/manifest.json":
            return self.serve_static("/static/manifest.json")
        if path.startswith("/static/"):
            return self.serve_static(path)
        if path == "/api/stream":
            return handle_sse_stream(self)
        if path == "/api/scan/ip/results":
            return self.serve_ip_scan_results()

        if path == "/api/status":
            return self.send_json({"status": "ok", "message": "Le serveur rpinode tourne."})
        elif path == "/api/gsm":
            return self.send_json(get_gsm_info())
        elif path == "/api/site/search":
            return self.handle_site_search_external()
        elif path == "/api/fleet/status":
            return self.handle_fleet_status()
        elif path == "/api/network/wifi/list":
            from services.wifi_mgr import get_visible_ssids
            return self.send_json(get_visible_ssids())
        
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
        elif path == "/api/table/columns/add":
            self.handle_column_add()
        elif path == "/api/table/columns/delete":
            self.handle_column_delete()
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
        elif filename.endswith(".svg"): content_type = "image/svg+xml"
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
        nav_html = render("nav.html", base_url=base_url)
        content = render("home.html", hostname=hostname)
        final_html = render("layout.html", title="Accueil", hostname=escape(hostname), base_url=escape(base_url), version=version, nav=nav_html, content=content)
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(final_html.encode("utf-8"))

    def serve_network_overview(self):
        config = load_config()
        base_url = config.get("base_url", "")
        hostname = socket.gethostname()
        version = str(int(time.time()))
        nav_html = render("nav.html", base_url=base_url)
        from services.network import get_network_overview
        net = get_network_overview()
        content = render("network_overview.html", net=net)
        final_html = render("layout.html", title="État Réseau", hostname=escape(hostname), base_url=escape(base_url), version=version, nav=nav_html, content=content)
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(final_html.encode("utf-8"))

    def serve_network_interfaces(self):
        config = load_config()
        base_url = config.get("base_url", "")
        hostname = socket.gethostname()
        version = str(int(time.time()))
        from core.database import get_db_connection
        from services.network import get_interface_status
        from services.network_config import get_site_network_profile
        from services.presence import get_current_site_name
        site_name = get_current_site_name()
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id FROM sites WHERE name = ?", (site_name,))
            site_row = cursor.fetchone()
            site_id = site_row["id"] if site_row else None

        def get_iface_context(iface, profile, status):
            method = profile["method"] if profile else "auto"
            addresses = profile["addresses"] if profile and profile["addresses"] else ""
            try:
                import json
                addresses_list = json.loads(addresses) if addresses else []
                if not isinstance(addresses_list, list):
                    addresses_list = [addresses]
            except Exception:
                addresses_list = [a.strip() for a in addresses.split(",") if a.strip()]
            
            if not addresses_list: addresses_list = [""]
            
            addresses_rows = ""
            for addr in addresses_list:
                addresses_rows += f"""
                <div class="address-row">
                    <input type="text" name="addresses" value="{escape(addr)}" placeholder="192.168.1.10/24">
                    <button type="button" class="btn-remove" onclick="removeAddressRow(this)">×</button>
                </div>
                """
            return {
                f"{iface}_method_auto_selected": 'selected' if method == "auto" else '',
                f"{iface}_method_manual_selected": 'selected' if method == "manual" else '',
                f"{iface}_manual_fields_display": 'block' if method == "manual" else 'none',
                f"{iface}_addresses_rows": addresses_rows,
                f"{iface}_gateway": profile["gateway"] if profile and profile["gateway"] else "",
                f"{iface}_ssid": profile["ssid"] if profile and "ssid" in profile.keys() and profile["ssid"] else "",
                f"{iface}_psk": profile["psk"] if profile and "psk" in profile.keys() and profile["psk"] else ""
            }
        eth0_profile = get_site_network_profile(site_id, "eth0") if site_id else None
        eth0_status = get_interface_status("eth0")
        eth0_ctx = get_iface_context("eth0", eth0_profile, eth0_status)
        wlan0_profile = get_site_network_profile(site_id, "wlan0") if site_id else None
        wlan0_status = get_interface_status("wlan0")
        wlan0_ctx = get_iface_context("wlan0", wlan0_profile, wlan0_status)
        context = {"site_name": site_name, **eth0_ctx, **wlan0_ctx}
        content = render("network_interfaces.html", **context)
        nav_html = render("nav.html", base_url=base_url)
        final_html = render("layout.html", title="Configuration Interfaces", hostname=escape(hostname), base_url=escape(base_url), version=version, nav=nav_html, content=content)
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(final_html.encode("utf-8"))

    def handle_network_profile_save(self):
        content_length = int(self.headers['Content-Length'])
        post_data = self.rfile.read(content_length)
        try:
            data = json.loads(post_data)
            from services.network_config import (apply_site_network_profiles,
                                                 save_site_network_profiles)
            from services.presence import get_current_site_name
            site_name = get_current_site_name()
            if site_name == "Inconnu": return self.send_json({"status": "error", "message": "Chantier non identifié."})
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT id FROM sites WHERE name = ?", (site_name,))
                row = cursor.fetchone()
                if not row: return self.send_json({"status": "error", "message": f"Le chantier '{site_name}' n'est pas en base."})
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
        from services.modbus_mgr import get_all_templates, get_site_devices
        from services.presence import get_current_site_name
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
            templates_html += f"<tr><td>{t['name']}</td><td>{t['manufacturer']}</td><td><button class='btn-blue' onclick='showEditTemplateModal({t_json})'>Modifier</button></td></tr>"
        if not templates: templates_html = "<tr><td colspan='3'>Aucun template disponible</td></tr>"
        devices_html = "".join([f"<div class='status-card'><strong>{d['name']}</strong><br>{d['template_name']} ({d['protocol']}://{d['address']})</div>" for d in devices])
        if not devices: devices_html = "<p>Aucun appareil configuré sur ce site.</p>"
        options_html = "".join([f"<option value='{t['id']}'>{t['name']}</option>" for t in templates])
        content = render("modbus.html", site_name=site_name, templates_list_html=templates_html, devices_list_html=devices_html, templates_options_html=options_html)
        nav_html = render("nav.html", base_url=base_url)
        final_html = render("layout.html", title="Gestion Modbus", hostname=escape(hostname), base_url=escape(base_url), version=version, nav=nav_html, content=content)
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(final_html.encode("utf-8"))

    def serve_bacnet_mgr(self):
        config = load_config()
        base_url = config.get("base_url", "")
        hostname = socket.gethostname()
        version = str(int(time.time()))
        from services.bacnet_mgr import get_all_templates, get_site_devices
        from services.presence import get_current_site_name
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
            templates_html += f"<tr><td>{t['name']}</td><td>{t['manufacturer']}</td><td><button class='btn-blue' onclick='showEditTemplateModal({t_json})'>Modifier</button></td></tr>"
        if not templates: templates_html = "<tr><td colspan='3'>Aucun template disponible</td></tr>"
        devices_html = "".join([f"<div class='status-card'><strong>{d['name']}</strong><br>{d['template_name']} (Inst: {d['device_instance']} @ {d['network_address']})</div>" for d in devices])
        if not devices: devices_html = "<p>Aucun appareil configuré sur ce site.</p>"
        options_html = "".join([f"<option value='{t['id']}'>{t['name']}</option>" for t in templates])
        content = render("bacnet.html", site_name=site_name, templates_list_html=templates_html, devices_list_html=devices_html, templates_options_html=options_html)
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
        site_name = get_current_site_name()
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM trends WHERE site_id = (SELECT id FROM sites WHERE name = ?) ORDER BY timestamp DESC LIMIT 50", (site_name,))
            rows = cursor.fetchall()
        trends_html = ""
        for r in rows:
            time_str = time.strftime("%H:%M:%S", time.localtime(r["timestamp"]))
            sync_class = "sync-ok" if r["is_synced"] else "sync-pending"
            sync_text = "OK" if r["is_synced"] else "Attente"
            trends_html += f"<tr><td>{time_str}</td><td>{r['protocol'].upper()}</td><td>{r['device_id']}</td><td>{r['object_id']}</td><td><strong>{r['value']}</strong></td><td><span class='sync-status {sync_class}'>{sync_text}</span></td></tr>"
        if not rows: trends_html = "<tr><td colspan='6'>Aucune donnée enregistrée.</td></tr>"
        content = render("trends.html", site_name=site_name, trends_rows_html=trends_html)
        nav_html = render("nav.html", base_url=base_url)
        final_html = render("layout.html", title="Suivi des Points", hostname=escape(hostname), base_url=escape(base_url), version=version, nav=nav_html, content=content)
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(final_html.encode("utf-8"))

    def _render_ip_scan_rows(self, base_url):
        from services.ipscan import load_ipscan_results
        results = load_ipscan_results()
        custom_columns = []
        with get_db_connection() as conn:
            rows = conn.execute("SELECT column_key, column_label FROM custom_column_definitions WHERE table_id = 'ip_scan'").fetchall()
            custom_columns = [dict(r) for r in rows]
        devices_rows = ""
        global_scanned_at = results.get("scanned_at", "Jamais")
        
        if results and results.get("devices"):
            for d in results["devices"]:
                mac = d.get('mac', '').lower()
                vendor = d.get("vendor") or "Inconnu"
                is_dirty = d.get("is_dirty", 0)
                
                # Détermination En ligne / Hors ligne
                is_offline = (d.get("last_seen") != global_scanned_at)
                row_class = "row-offline" if is_offline else ""
                status_dot = "<span title='Hors ligne (historique)' style='color: #e74c3c; font-size: 0.8em; margin-right: 5px;'>🔴</span>" if is_offline else "<span title='En ligne' style='color: #2ecc71; font-size: 0.8em; margin-right: 5px;'>🟢</span>"
                ip_style = "opacity: 0.5; text-decoration: line-through;" if is_offline else "font-weight: bold;"
                
                ports = d.get("ports", [])
                formatted_ports = []
                for p in ports:
                    if p == 502:
                        modbus_info = f" <small style='color:#2ecc71'>({d['modbus_info']})</small>" if d.get("modbus_info") else ""
                        formatted_ports.append(f"<a href='{base_url}/scan/modbus?ip={d['ip']}' style='color: #2ecc71; font-weight: bold;'>502 (Modbus)</a>{modbus_info}")
                    elif p == 47808:
                        bacnet_info = []
                        if d.get("bacnet_instance"): bacnet_info.append(f"Inst: {d['bacnet_instance']}")
                        if d.get("bacnet_name") and d.get("bacnet_name") != "Automate BACnet": bacnet_info.append(d["bacnet_name"])
                        info_str = f" <small style='color:#e67e22'>({', '.join(bacnet_info)})</small>" if bacnet_info else ""
                        formatted_ports.append(f"<a href='{base_url}/scan/bacnet?ip={d['ip']}' style='color: #e67e22; font-weight: bold;'>47808 (BACnet)</a>{info_str}")
                    elif p == 80: formatted_ports.append(f"<a href='http://{d['ip']}' target='_blank' style='color: #3498db;'>80</a>")
                    else: formatted_ports.append(str(p))
                ports_str = ", ".join(formatted_ports) if formatted_ports else "<span style='opacity:0.4;'>Aucun</span>"
                annots = d.get("annotations_json")
                if isinstance(annots, str):
                    try: annots = json.loads(annots)
                    except: annots = {}
                else: annots = annots or {}
                custom_cells = ""
                for col in custom_columns:
                    val = annots.get(col["column_key"], "")
                    custom_cells += f'<td class="editable" onclick="editCell(\'{mac}\', \'{col["column_key"]}\', \'{col["column_label"]}\')">{escape(str(val))}</td>'
                sync_indicator = "<span class='sync-pending' title='En attente de synchronisation'>☁️</span>" if is_dirty else ""
                devices_rows += f"<tr class='{row_class}'><td style='font-family: monospace; {ip_style}'>{status_dot}{escape(d.get('ip'))}</td><td style='font-family: monospace; font-size: 0.85em; color: #666;'>{escape(mac)}</td><td class='editable' onclick=\"editVendor('{mac}', '{escape(vendor)}')\">{escape(vendor)} {sync_indicator}</td><td>{ports_str}</td>{custom_cells}<td><small style='color:#999;'>{escape(d.get('iface'))}</small></td></tr>"
        else: devices_rows = "<tr><td colspan='10' style='text-align:center; padding: 40px; color: #999;'>Aucun résultat.</td></tr>"
        return devices_rows, global_scanned_at, custom_columns

    def serve_ip_scan_results(self):
        config = load_config()
        base_url = config.get("base_url", "")
        devices_rows, scanned_at, _ = self._render_ip_scan_rows(base_url)
        self.send_json({"scanned_at": scanned_at, "html": devices_rows})

    def serve_ip_scan(self):
        config = load_config()
        base_url = config.get("base_url", "")
        hostname = socket.gethostname()
        version = str(int(time.time()))
        from services.ipscan import load_ipscan_results, is_ipscan_running
        
        devices_rows, scanned_at, custom_columns = self._render_ip_scan_rows(base_url)
        
        custom_headers = ""
        for col in custom_columns:
            custom_headers += f"<th><div style='display:flex; align-items:center; justify-content:space-between;'>{escape(col['column_label'])}<button class='btn-icon-sm' onclick=\"deleteColumn('{col['column_key']}', '{escape(col['column_label'])}')\">×</button></div></th>"
        scan_content = render("ip_scan.html", scanned_at=scanned_at, devices_rows=devices_rows, custom_headers=custom_headers, is_running_class="running" if is_ipscan_running() else "idle", is_running_text="Scan en cours..." if is_ipscan_running() else "Prêt")
        nav_html = render("nav.html", base_url=base_url)
        final_html = render("layout.html", title="Inventaire Réseau", hostname=escape(hostname), base_url=escape(base_url), version=version, nav=nav_html, content=scan_content)
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(final_html.encode("utf-8"))

    def handle_ip_scan_start(self):
        if is_ipscan_running(): return self.send_json({"status": "error", "message": "Scan déjà en cours"})
        start_ip_scan_in_background()
        self.send_json({"status": "ok", "message": "Scan lancé"})

    def handle_fleet_status(self):
        self.send_json({"registered": fleet.is_registered(), "hostname": socket.gethostname(), "url": fleet.base_url})

    def handle_fleet_register(self):
        if fleet.register(): self.send_json({"status": "ok", "message": "Enregistrement réussi"})
        else: self.send_error(403, "Échec de l'enregistrement")

    def handle_site_search_external(self):
        parsed_url = urlparse(self.path)
        query = parse_qs(parsed_url.query).get('q', [''])[0]
        if not query: return self.send_json([])
        try:
            chantiers = fleet.get_chantiers(query=query)
            results = [{"external_id": str(c["id"]), "name": c["ref"]} for c in chantiers]
            self.send_json(results)
        except Exception as e: self.send_error(500, str(e))

    def handle_bacnet_device_add(self):
        content_length = int(self.headers['Content-Length'])
        post_data = self.rfile.read(content_length)
        try:
            data = json.loads(post_data)
            from services.bacnet_mgr import add_device_to_site
            from services.presence import get_current_site_name
            site_name = get_current_site_name()
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT id FROM sites WHERE name = ?", (site_name,))
                site_id = cursor.fetchone()["id"]
            add_device_to_site(site_id=site_id, template_id=data.get("template_id"), name=data.get("name"), device_instance=int(data.get("device_instance")), network_address=data.get("network_address"))
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
            save_template(name=data.get("name"), manufacturer=data.get("manufacturer"), objects=objects, template_id=data.get("template_id") or None)
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
            save_template(name=data.get("name"), manufacturer=data.get("manufacturer"), registers=registers, template_id=data.get("template_id") or None)
            self.send_json({"status": "ok", "message": "Template enregistré"})
        except Exception as e: self.send_error(400, str(e))

    def handle_site_rename(self):
        content_length = int(self.headers['Content-Length'])
        post_data = self.rfile.read(content_length)
        try:
            data = json.loads(post_data)
            from services.presence import label_current_location
            if label_current_location(data.get("name"), external_id=data.get("external_id")): self.send_json({"status": "ok"})
            else: self.send_error(500, "Échec du renommage")
        except Exception as e: self.send_error(400, str(e))

    def handle_modbus_device_add(self):
        content_length = int(self.headers['Content-Length'])
        post_data = self.rfile.read(content_length)
        try:
            data = json.loads(post_data)
            from services.modbus_mgr import add_device_to_site
            from services.presence import get_current_site_name
            site_name = get_current_site_name()
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT id FROM sites WHERE name = ?", (site_name,))
                site_id = cursor.fetchone()["id"]
            add_device_to_site(site_id=site_id, template_id=data.get("template_id"), name=data.get("name"), protocol=data.get("protocol"), address=data.get("address_ip") if data.get("protocol") == "tcp" else data.get("slave_id"), port=int(data.get("port", 502)) if data.get("protocol") == "tcp" else None)
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

    def handle_column_add(self):
        content_length = int(self.headers.get('Content-Length', 0))
        post_data = self.rfile.read(content_length)
        try:
            data = json.loads(post_data)
            table_id, label = data.get("table_id"), data.get("label")
            if not table_id or not label: return self.send_json({"status": "error", "message": "Données manquantes"})
            import re
            key = re.sub(r'[^a-z0-9]', '_', label.lower().strip())
            with get_db_connection() as conn:
                conn.execute("INSERT INTO custom_column_definitions (table_id, column_key, column_label) VALUES (?, ?, ?) ON CONFLICT DO NOTHING", (table_id, key, label))
                conn.commit()
            self.send_json({"status": "ok", "column_key": key})
        except Exception as e: self.send_json({"status": "error", "message": str(e)})

    def handle_column_delete(self):
        content_length = int(self.headers.get('Content-Length', 0))
        post_data = self.rfile.read(content_length)
        try:
            data = json.loads(post_data)
            table_id, column_key = data.get("table_id"), data.get("column_key")
            if not table_id or not column_key: return self.send_json({"status": "error", "message": "Données manquantes"})

            with get_db_connection() as conn:
                # 1. Supprimer la définition
                conn.execute("DELETE FROM custom_column_definitions WHERE table_id = ? AND column_key = ?", (table_id, column_key))
                
                # 2. Nettoyer les données dans discovered_devices si c'est pour l'IP scan
                if table_id == 'ip_scan':
                    rows = conn.execute("SELECT mac, annotations_json FROM discovered_devices WHERE annotations_json LIKE ?", (f'%"{column_key}":%',)).fetchall()
                    for row in rows:
                        try:
                            annots = json.loads(row["annotations_json"])
                            if column_key in annots:
                                del annots[column_key]
                                conn.execute("UPDATE discovered_devices SET annotations_json = ?, is_dirty = 1 WHERE mac = ?", (json.dumps(annots), row["mac"]))
                        except: continue
                
                conn.commit()
            
            # Déclenchement synchro pour propager la suppression au serveur docs
            if fleet.is_registered():
                fleet.delete_table_column(table_id, column_key)
                import threading
                threading.Thread(target=fleet.sync_location, args=(get_gsm_info(),), daemon=True).start()

            self.send_json({"status": "ok"})
        except Exception as e: self.send_json({"status": "error", "message": str(e)})

    def handle_ip_annotate(self):
        content_length = int(self.headers.get('Content-Length', 0))
        post_data = self.rfile.read(content_length)
        try:
            data = json.loads(post_data)
            mac, vendor, annotations = data.get('mac', '').lower(), data.get('vendor'), data.get('annotations')
            if not mac: return self.send_json({"status": "error", "message": "MAC manquante"})
            from services.presence import get_current_site_id
            site_id = get_current_site_id()
            if not site_id: return self.send_json({"status": "error", "message": "Aucun chantier actif"})
            with get_db_connection() as conn:
                if vendor:
                    conn.execute("INSERT INTO mac_vendors (prefix, vendor, is_dirty) VALUES (?, ?, 1) ON CONFLICT(prefix) DO UPDATE SET vendor = EXCLUDED.vendor, is_dirty = 1", (mac[:8], vendor))
                    conn.execute("UPDATE discovered_devices SET vendor = ?, is_dirty = 1 WHERE mac = ?", (vendor, mac))
                if annotations is not None:
                    row = conn.execute("SELECT annotations_json FROM discovered_devices WHERE mac = ?", (mac,)).fetchone()
                    existing = json.loads(row["annotations_json"]) if row and row["annotations_json"] else {}
                    if isinstance(annotations, dict): existing.update(annotations)
                    conn.execute("UPDATE discovered_devices SET annotations_json = ?, is_dirty = 1 WHERE mac = ?", (json.dumps(existing), mac))
                conn.commit()
            if fleet.is_registered():
                import threading
                threading.Thread(target=fleet.sync_location, args=(get_gsm_info(),), daemon=True).start()
            self.send_json({"status": "ok"})
        except Exception as e: self.send_json({"status": "error", "message": str(e)})

    def send_json(self, data):
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode("utf-8"))

def start_server():
    config = load_config()
    port = int(config.get("port", 8081))
    server_address = ('', port)
    ThreadingHTTPServer.allow_reuse_address = True
    httpd = ThreadingHTTPServer(server_address, WebAdminHandler)
    logger.info(f"Serveur HTTP rpinode sur port {port}")
    try: httpd.serve_forever()
    except KeyboardInterrupt: httpd.server_close()
