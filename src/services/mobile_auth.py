"""Authentification des connexions de l'application mobile native au
rpinode : validation du `access_token` transmis par l'app (liste blanche
synchronisée via `/sync`, voir `fleet.py::_apply_mobile_access_pull`) et
gestion d'une session (cookie signé) pour éviter de revalider à chaque
requête de la WebView.

Voir `docs/mobile/CAHIER_DES_CHARGES_APP_MOBILE.md` (section 5) pour le
contexte complet.

Important : ce mécanisme n'ajoute une contrainte que sur les connexions
qui fournissent explicitement un `access_token` (l'app mobile). Il ne
modifie jamais le comportement existant pour les connexions qui n'en
fournissent pas (accès desktop/LAN historique, sans authentification) —
voir `web/server.py::_process_mobile_access`.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import time

from core.config import load_config, save_config
from core.database import get_db_connection

logger = logging.getLogger(__name__)

SESSION_COOKIE_NAME = "rpinode_mobile_session"
SESSION_LIFETIME_SECONDS = 8 * 3600  # 8h


def hash_token(token: str) -> str:
    """Même algorithme que côté `docs` (services/fleet.py::hash_token) :
    sha256 hex. Les jetons ne sont jamais comparés/stockés en clair."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _get_session_secret() -> str:
    """Secret local pour signer les cookies de session mobile, généré une
    fois et persisté dans data/config.json (même mécanisme que
    `fleet_token`)."""
    config = load_config()
    secret = config.get("mobile_session_secret") or ""
    if not secret:
        secret = secrets.token_hex(32)
        config["mobile_session_secret"] = secret
        save_config(config)
    return secret


def is_access_token_valid(token: str) -> bool:
    """Vérifie le jeton fourni par l'app mobile contre la liste blanche
    locale, synchronisée périodiquement via `/sync` (voir `fleet.py`)."""
    if not token:
        return False
    token_hash = hash_token(token)
    try:
        with get_db_connection() as conn:
            row = conn.execute(
                "SELECT 1 FROM mobile_access_tokens "
                "WHERE token_hash = ? AND expire_at > datetime('now')",
                (token_hash,),
            ).fetchone()
        return row is not None
    except Exception as e:
        logger.error(f"Erreur validation access_token mobile : {e}")
        return False


def create_session_cookie_value() -> str:
    """Crée une valeur de cookie signée (HMAC-SHA256), valable
    SESSION_LIFETIME_SECONDS. Format : '<expiry_unix>.<signature_hex>'."""
    secret = _get_session_secret()
    expiry = int(time.time()) + SESSION_LIFETIME_SECONDS
    payload = str(expiry)
    sig = hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


def verify_session_cookie(value: str) -> bool:
    """Vérifie la signature et l'expiration d'un cookie de session mobile."""
    if not value or "." not in value:
        return False
    payload, _, sig = value.partition(".")
    secret = _get_session_secret()
    expected_sig = hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected_sig):
        return False
    try:
        expiry = int(payload)
    except ValueError:
        return False
    return time.time() < expiry
