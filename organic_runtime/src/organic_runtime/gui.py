from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import os
import threading
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from organic_runtime.factory import build_runtime

HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Organic AI Runtime</title>
  <style>
    :root {
      color-scheme: dark;
      --bg: #11100e;
      --panel: #1b1a17;
      --panel-soft: #24221e;
      --line: #3a372f;
      --ink: #f1eee7;
      --muted: #aaa398;
      --blue: #4aa7b5;
      --green: #76b889;
      --amber: #d4a24f;
      --red: #e06f5f;
      --chip: #302d27;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: "Segoe UI", Arial, sans-serif;
      background: var(--bg);
      color: var(--ink);
      letter-spacing: 0;
    }
    button, textarea, input {
      font: inherit;
    }
    button {
      border: 1px solid var(--line);
      background: var(--panel);
      color: var(--ink);
      border-radius: 6px;
      padding: 8px 10px;
      cursor: pointer;
      min-height: 36px;
    }
    button.primary {
      background: var(--blue);
      color: #071113;
      border-color: var(--blue);
      font-weight: 700;
    }
    button:disabled {
      cursor: wait;
      opacity: .55;
    }
    .app {
      display: grid;
      grid-template-columns: 280px minmax(420px, 1fr) 360px;
      min-height: 100vh;
    }
    aside, main, section {
      min-width: 0;
    }
    .left, .right {
      border-right: 1px solid var(--line);
      background: #151411;
      padding: 16px;
      overflow: auto;
      max-height: 100vh;
    }
    .right {
      border-right: 0;
      border-left: 1px solid var(--line);
    }
    .main {
      display: grid;
      grid-template-rows: auto 1fr auto;
      min-height: 100vh;
    }
    .topbar {
      border-bottom: 1px solid var(--line);
      padding: 14px 18px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      background: var(--panel);
    }
    .brand {
      font-weight: 700;
      font-size: 18px;
    }
    .subtle {
      color: var(--muted);
      font-size: 13px;
    }
    .stack {
      display: grid;
      gap: 12px;
    }
    .block {
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel);
      padding: 12px;
    }
    .block h2 {
      font-size: 13px;
      text-transform: uppercase;
      color: var(--muted);
      margin: 0 0 10px;
      letter-spacing: 0;
    }
    .kv {
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 8px;
      font-size: 13px;
      padding: 4px 0;
      border-bottom: 1px solid #302d27;
    }
    .kv:last-child {
      border-bottom: 0;
    }
    .value {
      font-weight: 650;
      text-align: right;
      overflow-wrap: anywhere;
    }
    .pill {
      display: inline-flex;
      align-items: center;
      min-height: 24px;
      padding: 3px 8px;
      border-radius: 999px;
      background: var(--chip);
      color: var(--ink);
      font-size: 12px;
      white-space: nowrap;
    }
    .pill.good { background: #173323; color: var(--green); }
    .pill.warn { background: #392915; color: var(--amber); }
    .pill.bad { background: #3d1d18; color: var(--red); }
    .chat {
      padding: 18px;
      overflow: auto;
      display: flex;
      flex-direction: column;
      gap: 12px;
      background: var(--bg);
    }
    .msg {
      max-width: min(820px, 92%);
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel);
      padding: 11px 12px;
      line-height: 1.45;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
    }
    .msg.user {
      align-self: flex-end;
      background: #14343a;
      border-color: #286a75;
    }
    .msg.assistant {
      align-self: flex-start;
    }
    .msg.error {
      border-color: #7e392f;
      background: #311917;
      color: var(--red);
    }
    .meta-line {
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
      margin-top: 8px;
    }
    .composer {
      border-top: 1px solid var(--line);
      background: var(--panel);
      padding: 12px;
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 10px;
    }
    textarea {
      width: 100%;
      min-height: 46px;
      max-height: 160px;
      resize: vertical;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 10px;
      background: var(--panel-soft);
      color: var(--ink);
    }
    .list {
      display: grid;
      gap: 8px;
      max-height: 240px;
      overflow: auto;
    }
    .item {
      border-top: 1px solid #302d27;
      padding-top: 8px;
      font-size: 13px;
    }
    .item:first-child {
      border-top: 0;
      padding-top: 0;
    }
    .item-title {
      font-weight: 650;
      overflow-wrap: anywhere;
    }
    .json {
      border: 1px solid var(--line);
      background: #0c0c0b;
      color: var(--ink);
      border-radius: 8px;
      padding: 10px;
      min-height: 160px;
      max-height: 360px;
      overflow: auto;
      font-family: Consolas, monospace;
      font-size: 12px;
      white-space: pre-wrap;
    }
    .controls {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
    }
    @media (max-width: 1100px) {
      .app {
        grid-template-columns: 1fr;
      }
      .left, .right {
        max-height: none;
        border: 0;
        border-bottom: 1px solid var(--line);
      }
      .main {
        min-height: 70vh;
      }
    }
  </style>
</head>
<body>
  <div class="app">
    <aside class="left stack">
      <div>
        <div class="brand">Organic AI Runtime</div>
        <div class="subtle" id="url"></div>
      </div>
      <div class="block">
        <h2>Runtime</h2>
        <div id="runtime"></div>
      </div>
      <div class="block">
        <h2>Organic Engine</h2>
        <div id="engine"></div>
      </div>
      <div class="block">
        <h2>Memory</h2>
        <div id="memory"></div>
      </div>
      <div class="block">
        <h2>Controls</h2>
        <div class="controls">
          <button id="refresh">Refresh</button>
          <button id="growth">Run Growth</button>
          <button id="idle">Toggle Idle</button>
          <button id="export">Export Package</button>
        </div>
        <div class="subtle" id="exportStatus"></div>
      </div>
    </aside>

    <main class="main">
      <div class="topbar">
        <div>
          <div class="brand">Conversation</div>
          <div class="subtle" id="status">Starting runtime</div>
        </div>
        <span class="pill" id="route">idle</span>
      </div>
      <div class="chat" id="chat"></div>
      <div class="composer">
        <textarea id="text" placeholder="Ask Organic AI"></textarea>
        <button class="primary" id="send">Send</button>
      </div>
    </main>

    <section class="right stack">
      <div class="block">
        <h2>Last Pipeline</h2>
        <div id="pipeline"></div>
      </div>
      <div class="block">
        <h2>Tools</h2>
        <div class="list" id="tools"></div>
      </div>
      <div class="block">
        <h2>Sources</h2>
        <div class="list" id="sources"></div>
      </div>
      <div class="block">
        <h2>Run Packages</h2>
        <div class="list" id="packages"></div>
      </div>
      <div class="block">
        <h2>Recent Tasks</h2>
        <div class="list" id="tasks"></div>
      </div>
      <div class="block">
        <h2>Details</h2>
        <pre class="json" id="details">{}</pre>
      </div>
    </section>
  </div>

  <script>
    const $ = (id) => document.getElementById(id);
    let lastState = null;
    let busy = false;

    function setBusy(value) {
      busy = value;
      $("send").disabled = value;
      $("growth").disabled = value;
      $("idle").disabled = value;
      $("export").disabled = value;
      $("status").textContent = value ? "Working" : "Ready";
    }

    async function api(path, options = {}) {
      const response = await fetch(path, {
        headers: { "Content-Type": "application/json" },
        ...options
      });
      const data = await response.json();
      if (!response.ok) {
        throw new Error(data.error || response.statusText);
      }
      return data;
    }

    function kv(label, value) {
      return `<div class="kv"><span>${escapeHtml(label)}</span><span class="value">${escapeHtml(String(value ?? ""))}</span></div>`;
    }

    function pill(text, cls = "") {
      return `<span class="pill ${cls}">${escapeHtml(String(text ?? ""))}</span>`;
    }

    function escapeHtml(value) {
      return String(value)
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;");
    }

    function renderState(state) {
      lastState = state;
      $("url").textContent = location.origin;
      const runtime = state.runtime || {};
      const engine = state.engine || {};
      const counts = state.counts || {};
      const web = state.web || {};
      const settings = state.settings || {};
      const executive = engine.executive || engine.core || {};
      const processor = engine.processor || {};
      const seed = processor.v7_seed || {};
      const thought = processor.thought_policy || {};
      const rolling = state.rolling_context || {};
      $("runtime").innerHTML = [
        kv("Status", runtime.status || "unknown"),
        kv("Requests", runtime.requests_total || 0),
        kv("Backend", state.backend || "unknown"),
        kv("Semantic", settings.semantic_mode || "unknown"),
        kv("Model", settings.model || "none"),
        kv("PydanticAI", state.pydantic_ai_available ? "available" : "not installed"),
        kv("Web", web.mode || "unknown")
      ].join("");
      $("engine").innerHTML = [
        kv("Running", engine.running ? "yes" : "no"),
        kv("Current task", engine.current_task_id || "none"),
        kv("Context", rolling.active_objective || "none"),
        kv("Idle growth", engine.idle_growth_enabled ? "on" : "off"),
        kv("User active", engine.external_user_active ? "yes" : "no"),
        kv("Manual budget", engine.manual_growth_budget || 0),
        kv("Idle cycles", engine.completed_idle_cycles || 0),
        kv("Executive mode", executive.mode || executive.kind || "available"),
        kv("Processor", `${processor.version || "unknown"} / ${processor.mode || "unavailable"}`),
        kv("Processor experiences", processor.experience_count || 0),
        kv("Processor composites", processor.composite_count || 0),
        kv("V7 seed", seed.loaded ? `${seed.experience_count || 0} experiences` : "not loaded"),
        kv("Primitives", `${processor.executable_primitive_count || 0} live / ${processor.seed_primitive_count || 0} seed`),
        kv("Thought policy", thought.available ? `${thought.policy || "v9"} ${thought.mode || "shadow"}` : "unavailable"),
        kv("Processor state bytes", processor.state_bytes || 0)
      ].join("");
      $("memory").innerHTML = [
        kv("Sources", counts.sources || 0),
        kv("Claims", counts.claims || 0),
        kv("Concepts", counts.concepts || 0),
        kv("Relations", counts.relations || 0),
        kv("Pending tasks", counts.pending_tasks || 0),
        kv("Run results", state.run_result_count || 0),
        kv("Review packages", state.review_package_count || 0),
        kv("Hermes reveiw", state.hermes_review_package_count || 0)
      ].join("");
      if (!$("details").textContent || $("details").textContent === "{}") {
        $("details").textContent = JSON.stringify({ state, rolling_context: rolling }, null, 2);
      }
      $("idle").textContent = engine.idle_growth_enabled ? "Idle Off" : "Idle On";
    }

    function addMessage(role, text, metadata) {
      const div = document.createElement("div");
      div.className = `msg ${role}`;
      div.textContent = text;
      if (metadata) {
        const meta = document.createElement("div");
        meta.className = "meta-line";
        meta.innerHTML = [
          pill(metadata.route || "route"),
          pill(metadata.core_invoked ? "core" : "no core", metadata.core_invoked ? "good" : ""),
          pill(metadata.growth_invoked ? "growth" : "no growth", metadata.growth_invoked ? "good" : ""),
          metadata.hard_blocked ? pill("blocked", "bad") : "",
          metadata.semantic_completeness_complete === true ? pill("complete", "good") : ""
        ].join("");
        div.appendChild(meta);
      }
      $("chat").appendChild(div);
      $("chat").scrollTop = $("chat").scrollHeight;
    }

    function renderPipeline(response) {
      const meta = response.metadata || {};
      $("route").textContent = response.route || "idle";
      $("pipeline").innerHTML = [
        kv("Route", response.route || ""),
        kv("Core", meta.core_invoked ? "invoked" : "skipped"),
        kv("Growth", meta.growth_invoked ? "invoked" : "skipped"),
        kv("Evidence", meta.growth_evidence_count || 0),
        kv("Completeness", meta.semantic_completeness_complete === true ? "accepted" : "not accepted"),
        kv("Blocked", meta.hard_blocked ? "yes" : "no")
      ].join("");
      $("details").textContent = JSON.stringify(response, null, 2);
    }

    function renderList(id, rows, titleKey, subKey) {
      const node = $(id);
      if (!rows || !rows.length) {
        node.innerHTML = `<div class="subtle">None</div>`;
        return;
      }
      node.innerHTML = rows.slice(0, 20).map((row) => {
        const title = row[titleKey] || row.goal || row.title || row.task_id || "";
        const sub = row[subKey] || row.status || row.url || row.created_at || "";
        return `<div class="item"><div class="item-title">${escapeHtml(title)}</div><div class="subtle">${escapeHtml(sub)}</div></div>`;
      }).join("");
    }

    function renderTools(rows) {
      const node = $("tools");
      if (!rows || !rows.length) {
        node.innerHTML = `<div class="subtle">None</div>`;
        return;
      }
      node.innerHTML = rows.map((tool) => {
        const routes = (tool.allowed_routes || []).join(", ");
        const safe = tool.semantic_safe ? "semantic-safe" : "gate-controlled";
        return `<div class="item"><div class="item-title">${escapeHtml(tool.name)}</div><div class="subtle">${escapeHtml(routes)} · ${escapeHtml(safe)}</div></div>`;
      }).join("");
    }

    async function refresh() {
      const state = await api("/api/state");
      renderState(state);
      const tasks = await api("/api/tasks?limit=20");
      const sources = await api("/api/sources?limit=20");
      const tools = await api("/api/tools");
      const packages = await api("/api/packages?limit=10");
      renderList("tasks", tasks.tasks || [], "goal", "status");
      renderList("sources", sources.sources || [], "title", "url");
      renderTools([...(tools.tools || []), ...(tools.mvp_tools || [])]);
      renderList("packages", packages.packages || [], "name", "path");
    }

    async function send() {
      const text = $("text").value.trim();
      if (!text || busy) return;
      $("text").value = "";
      addMessage("user", text);
      setBusy(true);
      try {
        const response = await api("/api/message", {
          method: "POST",
          body: JSON.stringify({ text })
        });
        addMessage("assistant", response.answer || "", {
          route: response.route,
          ...response.metadata
        });
        renderPipeline(response);
        await refresh();
      } catch (error) {
        addMessage("error", String(error.message || error));
      } finally {
        setBusy(false);
      }
    }

    $("send").addEventListener("click", send);
    $("text").addEventListener("keydown", (event) => {
      if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        send();
      }
    });
    $("refresh").addEventListener("click", () => refresh().catch((e) => addMessage("error", e.message)));
    $("growth").addEventListener("click", async () => {
      setBusy(true);
      try {
        await api("/api/growth", { method: "POST", body: JSON.stringify({ cycles: 1 }) });
        await refresh();
      } catch (error) {
        addMessage("error", String(error.message || error));
      } finally {
        setBusy(false);
      }
    });
    $("idle").addEventListener("click", async () => {
      const enabled = !(lastState && lastState.engine && lastState.engine.idle_growth_enabled);
      setBusy(true);
      try {
        await api("/api/idle", { method: "POST", body: JSON.stringify({ enabled }) });
        await refresh();
      } catch (error) {
        addMessage("error", String(error.message || error));
      } finally {
        setBusy(false);
      }
    });
    $("export").addEventListener("click", async () => {
      setBusy(true);
      $("exportStatus").textContent = "Packaging run data";
      try {
        const result = await api("/api/export", {
          method: "POST",
          body: JSON.stringify({ reason: "manual_gui" })
        });
        $("exportStatus").textContent = result.path || "Package created";
        await refresh();
      } catch (error) {
        $("exportStatus").textContent = String(error.message || error);
      } finally {
        setBusy(false);
      }
    });

    refresh()
      .then(() => setBusy(false))
      .catch((error) => addMessage("error", String(error.message || error)));
    window.setInterval(() => {
      if (!busy) refresh().catch(() => {});
    }, 5000);
  </script>
</body>
</html>
"""


class GuiApp:
    def __init__(self) -> None:
        self.runtime = build_runtime()
        self.lock = threading.Lock()

    def close(self) -> None:
        self.runtime.close()

    def system(self):
        for component in (self.runtime.memory, self.runtime.core, self.runtime.growth):
            system = getattr(component, "system", None)
            if system is not None:
                return system
        return None

    def snapshot(self) -> dict[str, Any]:
        system = self.system()
        engine_state: dict[str, Any] = {}
        counts: dict[str, Any] = {}
        web: dict[str, Any] = {}
        rolling_context: dict[str, Any] = {}
        run_result_count = 0
        review_package_count = 0
        hermes_review_package_count = 0
        backend = "mock"
        if system is not None:
            engine_state = system.engine.state()
            counts = engine_state.get("counts") or {}
            web = engine_state.get("web") or system.provider_status()
            backend = system.settings.backend
            loader = getattr(system, "load_rolling_context", None)
            if callable(loader):
                rolling_context = loader("")
            run_result_count = len(list((system.root / "run_results").glob("*.json")))
            review_package_count = len(list((system.root / "review_packages").glob("*.zip")))
            project_root = os.environ.get("ORGANIC_PROJECT_ROOT")
            if project_root:
                hermes_review_package_count = len(
                    list((Path(project_root) / "reveiw").glob("*.zip"))
                )
        return {
            "runtime": self.runtime.state.snapshot().model_dump(mode="json"),
            "backend": backend,
            "settings": {
                "semantic_mode": system.settings.semantic_mode if system else "unknown",
                "semantic_adapter": getattr(
                    self.runtime.semantic,
                    "__class__",
                    type(self.runtime.semantic),
                ).__name__,
                "model": system.settings.model if system else None,
                "ollama_base_url": system.settings.ollama_base_url if system else None,
            },
            "pydantic_ai_available": importlib.util.find_spec("pydantic_ai") is not None,
            "engine": engine_state,
            "counts": counts,
            "web": web,
            "rolling_context": rolling_context,
            "run_result_count": run_result_count,
            "review_package_count": review_package_count,
            "hermes_review_package_count": hermes_review_package_count,
        }

    def tasks(self, limit: int) -> dict[str, Any]:
        system = self.system()
        if system is None:
            return {"tasks": []}
        return {"tasks": [dict(row) for row in system.db.list_tasks(limit)]}

    def sources(self, limit: int) -> dict[str, Any]:
        system = self.system()
        if system is None:
            return {"sources": []}
        return {"sources": [dict(row) for row in system.db.list_sources(limit)]}

    def memory(self, limit: int) -> dict[str, Any]:
        system = self.system()
        if system is None:
            return {"concepts": [], "relations": []}
        return system.memory.graph_summary(limit)

    def tools(self) -> dict[str, Any]:
        return {
            "tools": [
                {
                    "name": descriptor.name,
                    "description": descriptor.description,
                    "allowed_routes": sorted(route.value for route in descriptor.allowed_routes),
                    "semantic_safe": descriptor.semantic_safe,
                }
                for descriptor in self.runtime.tools.descriptors()
            ],
            "mvp_tools": [
                {
                    "name": "organic.ask",
                    "description": "Send a request through the full Organic runtime.",
                },
                {
                    "name": "organic.state",
                    "description": "Read runtime, Organic Engine, growth, memory, and web status.",
                },
                {
                    "name": "organic.run_growth",
                    "description": "Request one or more background growth cycles.",
                },
                {
                    "name": "organic.set_idle_growth",
                    "description": "Enable or disable idle growth.",
                },
                {
                    "name": "organic.export_review",
                    "description": "Create a ZIP package with traces, logs, DB reports, sources, run results, processor state, and redacted config.",
                },
            ],
        }

    def packages(self, limit: int) -> dict[str, Any]:
        system = self.system()
        if system is None:
            return {"packages": []}
        packages = sorted(
            (system.root / "review_packages").glob("*.zip"),
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )
        return {
            "packages": [
                {
                    "name": path.name,
                    "path": str(path),
                    "bytes": path.stat().st_size,
                    "modified": path.stat().st_mtime,
                }
                for path in packages[:limit]
            ]
        }

    def export_review(self, reason: str) -> dict[str, Any]:
        system = self.system()
        if system is None:
            raise RuntimeError("Review export is only available for the MVP backend.")
        path = system.export_review(reason or "manual_gui")
        return {"ok": True, "path": path}

    def ask(
        self,
        text: str,
        semantic_envelope: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not text.strip():
            raise ValueError("Message must not be empty.")
        with self.lock:
            response = asyncio.run(self.runtime.handle(text, semantic_envelope=semantic_envelope))
        return response.model_dump(mode="json")

    def set_idle(self, enabled: bool) -> dict[str, Any]:
        system = self.system()
        if system is None:
            raise RuntimeError("Idle growth is only available for the MVP backend.")
        system.engine.set_idle_growth(enabled)
        return self.snapshot()

    def growth(self, cycles: int) -> dict[str, Any]:
        system = self.system()
        if system is None:
            raise RuntimeError("Growth cycles are only available for the MVP backend.")
        system.engine.request_growth_cycles(max(1, min(int(cycles), 20)))
        return self.snapshot()


class GuiServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, server_address, handler_class, app: GuiApp) -> None:
        super().__init__(server_address, handler_class)
        self.app = app


class GuiHandler(BaseHTTPRequestHandler):
    server_version = "OrganicRuntimeGUI/0.1"

    @property
    def app(self) -> GuiApp:
        return self.server.app

    def log_message(self, fmt, *args):
        print("[gui] " + fmt % args)

    def send_json(self, obj: Any, status: int = 200) -> None:
        data = json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def send_html(self) -> None:
        data = HTML.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def read_json(self) -> dict[str, Any]:
        size = int(self.headers.get("Content-Length") or 0)
        if not size:
            return {}
        raw = self.rfile.read(size)
        return json.loads(raw.decode("utf-8"))

    def do_GET(self) -> None:
        parsed = urllib.parse.urlsplit(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)
        try:
            if path == "/":
                self.send_html()
                return
            if path == "/api/state":
                self.send_json(self.app.snapshot())
                return
            if path == "/api/tasks":
                self.send_json(self.app.tasks(_bounded_limit(query, 100)))
                return
            if path == "/api/sources":
                self.send_json(self.app.sources(_bounded_limit(query, 100)))
                return
            if path == "/api/memory":
                self.send_json(self.app.memory(_bounded_limit(query, 50)))
                return
            if path == "/api/tools":
                self.send_json(self.app.tools())
                return
            if path == "/api/packages":
                self.send_json(self.app.packages(_bounded_limit(query, 20)))
                return
            self.send_error(404)
        except Exception as exc:
            self.send_json({"error": str(exc), "type": type(exc).__name__}, 500)

    def do_POST(self) -> None:
        parsed = urllib.parse.urlsplit(self.path)
        path = parsed.path
        try:
            body = self.read_json()
            if path == "/api/message":
                supplied = body.get("semantic_envelope")
                if supplied is not None and not isinstance(supplied, dict):
                    raise ValueError("semantic_envelope must be an object")
                self.send_json(
                    self.app.ask(
                        str(body.get("text") or ""),
                        semantic_envelope=supplied,
                    )
                )
                return
            if path == "/api/idle":
                self.send_json(self.app.set_idle(bool(body.get("enabled"))))
                return
            if path == "/api/growth":
                self.send_json(self.app.growth(int(body.get("cycles") or 1)))
                return
            if path == "/api/export":
                self.send_json(self.app.export_review(str(body.get("reason") or "manual_gui")))
                return
            if path == "/api/shutdown":
                self.send_json({"ok": True})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return
            self.send_error(404)
        except ValueError as exc:
            self.send_json({"error": str(exc), "type": type(exc).__name__}, 400)
        except Exception as exc:
            self.send_json({"error": str(exc), "type": type(exc).__name__}, 500)


def _bounded_limit(query: dict[str, list[str]], default: int) -> int:
    try:
        return max(1, min(int(query.get("limit", [str(default)])[0]), 500))
    except ValueError:
        return default


def run_gui(host: str = "127.0.0.1", port: int = 8787, open_browser: bool = True) -> int:
    app = GuiApp()
    server = GuiServer((host, port), GuiHandler, app)
    url = f"http://{host}:{port}/"
    print(f"[gui] Organic AI Runtime GUI started at {url}")
    if open_browser:
        threading.Thread(
            target=lambda: (time.sleep(0.75), webbrowser.open(url)), daemon=True
        ).start()
    try:
        server.serve_forever()
        return 0
    except KeyboardInterrupt:
        print()
        return 0
    finally:
        server.server_close()
        app.close()
        print("[gui] Organic AI Runtime GUI stopped.")


def add_gui_parser(subparsers: argparse._SubParsersAction) -> None:
    gui = subparsers.add_parser("gui", help="Run the local browser GUI")
    gui.add_argument("--host", default="127.0.0.1", help="Host interface for the GUI")
    gui.add_argument("--port", type=int, default=8787, help="Port for the GUI")
    gui.add_argument(
        "--no-open", action="store_true", help="Start the GUI without opening a browser"
    )
