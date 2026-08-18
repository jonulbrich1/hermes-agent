from __future__ import annotations

from copy import deepcopy
from threading import Lock

from organic_runtime.contracts import Route, RuntimeStateSnapshot


class InMemoryStateStore:
    def __init__(self) -> None:
        self._lock = Lock()
        self._state = RuntimeStateSnapshot()

    def snapshot(self) -> RuntimeStateSnapshot:
        with self._lock:
            return self._state.model_copy(deep=True)

    def record_success(self, route: Route) -> None:
        with self._lock:
            self._state.requests_total += 1
            counts = deepcopy(self._state.route_counts)
            counts[route.value] = counts.get(route.value, 0) + 1
            self._state.route_counts = counts
            self._state.last_error = None
            self._state.status = "healthy"

    def record_error(self, message: str) -> None:
        with self._lock:
            self._state.requests_total += 1
            self._state.last_error = message
            self._state.status = "degraded"

    def set_component_availability(
        self,
        *,
        core: bool | None = None,
        memory: bool | None = None,
        growth: bool | None = None,
    ) -> None:
        with self._lock:
            if core is not None:
                self._state.core_available = core
            if memory is not None:
                self._state.memory_available = memory
            if growth is not None:
                self._state.growth_available = growth
