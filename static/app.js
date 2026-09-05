// Application Front-End (Mécanisme de mise à jour dynamique)
console.log("Application rpinode initialisée.");

let heartbeatTimeout;
let errorTimerInterval;
let secondsSinceLastUpdate = 0;
let lastTsExit = null;
let lastTsIp = null;

/**
 * Affiche l'écran de terminal de boot temps réel relié au superviseur Rust.
 */
function showBootScreen(title = "Redémarrage de rpinode...") {
    document.body.innerHTML = `
        <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background-color: #0f172a; color: #f8fafc; display: flex; flex-direction: column; align-items: center; min-height: 100vh; padding: 24px; box-sizing: border-box;">
            <div style="text-align: center; margin-top: 10px; margin-bottom: 20px;">
                <h1 style="font-size: 1.5rem; font-weight: 600; display: flex; align-items: center; justify-content: center; gap: 10px; margin-bottom: 6px;">
                    <span>🔄</span>
                    <span>${title}</span>
                    <span style="background: #334155; color: #94a3b8; font-size: 0.85rem; padding: 4px 10px; border-radius: 9999px; font-weight: normal;" id="boot-timer">0s</span>
                </h1>
                <p style="color: #94a3b8; font-size: 0.9rem;">Le superviseur affiche les logs d'initialisation en direct.</p>
            </div>

            <div style="display: flex; align-items: center; gap: 12px; background: rgba(30, 41, 59, 0.8); border: 1px solid #334155; border-radius: 8px; padding: 12px 20px; margin-bottom: 16px; width: 100%; max-width: 900px; box-sizing: border-box;">
                <div style="width: 18px; height: 18px; border: 3px solid rgba(255, 255, 255, 0.2); border-radius: 50%; border-top-color: #38bdf8; animation: spin 1s linear infinite;" id="boot-spinner"></div>
                <div style="font-size: 0.95rem; color: #cbd5e1; flex: 1;" id="boot-status-desc">Connexion au superviseur...</div>
            </div>

            <div style="width: 100%; max-width: 900px; background: #020617; border: 1px solid #1e293b; border-radius: 10px; box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.5); overflow: hidden; display: flex; flex-direction: column; height: 62vh; box-sizing: border-box;">
                <div style="background: #1e293b; padding: 10px 16px; display: flex; align-items: center; justify-content: space-between; font-size: 0.8rem; color: #94a3b8; font-family: monospace;">
                    <div style="display: flex; gap: 6px;">
                        <span style="width: 10px; height: 10px; border-radius: 50%; background: #ef4444; display: inline-block;"></span>
                        <span style="width: 10px; height: 10px; border-radius: 50%; background: #eab308; display: inline-block;"></span>
                        <span style="width: 10px; height: 10px; border-radius: 50%; background: #22c55e; display: inline-block;"></span>
                    </div>
                    <span>LIVE BOOT LOGS</span>
                    <span id="boot-log-count">0 lignes</span>
                </div>
                <div style="padding: 14px 16px; overflow-y: auto; flex: 1; font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace; font-size: 0.82rem; line-height: 1.5; color: #e2e8f0; white-space: pre-wrap; word-break: break-word;" id="boot-terminal"></div>
            </div>
        </div>
        <style>
            @keyframes spin { to { transform: rotate(360deg); } }
            .log-line { margin-bottom: 4px; }
            .log-INFO { color: #38bdf8; }
            .log-WARNING { color: #facc15; }
            .log-ERROR { color: #f87171; font-weight: bold; }
            .log-DEBUG { color: #64748b; }
            .log-HIGHLIGHT { color: #4ade80; font-weight: bold; }
        </style>
    `;

    const terminal = document.getElementById('boot-terminal');
    const statusDesc = document.getElementById('boot-status-desc');
    const timerBadge = document.getElementById('boot-timer');
    const logCountBadge = document.getElementById('boot-log-count');
    const startTime = Date.now();
    let count = 0;

    setInterval(() => {
        const elapsed = Math.floor((Date.now() - startTime) / 1000);
        if (timerBadge) timerBadge.innerText = `${elapsed}s`;
    }, 1000);

    function appendLog(line) {
        count++;
        if (logCountBadge) logCountBadge.innerText = `${count} lignes`;
        const div = document.createElement('div');
        div.className = 'log-line';

        if (line.includes('--- DEMARRAGE') || line.includes('Serveur HTTP')) {
            div.className += ' log-HIGHLIGHT';
        } else if (line.includes(' - INFO - ') || line.startsWith('INFO:')) {
            div.className += ' log-INFO';
        } else if (line.includes(' - WARNING - ')) {
            div.className += ' log-WARNING';
        } else if (line.includes(' - ERROR - ')) {
            div.className += ' log-ERROR';
        } else if (line.includes(' - DEBUG - ')) {
            div.className += ' log-DEBUG';
        }

        div.textContent = line;
        terminal.appendChild(div);
        terminal.scrollTop = terminal.scrollHeight;
    }

    // Connexion SSE au superviseur Rust
    try {
        const evtSource = new EventSource('/supervisor/stream');

        evtSource.onmessage = (event) => {
            if (!event.data) return;
            try {
                const payload = JSON.parse(event.data);
                if (payload.type === 'log') {
                    appendLog(payload.message);
                } else if (payload.type === 'status') {
                    if (statusDesc) statusDesc.textContent = payload.message;
                    if (payload.ready) {
                        if (statusDesc) statusDesc.textContent = "✅ Serveur rpinode prêt ! Rechargement...";
                        setTimeout(() => {
                            window.location.reload();
                        }, 500);
                    }
                }
            } catch (e) {
                appendLog(event.data);
            }
        };

        evtSource.onerror = () => {
            if (statusDesc) statusDesc.textContent = "En attente du service...";
        };
    } catch (e) {
        console.error("Erreur connexion SSE superviseur :", e);
    }
}

