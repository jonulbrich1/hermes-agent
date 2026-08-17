import pytest

pydantic_ai = pytest.importorskip("pydantic_ai")

from pydantic_ai.models.test import TestModel

from organic_runtime.contracts import Route, RuntimeStateSnapshot
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
