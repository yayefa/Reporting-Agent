"""Configuration module for SecOps Weekly Operations & Reporting Agent.

All settings are 100% variable-driven via environment variables and configuration objects.
Supports automated OpenID Connect (OIDC) ID token authentication with in-memory caching,
customizable tool prefixes, runtime parameters, and Vertex AI settings.
"""

import os
import re
import time
import socket
import logging
import subprocess
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse
from dotenv import load_dotenv

import requests
from google.auth.transport.requests import Request
from google.oauth2 import id_token as google_id_token

# Load environment variables from .env if present
load_dotenv()

# ==============================================================================
# Default Configuration Variables & Fallbacks
# ==============================================================================
DEFAULT_MODEL_NAME = "gemini-2.5-flash"
DEFAULT_LOCATION = "us-central1"
DEFAULT_AGENT_NAME = "reporting_agent"
DEFAULT_AGENT_DESCRIPTION = (
    "Autonomous SecOps Weekly Operations & Reporting Agent that synthesizes operational metrics, "
    "case resolution breakdown, playbook automation updates, and remediation actions from "
    "Google Security Operations (SecOps / Chronicle) and Google Threat Intelligence (GTI) "
    "into comprehensive executive reports."
)
DEFAULT_APP_NAME = "secops_reporting"
DEFAULT_SESSION_ID = "secops_reporting_session_001"
DEFAULT_USER_ID = "soc_lead"
DEFAULT_SECOPS_TOOL_PREFIX = "secops_"
DEFAULT_GTI_TOOL_PREFIX = "gti_"
DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8080

DEFAULT_METADATA_TIMEOUT_SECONDS = 0.5
DEFAULT_GCLOUD_TIMEOUT_SECONDS = 6.0
DEFAULT_TOKEN_CACHE_TTL_SECONDS = 3000  # 50 minutes (tokens valid for 60 min)

# Force google-genai / ADK to use Vertex AI
os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "true")

logger = logging.getLogger(__name__)

# In-memory token cache: {audience: (token, expiry_timestamp)}
_TOKEN_CACHE: Dict[str, tuple[str, float]] = {}


def sanitize_identifier(name: Optional[str], default: str = DEFAULT_AGENT_NAME) -> str:
    """Sanitizes a string to ensure it is a valid Python identifier.

    Replaces invalid characters (hyphens, spaces, periods) with underscores
    and ensures it starts with a letter or underscore.
    """
    if not name or not name.strip():
        return default
    clean = name.strip()
    sanitized = re.sub(r"[^a-zA-Z0-9_]", "_", clean)
    if not re.match(r"^[a-zA-Z_]", sanitized):
        sanitized = f"agent_{sanitized}"
    sanitized = re.sub(r"_+", "_", sanitized).strip("_")
    if not sanitized or not sanitized.isidentifier():
        return default
    return sanitized


def sanitize_tool_prefix(prefix: Optional[str], default: str = "secops_") -> str:
    """Sanitizes a tool name prefix so generated tool names are valid Python identifiers."""
    if not prefix or not prefix.strip():
        return default
    clean = re.sub(r"[^a-zA-Z0-9_]", "_", prefix.strip())
    clean = re.sub(r"_+", "_", clean)
    if not clean.endswith("_"):
        clean = f"{clean}_"
    if not re.match(r"^[a-zA-Z_]", clean):
        clean = f"tool_{clean}"
    return clean


def _get_first_env(*keys: str, default: Optional[str] = None) -> Optional[str]:
    """Returns the value of the first environment variable that is set and non-empty."""
    for key in keys:
        val = os.getenv(key)
        if val is not None and val.strip():
            return val.strip()
    return default


def _normalize_mcp_url(url: Optional[str]) -> str:
    """Ensures a Cloud Run MCP URL is clean and ends with /mcp."""
    if not url:
        return ""
    clean_url = url.strip().strip('"').strip("'")
    if not clean_url:
        return ""
    return f"{clean_url.rstrip('/')}/mcp" if not clean_url.endswith("/mcp") else clean_url


def _get_base_audience(url: str) -> str:
    """Extracts the origin base URL (scheme://host) for the Cloud Run OIDC audience."""
    if not url:
        return ""
    parsed = urlparse(url)
    if parsed.scheme and parsed.netloc:
        return f"{parsed.scheme}://{parsed.netloc}"
    return url.replace("/mcp", "").rstrip("/")


