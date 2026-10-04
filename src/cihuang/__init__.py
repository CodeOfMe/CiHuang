"""CiHuang (雌黄) - a PySide6 editor for SVG elements.

雌黄 is the ancient correction pigment: you brush it over a wrong character to
erase it and write the right one.  This tool does the same to an SVG -- pick a
single element, move it, repaint it, reshape it, or rewrite its text.
"""

from .__version__ import __version__
from .api import (
    ToolResult,
    cihuang_inspect,
    cihuang_move,
    cihuang_remove,
    cihuang_set_color,
    cihuang_set_text,
)
from .core import SvgDocument
from .tools import TOOLS, dispatch, list_tool_names

__all__ = [
    "__version__",
    "ToolResult",
    "SvgDocument",
    "cihuang_inspect",
    "cihuang_set_color",
    "cihuang_move",
    "cihuang_set_text",
    "cihuang_remove",
    "TOOLS",
    "dispatch",
    "list_tool_names",
]
