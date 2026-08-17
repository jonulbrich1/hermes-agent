from organic_runtime.contracts import (
    CognitiveResource,
    GateDecision,
    IntentEnvelope,
    PreflightKnowledge,
    Route,
    WorldMode,
)
from organic_runtime.planning.resource_planner import CognitiveResourcePlanner
from organic_runtime.cognition.structural import infer_structural_fields
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
