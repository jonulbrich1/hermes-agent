from __future__ import annotations

from pathlib import Path
from typing import Any


class LegacyLogicSeedAdapter:
    """Preservation contract for the unavailable v0.2.1 logic seed."""

    KNOWN_PROJECT_FACTS = {
        "reported_parameter_count": 35004,
        "known_operator_inventory": ["AND", "XOR", "NOR", "ZOR", "KEL", "MIR"],
        "role": "logic/computation seed, not retrieval or executive planning",
    }

    def __init__(self, checkpoint: str | Path | None = None) -> None:
        self.checkpoint = Path(checkpoint) if checkpoint else None

    def available(self) -> bool:
        return bool(self.checkpoint and self.checkpoint.exists())

    def status(self) -> dict[str, Any]:
        return {
            "status": "AVAILABLE" if self.available() else "LEGACY_SEED_NOT_ATTACHED",
            "checkpoint": str(self.checkpoint) if self.checkpoint else None,
            "known_project_facts": dict(self.KNOWN_PROJECT_FACTS),
            "replacement_fabricated": False,
        }
