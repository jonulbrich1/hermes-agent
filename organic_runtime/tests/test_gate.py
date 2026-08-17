from organic_runtime.contracts import (
    GateContext,
    IntentEnvelope,
    PreflightKnowledge,
    Route,
    RuntimeStateSnapshot,
)
from organic_runtime.gate.policy import GatePolicy


def test_uncertainty_escalates_to_core():
    policy = GatePolicy()
    envelope = IntentEnvelope(
        original_request="maybe do something",
        normalized_request="maybe do something",
        intent="simple_conversation",
        complexity=0.10,
        uncertainty=0.90,
        suggested_route=Route.CONVERSATION,
    )
    decision = policy.decide(
        envelope,
        GateContext(state=RuntimeStateSnapshot(), preflight=PreflightKnowledge()),
    )
    assert decision.route == Route.ORGANIC_CORE
    assert decision.escalated is True


def test_known_route_requires_confidence_and_direct_answer():
    policy = GatePolicy()
    envelope = IntentEnvelope(
        original_request="known",
        normalized_request="known",
        intent="general_reasoning",
        complexity=0.40,
        uncertainty=0.10,
        suggested_route=Route.KNOWN_ROUTE,
    )
    weak = policy.decide(
        envelope,
        GateContext(
            state=RuntimeStateSnapshot(),
            preflight=PreflightKnowledge(
                known_route=True,
                confidence=0.50,
                direct_answer="weak answer",
            ),
        ),
    )
    assert weak.route == Route.ORGANIC_CORE

    strong = policy.decide(
        envelope,
        GateContext(
            state=RuntimeStateSnapshot(),
            preflight=PreflightKnowledge(
                known_route=True,
                confidence=0.95,
                direct_answer="strong answer",
            ),
        ),
    )
    assert strong.route == Route.KNOWN_ROUTE


def test_fresh_external_info_forces_growth():
    policy = GatePolicy()
    envelope = IntentEnvelope(
        original_request="latest changes",
        normalized_request="latest changes",
        intent="external_research",
        complexity=0.10,
        uncertainty=0.10,
        suggested_route=Route.CONVERSATION,
        requires_current_external_info=True,
    )
    decision = policy.decide(
        envelope,
        GateContext(state=RuntimeStateSnapshot(), preflight=PreflightKnowledge()),
    )
    assert decision.route == Route.GROWTH


def test_semantic_fast_suggestion_cannot_bypass_gate_constraints():
    policy = GatePolicy()
    envelope = IntentEnvelope(
        original_request="complex request",
        normalized_request="complex request",
        intent="simple_conversation",
        complexity=0.90,
        uncertainty=0.10,
        suggested_route=Route.CONVERSATION,
    )
    decision = policy.decide(
        envelope,
        GateContext(state=RuntimeStateSnapshot(), preflight=PreflightKnowledge()),
    )
    assert decision.route == Route.ORGANIC_CORE
