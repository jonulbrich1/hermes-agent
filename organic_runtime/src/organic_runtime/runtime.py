from __future__ import annotations

from uuid import uuid4

from organic_runtime.adapters.base import CoreAdapter, GrowthAdapter, MemoryAdapter
from organic_runtime.contracts import GateContext, RuntimeResponse
from organic_runtime.gate.policy import GatePolicy
from organic_runtime.routing.router import OrganicRouter
from organic_runtime.semantic.base import SemanticInterface
from organic_runtime.state.store import InMemoryStateStore
from organic_runtime.tracing.events import TraceRecorder
from organic_runtime.tooling.registry import ToolRegistry


class OrganicRuntime:
    """Top-level request orchestrator.

    This class is intentionally boring. It makes the execution order explicit so
    experiments can prove which stages did and did not run.
    """

    def __init__(
        self,
        semantic: SemanticInterface,
        memory: MemoryAdapter,
        core: CoreAdapter,
        growth: GrowthAdapter,
        gate: GatePolicy,
        state: InMemoryStateStore,
        traces: TraceRecorder,
        tools: ToolRegistry,
    ) -> None:
        self.semantic = semantic
        self.memory = memory
        self.core = core
        self.growth = growth
        self.gate = gate
        self.state = state
        self.traces = traces
        self.tools = tools
        self.router = OrganicRouter(semantic, memory, core, growth, tools)

    def _set_user_active(self, active: bool) -> None:
        for owner in self._component_owners():
            setter = getattr(owner, "set_user_active", None)
            if callable(setter):
                setter(active)

    def _component_owners(self):
        seen: set[int] = set()
        for component in (self.memory, self.core, self.growth):
            owner = getattr(component, "system", component)
            if id(owner) in seen:
                continue
            seen.add(id(owner))
            yield owner

    def close(self) -> None:
        for owner in self._component_owners():
            closer = getattr(owner, "close", None)
            if callable(closer):
                closer()

    def _record_runtime_result(self, response: RuntimeResponse) -> None:
        for owner in self._component_owners():
            recorder = getattr(owner, "record_runtime_result", None)
            if callable(recorder):
                recorder(response)

    def _load_rolling_context(self, request: str) -> dict:
        for owner in self._component_owners():
            loader = getattr(owner, "load_rolling_context", None)
            if callable(loader):
                context = loader(request)
                if isinstance(context, dict):
                    return context
        return {}

    def _record_conversation_turn(self, request: str, response: RuntimeResponse) -> None:
        for owner in self._component_owners():
            recorder = getattr(owner, "record_conversation_turn", None)
            if callable(recorder):
                recorder(request, response)

    async def handle(self, request: str) -> RuntimeResponse:
        if not request or not request.strip():
            raise ValueError("Request must not be empty.")

        trace_id = str(uuid4())
        initial_state = self.state.snapshot()
        rolling_context = self._load_rolling_context(request)
        if rolling_context:
            initial_state = initial_state.model_copy(
                update={"conversation_context": rolling_context}
            )
        self.traces.record(trace_id, "request_received", {"request": request})
        if rolling_context:
            self.traces.record(trace_id, "rolling_cognition_loaded", rolling_context)
        self._set_user_active(True)

        try:
            print(f"[runtime] trace={trace_id} request={request!r}")
            envelope = await self.semantic.analyze(request, initial_state)
            self.traces.record(
                trace_id,
                "semantic_envelope",
                envelope.model_dump(mode="json"),
            )
            print(
                "[runtime] semantic suggested="
                f"{envelope.suggested_route.value} intent={envelope.intent} "
                f"complexity={envelope.complexity:.2f} uncertainty={envelope.uncertainty:.2f}"
            )

            preflight = await self.memory.preflight(envelope)
            self.traces.record(
                trace_id,
                "memory_preflight",
                preflight.model_dump(mode="json"),
            )

            decision = self.gate.decide(
                envelope,
                GateContext(
                    state=initial_state,
                    preflight=preflight,
                    state_query_allowed=True,
                ),
            )
            self.traces.record(
                trace_id,
                "gate_decision",
                decision.model_dump(mode="json"),
            )
            print(
                f"[gate] authorized={decision.authorized} route={decision.route.value} "
                f"escalated={decision.escalated}"
            )

            answer, route_metadata = await self.router.execute(
                decision,
                envelope,
                preflight,
                initial_state,
            )
            self.traces.record(trace_id, "route_executed", route_metadata)
            self.state.record_success(decision.route)

            response = RuntimeResponse(
                request_id=envelope.request_id,
                trace_id=trace_id,
                route=decision.route,
                answer=answer,
                gate=decision,
                metadata={
                    "semantic_suggested_route": envelope.suggested_route.value,
                    "preflight_confidence": preflight.confidence,
                    **route_metadata,
                },
            )
            self.traces.record(
                trace_id,
                "request_completed",
                response.model_dump(mode="json"),
            )
            self._record_runtime_result(response)
            self._record_conversation_turn(request, response)
            return response
        except Exception as exc:
            self.state.record_error(str(exc))
            self.traces.record(
                trace_id,
                "request_failed",
                {"error_type": type(exc).__name__, "error": str(exc)},
            )
            print(f"[runtime] ERROR {type(exc).__name__}: {exc}")
            raise
        finally:
            self._set_user_active(False)
