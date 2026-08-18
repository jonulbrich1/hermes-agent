from __future__ import annotations

from collections.abc import Callable
from typing import Any

from organic_runtime.contracts import (
    CognitiveResource,
    CognitiveResourcePlan,
    GateDecision,
    IntentEnvelope,
    PreflightKnowledge,
    ResourcePlanStep,
    Route,
    WorldMode,
)

RESOURCE_CAPABILITIES: dict[CognitiveResource, dict[str, Any]] = {
    CognitiveResource.LIVING_MEMORY: {
        "capabilities": ["retrieve_known_information"],
        "cost": "low",
        "external_evidence": False,
    },
    CognitiveResource.COGNITION: {
        "capabilities": ["build_active_weave", "build_constraint_weave"],
        "cost": "low",
        "external_evidence": False,
    },
    CognitiveResource.ORGANIC_PROCESSOR: {
        "capabilities": [
            "arithmetic",
            "cardinality",
            "causal_chain",
            "comparison",
            "constraint_reasoning",
            "contradiction",
            "grounded_evidence_selection",
            "object_unification",
            "relative_order",
        ],
        "requires": ["structured_active_weave"],
        "cost": "medium",
        "external_evidence": False,
    },
    CognitiveResource.GRAPH_BROKER: {
        "capabilities": ["candidate_entities", "candidate_relationships"],
        "cost": "medium",
        "trust": "unverified",
    },
    CognitiveResource.EVIDENCE_WEB: {
        "capabilities": ["acquire_missing_world_information"],
        "cost": "high",
        "trust": "unverified",
    },
    CognitiveResource.MEMORY_COMPILER_VALIDATOR: {
        "capabilities": ["compile_and_validate_evidence"],
        "cost": "medium",
    },
    CognitiveResource.RESULT_VALIDATOR: {
        "capabilities": ["verify_constraints", "verify_grounding"],
        "cost": "low",
    },
    CognitiveResource.AUTHORIZED_TOOL: {
        "capabilities": ["execute_authorized_tool"],
        "cost": "variable",
    },
    CognitiveResource.SEMANTIC_INTERFACE: {
        "capabilities": ["understand_human", "present_result"],
        "cost": "low",
    },
}


