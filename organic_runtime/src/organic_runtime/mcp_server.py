from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import sys
from typing import Any
import urllib.error
import urllib.parse
import urllib.request

from organic_runtime.factory import build_runtime


def _runtime_request(
    base_url: str,
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(
        base_url.rstrip("/") + path,
        data=data,
        headers={"Content-Type": "application/json"},
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Organic runtime returned HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(
            f"Organic runtime is unavailable at {base_url}. Start RUN_ORGANIC_GUI.bat first."
        ) from exc
    if not isinstance(payload, dict):
        raise RuntimeError("Organic runtime returned a non-object response.")
    if payload.get("error"):
        raise RuntimeError(str(payload["error"]))
    return payload


class OrganicMcpServer:
    def __init__(self) -> None:
        self.runtime_url = (os.environ.get("ORGANIC_RUNTIME_URL") or "").rstrip("/")
        self.runtime = None
        if not self.runtime_url:
            with contextlib.redirect_stdout(sys.stderr):
                self.runtime = build_runtime()

    def close(self) -> None:
        if self.runtime is not None:
            with contextlib.redirect_stdout(sys.stderr):
                self.runtime.close()

    def system(self):
        if self.runtime is None:
            return None
        for component in (self.runtime.memory, self.runtime.core, self.runtime.growth):
            system = getattr(component, "system", None)
            if system is not None:
                return system
        return None

    def state(self) -> dict[str, Any]:
        if self.runtime_url:
            return _runtime_request(self.runtime_url, "GET", "/api/state")
        system = self.system()
        engine: dict[str, Any] = {}
        rolling_context: dict[str, Any] = {}
        if system is not None:
            engine = system.engine.state()
            loader = getattr(system, "load_rolling_context", None)
            if callable(loader):
                rolling_context = loader("")
        return {
            "runtime": self.runtime.state.snapshot().model_dump(mode="json"),
            "backend": system.settings.backend if system else "mock",
            "semantic_mode": system.settings.semantic_mode if system else "unknown",
            "model": system.settings.model if system else None,
            "engine": engine,
            "rolling_context": rolling_context,
            "tools": [
                {
                    "name": descriptor.name,
                    "description": descriptor.description,
                    "allowed_routes": sorted(route.value for route in descriptor.allowed_routes),
                    "semantic_safe": descriptor.semantic_safe,
                }
                for descriptor in self.runtime.tools.descriptors()
            ],
        }

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if self.runtime_url:
            if name == "organic.ask":
                text = str(arguments.get("text") or "")
                if not text.strip():
                    raise ValueError("text is required")
                return _runtime_request(
                    self.runtime_url,
                    "POST",
                    "/api/message",
                    {"text": text},
                )
            if name == "organic.state":
                return self.state()
            if name == "organic.run_growth":
                cycles = max(1, min(int(arguments.get("cycles") or 1), 20))
                return _runtime_request(
                    self.runtime_url,
                    "POST",
                    "/api/growth",
                    {"cycles": cycles},
                )
            if name == "organic.set_idle_growth":
                return _runtime_request(
                    self.runtime_url,
                    "POST",
                    "/api/idle",
                    {"enabled": bool(arguments.get("enabled"))},
                )
            if name == "organic.export_review":
                reason = str(arguments.get("reason") or "manual_mcp")
                return _runtime_request(
                    self.runtime_url,
                    "POST",
                    "/api/export",
                    {"reason": reason},
                )
            raise KeyError(f"Unknown tool: {name}")

        if name == "organic.ask":
            text = str(arguments.get("text") or "")
            if not text.strip():
                raise ValueError("text is required")
            with contextlib.redirect_stdout(sys.stderr):
                response = asyncio.run(self.runtime.handle(text))
            return response.model_dump(mode="json")

        if name == "organic.state":
            return self.state()

        if name == "organic.run_growth":
            system = self.system()
            if system is None:
                raise RuntimeError("Growth is only available for the MVP backend.")
            cycles = max(1, min(int(arguments.get("cycles") or 1), 20))
            with contextlib.redirect_stdout(sys.stderr):
                system.engine.request_growth_cycles(cycles)
            return {"ok": True, "cycles": cycles, "state": self.state()}

        if name == "organic.set_idle_growth":
            system = self.system()
            if system is None:
                raise RuntimeError("Idle growth is only available for the MVP backend.")
            enabled = bool(arguments.get("enabled"))
            with contextlib.redirect_stdout(sys.stderr):
                system.engine.set_idle_growth(enabled)
            return {"ok": True, "enabled": enabled, "state": self.state()}

        if name == "organic.export_review":
            system = self.system()
            if system is None:
                raise RuntimeError("Review export is only available for the MVP backend.")
            reason = str(arguments.get("reason") or "manual_mcp")
            with contextlib.redirect_stdout(sys.stderr):
                path = system.export_review(reason)
            return {"ok": True, "path": path}

        raise KeyError(f"Unknown tool: {name}")

    def resource(self, uri: str) -> dict[str, Any]:
        if self.runtime_url:
            paths = {
                "organic://state": "/api/state",
                "organic://tasks": "/api/tasks?limit=100",
                "organic://sources": "/api/sources?limit=100",
                "organic://memory": "/api/memory?limit=100",
            }
            if uri == "organic://rolling_context":
                return self.state().get("rolling_context") or {}
            if uri in paths:
                return _runtime_request(self.runtime_url, "GET", paths[uri])
            raise KeyError(f"Unknown resource: {uri}")

        system = self.system()
        if uri == "organic://state":
            return self.state()
        if uri == "organic://tasks":
            return {"tasks": [dict(row) for row in system.db.list_tasks(100)]} if system else {"tasks": []}
        if uri == "organic://sources":
            return {"sources": [dict(row) for row in system.db.list_sources(100)]} if system else {"sources": []}
        if uri == "organic://memory":
            return system.memory.graph_summary(100) if system else {"concepts": [], "relations": []}
        if uri == "organic://rolling_context":
            loader = getattr(system, "load_rolling_context", None) if system else None
            return loader("") if callable(loader) else {}
        raise KeyError(f"Unknown resource: {uri}")


def _tools() -> list[dict[str, Any]]:
    return [
        {
            "name": "organic.ask",
            "description": "Send a user request through the Organic runtime, gate, growth path, memory, and Core.",
            "inputSchema": {
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
        },
        {
            "name": "organic.state",
            "description": "Read runtime, Organic Engine, growth, memory, model, web, and tool status.",
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "organic.run_growth",
            "description": "Request one or more Organic Engine growth cycles.",
            "inputSchema": {
                "type": "object",
                "properties": {"cycles": {"type": "integer", "minimum": 1, "maximum": 20}},
            },
        },
        {
            "name": "organic.set_idle_growth",
            "description": "Enable or disable always-on idle growth.",
            "inputSchema": {
                "type": "object",
                "properties": {"enabled": {"type": "boolean"}},
                "required": ["enabled"],
            },
        },
        {
            "name": "organic.export_review",
            "description": "Create a review ZIP containing logs, database reports, sources, run results, and config.",
            "inputSchema": {
                "type": "object",
                "properties": {"reason": {"type": "string"}},
            },
        },
    ]


def _resources() -> list[dict[str, str]]:
    return [
        {"uri": "organic://state", "name": "Organic Runtime State", "mimeType": "application/json"},
        {"uri": "organic://tasks", "name": "Organic Tasks", "mimeType": "application/json"},
        {"uri": "organic://sources", "name": "Organic Sources", "mimeType": "application/json"},
        {"uri": "organic://memory", "name": "Organic Memory Summary", "mimeType": "application/json"},
        {"uri": "organic://rolling_context", "name": "Organic Rolling Context", "mimeType": "application/json"},
    ]


def _read_message() -> dict[str, Any] | None:
    headers: dict[str, str] = {}
    while True:
        line = sys.stdin.buffer.readline()
        if line == b"":
            return None
        line = line.strip()
        if not line:
            break
        key, _, value = line.decode("ascii", errors="replace").partition(":")
        headers[key.lower()] = value.strip()

    length = int(headers.get("content-length") or 0)
    if length <= 0:
        return None
    body = sys.stdin.buffer.read(length)
    return json.loads(body.decode("utf-8"))


def _write_message(message: dict[str, Any]) -> None:
    payload = json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    sys.stdout.buffer.write(f"Content-Length: {len(payload)}\r\n\r\n".encode("ascii"))
    sys.stdout.buffer.write(payload)
    sys.stdout.buffer.flush()


def _text_result(obj: Any, is_error: bool = False) -> dict[str, Any]:
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(obj, indent=2, ensure_ascii=False, default=str),
            }
        ],
        "isError": is_error,
    }


