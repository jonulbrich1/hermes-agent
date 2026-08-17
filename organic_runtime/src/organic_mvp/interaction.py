from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any, Callable

from .config import AppConfig
from .db import MemoryDB
from .evidence import EvidenceBroker
from .gate import InteractionGate
from .loggingx import AuditLog
from .memory import MemoryCompiler
from .semantic import LlamaCppSemanticInterface, RuleSemanticFallback, SemanticInterfaceError, ToolCall
from .util import norm_space, stable_uid, utcnow


TOOL_SCHEMAS = {
    "get_internal_state": {
        "type": "function",
        "function": {
            "name": "get_internal_state",
            "description": "Read Organic AI's actual current operational state. Use for questions like how are you, what are you doing, what are you learning, are you busy, or system status.",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    },
    "memory_lookup": {
        "type": "function",
        "function": {
            "name": "memory_lookup",
            "description": "Retrieve grounded claims already stored in Living Memory. This is external knowledge supplied to the Organic system; do not invent missing claims.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string", "description": "Semantic lookup query."}},
                "required": ["query"],
                "additionalProperties": False,
            },
        },
    },
    "organic_reason": {
        "type": "function",
        "function": {
            "name": "organic_reason",
            "description": "Ask the bounded Organic Processing Core to reason over a grounded Active Weave from Living Memory. This is the authoritative reasoning path for factual/analytical work.",
            "parameters": {
                "type": "object",
                "properties": {
                    "goal": {"type": "string", "description": "A clear restatement or refinement of the user's requested reasoning goal."},
                    "focus": {"type": "string", "description": "Optional missing aspect to focus on, without supplying an answer."},
                },
                "required": ["goal"],
                "additionalProperties": False,
            },
        },
    },
    "organic_research": {
        "type": "function",
        "function": {
            "name": "organic_research",
            "description": "Search configured external evidence sources, fetch raw unstructured text, and pass it through the Memory Compiler before it can be used. Use only when Organic reasoning reports missing or weak evidence.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Self-directed evidence search query."},
                    "reason": {"type": "string", "description": "What information gap this search is intended to fill."},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
    },
}


