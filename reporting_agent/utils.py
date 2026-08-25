import time
import socket
import logging
import subprocess
from urllib.parse import urlparse
from typing import Optional, Dict, Tuple

import google.auth
import google.auth.transport.requests
from google.oauth2 import id_token

logger = logging.getLogger(__name__)

# In-memory token cache: audience -> (token, expiry_timestamp)
_TOKEN_CACHE: Dict[str, Tuple[str, float]] = {}
_DEFAULT_CACHE_TTL = 3000.0  # 50 minutes (tokens are valid for 60 minutes)


def _is_metadata_server_reachable(timeout: float = 0.2) -> bool:
    """Quick socket check to verify if the GCP metadata server is reachable."""
    try:
        sock = socket.create_connection(("169.254.169.254", 80), timeout=timeout)
        sock.close()
        return True
    except (socket.timeout, OSError):
        return False


def get_id_token(url: str, cache_ttl: float = _DEFAULT_CACHE_TTL) -> Optional[str]:
    """Gets a Google ID token for the given audience (URL) with caching.

    Order of resolution:
    1. In-memory cache
    2. GCP Metadata Server (when running on Cloud Run, GKE, Vertex AI Agent Engine)
    3. gcloud auth print-identity-token with audience (service accounts)
    4. gcloud auth print-identity-token without audience (user accounts)
    """
    if not url:
        return None

    parsed = urlparse(url)
    audience = f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme and parsed.netloc else url.replace("/mcp", "").rstrip("/")

    # Check cache
    now = time.time()
    if audience in _TOKEN_CACHE:
        cached_token, expiry = _TOKEN_CACHE[audience]
        if now < expiry:
            return cached_token

    # 1. GCP Metadata Server (only if reachable)
    if _is_metadata_server_reachable(0.2):
        try:
            auth_req = google.auth.transport.requests.Request()
            token = id_token.fetch_id_token(auth_req, audience)
            if token:
                _TOKEN_CACHE[audience] = (token, now + cache_ttl)
                return token
        except Exception as e:
            logger.debug("Metadata server ID token fetch skipped: %s", e)

    # 2. Local gcloud for Service Accounts
    try:
        res = subprocess.run(
            ["gcloud", "auth", "print-identity-token", f"--audiences={audience}"],
            capture_output=True,
            text=True,
            check=True,
            timeout=2.0,
        )
        token = res.stdout.strip()
        if token:
            _TOKEN_CACHE[audience] = (token, now + cache_ttl)
            return token
    except Exception as e:
        logger.debug("gcloud identity token with audience failed: %s", e)

    # 3. Local gcloud for User Accounts
    try:
        res = subprocess.run(
            ["gcloud", "auth", "print-identity-token"],
            capture_output=True,
            text=True,
            check=True,
            timeout=2.0,
        )
        token = res.stdout.strip()
        if token:
            _TOKEN_CACHE[audience] = (token, now + cache_ttl)
            return token
    except Exception as e:
        logger.debug("gcloud identity token fetch failed: %s", e)

    return None
