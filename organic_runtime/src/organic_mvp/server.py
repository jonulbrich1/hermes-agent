from __future__ import annotations

import json
import logging
import mimetypes
import os
import threading
import urllib.parse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .config import AppConfig
from .db import MemoryDB
from .loggingx import AuditLog
from .memory import MemoryCompiler
from .review import ReviewExporter
from .scheduler import OrganicEngine


def rowdicts(rows):
    return [dict(r) for r in rows]


class OrganicHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, handler, app):
        super().__init__(address, handler)
        self.app = app


class OrganicRequestHandler(BaseHTTPRequestHandler):
    server_version = 'OrganicAI-MVP-SemanticGate/0.3'

    @property
    def app(self):
        return self.server.app

    def log_message(self, fmt, *args):
        self.app.logger.debug('HTTP %s - %s', self.address_string(), fmt % args)

    def _json(self, obj: Any, status: int = 200):
        data = json.dumps(obj, ensure_ascii=False, default=str).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(data)

    def _read_json(self) -> dict:
        n = int(self.headers.get('Content-Length') or 0)
        raw = self.rfile.read(n) if n else b'{}'
        try:
            return json.loads(raw.decode('utf-8'))
        except Exception:
            return {}

    def _serve_file(self, path: Path):
        if not path.exists() or not path.is_file():
            self.send_error(404)
            return
        data = path.read_bytes()
        ctype = mimetypes.guess_type(str(path))[0] or 'application/octet-stream'
        self.send_response(200)
        self.send_header('Content-Type', ctype + ('; charset=utf-8' if ctype.startswith('text/') else ''))
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        parsed = urllib.parse.urlsplit(self.path)
        path = parsed.path
        qs = urllib.parse.parse_qs(parsed.query)
        try:
            if path == '/':
                return self._serve_file(self.app.root / 'web' / 'index.html')
            if path.startswith('/static/'):
                rel = Path(path[len('/static/'):])
                if '..' in rel.parts:
                    return self.send_error(400)
                return self._serve_file(self.app.root / 'web' / rel)
            if path == '/api/state':
                return self._json(self.app.snapshot())
            if path == '/api/interaction':
                return self._json(self.app.interaction.status())
            if path == '/api/tasks':
                return self._json({'tasks': rowdicts(self.app.db.list_tasks(int(qs.get('limit', ['100'])[0])))})
            if path == '/api/conversation':
                return self._json({'messages': rowdicts(self.app.db.recent_conversation(int(qs.get('limit', ['200'])[0])))})
            if path == '/api/sources':
                return self._json({'sources': rowdicts(self.app.db.list_sources(int(qs.get('limit', ['100'])[0])))})
            if path == '/api/frontier':
                return self._json({'frontier': self.app.memory.frontier(int(qs.get('limit', ['50'])[0]))})
            if path == '/api/memory':
                return self._json(self.app.memory.graph_summary(int(qs.get('limit', ['50'])[0])))
            if path == '/api/user_claims':
                return self._json({'claims': rowdicts(self.app.db.list_user_claims(int(qs.get('limit', ['100'])[0])))})
            if path == '/api/core_learning':
                return self._json({'events': rowdicts(self.app.db.list_core_learning_events(int(qs.get('limit', ['200'])[0]))),
                                   'core': self.app.core.status()})
            if path == '/api/config':
                return self._json(self.app.config.public_snapshot())
            if path == '/api/logs':
                lines = max(20, min(2000, int(qs.get('lines', ['300'])[0])))
                log_path = self.app.root / 'data' / 'logs' / 'debug.log'
                if not log_path.exists():
                    return self._json({'lines': []})
                data = log_path.read_text(encoding='utf-8', errors='replace').splitlines()[-lines:]
                return self._json({'lines': data})
            self.send_error(404)
        except Exception as exc:
            self.app.logger.exception('GET %s failed: %s', path, exc)
            self._json({'error': str(exc)}, 500)

    def do_POST(self):
        parsed = urllib.parse.urlsplit(self.path)
        path = parsed.path
        body = self._read_json()
        try:
            if path == '/api/message':
                result = self.app.interaction.handle_message(str(body.get('text', '')))
                return self._json(result)
            if path == '/api/task':
                tid = self.app.engine.submit_user_task(str(body.get('text', '')))
                return self._json({'ok': True, 'task_id': tid})
            if path == '/api/information':
                uid, tid = self.app.engine.submit_user_information(str(body.get('text', '')))
                return self._json({'ok': True, 'user_claim_id': uid, 'task_id': tid})
            if path == '/api/url':
                tid = self.app.engine.submit_url(str(body.get('url', '')), str(body.get('note', '')))
                return self._json({'ok': True, 'task_id': tid})
            if path == '/api/idle':
                self.app.engine.set_idle_growth(bool(body.get('enabled')))
                return self._json({'ok': True, 'enabled': bool(body.get('enabled'))})
            if path == '/api/growth':
                cycles = int(body.get('cycles', 1))
                self.app.engine.request_growth_cycles(cycles)
                return self._json({'ok': True, 'cycles': cycles})
            if path == '/api/export':
                reason = str(body.get('reason') or 'manual_gui')
                out = self.app.review.export(reason)
                return self._json({'ok': True, 'path': out})
            if path == '/api/config':
                public = body.get('public') if isinstance(body.get('public'), dict) else {}
                private = body.get('private') if isinstance(body.get('private'), dict) else {}
                self.app.config.update(public=public, private=private)
                return self._json({'ok': True, 'config': self.app.config.public_snapshot(),
                                   'note': 'Restart the MVP after changing web or Processing Core learning settings.'})
            if path == '/api/shutdown':
                self._json({'ok': True})
                threading.Thread(target=self.app.shutdown, daemon=True).start()
                return
            self.send_error(404)
        except ValueError as exc:
            self._json({'error': str(exc)}, 400)
        except Exception as exc:
            self.app.logger.exception('POST %s failed: %s', path, exc)
            self._json({'error': str(exc)}, 500)
