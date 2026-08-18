from __future__ import annotations

import time
import zipfile
from pathlib import Path

import pytest

from organic_mvp.evidence import EvidenceDocument
from organic_mvp.memory import growth_retry_delay_seconds
from organic_mvp.util import utcnow
from organic_runtime.adapters.mvp import MvpOrganicSystem
from organic_runtime.config import RuntimeSettings
from organic_runtime.contracts import (
    EntityRef,
    IntentEnvelope,
    Route,
)
from organic_runtime.factory import build_runtime
from organic_runtime.instance_lock import RuntimeAlreadyRunningError

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


async def test_review_package_includes_stage_traces_and_processor_state(tmp_path):
    runtime = build_runtime(_settings(tmp_path))
    try:
        await runtime.handle("What is 2 + 3?")
        package = Path(runtime.core.system.export_review("test_trace_package"))

        with zipfile.ZipFile(package) as archive:
            names = set(archive.namelist())
            manifest = archive.read("manifest.json").decode("utf-8")

        assert any(name.startswith("traces/") and name.endswith(".jsonl") for name in names)
        assert "processor/processor_state.json" in names
        assert "integration_status.json" in names
        assert '"trace_file_count": 1' in manifest
        assert '"semantic_mode": "heuristic"' in manifest
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


def test_empty_growth_uses_entity_query_and_suppresses_immediate_retry(tmp_path, monkeypatch):
    settings = RuntimeSettings(
        semantic_mode="heuristic",
        backend="mvp",
        trace_dir=tmp_path / "traces",
        mvp_data_dir=tmp_path / "mvp",
        web_provider="auto",
        idle_growth_enabled=False,
        bootstrap_growth_enabled=False,
    )
    runtime = build_runtime(settings)
    try:
        system = runtime.growth.system
        db = system.db
        target_id = db.upsert_concept(
            "concept:johan",
            "Johan Fredrikzon",
            "johan fredrikzon",
            kind="ENTITY",
            status="GROUNDED",
            confidence=0.8,
        )
        neighbor_id = db.upsert_concept(
            "concept:history",
            "History of technology",
            "history of technology",
            kind="TOPIC",
            status="GROUNDED",
            confidence=0.8,
        )
        noise_id = db.upsert_concept(
            "concept:research",
            "research",
            "research",
            kind="CONCEPT",
            status="GROUNDED",
            confidence=0.8,
        )
        db.touch_concept(target_id, 2)
        db.touch_concept(neighbor_id, 2)
        db.touch_concept(noise_id, 2)
        db.add_claim(
            "claim:test-growth",
            None,
            "test",
            0,
            "Johan Fredrikzon studies the history of technology.",
            "Johan Fredrikzon studies the history of technology.",
            0.9,
            "GROUNDED",
        )
        db.add_relation(
            "relation:test-growth",
            "claim:test-growth",
            None,
            target_id,
            "studies",
            neighbor_id,
            None,
            0.9,
            "GROUNDED",
        )
        db.add_relation(
            "relation:test-noise",
            "claim:test-growth",
            None,
            noise_id,
            "related_to",
            neighbor_id,
            None,
            0.9,
            "GROUNDED",
        )
        task_id = "task:idle:test-empty-growth"
        db.create_task(
            task_id,
            "IDLE_GROWTH",
            "COGNITION",
            "Learn more about Johan Fredrikzon using external evidence, emphasizing what it is "
            "and how it connects to existing memory.",
            100,
            status="ACTIVE",
            target_concept_id=target_id,
        )
        db.update_task(task_id, started_at=utcnow(), attempts=1)
        queries = []
        monkeypatch.setattr(
            system.broker,
            "search",
            lambda query, limit=5: queries.append((query, limit)) or [],
        )

        system.engine._process_growth_task(dict(db.task(task_id)))

        saved = dict(db.task(task_id))
        assert queries == [("johan fredrikzon", 4)]
        assert saved["status"] == "PARTIAL"
        assert "retry cooldown" in saved["result_text"]
        assert growth_retry_delay_seconds(1) == 300
        assert target_id not in {
            item["concept_id"] for item in system.memory.frontier(limit=20)
        }
        assert neighbor_id in {
            item["concept_id"] for item in system.memory.frontier(limit=20)
        }
        assert noise_id not in {
            item["concept_id"] for item in system.memory.frontier(limit=20)
        }
    finally:
        _close(runtime)


