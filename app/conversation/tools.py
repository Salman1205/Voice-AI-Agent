"""LLM tools exposed during the live conversation."""

from __future__ import annotations

from typing import Any


def conversation_tools() -> list[dict]:
    # Only end_call is exposed mid-call. Earlier iterations also exposed a
    # `remember(key, value)` tool to capture facts live, but Groq's Llama 3.3
    # tool-call validator was unreliable for it (the model occasionally
    # emitted the full call object as the name field, which Groq rejected as
    # an unknown tool). Structured data capture is therefore handled entirely
    # by the post-call OutcomeRecorder, which runs one extraction pass over
    # the full transcript. That path is reliable across providers.
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
