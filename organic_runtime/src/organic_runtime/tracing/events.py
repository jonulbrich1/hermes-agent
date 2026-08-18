from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any, Protocol


class TraceRecorder(Protocol):
    def record(self, trace_id: str, stage: str, payload: dict[str, Any]) -> None:
        ...


class NullTraceRecorder:
    def record(self, trace_id: str, stage: str, payload: dict[str, Any]) -> None:
        return None


class JsonlTraceRecorder:
    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()

    def record(self, trace_id: str, stage: str, payload: dict[str, Any]) -> None:
        now = datetime.now(timezone.utc)
        event = {
            "timestamp": now.isoformat(),
            "trace_id": trace_id,
            "stage": stage,
            "payload": payload,
        }
        output = self.directory / f"{now.date().isoformat()}.jsonl"
        line = json.dumps(event, ensure_ascii=True, default=str)
        with self._lock:
            with output.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
