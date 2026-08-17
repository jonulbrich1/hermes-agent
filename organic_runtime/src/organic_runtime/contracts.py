from __future__ import annotations

from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


class Route(str, Enum):
    CONVERSATION = "conversation"
    INTERNAL_STATE = "internal_state"
    KNOWN_ROUTE = "known_route"
    ORGANIC_CORE = "organic_core"
    GROWTH = "growth"


class EntityRef(BaseModel):
    text: str
    canonical_uid: str | None = None
    entity_type: str | None = None
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)


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
    reasons: list[str] = Field(default_factory=list)


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


class CoreRequest(BaseModel):
    envelope: IntentEnvelope
    preflight: PreflightKnowledge
    growth: GrowthResult | None = None


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