/**
 * Redémarre le serveur et attend qu'il revienne pour recharger la page.
 */
async function restartServer() {
    if (!confirm('Relancer le serveur rpinode ?')) return;
    
    const baseUrl = window.CONFIG?.baseUrl || "";
    
    // Affichage immédiat du terminal live de boot
    showBootScreen("Redémarrage de rpinode...");

    // Envoi de l'ordre de redémarrage
    try {
        await fetch(`${baseUrl}/api/restart`, { method: 'POST' });
    } catch (e) {
        // Le serveur coupe
    }
}

/**
 * Gère les actions système (reboot/shutdown)
 */
async function systemAction(action) {
    const label = action === 'reboot' ? 'Redémarrage complet' : 'Arrêt';
    const confirmMsg = action === 'reboot' 
        ? 'Redémarrer COMPLÈTEMENT le boîtier ? La connexion sera coupée pendant environ une minute.' 
        : 'ÉTEINDRE le boîtier ? Il ne sera plus accessible à distance.';

    if (!confirm(confirmMsg)) return;

    const baseUrl = window.CONFIG?.baseUrl || "";

    if (action === 'reboot') {
        showBootScreen("Reboot Système en cours...");
    } else {
        document.body.innerHTML = `
            <div style="display:flex; flex-direction:column; align-items:center; justify-content:center; height:100vh; font-family:sans-serif; background:#c0392b; color:white; text-align:center; padding:20px;">
                <div style="font-size: 3rem; margin-bottom: 20px;">⛔</div>
                <h2 style="margin-bottom:10px;">Arrêt du système en cours...</h2>
                <p style="opacity: 0.8;">L'ordre d'extinction a été envoyé au Raspberry Pi.</p>
                <div style="margin-top:30px; font-family:monospace; padding: 10px 20px; background: rgba(255,255,255,0.1); border-radius: 5px;">Vous pouvez fermer cet onglet.</div>
            </div>
        `;
    }

    try {
        await fetch(`${baseUrl}/api/${action}`, { method: 'POST' });
    } catch (e) {
        // Normal
    }
}

/**
 * Met à jour un élément HTML (Text ou HTML selon la clé)
 */
function updateElement(el, value, key) {
    if (key.endsWith('_routes') || key.endsWith('_html')) {
        el.innerHTML = value;
    } else {
        el.textContent = value;
    }
}

