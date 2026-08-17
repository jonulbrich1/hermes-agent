from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .util import redact_dict


DEFAULTS = {
    "host": "127.0.0.1",
    "port": 8765,
    "auto_open_browser": True,
    "idle_growth_enabled": False,
    "idle_delay_seconds": 15,
    "idle_max_source_fetches": 2,
    "task_max_learning_rounds": 4,
    "auto_review_user_tasks": True,
    "auto_review_idle_every": 5,
    "allow_private_web": False,
    "max_fetch_bytes": 2_500_000,
    "max_source_chars": 180_000,
    "max_sentences_per_source": 350,
    "web_provider": "auto",
    "local_corpus_dir": "",
    "wikipedia_language": "en",
    "max_active_weave_claims": 18,
    "max_core_bytes": 5 * 1024 * 1024 * 1024,
    "core_learning_enabled": True,
    "core_learning_rate": 0.08,
    "development_stage": "foundation",
    "user_display_name": "Jon",
    "semantic_agent_enabled": True,
    "semantic_endpoint": "http://127.0.0.1:8081/v1",
    "semantic_model": "Qwen3-0.6B-GGUF",
    "semantic_timeout_seconds": 120,
    "semantic_temperature": 0.15,
    "semantic_tool_budget": 8,
    "gate_retrieval_threshold": 0.58,
    "gate_semantic_confidence_floor": 0.55,
    "interaction_search_results": 4,
    "interaction_fetch_limit": 2,
}

PRIVATE_DEFAULTS = {
    "brave_search_api_key": "",
}


class AppConfig:
    def __init__(self, root: Path):
        self.root = root
        self.public_path = root / "config.json"
        self.private_path = root / "data" / "private_config.json"
        self.data = dict(DEFAULTS)
        self.private = dict(PRIVATE_DEFAULTS)
        self.load()

    def load(self) -> None:
        if self.public_path.exists():
            try:
                loaded = json.loads(self.public_path.read_text(encoding="utf-8"))
                # Ignore obsolete LLM/core-endpoint keys from older MVP configs.
                self.data.update({k: v for k, v in loaded.items() if k in DEFAULTS})
            except Exception:
                pass
        if self.private_path.exists():
            try:
                loaded = json.loads(self.private_path.read_text(encoding="utf-8"))
                self.private.update({k: v for k, v in loaded.items() if k in PRIVATE_DEFAULTS})
            except Exception:
                pass
        if os.getenv("ORGANIC_BRAVE_API_KEY"):
            self.private["brave_search_api_key"] = os.getenv("ORGANIC_BRAVE_API_KEY", "")

    def save(self) -> None:
        self.public_path.write_text(json.dumps(self.data, indent=2, sort_keys=True), encoding="utf-8")
        self.private_path.parent.mkdir(parents=True, exist_ok=True)
        self.private_path.write_text(json.dumps(self.private, indent=2, sort_keys=True), encoding="utf-8")
        try:
            os.chmod(self.private_path, 0o600)
        except Exception:
            pass

    def update(self, public: dict[str, Any] | None = None, private: dict[str, Any] | None = None) -> None:
        if public:
            for k, v in public.items():
                if k in DEFAULTS:
                    self.data[k] = v
        if private:
            for k, v in private.items():
                if k in PRIVATE_DEFAULTS:
                    self.private[k] = v
        self.save()

    def get(self, key: str, default=None):
        if key in self.data:
            return self.data.get(key, default)
        return self.private.get(key, default)

    def public_snapshot(self) -> dict:
        return redact_dict({"public": self.data, "private": self.private})
