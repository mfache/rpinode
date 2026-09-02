import re

with open("src/services/ipscan.py", "r") as f:
    content = f.read()

# Enlever get_bacnet_ips
content = re.sub(
    r"def get_bacnet_ips\(\).*?except.*?return set\(\)\n\n",
    "",
    content,
    flags=re.DOTALL
)

# Enlever bacnet_ips=None de scan_host
content = content.replace(
    "async def scan_host(ip, mac, iface, scan_timestamp, delay_ms=0, bacnet_ips=None):",
    "async def scan_host(ip, mac, iface, scan_timestamp, delay_ms=0):"
)
content = content.replace("    if bacnet_ips is None:\n        bacnet_ips = set()\n\n", "")

# Enlever l'ajout conditionnel
content = re.sub(
    r"    if ip in bacnet_ips and 47808 not in open_ports:\n        open_ports\.append\(47808\)\n\n",
    "",
    content
)

# Enlever bacnet_ips = get_bacnet_ips() et l'argument
content = content.replace("            bacnet_ips = get_bacnet_ips()\n\n", "")
content = content.replace(
    "tasks.append(scan_host(ip_h, mac_h, iface, scan_timestamp, delay_ms=i * 50, bacnet_ips=bacnet_ips))",
    "tasks.append(scan_host(ip_h, mac_h, iface, scan_timestamp, delay_ms=i * 50))"
)

with open("src/services/ipscan.py", "w") as f:
    f.write(content)
