from __future__ import annotations

import re

from organic_runtime.contracts import (
    IntentEnvelope,
    Route,
    RuntimeStateSnapshot,
    SemanticCompletenessReview,
)
from organic_runtime.semantic.base import (
    clarification_subject,
    enforce_objective_coverage,
    looks_self_contained_reasoning,
)
from organic_runtime.cognition.structural import infer_structural_fields


class HeuristicSemanticInterface:
    """Offline semantic adapter for tests and architecture smoke checks.

    This deliberately does not try to be intelligent. Its job is to let the
    complete runtime run without a model server so gate behavior can be tested.
    """

    _greetings = {
        "hello",
        "hi",
        "hey",
        "good morning",
        "good afternoon",
        "good evening",
    }
    _state_phrases = (
        "how are you",
        "your status",
        "system status",
        "health status",
        "are you healthy",
        "are you okay",
    )
    _freshness_phrases = (
        "latest",
        "current version",
        "right now",
        "today",
        "look up",
        "search the web",
        "research",
        "newest",
    )
    _coding_phrases = (
        "code me",
        "write code",
        "build me",
        "implement",
        "make an app",
        "make a widget",
        "create a widget",
    )

    async def analyze(
        self,
        request: str,
        state: RuntimeStateSnapshot,
    ) -> IntentEnvelope:
        text = " ".join(request.strip().split())
        lower = text.lower()

        if lower in self._greetings or lower.rstrip("!.") in self._greetings:
            return IntentEnvelope(
                original_request=request,
                normalized_request=text,
                intent="simple_conversation",
                complexity=0.05,
                uncertainty=0.05,
                suggested_route=Route.CONVERSATION,
                reasons=["Offline heuristic recognized a greeting."],
            )

        if any(phrase in lower for phrase in self._state_phrases):
            return IntentEnvelope(
                original_request=request,
                normalized_request=text,
                intent="internal_state",
                required_capabilities=["internal_state"],
                complexity=0.10,
                uncertainty=0.05,
                suggested_route=Route.INTERNAL_STATE,
                reasons=["Offline heuristic recognized an internal-state request."],
            )

        structural = infer_structural_fields(text) or {}
        if structural or looks_self_contained_reasoning(text):
            return IntentEnvelope(
                original_request=request,
                normalized_request=text,
                intent="self_contained_reasoning",
                required_capabilities=["logical_reasoning"],
                complexity=0.55,
                uncertainty=0.20,
                suggested_route=Route.ORGANIC_CORE,
                self_contained_reasoning=True,
                closed_world=True,
                sufficient_premises=bool(structural),
                reasoning_family=structural.get("reasoning_family"),
                reasoning_goal=structural.get("reasoning_goal"),
                required_operations=structural.get("required_operations", []),
                structural_constraints=structural.get("structural_constraints", []),
                reasons=["All premises needed for the reasoning task are in the request."],
            )

        if clarification_subject(text):
            return IntentEnvelope(
                original_request=request,
                normalized_request=text,
                intent="clarification_needed",
                complexity=0.10,
                uncertainty=0.80,
                suggested_route=Route.ORGANIC_CORE,
                reasons=["The request names a subject but does not specify an action."],
            )

        if any(phrase in lower for phrase in self._coding_phrases):
            return IntentEnvelope(
                original_request=request,
                normalized_request=text,
                intent="external_tool_task",
                required_capabilities=["coding"],
                requested_output="code",
                likely_memory_domains=_domain_hints(lower),
                complexity=0.70,
                uncertainty=0.20,
                suggested_route=Route.ORGANIC_CORE,
                reasons=["Offline heuristic detected a coding/file-generation request."],
            )

        requires_fresh = any(phrase in lower for phrase in self._freshness_phrases)
        if requires_fresh:
            return IntentEnvelope(
                original_request=request,
                normalized_request=text,
                intent="external_research",
                required_capabilities=["research"],
                likely_memory_domains=_domain_hints(lower),
                complexity=0.55,
                uncertainty=0.20,
                suggested_route=Route.GROWTH,
                requires_current_external_info=True,
                reasons=["Offline heuristic detected a freshness/research requirement."],
            )

        return IntentEnvelope(
            original_request=request,
            normalized_request=text,
            intent="general_reasoning",
            likely_memory_domains=_domain_hints(lower),
            complexity=0.55,
            uncertainty=0.25,
            suggested_route=Route.ORGANIC_CORE,
            reasons=["Offline heuristic defaulted to Organic Core reasoning."],
        )

    async def respond_fast(
        self,
        request: str,
        envelope: IntentEnvelope,
        state: RuntimeStateSnapshot,
    ) -> str:
        lower = request.strip().lower().rstrip("!.")
        if lower in self._greetings:
            return "Hello. What would you like to work on?"
        return "I am ready. What would you like to work on?"

    async def review_completeness(
        self,
        request: str,
        envelope: IntentEnvelope,
        answer: str,
        metadata: dict,
    ) -> SemanticCompletenessReview:
        del envelope
        hard_blocked = bool(metadata.get("hard_blocked"))
        complete = bool(answer.strip()) and not hard_blocked
        review = SemanticCompletenessReview(
            complete=complete,
            needs_tool_loop=not complete and not hard_blocked,
            missing=[] if complete else ["A complete result was not produced."],
            reason="Offline semantic completeness check.",
        )
        return enforce_objective_coverage(request, answer, metadata, review)

    async def present_result(
        self,
        request: str,
        envelope: IntentEnvelope,
        answer: str,
        metadata: dict,
    ) -> str:
        del request, envelope, metadata
        return answer

def _domain_hints(text: str) -> list[str]:
    hints: list[str] = []
    for token, domain in (
        ("organic", "organic_ai"),
        ("memory", "memory"),
        ("graph", "graph"),
        ("code", "coding"),
        ("python", "coding"),
        ("network", "networking"),
        ("agent", "agent_runtime"),
    ):
        if re.search(rf"\b{re.escape(token)}\b", text):
            hints.append(domain)
    return sorted(set(hints))
