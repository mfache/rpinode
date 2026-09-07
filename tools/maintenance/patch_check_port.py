import re

with open("src/services/ipscan.py", "r") as f:
    content = f.read()

# Le timeout global du run_ip_scan sur les 40 appareils avec delay asynchrone 
# sature peut-être le démon BACnet MQTT en balançant 40 who-is unicast presque en même temps (limitations sockets UDP).
# Ou alors check_tcp_port lève une erreur et fait échouer d'autres coroutines.

# Modifions scan_host pour traiter le ping UDP et TCP plus délicatement :
new_scan_host = """async def scan_host(ip, mac, iface, scan_timestamp, delay_ms=0):
    \"\"\"Scanne les ports d'un hôte trouvé.\"\"\"
    if delay_ms > 0:
        await asyncio.sleep(delay_ms / 1000.0)

    # Scans TCP très rapides et bas niveau
    ports_to_check = [80, 443, 502, 4196, 22, 23, 445]
    tasks = [check_tcp_port(ip, p) for p in ports_to_check]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    open_ports = []
    for res in results:
        if isinstance(res, int):
            open_ports.append(res)
            
    # Sonde BACnet via MQTT (séparée pour ne pas interférer avec les sockets TCP rapides)
    try:
        if await check_bacnet_udp(ip):
            open_ports.append(47808)
    except Exception as e:
        logger.error(f"Erreur check BACnet pour {ip}: {e}")

    open_ports.sort()

    res = {"""

content = re.sub(
    r"async def scan_host\(ip, mac, iface, scan_timestamp, delay_ms=0\):.*?(?=\n        \"ip\": ip,)",
    new_scan_host,
    content,
    flags=re.DOTALL
)

with open("src/services/ipscan.py", "w") as f:
    f.write(content)