function showHeartbeatError() {
    const dot = document.getElementById("heartbeat-dot");
    const timer = document.getElementById("heartbeat-timer");
    
    if (dot) {
        dot.style.transition = "none";
        dot.style.backgroundColor = "#c62828"; // Rouge erreur
        dot.style.opacity = "1";
    }

    if (timer) {
        timer.style.display = "inline";
        // On initialise le compteur à 5 car c'est le moment où on déclenche l'erreur
        if (secondsSinceLastUpdate < 5) secondsSinceLastUpdate = 5;
        
        // On lance un intervalle pour incrémenter le compteur chaque seconde
        if (!errorTimerInterval) {
            errorTimerInterval = setInterval(() => {
                secondsSinceLastUpdate++;
                timer.textContent = `${secondsSinceLastUpdate}s`;
            }, 1000);
        }
        timer.textContent = `${secondsSinceLastUpdate}s`;
    }
}

/**
 * Gère l'affichage de la sidebar sur mobile
 */
function toggleSidebar() {
    console.log("Toggle Sidebar appelé");
    const sidebar = document.querySelector('.sidebar');
    if (sidebar) {
        sidebar.classList.toggle('open');
        console.log("Sidebar status:", sidebar.classList.contains('open') ? "Ouverte" : "Fermée");
    }
}

