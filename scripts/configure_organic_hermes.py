from __future__ import annotations

import json
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def runtime_python() -> Path:
    venv = ROOT / "organic_runtime" / ".venv"
    candidates = [venv / "Scripts" / "python.exe", venv / "bin" / "python"]
    return next((path for path in candidates if path.exists()), candidates[0 if os.name == "nt" else 1])


def organic_environment() -> dict[str, str]:
    organic_home = ROOT / "runtime" / "organic_home"
    runtime_src = str((ROOT / "organic_runtime" / "src").resolve())
    existing_pythonpath = os.environ.get("PYTHONPATH") or ""
    pythonpath = os.pathsep.join(
        [runtime_src, *[part for part in existing_pythonpath.split(os.pathsep) if part and part != runtime_src]]
    )
    return {
        "ORGANIC_PROJECT_ROOT": str(ROOT),
        "ORGANIC_HOME": str(organic_home),
        "ORGANIC_MVP_DATA_DIR": str(organic_home / "mvp"),
        "ORGANIC_TRACE_DIR": str(organic_home / "traces"),
        "ORGANIC_REVIEW_DIR": str(ROOT / "reveiw"),
        "ORGANIC_RUNTIME_URL": os.environ.get("ORGANIC_RUNTIME_URL", "http://127.0.0.1:8788"),
        "ORGANIC_BACKEND": os.environ.get("ORGANIC_BACKEND", "mvp"),
        "ORGANIC_IDLE_GROWTH_ENABLED": "1",
        "ORGANIC_BOOTSTRAP_GROWTH_ENABLED": os.environ.get(
            "ORGANIC_BOOTSTRAP_GROWTH_ENABLED", "1"
        ),
        "ORGANIC_WEB_PROVIDER": os.environ.get("ORGANIC_WEB_PROVIDER", "auto"),
        "ORGANIC_ALLOW_PRIVATE_WEB": os.environ.get("ORGANIC_ALLOW_PRIVATE_WEB", "0"),
        "ORGANIC_PROCESSOR_MAX_CYCLES": os.environ.get(
            "ORGANIC_PROCESSOR_MAX_CYCLES", "16"
        ),
        "ORGANIC_SEMANTIC_MODE": os.environ.get("ORGANIC_SEMANTIC_MODE", "pydantic"),
        "ORGANIC_HERMES_MODE": "1",
        "ORGANIC_SEMANTIC_INTERFACE": "1",
        "ORGANIC_HERMES_PLUGIN_ENABLED": "1",
        "ORGANIC_MODEL": os.environ.get("ORGANIC_MODEL", "ollama:qwen3:0.6b"),
        "OLLAMA_BASE_URL": os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434/v1"),
        "PYTHONPATH": pythonpath,
    }


def main() -> int:
    python = runtime_python()
    if not python.exists():
        raise SystemExit(f"Organic runtime Python is missing: {python}")

    from hermes_cli.mcp_config import _save_mcp_server
    from hermes_cli.plugins_cmd import cmd_enable

    cmd_enable("organic-ai", allow_tool_override=False)
    config = {
        "command": str(python.resolve()),
        "args": ["-m", "organic_runtime", "mcp"],
        "env": organic_environment(),
        "timeout": 180,
        "connect_timeout": 30,
        "tools": {
            "include": [
                "organic.ask",
                "organic.state",
                "organic.run_growth",
                "organic.set_idle_growth",
                "organic.export_review",
            ]
        },
    }
    if not _save_mcp_server("organic-ai", config):
        raise SystemExit("Hermes rejected the Organic MCP server configuration")
    print(
        json.dumps(
            {
                "plugin": "organic-ai",
                "plugin_enabled": True,
                "mcp_configured": True,
                "runtime_python": str(python),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
