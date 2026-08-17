from __future__ import annotations

import time
from pathlib import Path

from organic_runtime.config import RuntimeSettings
from organic_runtime.contracts import Route
from organic_runtime.factory import build_runtime


REAL_SOURCES = Path(__file__).resolve().parent / "real_sources"


def _settings(tmp_path, *, bootstrap: bool = False, web_provider: str = "local_corpus"):
    return RuntimeSettings(
        semantic_mode="heuristic",
        backend="mvp",
        trace_dir=tmp_path / "traces",
        mvp_data_dir=tmp_path / "mvp",
        web_provider=web_provider,
        local_corpus_dir=REAL_SOURCES,
        idle_growth_enabled=True,
        idle_delay_seconds=1,
        bootstrap_growth_enabled=bootstrap,
        bootstrap_growth_query="stomata regulate transpiration",
    )


def _close(runtime) -> None:
    runtime.close()


async def test_mvp_backend_runs_growth_memory_and_core(tmp_path):
    runtime = build_runtime(_settings(tmp_path))
    try:
        response = await runtime.handle(
            "Explain how stomata regulate water loss when the air becomes too dry."
        )
        assert response.route == Route.GROWTH
        assert response.metadata["growth_invoked"] is True
        assert response.metadata["core_invoked"] is True
        assert response.metadata["growth_evidence_count"] >= 1
        assert response.metadata["grounded_answer_used"] is True
        assert response.metadata["semantic_completeness_complete"] is True
        assert "mock" not in response.answer.lower()
        assert runtime.growth.system.db.counts()["claims"] > 0
    finally:
        _close(runtime)


async def test_mvp_backend_blocks_ungrounded_factual_answer(tmp_path):
    runtime = build_runtime(_settings(tmp_path))
    try:
        response = await runtime.handle("What country won the Olympics of 2026?")
        assert response.route == Route.GROWTH
        assert response.metadata["hard_blocked"] is True
        assert response.metadata["semantic_completeness_complete"] is False
        assert "japan" not in response.answer.lower()
        assert "not yet have enough grounded information" in response.answer.lower()
    finally:
        _close(runtime)


def test_mvp_backend_starts_idle_growth_and_bootstraps_memory(tmp_path):
    runtime = build_runtime(_settings(tmp_path, bootstrap=True))
    try:
        deadline = time.time() + 20
        while time.time() < deadline:
            counts = runtime.growth.system.db.counts()
            if counts["sources"] > 0 and counts["claims"] > 0:
                break
            time.sleep(0.25)
        counts = runtime.growth.system.db.counts()
        assert counts["sources"] > 0
        assert counts["claims"] > 0
        assert runtime.growth.system.config.get("idle_growth_enabled") is True
        assert runtime.growth.system.engine.state()["running"] is True
    finally:
        _close(runtime)


def test_mvp_backend_internet_access_is_enabled_in_auto_mode(tmp_path):
    runtime = build_runtime(_settings(tmp_path, web_provider="auto"))
    try:
        status = runtime.growth.system.provider_status()
        assert status["mode"] == "auto"
        assert status["wikipedia_available_without_key"] is True
        assert status["direct_url_fetch"] is True
    finally:
        _close(runtime)


async def test_mvp_backend_does_not_answer_coding_request_from_unrelated_memory(tmp_path):
    runtime = build_runtime(_settings(tmp_path, bootstrap=True))
    try:
        deadline = time.time() + 20
        while time.time() < deadline:
            if runtime.growth.system.db.counts()["claims"] > 0:
                break
            time.sleep(0.25)
        response = await runtime.handle("code me a clock widget")
        assert response.route == Route.ORGANIC_CORE
        assert response.metadata["hard_blocked"] is True
        assert "coding or file-editing tools" in response.answer
        assert "whale" not in response.answer.lower()
    finally:
        _close(runtime)
