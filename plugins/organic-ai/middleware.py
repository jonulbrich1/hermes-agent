"""Hermes middleware implementing the Semantic Interface <-> Organic gate."""

from __future__ import annotations

import json
import threading
import time
from types import SimpleNamespace
from uuid import uuid4

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


def _decoded_tool_result(result) -> dict | None:
    if isinstance(result, dict):
        return result
    if isinstance(result, str):
        try:
            decoded = json.loads(result)
        except (TypeError, ValueError):
            return None
        return decoded if isinstance(decoded, dict) else None
    return None


def _organic_presenter_response(answer: str, api_mode: str):
    """Return the authoritative Organic packet without a second model solution."""
    usage = SimpleNamespace(
        prompt_tokens=0,
        completion_tokens=0,
        total_tokens=0,
        input_tokens=0,
        output_tokens=0,
    )
    if api_mode == "codex_responses":
        content = SimpleNamespace(type="output_text", text=answer, annotations=[])
        message = SimpleNamespace(
            type="message",
            role="assistant",
            status="completed",
            content=[content],
        )
        return SimpleNamespace(
            id="organic_presenter",
            object="response",
            status="completed",
            output=[message],
            output_text=answer,
            usage=usage,
            error=None,
            incomplete_details=None,
        )
    message = SimpleNamespace(role="assistant", content=answer, tool_calls=None)
    choice = SimpleNamespace(index=0, message=message, finish_reason="stop", logprobs=None)
    return SimpleNamespace(
        id="organic_presenter",
        object="chat.completion",
        choices=[choice],
        usage=usage,
    )


def _field(value, name: str, default=None):
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def _response_has_tool_call(response, api_mode: str) -> bool:
    """Inspect raw provider response shapes before Hermes normalization."""
    if api_mode == "codex_responses":
        return any(
            _field(item, "type") in {"function_call", "custom_tool_call"}
            for item in (_field(response, "output", []) or [])
        )
    if api_mode == "anthropic_messages":
        return any(
            _field(item, "type") == "tool_use"
            for item in (_field(response, "content", []) or [])
        )

    choices = _field(response, "choices", []) or []
    if not choices:
        return False
    message = _field(choices[0], "message")
    return bool(_field(message, "tool_calls", []) or [])


def _required_tool_arguments(tool_name: str, user_text: str) -> dict:
    if tool_name == "organic_reason":
        return {"problem": user_text}
    if tool_name == "organic_memory_search":
        return {"query": user_text}
    return {}


