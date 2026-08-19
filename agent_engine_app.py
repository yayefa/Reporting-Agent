try:
    from .agent import root_agent
except (ImportError, ValueError):
    from agent import root_agent

__all__ = ["root_agent"]
