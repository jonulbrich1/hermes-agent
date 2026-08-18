from __future__ import annotations

import json
import os
import hashlib
import platform
import re
import shutil
from pathlib import Path
from time import perf_counter
from typing import Any
from uuid import uuid4

from organic_mvp.config import AppConfig
from organic_mvp.core import build_core
from organic_mvp.db import MemoryDB
from organic_mvp.evidence import EvidenceBroker
from organic_mvp.loggingx import AuditLog, setup_logging
from organic_mvp.memory import MemoryCompiler
from organic_mvp.review import ReviewExporter
from organic_mvp.scheduler import OrganicEngine
from organic_mvp.util import content_words, norm_space, stable_uid, utcnow
from organic_processor import LegacyLogicSeedAdapter, OrganicProcessor, StructuralTask

from organic_runtime.config import RuntimeSettings
from organic_runtime.contracts import (
    ActiveWeave,
    ActiveWeaveItem,
    ActiveWeaveScope,
    ActiveWeaveTrust,
    CognitiveResourcePlan,
    CoreRequest,
    CoreResult,
    GrowthEvidence,
    GrowthResult,
    IntentEnvelope,
    PreflightKnowledge,
)
from organic_runtime.instance_lock import RuntimeDataLock
from organic_runtime.cognition import build_active_weave, compile_structural_task, verify_trace
from organic_runtime.semantic.base import clarification_subject


KNOWN_BOOTSTRAP_ANSWERS = {
    "organic ai routing principle": (
        "mem-routing-principle",
        "The Semantic Interface may recommend a route, but the programmatic Interaction "
        "Gate authorizes it. Uncertainty escalates rather than bypassing the Organic Core.",
    ),
    "bounded core": (
        "mem-bounded-core",
        "The learned core remains bounded while durable domain knowledge lives in external "
        "memory, cognition structures, procedures, indexes, and tools.",
    ),
    "gate is deterministic": (
        "mem-deterministic-gate",
        "The gate is deterministic so a model suggestion is never enough to authorize a "
        "shortcut, privileged tool, factual answer, or memory write.",
    ),
}

ACTIVE_WEAVE_MIN_RETRIEVAL_SCORE = 0.45


def _git_commit(project_root: Path | None) -> str | None:
    if project_root is None:
        return None
    head_path = project_root / ".git" / "HEAD"
    try:
        head = head_path.read_text(encoding="utf-8").strip()
        if head.startswith("ref: "):
            return (project_root / ".git" / head[5:]).read_text(encoding="utf-8").strip()
        return head or None
    except OSError:
        return None


def _format_boolean_entailment(task: StructuralTask, answer: bool) -> str:
    if not answer:
        return "No. The requested relation does not hold in every allowed assignment of the unknown values."
    unknowns = [
        str(item.get("entity") or "")
        for item in task.constraints
        if item.get("kind") == "entity_property" and item.get("value") is None
    ]
    if len(unknowns) == 1:
        entity = unknowns[0]
        relations = [item for item in task.constraints if item.get("kind") == "directed_relation"]
        known_properties = {
            str(item.get("entity") or ""): item.get("value")
            for item in task.constraints
            if item.get("kind") == "entity_property" and item.get("value") is not None
        }
        false_witness = next(
            (
                item
                for item in relations
                if known_properties.get(str(item.get("subject") or "")) is True
                and str(item.get("object") or "") == entity
            ),
            None,
        )
        true_witness = next(
            (
                item
                for item in relations
                if str(item.get("subject") or "") == entity
                and known_properties.get(str(item.get("object") or "")) is False
            ),
            None,
        )
        if false_witness and true_witness:
            return (
                f"Yes. If {entity} is unmarried, {false_witness['subject']} looks at {entity}; "
                f"if {entity} is married, {entity} looks at {true_witness['object']}. "
                "The conclusion holds in every assignment of the unknown value."
            )
    return "Yes. The conclusion holds in every allowed assignment of the unknown values."


