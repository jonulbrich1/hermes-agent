"""Organic AI Hermes tool handlers."""

from __future__ import annotations

from pathlib import Path
from dataclasses import replace
import json
import os
import re
import sys
import threading
import urllib.error
import urllib.request

from .runtime.store import OrganicStore
from .runtime.core_adapter import OrganicCoreAdapter
from .runtime.review import export_review
from .growth import select_frontier


_CTX = None
_MVP_SYSTEM = None
_MVP_ERROR = None
_LOCK = threading.RLock()


def _strict_organic_mode() -> bool:
    return os.environ.get("ORGANIC_HERMES_MODE", "0").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _runtime_api(
    method: str, path: str, payload: dict | None = None, timeout: float = 180.0
):
    base = os.environ.get("ORGANIC_RUNTIME_URL", "http://127.0.0.1:8788").rstrip("/")
    url = base + (path if path.startswith("/") else "/" + path)
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method=method.upper(),
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _project_root() -> Path:
    env = os.environ.get("ORGANIC_PROJECT_ROOT")
    if env:
        return Path(env).expanduser().resolve()
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "pyproject.toml").exists() and (parent / "hermes").exists():
            return parent
        if (parent / "HERMES_PIN.txt").exists() and (parent / "overlay").exists():
            return parent
    # overlay/plugins/organic-ai: parents[3] is the scaffold root.
    return here.parents[3]


def _organic_home() -> Path:
    env = os.environ.get("ORGANIC_HOME")
    if env:
        return Path(env).expanduser().resolve()
    return _project_root() / "runtime" / "organic_home"


def _ensure_runtime_path() -> None:
    runtime_src = _project_root() / "organic_runtime" / "src"
    if runtime_src.exists() and str(runtime_src) not in sys.path:
        sys.path.insert(0, str(runtime_src))


def _mvp_system():
    global _MVP_SYSTEM, _MVP_ERROR
    with _LOCK:
        if _MVP_SYSTEM is not None:
            return _MVP_SYSTEM
        _ensure_runtime_path()
        try:
            from organic_runtime.adapters.mvp import MvpOrganicSystem
            from organic_runtime.config import RuntimeSettings
        except Exception as exc:
            _MVP_ERROR = f"{type(exc).__name__}: {exc}"
            return None

        settings = RuntimeSettings.from_env()
        organic_home = _organic_home()
        settings = replace(
            settings,
            backend="mvp",
            mvp_data_dir=Path(
                os.environ.get("ORGANIC_MVP_DATA_DIR", organic_home / "mvp")
            ),
            trace_dir=Path(
                os.environ.get("ORGANIC_TRACE_DIR", organic_home / "traces")
            ),
            web_provider=os.environ
            .get("ORGANIC_WEB_PROVIDER", settings.web_provider)
            .strip()
            .lower(),
            idle_growth_enabled=os.environ
            .get("ORGANIC_IDLE_GROWTH_ENABLED", "1")
            .strip()
            .lower()
            not in {"0", "false", "no", "off"},
        )
        try:
            _MVP_SYSTEM = MvpOrganicSystem(settings)
            return _MVP_SYSTEM
        except Exception as exc:
            _MVP_ERROR = f"{type(exc).__name__}: {exc}"
            return None


def _intent_envelope(text: str, *, research: bool = False):
    _ensure_runtime_path()
    from organic_runtime.contracts import IntentEnvelope, Route

    lower = text.lower()
    needs_current = research or any(
        phrase in lower
        for phrase in (
            "latest",
            "current",
            "today",
            "right now",
            "newest",
            "search",
            "research",
        )
    )
    return IntentEnvelope(
        original_request=text,
        normalized_request=text,
        intent="external_research" if needs_current else "general_reasoning",
        suggested_route=Route.GROWTH if needs_current else Route.ORGANIC_CORE,
        requires_current_external_info=needs_current,
        required_capabilities=["research"] if needs_current else [],
        reasons=["Hermes Organic tool call delegated to the integrated MVP runtime."],
    )


def _store() -> OrganicStore:
    return OrganicStore(_organic_home() / "organic_memory.sqlite")


def configure_context(ctx):
    global _CTX
    _CTX = ctx


