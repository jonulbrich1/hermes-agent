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
T(
    "Organic defaults inherit the Hermes semantic model",
    env.get("ORGANIC_MODEL") == "inherit",
)
T("Organic defaults fail closed", env.get("ORGANIC_HERMES_MODE") == "1")
T(
    "Hermes role uses scoped semantic context",
    env.get("ORGANIC_SEMANTIC_INTERFACE") == "1",
)
T(
    "Organic defaults do not replace the configured Hermes model",
    "HERMES_MODEL" not in env,
)
T("Hermes toolsets default to all", env.get("HERMES_TUI_TOOLSETS") == "all")
T(
    "Organic runtime source path resolved",
    Path(paths["runtime_src"]).parts[-2:] == ("organic_runtime", "src"),
)
T(
    "MCP command is Python",
    Path(mcp_config.get("command", "")).name.lower() in {"python", "python.exe"},
)
T(
    "MCP launches organic runtime module",
    mcp_config.get("args") == ["-m", "organic_runtime", "mcp"],
)
T(
    "MCP includes Organic ask tool",
    "organic.ask" in mcp_config.get("tools", {}).get("include", []),
)
T(
    "MCP proxies to shared Organic runtime",
    mcp_config.get("env", {}).get("ORGANIC_RUNTIME_URL") == "http://127.0.0.1:8788",
)
T(
    "Fallback state keeps dashboard loadable",
    fallback.get("runtime", {}).get("status") == "error",
)
T(
    "Runtime bridge uses subprocess mode",
    api._bridge_status().get("mode") == "subprocess",
)
bridge_python = Path(api._bridge_status().get("python", ""))
T(
    "Runtime bridge uses Organic venv",
    "organic_runtime" in bridge_python.parts and ".venv" in bridge_python.parts,
)
T(
    "Runtime bridge reports shared-process reachability",
    "reachable" in api._bridge_status(),
)

js = (DASHBOARD / "dist" / "index.js").read_text(encoding="utf-8")
css = (DASHBOARD / "dist" / "style.css").read_text(encoding="utf-8")

T("Dashboard JS registers Organic plugin", 'register("organic-ai"' in js)
T("Dashboard JS calls plugin API", "/api/plugins/organic-ai" in js)
T(
    "Dashboard JS shows full pipeline",
    "Semantic Interface LLM" in js and "Memory Compiler / Validator" in js,
)
T(
    "Dashboard JS shows processor learning stats",
    "Processor experiences" in js
    and "Learned pathways" in js
    and "Capability frontier" in js,
)
T("Dashboard JS supports MCP registration", "/mcp/register" in js)
T("Dashboard JS supports review export", "/export" in js)
T("Dashboard CSS forces dark mode", "color-scheme: dark" in css)
T(
    "Dashboard CSS has no light root background",
    "#ffffff" not in css.lower() and "background: white" not in css.lower(),
)

env_bat = (ROOT / "ORGANIC_ENV.bat").read_text(encoding="utf-8")
run_bat = (ROOT / "RUN_ORGANIC_HERMES_GUI.bat").read_text(encoding="utf-8")
legacy_bat = (ROOT / "RUN_ORGANIC_GUI.bat").read_text(encoding="utf-8")
setup_bat = (ROOT / "SETUP_ORGANIC_HERMES.bat").read_text(encoding="utf-8")
env_sh = (ROOT / "ORGANIC_ENV.sh").read_text(encoding="utf-8")
run_sh = (ROOT / "RUN_ORGANIC_HERMES_GUI.sh").read_text(encoding="utf-8")
setup_sh = (ROOT / "SETUP_ORGANIC_HERMES.sh").read_text(encoding="utf-8")
configure_py = (ROOT / "scripts" / "configure_organic_hermes.py").read_text(
    encoding="utf-8"
)
ensure_runtime_py = (ROOT / "scripts" / "ensure_organic_runtime.py").read_text(
    encoding="utf-8"
)
conversation_loop_py = (ROOT / "agent" / "conversation_loop.py").read_text(
    encoding="utf-8"
)
pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
runtime_pyproject = (ROOT / "organic_runtime" / "pyproject.toml").read_text(
    encoding="utf-8"
)
mcp_server_py = (
    ROOT / "organic_runtime" / "src" / "organic_runtime" / "mcp_server.py"
).read_text(encoding="utf-8")

