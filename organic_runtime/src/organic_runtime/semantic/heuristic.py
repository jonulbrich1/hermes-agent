from __future__ import annotations

import re

from organic_runtime.contracts import IntentEnvelope, Route, RuntimeStateSnapshot


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