def _json(payload):
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _semantic_envelope(problem: str, structure) -> dict | None:
    """Convert Hermes's untrusted structural translation into runtime input."""
    if not isinstance(structure, dict):
        return None
    family = re.sub(r"[^a-z0-9]+", "_", str(structure.get("family") or "").lower()).strip("_")
    goal = re.sub(r"[^a-z0-9]+", "_", str(structure.get("goal") or "").lower()).strip("_")
    constraints = structure.get("constraints")
    required_operations = structure.get("required_operations")
    aliases = {
        "linear_constraints": "symbolic_linear_constraints",
        "linear_equations": "symbolic_linear_constraints",
        "symbolic_linear_system": "symbolic_linear_constraints",
    }
    family = aliases.get(family, family)
    goal = {
        "solve_and_evaluate": "solve_linear_target",
        "solve_linear_system_and_evaluate": "solve_linear_target",
        "evaluate_linear_target": "solve_linear_target",
        "topological_order": "linearize_order",
        "prove_entailment": "prove_existential_relation",
    }.get(goal, goal)
    if not family or not goal:
        raise ValueError("structure.family and structure.goal are required")
    if not isinstance(constraints, list) or not constraints or len(constraints) > 64:
        raise ValueError("structure.constraints must be a non-empty array")
    if not all(isinstance(item, dict) and str(item.get("kind") or "").strip() for item in constraints):
        raise ValueError("Every structural constraint must be an object with a kind")
    forbidden = {"answer", "solution", "expected_result", "reward"}
    if any(forbidden.intersection(str(key).lower() for key in item) for item in constraints):
        raise ValueError("Structural constraints must contain premises, not proposed solutions")
    if not isinstance(required_operations, list) or not required_operations:
        raise ValueError("structure.required_operations must be a non-empty array")
    operation_aliases = {
        "CREATE_LINEAR_EQUATION": "CREATE_EQUATION",
        "SOLVE_EQUATIONS": "SOLVE_LINEAR_SYSTEM",
        "EVALUATE_TARGET": "EVALUATE_EXPRESSION",
        "VALIDATE_SOLUTION": "VERIFY_SOLUTION",
        "VERIFY_CONSTRAINTS": "CHECK_CONSTRAINTS",
    }
    operations = []
    for value in required_operations[:24]:
        normalized = re.sub(r"[^A-Z0-9]+", "_", str(value).upper()).strip("_")
        normalized = operation_aliases.get(normalized, normalized)
        if normalized and normalized not in operations:
            operations.append(normalized)
    if not operations:
        raise ValueError("structure.required_operations must name at least one operation")
    return {
        "original_request": problem,
        "normalized_request": " ".join(problem.split()),
        "intent": "structural_reasoning",
        "required_capabilities": [
            family,
            *[operation.lower() for operation in operations],
        ],
        "required_operations": operations,
        "requested_output": "text",
        "complexity": 0.7,
        "uncertainty": 0.2,
        "suggested_route": "organic_core",
        "requires_current_external_info": False,
        "self_contained_reasoning": True,
        "closed_world": True,
        "sufficient_premises": True,
        "reasoning_family": family,
        "reasoning_goal": goal,
        "structural_constraints": constraints,
        "reasons": [
            "Hermes translated the current user premises into an untrusted structural task.",
            "The Organic gate, processor, and verifier retain execution authority.",
        ],
    }


def organic_get_state(args, **kwargs):
    try:
        state = _runtime_api("GET", "/api/state", timeout=5.0)
        return _json(state)
    except Exception as exc:
        if _strict_organic_mode():
            return _json({
                "status": "ORGANIC_RUNTIME_UNAVAILABLE",
                "error": f"{type(exc).__name__}: {exc}",
                "fallback_used": False,
            })
    system = _mvp_system()
    if system is not None:
        engine_state = system.engine.state()
        loader = getattr(system, "load_rolling_context", None)
        return _json({
            "status": "healthy",
            "mode": "hermes_plugin_integrated_mvp",
            "semantic_interface": {
                "role": "The configured Hermes model is interpreter and tool operator, not Organic Core.",
                "model": os.environ.get("ORGANIC_MODEL", "inherit"),
            },
            "engine": engine_state,
            "memory_counts": engine_state.get("counts") or system.db.counts(),
            "web": engine_state.get("web") or system.provider_status(),
            "rolling_context": loader("") if callable(loader) else {},
            "review_dir": str(_project_root() / "reveiw"),
            "mvp_data_dir": str(system.root),
        })

    store = _store()
    core = OrganicCoreAdapter(os.environ.get("ORGANIC_CORE_MODULE", "organic_core"))
    return _json({
        "status": "healthy",
        "mode": "scaffold_fallback",
        "core": core.status(),
        "memory_counts": store.counts(),
        "mvp_runtime_error": _MVP_ERROR,
        "review_dir": str(_project_root() / "reveiw"),
    })


def organic_memory_search(args, **kwargs):
    query = str(args.get("query") or "").strip()
    limit = int(args.get("limit") or 8)
    system = _mvp_system()
    if system is not None:
        hits = system.memory.retrieve(query, limit=limit)
        return _json({
            "query": query,
            "hits": hits,
            "count": len(hits),
            "memory": "mvp_living_memory",
        })
    hits = _store().search(query, limit=limit)
    return _json({"query": query, "hits": hits, "count": len(hits)})


