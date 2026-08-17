from __future__ import annotations

import json

import pytest

from organic_processor import OrganicProcessor, StructuralTask
from organic_runtime.config import RuntimeSettings
from organic_runtime.factory import build_runtime
from organic_runtime.semantic.pydantic_ai_adapter import PydanticAISemanticInterface
from organic_runtime.contracts import IntentEnvelope
from organic_runtime.cognition import verify_trace


def _duck_task(before: int = 2, after: int = 2) -> StructuralTask:
    return StructuralTask(
        family="order_cardinality",
        goal="min_distinct_count",
        constraints=(
            {"kind": "exists_count_before", "count": before},
            {"kind": "exists_count_after", "count": after},
            {"kind": "exists_middle"},
        ),
    )


def test_fresh_processor_is_not_answer_hardcoded_and_explores_generic_model(tmp_path):
    processor = OrganicProcessor(tmp_path / "processor.json")
    greedy = processor.process(_duck_task())
    explored = processor.process(_duck_task(), explore=True)

    assert greedy.answer == 5
    assert any(trace.answer == 3 for trace in explored.alternatives)
    assert "ducks" not in json.dumps(processor.snapshot()).lower()


def test_processor_learning_persists_and_transfers_to_new_counts(tmp_path):
    path = tmp_path / "processor.json"
    processor = OrganicProcessor(path)
    for _ in range(3):
        explored = processor.process(_duck_task(), explore=True)
        for trace in explored.alternatives:
            processor.learn(trace, 1.0 if trace.answer == 3 else -0.65, "test_verifier")

    restarted = OrganicProcessor(path)
    assert restarted.status()["composite_count"] == 1
    transfer = restarted.process(_duck_task(3, 3), explore=True)
    assert any(trace.answer == 5 for trace in transfer.alternatives)
    assert restarted.status()["state_bytes"] < 16 * 1024 * 1024


def test_grounded_selection_limit_is_ranked_and_externally_verified(tmp_path):
    task = StructuralTask(
        family="grounded_evidence_selection",
        goal="select_supported_items",
        constraints=(
            {
                "kind": "grounded_candidate",
                "index": 0,
                "retrieval_score": 0.60,
                "confidence": 0.8,
                "selection_threshold": 0.45,
                "selection_limit": 1,
            },
            {
                "kind": "grounded_candidate",
                "index": 1,
                "retrieval_score": 0.90,
                "confidence": 0.8,
                "selection_threshold": 0.45,
                "selection_limit": 1,
            },
        ),
    )

    trace = OrganicProcessor(tmp_path / "processor.json").process(task).trace

    assert trace is not None
    assert trace.answer == [1]
    assert verify_trace(task, trace).accepted is True


def test_semantic_finalizer_removes_model_answer_leakage():
    request = (
        "There are two ducks in front of a duck, two ducks behind a duck and a duck "
        "in the middle. How many ducks are there?"
    )
    proposed = IntentEnvelope(
        original_request="stale",
        normalized_request="3 ducks",
        intent="logic_puzzle",
        self_contained_reasoning=True,
        closed_world=True,
        sufficient_premises=True,
        reasoning_family="order_cardinality",
        reasoning_goal="min_distinct_count",
        structural_constraints=[{"kind": "answer", "answer": 999}],
    )

    finalized = PydanticAISemanticInterface._finalize_envelope(proposed, request)

    assert finalized.original_request == request
    assert finalized.normalized_request == request
    assert all("answer" not in item for item in finalized.structural_constraints)


def test_semantic_finalizer_clears_conflicting_closed_world_research_flag():
    request = "Research the latest stable Python release."
    proposed = IntentEnvelope(
        original_request=request,
        normalized_request=request,
        intent="external_research",
        self_contained_reasoning=True,
        closed_world=True,
        sufficient_premises=True,
    )

    finalized = PydanticAISemanticInterface._finalize_envelope(proposed, request)

    assert finalized.requires_current_external_info is True
    assert finalized.self_contained_reasoning is False
    assert finalized.closed_world is False
    assert finalized.structural_constraints == []


@pytest.mark.asyncio
async def test_runtime_transfers_order_structure_to_unseen_surface_nouns(tmp_path):
    settings = RuntimeSettings(
        backend="mvp",
        semantic_mode="heuristic",
        mvp_data_dir=tmp_path / "mvp",
        trace_dir=tmp_path / "traces",
        idle_growth_enabled=False,
        bootstrap_growth_enabled=False,
    )
    runtime = build_runtime(settings)
    try:
        response = await runtime.handle(
            "There are three cars in front of a car, three cars behind a car and a car "
            "in the middle. How many cars are there?"
        )
        assert response.answer.startswith("5 cars")
        assert response.metadata["sources"] == []
        assert response.metadata["processor_capabilities"] == ["order_cardinality"]
    finally:
        runtime.close()
