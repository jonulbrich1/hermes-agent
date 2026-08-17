from __future__ import annotations

from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field, model_validator


class Route(str, Enum):
    CONVERSATION = "conversation"
    INTERNAL_STATE = "internal_state"
    KNOWN_ROUTE = "known_route"
    ORGANIC_CORE = "organic_core"
    GROWTH = "growth"


class WorldMode(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    MIXED = "mixed"


class ActiveWeaveScope(str, Enum):
    TASK_LOCAL_PREMISE = "task_local_premise"
    GROUNDED_MEMORY = "grounded_memory"
    FOREIGN_CANDIDATE = "foreign_candidate"
    UNVERIFIED_EVIDENCE = "unverified_evidence"
    HYPOTHESIS = "hypothesis"
    PROCESSOR_DERIVATION = "processor_derivation"


class ActiveWeaveTrust(str, Enum):
    USER_SUPPLIED = "user_supplied"
    VALIDATED = "validated"
    UNVERIFIED = "unverified"
    DERIVED = "derived"


class CognitiveResource(str, Enum):
    SEMANTIC_INTERFACE = "semantic_interface"
    LIVING_MEMORY = "living_memory"
    COGNITION = "cognition"
    ORGANIC_PROCESSOR = "organic_processor"
    GRAPH_BROKER = "graph_broker"
    EVIDENCE_WEB = "evidence_web"
    MEMORY_COMPILER_VALIDATOR = "memory_compiler_validator"
    RESULT_VALIDATOR = "result_validator"
    AUTHORIZED_TOOL = "authorized_tool"


class EntityRef(BaseModel):
    text: str
    canonical_uid: str | None = None
    entity_type: str | None = None
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)

    @model_validator(mode="before")
    @classmethod
    def normalize_semantic_entity(cls, value: Any) -> Any:
        """Normalize common model-produced shapes into the runtime contract."""
        if isinstance(value, str):
            return {"text": value}
        if not isinstance(value, dict) or "text" in value:
            return value

        text = next(
            (
                value.get(key)
                for key in ("value", "name", "label", "entity", "mention")
                if value.get(key) not in (None, "")
            ),
            None,
        )
        if text is None:
            return value

        normalized = {
            "text": str(text),
            "confidence": value.get("confidence", 0.5),
        }
        entity_type = value.get("entity_type") or value.get("type") or value.get("category")
        canonical_uid = value.get("canonical_uid") or value.get("uid") or value.get("id")
        if entity_type not in (None, ""):
            normalized["entity_type"] = str(entity_type)
        if canonical_uid not in (None, ""):
            normalized["canonical_uid"] = str(canonical_uid)
        return normalized


class IntentEnvelope(BaseModel):
    request_id: str = Field(default_factory=lambda: str(uuid4()))
    original_request: str
    normalized_request: str
    intent: str
    entities: list[EntityRef] = Field(default_factory=list)
    required_capabilities: list[str] = Field(default_factory=list)
    likely_memory_domains: list[str] = Field(default_factory=list)
    requested_output: str = "text"
    complexity: float = Field(default=0.5, ge=0.0, le=1.0)
    uncertainty: float = Field(default=0.5, ge=0.0, le=1.0)
    suggested_route: Route = Route.ORGANIC_CORE
    requires_current_external_info: bool = False
    self_contained_reasoning: bool = False
    closed_world: bool = False
    sufficient_premises: bool = False
    reasoning_family: str | None = None
    reasoning_goal: str | None = None
    structural_constraints: list[dict[str, Any]] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)


class ActiveWeaveItem(BaseModel):
    uid: str = Field(default_factory=lambda: str(uuid4()))
    role: str
    item_type: str
    scope: ActiveWeaveScope
    trust: ActiveWeaveTrust
    provenance: dict[str, Any] = Field(default_factory=dict)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    uncertainty: float = Field(default=0.5, ge=0.0, le=1.0)
    payload: dict[str, Any] = Field(default_factory=dict)


class ActiveWeave(BaseModel):
    weave_id: str = Field(default_factory=lambda: str(uuid4()))
    request_id: str
    family: str
    goal: str
    items: list[ActiveWeaveItem] = Field(default_factory=list)
    durable_memory_allowed: bool = False
    external_resources_allowed: bool = False


class PreflightKnowledge(BaseModel):
    known_route: bool = False
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    direct_answer: str | None = None
    route_name: str | None = None
    memory_ids: list[str] = Field(default_factory=list)
    requires_research: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class RuntimeStateSnapshot(BaseModel):
    status: str = "healthy"
    core_available: bool = True
    memory_available: bool = True
    growth_available: bool = True
    requests_total: int = 0
    route_counts: dict[str, int] = Field(default_factory=dict)
    last_error: str | None = None
    conversation_context: dict[str, Any] = Field(default_factory=dict)


class GateContext(BaseModel):
    state: RuntimeStateSnapshot
    preflight: PreflightKnowledge
    state_query_allowed: bool = True


class GateDecision(BaseModel):
    request_id: str
    route: Route
    authorized: bool = True
    escalated: bool = False
    reasons: list[str] = Field(default_factory=list)


class GrowthEvidence(BaseModel):
    source_id: str
    content: str
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class GrowthResult(BaseModel):
    evidence: list[GrowthEvidence] = Field(default_factory=list)
    durable_candidate_ids: list[str] = Field(default_factory=list)
    summary: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class ResourcePlanStep(BaseModel):
    order: int = Field(ge=1)
    resource: CognitiveResource
    capability: str
    required: bool = True
    budget: int = Field(default=1, ge=1)
    reasons: list[str] = Field(default_factory=list)


class CognitiveResourcePlan(BaseModel):
    plan_id: str = Field(default_factory=lambda: str(uuid4()))
    request_id: str
    route: Route
    world_mode: WorldMode
    task_signature: list[str] = Field(default_factory=list)
    steps: list[ResourcePlanStep] = Field(default_factory=list)
    processor_capabilities: list[str] = Field(default_factory=list)
    prohibited_resources: list[CognitiveResource] = Field(default_factory=list)
    max_processor_cycles: int = Field(default=1, ge=1, le=128)
    score: float = Field(default=0.0, ge=0.0, le=1.0)
    reasons: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class CoreRequest(BaseModel):
    envelope: IntentEnvelope
    preflight: PreflightKnowledge
    growth: GrowthResult | None = None
    resource_plan: CognitiveResourcePlan | None = None


class CoreResult(BaseModel):
    answer: str
    activated_memory_ids: list[str] = Field(default_factory=list)
    active_paths: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class RuntimeResponse(BaseModel):
    request_id: str
    trace_id: str
    route: Route
    answer: str
    gate: GateDecision
    metadata: dict[str, Any] = Field(default_factory=dict)


class SemanticCompletenessReview(BaseModel):
    complete: bool
    needs_tool_loop: bool = False
    missing: list[str] = Field(default_factory=list)
    reason: str = ""