def _resolve_service_url(
    url_or_name: Optional[str],
    region: Optional[str] = None,
    project: Optional[str] = None,
    timeout: float = DEFAULT_GCLOUD_TIMEOUT_SECONDS,
) -> str:
    """Resolves a service name or direct URL to a full Cloud Run endpoint URL."""
    if not url_or_name:
        return ""
    url_or_name = url_or_name.strip()
    if url_or_name.startswith("http://") or url_or_name.startswith("https://"):
        return url_or_name

    try:
        cmd = ["gcloud", "run", "services", "describe", url_or_name, "--format=value(status.url)"]
        if region:
            cmd.extend(["--region", region])
        if project:
            cmd.extend(["--project", project])
        res = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=timeout)
        resolved = res.stdout.strip()
        if resolved:
            return resolved
    except Exception as exc:
        logger.debug("Could not resolve service name '%s' via gcloud: %s", url_or_name, exc)

    return url_or_name


def _is_metadata_server_reachable(timeout: float = 0.2) -> bool:
    """Quick socket check to verify if the GCP metadata server is reachable."""
    try:
        sock = socket.create_connection(("169.254.169.254", 80), timeout=timeout)
        sock.close()
        return True
    except (socket.timeout, OSError):
        return False


def get_cloud_run_id_token(
    target_url: str,
    explicit_token: Optional[str] = None,
    metadata_timeout: float = DEFAULT_METADATA_TIMEOUT_SECONDS,
    gcloud_timeout: float = DEFAULT_GCLOUD_TIMEOUT_SECONDS,
    cache_ttl: float = DEFAULT_TOKEN_CACHE_TTL_SECONDS,
) -> Optional[str]:
    """Retrieves an OIDC ID token for Cloud Run service invocation with caching.

    Order of resolution:
    1. Explicit auth token parameter / environment variable
    2. In-memory cached token (if not expired)
    3. GCP Metadata Server (Compute Engine, Cloud Run, GKE, Vertex AI Agent Engine)
    4. Local gcloud CLI fallback (local development)
    """
    if explicit_token and explicit_token.strip():
        return explicit_token.strip()

    audience = _get_base_audience(target_url)
    if not audience or "internal" in audience or audience.startswith("http://localhost"):
        return None

    # Check cached token
    now = time.time()
    if audience in _TOKEN_CACHE:
        cached_token, expiry = _TOKEN_CACHE[audience]
        if now < expiry:
            return cached_token

    # 1. GCP Metadata Server (only if reachable)
    if _is_metadata_server_reachable(metadata_timeout):
        try:
            session = requests.Session()
            session.mount("http://", requests.adapters.HTTPAdapter(max_retries=0))
            auth_req = Request(session=session)
            token = google_id_token.fetch_id_token(auth_req, audience)
            if token:
                _TOKEN_CACHE[audience] = (token, now + cache_ttl)
                return token
        except Exception as exc:
            logger.debug("Metadata server token fetch skipped for audience %s: %s", audience, exc)

    # 2. Local gcloud CLI fallback
    try:
        res = subprocess.run(
            ["gcloud", "auth", "print-identity-token", f"--audiences={audience}"],
            capture_output=True,
            text=True,
            check=True,
            timeout=gcloud_timeout,
        )
        token = res.stdout.strip()
        if token:
            _TOKEN_CACHE[audience] = (token, now + cache_ttl)
            return token
    except Exception as exc:
        logger.debug("gcloud CLI auth token fetch failed for audience %s: %s", audience, exc)

    return None


def get_auth_headers(target_url: str, explicit_token: Optional[str] = None) -> Dict[str, str]:
    """Generates the Authorization header dictionary if an ID token is resolved."""
    if not target_url:
        return {}
    token = get_cloud_run_id_token(target_url, explicit_token)
    return {"Authorization": f"Bearer {token}"} if token else {}


# ==============================================================================
# Configuration Data Classes
# ==============================================================================