def test_memory_compiler_honors_idle_sentence_budget(tmp_path):
    runtime = build_runtime(_settings(tmp_path))
    try:
        system = runtime.growth.system
        source_id = "source:bounded-idle-test"
        system.db.add_source(
            source_id,
            "https://example.com/bounded-idle-test",
            "Bounded idle test",
            "test",
            "bounded-idle-test",
            "data/source_cache/bounded-idle-test.txt",
            0.8,
        )
        document = EvidenceDocument(
            source_id=source_id,
            title="Bounded idle test",
            url="https://example.com/bounded-idle-test",
            text=" ".join(
                f"Topic {index} connects concept alpha to concept beta."
                for index in range(30)
            ),
            provider="test",
            trust=0.8,
            cache_path="data/source_cache/bounded-idle-test.txt",
            metadata={},
        )

        result = system.memory.ingest(document, reason="bounded test", max_sentences=5)

        assert result.sentences == 5
        assert result.claims_added == 5
    finally:
        _close(runtime)


def test_engine_recovers_interrupted_idle_task_as_partial(tmp_path):
    runtime = build_runtime(_settings(tmp_path))
    try:
        system = runtime.growth.system
        task_id = "task:idle:interrupted-test"
        system.db.create_task(
            task_id,
            "IDLE_GROWTH",
            "COGNITION",
            "Interrupted growth test",
            100,
            status="ACTIVE",
        )

        system.engine._recover_interrupted_tasks()

        saved = dict(system.db.task(task_id))
        assert saved["status"] == "PARTIAL"
        assert saved["completed_at"]
        assert "interrupted by runtime shutdown" in saved["result_text"]
    finally:
        _close(runtime)


def test_mvp_backend_rejects_two_engines_for_the_same_living_memory(tmp_path):
    runtime = build_runtime(_settings(tmp_path))
    try:
        with pytest.raises(RuntimeAlreadyRunningError, match="already owns this Living Memory"):
            build_runtime(_settings(tmp_path))
    finally:
        _close(runtime)

    restarted = build_runtime(_settings(tmp_path))
    _close(restarted)


async def test_mvp_backend_clarifies_underspecified_preference_without_growth(tmp_path):
    runtime = build_runtime(_settings(tmp_path))
    try:
        response = await runtime.handle("I want pizza")

        assert response.route == Route.ORGANIC_CORE
        assert response.metadata["core_invoked"] is False
        assert response.metadata["growth_invoked"] is False
        assert response.metadata["clarification_required"] is True
        assert "What would you like me to do about pizza?" in response.answer
    finally:
        _close(runtime)


def test_active_weave_requires_entity_coverage():
    envelope = IntentEnvelope(
        original_request="Recommend pizza",
        normalized_request="Recommend pizza",
        intent="general_reasoning",
        entities=[EntityRef(text="pizza", entity_type="food", confidence=0.9)],
    )
    unrelated = {
        "text": "Declarative knowledge is interested in what is true independently.",
        "retrieval_score": 0.95,
        "relations": [],
    }
    relevant = {
        "text": "Pizza is a baked dish with a dough base.",
        "retrieval_score": 0.80,
        "relations": [],
    }

    assert MvpOrganicSystem._active_weave_evidence(envelope, [unrelated]) == []
    assert MvpOrganicSystem._active_weave_evidence(envelope, [unrelated, relevant]) == [relevant]
    assert MvpOrganicSystem._active_weave_evidence(
        envelope,
        [unrelated],
        require_entity_overlap=False,
    ) == [unrelated]

    low_confidence_envelope = envelope.model_copy(
        update={"entities": [EntityRef(text="pizza", confidence=0.5)]}
    )
    assert MvpOrganicSystem._active_weave_evidence(
        low_confidence_envelope,
        [unrelated],
    ) == [unrelated]