document.addEventListener("DOMContentLoaded", () => {
    // Fermer la sidebar mobile lors du clic sur un lien
    document.querySelectorAll('.sidebar a').forEach(link => {
        link.addEventListener('click', () => {
            const sidebar = document.querySelector('.sidebar');
            if (window.innerWidth <= 768 && sidebar) {
                sidebar.classList.remove('open');
            }
        });
    });

    // Connexion SSE pour mettre à jour les éléments dynamiques
    const baseUrl = window.CONFIG?.baseUrl || "";
    const evtSource = new EventSource(`${baseUrl}/api/stream`);

    evtSource.addEventListener('host_ready', (e) => {
        if (!window.location.pathname.includes('/scan/ip')) return;
        try {
            const data = JSON.parse(e.data);
            const tbody = document.getElementById('ipscan-results-body');
            if (!tbody) return;

            let row = null;
            if (data.mac) {
                for (let tr of tbody.rows) {
                    if (tr.cells.length > 1 && tr.cells[1].textContent.trim().toLowerCase() === data.mac.toLowerCase()) {
                        row = tr;
                        break;
                    }
                }
            }

            let portsHtml = [];
            const portsList = Array.isArray(data.ports) ? data.ports : [];
            for (let p of portsList) {
                if (p === 502) {
                    let m = data.modbus_info ? ` <small style='color:#2ecc71'>(${data.modbus_info})</small>` : '';
                    portsHtml.push(`<a href='${baseUrl}/modbus/tools?ip=${data.ip}' style='color: #2ecc71; font-weight: bold;'>502 (Modbus)</a>${m}`);
                } else if (p === 47808) {
                    let b = [];
                    if (data.bacnet_instance) b.push(`Inst: ${data.bacnet_instance}`);
                    if (data.bacnet_name && data.bacnet_name !== "Automate BACnet") b.push(data.bacnet_name);
                    let info = b.length ? ` <small style='color:#e67e22'>(${b.join(', ')})</small>` : '';
                    portsHtml.push(`<a href='${baseUrl}/scan/bacnet?ip=${data.ip}' style='color: #e67e22; font-weight: bold;'>47808 (BACnet)</a>${info}`);
                } else if (p === 80) {
                    portsHtml.push(`<a href='http://${data.ip}' target='_blank' style='color: #3498db;'>80</a>`);
                } else if (p === 443) {
                    portsHtml.push(`<a href='https://${data.ip}' target='_blank' style='color: #9b59b6;'>443</a>`);
                } else {
                    portsHtml.push(p);
                }
            }
            let portsStr = portsHtml.length ? portsHtml.join(', ') : "<span style='opacity:0.4;'>Aucun</span>";

            if (row) {
                row.classList.remove('row-offline');
                const ipCell = row.cells[0];
                ipCell.style.opacity = '1';
                ipCell.style.textDecoration = 'none';
                ipCell.style.fontWeight = 'bold';
                ipCell.innerHTML = `<span title='En ligne' style='color: #2ecc71; font-size: 0.8em; margin-right: 5px;'>🟢</span>${data.ip}`;
                row.cells[3].innerHTML = portsStr;

                row.style.transition = 'background 0.3s';
                row.style.background = '#e8f8f5';
                setTimeout(() => { row.style.background = ''; }, 600);
            } else {
                if (tbody.rows.length === 1 && tbody.rows[0].cells.length === 1) tbody.innerHTML = '';
                // Nouvelle ligne, rechargement silencieux pour rafraîchir toutes les annotations custom
                const refreshTableSilently = async () => {
                    try {
                        const url = (baseUrl + '/api/scan/ip/results').replace(/\/+/g, '/');
                        const res = await fetch(url);
                        const resData = await res.json();
                        if (resData.html) tbody.innerHTML = resData.html;
                        if (typeof filterTable === 'function') filterTable();
                    } catch(err) {}
                };
                refreshTableSilently();
            }
        } catch (err) { console.error("Erreur SSE host_ready:", err); }
    });

    // Événement SSE Modbus en direct (poussé par MQTT)
    evtSource.addEventListener('modbus_point', (e) => {
        try {
            const data = JSON.parse(e.data);
            const el = document.getElementById(`val-${data.point_id}`);
            if (!el) return;

            const newDisplay = data.display || "—";
            if (data.error) {
                el.className = "live-val badge-error";
                el.textContent = "Err";
                el.title = data.error;
            } else {
                el.className = "live-val badge-success";
                el.textContent = newDisplay;
                el.title = `Mis à jour à ${new Date(data.ts * 1000).toLocaleTimeString()}`;

                el.classList.remove("suivi-flash");
                void el.offsetWidth;
                el.classList.add("suivi-flash");
            }
        } catch (err) {
            console.error("Erreur SSE modbus_point:", err);
        }
    });

    evtSource.onmessage = function(event) {
        try {
            // Réinitialisation du "watchdog" et du compteur
            clearTimeout(heartbeatTimeout);
            clearInterval(errorTimerInterval);
            errorTimerInterval = null;
            secondsSinceLastUpdate = 0;

            const timer = document.getElementById("heartbeat-timer");
            if (timer) {
                timer.style.display = "none";
                timer.textContent = "";
            }

            heartbeatTimeout = setTimeout(showHeartbeatError, 5000);

            const updates = JSON.parse(event.data);
            console.log("Mises à jour reçues:", updates);
            
            // Mise à jour générique : cherche tout ID ou CLASSE commençant par "subt_"
            // correspondant à une clé dans le JSON reçu.
            for (const [key, value] of Object.entries(updates)) {
                try {
                    if (value === undefined || value === null) continue;
                    
                    // Traitement spécial pour le chantier provisoire
                    if (key === 'is_provisional') {
                        const banner = document.getElementById('subt_is_provisional');
                        if (banner) {
                            banner.style.display = value ? 'block' : 'none';
                            if (value) checkFleetRegistration();
                        }
                        continue; // On ne veut pas que updateElement mette "true" dans la bannière
                    }

                    // Traitement spécial pour le mode WiFi
                    if (key === 'wifi_mode') {
                        const apZone = document.getElementById('ap-info-zone');
                        if (apZone && typeof value === 'string') {
                            apZone.style.display = value.includes('Access Point') ? 'block' : 'none';
                        }
                    }

                    // Traitement spécial pour le statut de synchronisation
                    if (key === 'sync_ok') {
                        const syncDot = document.getElementById('subt_sync_ok');
                        if (syncDot) {
                            syncDot.style.display = (value === true || value === "true") ? 'none' : 'inline-block';
                        }
                        continue;
                    }

                    const targetKey = `subt_${key}`;

                    // 1. Chercher par ID
                    const elById = document.getElementById(targetKey);
                    if (elById) {
                        if (key === 'net_wlan0_clients_html') console.log("Mise à jour clients DHCP:", value);
                        updateElement(elById, value, key);
                    }

                    // 2. Chercher par Classe (pour les éléments répétés)
                    const elsByClass = document.getElementsByClassName(targetKey);
                    for (const el of elsByClass) {
                        updateElement(el, value, key);
                    }

                    // Traitement spécial pour les états actifs (couleurs des nodes, pills et indicateurs)
                    if (key.endsWith('_active') || key.startsWith('net_eth0_') || key.startsWith('net_wlan0_') || key.startsWith('net_wwan0_')) {
                        let iface = null;
                        if (key.startsWith('net_eth0_')) iface = 'eth0';
                        else if (key.startsWith('net_wlan0_')) iface = 'wlan0';
                        else if (key.startsWith('net_wwan0_')) iface = 'wwan0';
                        else if (key.endsWith('_active')) iface = key.replace('net_', '').replace('_active', '');

                        if (iface) {
                            window.ifaceState = window.ifaceState || {};
                            window.ifaceState[iface] = window.ifaceState[iface] || {};

                            if (key.startsWith(`net_${iface}_`)) {
                                window.ifaceState[iface][key.replace(`net_${iface}_`, '')] = value;
                            } else if (key.endsWith('_active')) {
                                window.ifaceState[iface]['active'] = value;
                            }

                            const node = document.getElementById(`node-${iface}`);
                            const pill = document.getElementById(`pill-${iface}`);
                            const indicator = document.getElementById(`status-indicator-${iface}`);
                            const statusLabel = document.getElementById(`status-text-${iface}`);

                            const s = window.ifaceState[iface] || {};
                            const isActive = (s.active === true || s.active === "true");
                            const hasCable = s.cable !== undefined ? (s.cable === true || s.cable === "true") : true;
                            const isDhcp = (s.dhcp === true || s.dhcp === "true");
                            const hasIp = (s.has_ip === true || s.has_ip === "true");

                            let pillText = 'Inactif';
                            let indicatorText = 'Inactif';
                            let isOnline = false;

                            if (!hasCable && iface === 'eth0') {
                                pillText = 'Débranché';
                                indicatorText = 'Câble débranché';
                                isOnline = false;
                            } else if (isActive && hasIp) {
                                pillText = 'Connecté';
                                indicatorText = 'En ligne';
                                isOnline = true;
                            } else if (isDhcp && !hasIp && hasCable) {
                                pillText = 'En attente';
                                indicatorText = 'En attente DHCP';
                                isOnline = false;
                            } else if (isActive) {
                                pillText = 'Connecté';
                                indicatorText = 'En ligne';
                                isOnline = true;
                            } else {
                                pillText = 'Coupé';
                                indicatorText = 'Inactif';
                                isOnline = false;
                            }

                            if (node && pill) {
                                if (isOnline) {
                                    node.classList.add('active');
                                    pill.className = 'status-pill active';
                                } else {
                                    node.classList.remove('active');
                                    pill.className = 'status-pill inactive';
                                }
                                pill.textContent = pillText;
                            }

                            if (indicator && statusLabel) {
                                indicator.className = `status-indicator ${isOnline ? 'status-online' : 'status-offline'}`;
                                statusLabel.textContent = indicatorText;
                            }
                        }
                    }

                    // Traitement spécial pour Tailscale exit et IP
                    if (key === 'net_ts_exit' || key === 'net_ts_ip') {
                        const exitIface = updates['net_ts_exit'] || window.lastTsExit;
                        const tsIp = updates['net_ts_ip'] || window.lastTsIp;
                        
                        if (exitIface) {
                            // Masquer tous les blocs TS
                            document.querySelectorAll('.ts-integrated-info').forEach(m => m.style.display = 'none');
                            
                            // Afficher et mettre à jour le bon bloc
                            const tsBlock = document.getElementById(`ts-info-${exitIface}`);
                            const tsIpDisplay = document.getElementById(`subt_net_ts_ip_${exitIface}`);
                            
                            if (tsBlock) tsBlock.style.display = 'block';
                            if (tsIpDisplay && tsIp) tsIpDisplay.textContent = tsIp;
                            
                            window.lastTsExit = exitIface;
                        }
                        if (tsIp) window.lastTsIp = tsIp;
                    }

                    // Traitement spécial pour le scanner IP
                    if (key === 'ipscan_running') {
                        const btn = document.getElementById('btn-start-scan');
                        const status = document.getElementById('scan-status');
                        
                        if (btn && status) {
                            const isRunning = (value === true || value === "true");
                            const wasRunning = status.classList.contains('running');
                            
                            btn.disabled = isRunning;
                            status.textContent = isRunning ? 'Scan en cours...' : 'Prêt';
                            status.className = `status-badge ${isRunning ? 'running' : 'idle'}`;
                            
                            // Si le scan vient de se terminer, on rafraîchit la table en arrière-plan
                            if (wasRunning && !isRunning && window.location.pathname.includes('/scan/ip')) {
                                setTimeout(async () => {
                                    try {
                                        const url = (baseUrl + '/api/scan/ip/results').replace(/\/+/g, '/');
                                        const res = await fetch(url);
                                        const data = await res.json();
                                        
                                        const tbody = document.getElementById('ipscan-results-body');
                                        const lastScan = document.getElementById('subt_ipscan_last_scan');
                                        const table = document.getElementById('inventory-table');

                                        if (tbody && data.html) {
                                            tbody.innerHTML = data.html;
                                        }
                                        if (lastScan && data.scanned_at) {
                                            lastScan.textContent = data.scanned_at;
                                        }
                                        if (table) {
                                            table.classList.remove('scanning');
                                        }

                                        // Réappliquer les filtres/tris/visibilités
                                        if (typeof initColumnPicker === 'function') {
                                            const hiddenColLabels = JSON.parse(localStorage.getItem('ipscan_hidden_labels') || '[]');
                                            const table = document.getElementById("inventory-table");
                                            const headers = table.querySelectorAll("thead th");
                                            headers.forEach((th, index) => {
                                                if (index === 0) return;
                                                const label = th.textContent.replace(' ↕', '').replace('×', '').trim();
                                                if (hiddenColLabels.includes(label)) {
                                                    applyColumnVisibility(index, false);
                                                }
                                            });
                                        }
                                        if (typeof filterTable === 'function') filterTable();
                                    } catch(e) {
                                        console.error("Erreur lors du rafraîchissement du tableau:", e);
                                    }
                                }, 500);
                            }
                        }
                    }
                    if (key === 'ipscan_last_at') {
                        // Si le scan a été mis à jour pendant qu'on regarde la page
                        // On update juste le label du timestamp pour éviter de recharger la grille de manière agressive.
                        // Les mises à jour fines sont gérées par host_ready.
                        if (window.location.pathname.includes('/scan/ip')) {
                            const lastScan = document.getElementById('subt_ipscan_last_scan');
                            if (lastScan && window.lastIpscanAt && window.lastIpscanAt !== value) {
                                lastScan.textContent = value;
                            }
                            window.lastIpscanAt = value;
                        }
                    }
                } catch (innerError) {
                    console.error(`Erreur lors de la mise à jour de la clé ${key}:`, innerError);
                }
            }

            // Animation du point de pulsation (Heartbeat)
            // Animation du point de pulsation (Heartbeat) : Flash (pop) puis Fondu (fade)
            const dot = document.getElementById("heartbeat-dot");
            if (dot) {
                dot.style.transition = "none";      // Apparition instantanée
                dot.style.backgroundColor = "#2ecc71"; // Vert Delta
                dot.style.opacity = "1";
                
                dot.offsetHeight; // Force le recalcul (reflow) pour appliquer 'transition: none' immédiatement

                setTimeout(() => { 
                    // On ne lance le fondu que si on est toujours "en vie" (fond vert)
                    if (dot.style.backgroundColor.includes("rgb(46, 204, 113)") || dot.style.backgroundColor === "#2ecc71") {
                        dot.style.transition = "opacity 1s ease-out"; // Disparition lente
                        dot.style.opacity = "0"; 
                    }
                }, 100);
            }
        } catch (e) {
            console.error("Erreur parsing SSE JSON:", e);
        }
    };

    evtSource.onerror = function(err) {
        console.error("Erreur de connexion au flux SSE:", err);
        showHeartbeatError();
    };

    // Gestion de la recherche de chantiers
    const siteInput = document.getElementById('site-search');
    const siteResults = document.getElementById('site-results');
    let searchTimeout;

    if (siteInput) {
        siteInput.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') {
                e.preventDefault();
                confirmNewSite();
            }
        });

        siteInput.addEventListener('input', () => {
            clearTimeout(searchTimeout);
            const query = siteInput.value.trim();
            if (query.length < 2) {
                siteResults.style.display = 'none';
                return;
            }

            searchTimeout = setTimeout(async () => {
                try {
                    const response = await fetch(`${baseUrl}/api/site/search?q=${encodeURIComponent(query)}`);
                    const results = await response.json();
                    
                    if (results.length > 0) {
                        siteResults.innerHTML = results.map(site => {
                            const escapedName = site.name.replace(/'/g, "\\'");
                            return `
                                <div class="site-result-item" onclick="selectSite('${escapedName}', '${site.external_id}')" style="padding: 10px; border-bottom: 1px solid #eee; cursor: pointer; transition: background 0.2s;">
                                    <strong>${site.name}</strong>
                                </div>
                            `;
                        }).join('');
                        siteResults.style.display = 'block';
                    } else {
                        siteResults.style.display = 'none';
                    }
                } catch (e) {
                    console.error("Erreur recherche site:", e);
                }
            }, 300);
        });
    }
});

