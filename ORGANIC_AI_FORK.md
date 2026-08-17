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
- Organic Processing Core
- Evidence Broker
- Memory Compiler / Validator
- Living Memory
- Growth Frontier
- Rolling Cognition working context

The Semantic Interface model is not allowed to silently answer factual or
reasoning requests from pretrained knowledge. The Organic middleware restricts
available tools by gate-authorized route and requires an Organic tool call before
presentation on non-conversation routes.

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

## Quick Checks

From this `hermes_fork` directory:

```bat
VERIFY_ORGANIC_FORK.bat
```

This verifies the Organic plugin gate/frontier/middleware contract and the
embedded runtime tests.

## Running The Standalone Organic GUI From The Fork

```bat
RUN_ORGANIC_GUI.bat
```

This uses the embedded runtime package, Qwen through Ollama, and the same
`runtime/organic_home` data directory as the Hermes plugin.

## Enabling In Hermes

Use Hermes' normal plugin enable/config flow and enable the `organic-ai` plugin.
At minimum set:

```text
ORGANIC_PROJECT_ROOT=<path-to-this-hermes_fork>
ORGANIC_HOME=<path-to-this-hermes_fork>\runtime\organic_home
ORGANIC_MVP_DATA_DIR=<path-to-this-hermes_fork>\runtime\organic_home\mvp
ORGANIC_TRACE_DIR=<path-to-this-hermes_fork>\runtime\organic_home\traces
ORGANIC_REVIEW_DIR=<path-to-this-hermes_fork>\reveiw
ORGANIC_IDLE_GROWTH_ENABLED=1
ORGANIC_WEB_PROVIDER=auto
```

The Organic plugin toolset name is `organic_ai`.
