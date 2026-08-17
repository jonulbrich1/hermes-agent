from __future__ import annotations

import json
import re
import urllib.request
from typing import Any

from pydantic_ai import Agent
from pydantic_ai.exceptions import UnexpectedModelBehavior

from organic_runtime.contracts import IntentEnvelope, RuntimeStateSnapshot


_CLASSIFIER_INSTRUCTIONS = """
You are the Semantic Interface for Organic AI.

Your job is translation and routing recommendation, not final problem solving.
Return a typed IntentEnvelope describing what the user is asking in the form
most useful to the Organic runtime.

Rules:
- Preserve the user's actual objective in normalized_request.
- suggested_route is advisory only. A deterministic programmatic gate decides.
- Use conversation only for trivial social conversation that needs no memory or reasoning.
- Use internal_state only when the user is asking about this system's actual operational state.
- Use known_route only when the request appears likely to be answerable by established memory.
- Use organic_core when reasoning, synthesis, procedures, or nontrivial memory activation is needed.
- Use growth when current external information, new evidence, or unresolved research is required.
- Higher uncertainty should not be used as a reason to bypass the Organic Core.
- Do not claim that tools, memory, research, or the Organic Core have run. They have not.
""".strip()

_JSON_CLASSIFIER_INSTRUCTIONS = """
You are the Qwen Semantic Interface for Organic AI.

Return only one JSON object. Do not use markdown. Do not include chain-of-thought.
The JSON object must use these fields:
- original_request: string
- normalized_request: string
- intent: string
- entities: array
- required_capabilities: array of strings
- likely_memory_domains: array of strings
- requested_output: string
- complexity: number from 0 to 1
- uncertainty: number from 0 to 1
- suggested_route: one of conversation, internal_state, known_route, organic_core, growth
- requires_current_external_info: boolean
- reasons: array of strings

You only interpret and normalize the user request. The deterministic Organic
Interaction Gate authorizes the final route.
""".strip()

_FAST_RESPONSE_INSTRUCTIONS = """
You are the lightweight conversational interface for Organic AI.
The programmatic gate has already authorized this request as trivial conversation.
Respond naturally and briefly. Do not claim to have checked memory, tools, internal state,
the Organic Core, or external information. Those paths were not run.
""".strip()


