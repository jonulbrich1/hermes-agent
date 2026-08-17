import pytest

from organic_runtime.contracts import Route, RuntimeStateSnapshot
from organic_runtime.tooling.registry import ToolDescriptor, ToolRegistry


async def test_tool_registry_enforces_gate_route():
    tools = ToolRegistry()
    tools.register(
        ToolDescriptor(
            name="internal_state.snapshot",
            description="state",
            allowed_routes=frozenset({Route.INTERNAL_STATE}),
        ),
        lambda state: state,
    )

    state = RuntimeStateSnapshot()
    result = await tools.invoke("internal_state.snapshot", Route.INTERNAL_STATE, state=state)
    assert result.status == "healthy"

    with pytest.raises(PermissionError):
        await tools.invoke("internal_state.snapshot", Route.CONVERSATION, state=state)


def test_semantic_safe_tool_catalog_is_explicit():
    tools = ToolRegistry()
    tools.register(
        ToolDescriptor(
            name="route.catalog",
            description="read-only route catalog",
            allowed_routes=frozenset({Route.CONVERSATION}),
            semantic_safe=True,
        ),
        lambda: [],
    )
    tools.register(
        ToolDescriptor(
            name="growth.web",
            description="research",
            allowed_routes=frozenset({Route.GROWTH}),
            semantic_safe=False,
        ),
        lambda: None,
    )
    assert [d.name for d in tools.semantic_safe_tools()] == ["route.catalog"]
