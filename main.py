"""CLI entrypoint for running the SecOps Weekly Operations & Reporting Agent.

All configuration is variable-driven and can be supplied via environment variables,
configuration objects, or command-line flags.

Usage:
    python main.py
    python main.py "Generate SecOps Weekly Operations Report for 2026-08-10 to 2026-08-17"
    python main.py --file path/to/weekly_telemetry.json
    python main.py --show-config
    python main.py --interactive
"""

import sys
import json
import asyncio
import argparse
import logging
from typing import Optional

from google.adk.runners import Runner
from google.adk.sessions.in_memory_session_service import InMemorySessionService
from google.adk.sessions.session import Session

try:
    from .agent import create_agent, root_agent
    from .config import config, load_config, AgentConfig
except (ImportError, ValueError):
    from agent import create_agent, root_agent
    from config import config, load_config, AgentConfig


def print_banner(cfg: AgentConfig) -> None:
    """Prints an informative banner showing active runtime variables."""
    print("\n" + "=" * 80)
    print(f"🛡️  {cfg.runtime.agent_name.replace('_', ' ').title()} (Google ADK)")
    print("=" * 80)
    print(f"🤖 LLM Model:           {cfg.vertex.model_name}")
    print(f"🌍 GCP Project:         {cfg.vertex.project_id or 'Application Default Credentials'}")
    print(f"📍 GCP Location:        {cfg.vertex.location}")
    print(f"📡 SecOps MCP Endpoint: {cfg.secops.url or '<Not configured>'}")
    print(f"🌐 GTI MCP Endpoint:    {cfg.gti.url or '<Not configured>'}")
    print(f"👤 User ID:             {cfg.runtime.user_id}")
    print(f"🔖 Session ID:          {cfg.runtime.session_id}")
    print("=" * 80 + "\n")


async def run_reporting_query(
    query: str,
    cfg: Optional[AgentConfig] = None,
    session_id: Optional[str] = None,
    user_id: Optional[str] = None,
    app_name: Optional[str] = None,
) -> None:
    """Executes a single reporting query with the SecOps reporting agent.

    Args:
        query: Weekly report request, date range, or custom operations query.
        cfg: Optional AgentConfig instance. If None, uses active configuration.
        session_id: Optional session identifier override.
        user_id: Optional user identifier override.
        app_name: Optional application name override.
    """
    resolved_cfg = cfg or config or load_config()
    active_session_id = session_id or resolved_cfg.runtime.session_id
    active_user_id = user_id or resolved_cfg.runtime.user_id
    active_app_name = app_name or resolved_cfg.runtime.app_name

    # Create session
    session_service = InMemorySessionService()
    session = await session_service.create_session(
        app_name=active_app_name,
        user_id=active_user_id,
        session_id=active_session_id,
    )

    # Initialize agent for active config
    agent_instance = create_agent(
        name=resolved_cfg.runtime.agent_name,
        model=resolved_cfg.vertex.model_name,
    )

    runner = Runner(
        agent=agent_instance,
        session_service=session_service,
        app_name=active_app_name,
    )

    print_banner(resolved_cfg)
    print(f"📥 Reporting Request / Query:\n{query}\n")
    print("-" * 80)
    print("⚡ Synthesizing SecOps Weekly Operations Report...\n")

    from google.genai import types
    content = types.Content(
        role="user",
        parts=[types.Part.from_text(text=query)],
    )

    try:
        async for event in runner.run_async(
            session_id=session.id,
            user_id=active_user_id,
            new_message=content,
        ):
            if event.content and event.content.parts:
                for p in event.content.parts:
                    if hasattr(p, "text") and p.text:
                        print(p.text, end="", flush=True)

        print("\n\n" + "=" * 80)
        print("✅ SecOps Weekly Operations Report generated successfully.")
        print("=" * 80 + "\n")

    except Exception as exc:
        print(f"\n❌ Execution Error: {exc}", file=sys.stderr)
        warnings = resolved_cfg.validate()
        if warnings:
            print("\n⚠️ Configuration issues detected:", file=sys.stderr)
            for w in warnings:
                print(f"  - {w}", file=sys.stderr)


async def interactive_session(cfg: Optional[AgentConfig] = None) -> None:
    """Runs an interactive CLI session with the agent."""
    resolved_cfg = cfg or config or load_config()
    print_banner(resolved_cfg)
    print("💡 Type 'exit', 'quit', or 'q' to stop.")
    print("💡 Enter reporting request or command (e.g. 'Generate weekly operations report'):\n")

    while True:
        try:
            user_input = input("secops-report> ").strip()
            if not user_input:
                continue
            if user_input.lower() in ("exit", "quit", "q"):
                print("Exiting.")
                break

            await run_reporting_query(user_input, cfg=resolved_cfg)

        except (KeyboardInterrupt, EOFError):
            print("\nExiting.")
            break


def main() -> None:
    parser = argparse.ArgumentParser(
        description="SecOps Weekly Operations & Reporting Agent (Google ADK)",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("query", nargs="?", help="Reporting request or date range (e.g. 'Generate report for 2026-08-10 to 2026-08-17')")
    parser.add_argument("--file", "-f", help="Path to a JSON file or raw text file containing operational metrics")
    parser.add_argument("--interactive", "-i", action="store_true", help="Start interactive CLI session")
    parser.add_argument("--show-config", action="store_true", help="Display active configuration and exit")

    # Variable overrides
    parser.add_argument("--model", "-m", help="Override Vertex AI model name")
    parser.add_argument("--project", "-p", help="Override GCP Project ID")
    parser.add_argument("--region", "-r", help="Override GCP Region / Location")
    parser.add_argument("--secops-url", help="Override SecOps MCP endpoint URL")
    parser.add_argument("--gti-url", help="Override GTI MCP endpoint URL")
    parser.add_argument("--session-id", help="Override Agent Session ID")
    parser.add_argument("--user-id", help="Override Agent User ID")
    parser.add_argument("--app-name", help="Override Agent App Name")

    args = parser.parse_args()

    # Build active config with optional CLI flag overrides
    cfg = load_config()
    if args.model:
        cfg.vertex.model_name = args.model
    if args.project:
        cfg.vertex.project_id = args.project
    if args.region:
        cfg.vertex.location = args.region
    if args.secops_url:
        cfg.secops.url = args.secops_url
    if args.gti_url:
        cfg.gti.url = args.gti_url
    if args.session_id:
        cfg.runtime.session_id = args.session_id
    if args.user_id:
        cfg.runtime.user_id = args.user_id
    if args.app_name:
        cfg.runtime.app_name = args.app_name

    if args.show_config:
        print(json.dumps(cfg.to_dict(), indent=2))
        sys.exit(0)

    if args.interactive:
        asyncio.run(interactive_session(cfg=cfg))
        return

    # Read query from file, positional argument, or stdin
    if args.file:
        try:
            with open(args.file, "r") as f:
                query = f.read().strip()
        except Exception as exc:
            print(f"Error reading input file '{args.file}': {exc}", file=sys.stderr)
            sys.exit(1)
    elif args.query:
        query = args.query.strip()
    else:
        # Default prompt if none provided
        query = "Generate the SecOps Weekly Operations Report for the past 7 days based on current SecOps and GTI telemetry."

    asyncio.run(run_reporting_query(
        query=query,
        cfg=cfg,
        session_id=args.session_id,
        user_id=args.user_id,
        app_name=args.app_name,
    ))


if __name__ == "__main__":
    main()
