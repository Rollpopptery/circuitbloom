#!/usr/bin/env python3
"""
Watchdog MCP Server
Exposes hit_watchdog as a tool for Claude Code or Claude Desktop.

Install:
    pip install mcp --break-system-packages

Run:
    python3 watchdog_mcp.py /path/to/pcb_prompts.txt
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


import asyncio
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent
from prompt_watchdog import PromptWatchdog

# --- Init ---
if len(sys.argv) < 2:
    print("Usage: watchdog_mcp.py <prompt_file>", file=sys.stderr)
    sys.exit(1)

watchdog = PromptWatchdog(sys.argv[1])
app = Server("watchdog")


@app.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="hit_watchdog",
            description=(
                "Call this when you are ready to advance to the next step in the task sequence. "
                "Pass a signal of PASS, FAIL, YES, or NO if the current step requires a decision. "
                "Pass no signal to simply advance. "
                "You MUST supply a `note` describing, in your own words, what you did or "
                "considered for the step you are completing. The note is the artifact that "
                "proves the step was taken. Empty, boilerplate, or duplicate notes are rejected."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "signal": {
                        "type": "string",
                        "enum": ["PASS", "FAIL", "YES", "NO"],
                        "description": "Decision signal for branching steps. Omit if not required."
                    },
                    "note": {
                        "type": "string",
                        "description": (
                            "Required. A sentence or two in your own words about what "
                            "you did or considered for the current step. Must be specific "
                            "to this step — generic filler will be rejected."
                        )
                    }
                },
                "required": ["note"]
            }
        )
    ]


@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    if name != "hit_watchdog":
        raise ValueError(f"Unknown tool: {name}")

    signal = arguments.get("signal", None)
    note = arguments.get("note", None)
    result = watchdog.hit(signal=signal, note=note)

    if result["status"] == "rejected":
        text = f"WATCHDOG REJECT: {result['reason']}"
    elif result["status"] == "done":
        text = "WATCHDOG: Sequence complete. All steps finished."
    elif result["status"] == "abort":
        text = result["prompt"]
    else:
        text = (
            f"[Step {result['counter']} | Retries: {result['retries']}]\n\n"
            f"{result['prompt']}"
        )

    return [TextContent(type="text", text=text)]


async def main():
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())