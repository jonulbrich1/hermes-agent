from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from organic_runtime.contracts import Route


ToolHandler = Callable[..., Any] | Callable[..., Awaitable[Any]]


@dataclass(frozen=True)
class ToolDescriptor:
    name: str
    description: str
    allowed_routes: frozenset[Route]
    semantic_safe: bool = False


class ToolRegistry:
    """Programmatic tool authorization boundary.

    A model may ask for a tool, but route authorization is checked again here.
    This prevents a Semantic Interface or downstream model from bypassing the
    Interaction Gate merely by emitting a tool call.
    """

    def __init__(self) -> None:
        self._descriptors: dict[str, ToolDescriptor] = {}
        self._handlers: dict[str, ToolHandler] = {}

    def register(self, descriptor: ToolDescriptor, handler: ToolHandler) -> None:
        if descriptor.name in self._descriptors:
            raise ValueError(f"Tool {descriptor.name!r} is already registered.")
        self._descriptors[descriptor.name] = descriptor
        self._handlers[descriptor.name] = handler
        print(
            f"[tools] registered {descriptor.name!r} for routes="
            f"{sorted(route.value for route in descriptor.allowed_routes)}"
        )

    def allowed_for_route(self, route: Route) -> list[ToolDescriptor]:
        return sorted(
            (
                descriptor
                for descriptor in self._descriptors.values()
                if route in descriptor.allowed_routes
            ),
            key=lambda item: item.name,
        )

    def semantic_safe_tools(self) -> list[ToolDescriptor]:
        return sorted(
            (d for d in self._descriptors.values() if d.semantic_safe),
            key=lambda item: item.name,
        )

    def descriptors(self) -> list[ToolDescriptor]:
        return sorted(self._descriptors.values(), key=lambda item: item.name)

    async def invoke(self, name: str, route: Route, **kwargs: Any) -> Any:
        descriptor = self._descriptors.get(name)
        if descriptor is None:
            raise KeyError(f"Unknown tool {name!r}.")
        if route not in descriptor.allowed_routes:
            raise PermissionError(
                f"Tool {name!r} is not authorized for route {route.value!r}."
            )

        print(f"[tools] invoking {name!r} under authorized route={route.value}")
        result = self._handlers[name](**kwargs)
        if inspect.isawaitable(result):
            return await result
        return result
