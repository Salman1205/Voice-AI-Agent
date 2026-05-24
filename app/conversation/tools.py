"""LLM tools exposed during the live conversation."""

from __future__ import annotations

from typing import Any


def conversation_tools() -> list[dict]:
    # Only end_call is exposed mid-call. Structured data capture is handled
    # by the post-call OutcomeRecorder over the full transcript, which is more
    # reliable than asking the live model to emit schema-validated tool calls
    # every turn.
    return [
        {
            "type": "function",
            "function": {
                "name": "end_call",
                "description": (
                    "End the call gracefully. Use when the objective is achieved, "
                    "the caller wants to hang up, or the conversation cannot progress."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "reason": {
                            "type": "string",
                            "description": "Short reason for ending the call.",
                        },
                        "farewell": {
                            "type": "string",
                            "description": "Final words to say before hanging up.",
                        },
                    },
                    "required": [],
                },
            },
        },
    ]


def dispatch_tool(name: str, arguments: dict) -> dict[str, Any]:
    if name == "end_call":
        return {"ok": True, "reason": arguments.get("reason", "unspecified")}
    return {"ok": False, "error": f"Unknown tool: {name}"}
