# Organic Processor V11 Integration

## Status

Organic Processor `0.11.0-rc2` is integrated into the existing Hermes/Organic
pipeline. It upgrades the processor boundary without replacing the Interaction
Gate, Cognition/Active Weave, Living Memory, graph growth, Evidence Broker,
Memory Compiler, independent verifier, MCP server, or Hermes presenter.

## Live Pipeline

```text
Hermes configured model
  -> Interaction Gate
  -> Organic Cognition / Active Weave
  -> Organic Processor 0.11.0-rc2
       -> immutable V7 seed ranking
       -> recoverable runtime learning overlay
       -> optional V9 bounded-thought action ranking
       -> deterministic executable primitive registry
  -> independent task-family verifier
  -> PresenterPacket
  -> Hermes presenter
```

The processor has no direct graph, web, Living Memory, MCP, or arbitrary tool
access. Retrieval and context paging remain Cognition responsibilities. A V9
`retrieve` or `follow_reference` decision is a typed context request, not a tool
call.

## V7 Seed

- Experiences: `930838`
- Primitive vocabulary: `40`
- SHA-256: `f35cd1d9fc1ef44721234318b3aea6ae73ba577a7ac15f7f5acd60e179cb8e81`
- Stored full pathways: none
- Stored composites: none
- Runtime mutation: prohibited

The accepted seed contributes global transition, context-conditioned
transition, and primitive-presence priors. Online verifier feedback remains in
`processor_state.json` as a separate overlay, preserving rollback and audit.

## Primitive Boundary

Seed preference cannot create an operator. Candidate paths are filtered against
the deterministic `OPERATORS` registry before execution. Missing handlers return
`WAITING_FOR_PRIMITIVE` and feed the existing capability Growth Frontier.

The V7 vocabulary contains more primitives than the current deterministic
runtime: 15 are executable, 35 have typed specs, and 25 of the 40 seed names are
still waiting for handlers. This release does not claim all 40 are executable. New handlers require
typed bounds, adversarial tests, and independent verifier coverage before they
can leave the waiting set.

## Thought Policy

The packaged V9 PyTorch policy is a small action ranker, not a semantic LLM and
not a second agent framework. Defaults:

```text
ORGANIC_THOUGHT_ENABLED=0
ORGANIC_THOUGHT_SHADOW=1
ORGANIC_THOUGHT_POLICY=v9
ORGANIC_THOUGHT_MAX_TOKENS=2048
ORGANIC_THOUGHT_MAX_CYCLES=20
ORGANIC_THOUGHT_MAX_CONTEXT_TOKENS=32000
ORGANIC_THOUGHT_MAX_RETRIEVAL_CALLS=14
ORGANIC_THOUGHT_MAX_MODEL_TOKENS=192
```

Shadow decisions are written to the audit log and processing episode metadata.
Only authorized actions are scored. The default cannot alter routing or results.

## Presenter Boundary

Verified processor and grounded-memory results produce a `PresenterPacket`.
For an unverified result, `verified=false`, `answer=null`, and the packet carries
`presenter_must_not_invent_an_answer`. Hermes and the optional dedicated semantic
presenter are instructed to preserve that packet and not recompute its result.

## RC2 Reliability Fixes

- Truth/lie navigation puzzles compile into task-local responder cases and use
  `ENUMERATE_CASES -> TEST_ENTAILMENT -> VERIFY_ALL_CASES -> STOP_IF_VERIFIED`.
  The independent verifier simulates both possible responder types before the
  Semantic Interface may present the question.
- Idle growth searches use the selected concept label rather than scheduler
  instruction text.
- Empty evidence runs receive per-concept exponential retry cooldown, preventing
  one unresolved topic from monopolizing the autonomous growth frontier.
- Generic instruction vocabulary is excluded from frontier topics, and repeated
  historical growth rows are collapsed in the GUI without deleting audit data.
- Idle compilation is capped at 80 sentences per source, and interrupted tasks
  are reconciled on restart instead of remaining permanently `ACTIVE`.

## Validation

Validated during integration:

```text
81 passed - Organic runtime, processor, Hermes plugin, and dashboard suites
6 passed  - original V11 handoff package tests
Ruff       - changed Python files passed focused checks
```

The package seed checksum was verified before and after integration. Windows
runtime acceptance and review export are recorded in the generated review ZIP.
Linux launch scripts use the same assets, optional dependency extra, environment
flags, and runtime paths; shell syntax is validated separately.