@dataclass
class VertexAIConfig:
    """Vertex AI platform and model configuration."""
    project_id: Optional[str] = field(
        default_factory=lambda: _get_first_env(
            "GOOGLE_CLOUD_PROJECT", "GCP_PROJECT", "PROJECT_ID", "GOOGLE_CLOUD_PROJECT_ID"
        )
    )
    location: str = field(
        default_factory=lambda: _get_first_env(
            "GOOGLE_CLOUD_LOCATION", "GOOGLE_CLOUD_REGION", "GCP_REGION", "REGION",
            default=DEFAULT_LOCATION
        )
    )
    model_name: str = field(
        default_factory=lambda: _get_first_env(
            "SECOPS_AGENT_MODEL", "AGENT_MODEL", "MODEL_NAME", "GEMINI_MODEL",
            default=DEFAULT_MODEL_NAME
        )
    )
    use_vertex_ai: bool = field(
        default_factory=lambda: _get_first_env("GOOGLE_GENAI_USE_VERTEXAI", default="true").lower() == "true"
    )
    use_client_certificate: bool = field(
        default_factory=lambda: _get_first_env("GOOGLE_API_USE_CLIENT_CERTIFICATE", default="false").lower() == "true"
    )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "project_id": self.project_id,
            "location": self.location,
            "model_name": self.model_name,
            "use_vertex_ai": self.use_vertex_ai,
            "use_client_certificate": self.use_client_certificate,
        }


@dataclass
class SecOpsConfig:
    """Google Security Operations (Chronicle SecOps) MCP configuration."""
    url: str = ""
    service_name: Optional[str] = None
    auth_token: Optional[str] = None
    tool_prefix: str = DEFAULT_SECOPS_TOOL_PREFIX

    @classmethod
    def from_env(cls, region: Optional[str] = None, project: Optional[str] = None) -> "SecOpsConfig":
        raw_url = _get_first_env("SECOPS_URL", "SECOPS_MCP_URL")
        service_name = _get_first_env("SECOPS_SERVICE_NAME")
        auth_token = _get_first_env("SECOPS_AUTH_TOKEN", "SECOPS_ID_TOKEN")
        tool_prefix = sanitize_tool_prefix(
            _get_first_env("SECOPS_TOOL_PREFIX", default=DEFAULT_SECOPS_TOOL_PREFIX),
            default=DEFAULT_SECOPS_TOOL_PREFIX,
        )

        target = raw_url or service_name or ""
        resolved_url = _resolve_service_url(target, region=region, project=project) if target else ""

        return cls(
            url=_normalize_mcp_url(resolved_url),
            service_name=service_name,
            auth_token=auth_token,
            tool_prefix=tool_prefix,
        )

    @property
    def headers(self) -> Dict[str, str]:
        """Dynamically resolves authorization headers."""
        return get_auth_headers(self.url, self.auth_token)

    def get_headers(self) -> Dict[str, str]:
        """Callable helper for lazy header evaluation in MCP toolsets."""
        return self.headers

    def to_dict(self) -> Dict[str, Any]:
        return {
            "url": self.url,
            "service_name": self.service_name,
            "auth_token_set": bool(self.auth_token),
            "tool_prefix": self.tool_prefix,
        }


@dataclass
class GTIConfig:
    """Google Threat Intelligence (GTI) MCP configuration."""
    url: str = ""
    service_name: Optional[str] = None
    auth_token: Optional[str] = None
    tool_prefix: str = DEFAULT_GTI_TOOL_PREFIX

    @classmethod
    def from_env(cls, region: Optional[str] = None, project: Optional[str] = None) -> "GTIConfig":
        raw_url = _get_first_env("GTI_URL", "GTI_MCP_URL")
        service_name = _get_first_env("GTI_SERVICE_NAME")
        auth_token = _get_first_env("GTI_AUTH_TOKEN", "GTI_ID_TOKEN")
        tool_prefix = sanitize_tool_prefix(
            _get_first_env("GTI_TOOL_PREFIX", default=DEFAULT_GTI_TOOL_PREFIX),
            default=DEFAULT_GTI_TOOL_PREFIX,
        )

        target = raw_url or service_name or ""
        resolved_url = _resolve_service_url(target, region=region, project=project) if target else ""

        return cls(
            url=_normalize_mcp_url(resolved_url),
            service_name=service_name,
            auth_token=auth_token,
            tool_prefix=tool_prefix,
        )

    @property
    def headers(self) -> Dict[str, str]:
        """Dynamically resolves authorization headers."""
        return get_auth_headers(self.url, self.auth_token)

    def get_headers(self) -> Dict[str, str]:
        """Callable helper for lazy header evaluation in MCP toolsets."""
        return self.headers

    def to_dict(self) -> Dict[str, Any]:
        return {
            "url": self.url,
            "service_name": self.service_name,
            "auth_token_set": bool(self.auth_token),
            "tool_prefix": self.tool_prefix,
        }


