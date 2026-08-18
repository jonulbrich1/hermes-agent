"""Organic AI Hermes plugin registration."""

from . import schemas
from . import tools
from .middleware import on_llm_execution, on_llm_request, on_tool_execution


def _on_session_start(**kwargs):
    try:
        tools._store().event("session_start", {
            "session_id": kwargs.get("session_id"),
            "task_id": kwargs.get("task_id"),
        })
    except Exception:
        pass


def _on_session_end(**kwargs):
    try:
        tools._store().event("session_end", {
            "session_id": kwargs.get("session_id"),
            "task_id": kwargs.get("task_id"),
        })
    except Exception:
        pass


def register(ctx):
    tools.configure_context(ctx)

    ctx.register_tool(
        name="organic_get_state",
        toolset="organic_ai",
        schema=schemas.ORGANIC_GET_STATE,
        handler=tools.organic_get_state,
    )
    ctx.register_tool(
        name="organic_memory_search",
        toolset="organic_ai",
        schema=schemas.ORGANIC_MEMORY_SEARCH,
        handler=tools.organic_memory_search,
    )
    ctx.register_tool(
        name="organic_reason",
        toolset="organic_ai",
        schema=schemas.ORGANIC_REASON,
        handler=tools.organic_reason,
    )
    ctx.register_tool(
        name="organic_research",
        toolset="organic_ai",
        schema=schemas.ORGANIC_RESEARCH,
        handler=tools.organic_research,
    )
    ctx.register_tool(
        name="organic_submit_evidence",
        toolset="organic_ai",
        schema=schemas.ORGANIC_SUBMIT_EVIDENCE,
        handler=tools.organic_submit_evidence,
    )
    ctx.register_tool(
        name="organic_validate_claim",
        toolset="organic_ai",
        schema=schemas.ORGANIC_VALIDATE_CLAIM,
        handler=tools.organic_validate_claim,
    )
    ctx.register_tool(
        name="organic_growth_frontier",
        toolset="organic_ai",
        schema=schemas.ORGANIC_GROWTH_FRONTIER,
        handler=tools.organic_growth_frontier,
    )
    ctx.register_tool(
        name="organic_growth_cycle",
        toolset="organic_ai",
        schema=schemas.ORGANIC_GROWTH_CYCLE,
        handler=tools.organic_growth_cycle,
    )
    ctx.register_tool(
        name="organic_export_review",
        toolset="organic_ai",
        schema=schemas.ORGANIC_EXPORT_REVIEW,
        handler=tools.organic_export_review,
    )

    # Hermes v0.20.2 middleware API.
    ctx.register_middleware("llm_request", on_llm_request)
    ctx.register_middleware("llm_execution", on_llm_execution)
    ctx.register_middleware("tool_execution", on_tool_execution)

    ctx.register_hook("on_session_start", _on_session_start)
    ctx.register_hook("on_session_end", _on_session_end)
