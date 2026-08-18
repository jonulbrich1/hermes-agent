from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable

from .config import AppConfig
from .util import norm_space


class SemanticInterfaceError(RuntimeError):
    pass


@dataclass
class ToolCall:
    call_id: str
    name: str
    arguments: dict[str, Any]


class LlamaCppSemanticInterface:
    """Small local LLM used only as a semantic interface and tool operator.

    The model is not the logic core and is not allowed to directly write trusted
    memory. It interprets user language, invokes Organic tools, evaluates whether
    a tool result answers the request, and presents structured Organic results.
    """

    def __init__(self, config: AppConfig, logger: logging.Logger):
        self.config = config
        self.logger = logger
        self.endpoint = str(config.get("semantic_endpoint", "http://127.0.0.1:8081/v1")).rstrip("/")
        self.model = str(config.get("semantic_model", "auto"))
        self.timeout = float(config.get("semantic_timeout_seconds", 120))
        self.temperature = float(config.get("semantic_temperature", 0.15))
        self.enabled = bool(config.get("semantic_agent_enabled", True))
        self._last_error = ""
        self._resolved_model = ""

    def _request(self, path: str, payload: dict | None = None, method: str = "GET") -> Any:
        url = self.endpoint + path
        data = None
        headers = {"Content-Type": "application/json"}
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            method = "POST"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                raw = r.read().decode("utf-8", errors="replace")
            return json.loads(raw)
        except Exception as exc:
            self._last_error = str(exc)
            raise SemanticInterfaceError(f"semantic interface request failed: {exc}") from exc

    def resolve_model(self) -> str:
        if self._resolved_model:
            return self._resolved_model
        try:
            data = self._request("/models")
            items = data.get("data") if isinstance(data, dict) else None
            if items and isinstance(items, list) and isinstance(items[0], dict):
                self._resolved_model = str(items[0].get("id") or self.model)
            else:
                self._resolved_model = self.model
        except Exception:
            self._resolved_model = self.model
        return self._resolved_model

    def health(self) -> dict[str, Any]:
        if not self.enabled:
            return {"enabled": False, "ready": False, "backend": "llama.cpp", "model": self.model, "error": "disabled"}
        try:
            data = self._request("/models")
            model = self.resolve_model()
            return {"enabled": True, "ready": True, "backend": "llama.cpp-openai-tools", "model": model, "endpoint": self.endpoint}
        except Exception as exc:
            return {"enabled": True, "ready": False, "backend": "llama.cpp-openai-tools", "model": self.model, "endpoint": self.endpoint, "error": str(exc)}

    @staticmethod
    def _content(message: dict) -> str:
        content = message.get("content", "") if isinstance(message, dict) else ""
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, dict) and item.get("text"):
                    parts.append(str(item["text"]))
            return "\n".join(parts)
        return str(content or "")

    def chat(self, messages: list[dict], tools: list[dict] | None = None, max_tokens: int = 700) -> dict:
        payload: dict[str, Any] = {
            "model": self.resolve_model(),
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
            payload["parallel_tool_calls"] = False
        data = self._request("/chat/completions", payload)
        choices = data.get("choices") if isinstance(data, dict) else None
        if not choices:
            raise SemanticInterfaceError("semantic backend returned no choices")
        message = choices[0].get("message") or {}
        return message

    def analyze_user(self, text: str, state_summary: dict[str, Any]) -> dict[str, Any]:
        """Advisory semantic analysis. The Interaction Gate remains authoritative."""
        if not self.enabled:
            raise SemanticInterfaceError("semantic interface is disabled")
        system = (
            "You are the Semantic Interface for Organic AI. You are NOT the logic core and must not solve factual questions from pretrained knowledge. "
            "Interpret the user's language only. Return one compact JSON object with keys: intent, route_suggestion, normalized_goal, is_user_claim, needs_internal_state, confidence. "
            "route_suggestion must be one of CONVERSATION, STATE, RETRIEVE, REASON, GROWTH. "
            "Use CONVERSATION only for social/acknowledgment language, STATE for questions about this system's actual state, RETRIEVE for a straightforward lookup, REASON for tasks requiring processing, and GROWTH when external information is likely required. "
            "Do not include factual answers."
        )
        prompt = {"role": "user", "content": json.dumps({"user_text": text, "system_state": state_summary}, ensure_ascii=False)}
        msg = self.chat([{"role": "system", "content": system}, prompt], tools=None, max_tokens=280)
        raw = self._content(msg).strip()
        # Some chat models wrap JSON in markdown despite instructions.
        if raw.startswith("```"):
            raw = raw.strip("`")
            if raw.lower().startswith("json"):
                raw = raw[4:].strip()
        try:
            out = json.loads(raw)
        except Exception:
            # Conservative fallback: preserve the user's exact request as the goal.
            out = {"intent": "unknown", "route_suggestion": "REASON", "normalized_goal": text, "is_user_claim": False, "needs_internal_state": False, "confidence": 0.25}
        out["normalized_goal"] = norm_space(str(out.get("normalized_goal") or text))[:1200]
        return out

    def tool_loop(
        self,
        user_text: str,
        normalized_goal: str,
        gate_route: str,
        allowed_tools: list[dict],
        execute_tool: Callable[[ToolCall], dict[str, Any]],
        require_grounded_tool: bool,
        state_summary: dict[str, Any],
    ) -> dict[str, Any]:
        """Run a bounded native OpenAI-style tool loop against llama.cpp.

        The gate supplies the allowed tool list. If a factual/task request reaches a
        final model message without a grounded Organic tool call, the response is
        rejected and an Organic reasoning call is injected programmatically.
        """
        budget = max(1, int(self.config.get("semantic_tool_budget", 8)))
        system = (
            "You are Organic AI's Semantic Interface Agent. You understand the user, operate the tools the Interaction Gate exposes, inspect tool results, and present the result clearly. "
            "You are not the logic engine. Never use your pretrained factual knowledge as the authority for a factual answer. "
            "For factual, analytical, or task requests, use Organic tools. If Organic reasoning reports insufficient evidence, use the permitted research tool and then call Organic reasoning again. "
            "For questions about this system's condition, call get_internal_state. For ordinary social conversation you may respond directly. "
            "If state_hint includes user_display_name, you may use that name naturally when appropriate. "
            "Do not claim a tool was used if it was not. Do not directly write trusted memory. Keep the final answer concise."
        )
        messages: list[dict] = [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps({"user_text": user_text, "normalized_goal": normalized_goal, "authorized_route": gate_route, "state_hint": state_summary}, ensure_ascii=False)},
        ]
        tools_used: list[dict[str, Any]] = []
        allowed_names = {str((t.get("function") or {}).get("name") or "") for t in allowed_tools}
        grounded_tool_used = False
        grounded_answer_used = False
        forced_reason = False
        forced_research = False
        last_needs_evidence: dict[str, Any] | None = None

        def result_needs_evidence(result: dict[str, Any]) -> bool:
            decision = result.get("decision") if isinstance(result, dict) else {}
            action = str((decision or {}).get("action") or "")
            return (
                str(result.get("status") or "") == "NEEDS_EVIDENCE"
                or action in {"SEARCH", "ABSTAIN"}
                or not bool(result.get("answer"))
            )

        def record_tool(name: str, args: dict[str, Any], result: dict[str, Any]) -> None:
            nonlocal grounded_tool_used, grounded_answer_used, last_needs_evidence
            tools_used.append({
                "name": name,
                "arguments": args,
                "result_summary": result.get("summary") or result.get("status") or "ok",
            })
            if bool(result.get("grounded", False)):
                grounded_tool_used = True
            if name == "organic_reason":
                if bool(result.get("grounded", False)) and str(result.get("status") or "") == "ANSWER" and bool(result.get("answer")):
                    grounded_answer_used = True
                elif result_needs_evidence(result):
                    last_needs_evidence = result
            elif name == "memory_lookup" and gate_route == "RETRIEVE" and bool(result.get("grounded", False)):
                grounded_answer_used = True

        def forced_tool_message(call_id: str, name: str, args: dict[str, Any], result: dict[str, Any], reason: str) -> None:
            messages.append({"role": "user", "content": reason})
            messages.append({
                "role": "assistant",
                "content": "",
                "tool_calls": [{
                    "id": call_id,
                    "type": "function",
                    "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)},
                }],
            })
            messages.append({"role": "tool", "tool_call_id": call_id, "name": name, "content": json.dumps(result, ensure_ascii=False, default=str)})

        def force_tool(name: str, args: dict[str, Any], call_id: str, reason: str) -> dict[str, Any]:
            tc = ToolCall(call_id, name, args)
            result = execute_tool(tc)
            record_tool(name, args, result)
            forced_tool_message(call_id, name, args, result, reason)
            return result

        def force_research_then_retry(reason_result: dict[str, Any] | None) -> None:
            nonlocal forced_research
            if forced_research or "organic_research" not in allowed_names:
                return
            forced_research = True
            missing = "; ".join(str(x) for x in ((reason_result or {}).get("missing") or []) if x)[:500]
            why = missing or "Organic reasoning did not return a grounded answer."
            research_args = {"query": normalized_goal, "reason": why}
            force_tool(
                "organic_research",
                research_args,
                "gate_forced_research",
                "The Interaction Gate/Organic Executive detected missing evidence and invoked evidence acquisition before any final answer.",
            )
            force_tool(
                "organic_reason",
                {"goal": normalized_goal},
                "gate_forced_core_retry",
                "The Interaction Gate retried the Organic Processing Core after evidence acquisition.",
            )

        for _ in range(budget):
            message = self.chat(messages, tools=allowed_tools, max_tokens=700)
            messages.append(message)
            calls = message.get("tool_calls") if isinstance(message, dict) else None
            if calls:
                for raw_call in calls:
                    fn = (raw_call or {}).get("function") or {}
                    name = str(fn.get("name") or "")
                    arg_raw = fn.get("arguments") or "{}"
                    try:
                        args = json.loads(arg_raw) if isinstance(arg_raw, str) else dict(arg_raw)
                    except Exception:
                        args = {}
                    tc = ToolCall(str(raw_call.get("id") or f"call_{len(tools_used)+1}"), name, args)
                    result = execute_tool(tc)
                    record_tool(name, args, result)
                    messages.append({"role": "tool", "tool_call_id": tc.call_id, "name": name, "content": json.dumps(result, ensure_ascii=False, default=str)})
                if require_grounded_tool and last_needs_evidence and not grounded_answer_used:
                    force_research_then_retry(last_needs_evidence)
                continue

            final_text = self._content(message).strip()
            if require_grounded_tool and not grounded_answer_used and not forced_reason:
                # The model tried to bypass Organic processing. The gate injects a Core
                # call and gives the interface agent one more chance to present it.
                forced_reason = True
                result = force_tool(
                    "organic_reason",
                    {"goal": normalized_goal},
                    "gate_forced_core",
                    "The Interaction Gate rejected an ungrounded final answer and invoked the Organic Processing Core.",
                )
                if result_needs_evidence(result):
                    force_research_then_retry(result)
                continue

            if require_grounded_tool and not grounded_answer_used:
                if last_needs_evidence and not forced_research:
                    force_research_then_retry(last_needs_evidence)
                    continue
                return {
                    "text": "I do not yet have enough grounded information to answer that reliably.",
                    "tools_used": tools_used,
                    "grounded_tool_used": grounded_tool_used,
                    "grounded_answer_used": grounded_answer_used,
                    "forced_core": forced_reason,
                    "forced_research": forced_research,
                    "hard_blocked": True,
                    "messages": messages[-12:],
                }

            return {
                "text": final_text,
                "tools_used": tools_used,
                "grounded_tool_used": grounded_tool_used,
                "grounded_answer_used": grounded_answer_used,
                "forced_core": forced_reason,
                "forced_research": forced_research,
                "messages": messages[-12:],
            }

        return {
            "text": "I reached the current tool-call budget before I could produce a reliable answer." if not require_grounded_tool or grounded_answer_used else "I do not yet have enough grounded information to answer that reliably.",
            "tools_used": tools_used,
            "grounded_tool_used": grounded_tool_used,
            "grounded_answer_used": grounded_answer_used,
            "forced_core": forced_reason,
            "forced_research": forced_research,
            "budget_exhausted": True,
            "hard_blocked": bool(require_grounded_tool and not grounded_answer_used),
        }


