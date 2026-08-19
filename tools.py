import os
import logging
from typing import Optional, Callable
from urllib.parse import urlparse
from google.adk.tools.mcp_tool import (
    McpToolset,
    SseConnectionParams,
    StreamableHTTPConnectionParams,
)
try:
    from .utils import get_id_token
except (ImportError, ValueError):
    from utils import get_id_token

logger = logging.getLogger(__name__)


def _default_tool_filter(tool, readonly_context=None) -> bool:
    """Filters out internal / duplicate health_check tools across MCP servers."""
    tool_name = getattr(tool, "name", "")
    return tool_name != "health_check"


def create_mcp_toolset(
    url: str,
    tool_name_prefix: Optional[str] = None,
    tool_filter: Optional[Callable] = None,
) -> McpToolset:
    """Creates an ADK McpToolset for the given MCP URL (Streamable HTTP or SSE)."""
    if not url:
        raise ValueError("MCP URL cannot be None or empty")

    url = url.strip().strip('"').strip("'").rstrip("/")
    # If URL is a root Cloud Run service URL, default to /mcp for Streamable HTTP
    if not url.endswith("/mcp") and not url.endswith("/sse"):
        url = f"{url}/mcp"

    parsed_url = urlparse(url)
    audience = f"{parsed_url.scheme}://{parsed_url.netloc}"

    def dynamic_jwt_header_provider(session_state=None):
        token = os.getenv("AUTH_TOKEN") or get_id_token(audience)
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        return headers

    initial_token = os.getenv("AUTH_TOKEN") or get_id_token(audience)
    initial_headers = {"Authorization": f"Bearer {initial_token}"} if initial_token else None

    # Filter out duplicate generic tools like health_check by default
    active_filter = tool_filter if tool_filter is not None else _default_tool_filter

    if url.endswith("/sse"):
        params = SseConnectionParams(url=url, headers=initial_headers)
        transport_type = "SSE"
    else:
        params = StreamableHTTPConnectionParams(url=url, headers=initial_headers)
        transport_type = "Streamable HTTP"

    if url.startswith("http://localhost") or url.startswith("http://127.0.0.1"):
        logger.info(f"Connecting to local {transport_type} MCP server at {url}")
        return McpToolset(
            connection_params=params,
            tool_name_prefix=tool_name_prefix,
            tool_filter=active_filter,
        )
    else:
        logger.info(f"Connecting to remote {transport_type} MCP server at {url}")
        return McpToolset(
            connection_params=params,
            header_provider=dynamic_jwt_header_provider,
            tool_name_prefix=tool_name_prefix,
            tool_filter=active_filter,
        )
