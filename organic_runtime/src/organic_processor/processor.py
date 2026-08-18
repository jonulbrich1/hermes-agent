from __future__ import annotations

import copy
import json
import os
import threading
from collections import Counter
from collections.abc import Callable, Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .contracts import (
    CognitiveContext,
    PresenterPacket,
    ProcessorBudgets,
    ProcessOutcome,
    VerificationReceipt,
    VerifiedAttempt,
)
from .operators import OPERATORS, OperatorError
from .primitives import PRIMITIVE_SPECS
from .seed_policy import SeedFormatError, V7SeedPolicy
from .types import ProcessResult, ProcessTrace, StructuralTask

SEED_PATHWAYS: dict[str, dict[str, list[str]]] = {
    "order_cardinality": {
        "NAIVE_SUM": ["SUM_CONSTRAINT_MENTIONS", "EMIT_SCALAR"],
        "MAX_ONLY": ["MAX_CONSTRAINT_ONLY", "EMIT_SCALAR"],
        "MIN_CONSISTENT_MODEL": [
            "INFER_MINIMUM_LINE_BOUND",
            "CONSTRUCT_LINEAR_MODEL",
            "REPAIR_UNTIL_VALID",
            "EMIT_MODEL_CARDINALITY",
        ],
    },
    "bounded_arithmetic": {
        "SAFE_ARITHMETIC": ["EVALUATE_BOUNDED_EXPRESSION"],
    },
    "grounded_evidence_selection": {
        "GROUNDED_THRESHOLD_SELECTION": ["SELECT_GROUNDED_CANDIDATES"],
    },
}

INITIAL_EDGE_WEIGHTS = {
    "START->SUM_CONSTRAINT_MENTIONS": 0.55,
    "SUM_CONSTRAINT_MENTIONS->EMIT_SCALAR": 0.55,
    "START->MAX_CONSTRAINT_ONLY": 0.30,
    "MAX_CONSTRAINT_ONLY->EMIT_SCALAR": 0.30,
    "START->INFER_MINIMUM_LINE_BOUND": 0.10,
    "INFER_MINIMUM_LINE_BOUND->CONSTRUCT_LINEAR_MODEL": 0.10,
    "CONSTRUCT_LINEAR_MODEL->REPAIR_UNTIL_VALID": 0.10,
    "REPAIR_UNTIL_VALID->EMIT_MODEL_CARDINALITY": 0.10,
    "START->EVALUATE_BOUNDED_EXPRESSION": 0.80,
    "START->SELECT_GROUNDED_CANDIDATES": 0.80,
}


