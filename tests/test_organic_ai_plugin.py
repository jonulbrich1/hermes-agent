#!/usr/bin/env python3
from pathlib import Path
import importlib.util
import sys
import tempfile


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
T("Observed lexical-noise frontier is rejected", all(not guard.evaluate(x).accepted for x in bad))
T(
    "Connected promoted concept can become a growth target",
    guard.evaluate({"label": "tennis", "kind": "CONCEPT", "degree": 1}).accepted,
)

request = {
    "messages": [{"role": "user", "content": "What causes plate tectonics?"}],
    "tools": [
        {"type": "function", "function": {"name": "organic_reason", "parameters": {}}},
        {"type": "function", "function": {"name": "organic_memory_search", "parameters": {}}},
        {"type": "function", "function": {"name": "terminal", "parameters": {}}},
        {"type": "function", "function": {"name": "web_search", "parameters": {}}},
    ],
}
out = on_llm_request(request=request, turn_id="fork-test")["request"]
names = {t["function"]["name"] for t in out.get("tools", [])}
T("Factual route filters direct terminal/web tools", "terminal" not in names and "web_search" not in names)
T("Factual first pass requires an Organic tool", out.get("tool_choice") == "required")
T("Main factual pass exposes only the Organic Cognition entry tool", names == {"organic_reason"})

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
