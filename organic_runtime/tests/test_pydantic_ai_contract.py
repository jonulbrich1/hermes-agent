import pytest

pydantic_ai = pytest.importorskip("pydantic_ai")

from pydantic_ai.models.test import TestModel

from organic_runtime.contracts import (
    IntentEnvelope,
    Route,
    RuntimeStateSnapshot,
)
from organic_runtime.semantic.pydantic_ai_adapter import PydanticAISemanticInterface


async def test_pydantic_ai_adapter_produces_intent_envelope_with_test_model():
    model = TestModel(
        custom_output_args={
            "original_request": "placeholder",
            "normalized_request": "hello",
            "intent": "simple_conversation",
            "entities": [],
            "required_capabilities": [],
            "likely_memory_domains": [],
            "requested_output": "text",
            "complexity": 0.05,
            "uncertainty": 0.05,
            "suggested_route": "conversation",
            "requires_current_external_info": False,
            "reasons": ["test"],
        }
    )
    semantic = PydanticAISemanticInterface(model=model)
    envelope = await semantic.analyze("Hello", RuntimeStateSnapshot())
    assert envelope.original_request == "Hello"
    assert envelope.suggested_route == Route.CONVERSATION


def test_intent_envelope_normalizes_model_entity_aliases():
    envelope = IntentEnvelope.model_validate(
        {
            "original_request": "I want pizza",
            "normalized_request": "Find pizza",
            "intent": "food_search",
            "entities": [{"type": "food", "value": "pizza"}],
        }
    )

    assert envelope.entities[0].text == "pizza"
    assert envelope.entities[0].entity_type == "food"
    assert envelope.entities[0].confidence == 0.5


def test_raw_qwen_envelope_normalizes_type_value_entity_shape():
    raw = """{
        "original_request": "I want pizza",
        "normalized_request": "Find pizza",
        "intent": "food_search",
        "entities": [{"type": "food", "value": "pizza"}],
        "suggested_route": "organic_core"
    }"""

    envelope = PydanticAISemanticInterface._parse_raw_envelope(raw, "I want pizza")

    assert envelope.entities[0].model_dump() == {
        "text": "pizza",
        "canonical_uid": None,
        "entity_type": "food",
        "confidence": 0.5,
    }


def test_pydantic_adapter_marks_short_preference_for_clarification():
    envelope = IntentEnvelope(
        original_request="I want pizza",
        normalized_request="I want pizza",
        intent="requesting_service",
        entities=[{"type": "food", "value": "pizza"}],
        required_capabilities=["access to pizza services"],
        uncertainty=0.8,
    )

    finalized = PydanticAISemanticInterface._finalize_envelope(envelope, "I want pizza")

    assert finalized.intent == "clarification_needed"
    assert finalized.required_capabilities == []
    assert finalized.suggested_route == Route.ORGANIC_CORE


def test_pydantic_adapter_marks_logic_puzzle_as_self_contained():
    request = (
        "Logic Puzzle: There are two ducks in front of a duck, two ducks behind a "
        "duck and a duck in the middle. How many ducks are there?"
    )
    envelope = IntentEnvelope(
        original_request=request,
        normalized_request=request,
        intent="logic_puzzle",
        required_capabilities=["logic_puzzle"],
        suggested_route=Route.ORGANIC_CORE,
    )

    finalized = PydanticAISemanticInterface._finalize_envelope(envelope, request)

    assert finalized.self_contained_reasoning is True
    assert finalized.requires_current_external_info is False
    assert "logical_reasoning" in finalized.required_capabilities


def test_self_contained_normalization_cannot_replace_current_request_with_rolling_context():
    envelope = IntentEnvelope(
        original_request="an older puzzle",
        normalized_request=(
            "Five people finished in an order determined by several prior constraints."
        ),
        intent="logic_puzzle",
        required_capabilities=["logic_puzzle"],
        self_contained_reasoning=True,
    )

    finalized = PydanticAISemanticInterface._finalize_envelope(envelope, "1+1=?")

    assert finalized.original_request == "1+1=?"
    assert finalized.normalized_request == "1+1=?"
    assert finalized.self_contained_reasoning is True