def _handle(server: OrganicMcpServer, request: dict[str, Any]) -> dict[str, Any] | None:
    method = str(request.get("method") or "")
    request_id = request.get("id")
    params = request.get("params") if isinstance(request.get("params"), dict) else {}

    if request_id is None:
        return None

    try:
        if method == "initialize":
            result = {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}, "resources": {}},
                "serverInfo": {"name": "organic-ai-runtime", "version": "0.3.0"},
            }
        elif method == "tools/list":
            result = {"tools": _tools()}
        elif method == "tools/call":
            result = _text_result(
                server.call_tool(
                    str(params.get("name") or ""),
                    params.get("arguments") if isinstance(params.get("arguments"), dict) else {},
                )
            )
        elif method == "resources/list":
            result = {"resources": _resources()}
        elif method == "resources/read":
            uri = str(params.get("uri") or "")
            data = server.resource(uri)
            result = {
                "contents": [
                    {
                        "uri": uri,
                        "mimeType": "application/json",
                        "text": json.dumps(data, indent=2, ensure_ascii=False, default=str),
                    }
                ]
            }
        elif method == "ping":
            result = {}
        else:
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32601, "message": f"Method not found: {method}"},
            }
        return {"jsonrpc": "2.0", "id": request_id, "result": result}
    except Exception as exc:
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": -32000, "message": str(exc), "data": {"type": type(exc).__name__}},
        }


