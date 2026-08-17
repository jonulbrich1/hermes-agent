from __future__ import annotations

import argparse
import asyncio
import json
import sys

from organic_runtime.factory import build_runtime


DEMO_REQUESTS = [
    "Hello",
    "How are you today?",
    "What is the Organic AI routing principle?",
    "Explain why the gate is deterministic",
    "Find the latest upstream changes",
]


def _print_response(response) -> None:
    print("\n--- RESULT ---")
    print(f"route: {response.route.value}")
    print(f"trace: {response.trace_id}")
    print(f"answer: {response.answer}")
    print("metadata:")
    print(json.dumps(response.metadata, indent=2, default=str))
    print("gate reasons:")
    for reason in response.gate.reasons:
        print(f"  - {reason}")
    print()


async def _run_demo() -> int:
    runtime = build_runtime()
    try:
        print("[demo] Running five requests through the same runtime instance.\n")
        for index, request in enumerate(DEMO_REQUESTS, start=1):
            print("=" * 78)
            print(f"[demo] {index}/{len(DEMO_REQUESTS)}: {request}")
            response = await runtime.handle(request)
            _print_response(response)
        return 0
    finally:
        runtime.close()


async def _run_ask(request: str) -> int:
    runtime = build_runtime()
    try:
        response = await runtime.handle(request)
        _print_response(response)
        return 0
    finally:
        runtime.close()


async def _run_interactive() -> int:
    runtime = build_runtime()
    try:
        print("Organic AI scaffold interactive mode. Type 'exit' to quit.")
        while True:
            try:
                request = input("\nYou> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                return 0
            if request.lower() in {"exit", "quit"}:
                return 0
            if not request:
                continue
            try:
                response = await runtime.handle(request)
                print(f"Organic[{response.route.value}]> {response.answer}")
            except Exception as exc:
                print(f"ERROR: {type(exc).__name__}: {exc}")
    finally:
        runtime.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Organic AI PydanticAI fork scaffold")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("demo", help="Run the offline multi-route demonstration")
    sub.add_parser("interactive", help="Run an interactive local session")
    ask = sub.add_parser("ask", help="Send one request through the runtime")
    ask.add_argument("request", help="Request text")
    from organic_runtime.gui import add_gui_parser

    add_gui_parser(sub)
    from organic_runtime.mcp_server import add_mcp_parser

    add_mcp_parser(sub)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.command == "demo":
        return asyncio.run(_run_demo())
    if args.command == "interactive":
        return asyncio.run(_run_interactive())
    if args.command == "ask":
        return asyncio.run(_run_ask(args.request))
    if args.command == "gui":
        from organic_runtime.gui import run_gui

        return run_gui(host=args.host, port=args.port, open_browser=not args.no_open)
    if args.command == "mcp":
        from organic_runtime.mcp_server import run_mcp_server

        return run_mcp_server()
    raise RuntimeError(f"Unhandled command {args.command!r}")


if __name__ == "__main__":
    sys.exit(main())