class OrganicProcessor:
    """Bounded adaptive structural processor with no external-resource access."""

    mode = "bounded_structural_processor"
    version = "0.11.0-rc1"

    def __init__(
        self,
        state_path: str | Path | None = None,
        learning_rate: float = 0.25,
        max_state_bytes: int = 16 * 1024 * 1024,
        seed_path: str | Path | None = None,
        seed_enabled: bool = True,
        budgets: ProcessorBudgets | None = None,
    ) -> None:
        self.state_path = Path(state_path) if state_path else None
        self.learning_rate = float(learning_rate)
        self.max_state_bytes = int(max_state_bytes)
        self.budgets = budgets or ProcessorBudgets()
        self._lock = threading.RLock()
        self.seed_policy: V7SeedPolicy | None = None
        self.seed_error: str | None = None
        self._thought_status: dict[str, Any] = {
            "enabled": False,
            "shadow": False,
            "available": False,
            "policy": "v9",
        }
        if seed_enabled:
            try:
                self.seed_policy = V7SeedPolicy.load(seed_path)
            except (OSError, ValueError, TypeError, SeedFormatError) as exc:
                self.seed_error = f"{type(exc).__name__}: {exc}"
        else:
            self.seed_error = "disabled_by_configuration"
        self.state = self._load()

    @staticmethod
    def _fresh_state() -> dict[str, Any]:
        return {
            "schema_version": 1,
            "edge_weights": dict(INITIAL_EDGE_WEIGHTS),
            "success_counts": {},
            "failure_counts": {},
            "feedback_source_counts": {},
            "composites": {},
            "learned_pathways": {},
            "growth_frontier": {},
            "growth_event_count": 0,
            "resolved_gap_count": 0,
            "last_growth_result": None,
            "experience_count": 0,
        }

    def _load(self) -> dict[str, Any]:
        if self.state_path and self.state_path.exists():
            try:
                loaded = json.loads(self.state_path.read_text(encoding="utf-8"))
                if loaded.get("schema_version") == 1:
                    for key, default in self._fresh_state().items():
                        loaded.setdefault(key, copy.deepcopy(default))
                    return loaded
            except (OSError, ValueError, TypeError):
                pass
        state = self._fresh_state()
        self._save_state(state)
        return state

    def _save_state(self, state: dict[str, Any]) -> None:
        if not self.state_path:
            return
        payload = json.dumps(state, indent=2, sort_keys=True)
        if len(payload.encode("utf-8")) > self.max_state_bytes:
            raise RuntimeError("Organic Processor state exceeds its configured cap")
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
        temporary.write_text(payload, encoding="utf-8")
        os.replace(temporary, self.state_path)

    @staticmethod
    def _path_edges(operators: list[str]) -> list[str]:
        previous = "START"
        edges: list[str] = []
        for name in operators:
            edges.append(f"{previous}->{name}")
            previous = name
        return edges

    def _overlay_path_score(self, operators: list[str]) -> float:
        return sum(
            float(self.state["edge_weights"].get(edge, 0.0)) for edge in self._path_edges(operators)
        )

    @staticmethod
    def cognitive_context(
        task: StructuralTask,
        *,
        evidence_refs: Iterable[str] = (),
        uncertainty: float = 0.0,
        metadata: dict[str, Any] | None = None,
    ) -> CognitiveContext:
        family_aliases = {
            "symbolic_linear_constraints": "linear_constraints",
            "boolean_case_analysis": "case_logic",
            "bounded_arithmetic": "scalar",
        }
        domain_by_family = {
            "symbolic_linear_constraints": "linear_direct",
            "boolean_case_analysis": "boolean_entailment",
            "bounded_arithmetic": "arithmetic",
            "partial_order": "topological",
        }
        family = family_aliases.get(task.family, task.family)
        features = {f"family:{family}", f"objective:{task.goal}"}
        domain = domain_by_family.get(task.family)
        if domain:
            features.add(f"domain:{domain}")

        kinds = Counter(str(item.get("kind") or "") for item in task.constraints)
        equation_count = kinds.get("linear_equation", 0)
        if equation_count:
            features.add(f"equations:{equation_count}")
        variables = {
            str(name) for item in task.constraints for name in (item.get("coefficients") or {})
        }
        if variables:
            features.add(f"variables:{len(variables)}")
        if task.constraints:
            features.add(f"premises:{len(task.constraints)}")

        return CognitiveContext(
            goal=task.goal,
            feature_keys=tuple(sorted(features)),
            operator_feature_keys=tuple(sorted(features)),
            evidence_refs=tuple(dict.fromkeys(str(item) for item in evidence_refs if str(item))),
            uncertainty=max(0.0, min(1.0, float(uncertainty))),
            metadata=dict(metadata or {}),
        )

    def _path_score(
        self,
        operators: list[str],
        task: StructuralTask | None = None,
        context: CognitiveContext | None = None,
    ) -> tuple[float, float]:
        if context is None and task is not None:
            context = self.cognitive_context(task)
        seed_score = (
            self.seed_policy.score_path(operators, context) if self.seed_policy is not None else 0.0
        )
        return seed_score + self._overlay_path_score(operators), seed_score

    def _rank_paths(
        self,
        task: StructuralTask,
        paths: Iterable[tuple[str, list[str]]],
        context: CognitiveContext | None = None,
    ) -> tuple[list[tuple[str, list[str], float, float]], list[str]]:
        missing: set[str] = set()
        ranked: list[tuple[str, list[str], float, float]] = []
        for name, operators in paths:
            normalized = [str(operator).upper() for operator in operators]
            absent = [operator for operator in normalized if operator not in OPERATORS]
            if absent:
                missing.update(absent)
                continue
            if len(normalized) > self.budgets.max_path_length:
                continue
            combined, seed_score = self._path_score(normalized, task, context)
            ranked.append((str(name), normalized, combined, seed_score))
        ranked.sort(key=lambda item: (-item[2], item[0], tuple(item[1])))
        return ranked[: self.budgets.max_candidate_paths], sorted(missing)

    def available_pathways(self, task: StructuralTask) -> dict[str, list[str]]:
        expected_goal = {
            "order_cardinality": "min_distinct_count",
            "bounded_arithmetic": "evaluate_expression",
            "grounded_evidence_selection": "select_supported_items",
        }.get(task.family)
        paths = dict(SEED_PATHWAYS.get(task.family, {})) if expected_goal == task.goal else {}
        learned_store = self.state.get("learned_pathways", {})
        learned = learned_store.get(task.capability_signature()) or learned_store.get(
            task.legacy_capability_signature()
        )
        if isinstance(learned, dict) and learned.get("operators"):
            paths[f"GROWN:{learned.get('name') or 'VERIFIED_PATHWAY'}"] = list(learned["operators"])
        composites = self.state.get("composites", {})
        composite = composites.get(task.signature()) or composites.get(task.legacy_signature())
        if isinstance(composite, dict) and composite.get("operators"):
            paths[f"COMPOSITE:{composite['name']}"] = list(composite["operators"])
        return paths

    def _execute(self, task: StructuralTask, pathway: str, operators: list[str]) -> ProcessTrace:
        state: dict[str, Any] = {}
        trace = ProcessTrace(pathway=pathway, operators=list(operators), signature=task.signature())
        try:
            for name in operators:
                state = OPERATORS[name](state, task)
                trace.intermediate.append({"operator": name, "state": dict(state)})
            trace.answer = state.get("answer")
            trace.valid = state.get(
                "model_valid",
                state.get(
                    "expression_valid",
                    state.get(
                        "selection_valid",
                        state.get(
                            "order_valid",
                            state.get("entailment_valid", state.get("linear_constraints_valid")),
                        ),
                    ),
                ),
            )
        except (
            KeyError,
            OperatorError,
            SyntaxError,
            ValueError,
            TypeError,
            ZeroDivisionError,
        ) as exc:
            trace.intermediate.append({"error": f"{type(exc).__name__}: {exc}"})
            trace.valid = False
        return trace

    def process(
        self,
        task: StructuralTask,
        explore: bool = False,
        context: CognitiveContext | None = None,
    ) -> ProcessResult:
        with self._lock:
            paths = self.available_pathways(task)
            if not paths:
                return ProcessResult(
                    status="CAPABILITY_GAP",
                    capability_gap=f"No pathway for structural signature {task.signature()}",
                )
            ranked, missing = self._rank_paths(task, paths.items(), context)
            if not ranked:
                reason = "WAITING_FOR_PRIMITIVE" if missing else "NO_BOUNDED_CANDIDATE"
                return ProcessResult(
                    status="CAPABILITY_GAP",
                    capability_gap=reason,
                    missing_primitives=missing,
                )
            if explore:
                return ProcessResult(
                    status="EXPLORED",
                    alternatives=[
                        self._execute(task, name, operators)
                        for name, operators, _combined, _seed in ranked
                    ],
                    missing_primitives=missing,
                )
            name, operators, combined_score, seed_score = ranked[0]
            trace = self._execute(task, name, operators)
            return ProcessResult(
                status="OK" if trace.answer is not None else "FAILED",
                answer=trace.answer,
                confidence=min(0.99, max(0.05, combined_score / 4.0)),
                trace=trace,
                missing_primitives=missing,
                seed_score=seed_score,
            )

    def verify_candidates(
        self,
        task: StructuralTask,
        traces: Iterable[ProcessTrace],
        verifier: Callable[[StructuralTask, ProcessTrace], VerificationReceipt],
        context: CognitiveContext | None = None,
    ) -> ProcessOutcome:
        """Convert candidate traces into a result only through an external verifier."""
        cognitive_context = context or self.cognitive_context(task)
        attempts: list[VerifiedAttempt] = []
        operator_steps = 0
        for trace in traces:
            if operator_steps + len(trace.operators) > self.budgets.max_operator_steps:
                break
            operator_steps += len(trace.operators)
            receipt = verifier(task, trace)
            if not isinstance(receipt, VerificationReceipt):
                raise TypeError("verifier_must_return_VerificationReceipt")
            _combined, seed_score = self._path_score(list(trace.operators), task, cognitive_context)
            attempts.append(VerifiedAttempt(trace, receipt, seed_score))
            if receipt.accepted:
                return ProcessOutcome(
                    status="VERIFIED",
                    answer=trace.answer,
                    trace=trace,
                    verification=receipt,
                    attempts=attempts,
                    seed_score=seed_score,
                )
        return ProcessOutcome(status="UNRESOLVED", attempts=attempts)

    @staticmethod
    def presenter_packet(
        question: str,
        outcome: ProcessOutcome,
        context: CognitiveContext,
    ) -> PresenterPacket:
        verified = bool(outcome.verification and outcome.verification.accepted)
        warnings: list[str] = []
        if outcome.capability_gap:
            warnings.append(outcome.capability_gap)
        if not verified:
            warnings.append("presenter_must_not_invent_an_answer")
        evidence_refs = (
            outcome.verification.evidence_refs
            if outcome.verification is not None
            else context.evidence_refs
        )
        return PresenterPacket(
            question=question,
            status=outcome.status,
            verified=verified,
            answer=outcome.answer if verified else None,
            evidence_refs=tuple(evidence_refs),
            processor_path=tuple(outcome.trace.operators) if outcome.trace else (),
            verifier=outcome.verification.verifier if outcome.verification else None,
            uncertainty=context.uncertainty,
            warnings=tuple(warnings),
        )

    def set_thought_status(self, status: dict[str, Any]) -> None:
        with self._lock:
            self._thought_status = copy.deepcopy(status)

    @staticmethod
    def _timestamp() -> str:
        return datetime.now(timezone.utc).isoformat()

    def register_gap(self, task: StructuralTask, reason: str) -> str:
        with self._lock:
            key = task.capability_signature()
            frontier = self.state["growth_frontier"]
            now = self._timestamp()
            entry = frontier.get(key)
            if not isinstance(entry, dict):
                if len(frontier) >= 64:
                    oldest = min(
                        frontier,
                        key=lambda item: str(frontier[item].get("last_seen") or ""),
                    )
                    frontier.pop(oldest, None)
                entry = {
                    "capability_signature": key,
                    "task": json.loads(json.dumps(task.to_dict(), default=str)),
                    "first_seen": now,
                    "attempts": 0,
                    "status": "PENDING",
                }
                frontier[key] = entry
            entry["last_seen"] = now
            entry["reason"] = str(reason or "No verified pathway")[:500]
            if entry.get("status") != "RESOLVED":
                entry["status"] = "PENDING"
            self.state["growth_event_count"] = int(self.state.get("growth_event_count", 0)) + 1
            self.state["last_growth_result"] = {
                "capability_signature": key,
                "status": "GAP_REGISTERED",
                "at": now,
            }
            self._save_state(self.state)
            return key

    def discover(
        self,
        task: StructuralTask,
        max_candidates: int = 8,
        context: CognitiveContext | None = None,
    ) -> ProcessResult:
        with self._lock:
            kinds = {str(item.get("kind") or "") for item in task.constraints}
            requested = {
                str(name).strip().upper() for name in task.required_operations if str(name).strip()
            }
            unavailable = sorted(requested - set(OPERATORS))
            if unavailable:
                return ProcessResult(
                    status="CAPABILITY_GAP",
                    capability_gap=(
                        "WAITING_FOR_PRIMITIVE: no deterministic handler for "
                        + ", ".join(unavailable)
                    ),
                    missing_primitives=unavailable,
                )
            primitives = [
                spec
                for spec in PRIMITIVE_SPECS
                if spec.name in OPERATORS
                and (not spec.goals or task.goal in spec.goals)
                and spec.required_kinds.issubset(kinds)
            ]
            limit = max(1, min(int(max_candidates), self.budgets.max_candidate_paths))
            candidates: list[list[str]] = []
            frontier: list[tuple[list[str], frozenset[str]]] = [([], frozenset())]
            visited: set[tuple[tuple[str, ...], frozenset[str]]] = set()
            while frontier and len(candidates) < limit:
                operators, available = frontier.pop(0)
                if len(operators) >= self.budgets.max_path_length:
                    continue
                for spec in primitives:
                    name = spec.name
                    if operators.count(name) >= spec.max_applications:
                        continue
                    if not spec.requires.issubset(available):
                        continue
                    provided = frozenset(set(available) | set(spec.provides))
                    if provided == available:
                        continue
                    path = [*operators, name]
                    if "answer" in provided and requested.issubset(path):
                        candidates.append(path)
                        if len(candidates) >= limit:
                            break
                        continue
                    marker = (tuple(path), provided)
                    if marker not in visited:
                        visited.add(marker)
                        frontier.append((path, provided))
            if not candidates:
                return ProcessResult(
                    status="CAPABILITY_GAP",
                    capability_gap=(
                        f"No bounded primitive composition satisfies {task.capability_signature()}"
                    ),
                )
            ranked, missing = self._rank_paths(
                task,
                (("DISCOVERED:" + ">".join(operators), operators) for operators in candidates),
                context,
            )
            return ProcessResult(
                status="EXPLORED",
                alternatives=[
                    self._execute(task, name, operators)
                    for name, operators, _combined, _seed in ranked
                ],
                missing_primitives=missing,
            )

    def promote_discovered(self, task: StructuralTask, trace: ProcessTrace) -> None:
        with self._lock:
            key = task.capability_signature()
            now = self._timestamp()
            self.state["learned_pathways"][key] = {
                "name": trace.pathway,
                "family": task.family,
                "goal": task.goal,
                "operators": list(trace.operators),
                "score": round(self._path_score(trace.operators, task)[0], 6),
                "discovered_at": now,
                "verification_required": True,
            }
            entry = self.state["growth_frontier"].setdefault(
                key,
                {
                    "capability_signature": key,
                    "task": task.to_dict(),
                    "first_seen": now,
                    "attempts": 0,
                },
            )
            entry.update(
                {
                    "status": "RESOLVED",
                    "resolved_at": now,
                    "selected_pathway": trace.pathway,
                    "operators": list(trace.operators),
                    "attempts": int(entry.get("attempts", 0)) + 1,
                }
            )
            self.state["resolved_gap_count"] = int(self.state.get("resolved_gap_count", 0)) + 1
            self.state["growth_event_count"] = int(self.state.get("growth_event_count", 0)) + 1
            self.state["last_growth_result"] = {
                "capability_signature": key,
                "status": "RESOLVED",
                "pathway": trace.pathway,
                "at": now,
            }
            self._save_state(self.state)

    def record_growth_failure(self, task: StructuralTask, reason: str) -> None:
        with self._lock:
            key = task.capability_signature()
            now = self._timestamp()
            entry = self.state["growth_frontier"].setdefault(
                key,
                {
                    "capability_signature": key,
                    "task": task.to_dict(),
                    "first_seen": now,
                    "attempts": 0,
                },
            )
            attempts = int(entry.get("attempts", 0)) + 1
            waiting_for_primitive = "WAITING_FOR_PRIMITIVE" in str(reason).upper()
            entry.update(
                {
                    "attempts": attempts,
                    "last_attempt": now,
                    "status": (
                        "WAITING_FOR_PRIMITIVE"
                        if waiting_for_primitive or attempts >= 2
                        else "PENDING"
                    ),
                    "last_error": str(reason or "No candidate passed external verification")[:500],
                }
            )
            self.state["growth_event_count"] = int(self.state.get("growth_event_count", 0)) + 1
            self.state["last_growth_result"] = {
                "capability_signature": key,
                "status": entry["status"],
                "at": now,
            }
            self._save_state(self.state)

    def next_growth_task(self) -> StructuralTask | None:
        with self._lock:
            pending = [
                entry
                for entry in self.state.get("growth_frontier", {}).values()
                if isinstance(entry, dict) and entry.get("status") == "PENDING"
            ]
            if not pending:
                return None
            pending.sort(key=lambda item: str(item.get("first_seen") or ""))
            try:
                return StructuralTask.from_dict(dict(pending[0]["task"]))
            except (KeyError, TypeError, ValueError):
                return None

    def learn(self, trace: ProcessTrace, reward: float, feedback_source: str) -> None:
        with self._lock:
            bounded_reward = max(-1.0, min(1.0, float(reward)))
            weights = self.state["edge_weights"]
            for edge in self._path_edges(trace.operators):
                current = float(weights.get(edge, 0.0))
                weights[edge] = round(
                    max(-2.0, min(3.0, current + self.learning_rate * bounded_reward)), 6
                )
            counts_key = "success_counts" if bounded_reward > 0 else "failure_counts"
            if bounded_reward != 0:
                self.state[counts_key][trace.signature] = (
                    int(self.state[counts_key].get(trace.signature, 0)) + 1
                )
            source = str(feedback_source or "external")[:64]
            sources = self.state["feedback_source_counts"]
            sources[source] = int(sources.get(source, 0)) + 1
            self.state["experience_count"] = int(self.state.get("experience_count", 0)) + 1
            successes = int(self.state["success_counts"].get(trace.signature, 0))
            if bounded_reward > 0 and successes >= 3:
                score = self._path_score(trace.operators)[0]
                existing = self.state["composites"].get(trace.signature)
                if not existing or score > float(existing.get("score", -999.0)):
                    self.state["composites"][trace.signature] = {
                        "name": "VERIFIED_STRUCTURAL_PATH",
                        "operators": list(trace.operators),
                        "score": round(score, 6),
                        "promoted_after_successes": successes,
                    }
            self._save_state(self.state)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self.state)

    def restore(self, state: dict[str, Any]) -> None:
        with self._lock:
            if state.get("schema_version") != 1:
                raise ValueError("Unsupported Organic Processor state schema")
            self._save_state(state)
            self.state = copy.deepcopy(state)

    def status(self) -> dict[str, Any]:
        with self._lock:
            learned = self.state.get("learned_pathways", {})
            frontier = self.state.get("growth_frontier", {})
            seed_names = (
                set(self.seed_policy.primitive_names)
                if self.seed_policy is not None
                else {spec.name for spec in PRIMITIVE_SPECS}
            )
            executable_seed_names = seed_names.intersection(OPERATORS)
            learned_families = {
                str(item.get("family"))
                for item in learned.values()
                if isinstance(item, dict) and item.get("family")
            }
            return {
                "mode": self.mode,
                "version": self.version,
                "state_path": str(self.state_path) if self.state_path else None,
                "state_bytes": len(json.dumps(self.state, sort_keys=True).encode("utf-8")),
                "state_cap_bytes": self.max_state_bytes,
                "experience_count": int(self.state.get("experience_count", 0)),
                "composite_count": len(self.state.get("composites", {})),
                "learned_pathway_count": len(learned),
                "growth_frontier_count": sum(
                    1
                    for item in frontier.values()
                    if isinstance(item, dict) and item.get("status") != "RESOLVED"
                ),
                "resolved_gap_count": int(self.state.get("resolved_gap_count", 0)),
                "growth_event_count": int(self.state.get("growth_event_count", 0)),
                "last_growth_result": copy.deepcopy(self.state.get("last_growth_result")),
                "supported_families": sorted(set(SEED_PATHWAYS) | learned_families),
                "seed_primitive_count": len(seed_names),
                "declared_primitive_count": len(PRIMITIVE_SPECS),
                "executable_primitive_count": len(executable_seed_names),
                "waiting_primitive_count": len(seed_names - executable_seed_names),
                "v7_seed": (
                    self.seed_policy.status()
                    if self.seed_policy is not None
                    else {
                        "loaded": False,
                        "version": "V7",
                        "error": self.seed_error,
                        "immutable_base": True,
                    }
                ),
                "runtime_learning_overlay": {
                    "state_path": str(self.state_path) if self.state_path else None,
                    "experience_count": int(self.state.get("experience_count", 0)),
                    "recoverable_without_seed_mutation": True,
                },
                "thought_policy": copy.deepcopy(self._thought_status),
                # Kept for callers of the earlier status contract.
                "discoverable_primitives": sum(
                    1 for spec in PRIMITIVE_SPECS if spec.name in OPERATORS
                ),
                "external_resource_access": False,
            }
