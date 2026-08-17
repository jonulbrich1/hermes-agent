from __future__ import annotations

import hashlib
import json
import logging
import re
from pathlib import Path
from typing import Any

from .config import AppConfig
from .memory import MemoryCompiler
from .util import atomic_write_text, content_words, norm_space, utcnow


ROUTES = ("CONVERSATION", "STATE", "RETRIEVE", "REASON", "GROWTH")


class InteractionGate:
    """Programmatic authority between the semantic LLM and Organic cognition.

    The small LLM may recommend a route, but this gate decides what is allowed.
    Uncertain/factual requests escalate; the LLM cannot use its own pretrained
    factual knowledge as a substitute for Organic memory/core processing.
    """

    def __init__(self, root: Path, config: AppConfig, memory: MemoryCompiler, logger: logging.Logger):
        self.root = root
        self.config = config
        self.memory = memory
        self.logger = logger
        self.cache_path = root / "data" / "route_cache.json"
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache = self._load_cache()
        if not self.cache_path.exists():
            self._save_cache()

    def _load_cache(self) -> dict[str, Any]:
        if not self.cache_path.exists():
            return {}
        try:
            return json.loads(self.cache_path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _save_cache(self) -> None:
        atomic_write_text(self.cache_path, json.dumps(self.cache, indent=2, sort_keys=True))

    @staticmethod
    def _looks_social(text: str) -> bool:
        t = re.sub(r"[^a-z0-9' ]+", " ", text.lower()).strip()
        exact = {
            "hi", "hello", "hey", "thanks", "thank you", "good morning", "good afternoon",
            "good evening", "good night", "cool", "nice", "ok", "okay", "got it", "sounds good",
        }
        if t in exact:
            return True
        patterns = (
            r"^(hi|hello|hey)\b",
            r"^(thanks|thank you)\b",
            r"^how('s| is) it going[? ]*$",
        )
        return any(re.search(p, t) for p in patterns)

    @staticmethod
    def _looks_state(text: str) -> bool:
        t = text.lower()
        patterns = (
            "how are you", "how are things", "what are you doing", "what are you learning",
            "what have you learned", "are you busy", "are you idle", "system status", "your status",
            "how is your memory", "how much have you learned", "any errors", "did anything go wrong",
        )
        return any(p in t for p in patterns)

    @staticmethod
    def _is_factual_or_task(text: str) -> bool:
        t = text.strip().lower()
        if not t:
            return False
        if InteractionGate._looks_social(t) or InteractionGate._looks_state(t):
            return False
        if "?" in text:
            return True
        first = t.split(None, 1)[0] if t else ""
        return first in {
            "what", "why", "how", "when", "where", "who", "which", "explain", "analyze", "compare",
            "find", "tell", "calculate", "determine", "identify", "write", "create", "build", "check",
            "verify", "research", "summarize", "debug", "fix",
        }

    @staticmethod
    def _signature(text: str, semantic: dict[str, Any]) -> str:
        t = text.lower().strip()
        first = t.split(None, 1)[0] if t else "none"
        qtype = "question" if "?" in text else "statement"
        neg = "neg" if any(x in t.split() for x in ("not", "never", "no")) else "pos"
        length = "short" if len(t.split()) < 7 else "medium" if len(t.split()) < 22 else "long"
        intent = str(semantic.get("intent") or "unknown").lower()[:40]
        raw = f"{first}|{qtype}|{neg}|{length}|{intent}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    def choose_route(self, text: str, semantic: dict[str, Any]) -> dict[str, Any]:
        text = norm_space(text)
        suggested = str(semantic.get("route_suggestion") or "REASON").upper()
        if suggested not in ROUTES:
            suggested = "REASON"
        confidence = float(semantic.get("confidence", 0.0) or 0.0)
        reason = []
        factual = self._is_factual_or_task(text)

        # The LLM can describe the user's language, but it cannot classify a task
        # into STATE just by setting needs_internal_state. That exact failure lets
        # ordinary work requests bypass Organic grounding.
        if self._looks_state(text):
            route = "STATE"
            reason.append("system-state intent")
        elif self._looks_social(text) and not factual:
            route = "CONVERSATION"
            reason.append("simple social interaction")
        else:
            evidence = self.memory.retrieve(str(semantic.get("normalized_goal") or text), limit=8)
            top = max((float(e.get("retrieval_score", 0.0) or 0.0) for e in evidence), default=0.0)
            threshold = float(self.config.get("gate_retrieval_threshold", 0.58))
            signature = self._signature(text, semantic)
            cached = self.cache.get(signature)
            cached_route = str((cached or {}).get("route") or "") if isinstance(cached, dict) else ""
            if cached_route in {"RETRIEVE", "REASON"} and top >= threshold * 0.80:
                route = cached_route
                reason.append("known structural route cache")
            elif top >= threshold and suggested in {"RETRIEVE", "REASON", "GROWTH"}:
                route = "RETRIEVE" if suggested == "RETRIEVE" else "REASON"
                reason.append(f"memory coverage top={top:.3f}")
            elif factual:
                # A factual/task request cannot stay on a conversational path even if
                # the small LLM thinks it knows the answer.
                route = "GROWTH" if suggested == "GROWTH" or top < threshold * 0.45 else "REASON"
                reason.append(f"factual/task escalation top={top:.3f}")
            else:
                route = suggested if suggested in {"RETRIEVE", "REASON", "GROWTH"} else "REASON"
                reason.append("non-social request")

            if confidence < float(self.config.get("gate_semantic_confidence_floor", 0.55)) and route in {"CONVERSATION", "RETRIEVE"}:
                route = "REASON"
                reason.append("low semantic confidence escalated")

        return {
            "route": route,
            "semantic_suggestion": suggested,
            "semantic_confidence": confidence,
            "reason": "; ".join(reason),
            "requires_grounding": route in {"RETRIEVE", "REASON", "GROWTH"},
            "signature": self._signature(text, semantic),
            "created_at": utcnow(),
        }

    def allowed_tool_names(self, route: str) -> set[str]:
        if route == "CONVERSATION":
            return set()
        if route == "STATE":
            return {"get_internal_state"}
        if route == "RETRIEVE":
            return {"memory_lookup", "organic_reason"}
        if route == "REASON":
            return {"memory_lookup", "organic_reason", "organic_research"}
        if route == "GROWTH":
            return {"memory_lookup", "organic_reason", "organic_research"}
        return {"organic_reason"}

    def authorize_tool(self, route: str, tool_name: str) -> tuple[bool, str]:
        allowed = self.allowed_tool_names(route)
        if tool_name in allowed:
            return True, "authorized"
        return False, f"tool {tool_name} is not authorized for route {route}"

    def record_success(self, gate_decision: dict[str, Any], success: bool) -> None:
        sig = str(gate_decision.get("signature") or "")
        route = str(gate_decision.get("route") or "REASON")
        if not sig or route not in {"RETRIEVE", "REASON"}:
            return
        row = self.cache.get(sig) if isinstance(self.cache.get(sig), dict) else {}
        row = dict(row or {})
        row["route"] = route
        row["success_count"] = int(row.get("success_count", 0)) + (1 if success else 0)
        row["failure_count"] = int(row.get("failure_count", 0)) + (0 if success else 1)
        row["last_used_at"] = utcnow()
        self.cache[sig] = row
        self._save_cache()

    def status(self) -> dict[str, Any]:
        return {
            "routes": list(ROUTES),
            "cached_structural_routes": len(self.cache),
            "retrieval_threshold": float(self.config.get("gate_retrieval_threshold", 0.58)),
            "semantic_confidence_floor": float(self.config.get("gate_semantic_confidence_floor", 0.55)),
        }
