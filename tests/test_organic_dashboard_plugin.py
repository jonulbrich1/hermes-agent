#!/usr/bin/env python3
from pathlib import Path
import importlib.util
import json
import sys


ROOT = Path(__file__).resolve().parents[1]
DASHBOARD = ROOT / "plugins" / "organic-ai" / "dashboard"


def check(name, condition, details=""):
    if condition:
        print(f"[PASS] {name}")
        return True
    print(f"[FAIL] {name}: {details}")
    return False


passed = 0
total = 0


def T(name, condition, details=""):
    global passed, total
    total += 1
    if check(name, condition, details):
        passed += 1


manifest = json.loads((DASHBOARD / "manifest.json").read_text(encoding="utf-8"))
T("Organic dashboard manifest name", manifest.get("name") == "organic-ai")
T("Organic dashboard tab path", manifest.get("tab", {}).get("path") == "/organic")
T("Organic dashboard API file declared", manifest.get("api") == "plugin_api.py")
T("Organic dashboard entry exists", (DASHBOARD / manifest["entry"]).exists())
T("Organic dashboard CSS exists", (DASHBOARD / manifest["css"]).exists())

spec = importlib.util.spec_from_file_location(
    "organic_dashboard_plugin_api",
    DASHBOARD / "plugin_api.py",
)
api = importlib.util.module_from_spec(spec)
sys.modules["organic_dashboard_plugin_api"] = api
spec.loader.exec_module(api)

env = api._default_env()
paths = api._organic_paths()
mcp_config = api._mcp_server_config()
fallback = api._fallback_state(RuntimeError("probe"))

T("Dashboard backend exposes router", hasattr(api, "router"))
T("Organic defaults select MVP backend", env.get("ORGANIC_BACKEND") == "mvp")
T("Organic defaults enable idle growth", env.get("ORGANIC_IDLE_GROWTH_ENABLED") == "1")
T("Organic defaults enable web", env.get("ORGANIC_WEB_PROVIDER") == "auto")
T("Organic defaults use Qwen semantic model", env.get("ORGANIC_MODEL") == "ollama:qwen3:0.6b")
T("Organic defaults fail closed", env.get("ORGANIC_HERMES_MODE") == "1")
T("Qwen role uses scoped semantic context", env.get("ORGANIC_SEMANTIC_INTERFACE") == "1")
T("Hermes chat defaults use local Qwen", env.get("HERMES_MODEL") == "qwen3:0.6b")
T("Hermes toolsets default to all", env.get("HERMES_TUI_TOOLSETS") == "all")
T("Organic runtime source path resolved", paths["runtime_src"].endswith("organic_runtime\\src"))
T("MCP command is Python", mcp_config.get("command", "").lower().endswith("python.exe"))
T("MCP launches organic runtime module", mcp_config.get("args") == ["-m", "organic_runtime", "mcp"])
T("MCP includes Organic ask tool", "organic.ask" in mcp_config.get("tools", {}).get("include", []))
T("MCP proxies to shared Organic runtime", mcp_config.get("env", {}).get("ORGANIC_RUNTIME_URL") == "http://127.0.0.1:8788")
T("Fallback state keeps dashboard loadable", fallback.get("runtime", {}).get("status") == "error")
T("Runtime bridge uses subprocess mode", api._bridge_status().get("mode") == "subprocess")
T("Runtime bridge uses Organic venv", api._bridge_status().get("python", "").endswith("organic_runtime\\.venv\\Scripts\\python.exe"))
T("Runtime bridge reports shared-process reachability", "reachable" in api._bridge_status())

js = (DASHBOARD / "dist" / "index.js").read_text(encoding="utf-8")
css = (DASHBOARD / "dist" / "style.css").read_text(encoding="utf-8")

T("Dashboard JS registers Organic plugin", 'register("organic-ai"' in js)
T("Dashboard JS calls plugin API", "/api/plugins/organic-ai" in js)
T("Dashboard JS shows full pipeline", "Semantic Interface LLM" in js and "Memory Compiler / Validator" in js)
T("Dashboard JS shows processor learning stats", "Processor experiences" in js and "Processor composites" in js)
T("Dashboard JS supports MCP registration", "/mcp/register" in js)
T("Dashboard JS supports review export", "/export" in js)
T("Dashboard CSS forces dark mode", "color-scheme: dark" in css)
T("Dashboard CSS has no light root background", "#ffffff" not in css.lower() and "background: white" not in css.lower())

env_bat = (ROOT / "ORGANIC_ENV.bat").read_text(encoding="utf-8")
run_bat = (ROOT / "RUN_ORGANIC_HERMES_GUI.bat").read_text(encoding="utf-8")
legacy_bat = (ROOT / "RUN_ORGANIC_GUI.bat").read_text(encoding="utf-8")
setup_bat = (ROOT / "SETUP_ORGANIC_HERMES.bat").read_text(encoding="utf-8")
pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
runtime_pyproject = (ROOT / "organic_runtime" / "pyproject.toml").read_text(encoding="utf-8")
mcp_server_py = (ROOT / "organic_runtime" / "src" / "organic_runtime" / "mcp_server.py").read_text(encoding="utf-8")

T("Environment seeds Hermes toolsets", "set HERMES_TUI_TOOLSETS=all" in env_bat)
T("Environment seeds custom Ollama provider", "set CUSTOM_BASE_URL=http://127.0.0.1:11434/v1" in env_bat)
T("Environment enables fail-closed Organic mode", "set ORGANIC_HERMES_MODE=1" in env_bat)
T("Hermes GUI runner starts dashboard", "hermes_cli.main dashboard" in run_bat)
T("Hermes GUI runner checks Hermes dashboard dependency", "fastapi, uvicorn" in run_bat)
T("Hermes GUI runner checks MCP dependency", "import fastapi, uvicorn, mcp" in run_bat)
T("Hermes GUI runner requires Organic runtime venv", "organic_runtime\\.venv\\Scripts\\python.exe" in run_bat)
T("Hermes GUI runner bypasses local npm engine block", "set npm_config_engine_strict=false" in run_bat)
T("Legacy GUI runner points at Hermes GUI", "RUN_ORGANIC_HERMES_GUI.bat" in legacy_bat)
T("Standalone GUI runner preserved", "organic_runtime gui" in (ROOT / "RUN_ORGANIC_STANDALONE_GUI.bat").read_text(encoding="utf-8"))
T("Standalone GUI reuses shared bridge port", "port 8788" in (ROOT / "RUN_ORGANIC_STANDALONE_GUI.bat").read_text(encoding="utf-8"))
T("Combined setup installs MCP support", "pip install mcp==1.28.1" in setup_bat)
T("Combined setup builds Organic runtime venv", "organic_runtime\\setup_local.bat" in setup_bat)
T("Root project keeps Hermes OpenAI pin", "openai==2.24.0" in pyproject)
T("Organic runtime declares MCP SDK", "mcp==1.28.1" in runtime_pyproject)
T("Organic MCP server uses FastMCP", "from mcp.server.fastmcp import FastMCP" in mcp_server_py)
T("Organic MCP server supports runtime proxy", "ORGANIC_RUNTIME_URL" in mcp_server_py)

print()
print(f"[RESULT] {passed}/{total} Organic dashboard checks passed")
if passed != total:
    raise SystemExit(1)
