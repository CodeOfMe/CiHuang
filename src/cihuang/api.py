"""Unified Python API for CiHuang.

Every function returns a :class:`ToolResult` so the same entry points can back
the CLI, the GUI, or an agent tool call.  Functions never raise for expected
failures; they report them in ``error``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .__version__ import __version__
from .core import COLOR_PROPS, SvgDocument


@dataclass
class ToolResult:
    """A uniform return value: success flag, payload, error text, metadata."""

    success: bool
    data: Any = None
    error: str | None = None
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "data": self.data,
            "error": self.error,
            "metadata": self.metadata,
        }


def _meta(**extra: Any) -> dict:
    base = {"version": __version__}
    base.update(extra)
    return base


def cihuang_inspect(input_path: str | Path) -> ToolResult:
    """Read an SVG and report its viewBox plus every editable element."""
    try:
        doc = SvgDocument.load(input_path)
    except Exception as exc:  # noqa: BLE001 - surfaced as a ToolResult
        return ToolResult(False, error=f"cannot read SVG: {exc}", metadata=_meta(action="inspect"))

    summary = doc.summary()
    return ToolResult(
        True,
        data={"summary": summary, "elements": doc.element_infos()},
        metadata=_meta(action="inspect"),
    )


def cihuang_set_color(
    *,
    input_path: str | Path,
    element_index: int,
    prop: str = "fill",
    color: str | None = None,
    output_path: str | Path | None = None,
) -> ToolResult:
    """Set ``fill`` or ``stroke`` of one element; ``color=None`` means none."""
    if prop not in COLOR_PROPS:
        return ToolResult(
            False, error=f"prop must be one of {COLOR_PROPS}", metadata=_meta(action="set_color")
        )
    try:
        doc = SvgDocument.load(input_path)
        doc.set_color(element_index, prop, color)
    except IndexError as exc:
        return ToolResult(False, error=str(exc), metadata=_meta(action="set_color"))
    except Exception as exc:  # noqa: BLE001
        return ToolResult(False, error=str(exc), metadata=_meta(action="set_color"))

    out = _save(doc, input_path, output_path)
    return ToolResult(
        True,
        data={"output": str(out), "index": element_index, "prop": prop, "color": color},
        metadata=_meta(action="set_color"),
    )


def cihuang_move(
    *,
    input_path: str | Path,
    element_index: int,
    dx: float,
    dy: float,
    output_path: str | Path | None = None,
) -> ToolResult:
    """Translate one element by (dx, dy) user units."""
    try:
        doc = SvgDocument.load(input_path)
        doc.translate(element_index, dx, dy)
    except IndexError as exc:
        return ToolResult(False, error=str(exc), metadata=_meta(action="move"))
    except Exception as exc:  # noqa: BLE001
        return ToolResult(False, error=str(exc), metadata=_meta(action="move"))

    out = _save(doc, input_path, output_path)
    return ToolResult(
        True,
        data={"output": str(out), "index": element_index, "dx": dx, "dy": dy},
        metadata=_meta(action="move"),
    )


def cihuang_set_text(
    *,
    input_path: str | Path,
    element_index: int,
    text: str,
    output_path: str | Path | None = None,
) -> ToolResult:
    """Replace the content of a ``<text>`` element."""
    try:
        doc = SvgDocument.load(input_path)
        doc.set_text(element_index, text)
    except ValueError as exc:
        return ToolResult(False, error=str(exc), metadata=_meta(action="set_text"))
    except IndexError as exc:
        return ToolResult(False, error=str(exc), metadata=_meta(action="set_text"))
    except Exception as exc:  # noqa: BLE001
        return ToolResult(False, error=str(exc), metadata=_meta(action="set_text"))

    out = _save(doc, input_path, output_path)
    return ToolResult(
        True,
        data={"output": str(out), "index": element_index, "text": text},
        metadata=_meta(action="set_text"),
    )


def cihuang_remove(
    *,
    input_path: str | Path,
    element_index: int,
    output_path: str | Path | None = None,
) -> ToolResult:
    """Delete one element."""
    try:
        doc = SvgDocument.load(input_path)
        doc.delete(element_index)
    except (IndexError, ValueError) as exc:
        return ToolResult(False, error=str(exc), metadata=_meta(action="remove"))
    except Exception as exc:  # noqa: BLE001
        return ToolResult(False, error=str(exc), metadata=_meta(action="remove"))

    out = _save(doc, input_path, output_path)
    return ToolResult(True, data={"output": str(out)}, metadata=_meta(action="remove"))


def _save(doc: SvgDocument, input_path: str | Path, output_path: str | Path | None) -> Path:
    """Write next to the input with a ``-edited`` suffix unless told otherwise."""
    if output_path is None:
        src = Path(input_path)
        output_path = src.with_name(f"{src.stem}-edited{src.suffix or '.svg'}")
    return doc.save(output_path)
