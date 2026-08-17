from __future__ import annotations

from organic_runtime.contracts import (
    CoreRequest,
    CoreResult,
    GrowthEvidence,
    GrowthResult,
    IntentEnvelope,
    PreflightKnowledge,
)


class KeywordMemoryAdapter:
    """Small deterministic memory stub used to prove the known-route fast path."""

    def __init__(self) -> None:
        self._known = {
            "organic ai routing principle": (
                "mem-routing-principle",
                "The Semantic Interface may recommend a route, but the programmatic "
                "Interaction Gate authorizes it. Uncertainty escalates rather than bypassing "
                "the Organic Core.",
            ),
            "bounded core": (
                "mem-bounded-core",
                "The learned core is intended to remain bounded while durable domain knowledge "
                "lives in external memory, cognition structures, procedures, indexes, and tools.",
            ),
        }

    async def preflight(self, envelope: IntentEnvelope) -> PreflightKnowledge:
        text = envelope.normalized_request.lower()
        for phrase, (memory_id, answer) in self._known.items():
            if phrase in text:
                print(f"[memory] Known route hit: {memory_id}")
                return PreflightKnowledge(
                    known_route=True,
                    confidence=0.97,
                    direct_answer=answer,
                    route_name="keyword_known_memory",
                    memory_ids=[memory_id],
                )

        print("[memory] No high-confidence known route hit")
        return PreflightKnowledge(
            known_route=False,
            confidence=0.10,
            requires_research=envelope.requires_current_external_info,
        )

    async def answer_known(
        self,
        envelope: IntentEnvelope,
        preflight: PreflightKnowledge,
    ) -> str:
        if not preflight.direct_answer:
            raise RuntimeError("Known-route handler called without a direct answer.")
        return preflight.direct_answer


class EchoCoreAdapter:
    """Instrumentation-friendly stand-in for the real Organic Processing Core."""

    def __init__(self) -> None:
        self.calls = 0

    async def process(self, request: CoreRequest) -> CoreResult:
        self.calls += 1
        print(f"[core] Organic Core mock invoked (call {self.calls})")
        growth_note = ""
        if request.growth and request.growth.evidence:
            growth_note = (
                f" Growth supplied {len(request.growth.evidence)} evidence item(s) before synthesis."
            )
        return CoreResult(
            answer=(
                "[Organic Core scaffold] The request reached the cognition/core boundary: "
                f"{request.envelope.normalized_request}.{growth_note}"
            ),
            activated_memory_ids=request.preflight.memory_ids,
            active_paths=["scaffold:core"],
            metadata={"mock_core_calls": self.calls},
        )


class MockGrowthAdapter:
    """Stand-in for controlled web/research/growth acquisition."""

    def __init__(self) -> None:
        self.calls = 0

    async def grow(self, envelope: IntentEnvelope) -> GrowthResult:
        self.calls += 1
        print(f"[growth] Growth mock invoked (call {self.calls})")
        return GrowthResult(
            evidence=[
                GrowthEvidence(
                    source_id=f"mock-growth-{self.calls}",
                    content=(
                        "Mock fresh evidence placeholder. Replace this adapter with the real "
                        "research, validation, provenance, and durable-memory growth cycle."
                    ),
                    confidence=0.60,
                    metadata={"scaffold": True},
                )
            ],
            durable_candidate_ids=[f"candidate-{self.calls}"],
            summary="Mock growth completed; evidence is ready for Organic Core synthesis.",
            metadata={"mock_growth_calls": self.calls},
        )