class RuleSemanticFallback:
    """Degraded-mode presenter used only when the local 0.6B interface model is unavailable."""

    def __init__(self, config: AppConfig, logger: logging.Logger):
        self.config, self.logger = config, logger

    def health(self) -> dict[str, Any]:
        return {"enabled": True, "ready": True, "backend": "rule-fallback", "model": "none", "degraded": True}

    def analyze_user(self, text: str, state_summary: dict[str, Any]) -> dict[str, Any]:
        lower = text.lower().strip()
        if any(p in lower for p in ("how are you", "what are you doing", "are you busy", "system status", "your status")):
            route = "STATE"
        elif lower in {"hi", "hello", "hey", "thanks", "thank you", "good morning", "good evening"} or len(lower.split()) <= 2 and lower.rstrip("!.") in {"hi", "hello", "hey", "thanks"}:
            route = "CONVERSATION"
        else:
            route = "REASON"
        return {"intent": "fallback", "route_suggestion": route, "normalized_goal": norm_space(text), "is_user_claim": False, "needs_internal_state": route == "STATE", "confidence": 0.45}

    def tool_loop(self, user_text: str, normalized_goal: str, gate_route: str, allowed_tools: list[dict], execute_tool, require_grounded_tool: bool, state_summary: dict[str, Any]) -> dict[str, Any]:
        names = {x.get("function", {}).get("name") for x in allowed_tools}
        if gate_route == "CONVERSATION":
            name = str(state_summary.get("user_display_name") or "").strip()
            greeting = f"I'm here and ready, {name}. What would you like to work on?" if name else "I'm here and ready. What would you like to work on?"
            return {"text": greeting, "tools_used": [], "grounded_tool_used": False, "degraded": True}
        if gate_route == "STATE" and "get_internal_state" in names:
            r = execute_tool(ToolCall("fallback_state", "get_internal_state", {}))
            state = r.get("state", {})
            mode = state.get("mode", "available")
            name = str(state.get("user_display_name") or "").strip()
            text = f"I'm operating normally, {name}. My current mode is {mode}." if name else f"I'm operating normally. My current mode is {mode}."
            return {"text": text, "tools_used": [{"name": "get_internal_state"}], "grounded_tool_used": True, "degraded": True}
        r = execute_tool(ToolCall("fallback_core", "organic_reason", {"goal": normalized_goal}))
        if not r.get("answer") and "organic_research" in names:
            execute_tool(ToolCall("fallback_research", "organic_research", {"query": normalized_goal, "reason": "fallback semantic interface"}))
            r = execute_tool(ToolCall("fallback_core2", "organic_reason", {"goal": normalized_goal}))
        text = str(r.get("answer") or "I do not yet have enough grounded information to answer that reliably.")
        return {"text": text, "tools_used": [{"name": "organic_reason"}], "grounded_tool_used": bool(r.get("grounded")), "degraded": True}