def organic_reason(args, **kwargs):
    problem = str(args.get("problem") or "").strip()
    context = args.get("context") or []
    if not problem:
        return _json({"status": "INVALID_REQUEST", "error": "problem is required"})
    try:
        semantic_envelope = _semantic_envelope(problem, args.get("structure"))
        payload = {"text": problem}
        if semantic_envelope is not None:
            payload["semantic_envelope"] = semantic_envelope
        response = _runtime_api("POST", "/api/message", payload)
        return _json({
            "status": "OK",
            "answer": response.get("answer"),
            "route": response.get("route"),
            "request_id": response.get("request_id"),
            "trace_id": response.get("trace_id"),
            "gate": response.get("gate"),
            "metadata": response.get("metadata") or {},
            "context_items_supplied_by_interface": len(context)
            if isinstance(context, list)
            else 0,
            "full_runtime_pipeline_used": True,
            "fallback_used": False,
        })
    except Exception as exc:
        if _strict_organic_mode():
            raise RuntimeError(
                f"Organic runtime is unavailable; no fallback was used: {type(exc).__name__}: {exc}"
            ) from exc
    system = _mvp_system()
    if system is not None:
        from organic_runtime.contracts import CoreRequest, IntentEnvelope

        supplied = _semantic_envelope(problem, args.get("structure"))
        envelope = (
            IntentEnvelope.model_validate(supplied)
            if supplied is not None
            else _intent_envelope(problem)
        )
        preflight = system.preflight(envelope)
        result = system.process_core(
            CoreRequest(envelope=envelope, preflight=preflight)
        )
        return _json({
            "status": "OK",
            "answer": result.answer,
            "activated_memory_ids": result.activated_memory_ids,
            "active_paths": result.active_paths,
            "metadata": result.metadata,
            "context_items_supplied_by_interface": len(context)
            if isinstance(context, list)
            else 0,
            "llm_fallback_used": False,
        })

    core = OrganicCoreAdapter(os.environ.get("ORGANIC_CORE_MODULE", "organic_core"))
    result = core.reason(problem, context)
    _store().event(
        "core_reason",
        {
            "problem": problem,
            "status": result.get("status"),
            "llm_fallback_used": result.get("llm_fallback_used"),
        },
    )
    return _json(result)


def _dispatch_hermes(tool_name: str, arguments: dict):
    if _CTX is None:
        return {"error": "Hermes plugin context is unavailable."}
    dispatcher = getattr(_CTX, "dispatch_tool", None)
    if not callable(dispatcher):
        return {"error": "This Hermes runtime does not expose ctx.dispatch_tool()."}
    try:
        result = dispatcher(tool_name, arguments)
        return result
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}


def organic_research(args, **kwargs):
    query = str(args.get("query") or "").strip()
    reason = str(args.get("reason") or "").strip()
    if not query:
        return _json({"error": "query is required"})

    system = _mvp_system()
    if system is not None:
        growth = system.grow(_intent_envelope(query, research=True))
        return _json({
            "query": query,
            "reason": reason,
            "trust_state": "EXTERNAL_EVIDENCE_COMPILED_BY_MVP",
            "memory_written": bool(growth.durable_candidate_ids),
            "growth": growth.model_dump(mode="json"),
        })

    store = _store()
    store.event("research_requested", {"query": query, "reason": reason})

    # Use Hermes' mature search tool instead of implementing another browser/search stack.
    # Different Hermes profiles may expose web_search with provider-specific internals.
    result = _dispatch_hermes("web_search", {"query": query})
    return _json({
        "query": query,
        "reason": reason,
        "trust_state": "EXTERNAL_EVIDENCE_ONLY",
        "memory_written": False,
        "hermes_result": result,
    })


def organic_submit_evidence(args, **kwargs):
    claim = str(args.get("claim") or "").strip()
    quote = str(args.get("quote") or "").strip()
    source_url = str(args.get("source_url") or "").strip()
    source_title = str(args.get("source_title") or "").strip()
    provenance_family = str(args.get("provenance_family") or "").strip().upper()
    source_kind = str(args.get("source_kind") or "OTHER").strip().upper()

    if not all([claim, quote, source_url, provenance_family]):
        return _json({
            "error": "claim, quote, source_url and provenance_family are required"
        })

    eid = _store().add_evidence(
        claim=claim,
        quote=quote,
        source_url=source_url,
        source_title=source_title,
        provenance_family=provenance_family,
        source_kind=source_kind,
    )
    return _json({
        "evidence_id": eid,
        "status": "UNVERIFIED",
        "memory_written": False,
    })


