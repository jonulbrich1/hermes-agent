"""Programmatic Interaction Gate.

The gate authorizes the cheapest safe path.
It deliberately does not trust the Semantic Interface LLM to downgrade factual work.
"""

from dataclasses import dataclass, field
from enum import Enum
import re
from typing import Iterable


class Route(str, Enum):
    CONVERSATION = "CONVERSATION"
    STATE = "STATE"
    RETRIEVE = "RETRIEVE"
    REASON = "REASON"
    GROWTH = "GROWTH"


@dataclass
class GateDecision:
    route: Route
    confidence: float
    reasons: list[str] = field(default_factory=list)
    requires_context_resolution: bool = False


class InteractionGate:
    _STATE_PATTERNS = (
        r"\bhow are you\b",
        r"\bwhat are you (doing|learning|working on)\b",
        r"\bare you (busy|healthy|okay|ok)\b",
        r"\bwhat(?:'s| is) your (status|state)\b",
        r"\bdid (anything|something) (fail|break|go wrong)\b",
        r"\bactive (task|tasks|growth)\b",
    )

    _GREETING_PATTERNS = (
        r"^(hi|hello|hey|good morning|good afternoon|good evening)[!. ]*$",
        r"^(thanks|thank you|ty)[!. ]*$",
        r"^(bye|goodbye|see you)[!. ]*$",
        r"^(how(?:'s| is) it going|what(?:'s| is) up)[?.! ]*$",
    )

    _CONTEXT_DEPENDENT = (
        r"^(why|how|what) (is|was|does|did) (it|that|this)\b",
        r"^(why|how) so[?.! ]*$",
        r"^what about (it|that|this)\b",
        r"^and (why|how|what)\b",
    )

    def decide(self, user_text: str, memory_confidence: float = 0.0) -> GateDecision:
        text = (user_text or "").strip()
        low = text.lower()

        if not text:
            return GateDecision(Route.CONVERSATION, 1.0, ["Empty/whitespace interaction."])

        if any(re.search(p, low) for p in self._STATE_PATTERNS):
            return GateDecision(
                Route.STATE,
                0.98,
                ["Internal-state intent must use actual runtime state."],
            )

        if any(re.search(p, low) for p in self._GREETING_PATTERNS):
            return GateDecision(
                Route.CONVERSATION,
                0.98,
                ["Deterministic social/conversation fast path."],
            )

        if any(re.search(p, low) for p in self._CONTEXT_DEPENDENT):
            return GateDecision(
                Route.REASON,
                0.93,
                ["Context-dependent follow-up must resolve its referent before retrieval or web search."],
                requires_context_resolution=True,
            )

        # Retrieval is only allowed after social/state checks so that learning a
        # definition for "hello" can never hijack a greeting.
        if memory_confidence >= 0.88:
            return GateDecision(
                Route.RETRIEVE,
                min(0.99, memory_confidence),
                ["High-confidence Organic memory preflight passed."],
            )

        # User text never jumps straight to GROWTH here.
        # The Organic Core / retrieval path must expose the gap first.
        return GateDecision(
            Route.REASON,
            0.80,
            ["Nontrivial request defaults to Organic reasoning; growth requires an explicit knowledge gap."],
        )


def allowed_tools(route: Route) -> set[str]:
    if route == Route.CONVERSATION:
        return set()
    if route == Route.STATE:
        return {"organic_get_state"}
    if route == Route.RETRIEVE:
        return {"organic_memory_search", "organic_reason"}
    if route == Route.REASON:
        return {
            "organic_memory_search",
            "organic_reason",
            "organic_research",
            "organic_submit_evidence",
            "organic_validate_claim",
        }
    if route == Route.GROWTH:
        return {
            "organic_memory_search",
            "organic_reason",
            "organic_research",
            "organic_submit_evidence",
            "organic_validate_claim",
            "organic_growth_frontier",
            "organic_growth_cycle",
        }
    return set()
