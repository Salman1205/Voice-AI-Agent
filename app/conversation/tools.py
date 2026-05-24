"""LLM tools exposed during the live conversation."""

from __future__ import annotations

from typing import Any


def conversation_tools() -> list[dict]:
    # Two tools, both with intentionally flat single-string-value schemas.
    # Earlier iterations exposed a nested `updates: dict[str, str]` shape and
    # Groq's tool-call validator would reject ~10% of completions for it. A
    # flat key/value pair is reliable across Groq, OpenAI, and Anthropic.
    return [
        {
            "type": "function",
            "function": {
                "name": "remember",
                "description": (
                    "Save a single fact you have just heard from the caller "
                    "(e.g. patient_name, preferred_slot, confirmation status). "
                    "Call this AS SOON AS the caller states the fact, so the "
                    "agent does not ask for it again."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "key": {
                            "type": "string",
                            "description": "Short snake_case identifier for the fact (e.g. patient_name).",
                        },
                        "value": {
                            "type": "string",
                            "description": "The fact as the caller stated it.",
                        },
                    },
                    "required": ["key", "value"],
                },
            },
        },
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
    if name == "remember":
        key = (arguments.get("key") or "").strip()
        value = arguments.get("value")
        if not key:
            return {"ok": False, "error": "missing key"}
        return {"ok": True, "remembered": key}
    if name == "end_call":
        return {"ok": True, "reason": arguments.get("reason", "unspecified")}
    return {"ok": False, "error": f"Unknown tool: {name}"}
