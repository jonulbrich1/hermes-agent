from organic_runtime.adapters.mock import EchoCoreAdapter, KeywordMemoryAdapter, MockGrowthAdapter
from organic_runtime.contracts import Route
from organic_runtime.gate.policy import GatePolicy
from organic_runtime.runtime import OrganicRuntime
from organic_runtime.semantic.heuristic import HeuristicSemanticInterface
from organic_runtime.state.store import InMemoryStateStore
from organic_runtime.tracing.events import NullTraceRecorder
from organic_runtime.tooling.registry import ToolDescriptor, ToolRegistry


def make_runtime():
    core = EchoCoreAdapter()
    growth = MockGrowthAdapter()
    tools = ToolRegistry()
    tools.register(
        ToolDescriptor(
            name="internal_state.snapshot",
            description="Return actual test runtime state.",
            allowed_routes=frozenset({Route.INTERNAL_STATE}),
        ),
        lambda state: state,
    )
    runtime = OrganicRuntime(
        semantic=HeuristicSemanticInterface(),
        memory=KeywordMemoryAdapter(),
        core=core,
        growth=growth,
        gate=GatePolicy(),
        state=InMemoryStateStore(),
        traces=NullTraceRecorder(),
        tools=tools,
    )
    return runtime, core, growth


class RollingKeywordMemory(KeywordMemoryAdapter):
    def __init__(self):
        super().__init__()
        self.loaded_request = None
        self.recorded_turn = None

    def load_rolling_context(self, request: str):
        self.loaded_request = request
        return {
            "kind": "rolling_cognition",
            "summary": "The user is checking whether rolling context is active.",
            "recent_turns": [],
        }

    def record_conversation_turn(self, request: str, response):
        self.recorded_turn = (request, response.answer)


def make_runtime_with_rolling_context():
    memory = RollingKeywordMemory()
    core = EchoCoreAdapter()
    growth = MockGrowthAdapter()
    tools = ToolRegistry()
    tools.register(
        ToolDescriptor(
            name="internal_state.snapshot",
            description="Return actual test runtime state.",
            allowed_routes=frozenset({Route.INTERNAL_STATE}),
        ),
        lambda state: state,
    )
    runtime = OrganicRuntime(
        semantic=HeuristicSemanticInterface(),
        memory=memory,
        core=core,
        growth=growth,
        gate=GatePolicy(),
        state=InMemoryStateStore(),
        traces=NullTraceRecorder(),
        tools=tools,
    )
    return runtime, memory


async def test_conversation_does_not_invoke_core_or_growth():
    runtime, core, growth = make_runtime()
    response = await runtime.handle("Hello")
    assert response.route == Route.CONVERSATION
    assert core.calls == 0
    assert growth.calls == 0
    assert response.metadata["core_invoked"] is False


async def test_internal_state_uses_real_state_without_core():
    runtime, core, growth = make_runtime()
    first = await runtime.handle("Hello")
    assert first.route == Route.CONVERSATION

    response = await runtime.handle("How are you today?")
    assert response.route == Route.INTERNAL_STATE
    assert "completed requests=1" in response.answer
    assert core.calls == 0
    assert growth.calls == 0


async def test_known_route_skips_core():
    runtime, core, growth = make_runtime()
    response = await runtime.handle("What is the Organic AI routing principle?")
    assert response.route == Route.KNOWN_ROUTE
    assert "Interaction Gate" in response.answer
    assert core.calls == 0
    assert growth.calls == 0


async def test_general_reasoning_invokes_core_only():
    runtime, core, growth = make_runtime()
    response = await runtime.handle("Explain why the gate is deterministic")
    assert response.route == Route.ORGANIC_CORE
    assert core.calls == 1
    assert growth.calls == 0
    assert response.metadata["core_invoked"] is True


async def test_fresh_request_invokes_growth_then_core():
    runtime, core, growth = make_runtime()
    response = await runtime.handle("Find the latest upstream changes")
    assert response.route == Route.GROWTH
    assert growth.calls == 1
    assert core.calls == 1
    assert response.metadata["growth_invoked"] is True
    assert response.metadata["growth_evidence_count"] == 1


async def test_runtime_loads_and_records_rolling_context():
    runtime, memory = make_runtime_with_rolling_context()
    response = await runtime.handle("Hello")
    assert response.route == Route.CONVERSATION
    assert memory.loaded_request == "Hello"
    assert memory.recorded_turn is not None
    assert memory.recorded_turn[0] == "Hello"
