from organic_runtime.cognition.structural import infer_structural_fields
from organic_runtime.contracts import (
    CognitiveResource,
    GateDecision,
    IntentEnvelope,
    PreflightKnowledge,
    Route,
    WorldMode,
)
from organic_runtime.planning.resource_planner import CognitiveResourcePlanner
from organic_runtime.semantic.pydantic_ai_adapter import PydanticAISemanticInterface


def test_closed_world_planner_authorizes_processor_and_prohibits_external_resources():
    request = (
        "Logic Puzzle: There are two ducks in front of a duck, two ducks behind a "
        "duck and a duck in the middle. How many ducks are there?"
    )
    structural = infer_structural_fields(request) or {}
    envelope = IntentEnvelope(
        original_request=request,
        normalized_request=request,
        intent="logic_puzzle",
        required_capabilities=["logical_reasoning"],
        suggested_route=Route.ORGANIC_CORE,
        self_contained_reasoning=True,
        **structural,
    )
    plan = CognitiveResourcePlanner(max_processor_cycles=16).plan(
        envelope,
        PreflightKnowledge(requires_research=False),
        GateDecision(request_id=envelope.request_id, route=Route.ORGANIC_CORE),
    )

    assert plan.world_mode == WorldMode.CLOSED
    assert plan.processor_capabilities == ["order_cardinality"]
    processor_steps = [
        step for step in plan.steps if step.resource == CognitiveResource.ORGANIC_PROCESSOR
    ]
    assert len(processor_steps) == 1
    assert processor_steps[0].capability == "order_cardinality"
    assert plan.steps[0].resource == CognitiveResource.COGNITION
    assert plan.steps[-1].resource == CognitiveResource.RESULT_VALIDATOR
    assert CognitiveResource.LIVING_MEMORY in plan.prohibited_resources
    assert CognitiveResource.EVIDENCE_WEB in plan.prohibited_resources
    assert CognitiveResource.GRAPH_BROKER in plan.prohibited_resources
    assert CognitiveResource.MEMORY_COMPILER_VALIDATOR in plan.prohibited_resources


def test_growth_plan_includes_evidence_compilation_before_cognition():
    envelope = IntentEnvelope(
        original_request="Find the latest population figure.",
        normalized_request="Find the latest population figure.",
        intent="external_research",
        requires_current_external_info=True,
        suggested_route=Route.GROWTH,
    )
    plan = CognitiveResourcePlanner().plan(
        envelope,
        PreflightKnowledge(requires_research=True),
        GateDecision(request_id=envelope.request_id, route=Route.GROWTH),
    )

    resources = [step.resource for step in plan.steps]
    assert plan.world_mode == WorldMode.OPEN
    assert resources.index(CognitiveResource.EVIDENCE_WEB) < resources.index(
        CognitiveResource.MEMORY_COMPILER_VALIDATOR
    )
    assert resources.index(CognitiveResource.MEMORY_COMPILER_VALIDATOR) < resources.index(
        CognitiveResource.COGNITION
    )


def test_growth_authorization_overrides_conflicting_model_closed_world_flag():
    envelope = IntentEnvelope(
        original_request="Research the latest stable Python release.",
        normalized_request="Research the latest stable Python release.",
        intent="external_research",
        requires_current_external_info=True,
        self_contained_reasoning=True,
        closed_world=True,
        sufficient_premises=True,
        suggested_route=Route.GROWTH,
    )
    decision = GateDecision(request_id=envelope.request_id, route=Route.GROWTH)

    plan = CognitiveResourcePlanner().plan(envelope, PreflightKnowledge(), decision)

    assert plan.world_mode == WorldMode.OPEN
    assert CognitiveResource.EVIDENCE_WEB in [step.resource for step in plan.steps]