class PydanticAISemanticInterface:
    """PydanticAI-backed Semantic Interface.

    PydanticAI owns provider communication and typed output validation here.
    It does not own the Organic route authorization policy.
    """

    def __init__(self, model: Any, ollama_base_url: str | None = None) -> None:
        self._ollama_model_name: str | None = None
        self._ollama_native_url: str | None = None
        if isinstance(model, str) and model.startswith("ollama:") and ollama_base_url:
            self._ollama_model_name = model.split(":", 1)[1]
            self._ollama_native_url = ollama_base_url.rstrip("/").removesuffix("/v1")
        resolved_model: Any = self._resolve_model(model, ollama_base_url)
        self._classifier = Agent(
            resolved_model,
            output_type=IntentEnvelope,
            instructions=_CLASSIFIER_INSTRUCTIONS,
            retries=3,
        )
        self._raw_classifier = Agent(
            resolved_model,
            instructions=_JSON_CLASSIFIER_INSTRUCTIONS,
            retries=2,
        )
        self._fast_responder = Agent(
            resolved_model,
            instructions=_FAST_RESPONSE_INSTRUCTIONS,
            retries=2,
        )

    @staticmethod
    def _resolve_model(model: Any, ollama_base_url: str | None) -> Any:
        if not isinstance(model, str):
            print(f"[semantic] Using explicit PydanticAI model object {type(model).__name__}")
            return model

        if model.startswith("ollama:") and ollama_base_url:
            from pydantic_ai.models.ollama import OllamaModel
            from pydantic_ai.providers.ollama import OllamaProvider

            model_name = model.split(":", 1)[1]
            print(
                "[semantic] Using explicit Ollama provider "
                f"model={model_name!r} base_url={ollama_base_url!r}"
            )
            return OllamaModel(
                model_name,
                provider=OllamaProvider(base_url=ollama_base_url),
            )

        print(f"[semantic] Using PydanticAI model selector {model!r}")
        return model

    async def analyze(
        self,
        request: str,
        state: RuntimeStateSnapshot,
    ) -> IntentEnvelope:
        prompt = (
            "User request:\n"
            f"{request}\n\n"
            "Current lightweight runtime state (routing context only):\n"
            f"{state.model_dump_json()}\n\n"
            "If conversation_context is present, use it only to resolve the user's "
            "current intent, referents, and requested output. It is rolling working "
            "context, not trusted factual memory."
        )
        print("[semantic] Requesting typed IntentEnvelope from PydanticAI")
        if self._ollama_model_name and self._ollama_model_name.lower().startswith("qwen"):
            envelope = await self._analyze_with_direct_qwen(request, state)
            return self._finalize_envelope(envelope, request)

        try:
            result = await self._classifier.run(prompt)
            envelope = result.output
        except UnexpectedModelBehavior as exc:
            print(f"[semantic] Typed Qwen envelope failed; requesting raw JSON envelope: {exc}")
            raw_prompt = (
                f"User request: {request}\n\n"
                f"Runtime state: {state.model_dump_json()}\n\n"
                "conversation_context, if present, is working context only and must not be "
                "treated as verified Organic knowledge.\n\n"
                "Return the required JSON object now."
            )
            raw = await self._raw_classifier.run(raw_prompt)
            envelope = self._parse_raw_envelope(str(raw.output), request)
        return self._finalize_envelope(envelope, request)

    async def respond_fast(
        self,
        request: str,
        envelope: IntentEnvelope,
        state: RuntimeStateSnapshot,
    ) -> str:
        print("[semantic] Fast conversational response authorized by gate")
        result = await self._fast_responder.run(request)
        return str(result.output)

    async def _analyze_with_direct_qwen(
        self,
        request: str,
        state: RuntimeStateSnapshot,
    ) -> IntentEnvelope:
        return await asyncio_to_thread(self._direct_qwen_envelope, request, state)

    def _direct_qwen_envelope(self, request: str, state: RuntimeStateSnapshot) -> IntentEnvelope:
        if not self._ollama_native_url or not self._ollama_model_name:
            raise RuntimeError("Qwen direct classifier requires an Ollama model and base URL.")
        payload = {
            "model": self._ollama_model_name,
            "stream": False,
            "messages": [
                {
                    "role": "system",
                    "content": _JSON_CLASSIFIER_INSTRUCTIONS,
                },
                {
                    "role": "user",
                    "content": (
                        "/no_think\n"
                        f"User request: {request}\n\n"
                        f"Runtime state: {state.model_dump_json()}\n\n"
                        "conversation_context, if present, is working context only and must not be "
                        "treated as verified Organic knowledge.\n\n"
                        "Return only the JSON envelope."
                    ),
                },
            ],
        }
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"{self._ollama_native_url}/api/chat",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=120) as response:
            body = json.loads(response.read().decode("utf-8"))
        content = str(((body.get("message") or {}).get("content")) or "")
        return self._parse_raw_envelope(content, request)

    @staticmethod
    def _parse_raw_envelope(raw: str, request: str) -> IntentEnvelope:
        text = re.sub(r"<think>.*?</think>", "", raw, flags=re.IGNORECASE | re.DOTALL).strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE).strip()
            text = re.sub(r"\s*```$", "", text).strip()
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            raise ValueError(f"Qwen did not return a JSON envelope: {raw[:500]}")
        data = json.loads(match.group(0))
        if not isinstance(data, dict):
            raise ValueError("Qwen JSON envelope was not an object.")

        data = PydanticAISemanticInterface._coerce_envelope_data(data, request)
        return IntentEnvelope.model_validate(data)

    @staticmethod
    def _coerce_envelope_data(data: dict[str, Any], request: str) -> dict[str, Any]:
        route = str(data.get("suggested_route") or "organic_core").strip()
        route_key = route.upper().replace("-", "_").replace(" ", "_")
        data["suggested_route"] = {
            "CONVERSATION": "conversation",
            "STATE": "internal_state",
            "INTERNAL_STATE": "internal_state",
            "RETRIEVE": "known_route",
            "KNOWN": "known_route",
            "KNOWN_ROUTE": "known_route",
            "REASON": "organic_core",
            "ORGANIC_CORE": "organic_core",
            "CORE": "organic_core",
            "GROWTH": "growth",
            "RESEARCH": "growth",
        }.get(route_key, route.lower())

        data.setdefault("original_request", request)
        data.setdefault("normalized_request", request.strip())
        data.setdefault("intent", "general_reasoning")
        data.setdefault("entities", [])
        data.setdefault("required_capabilities", [])
        data.setdefault("likely_memory_domains", [])
        data.setdefault("requested_output", "text")
        data.setdefault("complexity", 0.5)
        data.setdefault("uncertainty", 0.5)
        data.setdefault("requires_current_external_info", False)
        data.setdefault("reasons", ["Qwen raw JSON envelope parsed by runtime."])

        for key in ("required_capabilities", "likely_memory_domains", "reasons"):
            value = data.get(key)
            if isinstance(value, str):
                data[key] = [value] if value.strip() else []
            elif not isinstance(value, list):
                data[key] = []
        if isinstance(data.get("entities"), list):
            data["entities"] = [
                item
                if isinstance(item, dict)
                else {"text": str(item), "confidence": 0.5}
                for item in data["entities"]
                if item
            ]
        else:
            data["entities"] = []
        data["normalized_request"] = _clean_qwen_text(str(data.get("normalized_request") or request))
        data["original_request"] = request
        data["requested_output"] = _normalize_requested_output(str(data.get("requested_output") or "text"))
        data["intent"] = _normalize_intent(str(data.get("intent") or "general_reasoning"))
        data["complexity"] = _clamp01(data.get("complexity"), 0.5)
        data["uncertainty"] = _clamp01(data.get("uncertainty"), 0.5)
        data["requires_current_external_info"] = bool(data.get("requires_current_external_info"))

        route_value = str(data.get("suggested_route") or "organic_core")
        if route_value not in {"conversation", "internal_state", "known_route", "organic_core", "growth"}:
            if data["intent"] == "simple_conversation":
                route_value = "conversation"
            elif data["intent"] == "internal_state":
                route_value = "internal_state"
            elif data["requires_current_external_info"]:
                route_value = "growth"
            else:
                route_value = "organic_core"
        data["suggested_route"] = route_value
        return data

    @staticmethod
    def _finalize_envelope(envelope: IntentEnvelope, request: str) -> IntentEnvelope:
        data = envelope.model_dump(mode="json")
        data = PydanticAISemanticInterface._coerce_envelope_data(data, request)
        lower = request.lower()
        simple = lower.strip().rstrip("!.")
        if simple in {"hello", "hi", "hey", "good morning", "good afternoon", "good evening"}:
            data.update(
                {
                    "intent": "simple_conversation",
                    "required_capabilities": [],
                    "requested_output": "text",
                    "complexity": 0.05,
                    "uncertainty": 0.05,
                    "suggested_route": "conversation",
                    "requires_current_external_info": False,
                }
            )
            return IntentEnvelope.model_validate(data)

        if any(
            phrase in lower
            for phrase in (
                "how are you",
                "your status",
                "system status",
                "health status",
                "are you healthy",
                "are you okay",
            )
        ):
            capabilities = list(data.get("required_capabilities") or [])
            if "internal_state" not in capabilities:
                capabilities.append("internal_state")
            data.update(
                {
                    "intent": "internal_state",
                    "required_capabilities": capabilities,
                    "requested_output": "text",
                    "complexity": 0.10,
                    "uncertainty": 0.05,
                    "suggested_route": "internal_state",
                    "requires_current_external_info": False,
                }
            )
            return IntentEnvelope.model_validate(data)

        if any(
            phrase in lower
            for phrase in (
                "latest",
                "current version",
                "right now",
                "today",
                "look up",
                "search the web",
                "research",
                "newest",
            )
        ):
            capabilities = list(data.get("required_capabilities") or [])
            if "research" not in capabilities:
                capabilities.append("research")
            data.update(
                {
                    "intent": "external_research",
                    "required_capabilities": capabilities,
                    "suggested_route": "growth",
                    "requires_current_external_info": True,
                }
            )
            return IntentEnvelope.model_validate(data)

        coding_phrases = (
            "code me",
            "write code",
            "build me",
            "implement",
            "make an app",
            "make a widget",
            "create a widget",
        )
        if any(phrase in lower for phrase in coding_phrases):
            capabilities = list(data.get("required_capabilities") or [])
            if "coding" not in capabilities:
                capabilities.append("coding")
            data.update(
                {
                    "intent": "external_tool_task",
                    "required_capabilities": capabilities,
                    "requested_output": "code",
                    "suggested_route": "organic_core",
                }
            )
        return IntentEnvelope.model_validate(data)


def _clean_qwen_text(value: str) -> str:
    return re.sub(r"\s*/(?:no_)?think\s*", " ", value, flags=re.IGNORECASE).strip()


def _normalize_requested_output(value: str) -> str:
    value = value.strip().lower()
    if value in {"code", "json", "markdown", "text"}:
        return value
    return "text"


def _normalize_intent(value: str) -> str:
    cleaned = value.strip().lower().replace(" ", "_")
    if cleaned in {"greeting", "hello", "conversation", "simple_conversation"}:
        return "simple_conversation"
    if cleaned in {"state", "status", "internal_state"}:
        return "internal_state"
    if cleaned in {"research", "external_research", "growth"}:
        return "external_research"
    return cleaned or "general_reasoning"


def _clamp01(value: Any, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(1.0, number))


async def asyncio_to_thread(func, /, *args, **kwargs):
    import asyncio

    return await asyncio.to_thread(func, *args, **kwargs)