class InteractionManager:
    """Semantic LLM -> Interaction Gate -> Organic tooling -> presenter loop."""

    def __init__(self, root, config: AppConfig, db: MemoryDB, core, broker: EvidenceBroker,
                 memory: MemoryCompiler, engine, gate: InteractionGate,
                 logger: logging.Logger, audit: AuditLog, state_provider: Callable[[], dict[str, Any]]):
        self.root = root
        self.config = config
        self.db = db
        self.core = core
        self.broker = broker
        self.memory = memory
        self.engine = engine
        self.gate = gate
        self.logger = logger
        self.audit = audit
        self.state_provider = state_provider
        self.primary = LlamaCppSemanticInterface(config, logger)
        self.fallback = RuleSemanticFallback(config, logger)
        self._lock = threading.RLock()
        self._last_route: dict[str, Any] = {}
        self._last_semantic: dict[str, Any] = {}
        self._last_tool_trace: list[dict[str, Any]] = []
        self._last_stage_trace: list[dict[str, Any]] = []
        self._active_stage_trace: list[dict[str, Any]] | None = None
        self._active_task_id: str | None = None
        self._last_error = ""

    def semantic_health(self) -> dict[str, Any]:
        health = self.primary.health()
        health["fallback_available"] = True
        return health

    def _semantic(self):
        h = self.primary.health()
        return self.primary if h.get("ready") else self.fallback

    def _state_summary(self) -> dict[str, Any]:
        raw = self.state_provider()
        counts = raw.get("counts") or self.db.counts()
        current = raw.get("current_task_id")
        return {
            "user_display_name": str(self.config.get("user_display_name", "") or ""),
            "mode": "USER_TASK" if current else ("IDLE_GROWTH" if raw.get("idle_growth_enabled") else "IDLE"),
            "health": "NORMAL" if self.core.status().get("ready") else "DEGRADED",
            "active_task": current,
            "idle_growth_enabled": bool(raw.get("idle_growth_enabled")),
            "completed_idle_cycles": int(raw.get("completed_idle_cycles", 0) or 0),
            "memory_counts": {k: counts.get(k, 0) for k in ("claims", "concepts", "relations", "sources")},
            "core": {
                "learning_updates": self.core.status().get("learning_updates", 0),
                "core_bytes": self.core.status().get("core_bytes", 0),
                "hard_cap_bytes": self.core.status().get("hard_cap_bytes", 0),
            },
        }

    def _allowed_tools(self, route: str) -> list[dict]:
        names = self.gate.allowed_tool_names(route)
        return [TOOL_SCHEMAS[n] for n in TOOL_SCHEMAS if n in names]

    def _stage(self, name: str, message: str = "", data: dict[str, Any] | None = None) -> None:
        entry = {"stage": name, "message": message or name, "data": data or {}, "created_at": utcnow()}
        if self._active_stage_trace is not None:
            self._active_stage_trace.append(entry)
        self.engine.record_interaction_stage(self._active_task_id, name, message or name, data or {})
        self.audit.write("pipeline_stage", task_id=self._active_task_id, stage=name, message=message or name, data=data or {})

    def _execute_tool(self, route: str, normalized_goal: str, tc: ToolCall) -> dict[str, Any]:
        ok, why = self.gate.authorize_tool(route, tc.name)
        self.audit.write("interaction_tool_request", route=route, tool=tc.name, arguments=tc.arguments, authorized=ok, reason=why)
        if not ok:
            self._stage("TOOL_DENIED", why, {"tool": tc.name, "route": route})
            return {"status": "DENIED", "summary": why, "grounded": False}

        if tc.name == "get_internal_state":
            self._stage("STATE_TOOL", "Read actual internal state.", {"tool": tc.name})
            return {"status": "OK", "summary": "actual internal state", "grounded": True, "state": self._state_summary()}

        if tc.name == "memory_lookup":
            q = norm_space(str(tc.arguments.get("query") or normalized_goal))[:700]
            evidence = self.memory.retrieve(q, limit=int(self.config.get("max_active_weave_claims", 18)))
            self._stage("LIVING_MEMORY_RETRIEVE", f"Retrieved {len(evidence)} grounded claims.", {"query": q, "claim_ids": [e.get("claim_id") for e in evidence[:18]]})
            return {
                "status": "OK" if evidence else "EMPTY",
                "summary": f"retrieved {len(evidence)} grounded claims",
                "grounded": bool(evidence),
                "query": q,
                "claims": [
                    {
                        "claim_id": e.get("claim_id"), "text": e.get("text"), "confidence": e.get("confidence"),
                        "retrieval_score": e.get("retrieval_score"), "source_title": e.get("source_title"), "source_url": e.get("source_url"),
                    }
                    for e in evidence[:12]
                ],
            }

        if tc.name == "organic_reason":
            requested = norm_space(str(tc.arguments.get("goal") or normalized_goal))
            focus = norm_space(str(tc.arguments.get("focus") or ""))
            # Anchor every call to the user's normalized goal so the interface model cannot
            # replace the task with a different factual question that it already knows.
            goal = normalized_goal
            if focus:
                goal = f"{normalized_goal} Focus specifically on: {focus}"
            elif requested and requested.lower() != normalized_goal.lower():
                goal = f"{normalized_goal} Restatement from semantic interface: {requested}"
            if "retry" in tc.call_id.lower():
                self._stage("CORE_RETRY", "Retrying Organic Core after evidence acquisition.", {"goal": goal})
            evidence = self.memory.retrieve(goal, limit=int(self.config.get("max_active_weave_claims", 18)))
            self._stage("COGNITION_ACTIVE_WEAVE", f"Built Active Weave with {len(evidence)} grounded claims.", {"goal": goal, "claim_ids": [e.get("claim_id") for e in evidence]})
            result = self.core.answer(goal, evidence)
            answer = str(result.get("answer") or "")
            self._stage("CORE", f"Organic Processing Core action={result.get('decision', {}).get('action')}.", {"confidence": result.get("confidence", 0.0), "decision": result.get("decision") or {}, "reasoning_trace": result.get("reasoning_trace") or {}, "has_answer": bool(answer)})
            return {
                "status": "ANSWER" if answer else "NEEDS_EVIDENCE",
                "summary": f"Organic Core action={result.get('decision', {}).get('action')} confidence={result.get('confidence', 0)}",
                "grounded": bool(evidence),
                "answer": answer,
                "confidence": result.get("confidence", 0.0),
                "missing": result.get("missing") or [],
                "sources": result.get("sources") or [],
                "decision": result.get("decision") or {},
                "reasoning_trace": result.get("reasoning_trace") or {},
                "active_weave_claim_count": len(evidence),
            }

        if tc.name == "organic_research":
            q = norm_space(str(tc.arguments.get("query") or normalized_goal))[:500]
            reason = norm_space(str(tc.arguments.get("reason") or "Semantic interface identified an evidence gap."))[:700]
            task_id = stable_uid("task", f"tool-research|{utcnow()}|{q}")
            self.db.create_task(task_id, "TOOL_RESEARCH", "SEMANTIC_INTERFACE", f"Research evidence for: {q}", 92,
                                status="RUNNING", generated_reason=reason,
                                parent_task_id=self._active_task_id,
                                metadata={"route": route, "normalized_goal": normalized_goal, "query": q, "parent_interaction_task_id": self._active_task_id})
            self.db.add_task_event(task_id, "QUERY_GENERATED", q, {"origin": "semantic_interface_tool", "reason": reason})
            self._stage("EVIDENCE_BROKER", "Searching external evidence sources.", {"query": q, "research_task_id": task_id, "reason": reason})
            learned = []
            errors = []
            try:
                results = self.broker.search(q, limit=max(2, int(self.config.get("interaction_search_results", 4))))
                self._stage("EVIDENCE_RESULTS", f"Evidence search returned {len(results)} result(s).", {"query": q, "results": [r.__dict__ for r in results[:8]]})
                for r in results[:max(1, int(self.config.get("interaction_fetch_limit", 2)))]:
                    try:
                        doc = self.broker.fetch_result(r)
                        self._stage("EVIDENCE_FETCH", doc.title, {"url": doc.url, "provider": doc.provider, "source_id": doc.source_id})
                        ing = self.memory.ingest(doc, reason=f"interactive research: {reason}")
                        self._stage("MEMORY_COMPILER_VALIDATOR", f"Compiled and validated raw evidence from {doc.title}.", ing.__dict__)
                        learned.append({"title": doc.title, "url": doc.url, "source_id": doc.source_id,
                                        "claims_added": ing.claims_added, "relations_added": ing.relations_added})
                    except Exception as exc:
                        errors.append(str(exc))
                        self._stage("EVIDENCE_ERROR", str(exc), {"url": r.url})
                status = "COMPLETED" if learned else "PARTIAL"
                self.db.update_task(task_id, status=status, completed_at=utcnow(), result_text=f"Learned from {len(learned)} source(s); errors={len(errors)}")
            except Exception as exc:
                errors.append(str(exc))
                self.db.update_task(task_id, status="FAILED", completed_at=utcnow(), result_text=str(exc))
                self._stage("EVIDENCE_ERROR", str(exc), {"query": q})
            self._stage("LIVING_MEMORY_COMMIT", f"Committed evidence from {len(learned)} source(s).", {"learned": learned, "errors": errors[:4], "research_task_id": task_id})
            return {
                "status": "LEARNED" if learned else "NO_EVIDENCE",
                "summary": f"searched '{q}' and ingested {len(learned)} source(s)",
                "grounded": bool(learned),
                "query": q,
                "learned": learned,
                "errors": errors[:4],
                "task_id": task_id,
            }

        return {"status": "UNKNOWN_TOOL", "summary": f"unknown tool {tc.name}", "grounded": False}

    def handle_message(self, text: str) -> dict[str, Any]:
        text = norm_space(text)
        if not text:
            raise ValueError("message cannot be empty")
        with self._lock:
            self.engine.set_external_user_active(True)
            started = time.time()
            trace: list[dict[str, Any]] = []
            interaction_task_id: str | None = None
            self._active_stage_trace = trace
            self._active_task_id = None
            try:
                self._stage("INTERFACE_IN", "User message entered the Semantic Interface.", {"user_text": text})
                self.db.add_conversation("user", "message", text, None, {"interaction_gate": True})
                state = self._state_summary()
                semantic = self._semantic()
                backend = semantic.health().get("backend")
                try:
                    analysis = semantic.analyze_user(text, state)
                except Exception as exc:
                    self._last_error = str(exc)
                    semantic = self.fallback
                    backend = "rule-fallback"
                    analysis = semantic.analyze_user(text, state)
                self._stage("SEMANTIC_ANALYSIS", "Semantic Interface produced advisory analysis.", {"backend": backend, "analysis": analysis})
                gate = self.gate.choose_route(text, analysis)
                self._last_semantic = analysis
                self._last_route = gate
                self.audit.write("interaction_routed", user_text=text, semantic=analysis, gate=gate, backend=backend)
                self._stage("GATE", f"Interaction Gate selected {gate['route']}.", gate)

                if bool(gate.get("requires_grounding")):
                    interaction_task_id = self.engine.begin_interaction_task(
                        text,
                        str(analysis.get("normalized_goal") or text),
                        gate["route"],
                        gate,
                        analysis,
                    )
                    self._active_task_id = interaction_task_id
                    self._stage("EXECUTIVE", "Organic Executive accepted the grounded interaction task.", {"task_id": interaction_task_id})

                allowed = self._allowed_tools(gate["route"])
                def executor(tc: ToolCall):
                    return self._execute_tool(gate["route"], str(analysis.get("normalized_goal") or text), tc)

                try:
                    loop = semantic.tool_loop(
                        text,
                        str(analysis.get("normalized_goal") or text),
                        gate["route"],
                        allowed,
                        executor,
                        bool(gate.get("requires_grounding")),
                        state,
                    )
                except Exception as exc:
                    self.logger.warning("semantic tool loop failed; using fallback: %s", exc)
                    self._last_error = str(exc)
                    loop = self.fallback.tool_loop(
                        text,
                        str(analysis.get("normalized_goal") or text),
                        gate["route"],
                        allowed,
                        executor,
                        bool(gate.get("requires_grounding")),
                        state,
                    )
                    backend = "rule-fallback"

                answer = norm_space(str(loop.get("text") or ""))
                if not answer:
                    answer = "I do not yet have enough grounded information to answer that reliably."
                grounded_answer = bool(loop.get("grounded_answer_used")) or not bool(gate.get("requires_grounding"))
                if bool(gate.get("requires_grounding")) and not grounded_answer:
                    answer = "I do not yet have enough grounded information to answer that reliably."
                    loop["hard_blocked"] = True
                success = not bool(loop.get("budget_exhausted")) and bool(answer) and grounded_answer
                self._stage("INTERFACE_REVIEW", "Semantic Interface result reviewed against grounding requirements.",
                            {"requires_grounding": bool(gate.get("requires_grounding")), "grounded_answer_used": grounded_answer,
                             "hard_blocked": bool(loop.get("hard_blocked")), "forced_core": bool(loop.get("forced_core")),
                             "forced_research": bool(loop.get("forced_research"))})
                self._stage("FINAL" if not loop.get("hard_blocked") else "FINAL_BLOCKED", answer[:500],
                            {"success": success, "grounded_answer_used": grounded_answer})
                self.gate.record_success(gate, success)
                self._last_tool_trace = list(loop.get("tools_used") or [])
                self._last_stage_trace = list(trace)
                meta = {
                    "semantic_backend": backend,
                    "semantic_analysis": analysis,
                    "gate": gate,
                    "organic_task_id": interaction_task_id,
                    "tools_used": self._last_tool_trace,
                    "grounded_tool_used": bool(loop.get("grounded_tool_used")),
                    "grounded_answer_used": grounded_answer,
                    "forced_core": bool(loop.get("forced_core")),
                    "forced_research": bool(loop.get("forced_research")),
                    "hard_blocked": bool(loop.get("hard_blocked")),
                    "stage_trace": self._last_stage_trace,
                    "elapsed_seconds": round(time.time() - started, 3),
                }
                self.db.add_conversation("assistant", "message", answer, interaction_task_id, meta)
                self.audit.write("interaction_complete", answer=answer, metadata=meta)
                self.engine.complete_interaction_task(interaction_task_id, answer, success, meta)
                return {
                    "ok": True,
                    "answer": answer,
                    "route": gate,
                    "semantic": analysis,
                    "tools_used": self._last_tool_trace,
                    "backend": backend,
                    "grounded_tool_used": bool(loop.get("grounded_tool_used")),
                    "grounded_answer_used": grounded_answer,
                    "forced_core": bool(loop.get("forced_core")),
                    "forced_research": bool(loop.get("forced_research")),
                    "hard_blocked": bool(loop.get("hard_blocked")),
                    "organic_task_id": interaction_task_id,
                    "stage_trace": self._last_stage_trace,
                }
            finally:
                self._active_stage_trace = None
                self._active_task_id = None
                self.engine.set_external_user_active(False)

    def status(self) -> dict[str, Any]:
        return {
            "semantic": self.semantic_health(),
            "gate": self.gate.status(),
            "last_route": self._last_route,
            "last_semantic": self._last_semantic,
            "last_tools": self._last_tool_trace,
            "last_stage_trace": self._last_stage_trace,
            "last_error": self._last_error,
        }