def organic_validate_claim(args, **kwargs):
    claim_id = str(args.get("claim_id") or "").strip()

    # Scaffold contract: claim_id may be an evidence id. We identify the textual claim,
    # then require provenance independence. A FOREIGN_GRAPH or USER claim always needs
    # at least one different provenance family.
    store = _store()
    row = store.db.execute(
        "SELECT * FROM evidence WHERE evidence_id=?", (claim_id,)
    ).fetchone()
    if not row:
        return _json({"status": "NOT_FOUND", "claim_id": claim_id})

    claim = row["claim"]
    evidence = store.evidence_for_claim(claim)
    families = {e["provenance_family"] for e in evidence}
    kinds = {e["source_kind"] for e in evidence}

    requires_extra = bool({"FOREIGN_GRAPH", "USER"} & kinds)
    enough = len(families) >= 2 if requires_extra else len(families) >= 1

    if not enough:
        return _json({
            "status": "CORROBORATION_REQUIRED",
            "claim": claim,
            "provenance_families": sorted(families),
            "memory_written": False,
        })

    confidence = min(0.97, 0.70 + 0.10 * len(families))
    mem_id = store.promote_claim(
        claim,
        confidence,
        row["source_url"],
        row["provenance_family"],
    )
    return _json({
        "status": "GROUNDED",
        "claim": claim,
        "memory_id": mem_id,
        "confidence": confidence,
        "provenance_families": sorted(families),
        "memory_written": True,
    })


def organic_growth_frontier(args, **kwargs):
    limit = int(args.get("limit") or 10)
    system = _mvp_system()
    if system is not None:
        raw = system.memory.frontier(limit=max(limit * 4, 25))
        selected = select_frontier(
            [
                {
                    **dict(node),
                    "kind": str(node.get("kind") or "CONCEPT").upper(),
                    "degree": int(node.get("degree") or 0),
                    "task_relevance": 1.0
                    if node.get("last_growth_at") is None
                    else 0.2,
                }
                for node in raw
            ],
            limit=limit,
        )
        return _json({
            "candidates": selected,
            "raw_lexical_anchors_allowed": False,
            "source": "mvp_living_memory",
        })

    # Fallback mode reads candidate nodes from a project-local JSON file.
    candidate_file = _organic_home() / "frontier_candidates.json"
    if not candidate_file.exists():
        return _json({
            "candidates": [],
            "reason": "No promoted frontier candidate file exists yet.",
            "raw_lexical_anchors_allowed": False,
        })

    try:
        nodes = json.loads(candidate_file.read_text(encoding="utf-8"))
    except Exception as exc:
        return _json({"error": f"Could not read frontier: {exc}"})

    selected = select_frontier(list(nodes), limit=limit)
    return _json({
        "candidates": selected,
        "raw_lexical_anchors_allowed": False,
    })


def organic_growth_cycle(args, **kwargs):
    max_candidates = int(args.get("max_candidates") or 20)
    system = _mvp_system()
    if system is not None:
        frontier = system.memory.frontier(limit=max_candidates)
        system.engine.request_growth_cycles(1)
        return _json({
            "status": "GROWTH_CYCLE_REQUESTED",
            "growth_task_created": bool(frontier),
            "frontier_preview": frontier[:5],
            "engine": system.engine.state(),
            "note": "The Organic Engine will run the growth task when no foreground user task is active.",
        })

    candidate_file = _organic_home() / "frontier_candidates.json"
    if not candidate_file.exists():
        return _json({
            "status": "NO_FRONTIER",
            "growth_task_created": False,
        })

    nodes = json.loads(candidate_file.read_text(encoding="utf-8"))
    selected = select_frontier(nodes, limit=max_candidates)
    if not selected:
        return _json({
            "status": "NO_SAFE_FRONTIER",
            "growth_task_created": False,
        })

    target = selected[0]
    objective = (
        f"Resolve the knowledge gap around '{target.get('label')}' using independent "
        "evidence, then add only validated structure that connects to existing memory."
    )
    _store().event(
        "growth_task_proposed",
        {
            "target": target,
            "objective": objective,
        },
    )
    return _json({
        "status": "GROWTH_TASK_PROPOSED",
        "growth_task_created": True,
        "target": target,
        "objective": objective,
        "note": "The Semantic Interface may generate research queries, but evidence must pass Organic validation.",
    })


def organic_export_review(args, **kwargs):
    reason = str(args.get("reason") or "manual")
    system = _mvp_system()
    if system is not None:
        out = Path(system.export_review(reason))
        return _json({
            "status": "OK",
            "review_zip": str(out),
            "exists": out.exists(),
            "source": "mvp_review_exporter",
        })

    out = export_review(_project_root(), _organic_home(), reason)
    return _json({
        "status": "OK",
        "review_zip": str(out),
        "exists": out.exists(),
    })
