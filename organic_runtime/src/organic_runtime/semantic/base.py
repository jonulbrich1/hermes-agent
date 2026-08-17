from __future__ import annotations

from typing import Protocol

from organic_runtime.contracts import IntentEnvelope, RuntimeStateSnapshot


class SemanticInterface(Protocol):
    async def analyze(
        self,
        request: str,
        state: RuntimeStateSnapshot,
    ) -> IntentEnvelope:
        """Translate raw user language into a typed intent envelope."""
        ...

    async def respond_fast(
        self,
        request: str,
        envelope: IntentEnvelope,
        state: RuntimeStateSnapshot,
    ) -> str:
        """Respond only after the programmatic gate authorizes the fast path."""
        ...
