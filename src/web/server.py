import json
import logging
import os
import queue
import socket
import subprocess
import sys
import time
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
from urllib.parse import parse_qs, urlparse
from pathlib import Path

import paho.mqtt.client as mqtt

from core.config import load_config
from core.database import get_db_connection
from core.paths import STATIC_DIR
from services.fleet import fleet
from services.gsm import get_gsm_info
from services.ipscan import (is_ipscan_running, load_ipscan_results,
                             start_ip_scan_in_background)
from services.mqtt_service import mqtt_client
from web.stream import handle_sse_stream, handle_mqtt_stream
from web.templating import escape, render

logger = logging.getLogger(__name__)

class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True

class WebAdminHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        logger.debug(f"{self.address_string()} - - [{self.log_date_time_string()}] {format%args}")

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

        logger.debug(f"GET Request: {path} (original: {self.path})")
        
        if path == "/sw.js":
            return self.serve_static("/static/sw.js")
        if path == "/manifest.json":
            return self.serve_static("/static/manifest.json")
        if path.startswith("/static/"):
            return self.serve_static(path)
        if path == "/api/stream":
            return handle_sse_stream(self)
        if path == "/api/mqtt/stream":
            return handle_mqtt_stream(self, query)
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
        elif path == "/api/bacnet/catalog/status":
            return self.handle_bacnet_catalog_status()
        elif path == "/api/network/wifi/list":
            from services.wifi_mgr import get_visible_ssids
            return self.send_json(get_visible_ssids())
        elif path == "/api/devices":
            return self.handle_devices_api()
        elif path == "/api/devices/ports":
            return self.handle_devices_ports_api()
        elif path == "/api/bacnet/mstp/status":
            return self.handle_bacnet_mstp_status()
        elif path == "/api/bacnet/mstp/stream":
            from web.stream import handle_bacnet_mstp_stream
            return handle_bacnet_mstp_stream(self)
        elif path == "/api/monitor/logs":
            return self.handle_logs_api(query)
        elif path == "/api/monitor/logs/download":
            return self.handle_logs_download()

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
        elif path == "/api/modbus/suivi/values":
            return self.serve_modbus_suivi_values()
        elif path == "/api/bacnet/suivi/values":
            return self.serve_bacnet_suivi_values()
        elif path == "/api/monitor/suivi/values":
            return self.serve_monitor_suivi_values()
        elif path == "/scan/bacnet" or path == "/bacnet/devices":
            return self.serve_bacnet_devices(query)
        elif path == "/bacnet/suivi":
            return self.serve_bacnet_suivi()
        elif path == "/bacnet/device/view":
            return self.serve_bacnet_device_view(query)
        elif path == "/bacnet/templates":
            return self.serve_bacnet_templates()
        elif path == "/bacnet/tools":
            return self.serve_bacnet_tools(query)
        elif path == "/monitor/suivi":
            return self.serve_trends_view()
        elif path == "/monitor/system":
            return self.serve_system_status()
        elif path == "/monitor/logs":
            return self.serve_logs_view()
        elif path == "/configuration/logger":
            return self.serve_configuration_logger()
        elif path == "/configuration/mqtt":
            return self.serve_configuration_mqtt()
        elif path == "/scan/ip":
            return self.serve_ip_scan()
        elif path == "/devices" or path == "/storage/devices":
            return self.serve_devices()
            
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
        elif path == "/api/system/sync/test":
            self.handle_sync_test()
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
        elif path == "/api/bacnet/device/delete":
            self.handle_bacnet_device_delete()
        elif path == "/api/bacnet/device/points/save":
            self.handle_bacnet_device_points_save()
        elif path == "/api/bacnet/point/update":
            self.handle_bacnet_point_update()
        elif path == "/api/bacnet/point/delete":
            self.handle_bacnet_point_delete()
        elif path == "/api/bacnet/tools/read":
            self.handle_bacnet_tools_read()
        elif path == "/api/bacnet/template/save":
            self.handle_bacnet_template_save()
        elif path == "/api/bacnet/template/delete":
            self.handle_bacnet_template_delete()
        elif path == "/api/bacnet/template/import_from_fleet":
            self.handle_bacnet_template_import_from_fleet()
        elif path == "/api/bacnet/template/share_to_fleet":
            self.handle_bacnet_template_share_to_fleet()
        elif path == "/api/bacnet/tools/discover":
            self.handle_bacnet_tools_discover()
        elif path == "/api/bacnet/tools/whohas":
            self.handle_bacnet_tools_whohas()
        elif path == "/api/bacnet/mstp/start":
            self.handle_bacnet_mstp_start()
        elif path == "/api/bacnet/mstp/stop":
            self.handle_bacnet_mstp_stop()
        elif path == "/api/bacnet/catalog/build":
            self.handle_bacnet_catalog_build()
        elif path == "/api/bacnet/catalog/cancel":
            self.handle_bacnet_catalog_cancel()
        elif path == "/api/bacnet/catalog/search":
            self.handle_bacnet_catalog_search()
        elif path == "/api/bacnet/catalog/values":
            self.handle_bacnet_catalog_values()
        elif path == "/api/bacnet/points/track":
            self.handle_bacnet_points_track()
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
        elif path == "/api/scan/ip/delete":
            self.handle_ip_device_delete()
        elif path == "/api/scan/ip/purge_offline":
            self.handle_ip_scan_purge_offline()
        elif path == "/api/scan/ip/annotate":
            self.handle_ip_annotate()
        elif path == "/api/modbus/tools/probe":
            self.handle_modbus_probe()
        elif path == "/api/modbus/tools/read":
            self.handle_modbus_read()
        elif path == "/api/modbus/tools/write":
            return self.handle_modbus_write()
        elif path == "/api/configuration/logger/save":
            return self.handle_configuration_logger_save()
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
        
        from services.presence import get_current_site_name
        site_name = get_current_site_name()
        
        widget_cpu = render("widget.html", widget_id="cpu", title="Statut Système", data="Chargement...")
        widget_net = render("widget.html", widget_id="net", title="Réseau / Chantier", data=site_name)
        all_widgets = f"{widget_cpu}\n{widget_net}"
        
        nav_html = render("nav.html", base_url=base_url)
        content = render("home.html", user="Admin", widgets=all_widgets)
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
            dhcp_range = profile["dhcp_range"] if profile and "dhcp_range" in profile.keys() and profile["dhcp_range"] else ""
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
                    <span class="addr-icon">🌐</span>
                    <input type="text" name="addresses" value="{escape(addr)}" placeholder="Ex: 192.168.1.10/24" class="form-input font-mono">
                    <button type="button" class="btn-remove-addr" onclick="removeAddressRow(this)" title="Supprimer">✕</button>
                </div>
                """
            is_active = status.get("active", False)
            cable = status.get("cable", True)
            has_ip = status.get("has_ip", False)
            is_dhcp = status.get("dhcp", False)

            if not cable and iface == "eth0":
                status_text = "Câble débranché"
                status_class = "status-offline"
            elif is_active and has_ip:
                status_text = "En ligne"
                status_class = "status-online"
            elif is_dhcp and not has_ip and cable:
                status_text = "En attente DHCP"
                status_class = "status-offline"
            elif is_active:
                status_text = "En ligne"
                status_class = "status-online"
            else:
                status_text = "Inactif"
                status_class = "status-offline"

            return {
                f"{iface}_method_auto_selected": 'selected' if method == "auto" else '',
                f"{iface}_method_manual_selected": 'selected' if method == "manual" else '',
                f"{iface}_method_shared_selected": 'selected' if method == "shared" else '',
                f"{iface}_method": method,
                f"{iface}_manual_fields_display": 'block' if method in ("manual", "shared") else 'none',
                f"{iface}_dhcp_range_display": 'block' if method == "shared" else 'none',
                f"{iface}_addresses_rows": addresses_rows,
                f"{iface}_gateway": profile["gateway"] if profile and profile["gateway"] else "",
                f"{iface}_dhcp_range": escape(dhcp_range),
                f"{iface}_ssid": profile["ssid"] if profile and "ssid" in profile.keys() and profile["ssid"] else "",
                f"{iface}_psk": profile["psk"] if profile and "psk" in profile.keys() and profile["psk"] else "",
                f"{iface}_live_ip": escape(status.get("ip", "--")),
                f"{iface}_live_mac": escape(status.get("mac", "--")),
                f"{iface}_status_class": status_class,
                f"{iface}_status_text": status_text
            }
        eth0_profile = get_site_network_profile(site_id, "eth0") if site_id else None
        eth0_status = get_interface_status("eth0")
        eth0_ctx = get_iface_context("eth0", eth0_profile, eth0_status)
        wlan0_profile = get_site_network_profile(site_id, "wlan0") if site_id else None
        wlan0_status = get_interface_status("wlan0")
        wlan0_ctx = get_iface_context("wlan0", wlan0_profile, wlan0_status)
        context = {"site_name": site_name, "base_url": base_url, **eth0_ctx, **wlan0_ctx}
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
        from core.database import get_db_connection
        from services.presence import get_current_site_id, get_current_site_name
        site_name = get_current_site_name()
        site_id = get_current_site_id()

        target_ip = query.get("address", query.get("ip", [""]))[0]
        target_port = query.get("port", ["502"])[0]
        target_unit = query.get("unit", query.get("slave_unit", ["1"]))[0]

        discovered_ips_options = ""
        devices_map = {}

        if site_id:
            with get_db_connection() as conn:
                cursor = conn.cursor()

                # 1. Appareils Modbus configurés sur le chantier courant
                cursor.execute(
                    "SELECT name, protocol, address, port, slave_unit FROM modbus_devices WHERE site_id = ?",
                    (site_id,)
                )
                for dev in cursor.fetchall():
                    if dev["address"] and dev["protocol"] == "tcp":
                        ip = dev["address"]
                        if ip not in devices_map:
                            devices_map[ip] = {
                                "ip": ip,
                                "label": f"{ip} - {dev['name']}",
                                "unit": str(dev["slave_unit"] or 1),
                                "port": str(dev["port"] or 502),
                                "vendor": dev["name"]
                            }

                # 2. Appareils découverts lors du scan IP pour le chantier courant UNIQUEMENT
                query_sql = """
                    SELECT DISTINCT last_ip, vendor, modbus_info, annotations_json
                    FROM discovered_devices
                    WHERE site_id = ?
                      AND last_ip IS NOT NULL AND last_ip != ''
                      AND (last_ports LIKE '%502%' OR (modbus_info IS NOT NULL AND modbus_info != ''))
                    ORDER BY last_ip ASC
                """
                cursor.execute(query_sql, (site_id,))
                rows = cursor.fetchall()

                for r in rows:
                    ip = r["last_ip"]
                    if ip not in devices_map:
                        vendor = r["vendor"] or "Équipement"
                        modbus_info = r["modbus_info"] or ""
                        custom_name = ""
                        if r["annotations_json"]:
                            try:
                                ann = json.loads(r["annotations_json"])
                                custom_name = ann.get("Nom") or ann.get("Name") or ann.get("Description") or ""
                            except Exception:
                                pass

                        # Détection d'un premier Unit ID par défaut
                        first_unit = "1"
                        if modbus_info and "Units:" in modbus_info:
                            try:
                                u_part = modbus_info.split("Units:", 1)[1].strip()
                                first_u = [u.strip() for u in u_part.split(",") if u.strip().isdigit() and int(u.strip()) > 0]
                                if first_u:
                                    first_unit = first_u[0]
                            except Exception:
                                pass

                        label_desc = custom_name or vendor
                        label = f"{ip} ({label_desc})" if label_desc else ip
                        if modbus_info:
                            label += f" [{modbus_info}]"

                        devices_map[ip] = {
                            "ip": ip,
                            "label": label,
                            "unit": first_unit,
                            "port": "502",
                            "vendor": label_desc
                        }

        # Construction des options pour datalist
        for ip, info in devices_map.items():
            discovered_ips_options += f'<option value="{escape(ip)}">{escape(info["label"])}</option>\n'

        content = render(
            "modbus_tools.html",
            target_ip=escape(target_ip),
            target_port=escape(target_port),
            target_unit=escape(target_unit),
            discovered_ips_options=discovered_ips_options,
            site_name=escape(site_name),
            site_id=str(site_id or 0)
        )
        nav_html = render("nav.html", base_url=base_url)
        final_html = render("layout.html", title="Outils Modbus", hostname=escape(hostname), base_url=escape(base_url), version=version, nav=nav_html, content=content)

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(final_html.encode("utf-8"))

    def serve_bacnet_devices(self, query=None):
        config = load_config()
        base_url = config.get("base_url", "")
        hostname = socket.gethostname()
        version = str(int(time.time()))
        from services.bacnet_mgr import get_all_templates, get_site_devices
        from services.presence import get_current_site_name
        site_name = get_current_site_name()
        prefill_ip = query.get("ip", [""])[0] if query else ""
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
                cursor.execute(
                    "SELECT COUNT(*) as count_mon, SUM(is_recorded) as count_rec FROM bacnet_points WHERE device_id = ? AND is_monitored = 1",
                    (dev_id,)
                )
                stats = cursor.fetchone()
                mon_count = stats["count_mon"] or 0
                rec_count = stats["count_rec"] or 0
                total_monitored_site += mon_count
                total_recorded_site += rec_count

                total_tpl_objs = 0
                cursor.execute("SELECT objects_json FROM bacnet_templates WHERE id = ?", (d["template_id"],))
                tpl_row = cursor.fetchone()
                if tpl_row and tpl_row["objects_json"]:
                    try:
                        objs = json.loads(tpl_row["objects_json"])
                        total_tpl_objs = len(objs)
                    except Exception:
                        total_tpl_objs = 0

                manu = d.get('template_manufacturer') or 'Générique'
                instance_info = f"Inst: {d['device_instance']}" if d.get("device_instance") else "Non défini"

                cards_html.append(f"""
                <div class="device-card" id="device-card-{dev_id}">
                    <div class="device-card-header">
                        <div style="min-width: 0;">
                            <div style="display: flex; align-items: center; gap: 8px; flex-wrap: wrap;">
                                <h3 class="device-title">{escape(d['name'])}</h3>
                                <span class="badge-protocol proto-bacnet-ip">BACnet/IP</span>
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
                            <span class="addr-label">Cible :</span>
                            <span class="addr-value">{escape(d['network_address'])} ({instance_info})</span>
                        </div>
                        <div class="device-stats-grid">
                            <div class="stat-pill">
                                <span class="stat-icon">📊</span>
                                <div>
                                    <div class="stat-num">{mon_count} / {total_tpl_objs}</div>
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
                        <a href="{base_url}/bacnet/device/view?id={dev_id}" class="btn-primary btn-sm" style="flex: 1; text-align: center; text-decoration: none;">
                            ⚙️ Configurer les points
                        </a>
                        <a href="{base_url}/bacnet/tools?ip={escape(d['network_address'])}&instance={d['device_instance']}" class="btn-secondary btn-sm" title="Tester la liaison" style="text-decoration: none;">
                            🔍 Test
                        </a>
                    </div>
                </div>
                """)

        devices_html = "".join(cards_html)
        if not devices:
            devices_html = """
            <div class="empty-state">
                <div class="empty-icon">🔌</div>
                <h3>Aucun appareil BACnet</h3>
                <p>Ce chantier n'a pas encore d'appareil BACnet configuré.</p>
                <button class="btn-primary" onclick="showAddDeviceModal()" style="margin-top: 15px;">➕ Ajouter le premier appareil</button>
            </div>
            """

        options_html = "".join([f'<option value="{t["id"]}">{escape(t["name"])} ({escape(t["manufacturer"] or "Générique")}) - v{t.get("version", 1)}</option>' for t in templates])

        content = render(
            "bacnet_devices.html",
            site_name=site_name,
            devices_list_html=devices_html,
            templates_options_html=options_html,
            total_devices=len(devices),
            total_monitored=total_monitored_site,
            total_recorded=total_recorded_site,
            prefill_ip=escape(prefill_ip),
            base_url=base_url
        )
        nav_html = render("nav.html", base_url=base_url)
        final_html = render("layout.html", title="Appareils BACnet", hostname=escape(hostname), base_url=escape(base_url), version=version, nav=nav_html, content=content)
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(final_html.encode("utf-8"))

    def serve_bacnet_suivi(self):
        config = load_config()
        base_url = config.get("base_url", "")
        hostname = socket.gethostname()
        version = str(int(time.time()))
        from services.bacnet_mgr import get_site_bacnet_points
        from services.presence import get_current_site_name
        site_name = get_current_site_name()
        
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id FROM sites WHERE name = ?", (site_name,))
            site_row = cursor.fetchone()
            site_id = site_row["id"] if site_row else None
            
        points = get_site_bacnet_points(site_id, only_monitored=True) if site_id else []
        
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
            unit_display = f"<code>bacnet://{p['network_address']} (Inst: {p['device_instance']})</code>"
            
            rows_html += f"""
                <tr id="suivi-row-{pid}">
                    <td><strong>{escape(p['device_name'])}</strong><br><small style="color:#777;">{unit_display}</small></td>
                    <td><span class="badge-gray">{escape(p['object_id'])}</span></td>
                    <td><strong>{escape(p['name'] or p['object_id'])}</strong></td>
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
            rows_html = "<tr><td colspan='7' style='text-align:center; padding:30px; color:#888;'>Aucun point BACnet sélectionné pour le suivi sur ce chantier.<br><a href='" + base_url + "/bacnet/devices' class='btn-secondary btn-sm' style='margin-top:10px; display:inline-block;'>Sélectionner des points sur un appareil</a></td></tr>"
            
        content = render("bacnet_suivi.html", site_name=site_name, suivi_rows_html=rows_html, base_url=base_url)
        nav_html = render("nav.html", base_url=base_url)
        final_html = render("layout.html", title="Suivi BACnet (Live)", hostname=escape(hostname), base_url=escape(base_url), version=version, nav=nav_html, content=content)
        
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(final_html.encode("utf-8"))

    def serve_bacnet_suivi_values(self):
        from services.bacnet_mgr import read_site_monitored_points_live
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

    def serve_bacnet_device_view(self, query):
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
                SELECT d.*, t.name as template_name, t.objects_json
                FROM bacnet_devices d
                JOIN bacnet_templates t ON d.template_id = t.id
                WHERE d.id = ?
            """, (int(device_id),))
            device = cursor.fetchone()
            
        if not device:
            self.send_error(404, "Appareil non trouvé")
            return
            
        device = dict(device)
        objects = json.loads(device.get("objects_json") or "[]")
        
        from services.bacnet_mgr import get_device_points
        existing_points = get_device_points(int(device_id))
        monitored_keys = {p['object_id']: p for p in existing_points if p.get("is_monitored")}
        
        rows_html = ""
        for i, obj in enumerate(objects):
            obj_id = obj.get("obj", "")
            obj_name = obj.get("name", "") or obj_id
            
            is_mon = obj_id in monitored_keys
            chk_attr = "checked" if is_mon else ""
            
            data_attrs = f"data-obj='{escape(obj_id)}' data-name='{escape(obj_name)}'"
            
            rows_html += f"""
                <tr class="point-row" {data_attrs} id="row-{i}">
                    <td style="text-align:center;">
                        <input type="checkbox" class="cb-monitor" {chk_attr}>
                    </td>
                    <td><code>{escape(obj_id)}</code></td>
                    <td><strong>{escape(obj_name)}</strong></td>
                    <td><span class="live-val badge-gray" id="val-{i}">-</span></td>
                    <td>
                        <button class="btn-secondary btn-sm" onclick="readPoint({i})">Lire</button>
                    </td>
                </tr>
            """
            
        if not objects:
            rows_html = "<tr><td colspan='5' style='text-align:center; padding:20px; color:#888;'>Aucun objet défini dans le modèle de cet appareil.</td></tr>"
            
        device_json = json.dumps({
            "id": device["id"],
            "name": device["name"],
            "network_address": device["network_address"],
            "device_instance": device["device_instance"]
        })
        
        conn_display = f"{device['network_address']} (Instance {device['device_instance']})"
        
        content = render(
            "bacnet_device_view.html",
            device_name=escape(device["name"]),
            bacnet_template_name=escape(device["template_name"]),
            conn_display=escape(conn_display),
            rows_html=rows_html,
            device_json=device_json,
            base_url=base_url
        )
        nav_html = render("nav.html", base_url=base_url)
        final_html = render("layout.html", title=f"Points BACnet - {escape(device['name'])}", hostname=escape(hostname), base_url=escape(base_url), version=version, nav=nav_html, content=content)
        
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(final_html.encode("utf-8"))

    def handle_bacnet_device_points_save(self):
        content_length = int(self.headers.get('Content-Length', 0))
        post_data = self.rfile.read(content_length)
        try:
            data = json.loads(post_data)
            device_id = int(data.get("device_id"))
            points = data.get("points", [])
            
            from services.bacnet_mgr import save_device_points_selection
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
            logger.error(f"Erreur save bacnet device points: {e}")
            self.send_json({"status": "error", "message": str(e)})

    def handle_bacnet_point_update(self):
        content_length = int(self.headers.get('Content-Length', 0))
        post_data = self.rfile.read(content_length)
        try:
            data = json.loads(post_data)
            point_id = int(data.get("point_id"))
            is_monitored = data.get("is_monitored")
            is_recorded = data.get("is_recorded")
            cadence = data.get("cadence")
            
            from services.bacnet_mgr import update_point_settings
            success = update_point_settings(point_id, is_monitored=is_monitored, is_recorded=is_recorded, cadence=cadence)
            if success:
                self.send_json({"status": "ok"})
            else:
                self.send_json({"status": "error", "message": "Point introuvable"})
        except Exception as e:
            logger.error(f"Erreur update bacnet point: {e}")
            self.send_json({"status": "error", "message": str(e)})

    def handle_bacnet_point_delete(self):
        content_length = int(self.headers.get('Content-Length', 0))
        post_data = self.rfile.read(content_length)
        try:
            data = json.loads(post_data)
            point_id = int(data.get("point_id"))
            from services.bacnet_mgr import delete_bacnet_point
            delete_bacnet_point(point_id)
            self.send_json({"status": "ok"})
        except Exception as e:
            logger.error(f"Erreur delete bacnet point: {e}")
            self.send_json({"status": "error", "message": str(e)})

    def handle_bacnet_tools_read(self):
        content_length = int(self.headers.get('Content-Length', 0))
        post_data = self.rfile.read(content_length)
        try:
            data = json.loads(post_data)
            ip = data.get("address") or data.get("ip")
            obj_id = data.get("object_id") or data.get("obj")
            device_id = data.get("device_id") or data.get("device_instance")
            
            if not ip or not obj_id:
                return self.send_json({"status": "error", "message": "Adresse IP et Object ID requis"})
                
            job_id = str(uuid.uuid4())
            res_queue = queue.Queue()

            def on_msg(client, userdata, msg):
                try:
                    res_queue.put(json.loads(msg.payload.decode('utf-8')))
                except Exception:
                    pass

            try:
                temp_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
            except AttributeError:
                temp_client = mqtt.Client()
            temp_client.on_message = on_msg
            temp_client.connect("127.0.0.1", 1883, 60)
            temp_client.loop_start()
            temp_client.subscribe(f"rpinode/bacnet/res/read/{job_id}")

            mqtt_client.publish("rpinode/bacnet/cmd/read", {
                "job_id": job_id,
                "points": [{"address": ip, "object_id": obj_id, "device_id": device_id}]
            })

            try:
                results = res_queue.get(timeout=4.0)
                res_item = results[0] if results and isinstance(results, list) else {}
                if res_item.get("error"):
                    self.send_json({"status": "error", "message": res_item["error"]})
                else:
                    self.send_json({"status": "ok", "value": res_item.get("value")})
            except queue.Empty:
                self.send_json({"status": "error", "message": "Délai d'attente dépassé"})
            finally:
                temp_client.loop_stop()
                temp_client.disconnect()
        except Exception as e:
            self.send_json({"status": "error", "message": str(e)})

    def serve_bacnet_templates(self):
        config = load_config()
        base_url = config.get("base_url", "")
        hostname = socket.gethostname()
        version = str(int(time.time()))
        from services.bacnet_mgr import get_templates_overview

        local_templates, fleet_templates = get_templates_overview()

        local_html = ""
        for t in local_templates:
            t_json = json.dumps(dict(t)).replace("'", "\\'")
            escaped_name = t['name'].replace("'", "\\'")
            try:
                objs = json.loads(t.get("objects_json", "[]"))
                obj_count = len(objs)
            except Exception:
                obj_count = 0

            version_badge = f"<span style='background:#e8f4fd; color:#2980b9; padding:2px 6px; border-radius:10px; font-size:0.75em; font-weight:bold; margin-left:5px;'>v{t.get('version', 1)}</span>"
            if t.get('is_shared') == 1:
                status_badge = "<span style='background:#e8f8f5; color:#16a085; padding:2px 6px; border-radius:10px; font-size:0.75em; margin-left:4px;'>🌐 Partagé</span>"
            else:
                status_badge = "<span style='background:#fdf2e9; color:#d35400; padding:2px 6px; border-radius:10px; font-size:0.75em; margin-left:4px;'>🔒 Local</span>"

            local_html += (
                f"<tr data-name='{escape(t['name']).lower()}'>"
                f"<td><strong>{escape(t['name'])}</strong> {version_badge} {status_badge}</td>"
                f"<td>{escape(t['manufacturer']) if t['manufacturer'] else '—'}</td>"
                f"<td><span class='badge-count'>{obj_count} obj.</span></td>"
                f"<td style='display:flex; gap:6px; flex-wrap:wrap;'>"
                f"<button class='btn-blue btn-sm' onclick='showEditTemplateModal({t_json})' title='Modifier les objets'>✏️ Modifier</button>"
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
                    f"<span class='badge-installed' style='background:#fef9e7; color:#d4ac0d;'>⚠️ v{f['local_version']} ➤ v{f['version']}</span> "
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
                f"<td><span class='badge-count'>{f['objects_count']} obj.</span></td>"
                f"<td>{status_action}</td>"
                f"</tr>"
            )
        if not fleet_templates:
            fleet_html = "<tr><td colspan='4' style='text-align:center; color:#888; padding:25px;'>Bibliothèque de la flotte inaccessible (ou 0 template disponible).</td></tr>"

        content = render(
            "bacnet_templates.html",
            local_templates_html=local_html,
            local_count=len(local_templates),
            fleet_templates_html=fleet_html,
            fleet_count=len(fleet_templates),
            base_url=base_url
        )
        nav_html = render("nav.html", base_url=base_url)
        final_html = render("layout.html", title="Templates BACnet", hostname=escape(hostname), base_url=escape(base_url), version=version, nav=nav_html, content=content)
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(final_html.encode("utf-8"))

    def serve_bacnet_tools(self, query=None):
        config = load_config()
        base_url = config.get("base_url", "")
        hostname = socket.gethostname()
        version = str(int(time.time()))

        target_ip = query.get("ip", [""])[0] if query else ""
        target_instance = query.get("instance", [""])[0] if query else ""
        active_tab = query.get("tab", ["ip"])[0] if query else "ip"
        target_device = query.get("device", [""])[0] if query else ""

        from services.device_mgr import list_serial_ports
        from services.bacnet_mstp import mstp_available, get_mstp_snapshot

        serial_ports = list_serial_ports(include_modems=False)
        mstp_options_html = ""
        if serial_ports:
            for p in serial_ports:
                sel = "selected" if (target_device and target_device == p["path"]) or (not target_device and p.get("is_moxa")) else ""
                label = f"{p['description']} ({p['path']})"
                mstp_options_html += f"<option value='{escape(p['path'])}' {sel}>{escape(label)}</option>"
        else:
            mstp_options_html = "<option value='' disabled selected>Aucun port série détecté (branchez le Moxa)</option>"

        options_html = ""
        try:
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT last_ip, bacnet_instance, bacnet_name FROM discovered_devices WHERE bacnet_instance IS NOT NULL")
                for row in cursor.fetchall():
                    if row["last_ip"]:
                        options_html += f"<option value='{row['last_ip']}'>{row['bacnet_name'] or 'Automate BACnet'} (Inst: {row['bacnet_instance']})</option>"
        except Exception:
            pass

        tab_ip_active = "active" if active_tab != "mstp" else ""
        tab_mstp_active = "active" if active_tab == "mstp" else ""
        tab_ip_style = "display: block;" if active_tab != "mstp" else "display: none;"
        tab_mstp_style = "display: block;" if active_tab == "mstp" else "display: none;"

        content = render(
            "bacnet_tools.html",
            target_ip=escape(target_ip),
            target_instance=escape(target_instance),
            discovered_ips_options=options_html,
            active_tab=escape(active_tab),
            tab_ip_active=tab_ip_active,
            tab_mstp_active=tab_mstp_active,
            tab_ip_style=tab_ip_style,
            tab_mstp_style=tab_mstp_style,
            mstp_serial_options=mstp_options_html,
            base_url=base_url
        )
        nav_html = render("nav.html", base_url=base_url)
        final_html = render("layout.html", title="Outils BACnet", hostname=escape(hostname), base_url=escape(base_url), version=version, nav=nav_html, content=content)
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(final_html.encode("utf-8"))

    def serve_devices(self):
        config = load_config()
        base_url = config.get("base_url", "")
        hostname = socket.gethostname()
        version = str(int(time.time()))

        from services.device_mgr import list_system_devices
        sys_devices = list_system_devices()
        moxa_dev = sys_devices.get("moxa_device")

        # 1. Carte Moxa UPort 1150
        if sys_devices.get("moxa_connected") and moxa_dev:
            moxa_card_html = f"""
            <div class="moxa-hero-card">
                <div style="display: flex; justify-content: space-between; align-items: flex-start; flex-wrap: wrap; gap: 10px;">
                    <div>
                        <h3>⚡ {escape(moxa_dev.get('description', 'Passerelle Moxa UPort 1150'))}</h3>
                        <div class="moxa-badges">
                            <span class="moxa-badge moxa-badge-green">● Connecté & Opérationnel</span>
                            <span class="moxa-badge moxa-badge-blue">Pilote ti_usb_3410_5052 (RS-485 2 fils)</span>
                            <span class="moxa-badge moxa-badge-purple">BACnet MS/TP & Modbus RTU</span>
                        </div>
                    </div>
                </div>
                <div class="moxa-details-grid">
                    <div class="moxa-detail-item">
                        <div class="label">Port TTY / Périphérique</div>
                        <div class="value">{escape(moxa_dev.get('path', ''))}</div>
                    </div>
                    <div class="moxa-detail-item">
                        <div class="label">Identifiant persistant (by-id)</div>
                        <div class="value" style="font-size: 0.8rem;">{escape(moxa_dev.get('by_id_name', moxa_dev.get('path', '')))}</div>
                    </div>
                    <div class="moxa-detail-item">
                        <div class="label">Pilote Noyau</div>
                        <div class="value">{escape(moxa_dev.get('driver', 'ti_usb_3410_5052'))}</div>
                    </div>
                </div>
                <div class="moxa-actions">
                    <a href="{base_url}/bacnet/tools?tab=mstp&device={escape(moxa_dev.get('path', ''))}" class="btn-primary" style="text-decoration: none; background: #22c55e; border-color: #16a34a; display: inline-flex; align-items: center; gap: 6px;">
                        <span>🔌</span> Lancer la recherche BACnet MS/TP
                    </a>
                    <a href="{base_url}/modbus/tools?port={escape(moxa_dev.get('path', ''))}" class="btn-secondary" style="text-decoration: none; background: rgba(255,255,255,0.15); color: white; border-color: rgba(255,255,255,0.3); display: inline-flex; align-items: center; gap: 6px;">
                        <span>🔍</span> Outils Modbus RTU
                    </a>
                </div>
            </div>
            """
        else:
            moxa_card_html = f"""
            <div class="card" style="padding: 24px; text-align: center; background: #f8fafc; border: 2px dashed #cbd5e1;">
                <div style="font-size: 2.5rem; margin-bottom: 10px;">🔌</div>
                <h3 style="margin: 0 0 8px 0; color: #475569;">Aucune passerelle Moxa UPort détectée</h3>
                <p style="color: #64748b; max-width: 550px; margin: 0 auto 15px auto; font-size: 0.95rem;">
                    Branchez l'adaptateur Moxa UPort 1150 sur un port USB du Raspberry Pi pour activer la communication BACnet MS/TP et Modbus RTU sur bus RS-485.
                </p>
                <a href="{base_url}/devices" class="btn-secondary" style="text-decoration: none;"><span>🔄</span> Vérifier à nouveau</a>
            </div>
            """

        # 2. Table des ports série
        serial_ports = sys_devices.get("rs485_ports", []) + sys_devices.get("modem_ports", [])
        if serial_ports:
            serial_rows = ""
            for p in serial_ports:
                badge_color = "#22c55e" if p.get("is_moxa") else ("#3b82f6" if p.get("is_rs485") else "#64748b")
                caps = ", ".join(p.get("capabilities", [])) or "Série générique"
                serial_rows += f"""
                <tr>
                    <td><code>{escape(p.get('path', ''))}</code></td>
                    <td><small style="color: #64748b; font-family: monospace;">{escape(p.get('by_id_name', '—'))}</small></td>
                    <td><strong>{escape(p.get('description', ''))}</strong></td>
                    <td><code>{escape(p.get('driver', '—'))}</code></td>
                    <td><span style="font-size: 0.85rem; color: {badge_color}; font-weight: bold;">{escape(caps)}</span></td>
                    <td>
                        <div style="display: flex; gap: 6px;">
                            {f'<a href="{base_url}/bacnet/tools?tab=mstp&device={escape(p.get("path", ""))}" class="btn-secondary btn-sm" style="text-decoration: none;">BACnet MS/TP</a>' if p.get("is_rs485") else ''}
                            {f'<a href="{base_url}/modbus/tools?port={escape(p.get("path", ""))}" class="btn-secondary btn-sm" style="text-decoration: none;">Modbus</a>' if p.get("is_rs485") else ''}
                        </div>
                    </td>
                </tr>
                """
            serial_ports_table_html = f"""
            <div style="overflow-x: auto;">
                <table class="data-table">
                    <thead>
                        <tr>
                            <th style="width: 140px;">Port TTY</th>
                            <th>Identifiant (by-id)</th>
                            <th>Description</th>
                            <th style="width: 150px;">Pilote</th>
                            <th style="width: 180px;">Capacités</th>
                            <th style="width: 180px;">Actions</th>
                        </tr>
                    </thead>
                    <tbody>
                        {serial_rows}
                    </tbody>
                </table>
            </div>
            """
        else:
            serial_ports_table_html = "<p style='color: #64748b; padding: 15px;'>Aucun port série détecté.</p>"

        # 3. Lignes périphériques USB
        usb_rows = ""
        for u in sys_devices.get("usb_devices", []):
            usb_rows += f"""
            <tr>
                <td><code>Bus {u.get('bus')} / Dev {u.get('device')}</code></td>
                <td><code>{escape(u.get('vendor_id', ''))}:{escape(u.get('product_id', ''))}</code></td>
                <td><strong>{escape(u.get('description', ''))}</strong></td>
                <td><span class="badge-gray">{escape(u.get('category', ''))}</span></td>
                <td><code>{escape(u.get('driver_str', 'Aucun'))}</code></td>
            </tr>
            """
        if not usb_rows:
            usb_rows = "<tr><td colspan='5' style='text-align: center; color: #64748b; padding: 15px;'>Aucun périphérique USB listé.</td></tr>"

        content = render(
            "devices.html",
            base_url=base_url,
            moxa_card_html=moxa_card_html,
            serial_ports_table_html=serial_ports_table_html,
            usb_devices_rows_html=usb_rows,
            total_serial=sys_devices.get("total_serial", 0),
            total_usb=sys_devices.get("total_usb", 0),
        )
        nav_html = render("nav.html", base_url=base_url)
        final_html = render("layout.html", title="Périphériques & Passerelles", hostname=escape(hostname), base_url=escape(base_url), version=version, nav=nav_html, content=content)

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(final_html.encode("utf-8"))

    def handle_devices_api(self):
        from services.device_mgr import list_system_devices
        self.send_json(list_system_devices())

    def handle_devices_ports_api(self):
        from services.device_mgr import list_serial_ports
        self.send_json(list_serial_ports())

    def handle_bacnet_mstp_status(self):
        from services.bacnet_mstp import get_mstp_snapshot
        self.send_json(get_mstp_snapshot())

    def handle_bacnet_mstp_start(self):
        content_length = int(self.headers.get('Content-Length', 0))
        post_data = self.rfile.read(content_length)
        try:
            data = json.loads(post_data) if post_data else {}
            from services.bacnet_mstp import start_mstp_session, get_mstp_snapshot
            ok, msg = start_mstp_session(data)
            if ok:
                self.send_json({"status": "ok", "message": msg, "snapshot": get_mstp_snapshot()})
            else:
                self.send_json({"status": "error", "message": msg}, status_code=400)
        except Exception as e:
            self.send_json({"status": "error", "message": str(e)}, status_code=500)

    def handle_bacnet_mstp_stop(self):
        try:
            from services.bacnet_mstp import stop_mstp_session, get_mstp_snapshot
            ok, msg = stop_mstp_session()
            self.send_json({"status": "ok", "message": msg, "snapshot": get_mstp_snapshot()})
        except Exception as e:
            self.send_json({"status": "error", "message": str(e)}, status_code=500)

    def handle_bacnet_tools_discover(self):
        content_length = int(self.headers.get('Content-Length', 0))
        post_data = self.rfile.read(content_length)
        try:
            data = json.loads(post_data)
            ip = data.get("ip")
            device_instance = data.get("device_instance")

            if not ip or not device_instance:
                raise ValueError("IP ou Device Instance manquants")

            job_id = str(uuid.uuid4())
            res_queue = queue.Queue()

            def on_msg(client, userdata, msg):
                try:
                    res_queue.put(json.loads(msg.payload.decode('utf-8')))
                except Exception:
                    pass

            try:
                temp_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
            except AttributeError:
                temp_client = mqtt.Client()
            temp_client.on_message = on_msg
            temp_client.connect("127.0.0.1", 1883, 60)
            temp_client.loop_start()
            temp_client.subscribe(f"rpinode/bacnet/res/discover/{job_id}")

            mqtt_client.publish("rpinode/bacnet/cmd/discover", {
                "job_id": job_id, "ip": ip, "device_instance": device_instance
            })

            try:
                result = res_queue.get(timeout=15.0)
                if result.get("status") == "ok" and result.get("objects"):
                    try:
                        from services.bacnet_catalog import upsert_device_points
                        from services.presence import get_current_site_id
                        site_id = get_current_site_id()
                        if site_id:
                            upsert_device_points(site_id, ip, device_instance, result["objects"])
                    except Exception as e:
                        logger.debug(f"Mise à jour du dictionnaire BACnet ignorée : {e}")
                self.send_json(result)
            except queue.Empty:
                self.send_json({"status": "error", "message": "Délai d'attente dépassé (aucune réponse de l'équipement BACnet)"})
            finally:
                temp_client.loop_stop()
                temp_client.disconnect()
        except Exception as e:
            self.send_json({"status": "error", "message": str(e)})

    def handle_bacnet_catalog_status(self):
        from services.bacnet_catalog import get_status
        self.send_json(get_status())

    def handle_bacnet_catalog_build(self):
        content_length = int(self.headers.get('Content-Length', 0))
        post_data = self.rfile.read(content_length) if content_length else b"{}"
        try:
            data = json.loads(post_data) if post_data else {}
            when = data.get("when")  # None = immédiat, sinon chaîne ISO datetime
            from services.bacnet_catalog import schedule_build
            scheduled_at = schedule_build(when)
            self.send_json({"status": "ok", "scheduled_at": scheduled_at})
        except Exception as e:
            self.send_json({"status": "error", "message": str(e)})

    def handle_bacnet_catalog_cancel(self):
        try:
            from services.bacnet_catalog import cancel_build
            cancel_build()
            self.send_json({"status": "ok"})
        except Exception as e:
            self.send_json({"status": "error", "message": str(e)})

    def handle_bacnet_catalog_search(self):
        content_length = int(self.headers.get('Content-Length', 0))
        post_data = self.rfile.read(content_length)
        try:
            data = json.loads(post_data)
            pattern = (data.get("pattern") or "").strip()
            if not pattern:
                raise ValueError("Motif de recherche manquant")

            page = max(1, int(data.get("page", 1)))
            limit = max(1, min(500, int(data.get("limit", 100))))
            offset = (page - 1) * limit

            from services.bacnet_catalog import search_points, count_search_points
            from services.presence import get_current_site_id, get_current_site_name
            site_id = get_current_site_id()
            if not site_id:
                site_name = get_current_site_name()
                with get_db_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute("SELECT id FROM sites WHERE name = ?", (site_name,))
                    row = cursor.fetchone()
                    site_id = row["id"] if row else None

            total_count = count_search_points(pattern, site_id=site_id) if site_id else 0
            matches = search_points(pattern, site_id=site_id, limit=limit, offset=offset) if site_id else []

            total_pages = max(1, (total_count + limit - 1) // limit) if total_count > 0 else 1

            objects = [
                {
                    "device_id": m["device_instance"],
                    "device_name": m.get("device_name", "") or "",
                    "address": m["network_address"],
                    "object_id": m["object_id"],
                    "object_name": m["object_name"],
                    "is_monitored": bool(m.get("is_monitored", 0)),
                    "value": None
                }
                for m in matches
            ]

            self.send_json({
                "status": "ok",
                "objects": objects,
                "page": page,
                "limit": limit,
                "total_count": total_count,
                "total_pages": total_pages,
                "has_next": page < total_pages,
                "has_prev": page > 1,
                "truncated": total_count > limit
            })
        except Exception as e:
            self.send_json({"status": "error", "message": str(e)})

    def handle_bacnet_catalog_values(self):
        content_length = int(self.headers.get('Content-Length', 0))
        post_data = self.rfile.read(content_length)
        try:
            data = json.loads(post_data)
            points = data.get("points") or []
            if not points:
                self.send_json({"status": "ok", "values": {}})
                return

            # Limite de sécurité à 100 points
            points = points[:100]

            from services.bacnet_mgr import read_bacnet_points_live_raw
            values = read_bacnet_points_live_raw(points, timeout=4.0)

            self.send_json({"status": "ok", "values": values})
        except Exception as e:
            self.send_json({"status": "error", "message": str(e)})

    def handle_bacnet_points_track(self):
        content_length = int(self.headers.get('Content-Length', 0))
        post_data = self.rfile.read(content_length)
        try:
            data = json.loads(post_data)
            points = data.get("points", [])
            if not points:
                return self.send_json({"status": "error", "message": "Aucun point sélectionné"})

            from services.presence import get_current_site_id, get_current_site_name
            site_id = get_current_site_id()
            if not site_id:
                site_name = get_current_site_name()
                with get_db_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute("SELECT id FROM sites WHERE name = ?", (site_name,))
                    row = cursor.fetchone()
                    site_id = row["id"] if row else None

            if not site_id:
                return self.send_json({"status": "error", "message": "Aucun chantier actif"})

            from services.bacnet_mgr import add_points_to_suivi
            count = add_points_to_suivi(site_id, points)
            self.send_json({
                "status": "ok",
                "count": count,
                "message": f"{count} point(s) marqué(s) comme suivi avec succès"
            })
        except Exception as e:
            logger.error(f"Erreur track bacnet points: {e}")
            self.send_json({"status": "error", "message": str(e)})

    def handle_bacnet_tools_whohas(self):
        content_length = int(self.headers.get('Content-Length', 0))
        post_data = self.rfile.read(content_length)
        try:
            data = json.loads(post_data)
            object_name = data.get("object_name")
            if not object_name:
                raise ValueError("Nom de l'objet manquant")

            job_id = str(uuid.uuid4())
            res_queue = queue.Queue()

            def on_msg(client, userdata, msg):
                try:
                    res_queue.put(json.loads(msg.payload.decode('utf-8')))
                except Exception:
                    pass

            try:
                temp_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
            except AttributeError:
                temp_client = mqtt.Client()
            temp_client.on_message = on_msg
            temp_client.connect("127.0.0.1", 1883, 60)
            temp_client.loop_start()
            temp_client.subscribe(f"rpinode/bacnet/res/whohas/{job_id}")

            mqtt_client.publish("rpinode/bacnet/cmd/whohas", {
                "job_id": job_id, "object_name": object_name
            })

            try:
                result = res_queue.get(timeout=10.0)
                self.send_json({"status": "ok", "objects": result})
            except queue.Empty:
                self.send_json({"status": "error", "message": "Délai d'attente dépassé (aucune réponse I-Have)"})
            finally:
                temp_client.loop_stop()
                temp_client.disconnect()
        except Exception as e:
            self.send_json({"status": "error", "message": str(e)})

    def serve_trends_view(self):
        config = load_config()
        base_url = config.get("base_url", "")
        hostname = socket.gethostname()
        version = str(int(time.time()))
        from services.modbus_mgr import get_site_modbus_points
        from services.bacnet_mgr import get_site_bacnet_points
        from services.presence import get_current_site_name
        site_name = get_current_site_name()
        
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id FROM sites WHERE name = ?", (site_name,))
            site_row = cursor.fetchone()
            site_id = site_row["id"] if site_row else None
            
        modbus_points = get_site_modbus_points(site_id, only_monitored=True) if site_id else []
        bacnet_points = get_site_bacnet_points(site_id, only_monitored=True) if site_id else []
        
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
            
        for p in bacnet_points:
            pid = p["id"]
            rec_checked = "checked" if p["is_recorded"] else ""
            cad_options = "".join([
                f"<option value='{c}' {'selected' if p.get('cadence') == c else ''}>{c}</option>"
                for c in cadences
            ])
            val_display = p["last_value"] if p["last_value"] is not None else "—"
            unit_display = f"<code>bacnet://{p['network_address']} (Inst: {p['device_instance']})</code>"
            
            rows_html += f"""
                <tr id="suivi-row-bac-{pid}" data-proto="bacnet-ip">
                    <td><span class="badge-protocol proto-bacnet-ip">BACNET/IP</span></td>
                    <td><strong>{escape(p['device_name'])}</strong><br><small style="color:#777;">{unit_display}</small></td>
                    <td><span class="badge-gray">{escape(p['object_id'])}</span></td>
                    <td><strong>{escape(p['name'] or p['object_id'])}</strong></td>
                    <td><b class="live-val badge-gray" id="val-bac-{pid}" data-suivi-key="bac-{pid}">{escape(val_display)}</b></td>
                    <td>
                        <label style="cursor:pointer; display:inline-flex; align-items:center; gap:5px;">
                            <input type="checkbox" class="cb-record" data-point-id="{pid}" {rec_checked} onchange="toggleRecord({pid}, this.checked, 'bacnet')">
                            <span>Enregistrer</span>
                        </label>
                    </td>
                    <td>
                        <select id="cadence-bac-{pid}" class="cadence-select" {'disabled' if not p['is_recorded'] else ''} onchange="changeCadence({pid}, this.value, 'bacnet')">
                            {cad_options}
                        </select>
                    </td>
                    <td>
                        <button class="btn-icon-del" onclick="removePoint({pid}, 'bacnet')" title="Retirer du suivi">🗑️</button>
                    </td>
                </tr>
            """
            
        if not modbus_points and not bacnet_points:
            rows_html = "<tr><td colspan='8' style='text-align:center; padding:30px; color:#888;'>Aucun point sélectionné pour le suivi sur ce chantier.<br><a href='" + base_url + "/modbus/devices' class='btn-secondary btn-sm' style='margin-top:10px; display:inline-block; margin-right: 8px;'>Appareils Modbus</a><a href='" + base_url + "/bacnet/devices' class='btn-secondary btn-sm' style='margin-top:10px; display:inline-block;'>Appareils BACnet</a></td></tr>"
            
        content = render("trends.html", site_name=site_name, trends_rows_html=rows_html, base_url=base_url)
        nav_html = render("nav.html", base_url=base_url)
        final_html = render("layout.html", title="Suivi Global des Points", hostname=escape(hostname), base_url=escape(base_url), version=version, nav=nav_html, content=content)
        
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(final_html.encode("utf-8"))

    def serve_monitor_suivi_values(self):
        from services.modbus_mgr import read_site_monitored_points_live as read_modbus_live
        from services.bacnet_mgr import read_site_monitored_points_live as read_bacnet_live
        from services.presence import get_current_site_name
        site_name = get_current_site_name()
        
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id FROM sites WHERE name = ?", (site_name,))
            row = cursor.fetchone()
            site_id = row["id"] if row else None
            
        if not site_id:
            return self.send_json({"status": "error", "message": "Aucun chantier actif", "values": {}})
            
        mb_values = read_modbus_live(site_id)
        bac_values = read_bacnet_live(site_id)
        combined = {}
        for k, v in mb_values.items():
            combined[k] = v
        for k, v in bac_values.items():
            combined[f"bac-{k}"] = v
            combined[k] = v
        self.send_json({"status": "ok", "values": combined})

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
                        inst_param = ""
                        if d.get("bacnet_instance"): 
                            bacnet_info.append(f"Inst: {d['bacnet_instance']}")
                            inst_param = f"&instance={d['bacnet_instance']}"
                        if d.get("bacnet_name") and d.get("bacnet_name") != "Automate BACnet": bacnet_info.append(d["bacnet_name"])
                        info_str = f" <small style='color:#e67e22'>({', '.join(bacnet_info)})</small>" if bacnet_info else ""
                        formatted_ports.append(f"<a href='{base_url}/bacnet/tools?ip={d['ip']}{inst_param}' style='color: #e67e22; font-weight: bold;'>47808 (BACnet)</a>{info_str}")
                    elif p == 80: formatted_ports.append(f"<a href='http://{d['ip']}' target='_blank' style='color: #3498db;'>80</a>")
                    elif p == 443: formatted_ports.append(f"<a href='https://{d['ip']}' target='_blank' style='color: #9b59b6;'>443</a>")
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
                    custom_cells += f'<td class="editable" onclick="editCell(event, \'{mac}\', \'{col["column_key"]}\', \'{col["column_label"]}\')">{escape(str(val))}</td>'
                sync_indicator = "<span class='sync-pending' title='En attente de synchronisation'>☁️</span>" if is_dirty else ""
                delete_btn = f"<button class='btn-icon-sm' title='Supprimer de l\\'inventaire' onclick=\"deleteDevice('{mac}', '{escape(d.get('ip', ''))}')\">🗑️</button>"
                devices_rows += f"<tr class='{row_class}' id='device-row-{mac.replace(':', '')}'><td style='font-family: monospace; {ip_style}'>{status_dot}{escape(d.get('ip'))}</td><td style='font-family: monospace; font-size: 0.85em; color: #666;'>{escape(mac)}</td><td class='editable' onclick=\"editVendor('{mac}', '{escape(vendor)}')\">{escape(vendor)} {sync_indicator}</td><td>{ports_str}</td>{custom_cells}<td><small style='color:#999;'>{escape(d.get('iface'))}</small></td><td style='text-align:center;'>{delete_btn}</td></tr>"
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
            save_template(name=data.get("name"), manufacturer=data.get("manufacturer"), objects=objects, template_id=data.get("template_id") or None, is_shared=data.get("is_shared"))
            self.send_json({"status": "ok", "message": "Template BACnet enregistré"})
        except Exception as e: self.send_error(400, str(e))

    def handle_bacnet_device_delete(self):
        content_length = int(self.headers.get('Content-Length', 0))
        post_data = self.rfile.read(content_length)
        try:
            data = json.loads(post_data)
            device_id = int(data.get("device_id"))
            from services.bacnet_mgr import delete_device_from_site
            delete_device_from_site(device_id)
            self.send_json({"status": "ok", "message": "Appareil BACnet supprimé"})
        except Exception as e:
            logger.error(f"Erreur delete device BACnet: {e}")
            self.send_json({"status": "error", "message": str(e)})

    def handle_bacnet_template_delete(self):
        content_length = int(self.headers.get('Content-Length', 0))
        post_data = self.rfile.read(content_length)
        try:
            data = json.loads(post_data)
            template_id = int(data.get("template_id"))
            from services.bacnet_mgr import delete_template
            ok, err = delete_template(template_id)
            if ok:
                self.send_json({"status": "ok", "message": "Template supprimé avec succès"})
            else:
                self.send_json({"status": "error", "message": err or "Impossible de supprimer le template"})
        except Exception as e: self.send_error(400, str(e))

    def handle_bacnet_template_import_from_fleet(self):
        content_length = int(self.headers.get('Content-Length', 0))
        post_data = self.rfile.read(content_length)
        try:
            data = json.loads(post_data)
            template_name = data.get("name")
            from services.bacnet_mgr import import_template_from_fleet
            ok, err = import_template_from_fleet(template_name)
            if ok:
                self.send_json({"status": "ok", "message": f"Template '{template_name}' installé avec succès en local"})
            else:
                self.send_json({"status": "error", "message": err or "Impossible d'importer le template"})
        except Exception as e: self.send_error(400, str(e))

    def handle_bacnet_template_share_to_fleet(self):
        content_length = int(self.headers.get('Content-Length', 0))
        post_data = self.rfile.read(content_length)
        try:
            data = json.loads(post_data)
            template_id = int(data.get("template_id"))
            from services.bacnet_mgr import share_template_to_fleet
            ok, err = share_template_to_fleet(template_id)
            if ok:
                self.send_json({"status": "ok", "message": "Template partagé avec succès sur la flotte docs !"})
            else:
                self.send_json({"status": "error", "message": err or "Impossible de partager le template"})
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

    def handle_sync_test(self):
        try:
            from services.fleet import fleet
            from services.gsm import get_gsm_info
            gsm_info = get_gsm_info()
            res = fleet.sync_location(gsm_info)
            if res is not None:
                self.send_json({"status": "ok", "message": "Données synchronisées avec succès auprès de docs.deltathermic.be"})
            else:
                self.send_json({"status": "error", "message": "Le serveur distant n'a pas répondu ou a rejeté la synchronisation."})
        except Exception as e:
            logger.error(f"Erreur test sync: {e}")
            self.send_json({"status": "error", "message": str(e)})

    def serve_system_status(self):
        import shutil
        from core.sys import get_sys, ping_check
        from core.paths import LOG_FILE
        from services.presence import get_current_site_name
        from services.network import get_interface_status, get_tailscale_status
        from services.gsm import get_gsm_info
        from services.fleet import fleet

        config = load_config()
        base_url = config.get("base_url", "")
        hostname = socket.gethostname()
        version = str(int(time.time()))
        site_name = get_current_site_name()

        # 1. Ressources matérielles
        cpu_temp_val = float(get_sys("cpu_temp") if get_sys("cpu_temp") != "N/A" else 0)
        cpu_temp_class = "temp-hot" if cpu_temp_val > 75 else ("temp-warm" if cpu_temp_val > 60 else "temp-normal")
        uptime = get_sys("uptime")

        # RAM
        total_ram = used_ram = ram_percent = 0
        try:
            with open("/proc/meminfo") as f:
                mem = {}
                for line in f:
                    p = line.split(":")
                    if len(p) == 2:
                        mem[p[0].strip()] = int(p[1].split()[0])
            total_ram = mem.get("MemTotal", 0) // 1024
            avail_ram = mem.get("MemAvailable", 0) // 1024
            used_ram = total_ram - avail_ram
            ram_percent = int((used_ram / total_ram) * 100) if total_ram else 0
        except Exception:
            pass

        # Disque
        try:
            d_total, d_used, d_free = shutil.disk_usage("/")
            disk_total_go = d_total // (2**30)
            disk_used_go = d_used // (2**30)
            disk_percent = int((d_used / d_total) * 100) if d_total else 0
        except Exception:
            disk_total_go = disk_used_go = disk_percent = 0

        # 2. WAN & Réseaux
        wwan_status = get_interface_status("wwan0")
        wlan_status = get_interface_status("wlan0")
        ts_status = get_tailscale_status()
        gsm_info = get_gsm_info()
        has_internet = ping_check(target="8.8.8.8", timeout=2)

        wan_is_up = wwan_status.get("active", False)
        wan_card_class = "card-ok" if (wan_is_up and has_internet) else ("card-warning" if wan_is_up else "card-danger")
        wan_badge_class = "badge-ok" if (wan_is_up and has_internet) else ("badge-warning" if wan_is_up else "badge-danger")
        wan_icon_class = "icon-green" if (wan_is_up and has_internet) else ("icon-orange" if wan_is_up else "icon-red")
        wan_status_label = "Connecté (4G)" if (wan_is_up and has_internet) else ("Sans Internet" if wan_is_up else "Hors ligne (4G)")
        wan_ip_display = wwan_status.get("ip", "--")
        wan_ping_class = "text-ok" if has_internet else "text-danger"
        wan_ping_label = "Opérationnel (OK)" if has_internet else "Inaccessible (Échec)"

        gsm_cell_desc = f"MCC:{gsm_info.get('mcc') or '--'} MNC:{gsm_info.get('mnc') or '--'}"
        if gsm_info.get("enodeb"):
            gsm_cell_desc += f" | eNodeB:{gsm_info['enodeb']} (Secteur {gsm_info.get('sector', '--')})"

        # 3. Synchronisation & Serveur Central
        fleet_url = fleet.base_url
        fleet_reg_status = "Enregistré (Jeton valide)" if fleet.is_registered() else "Non enregistré (Sans jeton)"

        # Test de liaison à docs.deltathermic.be
        sync_ok = False
        sync_diag_msg = "En cours de vérification..."
        sync_diag_class = "text-warning"
        try:
            import requests
            probe_resp = requests.get(f"{fleet_url}/chantiers", headers=fleet._headers(), timeout=3)
            if probe_resp.status_code == 200:
                sync_ok = True
                sync_diag_msg = "Connecté & Opérationnel (HTTP 200 OK)"
                sync_diag_class = "text-ok"
            elif probe_resp.status_code == 401:
                sync_diag_msg = "Jeton d'authentification invalide ou expiré (401)"
                sync_diag_class = "text-danger"
            else:
                sync_diag_msg = f"Réponse serveur en anomalie (Code {probe_resp.status_code})"
                sync_diag_class = "text-warning"
        except requests.exceptions.ConnectionError as ce:
            err_str = str(ce)
            if "Network is unreachable" in err_str:
                sync_diag_msg = "Liaison 4G/WAN coupée : Réseau inaccessible"
            elif "Name or service not known" in err_str or "Failed to resolve" in err_str:
                sync_diag_msg = "Échec de résolution DNS (docs.deltathermic.be)"
            else:
                sync_diag_msg = f"Connexion impossible ({err_str[:60]})"
            sync_diag_class = "text-danger"
        except requests.exceptions.Timeout:
            sync_diag_msg = "Délai d'attente dépassé (Timeout sur docs.deltathermic.be)"
            sync_diag_class = "text-danger"
        except Exception as e:
            sync_diag_msg = f"Erreur : {str(e)[:60]}"
            sync_diag_class = "text-danger"

        sync_card_class = "card-ok" if sync_ok else "card-danger"
        sync_badge_class = "badge-ok" if sync_ok else "badge-danger"
        sync_icon_class = "icon-green" if sync_ok else "icon-red"
        sync_status_label = "Synchronisé" if sync_ok else "Erreur de Synchro"

        # Derniers logs
        sync_logs_list = []
        if LOG_FILE.exists():
            try:
                with open(LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
                    lines = f.readlines()
                for line in reversed(lines):
                    if any(k in line for k in ("services.fleet", "sync", "reports/api", "wwan0", "4G", "relevés synchronisés")):
                        sync_logs_list.append(line.strip())
                        if len(sync_logs_list) >= 5:
                            break
                sync_logs_list.reverse()
            except Exception:
                pass

        sync_recent_logs_html = escape("\n".join(sync_logs_list)) if sync_logs_list else "Aucune anomalie récente dans les journaux."

        wlan_mode = "Point d'Accès AP" if wlan_status.get("is_dhcp_server") else "Client"
        wlan_desc = f"{wlan_status.get('ip', '--')} ({wlan_mode})"

        content = render(
            "system_status.html",
            site_name=escape(site_name),
            hostname=escape(hostname),
            uptime=escape(uptime),
            cpu_temp=f"{cpu_temp_val:.1f}",
            cpu_temp_class=cpu_temp_class,
            ram_usage=f"{used_ram} Mo / {total_ram} Mo",
            ram_percent=str(ram_percent),
            disk_usage=f"{disk_used_go} Go / {disk_total_go} Go",
            disk_percent=str(disk_percent),
            wan_card_class=wan_card_class,
            wan_badge_class=wan_badge_class,
            wan_icon_class=wan_icon_class,
            wan_status_label=wan_status_label,
            wan_ip=escape(wan_ip_display),
            wan_ping_class=wan_ping_class,
            wan_ping_label=wan_ping_label,
            gsm_cell_desc=escape(gsm_cell_desc),
            ts_ip=escape(ts_status.get("ip", "--")),
            ts_status=escape(ts_status.get("status", "--")),
            wlan_desc=escape(wlan_desc),
            fleet_url=escape(fleet_url),
            fleet_reg_status=escape(fleet_reg_status),
            sync_card_class=sync_card_class,
            sync_badge_class=sync_badge_class,
            sync_icon_class=sync_icon_class,
            sync_status_label=sync_status_label,
            sync_diag_msg=escape(sync_diag_msg),
            sync_diag_class=sync_diag_class,
            sync_last_time=time.strftime("%Y-%m-%d %H:%M:%S"),
            sync_recent_logs=sync_recent_logs_html
        )
        nav_html = render("nav.html", base_url=base_url)
        final_html = render("layout.html", title="État Système & Synchronisation", hostname=escape(hostname), base_url=escape(base_url), version=version, nav=nav_html, content=content)

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(final_html.encode("utf-8"))

    def serve_logs_view(self):
        import os
        from core.paths import LOG_FILE
        from services.presence import get_current_site_name

        config = load_config()
        base_url = config.get("base_url", "")
        hostname = socket.gethostname()
        version = str(int(time.time()))
        site_name = get_current_site_name()

        log_file_size = "0 Ko"
        if LOG_FILE.exists():
            try:
                sz = os.path.getsize(LOG_FILE)
                if sz >= 1024 * 1024:
                    log_file_size = f"{sz / (1024 * 1024):.1f} Mo"
                else:
                    log_file_size = f"{sz / 1024:.0f} Ko"
            except Exception:
                pass

        content = render(
            "logs_view.html",
            site_name=escape(site_name),
            hostname=escape(hostname),
            base_url=escape(base_url),
            log_file_size=escape(log_file_size)
        )
        nav_html = render("nav.html", base_url=base_url)
        final_html = render("layout.html", title="Journaux d'activité (Logs)", hostname=escape(hostname), base_url=escape(base_url), version=version, nav=nav_html, content=content)

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(final_html.encode("utf-8"))

    def serve_configuration_logger(self):
        config = load_config()
        base_url = config.get("base_url", "")
        hostname = socket.gethostname()
        version = config.get("version", str(int(time.time())))
        
        logger_retries = config.get("logger_retries", 3)
        modbus_timeout = config.get("modbus_timeout", 1.2)
        bacnet_timeout = config.get("bacnet_timeout", 45)
        
        nav_html = render("nav.html", base_url=base_url)
        content = render("configuration_logger.html", 
                         logger_retries=logger_retries,
                         modbus_timeout=modbus_timeout,
                         bacnet_timeout=bacnet_timeout)
        final_html = render("layout.html", title="Configuration Enregistreurs", hostname=escape(hostname), base_url=escape(base_url), version=version, nav=nav_html, content=content)
        
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(final_html.encode("utf-8"))

    def serve_configuration_mqtt(self):
        config = load_config()
        base_url = config.get("base_url", "")
        hostname = socket.gethostname()
        version = config.get("version", str(int(time.time())))
        
        nav_html = render("nav.html", base_url=base_url)
        content = render("configuration_mqtt.html")
        final_html = render("layout.html", title="Moniteur MQTT", hostname=escape(hostname), base_url=escape(base_url), version=version, nav=nav_html, content=content)
        
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(final_html.encode("utf-8"))

    def handle_configuration_logger_save(self):
        try:
            content_length = int(self.headers["Content-Length"])
            post_data = self.rfile.read(content_length)
            params = json.loads(post_data)
            
            config = load_config()
            config["logger_retries"] = int(params.get("logger_retries", 3))
            config["modbus_timeout"] = float(params.get("modbus_timeout", 1.2))
            config["bacnet_timeout"] = int(params.get("bacnet_timeout", 45))
            
            from core.config import save_config
            save_config(config)
            
            self.send_json({"status": "ok"})
        except Exception as e:
            logger.error(f"Erreur lors de la sauvegarde de la configuration logger : {e}")
            self.send_json({"status": "error", "message": str(e)})

    def handle_logs_api(self, query):
        import os
        import re
        from core.paths import LOG_FILE

        try:
            limit = min(int(query.get("limit", [150])[0]), 1000)
        except Exception:
            limit = 150

        level_filter = query.get("level", [""])[0].upper()
        module_filter = query.get("module", [""])[0].lower()
        search_filter = query.get("search", [""])[0].lower()

        log_file_size = "0 Ko"
        if LOG_FILE.exists():
            try:
                sz = os.path.getsize(LOG_FILE)
                if sz >= 1024 * 1024:
                    log_file_size = f"{sz / (1024 * 1024):.1f} Mo"
                else:
                    log_file_size = f"{sz / 1024:.0f} Ko"
            except Exception:
                pass

        parsed_logs = []
        if LOG_FILE.exists():
            pattern = re.compile(r'^(\d{4}-\d{2}-\d{2}\s\d{2}:\d{2}:\d{2}(?:,\d+)?)\s-\s([^\s]+)\s-\s([A-Z]+)\s-\s(.*)$')
            try:
                with open(LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
                    lines = f.readlines()

                # On parcourt les lignes depuis la fin
                for line in reversed(lines):
                    line_str = line.strip()
                    if not line_str:
                        continue

                    m = pattern.match(line_str)
                    if m:
                        t, mod, lvl, msg = m.groups()
                    else:
                        t, mod, lvl, msg = "", "", "RAW", line_str

                    # Filtre par niveau
                    if level_filter:
                        if level_filter == "ERROR" and lvl != "ERROR":
                            continue
                        elif level_filter == "WARNING" and lvl not in ("WARNING", "ERROR"):
                            continue
                        elif level_filter == "INFO" and lvl not in ("INFO", "WARNING", "ERROR"):
                            continue
                        elif level_filter == "DEBUG" and lvl not in ("DEBUG", "INFO", "WARNING", "ERROR"):
                            continue

                    # Filtre par module
                    if module_filter and module_filter not in mod.lower():
                        continue

                    # Filtre par recherche textuelle
                    if search_filter:
                        full_text = f"{t} {mod} {lvl} {msg}".lower()
                        if search_filter not in full_text:
                            continue

                    parsed_logs.append({
                        "time": t,
                        "module": mod,
                        "level": lvl,
                        "msg": msg
                    })

                    if len(parsed_logs) >= limit:
                        break

                order = query.get("order", ["desc"])[0].lower()
                if order == "asc":
                    parsed_logs.reverse()
            except Exception as e:
                logger.error(f"Erreur lecture logs: {e}")

        self.send_json({
            "status": "ok",
            "logs": parsed_logs,
            "file_size": log_file_size
        })

    def handle_logs_download(self):
        from core.paths import LOG_FILE
        if not LOG_FILE.exists():
            self.send_error(404, "Fichier log introuvable")
            return

        try:
            with open(LOG_FILE, "rb") as f:
                content = f.read()

            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Disposition", "attachment; filename=rpinode.log")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
        except Exception as e:
            self.send_error(500, str(e))

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
        logger.info(f"handle_ip_annotate: body={post_data.decode('utf-8')}")
        try:
            data = json.loads(post_data)
            mac = data.get('mac', '').lower()
            vendor = data.get('vendor')
            annotations = data.get('annotations')
            
            logger.info(f"handle_ip_annotate: mac={mac}, vendor={vendor}, annotations={annotations}")
            
            if not mac:
                return self.send_json({"status": "error", "message": "MAC manquante"})
                
            from services.presence import get_current_site_id
            site_id = get_current_site_id()
            logger.info(f"handle_ip_annotate: site_id={site_id}")
            if not site_id:
                return self.send_json({"status": "error", "message": "Aucun chantier actif"})
                
            with get_db_connection() as conn:
                # 1. Mise à jour Fabricant (OUI) si fourni
                if vendor:
                    # Global (mac_vendors) - Clé OUI sur 8 caractères (ex: 00:11:22)
                    prefix = mac[:8].upper()
                    logger.info(f"handle_ip_annotate: updating vendor for prefix={prefix}")
                    conn.execute("""
                        INSERT INTO mac_vendors (prefix, vendor, is_dirty) 
                        VALUES (?, ?, 1) 
                        ON CONFLICT(prefix) DO UPDATE SET vendor = EXCLUDED.vendor, is_dirty = 1
                    """, (prefix, vendor))
                    
                    # Local (discovered_devices)
                    conn.execute("""
                        INSERT INTO discovered_devices (site_id, mac, vendor, is_dirty)
                        VALUES (?, ?, ?, 1)
                        ON CONFLICT(site_id, mac) DO UPDATE SET vendor = EXCLUDED.vendor, is_dirty = 1
                    """, (site_id, mac, vendor))
                
                # 2. Mise à jour Annotations si fournies
                if annotations is not None:
                    logger.info(f"handle_ip_annotate: updating annotations={annotations}")
                    row = conn.execute("SELECT annotations_json FROM discovered_devices WHERE site_id = ? AND mac = ?", (site_id, mac)).fetchone()
                    existing = json.loads(row["annotations_json"]) if row and row["annotations_json"] else {}
                    if isinstance(annotations, dict):
                        existing.update(annotations)
                    
                    logger.info(f"handle_ip_annotate: new annotations_json={json.dumps(existing)}")
                    conn.execute("""
                        INSERT INTO discovered_devices (site_id, mac, annotations_json, is_dirty)
                        VALUES (?, ?, ?, 1)
                        ON CONFLICT(site_id, mac) DO UPDATE SET annotations_json = EXCLUDED.annotations_json, is_dirty = 1
                    """, (site_id, mac, json.dumps(existing)))
                
                conn.commit()
            
            logger.info("handle_ip_annotate: success")
            return self.send_json({"status": "ok"})
        except Exception as e:
            logger.error(f"Erreur handle_ip_annotate : {e}", exc_info=True)
            self.send_json({"status": "error", "message": str(e)})

    def handle_ip_device_delete(self):
        try:
            content_length = int(self.headers.get("Content-Length", 0))
            post_data = self.rfile.read(content_length)
            params = json.loads(post_data)
            mac = params.get("mac", "").lower()

            if not mac:
                return self.send_json({"status": "error", "message": "Adresse MAC manquante"})

            from services.presence import get_current_site_id
            site_id = get_current_site_id()
            if not site_id:
                return self.send_json({"status": "error", "message": "Chantier introuvable"})

            with get_db_connection() as conn:
                conn.execute("DELETE FROM discovered_devices WHERE site_id = ? AND mac = ?", (site_id, mac))
                conn.commit()

            self.send_json({"status": "ok", "message": "Équipement supprimé"})
        except Exception as e:
            logger.error(f"Erreur handle_ip_device_delete : {e}", exc_info=True)
            self.send_json({"status": "error", "message": str(e)})

    def handle_ip_scan_purge_offline(self):
        try:
            from services.presence import get_current_site_id
            site_id = get_current_site_id()
            if not site_id:
                return self.send_json({"status": "error", "message": "Chantier introuvable"})

            with get_db_connection() as conn:
                row_last = conn.execute(
                    "SELECT MAX(last_seen) as last_scan FROM discovered_devices WHERE site_id = ?",
                    (site_id,)
                ).fetchone()

                if row_last and row_last["last_scan"]:
                    last_scan = row_last["last_scan"]
                    cursor = conn.execute(
                        "DELETE FROM discovered_devices WHERE site_id = ? AND (last_seen != ? OR last_seen IS NULL)",
                        (site_id, last_scan)
                    )
                    deleted_count = cursor.rowcount
                    conn.commit()
                else:
                    deleted_count = 0

            self.send_json({"status": "ok", "message": f"{deleted_count} équipement(s) hors-ligne purgé(s)", "deleted": deleted_count})
        except Exception as e:
            logger.error(f"Erreur handle_ip_scan_purge_offline : {e}", exc_info=True)
            self.send_json({"status": "error", "message": str(e)})

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
