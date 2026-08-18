from __future__ import annotations

from time import perf_counter
from uuid import uuid4

from organic_runtime.adapters.base import CoreAdapter, GrowthAdapter, MemoryAdapter
from organic_runtime.contracts import (
    CognitiveResourcePlan,
    CoreRequest,
    GateContext,
    IntentEnvelope,
    Route,
    RuntimeResponse,
)
from organic_runtime.gate.policy import GatePolicy
from organic_runtime.planning.resource_planner import CognitiveResourcePlanner
from organic_runtime.routing.router import OrganicRouter
from organic_runtime.semantic.base import SemanticInterface
from organic_runtime.state.store import InMemoryStateStore
from organic_runtime.tooling.registry import ToolRegistry
from organic_runtime.tracing.events import TraceRecorder


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
        planner: CognitiveResourcePlanner | None = None,
    ) -> None:
        self.semantic = semantic
        self.memory = memory
        self.core = core
        self.growth = growth
        self.gate = gate
        self.state = state
        self.traces = traces
        self.tools = tools
        self.planner = planner or CognitiveResourcePlanner()
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

    async def handle(
        self,
        request: str,
        semantic_envelope: IntentEnvelope | dict | None = None,
    ) -> RuntimeResponse:
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
        resource_plan: CognitiveResourcePlan | None = None
        outcome_recorded = False
        route_started = perf_counter()

        try:
            print(f"[runtime] trace={trace_id} request={request!r}")
            if semantic_envelope is None:
                envelope = await self.semantic.analyze(request, initial_state)
            else:
                envelope = (
                    semantic_envelope
                    if isinstance(semantic_envelope, IntentEnvelope)
                    else IntentEnvelope.model_validate(semantic_envelope)
                )
                if envelope.original_request != request:
                    envelope = envelope.model_copy(update={"original_request": request})
                self.traces.record(
                    trace_id,
                    "semantic_envelope_supplied",
                    {"source": "hermes_tool_call", "trusted": False},
                )
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

            resource_plan = self.planner.plan(envelope, preflight, decision)
            self.traces.record(
                trace_id,
                "cognitive_resource_plan",
                resource_plan.model_dump(mode="json"),
            )
            print(
                f"[planner] plan={resource_plan.plan_id} world={resource_plan.world_mode.value} "
                f"capabilities={resource_plan.processor_capabilities}"
            )

            answer, route_metadata = await self.router.execute(
                decision,
                envelope,
                preflight,
                initial_state,
                resource_plan,
            )
            self.traces.record(trace_id, "route_executed", route_metadata)
            presenter = getattr(self.semantic, "present_result", None)
            if callable(presenter) and decision.route.value not in {
                "conversation",
                "internal_state",
            }:
                organic_answer = answer
                answer = await presenter(request, envelope, organic_answer, route_metadata)
                route_metadata["semantic_interface_presented"] = True
                route_metadata["organic_result_length"] = len(organic_answer)
                self.traces.record(
                    trace_id,
                    "semantic_interface_presentation",
                    {
                        "pass": 0,
                        "organic_result": organic_answer,
                        "presented_result": answer,
                    },
                )
            completeness_reviewer = getattr(self.semantic, "review_completeness", None)
            if callable(completeness_reviewer) and decision.route.value not in {
                "conversation",
                "internal_state",
            }:
                semantic_review = await completeness_reviewer(
                    request,
                    envelope,
                    answer,
                    route_metadata,
                )
                review_payload = semantic_review.model_dump(mode="json")
                route_metadata["semantic_interface_completeness"] = review_payload
                route_metadata["semantic_interface_completeness_checked"] = True
                route_metadata["semantic_interface_tool_loop_requested"] = bool(
                    decision.route == Route.GROWTH
                    and (semantic_review.needs_tool_loop or not semantic_review.complete)
                )
                self.traces.record(
                    trace_id,
                    "semantic_interface_completeness_check",
                    review_payload,
                )
                if decision.route == Route.GROWTH and not semantic_review.complete:
                    missing_text = " ".join(semantic_review.missing[:4]).strip()
                    refined_envelope = envelope.model_copy(
                        update={
                            "normalized_request": " ".join(
                                part
                                for part in (
                                    envelope.normalized_request,
                                    missing_text,
                                    "prefer a current primary or official source",
                                )
                                if part
                            )
                        }
                    )
                    refined_growth = await self.growth.grow(refined_envelope)
                    refined_result = await self.core.process(
                        CoreRequest(
                            envelope=refined_envelope,
                            preflight=preflight,
                            growth=refined_growth,
                            resource_plan=resource_plan,
                        )
                    )
                    answer = refined_result.answer
                    prior_evidence_count = int(route_metadata.get("growth_evidence_count") or 0)
                    prior_durable = list(route_metadata.get("durable_candidate_ids") or [])
                    route_metadata.update(refined_growth.metadata)
                    route_metadata.update(refined_result.metadata)
                    route_metadata.update(
                        {
                            "growth_invoked": True,
                            "growth_evidence_count": prior_evidence_count
                            + len(refined_growth.evidence),
                            "durable_candidate_ids": list(
                                dict.fromkeys(
                                    [*prior_durable, *refined_growth.durable_candidate_ids]
                                )
                            ),
                            "activated_memory_ids": refined_result.activated_memory_ids,
                            "active_paths": refined_result.active_paths,
                            "semantic_interface_tool_loop_passes": 1,
                            "semantic_interface_tool_loop_query": refined_envelope.normalized_request,
                        }
                    )
                    if callable(presenter):
                        refined_organic_answer = answer
                        answer = await presenter(
                            request,
                            envelope,
                            refined_organic_answer,
                            route_metadata,
                        )
                        route_metadata["semantic_interface_presented"] = True
                        route_metadata["organic_result_length"] = len(refined_organic_answer)
                        self.traces.record(
                            trace_id,
                            "semantic_interface_presentation",
                            {
                                "pass": 1,
                                "organic_result": refined_organic_answer,
                                "presented_result": answer,
                            },
                        )
                    semantic_review = await completeness_reviewer(
                        request,
                        envelope,
                        answer,
                        route_metadata,
                    )
                    review_payload = semantic_review.model_dump(mode="json")
                    route_metadata["semantic_interface_completeness"] = review_payload
                    route_metadata["semantic_interface_tool_loop_requested"] = True
                    route_metadata["semantic_interface_tool_loop_complete"] = bool(
                        semantic_review.complete
                    )
                    self.traces.record(
                        trace_id,
                        "semantic_interface_completeness_tool_loop",
                        {
                            "pass": 1,
                            "refined_request": refined_envelope.normalized_request,
                            "review": review_payload,
                        },
                    )
            self.state.record_success(decision.route)

            planner_success = not bool(route_metadata.get("hard_blocked"))
            if envelope.self_contained_reasoning:
                planner_success = bool(route_metadata.get("semantic_completeness_complete"))
            decision_metadata = route_metadata.get("decision")
            result_code = (
                str(decision_metadata.get("action") or "COMPLETED")
                if isinstance(decision_metadata, dict)
                else "COMPLETED"
            )
            processor_cycles = int(route_metadata.get("processor_cycles") or 0)
            non_processor_steps = sum(
                step.resource.value != "organic_processor" for step in resource_plan.steps
            )
            outcome = {
                "duration_ms": round((perf_counter() - route_started) * 1000.0, 3),
                "result_code": result_code,
                "route": decision.route.value,
                "trace_id": trace_id,
                "cost_units": processor_cycles + non_processor_steps,
                "authorized_budget_units": sum(step.budget for step in resource_plan.steps),
            }
            self.planner.record_outcome(resource_plan, planner_success, outcome)
            outcome_recorded = True
            self.traces.record(
                trace_id,
                "cognitive_resource_plan_outcome",
                {"plan_id": resource_plan.plan_id, "success": planner_success, **outcome},
            )

            response = RuntimeResponse(
                request_id=envelope.request_id,
                trace_id=trace_id,
                route=decision.route,
                answer=answer,
                gate=decision,
                metadata={
                    "semantic_suggested_route": envelope.suggested_route.value,
                    "preflight_confidence": preflight.confidence,
                    "resource_plan_id": resource_plan.plan_id,
                    "planner_world_mode": resource_plan.world_mode.value,
                    "planner_task_signature": resource_plan.task_signature,
                    "planner_prohibited_resources": [
                        resource.value for resource in resource_plan.prohibited_resources
                    ],
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
            if resource_plan is not None and not outcome_recorded:
                outcome = {
                    "duration_ms": round((perf_counter() - route_started) * 1000.0, 3),
                    "result_code": type(exc).__name__,
                    "trace_id": trace_id,
                    "error": str(exc),
                    "cost_units": sum(step.budget for step in resource_plan.steps),
                    "authorized_budget_units": sum(step.budget for step in resource_plan.steps),
                }
                try:
                    self.planner.record_outcome(resource_plan, False, outcome)
                except Exception as record_exc:
                    outcome["recording_error"] = str(record_exc)
                self.traces.record(
                    trace_id,
                    "cognitive_resource_plan_outcome",
                    {"plan_id": resource_plan.plan_id, "success": False, **outcome},
                )
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
