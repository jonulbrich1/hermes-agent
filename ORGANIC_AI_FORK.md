# Organic AI Hermes Fork

This branch is a real local Hermes source fork:

- upstream: `NousResearch/hermes-agent`
- pinned base: `v2026.8.16`
- branch: `organic-ai-v2026.8.16`

The Organic integration is in-tree:

```text
plugins/organic-ai/
organic_runtime/
```

`plugins/organic-ai` is the Hermes plugin surface. It registers Organic tools,
middleware, and lifecycle hooks through Hermes' plugin API.

`organic_runtime` is the embedded Organic runtime project. The plugin adds
`organic_runtime/src` to `sys.path` when it needs the MVP engine, so source-tree
runs work without moving the Organic packages into Hermes core.

The Hermes dashboard also has a bundled Organic tab:

```text
plugins/organic-ai/dashboard/
```

It is mounted at `/organic` by Hermes' normal dashboard plugin loader. The tab
uses the same Organic runtime path as the standalone GUI and exposes engine
state, idle growth, rolling cognition, tools, MCP registration, memory, sources,
run packages, and debug export.

## Runtime Boundary

Hermes remains the agent body:

- CLI / gateway / desktop surfaces
- tool registry and toolsets
- plugin loading
- LLM request middleware
- tool execution middleware
- approvals and guardrails
- browser, web, terminal, file, MCP, scheduler infrastructure

Organic AI owns:

- Semantic Interface policy
- deterministic Interaction Gate
- Organic Executive
- Cognition / Active Weave
- bounded Organic Processor
- external processor result verification and learning feedback
- Evidence Broker
- Memory Compiler / Validator
- Living Memory
- Growth Frontier
- Rolling Cognition working context

The Semantic Interface model is not allowed to silently answer factual or
reasoning requests from pretrained knowledge. The Organic middleware performs a
programmatic shared-runtime handoff before the Hermes presenter runs on every
non-conversation route. The presenter receives no bypass tools on that pass.
Qwen may present only a validated Organic result; deterministic guards reject
new numbers, dropped grounded versions, and attempts to rejudge a verified
closed-world result.

Processor capability gaps are durable growth inputs. The processor records a
bounded structural task on its capability frontier, searches approved operator
compositions, submits every candidate to an independent verifier, and promotes
only a verified pathway. Idle time retries pending processor growth before the
Executive selects the next graph-growth task. Qwen can propose structure but
cannot generate executable operators, assign rewards, or approve a pathway.

The detailed processor POC review, implemented architecture, live acceptance
results, and remaining capability gaps are documented in
`docs/ORGANIC_PROCESSOR_REFACTOR_IMPLEMENTATION.md`.

## Data Paths

By default, source-tree runs store Organic data under:

```text
runtime/organic_home/
```

Review packages are written under:

```text
reveiw/
```

The `reveiw` spelling is preserved from the project requirement.

Only one Organic engine may own `runtime/organic_home/mvp` at a time. The
Hermes dashboard, standalone page, and Organic MCP tools all use the shared
runtime at `http://127.0.0.1:8788`. The MCP server proxies to that process; it
does not start a second idle-growth loop or write Living Memory independently.

## Quick Checks

From this `hermes_fork` directory:

```bat
VERIFY_ORGANIC_FORK.bat
```

This verifies the Organic plugin gate/frontier/middleware contract and the
embedded runtime tests.

## Setup

From this `hermes_fork` directory:

```bat
SETUP_ORGANIC_HERMES.bat
```

This creates the Hermes environment, installs MCP support for the dashboard,
ensures the embedded Organic runtime environment exists, enables the
`organic-ai` plugin, and configures the shared Organic MCP server. Qwen still
runs through Ollama as the Organic Semantic Interface. Hermes Chat uses a
separate context-capable tool-calling model (`gemma4:e2b` by default) because
Hermes requires at least a 64K context window:

```bat
ollama pull qwen3:0.6b
ollama pull gemma4:e2b
```

Set `ORGANIC_HERMES_CHAT_MODEL` before launching to use another Hermes model.
This does not change Qwen's role between the user request and the Organic Core.

Linux uses the equivalent scripts:

```bash
chmod +x ./*.sh
./SETUP_ORGANIC_HERMES.sh
./RUN_ORGANIC_HERMES_GUI.sh
```

## Running The Hermes GUI With Organic

```bat
RUN_ORGANIC_GUI.bat
```

or:

```bat
RUN_ORGANIC_HERMES_GUI.bat
```

Open:

```text
http://127.0.0.1:9119/organic
```

The normal Hermes Chat tab is directly integrated. Every launcher idempotently
enables the Organic plugin, configures MCP, starts the shared runtime before the
dashboard, and enables fail-closed Organic mode. Nontrivial Chat requests must
cross the programmatic Organic handoff; greetings remain conversational. The
MCP and native Organic tools remain available for explicit tool selection and
diagnostics. The Windows launcher also disables npm's local engine-strict check
for this source-tree run because the current Windows Node install is older than
Hermes' preferred build engine.

## Standalone Organic GUI

```bat
RUN_ORGANIC_STANDALONE_GUI.bat
```

This is kept as a troubleshooting path and opens the old Organic-only GUI on
the shared runtime port `8788`. If the Hermes runtime is already active, the
launcher opens that existing process instead of creating another engine.

## Direct Hermes Integration

Setup and launch configure these values on Windows and Linux:

```text
ORGANIC_PROJECT_ROOT=<path-to-this-hermes_fork>
ORGANIC_HOME=<path-to-this-hermes_fork>\runtime\organic_home
ORGANIC_MVP_DATA_DIR=<path-to-this-hermes_fork>/runtime/organic_home/mvp
ORGANIC_TRACE_DIR=<path-to-this-hermes_fork>/runtime/organic_home/traces
ORGANIC_REVIEW_DIR=<path-to-this-hermes_fork>/reveiw
ORGANIC_IDLE_GROWTH_ENABLED=1
ORGANIC_WEB_PROVIDER=auto
ORGANIC_RUNTIME_URL=http://127.0.0.1:8788
```

The Organic plugin toolset name is `organic_ai`.

The Organic dashboard can still re-register MCP manually. MCP calls are
forwarded to the one shared Organic runtime so Engine state, processor growth,
graph growth, rolling cognition, and Living Memory remain coherent.
