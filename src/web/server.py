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
from pathlib import Path

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
        query = parse_qs(parsed_url.query)
        
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
        elif path == "/modbus/devices":
            return self.serve_modbus_devices()
        elif path == "/modbus/suivi":
            return self.serve_modbus_suivi()
        elif path == "/modbus/device/view":
            return self.serve_modbus_device_view(query)
        elif path == "/modbus/templates":
            return self.serve_modbus_templates()
        elif path == "/modbus/tools":
            return self.serve_modbus_tools(query)
        elif path in ("/api/modbus/suivi/values", "/api/monitor/suivi/values"):
            return self.serve_modbus_suivi_values()
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
        elif path == "/api/modbus/device/delete":
            self.handle_modbus_device_delete()
        elif path == "/api/modbus/device/points/save":
            self.handle_modbus_device_points_save()
        elif path == "/api/modbus/point/update":
            self.handle_modbus_point_update()
        elif path == "/api/modbus/point/delete":
            self.handle_modbus_point_delete()
        elif path == "/api/modbus/template/save":
            self.handle_modbus_template_save()
        elif path == "/api/modbus/template/delete":
            self.handle_modbus_template_delete()
        elif path == "/api/modbus/template/import_from_fleet":
            self.handle_modbus_template_import_from_fleet()
        elif path == "/api/modbus/template/share_to_fleet":
            self.handle_modbus_template_share_to_fleet()
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
        elif path == "/api/modbus/tools/probe":
            self.handle_modbus_probe()
        elif path == "/api/modbus/tools/read":
            self.handle_modbus_read()
        elif path == "/api/modbus/tools/write":
            self.handle_modbus_write()
        else:
            self.send_error(404, "Action non trouvée")

    def serve_static(self, path):
        try:
            if ".." in path:
                self.send_error(403, "Forbidden")
                return

            if path.startswith("/"):
                path = path[1:]
                
            filepath = Path(path)
            if not filepath.exists() or not filepath.is_file():
                self.send_error(404, "File Not Found")
                return

            content_type = "text/plain"
            if path.endswith(".css"): content_type = "text/css"
            elif path.endswith(".js"): content_type = "application/javascript"
            elif path.endswith(".json"): content_type = "application/json"
            elif path.endswith(".html"): content_type = "text/html"
            elif path.endswith(".png"): content_type = "image/png"

            with open(filepath, "rb") as f:
                content = f.read()

            self.send_response(200)
            self.send_header("Content-Type", f"{content_type}; charset=utf-8")
            self.send_header("Cache-Control", "public, max-age=3600")
            self.end_headers()
            self.wfile.write(content)
        except Exception as e:
            self.send_error(500, str(e))

    def handle_modbus_probe(self):
        try:
            content_len = int(self.headers.get("Content-Length", 0))
            post_data = self.rfile.read(content_len).decode("utf-8")
            data = json.loads(post_data)
            from services.modbus_tools import probe_range
            
            protocol = data.get("protocol", "tcp")
            address = data.get("address", "")
            port = int(data.get("port", 502))
            unit = int(data.get("unit", 1))
            funcs = [int(f) for f in data.get("funcs", [3])]
            start = int(data.get("start", 0))
            end = int(data.get("end", 100))
            block = int(data.get("block", 20))
            timeout = float(data.get("timeout", 1.0))
            
            results = probe_range(protocol, address, port, unit, funcs, start, end, block, timeout)
            self.send_json({"status": "ok", "results": results})
        except Exception as e:
            self.send_json({"status": "error", "message": str(e)})

    def handle_modbus_read(self):
        try:
            content_len = int(self.headers.get("Content-Length", 0))
            post_data = self.rfile.read(content_len).decode("utf-8")
            data = json.loads(post_data)
            from services.modbus_tools import read_registers, read_bits
            
            protocol = data.get("protocol", "tcp")
            address = data.get("address", "")
            port = int(data.get("port", 502))
            unit = int(data.get("unit", 1))
            base = int(data.get("base", 0))
            func = int(data.get("function", 3))
            reg_addr = int(data.get("address_start", 0))
            wire_addr = reg_addr - (1 if base == 1 else 0)
            count = int(data.get("count", 1))
            timeout = float(data.get("timeout", 1.5))
            
            if func in (1, 2):
                vals = read_bits(protocol, address, port, unit, func, wire_addr, count, timeout)
            else:
                vals = read_registers(protocol, address, port, unit, func, wire_addr, count, timeout)
                
            self.send_json({"status": "ok", "values": vals, "function": func})
        except Exception as e:
            self.send_json({"status": "error", "message": str(e)})

    def handle_modbus_write(self):
        try:
            content_len = int(self.headers.get("Content-Length", 0))
            post_data = self.rfile.read(content_len).decode("utf-8")
            data = json.loads(post_data)
            from services.modbus_tools import write_single_register, write_single_coil
            
            protocol = data.get("protocol", "tcp")
            address = data.get("address", "")
            port = int(data.get("port", 502))
            unit = int(data.get("unit", 1))
            func = int(data.get("function", 6))
            reg_addr = int(data.get("address_start", 0))
            value = float(data.get("value", 0))
            timeout = float(data.get("timeout", 1.0))
            
            if func == 5:
                write_single_coil(protocol, address, port, unit, reg_addr, bool(value), timeout)
            else:
                write_single_register(protocol, address, port, unit, reg_addr, int(value), timeout)
                
            self.send_json({"status": "ok", "message": "Écriture effectuée avec succès."})
        except Exception as e:
            self.send_json({"status": "error", "message": str(e)})

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

    def serve_modbus_suivi(self):
        config = load_config()
        base_url = config.get("base_url", "")
        hostname = socket.gethostname()
        version = str(int(time.time()))
        from services.modbus_mgr import get_site_modbus_points
        from services.presence import get_current_site_name
        site_name = get_current_site_name()
        
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id FROM sites WHERE name = ?", (site_name,))
            site_row = cursor.fetchone()
            site_id = site_row["id"] if site_row else None
            
        points = get_site_modbus_points(site_id, only_monitored=True) if site_id else []
        
        rows_html = ""
        cadences = ["5s", "10s", "30s", "1m", "5m"]
        
        for p in points:
            pid = p["id"]
            rec_checked = "checked" if p["is_recorded"] else ""
            cad_options = "".join([
                f"<option value='{c}' {'selected' if p.get('cadence') == c else ''}>{c}</option>"
                for c in cadences
            ])
            val_display = p["last_value"] if p["last_value"] is not None else "—"
            if p["unit"] and p["last_value"] is not None:
                val_display += f" {p['unit']}"
                
            unit_display = f"<code>{p['protocol']}://{p['address']}{':' + str(p['port']) if p['port'] and p['port'] != 502 else ''}</code>"
            
            rows_html += f"""
                <tr id="suivi-row-{pid}">
                    <td><strong>{escape(p['device_name'])}</strong><br><small style="color:#777;">{unit_display}</small></td>
                    <td><span class="badge-gray">FC{p['function']:02d} @{p['reg']}</span></td>
                    <td><strong>{escape(p['name'])}</strong></td>
                    <td><b class="live-val badge-gray" id="val-{pid}" data-suivi-key="{pid}">{escape(val_display)}</b></td>
                    <td>
                        <label style="cursor:pointer; display:inline-flex; align-items:center; gap:5px;">
                            <input type="checkbox" class="cb-record" data-point-id="{pid}" {rec_checked} onchange="toggleRecord({pid}, this.checked)">
                            <span>Enregistrer</span>
                        </label>
                    </td>
                    <td>
                        <select id="cadence-{pid}" class="cadence-select" {'disabled' if not p['is_recorded'] else ''} onchange="changeCadence({pid}, this.value)">
                            {cad_options}
                        </select>
                    </td>
                    <td>
                        <button class="btn-icon-del" onclick="removePoint({pid})" title="Retirer du suivi">🗑️</button>
                    </td>
                </tr>
            """
            
        if not points:
            rows_html = "<tr><td colspan='7' style='text-align:center; padding:30px; color:#888;'>Aucun point sélectionné pour le suivi sur ce chantier.<br><a href='" + base_url + "/modbus/devices' class='btn-secondary btn-sm' style='margin-top:10px; display:inline-block;'>Sélectionner des points sur un appareil</a></td></tr>"
            
        content = render("modbus_suivi.html", site_name=site_name, suivi_rows_html=rows_html, base_url=base_url)
        nav_html = render("nav.html", base_url=base_url)
        final_html = render("layout.html", title="Suivi Modbus (Live)", hostname=escape(hostname), base_url=escape(base_url), version=version, nav=nav_html, content=content)
        
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(final_html.encode("utf-8"))

    def serve_modbus_suivi_values(self):
        from services.modbus_mgr import read_site_monitored_points_live
        from services.presence import get_current_site_name
        site_name = get_current_site_name()
        
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id FROM sites WHERE name = ?", (site_name,))
            row = cursor.fetchone()
            site_id = row["id"] if row else None
            
        if not site_id:
            return self.send_json({"status": "error", "message": "Aucun chantier actif", "values": {}})
            
        values = read_site_monitored_points_live(site_id)
        self.send_json({"status": "ok", "values": values})

    def handle_modbus_device_points_save(self):
        content_length = int(self.headers.get('Content-Length', 0))
        post_data = self.rfile.read(content_length)
        try:
            data = json.loads(post_data)
            device_id = int(data.get("device_id"))
            points = data.get("points", [])
            
            from services.modbus_mgr import save_device_points_selection
            from services.presence import get_current_site_name
            site_name = get_current_site_name()
            
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT id FROM sites WHERE name = ?", (site_name,))
                row = cursor.fetchone()
                site_id = row["id"] if row else None
                
            if not site_id:
                return self.send_json({"status": "error", "message": "Aucun chantier actif"})
                
            save_device_points_selection(device_id, site_id, points)
            self.send_json({"status": "ok", "message": "Sélection enregistrée"})
        except Exception as e:
            logger.error(f"Erreur save device points: {e}")
            self.send_json({"status": "error", "message": str(e)})

    def handle_modbus_point_update(self):
        content_length = int(self.headers.get('Content-Length', 0))
        post_data = self.rfile.read(content_length)
        try:
            data = json.loads(post_data)
            point_id = int(data.get("point_id"))
            is_monitored = data.get("is_monitored")
            is_recorded = data.get("is_recorded")
            cadence = data.get("cadence")
            
            from services.modbus_mgr import update_point_settings
            success = update_point_settings(point_id, is_monitored=is_monitored, is_recorded=is_recorded, cadence=cadence)
            if success:
                self.send_json({"status": "ok"})
            else:
                self.send_json({"status": "error", "message": "Point introuvable"})
        except Exception as e:
            logger.error(f"Erreur update point: {e}")
            self.send_json({"status": "error", "message": str(e)})

    def handle_modbus_point_delete(self):
        content_length = int(self.headers.get('Content-Length', 0))
        post_data = self.rfile.read(content_length)
        try:
            data = json.loads(post_data)
            point_id = int(data.get("point_id"))
            from services.modbus_mgr import delete_modbus_point
            delete_modbus_point(point_id)
            self.send_json({"status": "ok"})
        except Exception as e:
            logger.error(f"Erreur delete point: {e}")
            self.send_json({"status": "error", "message": str(e)})

    def handle_modbus_device_delete(self):
        content_length = int(self.headers.get('Content-Length', 0))
        post_data = self.rfile.read(content_length)
        try:
            data = json.loads(post_data)
            device_id = int(data.get("device_id"))
            from services.modbus_mgr import delete_device_from_site
            delete_device_from_site(device_id)
            self.send_json({"status": "ok", "message": "Appareil supprimé"})
        except Exception as e:
            logger.error(f"Erreur delete device: {e}")
            self.send_json({"status": "error", "message": str(e)})

    def serve_modbus_devices(self):
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
        
        total_monitored_site = 0
        total_recorded_site = 0
        cards_html = []
        
        with get_db_connection() as conn:
            cursor = conn.cursor()
            for d in devices:
                dev_id = d["id"]
                # Nombre de points suivis / enregistrés
                cursor.execute(
                    "SELECT COUNT(*) as count_mon, SUM(is_recorded) as count_rec FROM modbus_points WHERE device_id = ? AND is_monitored = 1",
                    (dev_id,)
                )
                stats = cursor.fetchone()
                mon_count = stats["count_mon"] or 0
                rec_count = stats["count_rec"] or 0
                total_monitored_site += mon_count
                total_recorded_site += rec_count
                
                # Nombre total de registres dans le template
                total_tpl_regs = 0
                cursor.execute("SELECT registers_json FROM modbus_templates WHERE id = ?", (d["template_id"],))
                tpl_row = cursor.fetchone()
                if tpl_row and tpl_row["registers_json"]:
                    try:
                        regs = json.loads(tpl_row["registers_json"])
                        total_tpl_regs = len(regs)
                    except Exception:
                        total_tpl_regs = 0
                        
                is_tcp = (d.get("protocol") == "tcp")
                proto_label = "Modbus TCP" if is_tcp else "Modbus RTU"
                proto_class = "proto-modbus-tcp" if is_tcp else "proto-modbus-mstp"
                port_str = f":{d['port']}" if is_tcp and d.get('port') and d['port'] != 502 else ""
                slave_unit = d.get("slave_unit") or 1
                if is_tcp:
                    addr_display = f"{d['protocol']}://{d['address']}{port_str} (Esclave {slave_unit})"
                else:
                    addr_display = f"Modbus RTU (Esclave {slave_unit})"
                
                manu = d.get('template_manufacturer') or 'Générique'
                
                cards_html.append(f"""
                <div class="device-card" id="device-card-{dev_id}">
                    <div class="device-card-header">
                        <div style="min-width: 0;">
                            <div style="display: flex; align-items: center; gap: 8px; flex-wrap: wrap;">
                                <h3 class="device-title">{escape(d['name'])}</h3>
                                <span class="badge-protocol {proto_class}">{proto_label}</span>
                            </div>
                            <div class="device-meta">
                                <span title="Modèle">📦 {escape(d['template_name'])}</span>
                                <span>•</span>
                                <span title="Fabricant">🏭 {escape(manu)}</span>
                            </div>
                        </div>
                        <button class="btn-icon-del" onclick="deleteDevice({dev_id}, '{escape(d['name'])}')" title="Supprimer cet appareil">
                            🗑️
                        </button>
                    </div>

                    <div class="device-card-body">
                        <div class="device-addr-box">
                            <span class="addr-label">Connexion :</span>
                            <code class="addr-value">{addr_display}</code>
                        </div>

                        <div class="device-stats-grid">
                            <div class="stat-pill">
                                <span class="stat-icon">📊</span>
                                <div>
                                    <div class="stat-num">{mon_count} / {total_tpl_regs}</div>
                                    <div class="stat-desc">Points suivis</div>
                                </div>
                            </div>
                            <div class="stat-pill">
                                <span class="stat-icon">💾</span>
                                <div>
                                    <div class="stat-num">{rec_count}</div>
                                    <div class="stat-desc">Enregistrés</div>
                                </div>
                            </div>
                        </div>
                    </div>

                    <div class="device-card-footer">
                        <a href="{base_url}/modbus/device/view?id={dev_id}" class="btn-primary btn-sm" style="flex: 1; text-align: center; text-decoration: none;">
                            ⚙️ Configurer les points
                        </a>
                        <a href="{base_url}/modbus/tools?protocol={d['protocol']}&address={d['address']}&port={d.get('port', 502)}" class="btn-secondary btn-sm" title="Tester la liaison" style="text-decoration: none;">
                            🔍 Test
                        </a>
                    </div>
                </div>
                """)
        
        devices_html = "".join(cards_html)
        if not devices:
            devices_html = f"""
            <div class="empty-state">
                <div class="empty-icon">🔌</div>
                <h3>Aucun appareil Modbus configuré</h3>
                <p>Ajoutez un appareil (VMC, régulateur, automate, compteur...) pour commencer à superviser et enregistrer ses registres.</p>
                <button class="btn-primary" onclick="showAddDeviceModal()" style="margin-top: 15px;">➕ Ajouter un premier appareil</button>
            </div>
            """
        
        options_html = "".join([f"<option value='{t['id']}'>{t['name']} ({t.get('manufacturer') or 'Générique'})</option>" for t in templates])
        
        content = render(
            "modbus_devices.html",
            site_name=site_name,
            devices_list_html=devices_html,
            templates_options_html=options_html,
            total_devices=len(devices),
            total_monitored=total_monitored_site,
            total_recorded=total_recorded_site,
            base_url=base_url
        )
        nav_html = render("nav.html", base_url=base_url)
        final_html = render("layout.html", title="Appareils Modbus", hostname=escape(hostname), base_url=escape(base_url), version=version, nav=nav_html, content=content)
        
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(final_html.encode("utf-8"))

    def serve_modbus_device_view(self, query):
        device_id = query.get("id", [""])[0]
        if not device_id.isdigit():
            self.send_error(400, "ID d'appareil invalide")
            return
            
        config = load_config()
        base_url = config.get("base_url", "")
        hostname = socket.gethostname()
        version = str(int(time.time()))
        
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT d.*, t.name as template_name, t.registers_json
                FROM modbus_devices d
                JOIN modbus_templates t ON d.template_id = t.id
                WHERE d.id = ?
            """, (int(device_id),))
            device = cursor.fetchone()
            
        if not device:
            self.send_error(404, "Appareil non trouvé")
            return
            
        device = dict(device)
        registers = json.loads(device.get("registers_json") or "[]")
        
        from services.modbus_mgr import get_device_points
        existing_points = get_device_points(int(device_id))
        monitored_keys = {f"{p['function']}:{p['reg']}": p for p in existing_points if p.get("is_monitored")}
        
        rows_html = ""
        for i, reg in enumerate(registers):
            func_val = reg.get("function", 3)
            # Normaliser la fonction (si texte)
            if str(func_val).lower().startswith("fc"): 
                func_val = int(func_val[2:])
            elif isinstance(func_val, str) and func_val.isdigit():
                func_val = int(func_val)
                
            reg_num = int(reg.get('reg'))
            base_val = int(reg.get('base', 0))
            scale = reg.get("scale", 1.0)
            if scale is None: scale = 1.0
            
            is_mon = f"{func_val}:{reg_num}" in monitored_keys
            chk_attr = "checked" if is_mon else ""
            
            # Attributs de données pour le JS
            data_attrs = f"data-reg='{reg_num}' data-base='{base_val}' data-func='{func_val}' data-type='{reg.get('type', 'int16')}' data-scale='{scale}' data-name='{escape(reg.get('name', ''))}' data-unit='{escape(reg.get('unit', ''))}'"
            
            rows_html += f"""
                <tr class="point-row" {data_attrs} id="row-{i}">
                    <td style="text-align:center;">
                        <input type="checkbox" class="cb-monitor" {chk_attr}>
                    </td>
                    <td>{reg_num}</td>
                    <td>FC{func_val:02d}</td>
                    <td><strong>{escape(reg.get('name', ''))}</strong></td>
                    <td>{reg.get('type', 'int16')}</td>
                    <td><span class="live-val badge-gray" id="val-{i}">-</span> {escape(reg.get('unit', ''))}</td>
                    <td>
                        <button class="btn-secondary btn-sm" onclick="readPoint({i})">Lire</button>
                    </td>
                </tr>
            """
            
        if not registers:
            rows_html = "<tr><td colspan='7'>Aucun point défini dans ce template.</td></tr>"
            
        is_tcp = (device.get("protocol") == "tcp")
        slave_unit = device.get("slave_unit") or 1
        port_val = device["port"] or 502
        port_str = f":{port_val}" if is_tcp and port_val != 502 else ""
        if is_tcp:
            conn_display = f"{device['protocol']}://{device['address']}{port_str} (Esclave {slave_unit})"
        else:
            conn_display = f"Modbus RTU (Esclave {slave_unit})"

        device_json = json.dumps({
            "id": device["id"],
            "protocol": device["protocol"],
            "address": device["address"],
            "port": port_val,
            "unit": slave_unit,
        })
            
        content = render("modbus_device_view.html", 
                         device_name=device["name"],
                         modbus_template_name=device["template_name"],
                         protocol=device["protocol"],
                         address=device["address"],
                         port=port_val,
                         slave_unit=slave_unit,
                         conn_display=conn_display,
                         rows_html=rows_html,
                         device_json=device_json,
                         base_url=base_url)
                         
        nav_html = render("nav.html", base_url=base_url)
        final_html = render("layout.html", title=device["name"], hostname=escape(hostname), base_url=escape(base_url), version=version, nav=nav_html, content=content)
        
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(final_html.encode("utf-8"))

    def serve_modbus_templates(self):
        config = load_config()
        base_url = config.get("base_url", "")
        hostname = socket.gethostname()
        version = str(int(time.time()))
        from services.modbus_mgr import get_templates_overview
        
        local_templates, fleet_templates = get_templates_overview()
        
        local_html = ""
        for t in local_templates:
            t_json = json.dumps(dict(t)).replace("'", "\\'")
            escaped_name = t['name'].replace("'", "\\'")
            try:
                regs = json.loads(t.get("registers_json", "[]"))
                reg_count = len(regs)
            except Exception:
                reg_count = 0
                
            version_badge = f"<span style='background:#e8f4fd; color:#2980b9; padding:2px 6px; border-radius:10px; font-size:0.75em; font-weight:bold; margin-left:5px;'>v{t.get('version', 1)}</span>"
            if t.get('is_shared') == 1:
                status_badge = "<span style='background:#e8f8f5; color:#16a085; padding:2px 6px; border-radius:10px; font-size:0.75em; margin-left:4px;'>🌐 Partagé</span>"
            else:
                status_badge = "<span style='background:#fdf2e9; color:#d35400; padding:2px 6px; border-radius:10px; font-size:0.75em; margin-left:4px;'>🔒 Local</span>"

            local_html += (
                f"<tr data-name='{escape(t['name']).lower()}'>"
                f"<td><strong>{escape(t['name'])}</strong> {version_badge} {status_badge}</td>"
                f"<td>{escape(t['manufacturer']) if t['manufacturer'] else '—'}</td>"
                f"<td><span class='badge-count'>{reg_count} reg.</span></td>"
                f"<td style='display:flex; gap:6px; flex-wrap:wrap;'>"
                f"<button class='btn-blue btn-sm' onclick='showEditTemplateModal({t_json})' title='Modifier les registres'>✏️ Modifier</button>"
                f"<button class='btn-secondary btn-sm' onclick='shareTemplate({t['id']}, \"{escaped_name}\")' title='Publier vers la flotte docs'>📤 Partager</button>"
                f"<button class='btn-red btn-sm' onclick='deleteTemplate({t['id']}, \"{escaped_name}\")' title='Supprimer du boîtier'>🗑️</button>"
                f"</td>"
                f"</tr>"
            )
        if not local_templates:
            local_html = "<tr><td colspan='4' style='text-align:center; color:#888; padding:25px;'>Aucun template installé localement.<br><small>Installez-en depuis la bibliothèque de la flotte à droite ou créez-en un nouveau !</small></td></tr>"
            
        fleet_html = ""
        for f in fleet_templates:
            escaped_name = f['name'].replace("'", "\\'")
            if f.get('needs_update'):
                status_action = (
                    f"<span class='badge-installed' style='background:#fef9e7; color:#d4ac0d;'>⚠️ v{f['local_version']} ➔ v{f['version']}</span> "
                    f"<button class='btn-blue btn-sm' onclick='importFromFleet(\"{escaped_name}\", true)' title='Mettre à jour vers la version {f['version']}'>⬆️ Mettre à jour</button>"
                )
            elif f['is_installed']:
                status_action = (
                    f"<span class='badge-installed'>✅ v{f['version']}</span> "
                    f"<button class='btn-gray btn-sm' onclick='importFromFleet(\"{escaped_name}\", true)' title='Réimporter la version de la flotte'>🔄</button>"
                )
            else:
                status_action = (
                    f"<button class='btn-primary btn-sm' onclick='importFromFleet(\"{escaped_name}\", false)'>⬇️ Installer (v{f['version']})</button>"
                )
                
            fleet_html += (
                f"<tr data-name='{escape(f['name']).lower()}'>"
                f"<td><strong>{escape(f['name'])}</strong></td>"
                f"<td><span title='{escape(f['notes'])}'>{escape(f['notes'])[:22] + ('...' if len(f['notes']) > 22 else '')}</span></td>"
                f"<td><span class='badge-count'>{f['reads_count']} reg.</span></td>"
                f"<td>{status_action}</td>"
                f"</tr>"
            )
        if not fleet_templates:
            fleet_html = "<tr><td colspan='4' style='text-align:center; color:#888; padding:25px;'>Bibliothèque de la flotte inaccessible (ou 0 template disponible).</td></tr>"
            
        content = render(
            "modbus_templates.html",
            local_templates_html=local_html,
            local_count=len(local_templates),
            fleet_templates_html=fleet_html,
            fleet_count=len(fleet_templates),
            base_url=base_url
        )
        nav_html = render("nav.html", base_url=base_url)
        final_html = render("layout.html", title="Templates Modbus", hostname=escape(hostname), base_url=escape(base_url), version=version, nav=nav_html, content=content)
        
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(final_html.encode("utf-8"))

    def serve_modbus_tools(self, query):
        config = load_config()
        base_url = config.get("base_url", "")
        hostname = socket.gethostname()
        version = str(int(time.time()))
        
        target_ip = query.get("ip", [""])[0] if "ip" in query else "Non définie"
        
        content = render("modbus_tools.html", target_ip=target_ip)
        nav_html = render("nav.html", base_url=base_url)
        final_html = render("layout.html", title="Outils Modbus", hostname=escape(hostname), base_url=escape(base_url), version=version, nav=nav_html, content=content)
        
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
        from services.modbus_mgr import get_site_modbus_points
        from services.presence import get_current_site_name
        site_name = get_current_site_name()
        
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id FROM sites WHERE name = ?", (site_name,))
            site_row = cursor.fetchone()
            site_id = site_row["id"] if site_row else None
            
        modbus_points = get_site_modbus_points(site_id, only_monitored=True) if site_id else []
        
        rows_html = ""
        cadences = ["5s", "10s", "30s", "1m", "5m"]
        
        for p in modbus_points:
            pid = p["id"]
            rec_checked = "checked" if p["is_recorded"] else ""
            cad_options = "".join([
                f"<option value='{c}' {'selected' if p.get('cadence') == c else ''}>{c}</option>"
                for c in cadences
            ])
            val_display = p["last_value"] if p["last_value"] is not None else "—"
            if p["unit"] and p["last_value"] is not None:
                val_display += f" {p['unit']}"
                
            unit_display = f"<code>{p['protocol']}://{p['address']}{':' + str(p['port']) if p['port'] and p['port'] != 502 else ''}</code>"
            is_tcp = (p.get("protocol") == "tcp")
            proto_key = "modbus-tcp" if is_tcp else "modbus-mstp"
            proto_badge = "proto-modbus-tcp" if is_tcp else "proto-modbus-mstp"
            proto_label = "MODBUS TCP" if is_tcp else "MODBUS RTU"
            
            rows_html += f"""
                <tr id="suivi-row-{pid}" data-proto="{proto_key}">
                    <td><span class="badge-protocol {proto_badge}">{proto_label}</span></td>
                    <td><strong>{escape(p['device_name'])}</strong><br><small style="color:#777;">{unit_display}</small></td>
                    <td><span class="badge-gray">FC{p['function']:02d} @{p['reg']}</span></td>
                    <td><strong>{escape(p['name'])}</strong></td>
                    <td><b class="live-val badge-gray" id="val-{pid}" data-suivi-key="{pid}">{escape(val_display)}</b></td>
                    <td>
                        <label style="cursor:pointer; display:inline-flex; align-items:center; gap:5px;">
                            <input type="checkbox" class="cb-record" data-point-id="{pid}" {rec_checked} onchange="toggleRecord({pid}, this.checked)">
                            <span>Enregistrer</span>
                        </label>
                    </td>
                    <td>
                        <select id="cadence-{pid}" class="cadence-select" {'disabled' if not p['is_recorded'] else ''} onchange="changeCadence({pid}, this.value)">
                            {cad_options}
                        </select>
                    </td>
                    <td>
                        <button class="btn-icon-del" onclick="removePoint({pid})" title="Retirer du suivi">🗑️</button>
                    </td>
                </tr>
            """
            
        if not modbus_points:
            rows_html = "<tr><td colspan='8' style='text-align:center; padding:30px; color:#888;'>Aucun point sélectionné pour le suivi sur ce chantier.<br><a href='" + base_url + "/modbus/devices' class='btn-secondary btn-sm' style='margin-top:10px; display:inline-block;'>Sélectionner des points sur un appareil</a></td></tr>"
            
        content = render("trends.html", site_name=site_name, trends_rows_html=rows_html, base_url=base_url)
        nav_html = render("nav.html", base_url=base_url)
        final_html = render("layout.html", title="Suivi Global des Points", hostname=escape(hostname), base_url=escape(base_url), version=version, nav=nav_html, content=content)
        
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
                        formatted_ports.append(f"<a href='{base_url}/modbus/tools?ip={d['ip']}' style='color: #2ecc71; font-weight: bold;'>502 (Modbus)</a>{modbus_info}")
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
            addrs = data.get("reg_addr[]", [])
            funcs = data.get("reg_func[]", [])
            bases = data.get("reg_base[]", [])
            names = data.get("reg_name[]", [])
            types = data.get("reg_type[]", [])
            scales = data.get("reg_scale[]", [])
            units = data.get("reg_unit[]", [])
            
            if isinstance(addrs, str): 
                addrs, funcs, bases, names, types, scales, units = [addrs], [funcs], [bases], [names], [types], [scales], [units]
                
            for i in range(len(addrs)):
                if addrs[i] and names[i]: 
                    registers.append({
                        "reg": int(addrs[i]), 
                        "function": int(funcs[i]) if i < len(funcs) and funcs[i] else 3,
                        "base": int(bases[i]) if i < len(bases) and bases[i] else 0,
                        "name": names[i], 
                        "type": types[i] if i < len(types) else "int16",
                        "scale": float(scales[i]) if i < len(scales) and scales[i] else 1.0,
                        "unit": units[i] if i < len(units) and units[i] else ""
                    })
            is_shared = data.get("is_shared")
            save_template(name=data.get("name"), manufacturer=data.get("manufacturer"), registers=registers, template_id=data.get("template_id") or None, is_shared=is_shared)
            self.send_json({"status": "ok", "message": "Template enregistré"})
        except Exception as e: self.send_error(400, str(e))

    def handle_modbus_template_delete(self):
        content_length = int(self.headers['Content-Length'])
        post_data = self.rfile.read(content_length)
        try:
            data = json.loads(post_data)
            template_id = int(data.get("template_id"))
            from services.modbus_mgr import delete_template
            ok, err = delete_template(template_id)
            if ok:
                self.send_json({"status": "ok", "message": "Template supprimé avec succès"})
            else:
                self.send_json({"status": "error", "message": err or "Impossible de supprimer le template"})
        except Exception as e: self.send_error(400, str(e))

    def handle_modbus_template_import_from_fleet(self):
        content_length = int(self.headers['Content-Length'])
        post_data = self.rfile.read(content_length)
        try:
            data = json.loads(post_data)
            template_name = data.get("name")
            from services.modbus_mgr import import_template_from_fleet
            ok, err = import_template_from_fleet(template_name)
            if ok:
                self.send_json({"status": "ok", "message": f"Template '{template_name}' installé avec succès en local"})
            else:
                self.send_json({"status": "error", "message": err or "Impossible d'importer le template"})
        except Exception as e: self.send_error(400, str(e))

    def handle_modbus_template_share_to_fleet(self):
        content_length = int(self.headers['Content-Length'])
        post_data = self.rfile.read(content_length)
        try:
            data = json.loads(post_data)
            template_id = int(data.get("template_id"))
            from services.modbus_mgr import share_template_to_fleet
            ok, err = share_template_to_fleet(template_id)
            if ok:
                self.send_json({"status": "ok", "message": "Template partagé avec succès sur la flotte docs !"})
            else:
                self.send_json({"status": "error", "message": err or "Impossible de partager le template"})
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
            port_val = int(data.get("port", 502)) if data.get("port") else 502
            slave_unit_val = int(data.get("slave_unit") or data.get("slave_id") or 1)
            add_device_to_site(
                site_id=site_id,
                template_id=data.get("template_id"),
                name=data.get("name"),
                protocol=data.get("protocol"),
                address=data.get("address_ip") if data.get("protocol") == "tcp" else str(slave_unit_val),
                port=port_val,
                slave_unit=slave_unit_val
            )
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
