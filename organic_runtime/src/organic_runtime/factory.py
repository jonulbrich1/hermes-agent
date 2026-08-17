from __future__ import annotations

from organic_runtime.adapters.mock import EchoCoreAdapter, KeywordMemoryAdapter, MockGrowthAdapter
from organic_runtime.adapters.mvp import (
    MvpCoreAdapter,
    MvpGrowthAdapter,
    MvpMemoryAdapter,
    MvpOrganicSystem,
)
from organic_runtime.config import RuntimeSettings
from organic_runtime.contracts import Route
from organic_runtime.gate.policy import GatePolicy, GateThresholds
from organic_runtime.planning.resource_planner import CognitiveResourcePlanner
from organic_runtime.runtime import OrganicRuntime
from organic_runtime.semantic.heuristic import HeuristicSemanticInterface
from organic_runtime.state.store import InMemoryStateStore
from organic_runtime.tooling.registry import ToolDescriptor, ToolRegistry
from organic_runtime.tracing.events import JsonlTraceRecorder


def build_runtime(settings: RuntimeSettings | None = None) -> OrganicRuntime:
    settings = settings or RuntimeSettings.from_env()
    print(
        f"[factory] semantic_mode={settings.semantic_mode!r} "
        f"backend={settings.backend!r} model={settings.model!r} "
        f"trace_dir={str(settings.trace_dir)!r}"
    )

    if settings.semantic_mode == "heuristic":
        semantic = HeuristicSemanticInterface()
    elif settings.semantic_mode == "pydantic":
        from organic_runtime.semantic.pydantic_ai_adapter import PydanticAISemanticInterface

        semantic = PydanticAISemanticInterface(
            model=settings.model,
            ollama_base_url=settings.ollama_base_url,
        )
    else:
        raise ValueError(
            "ORGANIC_SEMANTIC_MODE must be 'heuristic' or 'pydantic'; "
            f"got {settings.semantic_mode!r}."
        )

    thresholds = GateThresholds(
        fast_complexity_max=settings.fast_complexity_max,
        fast_uncertainty_max=settings.fast_uncertainty_max,
        known_confidence_min=settings.known_confidence_min,
        escalate_uncertainty=settings.escalate_uncertainty,
    )

    tools = ToolRegistry()
    tools.register(
        ToolDescriptor(
            name="internal_state.snapshot",
            description="Read the actual lightweight Organic runtime state snapshot.",
            allowed_routes=frozenset({Route.INTERNAL_STATE}),
            semantic_safe=False,
        ),
        lambda state: state,
    )

    if settings.backend == "mvp":
        system = MvpOrganicSystem(settings)
        memory = MvpMemoryAdapter(system)
        core = MvpCoreAdapter(system)
        growth = MvpGrowthAdapter(system)
    elif settings.backend == "mock":
        memory = KeywordMemoryAdapter()
        core = EchoCoreAdapter()
        growth = MockGrowthAdapter()
    else:
        raise ValueError("ORGANIC_BACKEND must be 'mvp' or 'mock'; got " f"{settings.backend!r}.")

    outcome_recorder = (
        system.record_resource_plan_outcome if settings.backend == "mvp" else None
    )
    return OrganicRuntime(
        semantic=semantic,
        memory=memory,
        core=core,
        growth=growth,
        gate=GatePolicy(thresholds),
        state=InMemoryStateStore(),
        traces=JsonlTraceRecorder(settings.trace_dir),
        tools=tools,
        planner=CognitiveResourcePlanner(
            max_processor_cycles=settings.processor_max_cycles,
            outcome_recorder=outcome_recorder,
        ),
    )
