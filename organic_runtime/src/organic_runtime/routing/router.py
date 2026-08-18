from __future__ import annotations

from organic_runtime.adapters.base import CoreAdapter, GrowthAdapter, MemoryAdapter
from organic_runtime.contracts import (
    CognitiveResourcePlan,
    CoreRequest,
    GateDecision,
    IntentEnvelope,
    PreflightKnowledge,
    Route,
    RuntimeStateSnapshot,
)
from organic_runtime.semantic.base import SemanticInterface
from organic_runtime.tooling.registry import ToolRegistry


class OrganicRouter:
    def __init__(
        self,
        semantic: SemanticInterface,
        memory: MemoryAdapter,
        core: CoreAdapter,
        growth: GrowthAdapter,
        tools: ToolRegistry,
    ) -> None:
        self.semantic = semantic
        self.memory = memory
        self.core = core
        self.growth = growth
        self.tools = tools

    async def execute(
        self,
        decision: GateDecision,
        envelope: IntentEnvelope,
        preflight: PreflightKnowledge,
        state: RuntimeStateSnapshot,
        resource_plan: CognitiveResourcePlan,
    ) -> tuple[str, dict[str, object]]:
        if not decision.authorized:
            raise RuntimeError("Interaction Gate did not authorize execution.")

        if decision.route == Route.CONVERSATION:
            print("[router] Executing conversation fast path")
            answer = await self.semantic.respond_fast(envelope.original_request, envelope, state)
            return answer, {"core_invoked": False, "growth_invoked": False}

        if decision.route == Route.INTERNAL_STATE:
            print("[router] Executing internal-state path")
            snapshot = await self.tools.invoke(
                "internal_state.snapshot",
                decision.route,
                state=state,
            )
            return self._internal_state_answer(snapshot), {
                "core_invoked": False,
                "growth_invoked": False,
            }

        if decision.route == Route.KNOWN_ROUTE:
            print("[router] Executing known-memory route")
            answer = await self.memory.answer_known(envelope, preflight)
            return answer, {
                "core_invoked": False,
                "growth_invoked": False,
                "memory_ids": preflight.memory_ids,
            }

        if decision.route == Route.ORGANIC_CORE:
            print("[router] Executing Organic Core path")
            result = await self.core.process(
                CoreRequest(
                    envelope=envelope,
                    preflight=preflight,
                    resource_plan=resource_plan,
                )
            )
            return result.answer, {
                "core_invoked": True,
                "growth_invoked": False,
                "activated_memory_ids": result.activated_memory_ids,
                "active_paths": result.active_paths,
                **result.metadata,
            }

        if decision.route == Route.GROWTH:
            print("[router] Executing growth path before Organic Core synthesis")
            growth_result = await self.growth.grow(envelope)
            core_result = await self.core.process(
                CoreRequest(
                    envelope=envelope,
                    preflight=preflight,
                    growth=growth_result,
                    resource_plan=resource_plan,
                )
            )
            return core_result.answer, {
                "core_invoked": True,
                "growth_invoked": True,
                "growth_evidence_count": len(growth_result.evidence),
                "durable_candidate_ids": growth_result.durable_candidate_ids,
                "activated_memory_ids": core_result.activated_memory_ids,
                "active_paths": core_result.active_paths,
                **growth_result.metadata,
                **core_result.metadata,
            }

        raise RuntimeError(f"Unsupported route: {decision.route}")

    @staticmethod
    def _internal_state_answer(state: RuntimeStateSnapshot) -> str:
        return (
            f"Current runtime status is {state.status}. "
            f"Core available={state.core_available}; "
            f"memory available={state.memory_available}; "
            f"growth available={state.growth_available}; "
            f"completed requests={state.requests_total}."
        )
