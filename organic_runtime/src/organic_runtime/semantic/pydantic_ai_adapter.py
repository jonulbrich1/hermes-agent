from __future__ import annotations

import json
import re
import urllib.request
from typing import Any

from pydantic_ai import Agent
from pydantic_ai.exceptions import UnexpectedModelBehavior

from organic_runtime.contracts import (
    IntentEnvelope,
    RuntimeStateSnapshot,
    SemanticCompletenessReview,
)
from organic_runtime.cognition.structural import infer_structural_fields
from organic_runtime.semantic.base import (
    clarification_subject,
    enforce_objective_coverage,
    looks_self_contained_reasoning,
)

_CLASSIFIER_INSTRUCTIONS = """
You are the Semantic Interface for Organic AI.

Your job is translation and routing recommendation, not final problem solving.
Return a typed IntentEnvelope describing what the user is asking in the form
most useful to the Organic runtime.

Rules:
- Preserve the user's actual objective in normalized_request.
- Set self_contained_reasoning=true for logic, arithmetic, deduction, or puzzles
  whose complete premises are in the request. These need Organic Processor
  cycles, not factual memory or web evidence.
- For a self-contained task, propose closed_world, sufficient_premises,
  reasoning_family, reasoning_goal, and structural_constraints. Express only
  premises and relationships. Never include or calculate an answer.
- Every entities item must be an object with text (required), plus optional
  entity_type, canonical_uid, and confidence. Example:
  {"text": "pizza", "entity_type": "food", "confidence": 0.9}
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
- entities: array of objects. Each object must use text (required), with optional
  entity_type, canonical_uid, and confidence. Never use value or type in place
  of text or entity_type. Example:
  [{"text": "pizza", "entity_type": "food", "confidence": 0.9}]
- required_capabilities: array of strings
- likely_memory_domains: array of strings
- requested_output: string
- complexity: number from 0 to 1
- uncertainty: number from 0 to 1
- suggested_route: one of conversation, internal_state, known_route, organic_core, growth
- requires_current_external_info: boolean
- self_contained_reasoning: boolean. Set true only when all premises needed to
  solve the task are in the user request, such as a logic puzzle or arithmetic.
  Such tasks require reasoning but do not require Living Memory or web evidence.
- closed_world: boolean
- sufficient_premises: boolean
- reasoning_family: string or null
- reasoning_goal: string or null
- structural_constraints: array of premise objects. Do not include an answer,
  expected result, solution, reward, or truth judgment in this array.
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

_COMPLETENESS_INSTRUCTIONS = """
You are the Semantic Interface completeness checker for Organic AI.
Decide only whether the supplied result directly addresses the user's requested
objective and output. Do not decide whether facts are true, whether reasoning is
valid, or whether memory should be trusted. Those decisions belong to validators.
Set needs_tool_loop only when another authorized Organic tool pass could fill a
specific missing part. Return the typed review without chain-of-thought.
""".strip()

_PRESENTATION_INSTRUCTIONS = """
You are the Semantic Interface presenter for Organic AI.
Rewrite only the validated Organic result into a concise direct response to the
user. You may shorten, order, and paraphrase supplied facts, but you must not add
facts, numbers, versions, conclusions, or source claims that are absent from the
validated result. Answer the requested objective directly in the first sentence.
Preserve exact version and numeric tokens. When the objective asks for the latest,
current, or stable version and the result supplies that selected version, explicitly
use the user's requested qualifier. Omit incomplete date fragments. Do not discuss
the pipeline. Return plain text without chain-of-thought.
""".strip()

class PydanticAISemanticInterface:
    """PydanticAI-backed Semantic Interface.

    PydanticAI owns provider communication and typed output validation here.
    It does not own the Organic route authorization policy.
    """

    def __init__(
        self,
        model: Any,
        ollama_base_url: str | None = None,
    ) -> None:
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
        self._completeness = Agent(
            resolved_model,
            output_type=SemanticCompletenessReview,
            instructions=_COMPLETENESS_INSTRUCTIONS,
            retries=2,
        )
        self._presenter = Agent(
            resolved_model,
            instructions=_PRESENTATION_INSTRUCTIONS,
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

    async def review_completeness(
        self,
        request: str,
        envelope: IntentEnvelope,
        answer: str,
        metadata: dict,
    ) -> SemanticCompletenessReview:
        prompt = (
            f"User objective: {request}\n"
            f"Requested output: {envelope.requested_output}\n"
            f"Result: {answer}\n"
            f"Execution status: hard_blocked={bool(metadata.get('hard_blocked'))}; "
            f"validator_complete={metadata.get('semantic_completeness_complete')}\n"
            "Check coverage only."
        )
        if self._ollama_model_name and self._ollama_model_name.lower().startswith("qwen"):
            review = await asyncio_to_thread(self._direct_qwen_completeness, prompt)
        else:
            result = await self._completeness.run(prompt)
            review = result.output
        return enforce_objective_coverage(request, answer, metadata, review)

    async def present_result(
        self,
        request: str,
        envelope: IntentEnvelope,
        answer: str,
        metadata: dict,
    ) -> str:
        if metadata.get("reasoning_mode") == "self_contained":
            return answer
        prompt = (
            f"User objective: {request}\n"
            f"Requested output: {envelope.requested_output}\n"
            f"Validated Organic result: {answer}\n"
            "Present this result only."
        )
        if self._ollama_model_name and self._ollama_model_name.lower().startswith("qwen"):
            presented = await asyncio_to_thread(self._direct_qwen_presentation, prompt)
        else:
            result = await self._presenter.run(prompt)
            presented = str(result.output)
        return self._bounded_presentation(request, answer, presented, metadata)

    def _direct_qwen_presentation(self, prompt: str) -> str:
        if not self._ollama_native_url or not self._ollama_model_name:
            raise RuntimeError("Qwen result presentation requires an Ollama model and base URL.")
        payload = {
            "model": self._ollama_model_name,
            "stream": False,
            "messages": [
                {"role": "system", "content": _PRESENTATION_INSTRUCTIONS},
                {"role": "user", "content": "/no_think\n" + prompt},
            ],
        }
        request = urllib.request.Request(
            f"{self._ollama_native_url}/api/chat",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=120) as response:
            body = json.loads(response.read().decode("utf-8"))
        return _clean_qwen_text(str(((body.get("message") or {}).get("content")) or ""))

    @staticmethod
    def _bounded_presentation(
        request: str,
        original: str,
        presented: str,
        metadata: dict,
    ) -> str:
        candidate = _clean_qwen_text(presented)
        if not candidate or len(candidate) > 2400:
            return original
        candidate = re.sub(
            r"\s*\((?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|"
            r"Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|"
            r"Nov(?:ember)?|Dec(?:ember)?)\.?\)",
            "",
            candidate,
            flags=re.IGNORECASE,
        ).strip()
        if candidate and candidate[-1].isalnum():
            candidate += "."
        number_pattern = r"\b\d+(?:\.\d+){0,3}\b"
        original_numbers = set(re.findall(number_pattern, original))
        candidate_numbers = set(re.findall(number_pattern, candidate))
        if candidate_numbers - original_numbers:
            return original

        request_terms = set(re.findall(r"[a-z]+", request.lower()))
        asks_for_release_version = bool(
            {"release", "version"} & request_terms
            and {"current", "latest", "newest", "recent", "stable"}
            & request_terms
        )
        grounded_versions = {
            str(version) for version in (metadata.get("grounded_version_tokens") or [])
        }
        if asks_for_release_version and grounded_versions and not (
            candidate_numbers & grounded_versions
        ):
            return original
        return candidate

    def _direct_qwen_completeness(self, prompt: str) -> SemanticCompletenessReview:
        if not self._ollama_native_url or not self._ollama_model_name:
            raise RuntimeError("Qwen completeness review requires an Ollama model and base URL.")
        instructions = (
            _COMPLETENESS_INSTRUCTIONS
            + "\nReturn only JSON with complete (boolean), needs_tool_loop (boolean), "
            "missing (array of strings), and reason (string)."
        )
        payload = {
            "model": self._ollama_model_name,
            "stream": False,
            "messages": [
                {"role": "system", "content": instructions},
                {"role": "user", "content": "/no_think\n" + prompt},
            ],
        }
        request = urllib.request.Request(
            f"{self._ollama_native_url}/api/chat",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=120) as response:
            body = json.loads(response.read().decode("utf-8"))
        data = _extract_json_object(str(((body.get("message") or {}).get("content")) or ""))
        missing = data.get("missing")
        if isinstance(missing, str):
            missing = [missing]
        if not isinstance(missing, list):
            missing = []
        return SemanticCompletenessReview(
            complete=_coerce_bool(data.get("complete")),
            needs_tool_loop=_coerce_bool(data.get("needs_tool_loop")),
            missing=[str(item) for item in missing if str(item).strip()],
            reason=str(data.get("reason") or "Qwen semantic coverage review."),
        )

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
        data = _extract_json_object(raw)
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
        data.setdefault("self_contained_reasoning", False)
        data.setdefault("closed_world", False)
        data.setdefault("sufficient_premises", False)
        data.setdefault("reasoning_family", None)
        data.setdefault("reasoning_goal", None)
        data.setdefault("structural_constraints", [])
        data.setdefault("reasons", ["Qwen raw JSON envelope parsed by runtime."])

        for key in ("required_capabilities", "likely_memory_domains", "reasons"):
            value = data.get(key)
            if isinstance(value, str):
                data[key] = [value] if value.strip() else []
            elif not isinstance(value, list):
                data[key] = []
        if isinstance(data.get("entities"), list):
            data["entities"] = [
                _normalize_entity_data(item)
                for item in data["entities"]
                if item
            ]
            data["entities"] = [item for item in data["entities"] if item is not None]
        else:
            data["entities"] = []
        data["normalized_request"] = _clean_qwen_text(str(data.get("normalized_request") or request))
        data["original_request"] = request
        data["requested_output"] = _normalize_requested_output(str(data.get("requested_output") or "text"))
        data["intent"] = _normalize_intent(str(data.get("intent") or "general_reasoning"))
        data["complexity"] = _clamp01(data.get("complexity"), 0.5)
        data["uncertainty"] = _clamp01(data.get("uncertainty"), 0.5)
        data["requires_current_external_info"] = _coerce_bool(
            data.get("requires_current_external_info")
        )
        data["self_contained_reasoning"] = _coerce_bool(
            data.get("self_contained_reasoning")
        )
        data["closed_world"] = _coerce_bool(data.get("closed_world"))
        data["sufficient_premises"] = _coerce_bool(data.get("sufficient_premises"))
        if not isinstance(data.get("structural_constraints"), list):
            data["structural_constraints"] = []
        if data["requires_current_external_info"]:
            data["self_contained_reasoning"] = False
            data["closed_world"] = False
            data["sufficient_premises"] = False
            data["reasoning_family"] = None
            data["reasoning_goal"] = None
            data["structural_constraints"] = []

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
        data["original_request"] = request
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

        intent_key = str(data.get("intent") or "").lower()
        capabilities_text = " ".join(str(item) for item in data.get("required_capabilities") or []).lower()
        model_identified_reasoning = any(
            marker in intent_key or marker in capabilities_text
            for marker in ("logic", "puzzle", "arithmetic", "math_reason", "deduct")
        )
        if (
            (looks_self_contained_reasoning(request) or model_identified_reasoning)
            and not data.get("requires_current_external_info")
        ):
            capabilities = list(data.get("required_capabilities") or [])
            if "logical_reasoning" not in capabilities:
                capabilities.append("logical_reasoning")
            structural = infer_structural_fields(request)
            data.update(
                {
                    "original_request": request,
                    "normalized_request": " ".join(request.strip().split()),
                    "required_capabilities": capabilities,
                    "suggested_route": "organic_core",
                    "requires_current_external_info": False,
                    "self_contained_reasoning": True,
                    "closed_world": True,
                    "sufficient_premises": bool(structural),
                    "reasoning_family": None,
                    "reasoning_goal": None,
                    "structural_constraints": [],
                }
            )
            if structural:
                data.update(structural)
            return IntentEnvelope.model_validate(data)

        if clarification_subject(request):
            data.update(
                {
                    "intent": "clarification_needed",
                    "required_capabilities": [],
                    "complexity": min(float(data.get("complexity") or 0.5), 0.20),
                    "uncertainty": max(float(data.get("uncertainty") or 0.5), 0.70),
                    "suggested_route": "organic_core",
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
                    "self_contained_reasoning": False,
                    "closed_world": False,
                    "sufficient_premises": False,
                    "reasoning_family": None,
                    "reasoning_goal": None,
                    "structural_constraints": [],
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


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _extract_json_object(raw: str) -> dict[str, Any]:
    text = re.sub(r"<think>.*?</think>", "", raw, flags=re.IGNORECASE | re.DOTALL).strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE).strip()
        text = re.sub(r"\s*```$", "", text).strip()
    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if not match:
        raise ValueError(f"Qwen did not return a JSON object: {raw[:500]}")
    data = json.loads(match.group(0))
    if not isinstance(data, dict):
        raise ValueError("Qwen JSON output was not an object.")
    return data


def _normalize_entity_data(value: Any) -> dict[str, Any] | None:
    if isinstance(value, str):
        text = value.strip()
        return {"text": text, "confidence": 0.5} if text else None
    if not isinstance(value, dict):
        return None

    text = next(
        (
            value.get(key)
            for key in ("text", "value", "name", "label", "entity", "mention")
            if value.get(key) not in (None, "")
        ),
        None,
    )
    if text is None:
        return None

    normalized: dict[str, Any] = {
        "text": str(text).strip(),
        "confidence": _clamp01(value.get("confidence"), 0.5),
    }
    entity_type = value.get("entity_type") or value.get("type") or value.get("category")
    canonical_uid = value.get("canonical_uid") or value.get("uid") or value.get("id")
    if entity_type not in (None, ""):
        normalized["entity_type"] = str(entity_type)
    if canonical_uid not in (None, ""):
        normalized["canonical_uid"] = str(canonical_uid)
    return normalized if normalized["text"] else None


async def asyncio_to_thread(func, /, *args, **kwargs):
    import asyncio

    return await asyncio.to_thread(func, *args, **kwargs)
