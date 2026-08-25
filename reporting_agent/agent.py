import os
import re
import logging
from typing import Optional
from dotenv import load_dotenv
from google.adk.agents.llm_agent import Agent
try:
    from .tools import create_mcp_toolset
    from .prompts import get_agent_instruction
except (ImportError, ValueError):
    from tools import create_mcp_toolset
    from prompts import get_agent_instruction

logger = logging.getLogger(__name__)
logging.basicConfig(format="[%(levelname)s]: %(message)s", level=logging.INFO)

# Load .env
load_dotenv()


def _sanitize_name(name: Optional[str], default: str = "reporting_agent") -> str:
    """Sanitizes agent name to be a valid Python identifier."""
    if not name or not name.strip():
        return default
    sanitized = re.sub(r"[^a-zA-Z0-9_]", "_", name.strip())
    if not re.match(r"^[a-zA-Z_]", sanitized):
        sanitized = f"agent_{sanitized}"
    sanitized = re.sub(r"_+", "_", sanitized).strip("_")
    return sanitized if sanitized.isidentifier() else default


# Variable-driven configuration with environment variables
MODEL_NAME = os.getenv("SECOPS_AGENT_MODEL") or os.getenv("MODEL_NAME") or "gemini-2.5-flash"
AGENT_NAME = _sanitize_name(os.getenv("AGENT_NAME") or os.getenv("AGENT_DISPLAY_NAME") or "reporting_agent")
AGENT_DESCRIPTION = os.getenv(
    "AGENT_DESCRIPTION",
    "Autonomous SecOps Weekly Operations & Reporting Agent that synthesizes operational metrics, "
    "case resolution breakdown, playbook automation updates, and remediation actions from "
    "Google Security Operations (SecOps / Chronicle) and Google Threat Intelligence (GTI) "
    "into comprehensive executive reports."
)

# MCP URLs read purely from environment variables
gti_mcp_server_url = os.getenv("GTI_MCP_URL") or os.getenv("GTI_URL")
secops_mcp_server_url = os.getenv("SECOPS_MCP_URL") or os.getenv("SECOPS_URL")
secops_tool_prefix = os.getenv("SECOPS_TOOL_PREFIX")
gti_tool_prefix = os.getenv("GTI_TOOL_PREFIX")


def get_tools():
    """Builds the list of MCP toolsets dynamically from environment URLs."""
    tools = []
    if secops_mcp_server_url:
        try:
            tools.append(create_mcp_toolset(secops_mcp_server_url, tool_name_prefix=secops_tool_prefix))
        except Exception as e:
            logger.warning(f"Failed to create SecOps MCP toolset: {e}")

    if gti_mcp_server_url:
        try:
            tools.append(create_mcp_toolset(gti_mcp_server_url, tool_name_prefix=gti_tool_prefix))
        except Exception as e:
            logger.warning(f"Failed to create GTI MCP toolset: {e}")

    return tools


def create_agent(
    model: Optional[str] = None,
    instruction: Optional[str] = None,
    name: Optional[str] = None,
    description: Optional[str] = None,
) -> Agent:
    """Creates a configured Reporting Agent instance."""
    return Agent(
        model=model or MODEL_NAME,
        name=name or AGENT_NAME,
        description=description or AGENT_DESCRIPTION,
        instruction=get_agent_instruction(instruction),
        tools=get_tools(),
    )


# Root agent entrypoint for ADK CLI / Web UI / Agent Engine
root_agent = create_agent()