async function checkFleetRegistration() {
    const baseUrl = window.CONFIG?.baseUrl || "";
    try {
        const response = await fetch(`${baseUrl}/api/fleet/status`);
        const status = await response.json();
        const warning = document.getElementById('fleet-unregistered-warning');
        if (warning) {
            warning.style.display = status.registered ? 'none' : 'block';
        }
    } catch (e) {}
}

/**
 * Sélectionne un site existant
 */
async function selectSite(name, externalId) {
    if (!confirm(`Associer ce boîtier au chantier existant "${name}" ?`)) return;
    
    const baseUrl = window.CONFIG?.baseUrl || "";
    try {
        const response = await fetch(`${baseUrl}/api/site/rename`, {
            method: 'POST',
            body: JSON.stringify({ name: name, external_id: externalId })
        });
        if (response.ok) {
            const input = document.getElementById('site-search');
            if (input) input.value = "";
            const res = document.getElementById('site-results');
            if (res) res.style.display = 'none';
            window.location.reload();
        } else {
            alert("Erreur lors de l'association du chantier");
        }
    } catch (e) {
        alert("Erreur lors de l'association");
    }
}

/**
 * Valide un nouveau nom de chantier
 */
async function confirmNewSite() {
    const input = document.getElementById('site-search');
    if (!input) return;
    const name = input.value.trim();
    if (!name) return;
    
    if (!confirm(`Créer le nouveau chantier "${name}" ?`)) return;
    
    const baseUrl = window.CONFIG?.baseUrl || "";
    try {
        const response = await fetch(`${baseUrl}/api/site/rename`, {
            method: 'POST',
            body: JSON.stringify({ name: name })
        });
        if (response.ok) {
            input.value = "";
            const res = document.getElementById('site-results');
            if (res) res.style.display = 'none';
            window.location.reload();
        } else {
            alert("Erreur lors de la création du chantier");
        }
    } catch (e) {
        alert("Erreur lors de la création");
    }
}

