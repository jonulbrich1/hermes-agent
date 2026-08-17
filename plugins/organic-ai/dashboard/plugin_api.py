"""Organic AI dashboard plugin backend.

Mounted by the Hermes dashboard at /api/plugins/organic-ai/.
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from typing import Any
import urllib.error
import urllib.request

try:
    from fastapi import APIRouter, HTTPException
except Exception:  # Allows lightweight import checks without dashboard deps.
    class APIRouter:  # type: ignore[no-redef]
        def get(self, *_args, **_kwargs):
            return lambda fn: fn

        def post(self, *_args, **_kwargs):
            return lambda fn: fn

    class HTTPException(Exception):  # type: ignore[no-redef]
        def __init__(self, status_code: int = 500, detail: str = "") -> None:
            super().__init__(detail)
            self.status_code = status_code
            self.detail = detail


router = APIRouter()
_LOG = logging.getLogger("hermes_cli.web_server.organic_ai")
_APP: Any | None = None
_APP_ERROR: str | None = None
_APP_LOCK = threading.RLock()
_BRIDGE_PROCESS: subprocess.Popen | None = None
_BRIDGE_LOCK = threading.RLock()


def _project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _organic_paths() -> dict[str, str]:
    root = _project_root()
    home = Path(os.environ.get("ORGANIC_HOME") or root / "runtime" / "organic_home").resolve()
    return {
        "project_root": str(root),
        "home": str(home),
        "mvp_data_dir": str(Path(os.environ.get("ORGANIC_MVP_DATA_DIR") or home / "mvp").resolve()),
        "trace_dir": str(Path(os.environ.get("ORGANIC_TRACE_DIR") or home / "traces").resolve()),
        "review_dir": str(Path(os.environ.get("ORGANIC_REVIEW_DIR") or root / "reveiw").resolve()),
        "runtime_src": str((root / "organic_runtime" / "src").resolve()),
    }


def _default_env() -> dict[str, str]:
    paths = _organic_paths()
    return {
        "ORGANIC_PROJECT_ROOT": paths["project_root"],
        "ORGANIC_HOME": paths["home"],
        "ORGANIC_MVP_DATA_DIR": paths["mvp_data_dir"],
        "ORGANIC_TRACE_DIR": paths["trace_dir"],
        "ORGANIC_REVIEW_DIR": paths["review_dir"],
        "ORGANIC_BACKEND": "mvp",
        "ORGANIC_IDLE_GROWTH_ENABLED": "1",
        "ORGANIC_WEB_PROVIDER": "auto",
        "ORGANIC_PROCESSOR_MAX_CYCLES": "16",
        "ORGANIC_SEMANTIC_MODE": "pydantic",
        "ORGANIC_MODEL": "ollama:qwen3:0.6b",
        "ORGANIC_HERMES_MODE": "1",
        "ORGANIC_SEMANTIC_INTERFACE": "1",
        "OLLAMA_BASE_URL": "http://127.0.0.1:11434/v1",
        "HERMES_TUI_TOOLSETS": "all",
        "HERMES_MODEL": "qwen3:0.6b",
        "HERMES_INFERENCE_MODEL": "qwen3:0.6b",
        "HERMES_TUI_PROVIDER": "custom",
        "HERMES_INFERENCE_PROVIDER": "custom",
        "CUSTOM_BASE_URL": "http://127.0.0.1:11434/v1",
        "OPENAI_API_KEY": "no-key-required",
    }


def _prepare_env() -> None:
    root = _project_root()
    runtime_src = str((root / "organic_runtime" / "src").resolve())
    for key, value in _default_env().items():
        os.environ.setdefault(key, value)
    if runtime_src not in sys.path:
        sys.path.insert(0, runtime_src)
    existing = os.environ.get("PYTHONPATH") or ""
    parts = [part for part in existing.split(os.pathsep) if part]
    if runtime_src not in parts:
        os.environ["PYTHONPATH"] = os.pathsep.join([runtime_src, *parts])


def _safe_counts(paths: dict[str, str]) -> dict[str, int]:
    root = Path(paths["mvp_data_dir"])
    review_dir = Path(paths["review_dir"])
    return {
        "run_results": len(list((root / "run_results").glob("*.json"))) if root.exists() else 0,
        "review_packages": len(list((root / "review_packages").glob("*.zip"))) if root.exists() else 0,
        "review_mirror_packages": len(list(review_dir.glob("*.zip"))) if review_dir.exists() else 0,
    }


def _mcp_server_config() -> dict[str, Any]:
    _prepare_env()
    root = _project_root()
    runtime_src = str((root / "organic_runtime" / "src").resolve())
    venv_python = root / "organic_runtime" / ".venv" / "Scripts" / "python.exe"
    command = str(venv_python if venv_python.exists() else Path(sys.executable).resolve())
    env = _default_env()
    env["ORGANIC_RUNTIME_URL"] = _bridge_base_url()
    existing_pythonpath = os.environ.get("PYTHONPATH") or ""
    env["PYTHONPATH"] = os.pathsep.join(
        [runtime_src, *[part for part in existing_pythonpath.split(os.pathsep) if part and part != runtime_src]]
    )
    return {
        "command": command,
        "args": ["-m", "organic_runtime", "mcp"],
        "env": env,
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


def _bridge_host() -> str:
    return os.environ.get("ORGANIC_GUI_BRIDGE_HOST") or "127.0.0.1"


def _bridge_port() -> int:
    try:
        return int(os.environ.get("ORGANIC_GUI_BRIDGE_PORT") or "8788")
    except ValueError:
        return 8788


def _bridge_base_url() -> str:
    return f"http://{_bridge_host()}:{_bridge_port()}"


def _bridge_python() -> Path:
    return _project_root() / "organic_runtime" / ".venv" / "Scripts" / "python.exe"


def _bridge_status(error: str | None = None) -> dict[str, Any]:
    proc = _BRIDGE_PROCESS
    child_running = proc is not None and proc.poll() is None
    reachable = child_running or _bridge_alive()
    return {
        "mode": "subprocess",
        "url": _bridge_base_url(),
        "python": str(_bridge_python()),
        "venv_exists": _bridge_python().exists(),
        "process_known": proc is not None,
        "process_running": reachable,
        "reachable": reachable,
        "ownership": "dashboard_child" if child_running else "shared_external" if reachable else "none",
        "error": error,
    }


def _http_json(method: str, path: str, body: dict[str, Any] | None = None, timeout: float = 60.0) -> dict[str, Any]:
    url = _bridge_base_url() + path
    data = None
    headers: dict[str, str] = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(raw)
            detail = parsed.get("error") or parsed.get("detail") or raw
        except Exception:
            detail = raw or str(exc)
        raise RuntimeError(str(detail)) from exc


def _bridge_alive() -> bool:
    try:
        _http_json("GET", "/api/state", timeout=1.0)
        return True
    except Exception:
        return False


def _start_bridge() -> None:
    global _BRIDGE_PROCESS
    _prepare_env()
    with _BRIDGE_LOCK:
        if _bridge_alive():
            return
        if _BRIDGE_PROCESS is not None and _BRIDGE_PROCESS.poll() is None:
            return
        python = _bridge_python()
        if not python.exists():
            raise RuntimeError(
                "Organic runtime environment not found. Run organic_runtime\\setup_local.bat."
            )

        paths = _organic_paths()
        env = os.environ.copy()
        env.update(_default_env())
        env["PYTHONPATH"] = os.environ.get("PYTHONPATH") or paths["runtime_src"]

        log_dir = Path(paths["home"]) / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / "dashboard_bridge.log"
        flags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
        log_file = log_path.open("ab")
        try:
            _BRIDGE_PROCESS = subprocess.Popen(
                [
                    str(python),
                    "-m",
                    "organic_runtime",
                    "gui",
                    "--host",
                    _bridge_host(),
                    "--port",
                    str(_bridge_port()),
                    "--no-open",
                ],
                cwd=str(_project_root()),
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                creationflags=flags,
            )
        finally:
            log_file.close()

        deadline = time.time() + 25.0
        last_error = ""
        while time.time() < deadline:
            if _BRIDGE_PROCESS.poll() is not None:
                raise RuntimeError(
                    f"Organic runtime bridge exited with code {_BRIDGE_PROCESS.returncode}. "
                    f"See {log_path}."
                )
            try:
                _http_json("GET", "/api/state", timeout=1.0)
                return
            except Exception as exc:
                last_error = str(exc)
                time.sleep(0.4)
        raise RuntimeError(f"Organic runtime bridge did not become ready: {last_error}")


def _bridge_request(method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    _start_bridge()
    api_path = path if path.startswith("/api/") else "/api" + path
    return _http_json(method, api_path, body=body, timeout=180.0)


async def _runtime_get(path: str) -> dict[str, Any]:
    return await asyncio.to_thread(_bridge_request, "GET", path, None)


async def _runtime_post(path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    return await asyncio.to_thread(_bridge_request, "POST", path, body or {})


def _mcp_status() -> dict[str, Any]:
    configured: dict[str, Any] | None = None
    live_status: list[dict[str, Any]] = []
    discovery_in_flight = False
    try:
        from hermes_cli.mcp_config import _get_mcp_servers

        configured = (_get_mcp_servers() or {}).get("organic-ai")
    except Exception as exc:
        return {
            "configured": False,
            "connected": False,
            "error": str(exc),
            "status": [],
            "discovery_in_flight": False,
        }

    try:
        from tools.mcp_tool import get_mcp_status

        raw_status = get_mcp_status() or []
        if isinstance(raw_status, list):
            live_status = [entry for entry in raw_status if "organic" in str(entry.get("name") or "").lower()]
    except Exception:
        live_status = []

    try:
        from hermes_cli.mcp_startup import mcp_discovery_in_flight

        discovery_in_flight = bool(mcp_discovery_in_flight())
    except Exception:
        discovery_in_flight = False

    return {
        "configured": configured is not None,
        "enabled": configured is not None and configured.get("enabled", True) is not False,
        "connected": any(bool(entry.get("connected")) for entry in live_status),
        "status": live_status,
        "discovery_in_flight": discovery_in_flight,
        "config": {
            "command": configured.get("command"),
            "args": configured.get("args"),
            "tools": configured.get("tools"),
        }
        if configured
        else None,
    }


def _kick_mcp_discovery() -> None:
    try:
        from hermes_cli.mcp_startup import start_background_mcp_discovery

        start_background_mcp_discovery(
            logger=_LOG,
            thread_name="organic-dashboard-mcp-discovery",
        )
    except Exception:
        _LOG.debug("Organic MCP discovery kick failed", exc_info=True)


def _get_app() -> Any:
    global _APP, _APP_ERROR
    _prepare_env()
    with _APP_LOCK:
        if _APP is not None:
            return _APP
        try:
            from organic_runtime.gui import GuiApp

            _APP = GuiApp()
            _APP_ERROR = None
            return _APP
        except Exception as exc:
            _APP_ERROR = f"{type(exc).__name__}: {exc}"
            _LOG.exception("Organic dashboard runtime failed to initialize")
            raise


def _fallback_state(error: BaseException | str) -> dict[str, Any]:
    _prepare_env()
    paths = _organic_paths()
    env = _default_env()
    message = str(error)
    return {
        "runtime": {
            "status": "error",
            "requests_total": 0,
            "errors_total": 1,
            "last_error": message,
        },
        "backend": os.environ.get("ORGANIC_BACKEND") or env["ORGANIC_BACKEND"],
        "settings": {
            "semantic_mode": os.environ.get("ORGANIC_SEMANTIC_MODE") or env["ORGANIC_SEMANTIC_MODE"],
            "semantic_adapter": "unavailable",
            "model": os.environ.get("ORGANIC_MODEL") or env["ORGANIC_MODEL"],
            "ollama_base_url": os.environ.get("OLLAMA_BASE_URL") or env["OLLAMA_BASE_URL"],
        },
        "pydantic_ai_available": importlib.util.find_spec("pydantic_ai") is not None,
        "engine": {
            "running": False,
            "idle_growth_enabled": os.environ.get("ORGANIC_IDLE_GROWTH_ENABLED", "1") != "0",
            "external_user_active": False,
        },
        "counts": {},
        "web": {"mode": os.environ.get("ORGANIC_WEB_PROVIDER") or env["ORGANIC_WEB_PROVIDER"]},
        "rolling_context": {},
        "run_result_count": _safe_counts(paths)["run_results"],
        "review_package_count": _safe_counts(paths)["review_packages"],
        "hermes_review_package_count": _safe_counts(paths)["review_mirror_packages"],
        "startup_error": message,
        "bridge": _bridge_status(message),
    }


async def _call_app(method_name: str, *args: Any) -> Any:
    app = await asyncio.to_thread(_get_app)
    method = getattr(app, method_name)
    return await asyncio.to_thread(method, *args)


def _enhance_state(state: dict[str, Any]) -> dict[str, Any]:
    paths = _organic_paths()
    state["paths"] = paths
    state["mcp"] = _mcp_status()
    state["bridge"] = state.get("bridge") or _bridge_status()
    state["startup_error"] = _APP_ERROR or state.get("startup_error")
    state["hermes_chat"] = {
        "toolsets": os.environ.get("HERMES_TUI_TOOLSETS") or _default_env()["HERMES_TUI_TOOLSETS"],
        "provider": os.environ.get("HERMES_TUI_PROVIDER") or _default_env()["HERMES_TUI_PROVIDER"],
        "model": os.environ.get("HERMES_MODEL") or _default_env()["HERMES_MODEL"],
        "base_url": os.environ.get("CUSTOM_BASE_URL") or _default_env()["CUSTOM_BASE_URL"],
    }
    state["mcp_server_template"] = {
        "command": _mcp_server_config()["command"],
        "args": _mcp_server_config()["args"],
    }
    return state


@router.get("/health")
async def health() -> dict[str, Any]:
    return {"ok": True, "plugin": "organic-ai", "paths": _organic_paths(), "mcp": _mcp_status()}


@router.get("/state")
async def state() -> dict[str, Any]:
    try:
        snapshot = await _runtime_get("/state")
    except Exception as exc:
        snapshot = _fallback_state(exc)
    return _enhance_state(snapshot)


@router.get("/tasks")
async def tasks(limit: int = 20) -> dict[str, Any]:
    try:
        return await _runtime_get(f"/tasks?limit={max(1, min(int(limit), 200))}")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/sources")
async def sources(limit: int = 20) -> dict[str, Any]:
    try:
        return await _runtime_get(f"/sources?limit={max(1, min(int(limit), 200))}")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/memory")
async def memory(limit: int = 50) -> dict[str, Any]:
    try:
        return await _runtime_get(f"/memory?limit={max(1, min(int(limit), 500))}")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/tools")
async def tools() -> dict[str, Any]:
    try:
        data = await _runtime_get("/tools")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    data["mcp"] = _mcp_status()
    return data


@router.get("/packages")
async def packages(limit: int = 10) -> dict[str, Any]:
    try:
        data = await _runtime_get(f"/packages?limit={max(1, min(int(limit), 100))}")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    paths = _organic_paths()
    review_dir = Path(paths["review_dir"])
    mirrored = sorted(review_dir.glob("*.zip"), key=lambda item: item.stat().st_mtime, reverse=True) if review_dir.exists() else []
    data["mirrored_packages"] = [
        {
            "name": path.name,
            "path": str(path),
            "bytes": path.stat().st_size,
            "modified": path.stat().st_mtime,
        }
        for path in mirrored[: max(1, min(int(limit), 100))]
    ]
    return data


@router.post("/message")
async def message(body: dict[str, Any]) -> dict[str, Any]:
    text = str(body.get("text") or "")
    if not text.strip():
        raise HTTPException(status_code=400, detail="Message must not be empty.")
    try:
        return await _runtime_post("/message", {"text": text})
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/growth")
async def growth(body: dict[str, Any]) -> dict[str, Any]:
    cycles = max(1, min(int(body.get("cycles") or 1), 20))
    try:
        return await _runtime_post("/growth", {"cycles": cycles})
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/idle")
async def idle(body: dict[str, Any]) -> dict[str, Any]:
    enabled = bool(body.get("enabled"))
    try:
        return await _runtime_post("/idle", {"enabled": enabled})
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/export")
async def export(body: dict[str, Any]) -> dict[str, Any]:
    reason = str(body.get("reason") or "manual_dashboard")
    try:
        return await _runtime_post("/export", {"reason": reason})
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/mcp")
async def mcp() -> dict[str, Any]:
    return _mcp_status()


@router.post("/mcp/register")
async def register_mcp() -> dict[str, Any]:
    config = _mcp_server_config()
    try:
        from hermes_cli.mcp_config import _save_mcp_server

        if not _save_mcp_server("organic-ai", config):
            raise HTTPException(
                status_code=400,
                detail="Organic MCP server configuration was rejected by Hermes security validation.",
            )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    _kick_mcp_discovery()
    return {
        "ok": True,
        "server": "organic-ai",
        "mcp": _mcp_status(),
        "config": {
            "command": config["command"],
            "args": config["args"],
            "tools": config["tools"],
        },
    }


@router.post("/restart-runtime")
async def restart_runtime() -> dict[str, Any]:
    global _APP, _APP_ERROR, _BRIDGE_PROCESS
    with _APP_LOCK:
        app = _APP
        _APP = None
        _APP_ERROR = None
    if app is not None:
        closer = getattr(app, "close", None)
        if callable(closer):
            await asyncio.to_thread(closer)
    proc = _BRIDGE_PROCESS
    _BRIDGE_PROCESS = None
    if proc is not None and proc.poll() is None:
        try:
            await asyncio.to_thread(_http_json, "POST", "/api/shutdown", {}, 5.0)
        except Exception:
            proc.terminate()
    return await state()