@dataclass
class RuntimeConfig:
    """Agent runtime, session, and execution parameters."""
    agent_name: str = field(
        default_factory=lambda: sanitize_identifier(
            _get_first_env("AGENT_NAME", "AGENT_DISPLAY_NAME", default=DEFAULT_AGENT_NAME),
            default=DEFAULT_AGENT_NAME,
        )
    )
    agent_description: str = field(
        default_factory=lambda: _get_first_env(
            "AGENT_DESCRIPTION", default=DEFAULT_AGENT_DESCRIPTION
        )
    )
    app_name: str = field(
        default_factory=lambda: _get_first_env(
            "AGENT_APP_NAME", "APP_NAME", default=DEFAULT_APP_NAME
        )
    )
    session_id: str = field(
        default_factory=lambda: _get_first_env(
            "AGENT_SESSION_ID", "SESSION_ID", default=DEFAULT_SESSION_ID
        )
    )
    user_id: str = field(
        default_factory=lambda: _get_first_env(
            "AGENT_USER_ID", "USER_ID", default=DEFAULT_USER_ID
        )
    )
    host: str = field(
        default_factory=lambda: _get_first_env(
            "AGENT_HOST", "HOST", default=DEFAULT_HOST
        )
    )
    port: int = field(
        default_factory=lambda: int(_get_first_env("PORT", "AGENT_PORT", default=str(DEFAULT_PORT)))
    )
    enable_telemetry: bool = field(
        default_factory=lambda: _get_first_env(
            "GOOGLE_CLOUD_AGENT_ENGINE_ENABLE_TELEMETRY", default="true"
        ).lower() == "true"
    )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "agent_name": self.agent_name,
            "agent_description": self.agent_description,
            "app_name": self.app_name,
            "session_id": self.session_id,
            "user_id": self.user_id,
            "host": self.host,
            "port": self.port,
            "enable_telemetry": self.enable_telemetry,
        }


@dataclass
class AgentConfig:
    """Root configuration aggregating all platform, MCP, and runtime settings."""
    vertex: VertexAIConfig = field(default_factory=VertexAIConfig)
    secops: SecOpsConfig = None
    gti: GTIConfig = None
    runtime: RuntimeConfig = field(default_factory=RuntimeConfig)

    def __post_init__(self):
        if self.secops is None:
            self.secops = SecOpsConfig.from_env(
                region=self.vertex.location,
                project=self.vertex.project_id,
            )
        if self.gti is None:
            self.gti = GTIConfig.from_env(
                region=self.vertex.location,
                project=self.vertex.project_id,
            )

    def validate(self) -> List[str]:
        """Validates configuration and returns a list of warnings / missing settings."""
        warnings = []
        if not self.vertex.project_id:
            warnings.append("GCP Project ID not specified (set GOOGLE_CLOUD_PROJECT or GCP_PROJECT).")
        if not self.secops.url:
            warnings.append("SecOps MCP URL not configured (set SECOPS_URL or SECOPS_SERVICE_NAME).")
        if not self.gti.url:
            warnings.append("GTI MCP URL not configured (set GTI_URL or GTI_SERVICE_NAME).")
        return warnings

    def to_dict(self) -> Dict[str, Any]:
        return {
            "vertex": self.vertex.to_dict(),
            "secops": self.secops.to_dict(),
            "gti": self.gti.to_dict(),
            "runtime": self.runtime.to_dict(),
        }


def load_config() -> AgentConfig:
    """Loads and initializes a fresh AgentConfig instance from active environment variables."""
    return AgentConfig()


# Global default configuration instance
config: AgentConfig = load_config()
