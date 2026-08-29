// Application Front-End (Mécanisme de mise à jour dynamique)
console.log("Application rpinode initialisée.");

let heartbeatTimeout;
let errorTimerInterval;
let secondsSinceLastUpdate = 0;
let lastTsExit = null;
let lastTsIp = null;

/**
 * Redémarre le serveur et attend qu'il revienne pour recharger la page.
 */
async function restartServer() {
    if (!confirm('Relancer le serveur rpinode ?')) return;
    
    const baseUrl = window.CONFIG?.baseUrl || "";
    
    // Affichage d'un écran d'attente
    document.body.innerHTML = `
        <div style="display:flex; flex-direction:column; align-items:center; justify-content:center; height:100vh; font-family:sans-serif; background:#2c3e50; color:white; text-align:center; padding:20px;">
            <div style="font-size: 3rem; margin-bottom: 20px;">🔄</div>
            <h2 style="margin-bottom:10px;">Redémarrage de rpinode...</h2>
            <p style="opacity: 0.8;">Le serveur se relance. Reconnexion automatique dès qu'il est prêt.</p>
            <div style="margin-top:30px; font-family:monospace; padding: 10px 20px; background: rgba(255,255,255,0.1); border-radius: 5px;" id="reconnect-status">Tentative de connexion...</div>
        </div>
    `;

    // Envoi de l'ordre de redémarrage
    try {
        fetch(`${baseUrl}/api/restart`, { method: 'POST' });
    } catch (e) {
        // L'erreur est attendue car le serveur se coupe
    }

    // Boucle de vérification
    const checkServer = async () => {
        try {
            const response = await fetch(`${baseUrl}/api/status`);
            if (response.ok) {
                // Succès ! On recharge la page à la racine de l'app
                window.location.href = baseUrl || "/";
                return;
            }
        } catch (e) {
            // Serveur toujours hors ligne
        }
        setTimeout(checkServer, 1000);
    };
    
    // On commence à chercher après un petit délai
    setTimeout(checkServer, 2000);
}

/**
 * Gère les actions système (reboot/shutdown)
 */
async function systemAction(action) {
    const label = action === 'reboot' ? 'Redémarrage' : 'Arrêt';
    const icon = action === 'reboot' ? '🔌' : '⛔';
    const confirmMsg = action === 'reboot' 
        ? 'Redémarrer COMPLÈTEMENT le boîtier ? La connexion sera coupée pendant environ une minute.' 
        : 'ÉTEINDRE le boîtier ? Il ne sera plus accessible à distance.';

    if (!confirm(confirmMsg)) return;

    const baseUrl = window.CONFIG?.baseUrl || "";

    // Affichage d'un écran d'attente
    document.body.innerHTML = `
        <div style="display:flex; flex-direction:column; align-items:center; justify-content:center; height:100vh; font-family:sans-serif; background:#c0392b; color:white; text-align:center; padding:20px;">
            <div style="font-size: 3rem; margin-bottom: 20px;">${icon}</div>
            <h2 style="margin-bottom:10px;">${label} du système en cours...</h2>
            <p style="opacity: 0.8;">L'ordre a été envoyé au Raspberry Pi.</p>
            <div style="margin-top:30px; font-family:monospace; padding: 10px 20px; background: rgba(255,255,255,0.1); border-radius: 5px;" id="reconnect-status">
                ${action === 'reboot' ? 'En attente du redémarrage (ceci peut prendre 60s)...' : 'Vous pouvez fermer cet onglet.'}
            </div>
        </div>
    `;

    try {
        await fetch(`${baseUrl}/api/${action}`, { method: 'POST' });
    } catch (e) {
        // Normal
    }

    if (action === 'reboot') {
        const checkServer = async () => {
            try {
                const response = await fetch(`${baseUrl}/api/status`);
                if (response.ok) {
                    window.location.href = baseUrl || "/";
                    return;
                }
            } catch (e) {}
            setTimeout(checkServer, 2000);
        };
        // Pour un reboot complet, on attend plus longtemps avant de chercher (15s)
        setTimeout(checkServer, 15000);
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
                    portsHtml.push(`<a href='${baseUrl}/scan/modbus?ip=${data.ip}' style='color: #2ecc71; font-weight: bold;'>502 (Modbus)</a>${m}`);
                } else if (p === 47808) {
                    let b = [];
                    if (data.bacnet_instance) b.push(`Inst: ${data.bacnet_instance}`);
                    if (data.bacnet_name && data.bacnet_name !== "Automate BACnet") b.push(data.bacnet_name);
                    let info = b.length ? ` <small style='color:#e67e22'>(${b.join(', ')})</small>` : '';
                    portsHtml.push(`<a href='${baseUrl}/scan/bacnet?ip=${data.ip}' style='color: #e67e22; font-weight: bold;'>47808 (BACnet)</a>${info}`);
                } else if (p === 80) {
                    portsHtml.push(`<a href='http://${data.ip}' target='_blank' style='color: #3498db;'>80</a>`);
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

                    // Traitement spécial pour les états actifs (couleurs des nodes et pills)
                    if (key.endsWith('_active') || key.startsWith('net_eth0_')) {
                        
                        // Stockage global de l'état eth0
                        if (key.startsWith('net_eth0_')) {
                            window.eth0State = window.eth0State || {};
                            window.eth0State[key.replace('net_eth0_', '')] = value;
                        }
                        
                        const isEth0Event = key.startsWith('net_eth0_') || key === 'net_eth0_active';
                        
                        if (isEth0Event) {
                            const node = document.getElementById('node-eth0');
                            const pill = document.getElementById('pill-eth0');
                            const s = window.eth0State || {};
                            
                            const isActive = (s.active === true || s.active === "true");
                            const hasCable = (s.cable === true || s.cable === "true");
                            const isDhcp = (s.dhcp === true || s.dhcp === "true");
                            const hasIp = (s.has_ip === true || s.has_ip === "true");
                            
                            if (node && pill) {
                                if (!hasCable) {
                                    node.classList.remove('active');
                                    pill.textContent = 'Débranché';
                                    pill.className = 'status-pill inactive';
                                } else if (isActive && hasIp) {
                                    node.classList.add('active');
                                    pill.textContent = 'Connecté';
                                    pill.className = 'status-pill active';
                                } else if (isDhcp && !hasIp) {
                                    node.classList.remove('active');
                                    pill.textContent = "En attente";
                                    pill.className = 'status-pill inactive';
                                } else {
                                    node.classList.remove('active');
                                    pill.textContent = 'Coupé';
                                    pill.className = 'status-pill inactive';
                                }
                            }
                        } else if (key.endsWith('_active')) {
                            const iface = key.replace('net_', '').replace('_active', '');
                            const node = document.getElementById(`node-${iface}`);
                            const pill = document.getElementById(`pill-${iface}`);
                            
                            const isActive = (value === true || value === "true");
                            
                            if (node) {
                                if (isActive) node.classList.add('active');
                                else node.classList.remove('active');
                            }
                            
                            if (pill) {
                                pill.textContent = isActive ? 'Connecté' : 'Coupé';
                                pill.className = `status-pill ${isActive ? 'active' : 'inactive'}`;
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
            document.getElementById('site-search').value = "";
            document.getElementById('site-results').style.display = 'none';
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
            document.getElementById('site-results').style.display = 'none';
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
