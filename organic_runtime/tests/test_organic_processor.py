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


def _partial_order_task() -> StructuralTask:
    return StructuralTask(
        family="partial_order",
        goal="linearize_order",
        constraints=(
            {"kind": "precedes", "before": "A", "after": "B"},
            {"kind": "precedes", "before": "C", "after": "A"},
            {"kind": "precedes", "before": "D", "after": "E"},
            {"kind": "precedes", "before": "B", "after": "D"},
        ),
    )


def _unknown_boolean_task() -> StructuralTask:
    return StructuralTask(
        family="boolean_case_analysis",
        goal="prove_existential_relation",
        constraints=(
            {"kind": "directed_relation", "subject": "Jack", "predicate": "looking_at", "object": "Anne"},
            {"kind": "directed_relation", "subject": "Anne", "predicate": "looking_at", "object": "George"},
            {"kind": "entity_property", "entity": "Jack", "property": "married", "value": True},
            {"kind": "entity_property", "entity": "George", "property": "married", "value": False},
            {"kind": "entity_property", "entity": "Anne", "property": "married", "value": None},
            {
                "kind": "exists_relation_by_property",
                "predicate": "looking_at",
                "subject_property": "married",
                "subject_value": True,
                "object_property": "married",
                "object_value": False,
            },
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


def test_capability_gap_discovers_verified_pathway_and_persists_it(tmp_path):
    path = tmp_path / "processor.json"
    processor = OrganicProcessor(path)
    task = _partial_order_task()

    assert processor.process(task, explore=True).alternatives == []
    processor.register_gap(task, "No learned partial-order pathway")
    discovery = processor.discover(task)
    accepted = next(trace for trace in discovery.alternatives if verify_trace(task, trace).accepted)
    for trace in discovery.alternatives:
        verification = verify_trace(task, trace)
        processor.learn(trace, verification.reward, "test_external_verifier")
    processor.promote_discovered(task, accepted)

    restarted = OrganicProcessor(path)
    result = restarted.process(task)
    assert result.answer == ["C", "A", "B", "D", "E"]
    assert restarted.status()["learned_pathway_count"] == 1
    assert restarted.status()["growth_frontier_count"] == 0
    assert restarted.status()["resolved_gap_count"] == 1


def test_unknown_value_is_enumerated_and_verified_in_every_case(tmp_path):
    processor = OrganicProcessor(tmp_path / "processor.json")
    task = _unknown_boolean_task()
    processor.register_gap(task, "No learned Boolean pathway")
    discovery = processor.discover(task)
    trace = discovery.alternatives[0]
    verification = verify_trace(task, trace)

    assert trace.answer is True
    assert verification.accepted is True
    assert verification.checks["all_unknown_assignments_evaluated"] is True


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


@pytest.mark.asyncio
async def test_runtime_grows_pathways_for_ordering_and_unknown_case_analysis(tmp_path):
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
        order = await runtime.handle(
            "Five people were eating apples, A finished before B, but behind C. "
            "D finished before E, but behind B. What was the finishing order?"
        )
        boolean = await runtime.handle(
            "Jack is looking at Anne. Anne is looking at George. Jack is married, "
            "George is not, and we don't know if Anne is married. Is a married person "
            "looking at an unmarried person?"
        )

        assert order.answer == "C, A, B, D, E"
        assert order.metadata["processor_growth_attempted"] is True
        assert order.metadata["processor_pathway_promoted"] is True
        assert boolean.answer.startswith("Yes.")
        assert boolean.metadata["decision"]["result_code"] == "verified_boolean_entailment"
        assert runtime.core.system.processor.status()["learned_pathway_count"] == 2
    finally:
        runtime.close()
