from organic_runtime.contracts import Route, RuntimeStateSnapshot
from organic_runtime.semantic.heuristic import HeuristicSemanticInterface


async def test_heuristic_semantic_outputs_typed_envelope():
    semantic = HeuristicSemanticInterface()
    envelope = await semantic.analyze("Hello", RuntimeStateSnapshot())
    assert envelope.intent == "simple_conversation"
    assert envelope.suggested_route == Route.CONVERSATION
    assert envelope.original_request == "Hello"


async def test_state_request_is_not_plain_conversation():
    semantic = HeuristicSemanticInterface()
    envelope = await semantic.analyze("How are you today?", RuntimeStateSnapshot())
    assert envelope.intent == "internal_state"
    assert envelope.suggested_route == Route.INTERNAL_STATE