def _run_raw_mcp_server() -> int:
    server = OrganicMcpServer()
    try:
        while True:
            request = _read_message()
            if request is None:
                return 0
            response = _handle(server, request)
            if response is not None:
                _write_message(response)
    finally:
        server.close()


def _run_sdk_mcp_server() -> int:
    from mcp.server.fastmcp import FastMCP

    server = OrganicMcpServer()
    mcp = FastMCP(
        "organic-ai-runtime",
        instructions=(
            "Organic AI runtime tools. Send user requests through the Organic "
            "workflow and inspect growth, memory, and rolling cognition state."
        ),
    )

    @mcp.tool(
        name="organic.ask",
        description="Send a user request through the Organic runtime, gate, growth path, memory, and Core.",
    )
    def organic_ask(text: str) -> dict[str, Any]:
        return server.call_tool("organic.ask", {"text": text})

    @mcp.tool(
        name="organic.state",
        description="Read runtime, Organic Engine, growth, memory, model, web, and tool status.",
    )
    def organic_state() -> dict[str, Any]:
        return server.state()

    @mcp.tool(
        name="organic.run_growth",
        description="Request one or more Organic Engine growth cycles.",
    )
    def organic_run_growth(cycles: int = 1) -> dict[str, Any]:
        return server.call_tool("organic.run_growth", {"cycles": cycles})

    @mcp.tool(
        name="organic.set_idle_growth",
        description="Enable or disable always-on idle growth.",
    )
    def organic_set_idle_growth(enabled: bool) -> dict[str, Any]:
        return server.call_tool("organic.set_idle_growth", {"enabled": enabled})

    @mcp.tool(
        name="organic.export_review",
        description="Create a review ZIP containing logs, database reports, sources, run results, and config.",
    )
    def organic_export_review(reason: str = "manual_mcp") -> dict[str, Any]:
        return server.call_tool("organic.export_review", {"reason": reason})

    @mcp.resource(
        "organic://state",
        name="Organic Runtime State",
        mime_type="application/json",
    )
    def organic_state_resource() -> str:
        return json.dumps(server.resource("organic://state"), indent=2, ensure_ascii=False, default=str)

    @mcp.resource(
        "organic://tasks",
        name="Organic Tasks",
        mime_type="application/json",
    )
    def organic_tasks_resource() -> str:
        return json.dumps(server.resource("organic://tasks"), indent=2, ensure_ascii=False, default=str)

    @mcp.resource(
        "organic://sources",
        name="Organic Sources",
        mime_type="application/json",
    )
    def organic_sources_resource() -> str:
        return json.dumps(server.resource("organic://sources"), indent=2, ensure_ascii=False, default=str)

    @mcp.resource(
        "organic://memory",
        name="Organic Memory Summary",
        mime_type="application/json",
    )
    def organic_memory_resource() -> str:
        return json.dumps(server.resource("organic://memory"), indent=2, ensure_ascii=False, default=str)

    @mcp.resource(
        "organic://rolling_context",
        name="Organic Rolling Context",
        mime_type="application/json",
    )
    def organic_rolling_context_resource() -> str:
        return json.dumps(
            server.resource("organic://rolling_context"),
            indent=2,
            ensure_ascii=False,
            default=str,
        )

    try:
        mcp.run("stdio")
        return 0
    finally:
        server.close()


def run_mcp_server() -> int:
    try:
        return _run_sdk_mcp_server()
    except ModuleNotFoundError as exc:
        if exc.name != "mcp":
            raise
        return _run_raw_mcp_server()


def add_mcp_parser(subparsers: argparse._SubParsersAction) -> None:
    subparsers.add_parser("mcp", help="Run the Organic runtime MCP server over stdio")
