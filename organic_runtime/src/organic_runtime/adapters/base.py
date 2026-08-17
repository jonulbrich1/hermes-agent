from __future__ import annotations

from typing import Protocol

from organic_runtime.contracts import (
    CoreRequest,
    CoreResult,
    GrowthResult,
    IntentEnvelope,
    PreflightKnowledge,
)


class MemoryAdapter(Protocol):
    async def preflight(self, envelope: IntentEnvelope) -> PreflightKnowledge:
        """Perform cheap bounded retrieval for routing evidence only."""
        ...

    async def answer_known(
        self,
        envelope: IntentEnvelope,
        preflight: PreflightKnowledge,
    ) -> str:
        """Return an established known answer after gate authorization."""
        ...


class CoreAdapter(Protocol):
    async def process(self, request: CoreRequest) -> CoreResult:
        """Run the actual Organic cognition/core pipeline."""
        ...


class GrowthAdapter(Protocol):
    async def grow(self, envelope: IntentEnvelope) -> GrowthResult:
        """Acquire/validate new evidence for a request that requires growth."""
        ...