class CognitiveResourcePlanner:
    """Deterministically authorize resources without solving the request."""

    def __init__(
        self,
        max_processor_cycles: int = 16,
        outcome_recorder: Callable[[CognitiveResourcePlan, bool, dict[str, Any]], None]
        | None = None,
    ) -> None:
        self.max_processor_cycles = max(4, min(int(max_processor_cycles), 128))
        self._outcome_recorder = outcome_recorder

    def plan(
        self,
        envelope: IntentEnvelope,
        preflight: PreflightKnowledge,
        decision: GateDecision,
    ) -> CognitiveResourcePlan:
        if decision.route == Route.CONVERSATION:
            return self._simple_plan(
                envelope,
                decision.route,
                CognitiveResource.SEMANTIC_INTERFACE,
                "respond_to_conversation",
            )
        if decision.route == Route.INTERNAL_STATE:
            return self._simple_plan(
                envelope,
                decision.route,
                CognitiveResource.AUTHORIZED_TOOL,
                "read_internal_state",
            )
        if decision.route == Route.KNOWN_ROUTE:
            return CognitiveResourcePlan(
                request_id=envelope.request_id,
                route=decision.route,
                world_mode=WorldMode.CLOSED,
                task_signature=["known_information_retrieval"],
                steps=[
                    self._step(1, CognitiveResource.LIVING_MEMORY, "retrieve_known_information"),
                    self._step(2, CognitiveResource.RESULT_VALIDATOR, "verify_grounding"),
                ],
                max_processor_cycles=1,
                score=0.96,
                reasons=["The Interaction Gate authorized an existing grounded memory route."],
            )
        if decision.route == Route.GROWTH:
            return self._growth_plan(envelope, decision.route)
        if envelope.self_contained_reasoning:
            return self._closed_world_plan(envelope, decision.route)

        return CognitiveResourcePlan(
            request_id=envelope.request_id,
            route=decision.route,
            world_mode=WorldMode.MIXED,
            task_signature=["grounded_reasoning"],
            steps=[
                self._step(1, CognitiveResource.LIVING_MEMORY, "retrieve_known_information"),
                self._step(2, CognitiveResource.COGNITION, "build_active_weave"),
                self._step(3, CognitiveResource.ORGANIC_PROCESSOR, "grounded_evidence_selection"),
                self._step(4, CognitiveResource.RESULT_VALIDATOR, "verify_grounding"),
            ],
            processor_capabilities=["grounded_evidence_selection"],
            max_processor_cycles=self.max_processor_cycles,
            score=0.82 if preflight.memory_ids else 0.62,
            reasons=["The request requires cognition over available grounded information."],
        )

    def record_outcome(
        self,
        plan: CognitiveResourcePlan,
        success: bool,
        metadata: dict[str, Any],
    ) -> None:
        if self._outcome_recorder is not None:
            self._outcome_recorder(plan, success, metadata)

    def _closed_world_plan(
        self,
        envelope: IntentEnvelope,
        route: Route,
    ) -> CognitiveResourcePlan:
        capability = envelope.reasoning_family or "structural_reasoning"
        signature = [
            "closed_world",
            capability,
            *sorted(str(item).lower() for item in envelope.required_operations),
            *sorted(
                str(item.get("kind") or "constraint")
                for item in envelope.structural_constraints
            ),
        ]
        steps = [
            self._step(
                1,
                CognitiveResource.COGNITION,
                "build_constraint_weave",
                budget=1,
                reason="The user supplied the premises; construct temporary working memory.",
            )
        ]
        steps.append(
            self._step(
                len(steps) + 1,
                CognitiveResource.ORGANIC_PROCESSOR,
                capability,
                budget=self.max_processor_cycles,
                reason="Search bounded processor pathways over the structural Active Weave.",
            )
        )
        steps.append(
            self._step(
                len(steps) + 1,
                CognitiveResource.RESULT_VALIDATOR,
                "verify_constraints",
                reason="Present only a model that satisfies every parsed premise.",
            )
        )
        return CognitiveResourcePlan(
            request_id=envelope.request_id,
            route=route,
            world_mode=WorldMode.CLOSED,
            task_signature=signature,
            steps=steps,
            processor_capabilities=[capability],
            prohibited_resources=[
                CognitiveResource.LIVING_MEMORY,
                CognitiveResource.GRAPH_BROKER,
                CognitiveResource.EVIDENCE_WEB,
                CognitiveResource.MEMORY_COMPILER_VALIDATOR,
            ],
            max_processor_cycles=self.max_processor_cycles,
            score=0.98 if envelope.sufficient_premises else 0.60,
            reasons=[
                "All required premises are present in the user request.",
                "External factual retrieval cannot improve a closed-world logical result.",
            ],
            metadata={
                "planning_policy": "deterministic_scored_v1",
                "required_operations": list(envelope.required_operations),
            },
        )

    def _growth_plan(self, envelope: IntentEnvelope, route: Route) -> CognitiveResourcePlan:
        return CognitiveResourcePlan(
            request_id=envelope.request_id,
            route=route,
            world_mode=WorldMode.OPEN,
            task_signature=["missing_world_information", *envelope.required_capabilities],
            steps=[
                self._step(1, CognitiveResource.LIVING_MEMORY, "retrieve_known_information"),
                self._step(2, CognitiveResource.GRAPH_BROKER, "candidate_entities"),
                self._step(3, CognitiveResource.EVIDENCE_WEB, "acquire_missing_world_information"),
                self._step(
                    4,
                    CognitiveResource.MEMORY_COMPILER_VALIDATOR,
                    "compile_and_validate_evidence",
                ),
                self._step(5, CognitiveResource.COGNITION, "build_active_weave"),
                self._step(6, CognitiveResource.ORGANIC_PROCESSOR, "grounded_evidence_selection"),
                self._step(7, CognitiveResource.RESULT_VALIDATOR, "verify_grounding"),
            ],
            processor_capabilities=["grounded_evidence_selection"],
            max_processor_cycles=self.max_processor_cycles,
            score=0.90,
            reasons=["The gate identified missing or current world information."],
        )

    @staticmethod
    def _step(
        order: int,
        resource: CognitiveResource,
        capability: str,
        budget: int = 1,
        reason: str | None = None,
    ) -> ResourcePlanStep:
        return ResourcePlanStep(
            order=order,
            resource=resource,
            capability=capability,
            budget=budget,
            reasons=[reason] if reason else [],
        )

    @staticmethod
    def _simple_plan(
        envelope: IntentEnvelope,
        route: Route,
        resource: CognitiveResource,
        capability: str,
    ) -> CognitiveResourcePlan:
        return CognitiveResourcePlan(
            request_id=envelope.request_id,
            route=route,
            world_mode=WorldMode.CLOSED,
            task_signature=[capability],
            steps=[ResourcePlanStep(order=1, resource=resource, capability=capability)],
            max_processor_cycles=1,
            score=0.99,
            reasons=["The authorized route requires one bounded resource."],
        )
