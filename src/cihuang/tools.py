"""OpenAI function-calling schema and dispatcher for CiHuang."""

from __future__ import annotations

import json
from typing import Any

TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "cihuang_inspect",
            "description": "List every editable element (with index, tag, fill, stroke) of an SVG file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "input_path": {"type": "string", "description": "Path to the SVG file"},
                },
                "required": ["input_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cihuang_set_color",
            "description": "Set the fill or stroke color of one element, or clear it with color=null.",
            "parameters": {
                "type": "object",
                "properties": {
                    "input_path": {"type": "string"},
                    "element_index": {"type": "integer", "description": "Index from cihuang_inspect"},
                    "prop": {"type": "string", "enum": ["fill", "stroke"], "default": "fill"},
                    "color": {
                        "type": ["string", "null"],
                        "description": "CSS color such as #ff0000, or null for none",
                    },
                    "output_path": {"type": "string", "description": "Where to write the result"},
                },
                "required": ["input_path", "element_index"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cihuang_move",
            "description": "Move one element by dx/dy SVG user units.",
            "parameters": {
                "type": "object",
                "properties": {
                    "input_path": {"type": "string"},
                    "element_index": {"type": "integer"},
                    "dx": {"type": "number"},
                    "dy": {"type": "number"},
                    "output_path": {"type": "string"},
                },
                "required": ["input_path", "element_index", "dx", "dy"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cihuang_set_text",
            "description": "Replace the text content of a <text> element.",
            "parameters": {
                "type": "object",
                "properties": {
                    "input_path": {"type": "string"},
                    "element_index": {"type": "integer"},
                    "text": {"type": "string"},
                    "output_path": {"type": "string"},
                },
                "required": ["input_path", "element_index", "text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cihuang_remove",
            "description": "Delete one element from the SVG.",
            "parameters": {
                "type": "object",
                "properties": {
                    "input_path": {"type": "string"},
                    "element_index": {"type": "integer"},
                    "output_path": {"type": "string"},
                },
                "required": ["input_path", "element_index"],
            },
        },
    },
]


def list_tool_names() -> list[str]:
    return [tool["function"]["name"] for tool in TOOLS]


def dispatch(name: str, arguments: dict[str, Any] | str) -> dict:
    """Run a named tool and return its ``to_dict`` payload."""
    if isinstance(arguments, str):
        arguments = json.loads(arguments)

    from . import api

    handlers = {
        "cihuang_inspect": api.cihuang_inspect,
        "cihuang_set_color": api.cihuang_set_color,
        "cihuang_move": api.cihuang_move,
        "cihuang_set_text": api.cihuang_set_text,
        "cihuang_remove": api.cihuang_remove,
    }
    if name not in handlers:
        raise ValueError(f"unknown tool: {name}")
    return handlers[name](**arguments).to_dict()