/**
 * Ajoute un appareil Modbus
 */
document.addEventListener('submit', async (e) => {
    if (e.target && (e.target.id === 'form-add-device' || e.target.id === 'form-edit-template')) {
        e.preventDefault();
        const formData = new FormData(e.target);
        const baseUrl = window.CONFIG?.baseUrl || "";
        try {
            // Helper pour transformer FormData en JSON gérant les tableaux []
            const payload = {};
            formData.forEach((value, key) => {
                if (key.endsWith('[]')) {
                    if (!payload[key]) payload[key] = [];
                    payload[key].push(value);
                } else {
                    payload[key] = value;
                }
            });

            let endpoint = '';
            if (e.target.id === 'form-add-device') {
                endpoint = window.location.pathname.includes('modbus') ? '/api/modbus/device/add' : '/api/bacnet/device/add';
            } else if (e.target.id === 'form-edit-template') {
                endpoint = window.location.pathname.includes('modbus') ? '/api/modbus/template/save' : '/api/bacnet/template/save';
            }

            const response = await fetch(`${baseUrl}${endpoint}`, {
                method: 'POST',
                body: JSON.stringify(payload)
            });
            if (response.ok) {
                hideModal(e.target.id.replace('form-', 'modal-'));
                location.reload();
            } else {
                const err = await response.json();
                alert("Erreur: " + err.message);
            }
        } catch (e) {
            alert("Erreur réseau");
        }
    }
});