def test_planner_uses_current_request_not_stale_semantic_normalization():
    envelope = PydanticAISemanticInterface._finalize_envelope(IntentEnvelope(
        original_request="1+1=?",
        normalized_request=(
            "There are two ducks in front of a duck, two ducks behind a duck, "
            "and a duck in the middle."
        ),
        intent="logic_puzzle",
        required_capabilities=["logical_reasoning"],
        self_contained_reasoning=True,
    ), "1+1=?")
    plan = CognitiveResourcePlanner().plan(
        envelope,
        PreflightKnowledge(requires_research=False),
        GateDecision(request_id=envelope.request_id, route=Route.ORGANIC_CORE),
    )

    assert envelope.reasoning_family == "bounded_arithmetic"
    assert plan.processor_capabilities == ["bounded_arithmetic"]


def test_structural_compiler_preserves_unknown_as_case_analysis_variable():
    request = (
        "Jack is looking at Anne. Anne is looking at George. Jack is married, "
        "George is not, and we don't know if Anne is married. Is a married person "
        "looking at an unmarried person?"
    )
    structural = infer_structural_fields(request)

    assert structural is not None
    assert structural["sufficient_premises"] is True
    assert structural["reasoning_family"] == "boolean_case_analysis"
    anne = next(
        item
        for item in structural["structural_constraints"]
        if item.get("kind") == "entity_property" and item.get("entity") == "Anne"
    )
    assert anne["value"] is None


def test_structural_compiler_builds_truth_lie_cases_without_embedding_answer():
    structural = infer_structural_fields(
        "Logic Puzzle: You're at a fork in the road. One direction leads to the City of Lies, "
        "where everyone always lies, and the other to the City of Truth, where everyone always "
        "tells the truth. What question could you ask to find the road to the City of Truth?"
    )

    assert structural is not None
    assert structural["reasoning_family"] == "truth_lie_navigation"
    assert structural["reasoning_goal"] == "identify_truth_road"
    assert structural["required_operations"] == [
        "ENUMERATE_CASES",
        "TEST_ENTAILMENT",
        "VERIFY_ALL_CASES",
        "STOP_IF_VERIFIED",
    ]
    assert all(
        "question" not in item and "answer" not in item
        for item in structural["structural_constraints"]
    )


def test_structural_compiler_builds_truth_role_assignment_without_answer():
    structural = infer_structural_fields(
        "Logic Puzzle: There are three people (Alex, Ben and Cody), one of whom is a knight, "
        "one a knave and one a spy. The knight always tells the truth, the knave always lies "
        "and the spy can either lie or tell the truth. Alex says: \"Cody is a knave.\" Ben "
        "says: \"Alex is a knight.\" Cody says: \"I am the spy.\" Who has each role?"
    )

    assert structural is not None
    assert structural["reasoning_family"] == "truth_role_assignment"
    assert structural["reasoning_goal"] == "identify_role_assignment"
    domain = next(
        item for item in structural["structural_constraints"]
        if item.get("kind") == "assignment_domain"
    )
    assert domain == {
        "kind": "assignment_domain",
        "entities": ["Alex", "Ben", "Cody"],
        "roles": ["knight", "knave", "spy"],
        "bijection": True,
    }
    assert all("answer" not in item for item in structural["structural_constraints"])


def test_structural_compiler_builds_transitive_order_premises_without_answer():
    structural = infer_structural_fields(
        "Five people were eating apples, A finished before B, but behind C. "
        "D finished before E, but behind B. What was the finishing order?"
    )

    assert structural is not None
    assert structural["reasoning_family"] == "partial_order"
    edges = {
        (item["before"], item["after"])
        for item in structural["structural_constraints"]
    }
    assert edges == {("A", "B"), ("C", "A"), ("D", "E"), ("B", "D")}
    assert all("answer" not in item for item in structural["structural_constraints"])


def test_structural_compiler_accepts_present_tense_before_and_after():
    structural = infer_structural_fields(
        "Five runners A, B, C, D, and E finish a race. A finishes before B. "
        "C finishes before A. D finishes after B. E finishes after D. "
        "What is the finishing order?"
    )

    assert structural is not None
    assert structural["reasoning_family"] == "partial_order"
    edges = {
        (item["before"], item["after"])
        for item in structural["structural_constraints"]
    }
    assert edges == {("A", "B"), ("C", "A"), ("B", "D"), ("D", "E")}
