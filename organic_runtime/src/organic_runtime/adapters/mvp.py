from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
from typing import Any

from organic_mvp.config import AppConfig
from organic_mvp.core import build_core
from organic_mvp.db import MemoryDB
from organic_mvp.evidence import EvidenceBroker
from organic_mvp.loggingx import AuditLog, setup_logging
from organic_mvp.memory import MemoryCompiler
from organic_mvp.review import ReviewExporter
from organic_mvp.scheduler import OrganicEngine
from organic_mvp.util import norm_space, stable_uid, utcnow

from organic_runtime.config import RuntimeSettings
from organic_runtime.contracts import (
    CoreRequest,
    CoreResult,
    GrowthEvidence,
    GrowthResult,
    IntentEnvelope,
    PreflightKnowledge,
)


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


class MvpOrganicSystem:
    """Shared bridge from the scaffold contracts into the hardened MVP modules."""

    def __init__(self, settings: RuntimeSettings) -> None:
        self.settings = settings
        self.root = settings.mvp_data_dir.expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        for rel in ("data/logs", "data/source_cache", "data/core", "review_packages", "run_results"):
            (self.root / rel).mkdir(parents=True, exist_ok=True)

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
        self.core = build_core(self.config, self.db, self.logger)
        self.broker = EvidenceBroker(self.root, self.config, self.db, self.logger, self.audit)
        self.memory = MemoryCompiler(self.db, self.core, self.logger, self.audit)
        self.review = ReviewExporter(
            self.root,
            "windows",
            self.config,
            self.db,
            self.memory,
            self.logger,
            self.audit,
        )
        self._recover_interrupted_tasks()
        self.engine = OrganicEngine(
            self.root,
            self.config,
            self.db,
            self.core,
            self.broker,
            self.memory,
            self.logger,
            self.audit,
        )
        self.core_calls = 0
        self.growth_calls = 0
        self.engine.start()
        self._ensure_bootstrap_growth_task()

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

    def close(self) -> None:
        self.engine.stop()
        self._recover_interrupted_tasks()
        self.db.close()

    def set_user_active(self, active: bool) -> None:
        self.engine.set_external_user_active(active)

    def provider_status(self) -> dict[str, Any]:
        return self.broker.provider_status()

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
        out.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
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
        payload = response.model_dump(mode="json") if hasattr(response, "model_dump") else dict(response)
        metadata = {
            "request_id": payload.get("request_id"),
            "trace_id": payload.get("trace_id"),
            "route": payload.get("route"),
            "semantic_suggested_route": (payload.get("metadata") or {}).get("semantic_suggested_route"),
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
        self._update_rolling_summary(request, str(payload.get("answer") or ""), str(payload.get("route") or ""))

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
        query = self.settings.bootstrap_growth_query or "organic ai cognition memory evidence growth"
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
        evidence = [
            item
            for item in raw_evidence
            if float(item.get("retrieval_score", 0.0) or 0.0) >= ACTIVE_WEAVE_MIN_RETRIEVAL_SCORE
        ]
        raw_top = max(
            (float(e.get("retrieval_score", 0.0) or 0.0) for e in raw_evidence),
            default=0.0,
        )
        top = max((float(e.get("retrieval_score", 0.0) or 0.0) for e in evidence), default=0.0)
        memory_ids = [str(e.get("claim_id")) for e in evidence if e.get("claim_id")]
        direct_answer = str(evidence[0].get("text")) if evidence and top >= 0.80 else None
        known_route = direct_answer is not None
        requires_research = (
            envelope.requires_current_external_info
            or (
                not evidence
                and "coding" not in envelope.required_capabilities
                and envelope.intent not in {"simple_conversation", "internal_state"}
            )
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
            request.envelope.requires_current_external_info
            or request.preflight.requires_research
            or growth_result is not None
        )
        has_answer = bool(answer)
        grounded_answer_used = has_answer and (
            not requires_grounding
            or (confidence >= 0.50 and (source_count > 0 or growth_evidence_count > 0) and not missing)
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

    def _answer_with_core(self, goal: str, task_id: str) -> dict[str, Any]:
        raw_evidence = self.memory.retrieve(goal, limit=int(self.config.get("max_active_weave_claims", 18)))
        evidence = [
            item
            for item in raw_evidence
            if float(item.get("retrieval_score", 0.0) or 0.0) >= ACTIVE_WEAVE_MIN_RETRIEVAL_SCORE
        ]
        self.db.add_task_event(
            task_id,
            "COGNITION_ACTIVE_WEAVE",
            f"{len(evidence)} grounded claims selected",
            {
                "claim_ids": [e.get("claim_id") for e in evidence],
                "raw_claim_count": len(raw_evidence),
                "min_retrieval_score": ACTIVE_WEAVE_MIN_RETRIEVAL_SCORE,
            },
        )
        result = self.core.answer(goal, evidence)
        self.core_calls += 1
        self.db.add_task_event(
            task_id,
            "CORE_DECISION",
            str((result.get("decision") or {}).get("action") or "UNKNOWN"),
            result.get("decision") or {},
        )
        return result

    def process_core(self, request: CoreRequest) -> CoreResult:
        task_id = self._create_runtime_task(request)
        internal_growth = False
        growth_result = request.growth
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

        result = self._answer_with_core(request.envelope.normalized_request, task_id)
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
            result = self._answer_with_core(request.envelope.normalized_request, task_id)
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
            active_paths=[str((result.get("reasoning_trace") or {}).get("operator") or "organic_core")],
            metadata={
                "mvp_task_id": task_id,
                "confidence": confidence,
                "missing": result.get("missing") or [],
                "sources": result.get("sources") or [],
                "decision": decision,
                "reasoning_trace": result.get("reasoning_trace") or {},
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
            results = self.broker.search(query, limit=max(4, int(self.config.get("interaction_search_results", 4))))
            self.db.add_task_event(
                task_id,
                "SEARCH_RESULTS",
                f"{len(results)} result(s)",
                {"results": [r.__dict__ for r in results[:8]]},
            )
            for search_result in results[: max(1, int(self.config.get("interaction_fetch_limit", 2)))]:
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