def _required_tool_response(tool_name: str, user_text: str, api_mode: str):
    """Build a provider-shaped call for Hermes's normal tool dispatcher."""
    call_id = f"call_organic_{uuid4().hex[:20]}"
    arguments = json.dumps(
        _required_tool_arguments(tool_name, user_text), ensure_ascii=False
    )
    usage = SimpleNamespace(
        prompt_tokens=0,
        completion_tokens=0,
        total_tokens=0,
        input_tokens=0,
        output_tokens=0,
    )

    if api_mode == "codex_responses":
        item = SimpleNamespace(
            id=f"fc_{call_id}",
            type="function_call",
            status="completed",
            call_id=call_id,
            name=tool_name,
            arguments=arguments,
        )
        return SimpleNamespace(
            id="organic_required_tool",
            object="response",
            status="completed",
            output=[item],
            output_text="",
            usage=usage,
            error=None,
            incomplete_details=None,
        )

    if api_mode == "anthropic_messages":
        block = SimpleNamespace(
            type="tool_use",
            id=call_id,
            name=tool_name,
            input=_required_tool_arguments(tool_name, user_text),
        )
        return SimpleNamespace(
            id="organic_required_tool",
            type="message",
            role="assistant",
            content=[block],
            stop_reason="tool_use",
            stop_sequence=None,
            usage=usage,
        )

    function = SimpleNamespace(name=tool_name, arguments=arguments)
    tool_call = SimpleNamespace(id=call_id, type="function", function=function)
    message = SimpleNamespace(role="assistant", content=None, tool_calls=[tool_call])
    choice = SimpleNamespace(
        index=0,
        message=message,
        finish_reason="tool_calls",
        logprobs=None,
    )
    return SimpleNamespace(
        id="organic_required_tool",
        object="chat.completion",
        choices=[choice],
        usage=usage,
    )


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
        if user_text.strip():
            state.setdefault("original_user_text", user_text)

        allowed = allowed_tools(decision.route)
        original_tools = list(request.get("tools") or [])
        filtered = [t for t in original_tools if _tool_name(t) in allowed]
        needs_organic = decision.route != Route.CONVERSATION
        tool_already_used = bool(state.get("organic_tool_used"))
        if needs_organic and not tool_already_used and not filtered:
            raise RuntimeError(
                "Organic routing requires a model-visible Organic tool, but Hermes "
                "provided none. The direct runtime handoff is disabled so the "
                "Semantic Interface cannot be bypassed."
            )

        request["tools"] = filtered if needs_organic and not tool_already_used else []
        if needs_organic and not tool_already_used:
            selected_tool = str(_tool_name(filtered[0]) or "")
            api_mode = str(kwargs.get("api_mode") or "")
            state["required_tool"] = selected_tool
            if api_mode == "anthropic_messages":
                request["tool_choice"] = {"type": "any"}
            else:
                # LM Studio accepts only the string choices none/auto/required.
                request["tool_choice"] = "required"

            # Keep a non-compliant small model from spending minutes writing a
            # direct answer that will be discarded at the Organic boundary.
            token_key = (
                "max_output_tokens" if api_mode == "codex_responses" else "max_tokens"
            )
            current_cap = request.get(token_key)
            if isinstance(current_cap, int) and not isinstance(current_cap, bool):
                request[token_key] = min(current_cap, 256)
            else:
                request[token_key] = 256
            request["_hermes_disable_streaming"] = True
        else:
            request.pop("tool_choice", None)

        result_context = json.dumps(
            state.get("organic_result") or {}, ensure_ascii=False
        )

        instruction = f"""
You are the Semantic Interface Agent for Organic AI.

Authorized route: {decision.route.value}
Gate reasons: {"; ".join(decision.reasons)}
Rolling Cognition: {json.dumps(rolling_context, ensure_ascii=False)[:4000]}
Validated Organic result: {result_context or "none"}

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
    args = kwargs["args"]

    if tool_name == "organic_reason" and isinstance(args, dict):
        with _LOCK:
            state = _TURNS.setdefault(turn, {})
            original_user_text = str(state.get("original_user_text") or "").strip()
            proposed_problem = str(args.get("problem") or "").strip()
            if original_user_text:
                args = dict(args)
                args["problem"] = original_user_text
                state["canonical_problem_enforced"] = True
                if proposed_problem and proposed_problem != original_user_text:
                    state["semantic_interface_proposed_problem"] = proposed_problem

    result = kwargs["next_call"](args)

    if tool_name.startswith("organic_"):
        with _LOCK:
            state = _TURNS.setdefault(turn, {})
            state["organic_tool_used"] = True
            state["last_organic_tool"] = tool_name
            state["last_organic_tool_at"] = time.time()
            decoded = _decoded_tool_result(result)
            if decoded is not None:
                state["organic_result"] = decoded

    return result


def on_llm_execution(**kwargs):
    """Enforce the presenter boundary before a model can replace Organic output."""
    turn = _turn_key(kwargs)
    request = kwargs["request"]
    with _LOCK:
        state = dict(_TURNS.get(turn) or {})
    organic_result = state.get("organic_result")
    answer = (
        str(organic_result.get("answer") or "").strip()
        if isinstance(organic_result, dict)
        else ""
    )
    if state.get("organic_tool_used") and answer and not request.get("tools"):
        return _organic_presenter_response(answer, str(kwargs.get("api_mode") or ""))

    response = kwargs["next_call"](request)
    api_mode = str(kwargs.get("api_mode") or "")
    required_tool = str(state.get("required_tool") or "").strip()
    if (
        required_tool
        and not state.get("organic_tool_used")
        and request.get("tools")
        and not _response_has_tool_call(response, api_mode)
    ):
        with _LOCK:
            current = _TURNS.setdefault(turn, {})
            current["semantic_interface_tool_call_recovered"] = True
            current["semantic_interface_tool_call_recovered_at"] = time.time()
        return _required_tool_response(
            required_tool,
            str(state.get("original_user_text") or ""),
            api_mode,
        )
    return response


def get_turn_ledger():
    with _LOCK:
        return json.loads(json.dumps(_TURNS))
