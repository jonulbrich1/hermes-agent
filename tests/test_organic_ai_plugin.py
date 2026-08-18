#!/usr/bin/env python3
from pathlib import Path
import importlib.util
import json
import sys
import tempfile
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "organic-ai"

spec = importlib.util.spec_from_file_location(
    "organic_ai_plugin",
    PLUGIN / "__init__.py",
    submodule_search_locations=[str(PLUGIN)],
)
pkg = importlib.util.module_from_spec(spec)
sys.modules["organic_ai_plugin"] = pkg
spec.loader.exec_module(pkg)

from organic_ai_plugin.gate import InteractionGate, Route
from organic_ai_plugin.growth import GrowthFrontierGuard
from organic_ai_plugin import middleware as organic_middleware
from organic_ai_plugin.middleware import on_llm_request
from organic_ai_plugin.runtime.store import OrganicStore


def check(name, condition, details=""):
    if condition:
        print(f"[PASS] {name}")
        return True
    print(f"[FAIL] {name}: {details}")
    return False


passed = 0
total = 0


def T(name, condition, details=""):
    global passed, total
    total += 1
    if check(name, condition, details):
        passed += 1


gate = InteractionGate()
T("Greeting stays conversational", gate.decide("Hello!").route == Route.CONVERSATION)
T("Internal state route", gate.decide("How are you today?").route == Route.STATE)
T(
    "Conversation outranks learned-memory preflight",
    gate.decide("Hello!", memory_confidence=0.99).route == Route.CONVERSATION,
)
ctx = gate.decide("Why is it?")
T(
    "Context-dependent follow-up does not jump to web growth",
    ctx.route == Route.REASON and ctx.requires_context_resolution,
)

guard = GrowthFrontierGuard()
bad = [
    {"label": "may", "kind": None, "degree": 0, "mention_count": 225},
    {"label": "one", "kind": None, "degree": 0, "mention_count": 200},
    {"label": "according", "kind": None, "degree": 0, "mention_count": 197},
    {"label": "even though", "kind": None, "degree": 0, "mention_count": 14},
]
T(
    "Observed lexical-noise frontier is rejected",
    all(not guard.evaluate(x).accepted for x in bad),
)
T(
    "Connected promoted concept can become a growth target",
    guard.evaluate({"label": "tennis", "kind": "CONCEPT", "degree": 1}).accepted,
)

request = {
    "messages": [{"role": "user", "content": "What causes plate tectonics?"}],
    "tools": [
        {"type": "function", "function": {"name": "organic_reason", "parameters": {}}},
        {
            "type": "function",
            "function": {"name": "organic_memory_search", "parameters": {}},
        },
        {"type": "function", "function": {"name": "terminal", "parameters": {}}},
        {"type": "function", "function": {"name": "web_search", "parameters": {}}},
    ],
}
organic_middleware._TURNS.clear()
out = on_llm_request(request=request, turn_id="fork-test")["request"]
names = {t["function"]["name"] for t in out.get("tools", [])}
T(
    "Factual route filters direct terminal/web tools",
    "terminal" not in names and "web_search" not in names,
)
T(
    "Hermes first pass uses its configured model with Organic tool",
    names == {"organic_reason"},
)
T(
    "Organic route requires the exposed Qwen tool call",
    out.get("tool_choice") == "required"
    and out.get("max_tokens") == 256
    and out.get("_hermes_disable_streaming") is True,
)
try:
    on_llm_request(
        request={"messages": request["messages"], "tools": []},
        turn_id="fork-missing-tool-test",
    )
except RuntimeError as exc:
    missing_tool_failed_closed = "Semantic Interface cannot be bypassed" in str(exc)
else:
    missing_tool_failed_closed = False
T("Missing Organic tool fails closed instead of using a direct handoff", missing_tool_failed_closed)
T(
    "Organic reason schema teaches structural translation",
    "structure" in pkg.schemas.ORGANIC_REASON["parameters"]["properties"],
)

generic = pkg.tools._semantic_envelope(
    "If A implies B and A is true, what follows?",
    {
        "family": "causal_rule_chain",
        "goal": "derive_reachable_conclusion",
        "required_operations": ["create relation", "propagate constraint", "verify solution"],
        "constraints": [
            {"kind": "fact", "symbol": "A"},
            {"kind": "implication", "if": "A", "then": "B"},
            {"kind": "query", "symbol": "B"},
        ],
    },
)
T(
    "Organic tool accepts unknown domain-neutral structures for growth",
    generic["reasoning_family"] == "causal_rule_chain"
    and generic["required_operations"]
    == ["CREATE_RELATION", "PROPAGATE_CONSTRAINT", "VERIFY_SOLUTION"],
    json.dumps(generic),
)

role_problem = (
    "There are three people (Alex, Ben and Cody), one of whom is a knight, one a knave and "
    "one a spy. The knight always tells the truth, the knave always lies and the spy can "
    "either lie or tell the truth. Alex says: \"Cody is a knave.\" Ben says: \"Alex is a "
    "knight.\" Cody says: \"I am the spy.\" Who has each role?"
)
role_envelope = pkg.tools._semantic_envelope(role_problem, None)
T(
    "Organic tool deterministically completes an omitted truth-role structure",
    role_envelope is not None
    and role_envelope["reasoning_family"] == "truth_role_assignment"
    and role_envelope["reasoning_goal"] == "identify_role_assignment",
    json.dumps(role_envelope),
)