def test_fresh_growth_retrieval_ranks_current_version_claim_from_bounded_sources(tmp_path):
    runtime = build_runtime(_settings(tmp_path))
    try:
        system = runtime.core.system
        system.db.add_source(
            "source:fresh",
            "https://example.test/releases/3-14-7",
            "Runtime Release 3.14.7",
            "test",
            "sha-fresh",
            "",
            trust=0.9,
        )
        system.db.add_source(
            "source:other",
            "https://example.test/archive",
            "Runtime Archive",
            "test",
            "sha-other",
            "",
            trust=0.9,
        )
        system.db.add_claim(
            "claim:boilerplate",
            "source:fresh",
            "WEB_EVIDENCE",
            0,
            "Skip to content. This page displays a fallback for Runtime Release 3.14.7.",
            None,
            0.8,
            "GROUNDED",
        )
        system.db.add_claim(
            "claim:answer",
            "source:fresh",
            "WEB_EVIDENCE",
            1,
            "Runtime 3.14.7 is the latest stable release, released on 5 August 2026.",
            None,
            0.8,
            "GROUNDED",
        )
        system.db.add_claim(
            "claim:excluded",
            "source:other",
            "WEB_EVIDENCE",
            0,
            "Runtime 99.0 is the latest stable release.",
            None,
            0.99,
            "GROUNDED",
        )

        evidence = system.memory.retrieve_from_sources(
            "Research the latest stable Runtime release and summarize what version it is.",
            {"source:fresh"},
        )

        assert evidence[0]["claim_id"] == "claim:answer"
        assert all(item["source_id"] == "source:fresh" for item in evidence)
        assert evidence[0]["retrieval_score"] > evidence[1]["retrieval_score"]
    finally:
        _close(runtime)


async def test_self_contained_duck_puzzle_uses_organic_processor_without_growth(tmp_path):
    runtime = build_runtime(_settings(tmp_path))
    try:
        response = await runtime.handle(
            "Logic Puzzle: There are two ducks in front of a duck, two ducks behind a "
            "duck and a duck in the middle. How many ducks are there?"
        )

        assert response.route == Route.ORGANIC_CORE
        assert response.answer.startswith("3 ducks")
        assert response.metadata["core_invoked"] is True
        assert response.metadata["growth_invoked"] is False
        assert response.metadata["reasoning_mode"] == "self_contained"
        assert response.metadata["planner_world_mode"] == "closed"
        assert response.metadata["processor_cycles"] == 3
        assert response.metadata["processor_capabilities"] == ["order_cardinality"]
        assert response.metadata["processor_operators"] == [
            "INFER_MINIMUM_LINE_BOUND",
            "CONSTRUCT_LINEAR_MODEL",
            "REPAIR_UNTIL_VALID",
            "EMIT_MODEL_CARDINALITY",
        ]
        assert "evidence_web" in response.metadata["planner_prohibited_resources"]
        assert "living_memory" in response.metadata["planner_prohibited_resources"]
        assert response.metadata["sources"] == []
        assert response.metadata["semantic_completeness_complete"] is True
        assert response.metadata["reasoning_trace"]["model"]["minimal_model"] is True
        assert response.metadata["processor_version"] == "0.11.0-rc2"
        assert response.metadata["v7_seed"]["experience_count"] == 930_838
        assert response.metadata["presenter_packet"]["verified"] is True
        assert response.metadata["presenter_packet"]["answer"] == 3
        assert response.metadata["thought_policy"]["direct_tool_access"] is False
        assert runtime.core.system.db.counts()["planner_outcomes"] == 1
        assert runtime.core.system.db.counts()["core_learning_events"] == 1
        assert runtime.core.system.db.counts()["processing_episodes"] == 1
    finally:
        _close(runtime)


async def test_self_contained_arithmetic_uses_processor_without_growth(tmp_path):
    runtime = build_runtime(_settings(tmp_path))
    try:
        response = await runtime.handle("What is 1 + 1?")

        assert response.answer == "2"
        assert response.route == Route.ORGANIC_CORE
        assert response.metadata["growth_invoked"] is False
        assert response.metadata["processor_capabilities"] == ["bounded_arithmetic"]
        assert response.metadata["processor_operators"] == ["EVALUATE_BOUNDED_EXPRESSION"]
        assert response.metadata["sources"] == []
        assert response.metadata["semantic_completeness_complete"] is True
        assert response.metadata["presenter_packet"]["verified"] is True
        assert response.metadata["presenter_packet"]["answer"] == 2
    finally:
        _close(runtime)


def test_processor_reports_capability_gap_without_external_lookup(tmp_path):
    runtime = build_runtime(_settings(tmp_path))
    try:
        from organic_processor import StructuralTask

        result = runtime.core.system.processor.process(
            StructuralTask(family="novel_symbolic", goal="solve", constraints=()),
        )

        assert result.status == "CAPABILITY_GAP"
        assert result.answer is None
        assert runtime.core.system.processor.status()["external_resource_access"] is False
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
