use axum::{
    body::Body,
    extract::{Request, State},
    http::{header, HeaderName, HeaderValue, StatusCode},
    response::{
        sse::{Event, KeepAlive, Sse},
        Html, IntoResponse, Response,
    },
    routing::get,
    Router,
};
use futures_util::stream::Stream;
use std::{
    convert::Infallible,
    io::SeekFrom,
    net::SocketAddr,
    path::PathBuf,
    time::Duration,
};
use tokio::{
    fs::File,
    io::{AsyncBufReadExt, AsyncSeekExt, BufReader},
    sync::watch,
    time::sleep,
};
use tokio_stream::wrappers::ReceiverStream;
use tracing::info;

#[derive(Clone)]
struct AppState {
    backend_url: String,
    log_file_path: PathBuf,
    client: reqwest::Client,
    backend_alive: watch::Receiver<bool>,
}

const FALLBACK_HTML: &str = r#"<!DOCTYPE html>
<html lang="fr">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>rpinode - Démarrage en cours</title>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            background-color: #0f172a;
            color: #f8fafc;
            display: flex;
            flex-direction: column;
            align-items: center;
            min-height: 100vh;
            padding: 24px;
        }
        .header {
            text-align: center;
            margin-top: 20px;
            margin-bottom: 24px;
        }
        .header h1 {
            font-size: 1.6rem;
            font-weight: 600;
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 10px;
            margin-bottom: 8px;
        }
        .badge {
            background: #334155;
            color: #94a3b8;
            font-size: 0.85rem;
            padding: 4px 10px;
            border-radius: 9999px;
            font-weight: normal;
        }
        .status-box {
            display: flex;
            align-items: center;
            gap: 12px;
            background: rgba(30, 41, 59, 0.8);
            border: 1px solid #334155;
            border-radius: 8px;
            padding: 12px 20px;
            margin-bottom: 20px;
            width: 100%;
            max-width: 900px;
        }
        .spinner {
            width: 20px;
            height: 20px;
            border: 3px solid rgba(255, 255, 255, 0.2);
            border-radius: 50%;
            border-top-color: #38bdf8;
            animation: spin 1s linear infinite;
        }
        @keyframes spin {
            to { transform: rotate(360deg); }
        }
        .status-text {
            font-size: 0.95rem;
            color: #cbd5e1;
            flex: 1;
        }
        .terminal-container {
            width: 100%;
            max-width: 900px;
            background: #020617;
            border: 1px solid #1e293b;
            border-radius: 10px;
            box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.5);
            overflow: hidden;
            display: flex;
            flex-direction: column;
            height: 60vh;
        }
        .terminal-topbar {
            background: #1e293b;
            padding: 10px 16px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            font-size: 0.8rem;
            color: #94a3b8;
            font-family: monospace;
        }
        .terminal-dots {
            display: flex;
            gap: 6px;
        }
        .dot { width: 10px; height: 10px; border-radius: 50%; }
        .dot-red { background: #ef4444; }
        .dot-yellow { background: #eab308; }
        .dot-green { background: #22c55e; }
        .terminal-body {
            padding: 14px 16px;
            overflow-y: auto;
            flex: 1;
            font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
            font-size: 0.82rem;
            line-height: 1.5;
            color: #e2e8f0;
            white-space: pre-wrap;
            word-break: break-word;
        }
        .log-line {
            margin-bottom: 4px;
        }
        .log-INFO { color: #38bdf8; }
        .log-WARNING { color: #facc15; }
        .log-ERROR { color: #f87171; font-weight: bold; }
        .log-DEBUG { color: #64748b; }
        .log-HIGHLIGHT { color: #4ade80; font-weight: bold; }
    </style>
</head>
<body>
    <div class="header">
        <h1>
            <span>🔄</span>
            <span>rpinode - Démarrage en cours</span>
            <span class="badge" id="timer">0s</span>
        </h1>
        <p style="color: #94a3b8; font-size: 0.9rem;">Le superviseur affiche les logs d'initialisation en direct.</p>
    </div>

    <div class="status-box">
        <div class="spinner" id="main-spinner"></div>
        <div class="status-text" id="status-desc">Attente de l'initialisation du service Python...</div>
    </div>

    <div class="terminal-container">
        <div class="terminal-topbar">
            <div class="terminal-dots">
                <div class="dot dot-red"></div>
                <div class="dot dot-yellow"></div>
                <div class="dot dot-green"></div>
            </div>
            <span>LIVE BOOT LOGS</span>
            <span id="log-count">0 lignes</span>
        </div>
        <div class="terminal-body" id="terminal"></div>
    </div>

    <script>
        const terminal = document.getElementById('terminal');
        const statusDesc = document.getElementById('status-desc');
        const timerBadge = document.getElementById('timer');
        const logCountBadge = document.getElementById('log-count');
        let startTime = Date.now();
        let count = 0;

        setInterval(() => {
            const elapsed = Math.floor((Date.now() - startTime) / 1000);
            timerBadge.innerText = `${elapsed}s`;
        }, 1000);

        function appendLog(line) {
            count++;
            logCountBadge.innerText = `${count} lignes`;
            const div = document.createElement('div');
            div.className = 'log-line';

            let formatted = line;
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

            div.textContent = formatted;
            terminal.appendChild(div);
            terminal.scrollTop = terminal.scrollHeight;
        }

        // Connexion SSE vers le superviseur Rust
        const evtSource = new EventSource('/supervisor/stream');

        evtSource.onmessage = (event) => {
            if (!event.data) return;
            try {
                const payload = JSON.parse(event.data);
                if (payload.type === 'log') {
                    appendLog(payload.message);
                } else if (payload.type === 'status') {
                    statusDesc.textContent = payload.message;
                    if (payload.ready) {
                        statusDesc.textContent = "✅ Serveur rpinode prêt ! Rechargement...";
                        setTimeout(() => {
                            window.location.reload();
                        }, 600);
                    }
                }
            } catch (e) {
                appendLog(event.data);
            }
        };

        evtSource.onerror = () => {
            statusDesc.textContent = "Tentative de reconnexion au superviseur...";
        };
    </script>
</body>
</html>"#;

#[tokio::main]
async fn main() {
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| "rpinode_supervisor=info".into()),
        )
        .init();

    let listen_port = std::env::var("PORT").unwrap_or_else(|_| "8081".to_string());
    let backend_port = std::env::var("BACKEND_PORT").unwrap_or_else(|_| "8082".to_string());
    let bind_addr: SocketAddr = format!("0.0.0.0:{}", listen_port)
        .parse()
        .expect("Adresse d'écoute invalide");
    let backend_url = format!("http://127.0.0.1:{}", backend_port);

    // Détection du chemin du fichier de log
    let log_path = if let Ok(p) = std::env::var("LOG_FILE") {
        PathBuf::from(p)
    } else if PathBuf::from("/tmp/rpinode/log/rpinode.log").exists() {
        PathBuf::from("/tmp/rpinode/log/rpinode.log")
    } else if PathBuf::from("data/rpinode.log").exists() {
        PathBuf::from("data/rpinode.log")
    } else {
        PathBuf::from("/tmp/rpinode/log/rpinode.log")
    };

    info!(
        "Superviseur Rust démarré sur {} -> Backend: {} (Log: {:?})",
        bind_addr, backend_url, log_path
    );

    let client = reqwest::Client::builder()
        .timeout(Duration::from_secs(60))
        .build()
        .expect("Erreur création client HTTP");

    let (alive_tx, alive_rx) = watch::channel(false);

    // Tâche d'arrière-plan surveillant la santé du backend Python
    let health_client = client.clone();
    let health_url = format!("{}/api/status", backend_url);
    tokio::spawn(async move {
        loop {
            let is_alive = match health_client.get(&health_url).timeout(Duration::from_millis(800)).send().await {
                Ok(res) => res.status().is_success(),
                Err(_) => false,
            };
            let _ = alive_tx.send(is_alive);
            sleep(Duration::from_millis(600)).await;
        }
    });

    let state = AppState {
        backend_url,
        log_file_path: log_path,
        client,
        backend_alive: alive_rx,
    };

    let app = Router::new()
        .route("/supervisor/stream", get(handle_supervisor_stream))
        .fallback(handle_proxy_or_fallback)
        .with_state(state);

    let listener = tokio::net::TcpListener::bind(bind_addr).await.unwrap();
    axum::serve(listener, app).await.unwrap();
}

/// Flux SSE envoyant en temps réel les logs et l'état du backend
async fn handle_supervisor_stream(
    State(state): State<AppState>,
) -> Sse<impl Stream<Item = Result<Event, Infallible>>> {
    let (tx, rx) = tokio::sync::mpsc::channel(100);

    let log_path = state.log_file_path.clone();
    let mut alive_rx = state.backend_alive.clone();

    tokio::spawn(async move {
        // Envoi d'un message initial
        let _ = tx
            .send(Ok(Event::default().data(r#"{"type":"status","message":"Lecture des logs en cours..."}"#)))
            .await;

        let mut file_reader = None;

        // Ouvrir le fichier de log dès qu'il existe
        if let Ok(mut file) = File::open(&log_path).await {
            // Se placer vers les 4 Ko précédents pour afficher le début du boot
            let meta = file.metadata().await;
            if let Ok(m) = meta {
                let len = m.len();
                let start_seek = len.saturating_sub(4096);
                let _ = file.seek(SeekFrom::Start(start_seek)).await;
            }
            file_reader = Some(BufReader::new(file));
        }

        let mut line_buf = String::new();

        loop {
            // 1. Vérification si le backend est redevenu disponible
            if *alive_rx.borrow_and_update() {
                let _ = tx
                    .send(Ok(Event::default().data(r#"{"type":"status","ready":true,"message":"Backend prêt"}"#)))
                    .await;
                break;
            }

            // 2. Lecture du fichier de log
            if file_reader.is_none() {
                if let Ok(file) = File::open(&log_path).await {
                    file_reader = Some(BufReader::new(file));
                }
            }

            if let Some(ref mut reader) = file_reader {
                line_buf.clear();
                match reader.read_line(&mut line_buf).await {
                    Ok(n) if n > 0 => {
                        let trimmed = line_buf.trim_end();
                        if !trimmed.is_empty() {
                            let msg = serde_json::json!({
                                "type": "log",
                                "message": trimmed
                            });
                            if tx.send(Ok(Event::default().data(msg.to_string()))).await.is_err() {
                                break;
                            }
                        }
                    }
                    Ok(_) => {
                        // Pas de nouvelle ligne pour le moment
                        sleep(Duration::from_millis(150)).await;
                    }
                    Err(_) => {
                        sleep(Duration::from_millis(300)).await;
                    }
                }
            } else {
                sleep(Duration::from_millis(300)).await;
            }
        }
    });

    Sse::new(ReceiverStream::new(rx)).keep_alive(KeepAlive::default())
}

/// Handler principal : proxy vers le backend Python ou affichage de la page de boot
async fn handle_proxy_or_fallback(
    State(state): State<AppState>,
    req: Request,
) -> Response {
    let method = req.method().clone();
    let uri = req.uri();
    let path_and_query = uri.path_and_query().map(|pq| pq.as_str()).unwrap_or("/");

    let target_url = format!("{}{}", state.backend_url, path_and_query);

    // Préparer la requête vers le backend Python
    let mut backend_req = state.client.request(
        reqwest::Method::from_bytes(method.as_str().as_bytes()).unwrap(),
        &target_url,
    );

    // Copier les headers
    for (k, v) in req.headers() {
        if k != header::HOST {
            if let Ok(header_name) = reqwest::header::HeaderName::from_bytes(k.as_str().as_bytes()) {
                if let Ok(header_val) = reqwest::header::HeaderValue::from_bytes(v.as_bytes()) {
                    backend_req = backend_req.header(header_name, header_val);
                }
            }
        }
    }

    // Récupérer le corps de la requête entrante
    let body_bytes = match axum::body::to_bytes(req.into_body(), 10 * 1024 * 1024).await {
        Ok(b) => b,
        Err(_) => return StatusCode::BAD_REQUEST.into_response(),
    };

    if !body_bytes.is_empty() {
        backend_req = backend_req.body(body_bytes);
    }

    // Tenter de contacter le backend Python
    match backend_req.send().await {
        Ok(backend_resp) => {
            let status = StatusCode::from_u16(backend_resp.status().as_u16())
                .unwrap_or(StatusCode::INTERNAL_SERVER_ERROR);

            let mut builder = Response::builder().status(status);

            for (k, v) in backend_resp.headers() {
                if let Ok(name) = HeaderName::from_bytes(k.as_str().as_bytes()) {
                    if let Ok(val) = HeaderValue::from_bytes(v.as_bytes()) {
                        builder = builder.header(name, val);
                    }
                }
            }

            let stream = backend_resp.bytes_stream();
            let body = Body::from_stream(stream);

            builder.body(body).unwrap_or_else(|_| StatusCode::INTERNAL_SERVER_ERROR.into_response())
        }
        Err(e) => {
            // Le backend Python est indisponible / en cours de reboot
            if method == axum::http::Method::GET || method == axum::http::Method::HEAD {
                // Servir la page de live log / maintenance
                Html(FALLBACK_HTML).into_response()
            } else {
                let err_json = serde_json::json!({
                    "status": "restarting",
                    "message": format!("Le serveur rpinode est en cours de redémarrage : {}", e)
                });
                (StatusCode::SERVICE_UNAVAILABLE, axum::Json(err_json)).into_response()
            }
        }
    }
}