canonical_args = []
organic_middleware.on_llm_request(
    request={
        "messages": [{"role": "user", "content": role_problem}],
        "tools": [request["tools"][0]],
    },
    turn_id="canonical-problem-test",
)
organic_middleware.on_tool_execution(
    tool_name="organic_reason",
    turn_id="canonical-problem-test",
    args={"problem": "A lossy model paraphrase.", "structure": {"family": "test"}},
    next_call=lambda args: canonical_args.append(args) or "{}",
)
T(
    "Organic execution preserves the exact user problem across Qwen tool arguments",
    canonical_args[0]["problem"] == role_problem
    and canonical_args[0]["structure"] == {"family": "test"},
)

organic_middleware.on_tool_execution(
    tool_name="organic_reason",
    turn_id="fork-test",
    args={"problem": "What causes plate tectonics?"},
    next_call=lambda args: "validated tool result",
)
presenter = on_llm_request(request=request, turn_id="fork-test")["request"]
T("Hermes presenter pass exposes no bypass tools", presenter.get("tools") == [])
T(
    "Hermes presenter is bound to the validated packet",
    "presenter_packet is the authoritative result boundary"
    in presenter["messages"][0]["content"],
)

provider_shaped = on_llm_request(
    request={"messages": [], "tools": request["tools"]},
    user_message="What causes plate tectonics?",
    turn_id="fork-explicit-user-test",
)["request"]
T(
    "Hermes original user turn drives Organic middleware",
    {t["function"]["name"] for t in provider_shaped.get("tools", [])}
    == {"organic_reason"},
)

recovery_request = on_llm_request(
    request=request,
    turn_id="fork-recovery-test",
)["request"]
recovery_calls = []
recovered = organic_middleware.on_llm_execution(
    request=recovery_request,
    turn_id="fork-recovery-test",
    api_mode="chat_completions",
    next_call=lambda request: recovery_calls.append(request)
    or SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content="A direct model answer.", tool_calls=None),
                finish_reason="stop",
            )
        ]
    ),
)
recovered_call = recovered.choices[0].message.tool_calls[0]
T(
    "Text-only Qwen output is recovered through the Hermes tool dispatcher",
    recovered_call.function.name == "organic_reason"
    and json.loads(recovered_call.function.arguments)["problem"]
    == "What causes plate tectonics?"
    and recovered.choices[0].finish_reason == "tool_calls"
    and len(recovery_calls) == 1,
)

native_tool_response = SimpleNamespace(
    choices=[
        SimpleNamespace(
            message=SimpleNamespace(
                content=None,
                tool_calls=[
                    SimpleNamespace(
                        id="call_native",
                        type="function",
                        function=SimpleNamespace(
                            name="organic_reason", arguments='{"problem":"native"}'
                        ),
                    )
                ],
            ),
            finish_reason="tool_calls",
        )
    ]
)
native_passed = organic_middleware.on_llm_execution(
    request=provider_shaped,
    turn_id="fork-explicit-user-test",
    api_mode="chat_completions",
    next_call=lambda request: native_tool_response,
)
T(
    "Native Qwen tool calls pass through without replacement",
    native_passed is native_tool_response,
)

presenter_calls = []
organic_middleware.on_tool_execution(
    tool_name="organic_reason",
    turn_id="organic-presenter-test",
    args={"problem": role_problem},
    next_call=lambda args: json.dumps(
        {
            "status": "OK",
            "answer": "Verified Organic packet.",
            "metadata": {"presenter_packet": {"verified": True}},
        }
    ),
)
presented = organic_middleware.on_llm_execution(
    request={"messages": [], "tools": []},
    turn_id="organic-presenter-test",
    api_mode="chat_completions",
    next_call=lambda request: presenter_calls.append(request),
)
T(
    "Hermes cannot replace the authoritative Organic presenter packet",
    presented.choices[0].message.content == "Verified Organic packet."
    and presenter_calls == [],
)

with tempfile.TemporaryDirectory() as td:
    store = OrganicStore(Path(td) / "mem.sqlite")
    try:
        store.add_evidence(
            claim="Belgium has capital Brussels",
            quote="Brussels is the capital of Belgium.",
            source_url="https://example.invalid/wikidata",
            source_title="Foreign Graph",
            provenance_family="WIKIMEDIA",
            source_kind="FOREIGN_GRAPH",
        )
        evidence = store.evidence_for_claim("Belgium has capital Brussels")
        T(
            "Foreign graph evidence persists as evidence, not trusted memory",
            len(evidence) == 1 and store.counts()["memories"] == 0,
        )
    finally:
        store.close()

print()
print(f"[RESULT] {passed}/{total} Organic Hermes fork checks passed")
if passed != total:
    raise SystemExit(1)
