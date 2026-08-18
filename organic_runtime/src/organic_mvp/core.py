from __future__ import annotations

import json
import logging
import math
import re
import threading
from pathlib import Path
from typing import Any

from .config import AppConfig
from .db import MemoryDB
from .util import (
    atomic_write_text,
    content_words,
    contains_negation,
    norm_space,
    sha256_file,
    sha256_text,
    utcnow,
)


class CoreError(RuntimeError):
    pass


class OrganicExecutivePolicy:
    """Bounded policy used by the Executive for evidence and growth decisions.

    The core does not contain a domain fact store and it never receives direct web access.
    Cognition supplies an Active Weave of grounded evidence. The core learns small,
    fixed-dimensional processing policies: when to answer, search, verify, or abstain,
    and which structural frontier properties have historically produced useful growth.

    Domain knowledge remains outside the core in Living Memory, indexes, evidence cache,
    procedures, and tools. Core state size is measured from disk and hard-capped.
    """

    mode = "organic_executive_policy"
    version = "0.4.0"

    ACTIONS = ("ANSWER", "SEARCH", "VERIFY", "ABSTAIN")
    TASK_FEATURES = (
        "bias",
        "evidence_count",
        "max_retrieval",
        "mean_retrieval",
        "query_coverage",
        "source_diversity",
        "has_negation",
        "question_why",
        "question_how",
        "question_definition",
        "question_yes_no",
    )
    GROWTH_FEATURES = (
        "bias",
        "knowledge_gap",
        "recurrence",
        "uncertainty",
        "not_recently_grown",
        "structural_connectedness",
    )

    def __init__(self, config: AppConfig, db: MemoryDB, logger: logging.Logger):
        self.config = config
        self.db = db
        self.logger = logger
        self.core_dir = config.root / "data" / "core"
        self.core_dir.mkdir(parents=True, exist_ok=True)
        self.state_path = self.core_dir / "processing_core_state.json"
        self.cap_bytes = int(config.get("max_core_bytes", 5 * 1024**3))
        self.learning_enabled = bool(config.get("core_learning_enabled", True))
        self.learning_rate = float(config.get("core_learning_rate", 0.08))
        self._state_lock = threading.RLock()
        self.state = self._load_or_initialize()
        self._enforce_cap()

    def _initial_state(self) -> dict[str, Any]:
        # Priors are structural only. There are no domain tokens, facts, entity names,
        # programming syntax, or subject-matter rules here.
        task_weights = {
            "ANSWER": {
                "bias": -0.55,
                "evidence_count": 0.65,
                "max_retrieval": 1.40,
                "mean_retrieval": 0.65,
                "query_coverage": 1.25,
                "source_diversity": 0.35,
                "has_negation": -0.15,
                "question_why": 0.10,
                "question_how": 0.10,
                "question_definition": 0.15,
                "question_yes_no": 0.00,
            },
            "SEARCH": {
                "bias": 1.15,
                "evidence_count": -0.70,
                "max_retrieval": -1.05,
                "mean_retrieval": -0.35,
                "query_coverage": -1.15,
                "source_diversity": -0.20,
                "has_negation": 0.05,
                "question_why": 0.18,
                "question_how": 0.16,
                "question_definition": 0.08,
                "question_yes_no": 0.00,
            },
            "VERIFY": {
                "bias": -0.25,
                "evidence_count": 0.10,
                "max_retrieval": 0.20,
                "mean_retrieval": 0.15,
                "query_coverage": 0.15,
                "source_diversity": 0.95,
                "has_negation": 0.45,
                "question_why": -0.10,
                "question_how": -0.10,
                "question_definition": -0.10,
                "question_yes_no": 0.30,
            },
            "ABSTAIN": {
                "bias": -0.40,
                "evidence_count": -0.45,
                "max_retrieval": -0.50,
                "mean_retrieval": -0.25,
                "query_coverage": -0.45,
                "source_diversity": -0.10,
                "has_negation": 0.05,
                "question_why": 0.00,
                "question_how": 0.00,
                "question_definition": 0.00,
                "question_yes_no": 0.00,
            },
        }
        growth_weights = {
            "bias": 0.05,
            "knowledge_gap": 1.20,
            "recurrence": 0.85,
            "uncertainty": 0.55,
            "not_recently_grown": 0.70,
            "structural_connectedness": 0.35,
        }
        return {
            "format": "organic-processing-core-state-v1",
            "version": self.version,
            "created_at": utcnow(),
            "updated_at": utcnow(),
            "learning_updates": 0,
            "task_decisions": 0,
            "growth_decisions": 0,
            "task_weights": task_weights,
            "growth_weights": growth_weights,
            "reward_totals": {a: 0.0 for a in self.ACTIONS},
            "growth_reward_total": 0.0,
        }

    def _load_or_initialize(self) -> dict[str, Any]:
        if self.state_path.exists():
            try:
                state = json.loads(self.state_path.read_text(encoding="utf-8"))
                if state.get("format") == "organic-processing-core-state-v1":
                    return state
            except Exception as exc:
                self.logger.warning("Processing Core state could not be loaded; reinitializing: %s", exc)
        state = self._initial_state()
        self._write_state(state)
        return state

    def _write_state(self, state: dict[str, Any]) -> None:
        with self._state_lock:
            state = dict(state)
            state["updated_at"] = utcnow()
            atomic_write_text(self.state_path, json.dumps(state, indent=2, sort_keys=True))
            self._enforce_cap()

    def _core_bytes(self) -> int:
        total = 0
        for p in self.core_dir.rglob("*"):
            if p.is_file():
                try:
                    total += p.stat().st_size
                except OSError:
                    pass
        return total

    def _enforce_cap(self) -> None:
        size = self._core_bytes()
        if size > self.cap_bytes:
            raise CoreError(f"Organic Processing Core is {size} bytes, above hard cap {self.cap_bytes} bytes")

    def implementation_fingerprint(self) -> str:
        try:
            return sha256_file(Path(__file__))
        except Exception:
            return sha256_text(self.version)

    def state_fingerprint(self) -> str:
        try:
            return sha256_file(self.state_path) if self.state_path.exists() else ""
        except OSError as exc:
            self.logger.warning("Processing Core state fingerprint unavailable: %s", exc)
            return "unavailable"

    def status(self) -> dict[str, Any]:
        with self._state_lock:
            return {
                "mode": self.mode,
                "version": self.version,
                "ready": True,
                "learning_enabled": self.learning_enabled,
                "learning_updates": int(self.state.get("learning_updates", 0)),
                "task_decisions": int(self.state.get("task_decisions", 0)),
                "growth_decisions": int(self.state.get("growth_decisions", 0)),
                "core_bytes": self._core_bytes(),
                "hard_cap_bytes": self.cap_bytes,
                "hard_cap_gib": round(self.cap_bytes / (1024**3), 3),
                "implementation_fingerprint": self.implementation_fingerprint(),
                "state_fingerprint": self.state_fingerprint(),
                "domain_knowledge_policy": "external_only",
                "web_access": "none_direct; evidence supplied by Cognition",
                "actions": list(self.ACTIONS),
                "task_features": list(self.TASK_FEATURES),
                "growth_features": list(self.GROWTH_FEATURES),
            }

    @staticmethod
    def _clip(v: float, low: float = -5.0, high: float = 5.0) -> float:
        return max(low, min(high, v))

    @staticmethod
    def _sigmoid(v: float) -> float:
        if v >= 0:
            z = math.exp(-v)
            return 1.0 / (1.0 + z)
        z = math.exp(v)
        return z / (1.0 + z)

    def _task_features(self, goal: str, evidence: list[dict]) -> dict[str, float]:
        qterms = set(content_words(goal))
        scores = [float(e.get("retrieval_score", 0.0) or 0.0) for e in evidence]
        source_keys = {
            str(e.get("source_id") or e.get("source_url") or "")
            for e in evidence
            if (e.get("source_id") or e.get("source_url"))
        }
        union = set()
        for e in evidence[:20]:
            union.update(content_words(str(e.get("text", ""))))
        qcov = len(qterms & union) / max(1, len(qterms))
        lc = goal.strip().lower()
        first = lc.split(None, 1)[0] if lc else ""
        return {
            "bias": 1.0,
            "evidence_count": min(1.0, len(evidence) / 8.0),
            "max_retrieval": max(scores) if scores else 0.0,
            "mean_retrieval": (sum(scores) / len(scores)) if scores else 0.0,
            "query_coverage": qcov,
            "source_diversity": min(1.0, len(source_keys) / 3.0),
            "has_negation": 1.0 if contains_negation(goal) else 0.0,
            "question_why": 1.0 if first == "why" else 0.0,
            "question_how": 1.0 if first == "how" else 0.0,
            "question_definition": 1.0 if first == "what" or lc.startswith("define ") else 0.0,
            "question_yes_no": 1.0 if first in {"is", "are", "was", "were", "do", "does", "did", "can", "could", "will", "would"} else 0.0,
        }

    def _score_actions(self, features: dict[str, float], mode: str = "task") -> dict[str, float]:
        scores: dict[str, float] = {}
        for action in self.ACTIONS:
            w = self.state["task_weights"][action]
            raw = sum(float(w.get(k, 0.0)) * float(features.get(k, 0.0)) for k in self.TASK_FEATURES)
            scores[action] = raw
        if mode == "claim_validation":
            scores["VERIFY"] += 1.35
            scores["ANSWER"] -= 0.45
        return scores

    def decide(self, goal: str, evidence: list[dict], mode: str = "task") -> dict[str, Any]:
        features = self._task_features(goal, evidence)
        with self._state_lock:
            scores = self._score_actions(features, mode=mode)
            # Safety floor: no evidence cannot produce an answer or verification result.
            if not evidence:
                scores["ANSWER"] -= 2.0
                scores["VERIFY"] -= 1.5
                scores["SEARCH"] += 1.5
            action = max(scores, key=scores.get)
            ordered = sorted(scores.values(), reverse=True)
            margin = ordered[0] - ordered[1] if len(ordered) > 1 else ordered[0]
            confidence = self._sigmoid(margin)
            self.state["task_decisions"] = int(self.state.get("task_decisions", 0)) + 1
            self._write_state(self.state)
        return {
            "action": action,
            "confidence": round(confidence, 4),
            "scores": {k: round(v, 4) for k, v in scores.items()},
            "features": {k: round(v, 4) for k, v in features.items()},
            "mode": mode,
        }

    def learn_decision(self, task_id: str, decision: dict[str, Any], reward: float, outcome: str, metadata: dict | None = None) -> None:
        action = str(decision.get("action") or "")
        if action not in self.ACTIONS:
            return
        reward = self._clip(float(reward), -1.0, 1.0)
        features = decision.get("features") or {}
        if self.learning_enabled:
            with self._state_lock:
                weights = self.state["task_weights"][action]
                for k in self.TASK_FEATURES:
                    old = float(weights.get(k, 0.0))
                    delta = self.learning_rate * reward * float(features.get(k, 0.0))
                    weights[k] = round(self._clip(old + delta), 6)
                # Small competitive pressure keeps a failed action from dominating forever.
                if reward < 0:
                    for other in self.ACTIONS:
                        if other == action:
                            continue
                        ow = self.state["task_weights"][other]
                        for k in self.TASK_FEATURES:
                            old = float(ow.get(k, 0.0))
                            delta = self.learning_rate * (-reward) * 0.03 * float(features.get(k, 0.0))
                            ow[k] = round(self._clip(old + delta), 6)
                self.state["learning_updates"] = int(self.state.get("learning_updates", 0)) + 1
                self.state["reward_totals"][action] = round(float(self.state["reward_totals"].get(action, 0.0)) + reward, 6)
                self._write_state(self.state)
        self.db.record_core_learning_event(
            decision_kind="task_policy",
            selected_action=action,
            reward=reward,
            outcome=outcome,
            features=features,
            scores=decision.get("scores") or {},
            metadata={"task_id": task_id, **(metadata or {})},
        )

    def _growth_features(self, candidate: dict) -> dict[str, float]:
        degree = max(0, int(candidate.get("degree") or 0))
        mentions = max(0, int(candidate.get("mention_count") or 0))
        return {
            "bias": 1.0,
            "knowledge_gap": 1.0 / (1.0 + degree),
            "recurrence": min(1.0, math.log1p(mentions) / math.log(8.0)),
            "uncertainty": 1.0 if str(candidate.get("status") or "").upper() != "GROUNDED" else 0.0,
            "not_recently_grown": 1.0 if not candidate.get("last_growth_at") else 0.25,
            "structural_connectedness": min(1.0, degree / 5.0),
        }

    def select_growth_target(self, candidates: list[dict]) -> tuple[dict | None, dict | None]:
        if not candidates:
            return None, None
        with self._state_lock:
            scored = []
            weights = self.state["growth_weights"]
            for c in candidates:
                feats = self._growth_features(c)
                score = sum(float(weights.get(k, 0.0)) * feats[k] for k in self.GROWTH_FEATURES)
                scored.append((score, c, feats))
            scored.sort(key=lambda x: (x[0], int(x[1].get("mention_count") or 0)), reverse=True)
            score, candidate, feats = scored[0]
            self.state["growth_decisions"] = int(self.state.get("growth_decisions", 0)) + 1
            self._write_state(self.state)
        trace = {
            "score": round(score, 4),
            "features": {k: round(v, 4) for k, v in feats.items()},
            "top_candidates": [
                {
                    "concept_id": x[1].get("concept_id"),
                    "label": x[1].get("label"),
                    "score": round(x[0], 4),
                }
                for x in scored[:5]
            ],
        }
        return candidate, trace

    def learn_growth_outcome(self, task_id: str, selection_trace: dict | None, reward: float, outcome: str, metadata: dict | None = None) -> None:
        if not selection_trace:
            return
        reward = self._clip(float(reward), -1.0, 1.0)
        feats = selection_trace.get("features") or {}
        if self.learning_enabled:
            with self._state_lock:
                weights = self.state["growth_weights"]
                for k in self.GROWTH_FEATURES:
                    old = float(weights.get(k, 0.0))
                    weights[k] = round(self._clip(old + self.learning_rate * 0.5 * reward * float(feats.get(k, 0.0))), 6)
                self.state["learning_updates"] = int(self.state.get("learning_updates", 0)) + 1
                self.state["growth_reward_total"] = round(float(self.state.get("growth_reward_total", 0.0)) + reward, 6)
                self._write_state(self.state)
        self.db.record_core_learning_event(
            decision_kind="growth_policy",
            selected_action="SELECT_FRONTIER",
            reward=reward,
            outcome=outcome,
            features=feats,
            scores={"selected_score": selection_trace.get("score", 0.0)},
            metadata={"task_id": task_id, **(metadata or {})},
        )

    def generate_query(self, goal: str, context: list[str]) -> str:
        """Create a search query using structure, novelty, and lexical salience only."""
        instruction_terms = {
            "check", "details", "find", "grounded", "identify", "information",
            "look", "lookup", "missing", "omits", "prefer", "provide", "report",
            "research", "result", "specific", "summarize", "summary", "verify",
        }
        raw_terms = list(dict.fromkeys(content_words(goal)))
        terms = [term for term in raw_terms if term not in instruction_terms] or raw_terms
        ctx = " ".join(context).lower()
        # Prefer terms that are central to the goal but not already well represented in context.
        ranked = []
        for i, term in enumerate(terms):
            novelty = 1.0 / (1.0 + ctx.count(term))
            length_bonus = min(0.8, max(0.0, (len(term) - 4) * 0.08))
            early_bonus = max(0.0, 0.35 - i * 0.025)
            ranked.append((novelty + length_bonus + early_bonus, term))
        ranked.sort(key=lambda x: x[0], reverse=True)
        chosen = [t for _s, t in ranked[:10]]
        if not chosen:
            return norm_space(goal)[:180]
        # Preserve goal order for readability/search-engine behavior.
        order = {t: i for i, t in enumerate(terms)}
        chosen.sort(key=lambda t: order.get(t, 999))
        return " ".join(chosen)[:180]

    @staticmethod
    def _term_overlap(a: str, b: str) -> float:
        aa, bb = set(content_words(a)), set(content_words(b))
        if not aa or not bb:
            return 0.0
        return len(aa & bb) / max(1, min(len(aa), len(bb)))

    def _relation_reason(self, question: str, evidence: list[dict]) -> dict[str, Any] | None:
        """Apply generic relation/direct-chain operators to the Active Weave.

        This contains no domain facts. It operates only on grounded relations supplied
        by Cognition/Living Memory and returns an auditable structural trace.
        """
        triples: list[dict[str, Any]] = []
        for e in evidence:
            for r in e.get("relations") or []:
                subject = norm_space(str(r.get("subject_label") or ""))
                predicate = norm_space(str(r.get("predicate") or ""))
                obj = norm_space(str(r.get("object_label") or r.get("object_text") or ""))
                if not subject or not predicate or not obj:
                    continue
                triples.append({
                    "subject": subject,
                    "predicate": predicate,
                    "object": obj,
                    "confidence": float(r.get("relation_confidence", 0.55) or 0.55),
                    "relation_id": r.get("relation_id"),
                    "claim_id": e.get("claim_id"),
                    "source_url": e.get("source_url"),
                    "source_title": e.get("source_title"),
                    "retrieval_score": float(e.get("retrieval_score", 0.0) or 0.0),
                })
        if not triples:
            return None

        qterms = set(content_words(question))
        if not qterms:
            return None
        ranked: list[tuple[float, dict]] = []
        for t in triples:
            node_terms = set(content_words(t["subject"] + " " + t["object"]))
            pred_terms = set(content_words(t["predicate"]))
            node_cov = len(qterms & node_terms) / max(1, len(qterms))
            pred_cov = len(qterms & pred_terms) / max(1, len(qterms))
            score = node_cov * 0.58 + pred_cov * 0.14 + t["retrieval_score"] * 0.18 + t["confidence"] * 0.10
            ranked.append((score, t))
        ranked.sort(key=lambda x: x[0], reverse=True)
        best_score, best = ranked[0]

        # Generic two/three-hop composition. Nodes may match exactly or by strong
        # lexical overlap; this allows surface variants without a domain ontology.
        best_chain: list[dict] = [best]
        best_chain_score = best_score
        for first_score, first in ranked[:12]:
            for second_score, second in ranked[:18]:
                if first is second:
                    continue
                bridge = self._term_overlap(first["object"], second["subject"])
                if bridge < 0.60:
                    continue
                combined = f'{first["subject"]} {first["predicate"]} {first["object"]} {second["predicate"]} {second["object"]}'
                cov = len(qterms & set(content_words(combined))) / max(1, len(qterms))
                score2 = cov * 0.58 + (first_score + second_score) * 0.18 + bridge * 0.18
                chain = [first, second]
                if score2 > best_chain_score + 0.05:
                    best_chain_score, best_chain = score2, chain
                for third_score, third in ranked[:18]:
                    if third is first or third is second:
                        continue
                    bridge2 = self._term_overlap(second["object"], third["subject"])
                    if bridge2 < 0.60:
                        continue
                    combined3 = combined + f' {third["predicate"]} {third["object"]}'
                    cov3 = len(qterms & set(content_words(combined3))) / max(1, len(qterms))
                    score3 = cov3 * 0.56 + (first_score + second_score + third_score) * 0.12 + (bridge + bridge2) * 0.15
                    if score3 > best_chain_score + 0.05:
                        best_chain_score, best_chain = score3, [first, second, third]

        lower = question.lower().strip()
        wants_chain = lower.startswith(("why ", "how ", "explain ", "trace ", "show how "))
        chosen = best_chain if wants_chain and len(best_chain) > 1 else [best]
        if len(chosen) == 1 and best_score < 0.16:
            return None

        if len(chosen) == 1:
            t = chosen[0]
            answer = f'{t["subject"]} {t["predicate"]} {t["object"]}.'
            operator = "DIRECT_RELATION"
        else:
            clauses = [f'{t["subject"]} {t["predicate"]} {t["object"]}' for t in chosen]
            answer = "; therefore the grounded relation path is: " + " -> ".join(clauses) + "."
            operator = f"CHAIN_{len(chosen)}"

        sources = []
        for t in chosen:
            if t.get("source_url") and not any(x.get("url") == t["source_url"] for x in sources):
                sources.append({"title": t.get("source_title") or t["source_url"], "url": t["source_url"]})
        trace = {
            "operator": operator,
            "score": round(best_chain_score if len(chosen) > 1 else best_score, 4),
            "path": [{k: t.get(k) for k in ("subject", "predicate", "object", "relation_id", "claim_id")} for t in chosen],
        }
        return {"answer": answer, "sources": sources[:6], "reasoning_trace": trace,
                "confidence": min(0.91, 0.45 + (best_chain_score if len(chosen) > 1 else best_score) * 0.42)}

    def answer(self, question: str, evidence: list[dict]) -> dict[str, Any]:
        decision = self.decide(question, evidence, mode="task")
        if not evidence:
            return {
                "answer": "",
                "confidence": 0.0,
                "missing": ["No grounded evidence was retrieved."],
                "decision": decision,
                "sources": [],
                "reasoning_trace": {"operator": "NO_EVIDENCE"},
            }
        if decision["action"] in {"SEARCH", "ABSTAIN"}:
            return {
                "answer": "",
                "confidence": min(0.45, float(decision["confidence"])),
                "missing": ["Processing Core selected additional evidence acquisition before answering."],
                "decision": decision,
                "sources": [],
                "reasoning_trace": {"operator": "DEFER_FOR_EVIDENCE"},
            }

        relation_result = self._relation_reason(question, evidence)
        if relation_result:
            return {
                "answer": relation_result["answer"],
                "confidence": round(float(relation_result["confidence"]), 3),
                "missing": [],
                "sources": relation_result["sources"],
                "decision": decision,
                "reasoning_trace": relation_result["reasoning_trace"],
            }

        # Fallback operator: select the strongest grounded claims without inventing a
        # conclusion. The Semantic Interface may paraphrase this result but must not
        # add factual conclusions that the Core did not produce.
        qterms = set(content_words(question))
        ranked = []
        for e in evidence:
            text = str(e.get("text", ""))
            tterms = set(content_words(text))
            overlap = len(qterms & tterms) / max(1, len(qterms))
            score = overlap * 0.72 + float(e.get("retrieval_score", 0.0) or 0.0) * 0.18 + float(e.get("confidence", 0.5)) * 0.10
            ranked.append((score, text, e))
        ranked.sort(key=lambda x: x[0], reverse=True)
        useful = [x for x in ranked if x[0] > 0.10][:5]
        if not useful:
            return {
                "answer": "",
                "confidence": 0.15,
                "missing": ["Current Active Weave does not sufficiently cover the task."],
                "decision": decision,
                "sources": [],
                "reasoning_trace": {"operator": "INSUFFICIENT_WEAVE"},
            }

        sentences, sources = [], []
        for score, text, e in useful:
            if text not in sentences:
                sentences.append(text)
            url = e.get("source_url")
            if url and not any(x.get("url") == url for x in sources):
                sources.append({"title": e.get("source_title") or url, "url": url})
        evidence_quality = useful[0][0]
        diversity = min(1.0, len(sources) / 3.0)
        confidence = min(0.82, 0.30 + evidence_quality * 0.40 + diversity * 0.10 + len(useful[:3]) * 0.03)
        return {
            "answer": " ".join(sentences[:4]),
            "confidence": round(confidence, 3),
            "missing": ["Grounded evidence coverage remains weak."] if confidence < 0.50 else [],
            "sources": sources[:6],
            "decision": decision,
            "reasoning_trace": {"operator": "GROUNDED_CLAIM_SELECTION", "claim_ids": [x[2].get("claim_id") for x in useful[:4]]},
        }

    def solve_self_contained(self, question: str, plan: dict[str, Any]) -> dict[str, Any]:
        """Execute an authorized closed-world plan without factual memory or web access."""
        del question, plan
        raise CoreError(
            "Raw-language self-contained solving was removed. Route typed structural tasks "
            "through Cognition and organic_processor."
        )

    def judge_claim(self, claim: str, evidence: list[dict]) -> dict[str, Any]:
        decision = self.decide(claim, evidence, mode="claim_validation")
        if not evidence:
            return {
                "status": "UNRESOLVED",
                "confidence": 0.15,
                "reason": "No independent grounded evidence is available.",
                "used_claim_ids": [],
                "decision": decision,
            }

        # Claim validation is intentionally conservative. A user statement is not
        # promoted merely because a related passage shares many words. Normalize
        # light morphology (regulate/regulating, release/released) and require very
        # high claim coverage for positive support. Ambiguous cases stay UNRESOLVED.
        def _claim_term(word: str) -> str:
            w = word.lower()
            if len(w) > 6 and w.endswith("ing"):
                w = w[:-3]
            elif len(w) > 5 and w.endswith("ed"):
                w = w[:-2]
            elif len(w) > 5 and w.endswith("es"):
                w = w[:-2]
            elif len(w) > 4 and w.endswith("s"):
                w = w[:-1]
            if len(w) > 5 and w.endswith("e"):
                w = w[:-1]
            return w

        claim_terms = {_claim_term(w) for w in content_words(claim)}
        claim_neg = contains_negation(claim)
        best_support = (0.0, None)
        best_contra = (0.0, None)

        # Generic opposition pairs describe logical polarity, not domain knowledge.
        opposite_pairs = (
            (("open", "opens", "opening", "opened"), ("close", "closes", "closing", "closed")),
            (("increase", "increases", "increasing", "increased"), ("decrease", "decreases", "reduce", "reduces", "reducing", "reduced")),
            (("enter", "enters", "intake", "absorb", "absorbs"), ("exit", "exits", "escape", "escapes", "release", "releases")),
            (("present", "exists", "exist"), ("absent", "missing", "does not exist")),
        )

        for e in evidence:
            text = str(e.get("text", ""))
            eterms = {_claim_term(w) for w in content_words(text)}
            overlap = len(claim_terms & eterms) / max(1, len(claim_terms))
            ev_neg = contains_negation(text)
            support = overlap
            contra = 0.0
            if overlap >= 0.34 and claim_neg != ev_neg:
                contra = overlap + 0.10
            lc, le = claim.lower(), text.lower()
            for left, right in opposite_pairs:
                claim_left = any(re.search(r"\b" + re.escape(x) + r"\b", lc) for x in left)
                claim_right = any(re.search(r"\b" + re.escape(x) + r"\b", lc) for x in right)
                ev_left = any(re.search(r"\b" + re.escape(x) + r"\b", le) for x in left)
                ev_right = any(re.search(r"\b" + re.escape(x) + r"\b", le) for x in right)
                if overlap >= 0.30 and ((claim_left and ev_right and not ev_left) or (claim_right and ev_left and not ev_right)):
                    contra = max(contra, overlap + 0.24)
            if contra > best_contra[0]:
                best_contra = (contra, e)
            if support > best_support[0] and contra < support:
                best_support = (support, e)

        if best_contra[0] >= 0.58 and best_contra[0] >= best_support[0] + 0.05:
            e = best_contra[1] or {}
            return {
                "status": "CONTRADICTED",
                "confidence": min(0.92, best_contra[0]),
                "reason": str(e.get("text", "")),
                "used_claim_ids": [e.get("claim_id")] if e.get("claim_id") else [],
                "decision": decision,
            }
        if best_support[0] >= 0.88:
            e = best_support[1] or {}
            return {
                "status": "SUPPORTED",
                "confidence": min(0.90, best_support[0]),
                "reason": str(e.get("text", "")),
                "used_claim_ids": [e.get("claim_id")] if e.get("claim_id") else [],
                "decision": decision,
            }
        return {
            "status": "UNRESOLVED",
            "confidence": max(best_support[0], best_contra[0]),
            "reason": "Retrieved evidence is related but not decisive.",
            "used_claim_ids": [],
            "decision": decision,
        }

    def extract_proposals(self, text: str, title: str = "") -> list[dict]:
        # Deliberately empty in this MVP. Durable graph extraction is handled by the
        # evidence-grounded Memory Compiler. This prevents the Processing Core from
        # becoming a hidden domain-memory writer.
        return []


# Backward-compatible type alias used by the cognition/memory modules.
OrganicProcessingCore = OrganicExecutivePolicy
BaseCore = OrganicExecutivePolicy


def build_core(config: AppConfig, db: MemoryDB, logger: logging.Logger) -> OrganicExecutivePolicy:
    return OrganicExecutivePolicy(config, db, logger)