T("Environment seeds Hermes toolsets", "set HERMES_TUI_TOOLSETS=all" in env_bat)
T(
    "Environment does not replace the configured Hermes provider",
    "set CUSTOM_BASE_URL=" not in env_bat,
)
T(
    "Environment enables fail-closed Organic mode",
    "set ORGANIC_HERMES_MODE=1" in env_bat,
)
T(
    "Hermes conversation loop forwards original user turn",
    "user_message=original_user_message" in conversation_loop_py,
)
T("Hermes GUI runner starts dashboard", "hermes_cli.main dashboard" in run_bat)
T("Hermes GUI runner checks Hermes dashboard dependency", "fastapi, uvicorn" in run_bat)
T("Hermes GUI runner checks MCP dependency", "import fastapi, uvicorn, mcp" in run_bat)
T(
    "Hermes GUI runner requires Organic runtime venv",
    "organic_runtime\\.venv\\Scripts\\python.exe" in run_bat,
)
T(
    "Hermes GUI runner bypasses local npm engine block",
    "set npm_config_engine_strict=false" in run_bat,
)
T("Hermes GUI runner enables plugin", "configure_organic_hermes.py" in run_bat)
T(
    "Hermes GUI runner starts shared runtime before Chat",
    "ensure_organic_runtime.py" in run_bat,
)
T("Legacy GUI runner points at Hermes GUI", "RUN_ORGANIC_HERMES_GUI.bat" in legacy_bat)
T(
    "Standalone GUI runner preserved",
    "organic_runtime gui"
    in (ROOT / "RUN_ORGANIC_STANDALONE_GUI.bat").read_text(encoding="utf-8"),
)
T(
    "Standalone GUI reuses shared bridge port",
    "port 8788"
    in (ROOT / "RUN_ORGANIC_STANDALONE_GUI.bat").read_text(encoding="utf-8"),
)
T("Combined setup installs MCP support", "pip install mcp==1.28.1" in setup_bat)
T(
    "Combined setup builds Organic runtime venv",
    "organic_runtime\\setup_local.bat" in setup_bat,
)
T(
    "Combined setup enables plugin and configures MCP",
    "configure_organic_hermes.py" in setup_bat,
)
T(
    "Linux environment enables fail-closed Organic mode",
    'ORGANIC_HERMES_MODE="1"' in env_sh,
)
T("Linux GUI runner starts Hermes dashboard", "hermes_cli.main dashboard" in run_sh)
T(
    "Linux GUI runner starts shared runtime before Chat",
    "ensure_organic_runtime.py" in run_sh,
)
T(
    "Linux setup installs both environments",
    "organic_runtime/.venv/bin/python" in setup_sh and "/.venv/bin/python" in setup_sh,
)
T(
    "Cross-platform configurator enables Organic plugin",
    'cmd_enable("organic-ai"' in configure_py,
)
T(
    "Cross-platform configurator registers Organic MCP",
    '_save_mcp_server("organic-ai"' in configure_py,
)
T(
    "Cross-platform configurator preserves inherited semantic mode",
    '"ORGANIC_MODEL"' in configure_py and '"inherit"' in configure_py,
)
T(
    "Cross-platform configurator preserves Organic backend",
    '"ORGANIC_BACKEND"' in configure_py and '"mvp"' in configure_py,
)
T(
    "Cross-platform runtime starter uses POSIX sessions",
    'start_new_session=os.name != "nt"' in ensure_runtime_py,
)
T("Root project keeps Hermes OpenAI pin", "openai==2.24.0" in pyproject)
T("Organic runtime declares MCP SDK", "mcp==1.28.1" in runtime_pyproject)
T(
    "Organic MCP server uses FastMCP",
    "from mcp.server.fastmcp import FastMCP" in mcp_server_py,
)
T("Organic MCP server supports runtime proxy", "ORGANIC_RUNTIME_URL" in mcp_server_py)

print()
print(f"[RESULT] {passed}/{total} Organic dashboard checks passed")
if passed != total:
    raise SystemExit(1)
