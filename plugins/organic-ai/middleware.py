"""Hermes middleware implementing the Semantic Interface <-> Organic gate."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
import threading
import time

from .gate import InteractionGate, Route, allowed_tools


_GATE = InteractionGate()
_LOCK = threading.RLock()
_TURNS: dict[str, dict] = {}


def _turn_key(kwargs) -> str:
    return str(
        kwargs.get("turn_id")
        or kwargs.get("task_id")
        or kwargs.get("api_request_id")
        or "unknown"
    )


def _tool_name(tool_def: dict) -> str | None:
    if not isinstance(tool_def, dict):
        return None
    fn = tool_def.get("function")
    if isinstance(fn, dict):
        return fn.get("name")
    return tool_def.get("name")


def _latest_user_text(request: dict) -> str:
    messages = request.get("messages")
    if isinstance(messages, list):
        for msg in reversed(messages):
            if isinstance(msg, dict) and msg.get("role") == "user":
                content = msg.get("content")
                if isinstance(content, str):
                    return content
                if isinstance(content, list):
                    parts = []
                    for p in content:
                        if isinstance(p, dict) and isinstance(p.get("text"), str):
                            parts.append(p["text"])
                    if parts:
                        return "\n".join(parts)

    # Responses-style requests may use input.
    inp = request.get("input")
    if isinstance(inp, str):
        return inp
    if isinstance(inp, list):
        for item in reversed(inp):
            if isinstance(item, dict) and item.get("role") == "user":
                c = item.get("content")
                if isinstance(c, str):
                    return c
    return ""


def _inject_system_instruction(request: dict, text: str):
    messages = request.get("messages")
    if isinstance(messages, list):
        req = dict(request)
        req["messages"] = [{"role": "system", "content": text}] + list(messages)
        return req

    # Keep Responses API compatibility conservative; use instructions when available.
    req = dict(request)
    existing = str(req.get("instructions") or "").strip()
    req["instructions"] = (text + "\n\n" + existing).strip()
    return req


def on_llm_request(**kwargs):
    request = dict(kwargs["request"])
    turn = _turn_key(kwargs)
    user_text = _latest_user_text(request)
    rolling_context: dict = {}
    if os.environ.get("ORGANIC_MVP_DATA_DIR"):
        try:
            from .tools import _mvp_system

            system = _mvp_system()
            loader = getattr(system, "load_rolling_context", None) if system is not None else None
            if callable(loader):
                rolling_context = loader(user_text)
        except Exception:
            rolling_context = {}

    with _LOCK:
        state = _TURNS.setdefault(turn, {
            "organic_tool_used": False,
            "created_at": time.time(),
        })

        # A future memory preflight can be injected into state by an observer.
        memory_confidence = float(state.get("memory_confidence") or 0.0)
        decision = _GATE.decide(user_text, memory_confidence=memory_confidence)
        state["route"] = decision.route.value
        state["gate_reasons"] = decision.reasons
        state["requires_context_resolution"] = decision.requires_context_resolution

        allowed = allowed_tools(decision.route)
        original_tools = list(request.get("tools") or [])
        filtered = [t for t in original_tools if _tool_name(t) in allowed]
        request["tools"] = filtered

        if decision.route != Route.CONVERSATION and not filtered:
            raise RuntimeError(
                "Organic mode failed closed because its authorized entry tool is unavailable."
            )

        first_organic_call_required = (
            decision.route != Route.CONVERSATION
            and not state.get("organic_tool_used")
            and bool(filtered)
        )

        if first_organic_call_required:
            request["tool_choice"] = "required"
        elif filtered:
            request["tool_choice"] = "auto"
        else:
            request.pop("tool_choice", None)

        instruction = f"""
You are the Semantic Interface Agent for Organic AI.

Authorized route: {decision.route.value}
Gate reasons: {'; '.join(decision.reasons)}
Rolling Cognition: {json.dumps(rolling_context, ensure_ascii=False)[:4000]}

You are a presenter and tool operator, not the logic core.

Hard rules:
- Do not treat your pretrained factual knowledge as Organic knowledge.
- Do not treat Rolling Cognition as trusted factual memory; use it only for active task and referent context.
- Do not directly decide factual truth.
- Do not write trusted memory.
- When an Organic tool is available, use it for factual/reasoning work.
- If the Organic result does not answer the user's actual question, call another authorized Organic tool or request refinement.
- If the request depends on unresolved context, resolve the referent from conversation context or ask for clarification. Never web-search a meaningless literal query such as "why is it?".
- Present grounded Organic results naturally after the required Organic tool path has executed.
""".strip()

        request = _inject_system_instruction(request, instruction)

    return {
        "request": request,
        "source": "organic-ai",
        "reason": f"Interaction Gate route={decision.route.value}; exposed {len(filtered)} Organic tools.",
    }


def on_tool_execution(**kwargs):
    tool_name = str(kwargs.get("tool_name") or "")
    turn = _turn_key(kwargs)

    result = kwargs["next_call"](kwargs["args"])

    if tool_name.startswith("organic_"):
        with _LOCK:
            state = _TURNS.setdefault(turn, {})
            state["organic_tool_used"] = True
            state["last_organic_tool"] = tool_name
            state["last_organic_tool_at"] = time.time()

    return result


def get_turn_ledger():
    with _LOCK:
        return json.loads(json.dumps(_TURNS))