class MvpOrganicSystem:
    """Shared bridge from the scaffold contracts into the hardened MVP modules."""

    def __init__(self, settings: RuntimeSettings) -> None:
        self.settings = settings
        self.root = settings.mvp_data_dir.expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        for rel in (
            "data/logs",
            "data/source_cache",
            "data/core",
            "review_packages",
            "run_results",
        ):
            (self.root / rel).mkdir(parents=True, exist_ok=True)

        self._closed = False
        self._instance_lock = RuntimeDataLock(self.root / "data" / "runtime.lock")
        self._instance_lock.acquire()

        try:
            self.config = AppConfig(self.root)
            public: dict[str, Any] = {
                "idle_growth_enabled": settings.idle_growth_enabled,
                "idle_delay_seconds": settings.idle_delay_seconds,
                "web_provider": settings.web_provider,
                "allow_private_web": settings.allow_private_web,
                "semantic_agent_enabled": False,
                "auto_review_user_tasks": False,
                "auto_review_idle_every": 0,
                "interaction_fetch_limit": 2,
                "interaction_search_results": 4,
                "max_sentences_per_source": 350,
            }
            if settings.local_corpus_dir:
                public["local_corpus_dir"] = str(settings.local_corpus_dir)
            self.config.update(public=public)

            self.logger = setup_logging(self.root / "data" / "logs" / "debug.log", verbose=False)
            self.audit = AuditLog(self.root / "data" / "logs" / "audit.jsonl")
            self.db = MemoryDB(self.root / "data" / "organic_memory.sqlite")
            self.executive = build_core(self.config, self.db, self.logger)
            self.core = self.executive
            self.processor = OrganicProcessor(
                self.root / "data" / "processor" / "processor_state.json",
                max_state_bytes=min(
                    int(self.config.get("max_core_bytes", 5 * 1024**3)), 16 * 1024**2
                ),
            )
            self.legacy_processor = LegacyLogicSeedAdapter(
                self.root / "runtime" / "legacy_seed" / "v0.2.1"
            )
            self.broker = EvidenceBroker(self.root, self.config, self.db, self.logger, self.audit)
            self.memory = MemoryCompiler(self.db, self.executive, self.logger, self.audit)
            project_root_value = os.getenv("ORGANIC_PROJECT_ROOT")
            project_root = (
                Path(project_root_value).expanduser().resolve() if project_root_value else None
            )
            self.review = ReviewExporter(
                self.root,
                platform.system().lower() or os.name,
                self.config,
                self.db,
                self.memory,
                self.logger,
                self.audit,
                trace_dir=settings.trace_dir,
                runtime_metadata={
                    "backend": settings.backend,
                    "semantic_mode": settings.semantic_mode,
                    "semantic_model": settings.model,
                    "ollama_base_url": settings.ollama_base_url,
                    "web_provider": settings.web_provider,
                    "idle_growth_enabled": settings.idle_growth_enabled,
                    "processor_max_cycles": settings.processor_max_cycles,
                    "hermes_mode": os.getenv("ORGANIC_HERMES_MODE", "0") == "1",
                    "hermes_plugin_enabled": os.getenv("ORGANIC_HERMES_PLUGIN_ENABLED", "0") == "1",
                    "shared_runtime_url": os.getenv("ORGANIC_RUNTIME_URL", "http://127.0.0.1:8788"),
                    "project_root": str(project_root) if project_root else None,
                    "git_commit": _git_commit(project_root),
                },
            )
            self._recover_interrupted_tasks()
            self.engine = OrganicEngine(
                self.root,
                self.config,
                self.db,
                self.executive,
                self.broker,
                self.memory,
                self.logger,
                self.audit,
                processor=self.processor,
                processor_growth_callback=self._run_processor_growth_cycle,
            )
            self.core_calls = 0
            self.growth_calls = 0
            self.engine.start()
            self._ensure_bootstrap_growth_task()
        except BaseException:
            self._instance_lock.release()
            raise

    def _recover_interrupted_tasks(self) -> None:
        rows = self.db.query("SELECT * FROM tasks WHERE status='ACTIVE'")
        for row in rows:
            task_id = row["task_id"]
            kind = row["kind"]
            if kind == "INTERACTIVE_TASK":
                self.db.update_task(
                    task_id,
                    status="PARTIAL",
                    completed_at=utcnow(),
                    result_text="Interrupted before completion; marked partial during runtime recovery.",
                )
                self.db.add_task_event(
                    task_id,
                    "RECOVERED_INTERRUPTED_INTERACTION",
                    "Marked partial on startup because no active runtime owns this task.",
                )
                continue
            self.db.update_task(task_id, status="PENDING")
            self.db.add_task_event(
                task_id,
                "RECOVERED_INTERRUPTED_GROWTH",
                "Returned active task to pending on startup because no active runtime owns it.",
            )
        if rows:
            self.audit.write("interrupted_tasks_recovered", count=len(rows))

    def _run_processor_growth_cycle(self) -> dict[str, Any]:
        task = self.processor.next_growth_task()
        if task is None:
            return {"attempted": False}
        discovery = self.processor.discover(task)
        attempts = list(discovery.alternatives)
        accepted_trace = None
        rewards: list[dict[str, Any]] = []
        for trace in attempts:
            verification = verify_trace(task, trace)
            self.processor.learn(trace, verification.reward, "idle_result_validator")
            rewards.append(
                {
                    "pathway": trace.pathway,
                    "reward": verification.reward,
                    "accepted": verification.accepted,
                    "result_code": verification.result_code,
                }
            )
            if verification.accepted and accepted_trace is None:
                accepted_trace = trace
        if accepted_trace is not None:
            self.processor.promote_discovered(task, accepted_trace)
            status = "RESOLVED"
        else:
            self.processor.record_growth_failure(
                task,
                discovery.capability_gap or "No discovered pathway passed external verification.",
            )
            status = "WAITING_FOR_PRIMITIVE"
        result = {
            "attempted": True,
            "status": status,
            "capability_signature": task.capability_signature(),
            "candidate_count": len(attempts),
            "selected_pathway": accepted_trace.pathway if accepted_trace else None,
            "rewards": rewards,
        }
        self.audit.write("processor_growth_attempt", **result)
        return result

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self.engine.stop()
            self._recover_interrupted_tasks()
            self.db.close()
        finally:
            self._instance_lock.release()

    def set_user_active(self, active: bool) -> None:
        self.engine.set_external_user_active(active)

    def provider_status(self) -> dict[str, Any]:
        return self.broker.provider_status()

    def record_resource_plan_outcome(
        self,
        plan: CognitiveResourcePlan,
        success: bool,
        metadata: dict[str, Any],
    ) -> None:
        self.db.record_planner_outcome(
            plan_id=plan.plan_id,
            request_id=plan.request_id,
            task_signature=plan.task_signature,
            resources=[step.model_dump(mode="json") for step in plan.steps],
            world_mode=plan.world_mode.value,
            success=success,
            cost_units=float(metadata.get("cost_units", 0.0) or 0.0),
            duration_ms=float(metadata.get("duration_ms", 0.0) or 0.0),
            result_code=str(metadata.get("result_code") or "UNKNOWN"),
            metadata={
                **metadata,
                "route": plan.route.value,
                "processor_capabilities": plan.processor_capabilities,
                "prohibited_resources": [resource.value for resource in plan.prohibited_resources],
                "planning_policy": plan.metadata.get("planning_policy"),
            },
        )

    def export_review(self, reason: str = "manual") -> str:
        path = Path(self.review.export(reason))
        review_dir_value = os.getenv("ORGANIC_REVIEW_DIR")
        project_root_value = os.getenv("ORGANIC_PROJECT_ROOT")
        review_dir = (
            Path(review_dir_value).expanduser().resolve()
            if review_dir_value
            else Path(project_root_value).expanduser().resolve() / "reveiw"
            if project_root_value
            else None
        )
        if review_dir is None:
            return str(path)
        review_dir.mkdir(parents=True, exist_ok=True)
        mirrored = review_dir / path.name
        if mirrored.resolve() != path.resolve():
            shutil.copy2(path, mirrored)
        self.audit.write("review_package_mirrored", source=str(path), mirrored=str(mirrored))
        return str(mirrored)

    def record_runtime_result(self, response: Any) -> str:
        payload = response.model_dump(mode="json") if hasattr(response, "model_dump") else response
        trace_id = str(payload.get("trace_id") or stable_uid("trace", utcnow()))
        stamp = utcnow().replace("-", "").replace(":", "").replace(".", "").replace("+00:00", "Z")
        out_dir = self.root / "run_results"
        out_dir.mkdir(parents=True, exist_ok=True)
        out = out_dir / f"runtime_result_{stamp}_{trace_id}.json"
        out.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8"
        )
        self.audit.write("runtime_result_recorded", path=str(out), trace_id=trace_id)
        return str(out)

    def load_rolling_context(self, current_request: str = "") -> dict[str, Any]:
        recent_turns: list[dict[str, Any]] = []
        for row in self.db.recent_conversation(16):
            metadata: dict[str, Any] = {}
            try:
                metadata = json.loads(row["metadata_json"] or "{}")
            except Exception:
                metadata = {}
            recent_turns.append(
                {
                    "role": row["role"],
                    "kind": row["kind"],
                    "text": row["text"][:1200],
                    "task_id": row["task_id"],
                    "route": metadata.get("route"),
                    "trace_id": metadata.get("trace_id"),
                }
            )
        engine_state = self.engine.state()
        return {
            "kind": "rolling_cognition",
            "summary": self.db.get_meta("rolling_cognition_summary") or "",
            "active_objective": self.db.get_meta("rolling_cognition_active_objective") or "",
            "updated_at": self.db.get_meta("rolling_cognition_updated_at"),
            "current_request": current_request,
            "active_task_id": engine_state.get("current_task_id"),
            "idle_growth_enabled": engine_state.get("idle_growth_enabled"),
            "recent_turns": recent_turns,
            "note": "Working conversation context only. It is not trusted Living Memory.",
        }

    def record_conversation_turn(self, request: str, response: Any) -> None:
        payload = (
            response.model_dump(mode="json") if hasattr(response, "model_dump") else dict(response)
        )
        metadata = {
            "request_id": payload.get("request_id"),
            "trace_id": payload.get("trace_id"),
            "route": payload.get("route"),
            "semantic_suggested_route": (payload.get("metadata") or {}).get(
                "semantic_suggested_route"
            ),
            "mvp_task_id": (payload.get("metadata") or {}).get("mvp_task_id"),
            "rolling_cognition": True,
        }
        task_id = metadata.get("mvp_task_id")
        self.db.add_conversation("user", "message", request, task_id, metadata)
        self.db.add_conversation(
            "assistant",
            "message",
            str(payload.get("answer") or ""),
            task_id,
            metadata,
        )
        self._update_rolling_summary(
            request, str(payload.get("answer") or ""), str(payload.get("route") or "")
        )

    def _update_rolling_summary(self, request: str, answer: str, route: str) -> None:
        previous_objective = self.db.get_meta("rolling_cognition_active_objective") or ""
        if route not in {"conversation", "internal_state"} and request.strip():
            active_objective = norm_space(request)[:500]
        else:
            active_objective = previous_objective

        turns = [
            f"{row['role']}: {norm_space(row['text'])[:220]}"
            for row in self.db.recent_conversation(8)
        ]
        parts = []
        if active_objective:
            parts.append(f"Active objective: {active_objective}")
        parts.append("Recent interaction:")
        parts.extend(f"- {turn}" for turn in turns)
        parts.append("Boundary: this is working context only; durable facts require validation.")
        summary = "\n".join(parts)[:3000]
        self.db.set_meta("rolling_cognition_summary", summary)
        self.db.set_meta("rolling_cognition_active_objective", active_objective)
        self.db.set_meta("rolling_cognition_updated_at", utcnow())
        self.audit.write(
            "rolling_cognition_updated",
            active_objective=active_objective,
            route=route,
            request_preview=request[:300],
            answer_preview=answer[:300],
        )

    def _ensure_bootstrap_growth_task(self) -> None:
        if not self.settings.idle_growth_enabled or not self.settings.bootstrap_growth_enabled:
            return
        counts = self.db.counts()
        if counts.get("sources", 0) > 0 or counts.get("pending_tasks", 0) > 0:
            return
        task_id = "task:block:pydantic-bootstrap"
        if self.db.task(task_id):
            return
        query = (
            self.settings.bootstrap_growth_query or "organic ai cognition memory evidence growth"
        )
        self.db.create_task(
            task_id,
            "BLOCKING_GROWTH",
            "PYDANTIC_RUNTIME",
            f"Bootstrap Living Memory so idle growth has a frontier. Query: {query}",
            110,
            generated_reason="Pydantic scaffold startup enabled always-on Organic growth.",
            metadata={"query": query, "bootstrap": True},
        )
        self.db.add_task_event(task_id, "BOOTSTRAP_GROWTH_CREATED", query)
        self.audit.write("bootstrap_growth_created", task_id=task_id, query=query)
        self.engine.wake()

    def preflight(self, envelope: IntentEnvelope) -> PreflightKnowledge:
        text = envelope.normalized_request.lower()
        if envelope.self_contained_reasoning:
            return PreflightKnowledge(
                known_route=False,
                confidence=1.0,
                requires_research=False,
                metadata={
                    "reasoning_mode": "self_contained",
                    "active_weave_claim_count": 0,
                    "memory_lookup_required": False,
                },
            )
        if envelope.intent == "clarification_needed":
            return PreflightKnowledge(
                known_route=False,
                confidence=0.0,
                requires_research=False,
                metadata={"clarification_required": True, "active_weave_claim_count": 0},
            )
        for phrase, (memory_id, answer) in KNOWN_BOOTSTRAP_ANSWERS.items():
            if phrase in text:
                return PreflightKnowledge(
                    known_route=True,
                    confidence=0.97,
                    direct_answer=answer,
                    route_name="mvp_bootstrap_known_route",
                    memory_ids=[memory_id],
                )

        raw_evidence = self.memory.retrieve(envelope.normalized_request, limit=8)
        evidence = self._active_weave_evidence(envelope, raw_evidence)
        raw_top = max(
            (float(e.get("retrieval_score", 0.0) or 0.0) for e in raw_evidence),
            default=0.0,
        )
        top = max((float(e.get("retrieval_score", 0.0) or 0.0) for e in evidence), default=0.0)
        memory_ids = [str(e.get("claim_id")) for e in evidence if e.get("claim_id")]
        direct_answer = str(evidence[0].get("text")) if evidence and top >= 0.80 else None
        known_route = direct_answer is not None
        requires_research = envelope.requires_current_external_info or (
            not evidence
            and "coding" not in envelope.required_capabilities
            and envelope.intent not in {"simple_conversation", "internal_state"}
        )
        return PreflightKnowledge(
            known_route=known_route,
            confidence=top if evidence else min(raw_top, 0.40),
            direct_answer=direct_answer,
            route_name="mvp_living_memory" if known_route else None,
            memory_ids=memory_ids[:8],
            requires_research=requires_research,
            metadata={
                "active_weave_claim_count": len(evidence),
                "raw_retrieval_count": len(raw_evidence),
                "min_active_weave_retrieval_score": ACTIVE_WEAVE_MIN_RETRIEVAL_SCORE,
                "top_retrieval_score": top,
                "raw_top_retrieval_score": raw_top,
                "web": self.provider_status(),
            },
        )

    def answer_known(self, preflight: PreflightKnowledge) -> str:
        if not preflight.direct_answer:
            raise RuntimeError("Known route requested without a direct answer.")
        return preflight.direct_answer

    @staticmethod
    def _entity_terms(envelope: IntentEnvelope) -> set[str]:
        return {
            term
            for entity in envelope.entities
            if entity.confidence >= 0.65
            for term in content_words(entity.text)
        }

    @classmethod
    def _active_weave_evidence(
        cls,
        envelope: IntentEnvelope,
        raw_evidence: list[dict[str, Any]],
        min_retrieval_score: float = ACTIVE_WEAVE_MIN_RETRIEVAL_SCORE,
        require_entity_overlap: bool = True,
    ) -> list[dict[str, Any]]:
        entity_terms = cls._entity_terms(envelope)
        evidence: list[dict[str, Any]] = []
        for item in raw_evidence:
            score = float(item.get("retrieval_score", 0.0) or 0.0)
            if score < min_retrieval_score:
                continue
            if require_entity_overlap and entity_terms:
                evidence_text = " ".join(
                    [
                        str(item.get("text") or ""),
                        *[
                            " ".join(
                                str(relation.get(key) or "")
                                for key in (
                                    "subject_label",
                                    "predicate",
                                    "object_label",
                                    "object_text",
                                )
                            )
                            for relation in (item.get("relations") or [])
                        ],
                    ]
                )
                if not entity_terms.intersection(content_words(evidence_text)):
                    continue
            evidence.append(item)
        return evidence

    def _create_runtime_task(self, request: CoreRequest) -> str:
        envelope = request.envelope
        route = "growth" if request.growth is not None else "organic_core"
        task_id = self.engine.begin_interaction_task(
            envelope.original_request,
            envelope.normalized_request,
            route,
            {
                "route": route,
                "requires_grounding": bool(
                    envelope.requires_current_external_info or request.preflight.requires_research
                ),
                "preflight": request.preflight.model_dump(mode="json"),
            },
            {
                "adapter": "pydantic_runtime",
                "intent_envelope": envelope.model_dump(mode="json"),
            },
        )
        self.db.add_task_event(task_id, "PYDANTIC_USER_TASK_STARTED", envelope.original_request)
        return task_id

    @staticmethod
    def _answer_text(result: dict[str, Any]) -> str:
        return norm_space(str(result.get("answer") or ""))

    @staticmethod
    def _missing_text(result: dict[str, Any]) -> str:
        missing = [str(item) for item in (result.get("missing") or []) if item]
        return "; ".join(missing or ["Insufficient grounded evidence."])

    def _semantic_completeness_review(
        self,
        request: CoreRequest,
        result: dict[str, Any],
        answer: str,
        growth_result: GrowthResult | None,
    ) -> dict[str, Any]:
        confidence = float(result.get("confidence", 0.0) or 0.0)
        missing = [str(item) for item in (result.get("missing") or []) if item]
        source_count = len(result.get("sources") or [])
        growth_evidence_count = len(growth_result.evidence) if growth_result else 0
        requires_grounding = bool(
            request.envelope.intent
            not in {
                "simple_conversation",
                "internal_state",
                "clarification_needed",
            }
            or request.envelope.requires_current_external_info
            or request.preflight.requires_research
            or growth_result is not None
        )
        has_answer = bool(answer)
        relevant_evidence = bool(result.get("active_weave_relevant"))
        grounded_answer_used = has_answer and (
            not requires_grounding
            or (
                confidence >= 0.50
                and relevant_evidence
                and (source_count > 0 or growth_evidence_count > 0)
                and not missing
            )
        )
        complete = has_answer and confidence >= 0.50 and grounded_answer_used
        needs_retry = requires_grounding and not grounded_answer_used and growth_result is None
        hard_blocked = requires_grounding and not grounded_answer_used and growth_result is not None
        return {
            "requires_grounding": requires_grounding,
            "has_answer": has_answer,
            "confidence": confidence,
            "missing": missing,
            "source_count": source_count,
            "growth_evidence_count": growth_evidence_count,
            "relevant_evidence": relevant_evidence,
            "grounded_answer_used": grounded_answer_used,
            "complete": complete,
            "needs_retry": needs_retry,
            "hard_blocked": hard_blocked,
        }

    def _record_completeness_review(self, task_id: str, review: dict[str, Any]) -> None:
        message = (
            "Semantic Interface completeness check accepted the grounded result."
            if review["complete"]
            else "Semantic Interface completeness check found missing grounding."
        )
        self.db.add_task_event(task_id, "SEMANTIC_COMPLETENESS_REVIEW", message, review)

    def _answer_with_core(
        self,
        envelope: IntentEnvelope,
        task_id: str,
        growth_result: GrowthResult | None = None,
    ) -> dict[str, Any]:
        raw_evidence = self.memory.retrieve(
            envelope.normalized_request,
            limit=int(self.config.get("max_active_weave_claims", 18)),
        )
        selection_threshold = ACTIVE_WEAVE_MIN_RETRIEVAL_SCORE
        fresh_growth = bool(growth_result and growth_result.evidence)
        if growth_result and growth_result.evidence:
            fresh_source_ids = {item.source_id for item in growth_result.evidence}
            raw_evidence = self.memory.retrieve_from_sources(
                envelope.normalized_request,
                fresh_source_ids,
                limit=int(self.config.get("max_active_weave_claims", 18)),
            )
            selection_threshold = 0.22
        candidate_evidence = self._active_weave_evidence(
            envelope,
            raw_evidence,
            min_retrieval_score=selection_threshold,
            require_entity_overlap=True,
        )
        request_terms = set(content_words(envelope.normalized_request))
        current_version_objective = bool(
            {"release", "version"} & request_terms
            and {"current", "latest", "newest", "recent", "stable"} & request_terms
        )
        selection_limit = 1 if current_version_objective else 4
        structural_constraints = tuple(
            {
                "kind": "grounded_candidate",
                "index": index,
                "retrieval_score": float(item.get("retrieval_score", 0.0) or 0.0),
                "confidence": float(item.get("confidence", 0.5) or 0.5),
                "selection_threshold": selection_threshold,
                "selection_limit": selection_limit,
            }
            for index, item in enumerate(candidate_evidence)
        )
        structural_task = StructuralTask(
            family="grounded_evidence_selection",
            goal="select_supported_items",
            constraints=structural_constraints,
        )
        active_weave = ActiveWeave(
            request_id=envelope.request_id,
            family=structural_task.family,
            goal=structural_task.goal,
            items=[
                ActiveWeaveItem(
                    role="candidate_evidence",
                    item_type="grounded_claim_reference",
                    scope=ActiveWeaveScope.GROUNDED_MEMORY,
                    trust=ActiveWeaveTrust.VALIDATED,
                    provenance={
                        "claim_id": item.get("claim_id"),
                        "source_id": item.get("source_id"),
                    },
                    confidence=float(item.get("confidence", 0.5) or 0.5),
                    uncertainty=max(0.0, 1.0 - float(item.get("confidence", 0.5) or 0.5)),
                    payload=dict(structural_constraints[index]),
                )
                for index, item in enumerate(candidate_evidence)
            ],
            durable_memory_allowed=False,
            external_resources_allowed=False,
        )
        state_before = self.processor.snapshot()
        before_hash = hashlib.sha256(
            json.dumps(state_before, sort_keys=True).encode("utf-8")
        ).hexdigest()
        started = perf_counter()
        processor_result = self.processor.process(structural_task, explore=True)
        attempts = list(processor_result.alternatives)
        verified = [(trace, verify_trace(structural_task, trace)) for trace in attempts]
        for trace, verification in verified:
            self.processor.learn(trace, verification.reward, "grounded_result_validator")
        selected_pair = next((pair for pair in verified if pair[1].accepted), None)
        selected_trace = selected_pair[0] if selected_pair else None
        selected_indexes = list(selected_trace.answer) if selected_trace else []
        evidence = [
            dict(candidate_evidence[index])
            for index in selected_indexes
            if 0 <= index < len(candidate_evidence)
        ]
        if fresh_growth:
            # Drop malformed first-pass relations without discarding useful compiled
            # relations from the same fresh sources.
            requires_version_relation = current_version_objective
            for item in evidence:
                claim_versions = set(
                    re.findall(r"\b\d+(?:\.\d+){1,3}\b", str(item.get("text") or ""))
                )
                item["relations"] = [
                    relation
                    for relation in (item.get("relations") or [])
                    if not re.match(
                        r"^(?:\d|of\b|to\b|from\b|for\b|with\b|in\b|on\b|at\b|by\b)",
                        norm_space(str(relation.get("subject_label") or "")).lower(),
                    )
                    and not re.match(
                        r"^(?:of\b|to\b|from\b|for\b|with\b|in\b|on\b|at\b|by\b)",
                        norm_space(
                            str(relation.get("object_label") or relation.get("object_text") or "")
                        ).lower(),
                    )
                    and (
                        not claim_versions
                        or any(
                            version
                            in " ".join(
                                str(relation.get(key) or "")
                                for key in (
                                    "subject_label",
                                    "predicate",
                                    "object_label",
                                    "object_text",
                                )
                            )
                            for version in claim_versions
                        )
                    )
                    and (
                        not requires_version_relation
                        or bool(
                            re.search(
                                r"\b\d+(?:\.\d+){1,3}\b",
                                " ".join(
                                    str(relation.get(key) or "")
                                    for key in (
                                        "subject_label",
                                        "predicate",
                                        "object_label",
                                        "object_text",
                                    )
                                ),
                            )
                        )
                    )
                ]
        after_hash = hashlib.sha256(
            json.dumps(self.processor.snapshot(), sort_keys=True).encode("utf-8")
        ).hexdigest()
        self.db.record_processing_episode(
            episode_id=str(uuid4()),
            task_id=task_id,
            request_id=envelope.request_id,
            task_signature=structural_task.signature(),
            active_weave=active_weave.model_dump(mode="json"),
            attempts=[trace.to_dict() for trace in attempts],
            selected_pathway=selected_trace.pathway if selected_trace else None,
            selected_answer=selected_indexes,
            accepted=selected_pair is not None,
            result_code=(
                selected_pair[1].result_code
                if selected_pair
                else "grounded_selection_capability_gap"
            ),
            rewards=[
                {
                    "pathway": trace.pathway,
                    "reward": verification.reward,
                    "accepted": verification.accepted,
                }
                for trace, verification in verified
            ],
            feedback_source="grounded_result_validator",
            state_before_hash=before_hash,
            state_after_hash=after_hash,
            duration_ms=(perf_counter() - started) * 1000.0,
            metadata={"external_resources_used_by_processor": False},
        )
        self.db.add_task_event(
            task_id,
            "COGNITION_ACTIVE_WEAVE",
            f"{len(evidence)} grounded claims selected",
            {
                "claim_ids": [e.get("claim_id") for e in evidence],
                "raw_claim_count": len(raw_evidence),
                "min_retrieval_score": selection_threshold,
                "processor_pathway": selected_trace.pathway if selected_trace else None,
                "processor_operators": selected_trace.operators if selected_trace else [],
            },
        )
        result = self.core.answer(envelope.normalized_request, evidence)
        result["grounded_version_tokens"] = list(
            dict.fromkeys(
                re.findall(
                    r"\b\d+(?:\.\d+){1,3}\b",
                    " ".join(
                        f"{item.get('source_title') or ''} {item.get('text') or ''}"
                        for item in evidence
                    ),
                )
            )
        )[:24]
        result["active_weave_relevant"] = bool(evidence)
        result["active_weave_entity_terms"] = sorted(self._entity_terms(envelope))
        result["processor_cycles"] = [
            {
                "cycle": index,
                "pathway": trace.pathway,
                "operators": trace.operators,
                "candidate_answer": trace.answer,
                "satisfied": verification.accepted,
                "validation_checks": verification.checks,
            }
            for index, (trace, verification) in enumerate(verified, start=1)
        ]
        result["processor_operators"] = selected_trace.operators if selected_trace else []
        result["processor_capabilities"] = ["grounded_evidence_selection"]
        self.core_calls += 1
        self.db.add_task_event(
            task_id,
            "CORE_DECISION",
            str((result.get("decision") or {}).get("action") or "UNKNOWN"),
            result.get("decision") or {},
        )
        return result

    def _process_self_contained(self, request: CoreRequest, task_id: str) -> CoreResult:
        plan = request.resource_plan
        if plan is None:
            raise RuntimeError("Self-contained processing requires an authorized resource plan.")
        started = perf_counter()
        plan_payload = plan.model_dump(mode="json")
        self.db.add_task_event(
            task_id,
            "COGNITIVE_RESOURCE_PLAN",
            f"Authorized closed-world plan {plan.plan_id}.",
            plan_payload,
        )
        self.db.add_task_event(
            task_id,
            "COGNITION_CONSTRAINT_WEAVE",
            "Compiling a task-local structural Active Weave.",
            {"task_signature": plan.task_signature, "living_memory_claim_ids": []},
        )

        structural_task = compile_structural_task(request.envelope)
        active_weave = (
            build_active_weave(request.envelope, structural_task) if structural_task else None
        )
        state_before = self.processor.snapshot()
        state_before_hash = hashlib.sha256(
            json.dumps(state_before, sort_keys=True).encode("utf-8")
        ).hexdigest()
        processor_result = (
            self.processor.process(structural_task, explore=True)
            if structural_task is not None
            else None
        )
        attempts = list(processor_result.alternatives) if processor_result else []
        processor_growth_attempted = False
        processor_gap_key = None
        if structural_task is not None and not attempts:
            processor_growth_attempted = True
            processor_gap_key = self.processor.register_gap(
                structural_task,
                (
                    processor_result.capability_gap
                    if processor_result is not None
                    else "No executable pathway was available."
                ),
            )
            discovery = self.processor.discover(
                structural_task,
                max_candidates=max(1, int(plan.max_processor_cycles)),
            )
            attempts = list(discovery.alternatives)
            self.db.add_task_event(
                task_id,
                "ORGANIC_PROCESSOR_GROWTH_ATTEMPT",
                f"Discovered {len(attempts)} approved operator composition(s).",
                {
                    "capability_signature": structural_task.capability_signature(),
                    "candidate_pathways": [trace.pathway for trace in attempts],
                    "capability_gap": discovery.capability_gap,
                },
            )
        verified: list[tuple[Any, Any]] = []
        rewards: list[dict[str, Any]] = []
        for trace in attempts:
            verification = verify_trace(structural_task, trace)
            verified.append((trace, verification))
            self.processor.learn(trace, verification.reward, "deterministic_result_validator")
            rewards.append(
                {
                    "pathway": trace.pathway,
                    "reward": verification.reward,
                    "accepted": verification.accepted,
                    "result_code": verification.result_code,
                }
            )

        selected_pair = next((pair for pair in verified if pair[1].accepted), None)
        selected = selected_pair[0] if selected_pair else None
        selected_verification = selected_pair[1] if selected_pair else None
        accepted = selected is not None and selected_verification is not None
        if processor_growth_attempted and structural_task is not None:
            if accepted and selected is not None:
                self.processor.promote_discovered(structural_task, selected)
            else:
                self.processor.record_growth_failure(
                    structural_task,
                    discovery.capability_gap
                    or "No discovered pathway passed deterministic external verification.",
                )
        cycles: list[dict[str, Any]] = []
        for index, (trace, verification) in enumerate(verified, start=1):
            cycle = {
                "cycle": index,
                "pathway": trace.pathway,
                "operators": trace.operators,
                "candidate_answer": trace.answer,
                "satisfied": verification.accepted,
                "validation_checks": verification.checks,
                "result_code": verification.result_code,
            }
            cycles.append(cycle)
            self.db.add_task_event(
                task_id,
                "ORGANIC_PROCESSOR_CYCLE",
                f"Cycle {index}: {trace.pathway} satisfied={verification.accepted}",
                cycle,
            )

        self.core_calls += 1
        answer_value = selected.answer if selected else None
        if accepted and structural_task and structural_task.goal == "min_distinct_count":
            label_match = re.search(
                r"\bhow many\s+([a-z][a-z-]*)",
                request.envelope.original_request,
                flags=re.IGNORECASE,
            )
            label = label_match.group(1).lower() if label_match else "objects"
            answer = (
                f"{answer_value} {label}. The same objects can satisfy more than one relative-position "
                "description, so the stated groups overlap in the smallest consistent arrangement."
            )
        elif accepted and structural_task and structural_task.goal == "linearize_order":
            answer = ", ".join(str(item) for item in (answer_value or []))
        elif accepted and structural_task and structural_task.goal == "prove_existential_relation":
            answer = _format_boolean_entailment(structural_task, bool(answer_value))
        elif (
            accepted and structural_task and structural_task.goal == "solve_linear_target"
        ):
            result = answer_value if isinstance(answer_value, dict) else {}
            values = result.get("values") if isinstance(result.get("values"), dict) else {}
            target_constraint = next(
                (
                    item
                    for item in structural_task.constraints
                    if item.get("kind") == "linear_target"
                ),
                {},
            )
            label = str(target_constraint.get("label") or "Result").strip() or "Result"
            assignments = ", ".join(f"{name} = {value}" for name, value in sorted(values.items()))
            answer = f"{label}: {result.get('target')}. Verified values: {assignments}."
        elif accepted:
            answer = str(answer_value)
        else:
            answer = ""
        if not accepted:
            answer = (
                "The Organic Processor could not solve this closed-world structure with its "
                "currently implemented operators and cycle budget. No memory or web lookup was used."
            )
        selected_operators = list(selected.operators) if selected else []
        selected_model = None
        selected_model_details: dict[str, Any] = {}
        if selected:
            for intermediate in selected.intermediate:
                state = intermediate.get("state") if isinstance(intermediate, dict) else None
                if isinstance(state, dict) and isinstance(state.get("model"), list):
                    selected_model = state["model"]
                if isinstance(state, dict) and isinstance(state.get("ordered"), list):
                    selected_model_details["ordered_items"] = list(state["ordered"])
                if isinstance(state, dict) and isinstance(state.get("case_results"), list):
                    selected_model_details["case_count"] = len(state["case_results"])
                    selected_model_details["all_cases_satisfied"] = bool(state.get("entailed"))
        validation_checks = (
            dict(selected_verification.checks)
            if selected_verification
            else {"supported_family": False}
        )
        decision = {
            "action": "ANSWER" if accepted else "ABSTAIN",
            "confidence": 0.99 if accepted else 0.0,
            "mode": "closed_world_structural_processor",
            "validation_checks": validation_checks,
            "result_code": (
                selected_verification.result_code
                if selected_verification
                else "processor_capability_gap"
            ),
        }
        reasoning_trace = {
            "operator": "BOUNDED_STRUCTURAL_PATH"
            if accepted
            else "ORGANIC_PROCESSOR_CAPABILITY_GAP",
            "pathway": selected.pathway if selected else None,
            "operators": selected_operators,
            "signature": structural_task.signature() if structural_task else None,
            "model": {
                "positions": selected_model,
                "minimal_model": bool(
                    accepted and structural_task and structural_task.family == "order_cardinality"
                ),
                **selected_model_details,
            },
            "attempts": cycles,
            "growth": {
                "attempted": processor_growth_attempted,
                "capability_signature": processor_gap_key,
                "promoted": bool(processor_growth_attempted and accepted),
            },
        }
        review = {
            "complete": accepted,
            "reasoning_mode": "self_contained",
            "processor_satisfied": accepted,
            "processor_cycles": len(cycles),
            "processor_operators": selected_operators,
            "processor_capabilities": plan.processor_capabilities,
            "external_grounding_required": False,
            "external_grounding_used": False,
            "validation_checks": validation_checks,
            "hard_blocked": not accepted,
        }
        self.db.add_task_event(
            task_id,
            "CORE_SELF_CONTAINED_RESULT",
            str(decision.get("action") or "ABSTAIN"),
            {
                **decision,
                "resource_plan_id": plan.plan_id,
                "reasoning_trace": reasoning_trace,
            },
        )
        self._record_completeness_review(task_id, review)
        state_after = self.processor.snapshot()
        state_after_hash = hashlib.sha256(
            json.dumps(state_after, sort_keys=True).encode("utf-8")
        ).hexdigest()
        self.db.record_processing_episode(
            episode_id=str(uuid4()),
            task_id=task_id,
            request_id=request.envelope.request_id,
            task_signature=structural_task.signature() if structural_task else "capability_gap",
            active_weave=active_weave.model_dump(mode="json") if active_weave else {},
            attempts=[trace.to_dict() for trace in attempts],
            selected_pathway=selected.pathway if selected else None,
            selected_answer=answer_value,
            accepted=accepted,
            result_code=str(decision["result_code"]),
            rewards=rewards,
            feedback_source="deterministic_result_validator",
            state_before_hash=state_before_hash,
            state_after_hash=state_after_hash,
            duration_ms=(perf_counter() - started) * 1000.0,
            metadata={
                "resource_plan_id": plan.plan_id,
                "external_resources_used": False,
                "structural_task": structural_task.to_dict() if structural_task else None,
                "processor_growth_attempted": processor_growth_attempted,
                "processor_gap_key": processor_gap_key,
                "processor_pathway_promoted": bool(processor_growth_attempted and accepted),
                "legacy_seed": self.legacy_processor.status(),
            },
        )
        self.db.record_core_learning_event(
            decision_kind="self_contained_processor",
            selected_action=str(decision.get("action") or "ABSTAIN"),
            reward=1.0 if accepted else -0.35,
            outcome="constraint_model_verified" if accepted else "processor_capability_gap",
            features={
                "cycles": len(cycles),
                "operator_count": len(selected_operators),
                "world_mode_closed": 1.0 if plan.world_mode.value == "closed" else 0.0,
            },
            scores={"confidence": 0.99 if accepted else 0.0},
            metadata={
                "task_id": task_id,
                "resource_plan_id": plan.plan_id,
                "validation_checks": review["validation_checks"],
            },
        )
        self.engine.complete_interaction_task(
            task_id,
            answer,
            accepted,
            {
                "semantic_completeness": review,
                "reasoning_mode": "self_contained",
                "external_growth_attached": False,
                "resource_plan_id": plan.plan_id,
            },
        )
        return CoreResult(
            answer=answer,
            active_paths=[
                "COGNITION_CONSTRAINT_WEAVE",
                *(
                    ["ORGANIC_PROCESSOR_GROWTH:PATHWAY_DISCOVERY"]
                    if processor_growth_attempted
                    else []
                ),
                *[f"ORGANIC_PROCESSOR:{operator}" for operator in selected_operators],
                "RESULT_VALIDATOR:verify_constraints",
            ],
            metadata={
                "mvp_task_id": task_id,
                "resource_plan_id": plan.plan_id,
                "planner_world_mode": plan.world_mode.value,
                "processor_capabilities": plan.processor_capabilities,
                "required_operations": list(structural_task.required_operations)
                if structural_task
                else [],
                "processor_operators": selected_operators,
                "processor_cycles": len(cycles),
                "processor_growth_attempted": processor_growth_attempted,
                "processor_gap_key": processor_gap_key,
                "processor_pathway_promoted": bool(processor_growth_attempted and accepted),
                "confidence": 0.99 if accepted else 0.0,
                "missing": []
                if accepted
                else ["No verified processor pathway for the structural task."],
                "sources": [],
                "decision": decision,
                "reasoning_trace": reasoning_trace,
                "reasoning_mode": "self_contained",
                "reasoning_cycles": len(cycles),
                "internal_growth_invoked": False,
                "grounded_answer_used": False,
                "self_contained_reasoning_used": True,
                "semantic_completeness_complete": accepted,
                "semantic_completeness_review": review,
                "hard_blocked": not accepted,
                "core_calls_total": self.core_calls,
            },
        )

    def process_core(self, request: CoreRequest) -> CoreResult:
        task_id = self._create_runtime_task(request)
        internal_growth = False
        growth_result = request.growth
        if (
            request.envelope.self_contained_reasoning
            and request.resource_plan is not None
            and request.resource_plan.world_mode.value == "closed"
        ):
            return self._process_self_contained(request, task_id)
        if request.envelope.intent == "clarification_needed":
            subject = clarification_subject(request.envelope.normalized_request)
            if not subject and request.envelope.entities:
                subject = request.envelope.entities[0].text
            subject = subject or "that"
            answer = (
                f"What would you like me to do about {subject}? "
                "For example, I can research current options, explain it, or compare choices."
            )
            review = {
                "complete": False,
                "clarification_required": True,
                "grounded_answer_used": False,
                "hard_blocked": False,
            }
            self.db.add_task_event(
                task_id,
                "CLARIFICATION_REQUIRED",
                answer,
                {"subject": subject, "intent": request.envelope.intent},
            )
            self.engine.complete_interaction_task(
                task_id,
                answer,
                False,
                {"semantic_completeness": review},
            )
            return CoreResult(
                answer=answer,
                active_paths=["CLARIFICATION_REQUIRED"],
                metadata={
                    "core_invoked": False,
                    "mvp_task_id": task_id,
                    "confidence": 1.0,
                    "missing": ["actionable objective"],
                    "sources": [],
                    "decision": {"action": "CLARIFY"},
                    "reasoning_trace": {"operator": "CLARIFICATION_REQUIRED"},
                    "internal_growth_invoked": False,
                    "grounded_answer_used": False,
                    "semantic_completeness_complete": False,
                    "semantic_completeness_review": review,
                    "clarification_required": True,
                    "hard_blocked": False,
                    "core_calls_total": self.core_calls,
                },
            )
        if "coding" in request.envelope.required_capabilities:
            answer = (
                "This Organic runtime GUI is not connected to coding or file-editing tools. "
                "Use the Codex workspace for implementation work, or connect a coding-capable "
                "tool through MCP before asking this runtime to execute that task."
            )
            self.db.add_task_event(
                task_id,
                "CAPABILITY_UNAVAILABLE",
                "Coding/file-editing capability is not attached to this runtime.",
                {"required_capabilities": request.envelope.required_capabilities},
            )
            self.engine.complete_interaction_task(
                task_id,
                answer,
                False,
                {
                    "required_capabilities": request.envelope.required_capabilities,
                    "capability_missing": "coding",
                    "semantic_completeness": {
                        "complete": False,
                        "grounded_answer_used": False,
                        "hard_blocked": True,
                    },
                },
            )
            return CoreResult(
                answer=answer,
                metadata={
                    "mvp_task_id": task_id,
                    "confidence": 0.0,
                    "missing": ["coding/file-editing tool capability"],
                    "sources": [],
                    "decision": {"action": "ABSTAIN", "reason": "capability_unavailable"},
                    "reasoning_trace": {"operator": "CAPABILITY_UNAVAILABLE"},
                    "internal_growth_invoked": False,
                    "grounded_answer_used": False,
                    "semantic_completeness_complete": False,
                    "semantic_completeness_review": {
                        "complete": False,
                        "grounded_answer_used": False,
                        "hard_blocked": True,
                    },
                    "hard_blocked": True,
                    "core_calls_total": self.core_calls,
                },
            )
        if growth_result:
            self.db.add_task_event(
                task_id,
                "GROWTH_EVIDENCE_ATTACHED",
                growth_result.summary,
                growth_result.model_dump(mode="json"),
            )

        result = self._answer_with_core(request.envelope, task_id, growth_result)
        answer = self._answer_text(result)
        review = self._semantic_completeness_review(request, result, answer, growth_result)
        self._record_completeness_review(task_id, review)
        if review["needs_retry"]:
            internal_growth = True
            growth_result = self.grow(request.envelope, parent_task_id=task_id)
            self.db.add_task_event(
                task_id,
                "COMPLETENESS_FORCED_RESEARCH",
                growth_result.summary,
                growth_result.model_dump(mode="json"),
            )
            result = self._answer_with_core(request.envelope, task_id, growth_result)
            answer = self._answer_text(result)
            review = self._semantic_completeness_review(request, result, answer, growth_result)
            self._record_completeness_review(task_id, review)

        decision = result.get("decision") or {}
        confidence = float(result.get("confidence", 0.0) or 0.0)
        success = bool(review["complete"])
        reward = 1.0 if success else -0.35
        self.core.learn_decision(
            task_id,
            decision,
            reward,
            "pydantic_runtime_answer" if success else "pydantic_runtime_insufficient_grounding",
            {
                "confidence": confidence,
                "internal_growth_invoked": internal_growth,
                "external_growth_attached": request.growth is not None,
                "semantic_completeness": review,
            },
        )
        if not answer or review["hard_blocked"]:
            missing = self._missing_text(result)
            answer = f"I do not yet have enough grounded information to answer that reliably. Missing: {missing}"
        self.engine.complete_interaction_task(
            task_id,
            answer,
            success,
            {
                "confidence": confidence,
                "semantic_completeness": review,
                "internal_growth_invoked": internal_growth,
                "external_growth_attached": request.growth is not None,
            },
        )
        return CoreResult(
            answer=answer,
            activated_memory_ids=list(request.preflight.memory_ids),
            active_paths=[
                *[f"ORGANIC_PROCESSOR:{name}" for name in result.get("processor_operators") or []],
                str((result.get("reasoning_trace") or {}).get("operator") or "organic_executive"),
            ],
            metadata={
                "mvp_task_id": task_id,
                "confidence": confidence,
                "missing": result.get("missing") or [],
                "sources": result.get("sources") or [],
                "decision": decision,
                "reasoning_trace": result.get("reasoning_trace") or {},
                "grounded_version_tokens": result.get("grounded_version_tokens") or [],
                "processor_capabilities": result.get("processor_capabilities") or [],
                "processor_operators": result.get("processor_operators") or [],
                "processor_cycles": len(result.get("processor_cycles") or []),
                "internal_growth_invoked": internal_growth,
                "grounded_answer_used": review["grounded_answer_used"],
                "semantic_completeness_complete": review["complete"],
                "semantic_completeness_review": review,
                "hard_blocked": review["hard_blocked"],
                "core_calls_total": self.core_calls,
            },
        )

    def grow(self, envelope: IntentEnvelope, parent_task_id: str | None = None) -> GrowthResult:
        self.growth_calls += 1
        query = self.core.generate_query(envelope.normalized_request, [])
        task_id = stable_uid("task", f"pydantic-growth|{utcnow()}|{query}")
        self.db.create_task(
            task_id,
            "PYDANTIC_GROWTH",
            "PYDANTIC_RUNTIME",
            f"Acquire evidence for: {envelope.normalized_request}",
            930,
            status="ACTIVE",
            parent_task_id=parent_task_id,
            generated_reason="Gate/Core requested evidence acquisition through the scaffold runtime.",
            metadata={"query": query, "request_id": envelope.request_id},
        )
        self.db.update_task(task_id, started_at=utcnow(), attempts=1)
        self.db.add_task_event(task_id, "QUERY_GENERATED", query)

        learned: list[GrowthEvidence] = []
        durable_ids: list[str] = []
        errors: list[str] = []
        try:
            results = self.broker.search(
                query, limit=max(4, int(self.config.get("interaction_search_results", 4)))
            )
            self.db.add_task_event(
                task_id,
                "SEARCH_RESULTS",
                f"{len(results)} result(s)",
                {"results": [r.__dict__ for r in results[:8]]},
            )
            for search_result in results[
                : max(1, int(self.config.get("interaction_fetch_limit", 2)))
            ]:
                try:
                    doc = self.broker.fetch_result(search_result)
                    ingest = self.memory.ingest(doc, reason=f"{task_id}: pydantic runtime growth")
                    durable_ids.append(doc.source_id)
                    learned.append(
                        GrowthEvidence(
                            source_id=doc.source_id,
                            content=doc.text[:1500],
                            confidence=doc.trust,
                            metadata={
                                "title": doc.title,
                                "url": doc.url,
                                "provider": doc.provider,
                                "claims_added": ingest.claims_added,
                                "relations_added": ingest.relations_added,
                            },
                        )
                    )
                    self.db.add_task_event(task_id, "EVIDENCE_INGESTED", doc.title, ingest.__dict__)
                except Exception as exc:
                    errors.append(str(exc))
                    self.db.add_task_event(task_id, "EVIDENCE_ERROR", str(exc))
        except Exception as exc:
            errors.append(str(exc))
            self.db.add_task_event(task_id, "SEARCH_ERROR", str(exc))

        summary = f"Growth searched '{query}' and ingested {len(learned)} source(s)."
        if errors:
            summary += f" Errors: {len(errors)}."
        status = "COMPLETED" if learned else "PARTIAL"
        self.db.update_task(task_id, status=status, completed_at=utcnow(), result_text=summary)
        return GrowthResult(
            evidence=learned,
            durable_candidate_ids=durable_ids,
            summary=summary,
            metadata={
                "mvp_growth_task_id": task_id,
                "query": query,
                "errors": errors[:5],
                "growth_calls_total": self.growth_calls,
            },
        )


class MvpMemoryAdapter:
    def __init__(self, system: MvpOrganicSystem) -> None:
        self.system = system

    async def preflight(self, envelope: IntentEnvelope) -> PreflightKnowledge:
        return self.system.preflight(envelope)

    async def answer_known(
        self,
        envelope: IntentEnvelope,
        preflight: PreflightKnowledge,
    ) -> str:
        return self.system.answer_known(preflight)

    def set_user_active(self, active: bool) -> None:
        self.system.set_user_active(active)


class MvpCoreAdapter:
    def __init__(self, system: MvpOrganicSystem) -> None:
        self.system = system

    async def process(self, request: CoreRequest) -> CoreResult:
        return self.system.process_core(request)

    def set_user_active(self, active: bool) -> None:
        self.system.set_user_active(active)


class MvpGrowthAdapter:
    def __init__(self, system: MvpOrganicSystem) -> None:
        self.system = system

    async def grow(self, envelope: IntentEnvelope) -> GrowthResult:
        return self.system.grow(envelope)

    def set_user_active(self, active: bool) -> None:
        self.system.set_user_active(active)
