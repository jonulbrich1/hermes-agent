"""Hermes middleware implementing the Semantic Interface <-> Organic gate."""

from __future__ import annotations

import json
import threading
import time

from .gate import InteractionGate, Route, allowed_tools


_GATE = InteractionGate()
_LOCK = threading.RLock()
_TURNS: dict[str, dict] = {}


def _prune_turns(
    now: float, max_age_seconds: float = 3600.0, max_entries: int = 256
) -> None:
    for key, value in list(_TURNS.items()):
        if now - float(value.get("created_at") or now) > max_age_seconds:
            _TURNS.pop(key, None)
    overflow = len(_TURNS) - max_entries
    if overflow > 0:
        oldest = sorted(
            _TURNS,
            key=lambda key: float(_TURNS[key].get("created_at") or 0.0),
        )
        for key in oldest[:overflow]:
            _TURNS.pop(key, None)


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


def _explicit_user_text(value) -> str:
    if isinstance(value, str):
        return value
    if not isinstance(value, list):
        return ""
    parts: list[str] = []
    for item in value:
        if isinstance(item, str):
            parts.append(item)
        elif isinstance(item, dict):
            text = item.get("text")
            if isinstance(text, str):
                parts.append(text)
    return "\n".join(part for part in parts if part.strip())


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


def _organic_handoff(user_text: str) -> dict:
    from .tools import _runtime_api

    result = _runtime_api("POST", "/api/message", {"text": user_text}, timeout=180.0)
    if not isinstance(result, dict) or not str(result.get("answer") or "").strip():
        raise RuntimeError("The shared Organic runtime returned no presentable result.")
    metadata = result.get("metadata") or {}
    return {
        "answer": result.get("answer"),
        "route": result.get("route"),
        "trace_id": result.get("trace_id"),
        "metadata": {
            "semantic_interface_completeness": metadata.get(
                "semantic_interface_completeness"
            ),
            "hard_blocked": metadata.get("hard_blocked"),
            "presenter_packet": metadata.get("presenter_packet"),
        },
    }


def on_llm_request(**kwargs):
    request = dict(kwargs["request"])
    turn = _turn_key(kwargs)
    user_text = _explicit_user_text(kwargs.get("user_message")) or _latest_user_text(
        request
    )
    rolling_context: dict = {}
    try:
        from .tools import _runtime_api

        runtime_state = _runtime_api("GET", "/api/state", timeout=1.0)
        candidate_context = runtime_state.get("rolling_context")
        if isinstance(candidate_context, dict):
            rolling_context = candidate_context
    except Exception:
        rolling_context = {}

    with _LOCK:
        state = _TURNS.setdefault(
            turn,
            {
                "organic_tool_used": False,
                "created_at": time.time(),
            },
        )
        _prune_turns(time.time())

        # A future memory preflight can be injected into state by an observer.
        memory_confidence = float(state.get("memory_confidence") or 0.0)
        decision = _GATE.decide(user_text, memory_confidence=memory_confidence)
        state["route"] = decision.route.value
        state["gate_reasons"] = decision.reasons
        state["requires_context_resolution"] = decision.requires_context_resolution

        allowed = allowed_tools(decision.route)
        original_tools = list(request.get("tools") or [])
        filtered = [t for t in original_tools if _tool_name(t) in allowed]
        needs_organic = decision.route != Route.CONVERSATION
        tool_already_used = bool(state.get("organic_tool_used"))
        if needs_organic and not tool_already_used and not filtered:
            # Compatibility fallback for hosts that loaded the middleware but did
            # not expose the Organic toolset. Normal Hermes operation uses the
            # active model and the typed tool schema instead.
            organic_result = _organic_handoff(user_text)
            state["organic_result"] = organic_result
            state["organic_tool_used"] = True
            state["last_organic_tool"] = "shared_runtime_handoff"
            state["last_organic_tool_at"] = time.time()
            tool_already_used = True

        request["tools"] = filtered if needs_organic and not tool_already_used else []
        request.pop("tool_choice", None)

        result_context = json.dumps(
            state.get("organic_result") or {}, ensure_ascii=False
        )

        instruction = f"""
You are the Semantic Interface Agent for Organic AI.

Authorized route: {decision.route.value}
Gate reasons: {"; ".join(decision.reasons)}
Rolling Cognition: {json.dumps(rolling_context, ensure_ascii=False)[:4000]}
Fallback Organic result: {result_context or "none"}

You are a presenter and tool operator, not the logic core.

Hard rules:
- Do not treat your pretrained factual knowledge as Organic knowledge.
- Do not treat Rolling Cognition as trusted factual memory; use it only for active task and referent context.
- Do not directly decide factual truth.
- Do not write trusted memory.
- For a non-conversation turn before an Organic tool has run, call the available Organic tool. For `organic_reason`, translate closed-world premises into its documented structure without calculating the answer yourself.
- A structural translation is an untrusted proposal. Preserve the user's equations exactly; Organic owns solving, constraint checks, reward, and pathway promotion.
- After an Organic tool result is present, present only that result. Do not solve the request again or add a new conclusion.
- A presenter_packet is the authoritative result boundary. Never recompute, rejudge, replace, or contradict it. If verified=false, do not invent an answer.
- The shared runtime owns completeness checks and any additional authorized tool loop.
- If the request depends on unresolved context, resolve the referent from conversation context or ask for clarification. Never web-search a meaningless literal query such as "why is it?".
- Present grounded Organic results naturally after the required Organic tool path has executed.
""".strip()

        request = _inject_system_instruction(request, instruction)

    return {
        "request": request,
        "source": "organic-ai",
        "reason": (
            f"Interaction Gate route={decision.route.value}; "
            f"organic_tool_used={tool_already_used}; "
            f"candidate_tools={len(filtered)}."
        ),
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
