from __future__ import annotations

import json
import threading
import urllib.request

from organic_runtime.gui import GuiHandler, GuiServer


def test_health_endpoint_does_not_require_full_runtime_snapshot():
    server = GuiServer(("127.0.0.1", 0), GuiHandler, app=None)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        with urllib.request.urlopen(f"http://{host}:{port}/api/health", timeout=2) as response:
            payload = json.loads(response.read().decode("utf-8"))

        assert payload == {"status": "healthy"}
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
