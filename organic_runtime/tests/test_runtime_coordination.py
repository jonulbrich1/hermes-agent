from __future__ import annotations

import pytest

from organic_runtime import mcp_server
from organic_runtime.instance_lock import RuntimeAlreadyRunningError, RuntimeDataLock


def test_runtime_data_lock_rejects_a_second_owner(tmp_path):
    path = tmp_path / "mvp" / "data" / "runtime.lock"
    first = RuntimeDataLock(path)
    second = RuntimeDataLock(path)

    first.acquire()
    try:
        with pytest.raises(RuntimeAlreadyRunningError):
            second.acquire()
    finally:
        first.release()

    second.acquire()
    second.release()


def test_mcp_server_proxies_to_the_shared_runtime(monkeypatch):
    calls = []

    def fake_request(base_url, method, path, body=None):
        calls.append((base_url, method, path, body))
        if path == "/api/state":
            return {"runtime": {"status": "healthy"}, "rolling_context": {"summary": "active"}}
        return {"ok": True, "path": path, "body": body}

    monkeypatch.setenv("ORGANIC_RUNTIME_URL", "http://127.0.0.1:8788")
    monkeypatch.setattr(mcp_server, "_runtime_request", fake_request)
    server = mcp_server.OrganicMcpServer()

    assert server.runtime is None
    assert server.call_tool("organic.ask", {"text": "hello"})["path"] == "/api/message"
    assert server.call_tool("organic.run_growth", {"cycles": 3})["body"] == {"cycles": 3}
    assert server.resource("organic://rolling_context") == {"summary": "active"}
    assert calls[0] == (
        "http://127.0.0.1:8788",
        "POST",
        "/api/message",
        {"text": "hello"},
    )
