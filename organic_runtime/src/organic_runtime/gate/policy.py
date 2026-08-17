from __future__ import annotations

from dataclasses import dataclass

from organic_runtime.contracts import GateContext, GateDecision, IntentEnvelope, Route


@dataclass(frozen=True)
class GateThresholds:
    fast_complexity_max: float = 0.25
    fast_uncertainty_max: float = 0.35
    known_confidence_min: float = 0.80
    escalate_uncertainty: float = 0.55


class GatePolicy:
    """Deterministic route authorization.

    The Semantic Interface's suggested_route is evidence, never authority.
    """

    def __init__(self, thresholds: GateThresholds | None = None) -> None:
        self.thresholds = thresholds or GateThresholds()

    def decide(self, envelope: IntentEnvelope, context: GateContext) -> GateDecision:
        t = self.thresholds
        reasons: list[str] = []

        if envelope.requires_current_external_info or context.preflight.requires_research:
            if context.state.growth_available:
                reasons.append("Fresh external evidence is required; growth path is authorized.")
                return self._decision(envelope, Route.GROWTH, reasons)
            reasons.append("Growth is unavailable; escalating to Organic Core for bounded handling.")
            return self._decision(envelope, Route.ORGANIC_CORE, reasons, escalated=True)

        if envelope.uncertainty > t.escalate_uncertainty:
            reasons.append(
                f"Semantic uncertainty {envelope.uncertainty:.2f} exceeds "
                f"{t.escalate_uncertainty:.2f}; escalating to Organic Core."
            )
            return self._decision(envelope, Route.ORGANIC_CORE, reasons, escalated=True)

        if envelope.intent == "internal_state" and context.state_query_allowed:
            reasons.append("Internal-state intent must be answered from actual runtime state.")
            return self._decision(envelope, Route.INTERNAL_STATE, reasons)

        if (
            envelope.suggested_route == Route.CONVERSATION
            and envelope.intent == "simple_conversation"
            and envelope.complexity <= t.fast_complexity_max
            and envelope.uncertainty <= t.fast_uncertainty_max
            and not envelope.required_capabilities
        ):
            reasons.append("Request satisfies all deterministic fast-conversation constraints.")
            return self._decision(envelope, Route.CONVERSATION, reasons)

        if (
            context.preflight.known_route
            and context.preflight.direct_answer is not None
            and context.preflight.confidence >= t.known_confidence_min
        ):
            reasons.append(
                f"Known route confidence {context.preflight.confidence:.2f} meets "
                f"{t.known_confidence_min:.2f}; full core is unnecessary."
            )
            return self._decision(envelope, Route.KNOWN_ROUTE, reasons)

        if not context.state.core_available:
            reasons.append("Organic Core is unavailable; request cannot be safely routed deeper.")
            return GateDecision(
                request_id=envelope.request_id,
                route=Route.ORGANIC_CORE,
                authorized=False,
                escalated=True,
                reasons=reasons,
            )

        if envelope.suggested_route != Route.ORGANIC_CORE:
            reasons.append(
                f"Semantic Interface suggested {envelope.suggested_route.value!r}, but gate "
                "conditions did not authorize that shortcut; escalating to Organic Core."
            )
        else:
            reasons.append("Organic Core is the safe default for nontrivial requests.")
        return self._decision(envelope, Route.ORGANIC_CORE, reasons)

    @staticmethod
    def _decision(
        envelope: IntentEnvelope,
        route: Route,
        reasons: list[str],
        escalated: bool = False,
    ) -> GateDecision:
        return GateDecision(
            request_id=envelope.request_id,
            route=route,
            authorized=True,
            escalated=escalated,
            reasons=reasons,
        )
