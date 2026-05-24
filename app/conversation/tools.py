"""LLM tool definitions used during the live conversation.

The LLM uses these to (a) save structured data as it's gathered,
(b) signal the call should end. They're declared once here so both
the prompt builder and the tool-dispatcher stay in sync.
"""

from __future__ import annotations

from typing import Any


def conversation_tools() -> list[dict]:
    """JSON Schema tool definitions in OpenAI-compatible format.

    We only expose `end_call` as a tool. Mid-call structured data capture
    is delegated to the post-call OutcomeRecorder which makes a single
    cheap LLM extraction over the full transcript — more reliable than
    asking the live model to fire tool calls every turn (which led to
    schema-validation failures on Groq for `update_extracted_data`).
    """
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
    """Apply a tool call's side effects and return a result payload for the LLM."""
    if name == "update_extracted_data":
        updates = arguments.get("updates") or {}
        return {"ok": True, "stored_keys": list(updates.keys())}
    if name == "end_call":
        return {"ok": True, "reason": arguments.get("reason", "unspecified")}
    return {"ok": False, "error": f"Unknown tool: {name}"}
