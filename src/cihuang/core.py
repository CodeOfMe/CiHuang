"""SVG document model for CiHuang.

This module deliberately has no Qt dependency so that the editing logic can be
unit-tested and used head-less.  The GUI layers Qt on top of it.

The model keeps the parsed element tree and edits it in place.  Elements that a
user may select ("drawables") are leaf shapes plus groups; container/metadata
subtrees such as ``<defs>`` are skipped so that gradients and markers never show
up as selectable units.

Coordinates are SVG user units throughout.  Moving an element is done by
prepending a ``translate(...)`` to its own ``transform`` list, which is exactly
what an SVG renderer would apply, so the result stays valid SVG.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

SVG_NS = "http://www.w3.org/2000/svg"
XLINK_NS = "http://www.w3.org/1999/xlink"

ET.register_namespace("", SVG_NS)
ET.register_namespace("xlink", XLINK_NS)

# Leaf elements the user can pick and edit.
_SHAPE_TAGS = {
    "path",
    "rect",
    "circle",
    "ellipse",
    "line",
    "polyline",
    "polygon",
    "text",
    "image",
    "use",
}
# Groups are selectable as a whole but are not leaves.
_GROUP_TAGS = {"g", "a", "switch"}
# Subtrees that must never be exposed for editing.
_SKIP_TAGS = {
    "defs",
    "clipPath",
    "mask",
    "pattern",
    "marker",
    "symbol",
    "style",
    "title",
    "desc",
    "metadata",
    "script",
    "filter",
    "linearGradient",
    "radialGradient",
    "font",
}

_STYLE_RE = re.compile(r"\s*([^:;]+)\s*:\s*([^;]+)\s*")

# Presentation properties the GUI edits by name.  ``style`` always wins over the
# matching presentation attribute in SVG, so set_property keeps them in sync.
COLOR_PROPS = ("fill", "stroke")


def local_name(tag: object) -> str:
    """Return the local part of a possibly namespaced ElementTree tag."""
    if not isinstance(tag, str):
        return ""
    if tag.startswith("{"):
        return tag.split("}", 1)[1]
    return tag


def _strip_unit(value: str) -> float:
    """Parse the numeric part of an SVG length, ignoring any unit suffix."""
    m = re.match(r"\s*[-+]?[0-9]*\.?[0-9]+(?:[eE][-+]?[0-9]+)?", value)
    if not m:
        raise ValueError(f"not a number: {value!r}")
    return float(m.group(0))


def collect_drawables(root: ET.Element) -> list[ET.Element]:
    """Return the editable elements of a tree in document (paint) order.

    Leaves (shapes, text, images, ``use``) and groups are included; definition
    subtrees are skipped.  The order matches what a renderer would paint, so the
    caller can walk it in reverse to find the top-most element under a point.
    """
    found: list[ET.Element] = []

    def walk(parent: ET.Element) -> None:
        for child in parent:
            name = local_name(child.tag)
            if name in _SKIP_TAGS:
                continue
            if name in _SHAPE_TAGS:
                found.append(child)
            elif name in _GROUP_TAGS:
                found.append(child)
                walk(child)

    walk(root)
    return found


def parse_style(value: str | None) -> dict[str, str]:
    """Parse a ``style="a:b;c:d"`` attribute into a dict (order preserved)."""
    result: dict[str, str] = {}
    if not value:
        return result
    for chunk in value.split(";"):
        chunk = chunk.strip()
        if not chunk or ":" not in chunk:
            continue
        key, val = chunk.split(":", 1)
        result[key.strip()] = val.strip()
    return result


def format_number(value: float) -> str:
    """Format a float compactly (no trailing ``.0``)."""
    if value == int(value):
        return str(int(value))
    return f"{value:.4f}".rstrip("0").rstrip(".")


def is_colorless(value: str | None) -> bool:
    """True when a paint value means "no paint"."""
    if value is None:
        return True
    return value.strip().lower() in {"", "none", "transparent"}


@dataclass
class ViewBox:
    """The visible user-space rectangle of an SVG document."""

    x: float = 0.0
    y: float = 0.0
    width: float = 300.0
    height: float = 150.0

    @classmethod
    def from_root(cls, root: ET.Element) -> ViewBox:
        raw = root.get("viewBox")
        if raw:
            parts = re.split(r"[\s,]+", raw.strip())
            if len(parts) == 4:
                try:
                    x, y, w, h = (_strip_unit(p) for p in parts)
                    if w > 0 and h > 0:
                        return cls(x, y, w, h)
                except ValueError:
                    pass
        # Fall back to width/height attributes, then to the SVG default.
        try:
            w = _strip_unit(root.get("width", ""))
            h = _strip_unit(root.get("height", ""))
            if w > 0 and h > 0:
                return cls(0.0, 0.0, w, h)
        except ValueError:
            pass
        return cls()

    def to_string(self) -> str:
        return " ".join(format_number(v) for v in (self.x, self.y, self.width, self.height))


class SvgDocument:
    """An editable SVG document."""

    def __init__(self) -> None:
        self.root: ET.Element | None = None
        self.path: Path | None = None
        self._drawables: list[ET.Element] = []
        self._undo: list[str] = []
        self._redo: list[str] = []
        self._dirty = False

    # ---------------------------------------------------------------- loading
    @classmethod
    def load(cls, source: str | Path) -> SvgDocument:
        """Load from a filesystem path or an SVG string/bytes."""
        doc = cls()
        text = source.decode("utf-8") if isinstance(source, bytes) else str(source)
        if isinstance(source, (str, Path)) and "<" not in text:
            doc.path = Path(source)
            doc.root = ET.parse(doc.path).getroot()
        else:
            doc.root = ET.fromstring(text)
        if local_name(doc.root.tag) != "svg":
            raise ValueError("root element is not <svg>")
        doc._rescan()
        return doc

    def reload_from(self, svg_text: str) -> None:
        """Replace the tree from serialized SVG text (used by undo/redo)."""
        self.root = ET.fromstring(svg_text)
        self._rescan()

    def _rescan(self) -> None:
        assert self.root is not None
        self._drawables = collect_drawables(self.root)

    # -------------------------------------------------------------- accessors
    @property
    def viewbox(self) -> ViewBox:
        assert self.root is not None
        return ViewBox.from_root(self.root)

    def elements(self) -> list[ET.Element]:
        return list(self._drawables)

    def count(self) -> int:
        return len(self._drawables)

    def element(self, uid: int) -> ET.Element:
        if uid < 0 or uid >= len(self._drawables):
            raise IndexError(f"element index out of range: {uid}")
        return self._drawables[uid]

    def tag(self, uid: int) -> str:
        return local_name(self.element(uid).tag)

    def is_group(self, uid: int) -> bool:
        return local_name(self.element(uid).tag) in _GROUP_TAGS

    def label(self, uid: int) -> str:
        """A short human label for lists and tables."""
        elem = self.element(uid)
        name = local_name(elem.tag)
        ident = elem.get("id")
        if name == "text":
            snippet = self.get_text(uid).strip().replace("\n", " ")
            if snippet:
                snippet = snippet if len(snippet) <= 18 else snippet[:18] + "…"
                return f'{name} "{snippet}"'
        if name in ("rect", "circle", "ellipse") and elem.get("id") is None:
            r = elem.get("width") or elem.get("r")
            if r:
                return f"{name} {r}"
        return f"{name} #{ident}" if ident else name

    # -------------------------------------------------------------- property
    def get_property(self, uid: int, name: str, default: str | None = None) -> str | None:
        style = parse_style(self.element(uid).get("style"))
        if name in style:
            return style[name]
        return self.element(uid).get(name, default)

    def set_property(self, uid: int, name: str, value: str | None) -> None:
        """Set a presentation property (``fill``, ``stroke``, ...), or remove it.

        Writes into the ``style`` attribute when the property already lives
        there, otherwise falls back to the plain presentation attribute.  This
        keeps the edit visible regardless of how the original file expressed it.
        """
        elem = self.element(uid)
        style = parse_style(elem.get("style"))
        if name in style:
            if value is None:
                style.pop(name)
            else:
                style[name] = value
            self._write_style(elem, style)
        else:
            if value is None:
                elem.attrib.pop(name, None)
            else:
                elem.set(name, value)
        self._dirty = True

    def set_color(self, uid: int, name: str, color: str | None) -> None:
        """Set ``fill`` or ``stroke`` to a CSS color, or to ``none``."""
        if name not in COLOR_PROPS:
            raise ValueError(f"not a color property: {name}")
        self.set_property(uid, name, "none" if color is None else color)

    @staticmethod
    def _write_style(elem: ET.Element, style: dict[str, str]) -> None:
        if style:
            elem.set("style", ";".join(f"{k}:{v}" for k, v in style.items()))
        else:
            elem.attrib.pop("style", None)

    # ----------------------------------------------------------- raw attributes
    def attributes(self, uid: int) -> list[tuple[str, str]]:
        return list(self.element(uid).attrib.items())

    def set_attribute(self, uid: int, name: str, value: str | None) -> None:
        """Set or remove any XML attribute; used for shape/geometry editing."""
        elem = self.element(uid)
        if value is None or value == "":
            elem.attrib.pop(name, None)
        else:
            elem.set(name, value)
        self._dirty = True

    # ------------------------------------------------------------------ text
    def get_text(self, uid: int) -> str:
        return "".join(self.element(uid).itertext())

    def set_text(self, uid: int, text: str) -> None:
        elem = self.element(uid)
        if local_name(elem.tag) != "text":
            raise ValueError("set_text is only valid for <text> elements")
        # Drop any <tspan> children and write a single text run.
        for child in list(elem):
            elem.remove(child)
        elem.text = text
        self._dirty = True

    # -------------------------------------------------------------- transform
    def translate(self, uid: int, dx: float, dy: float, base: str | None = None) -> None:
        """Move an element by (dx, dy) user units.

        ``base`` is the transform string captured when a drag started; passing it
        prevents repeated moves from stacking without bound.
        """
        elem = self.element(uid)
        existing = (elem.get("transform") if base is None else base) or ""
        prefix = f"translate({format_number(dx)} {format_number(dy)})"
        combined = f"{prefix} {existing}".strip() if existing.strip() else prefix
        elem.set("transform", combined)
        self._dirty = True

    # ------------------------------------------------------------ add / remove
    def duplicate(self, uid: int) -> int:
        """Duplicate an element into its parent and return the new index."""
        import copy

        elem = self.element(uid)
        parent = self._find_parent(elem)
        if parent is None:
            raise ValueError("cannot duplicate the root")
        index = list(parent).index(elem)
        clone = copy.deepcopy(elem)
        parent.insert(index + 1, clone)
        self._rescan()
        # The clone sits directly after the original in document order.
        self._dirty = True
        return uid + 1

    def delete(self, uid: int) -> None:
        elem = self.element(uid)
        parent = self._find_parent(elem)
        if parent is None:
            raise ValueError("cannot delete the root")
        parent.remove(elem)
        self._rescan()
        self._dirty = True

    def parent_of(self, uid: int) -> ET.Element | None:
        return self._find_parent(self.element(uid))

    def _parent_map(self) -> dict[int, ET.Element]:
        mapping: dict[int, ET.Element] = {}
        assert self.root is not None
        for parent in self.root.iter():
            for child in parent:
                mapping[id(child)] = parent
        return mapping

    def top_level_uid(self, uid: int) -> int:
        """Index of the outermost group containing ``uid`` (or ``uid`` itself).

        This is what a single click selects in editors such as Inkscape: the
        whole group, not the leaf inside it.
        """
        elem = self.element(uid)
        mapping = self._parent_map()
        best = elem
        node = elem
        while True:
            parent = mapping.get(id(node))
            if parent is None:
                break
            if local_name(parent.tag) in _GROUP_TAGS and parent in self._drawables:
                best = parent
            node = parent
        return self._drawables.index(best)

    def group(self, uids: Iterable[int]) -> int:
        """Wrap several sibling elements in a new ``<g>``; return its index.

        All elements must share one parent so their coordinate space is
        unchanged by the regroup.  The group is inserted where the
        earliest-in-document-order member was, preserving visual stacking.
        """
        return self.group_elements([self.element(uid) for uid in dict.fromkeys(uids)])

    def group_elements(self, elems: Iterable[ET.Element]) -> int:
        """Same as :meth:`group` but takes element objects (identity-based).

        Smart grouping builds its clusters from live element references, which
        stay valid while earlier clusters are wrapped, so it uses this form.
        """
        elems = list(elems)
        if not elems:
            raise ValueError("no elements selected")
        parents = [self._find_parent(elem) for elem in elems]
        if any(parent is None for parent in parents):
            raise ValueError("cannot group the root")
        if len({id(parent) for parent in parents}) != 1:
            raise ValueError("elements must share the same parent to be grouped")
        parent = parents[0]
        order = {id(child): i for i, child in enumerate(parent)}
        elems.sort(key=lambda elem: order[id(elem)])
        insert_at = order[id(elems[0])]
        group_elem = ET.Element(f"{{{SVG_NS}}}g")
        for elem in elems:
            parent.remove(elem)
            group_elem.append(elem)
        parent.insert(insert_at, group_elem)
        self._rescan()
        self._dirty = True
        return self._drawables.index(group_elem)

    def ungroup(self, uid: int) -> None:
        """Replace a group with its children, keeping their order and place."""
        elem = self.element(uid)
        if local_name(elem.tag) not in _GROUP_TAGS:
            raise ValueError("selected element is not a group")
        parent = self._find_parent(elem)
        if parent is None:
            raise ValueError("cannot ungroup the root")
        index = list(parent).index(elem)
        children = list(elem)
        for child in children:
            elem.remove(child)
        parent.remove(elem)
        for offset, child in enumerate(children):
            parent.insert(index + offset, child)
        self._rescan()
        self._dirty = True

    def is_grouped(self, uid: int) -> bool:
        """True when the element sits inside at least one group."""
        return self.top_level_uid(uid) != uid

    def _find_parent(self, target: ET.Element) -> ET.Element | None:
        assert self.root is not None
        for parent in self.root.iter():
            for child in parent:
                if child is target:
                    return parent
        return self.root if target is self.root else None

    # ---------------------------------------------------------------- undo
    def push_undo(self) -> None:
        """Snapshot the current state onto the undo stack."""
        self._undo.append(self.to_string())
        if len(self._undo) > 200:
            self._undo.pop(0)
        self._redo.clear()

    def can_undo(self) -> bool:
        return bool(self._undo)

    def can_redo(self) -> bool:
        return bool(self._redo)

    def undo(self) -> bool:
        if not self._undo:
            return False
        self._redo.append(self.to_string())
        self.reload_from(self._undo.pop())
        self._dirty = True
        return True

    def redo(self) -> bool:
        if not self._redo:
            return False
        self._undo.append(self.to_string())
        self.reload_from(self._redo.pop())
        self._dirty = True
        return True

    # ---------------------------------------------------------------- output
    def to_string(self) -> str:
        assert self.root is not None
        body = ET.tostring(self.root, encoding="unicode")
        return '<?xml version="1.0" encoding="UTF-8"?>\n' + body

    def save(self, path: str | Path) -> Path:
        out = Path(path)
        out.write_text(self.to_string(), encoding="utf-8")
        self.path = out
        self._dirty = False
        return out

    @property
    def dirty(self) -> bool:
        return self._dirty

    def summary(self) -> dict:
        """A small JSON-friendly description, used by the CLI and API."""
        by_tag: dict[str, int] = {}
        for elem in self._drawables:
            name = local_name(elem.tag)
            by_tag[name] = by_tag.get(name, 0) + 1
        vb = self.viewbox
        return {
            "source": str(self.path) if self.path else None,
            "elements": len(self._drawables),
            "by_tag": by_tag,
            "viewbox": [vb.x, vb.y, vb.width, vb.height],
        }

    def element_infos(self) -> list[dict]:
        infos = []
        for uid, elem in enumerate(self._drawables):
            infos.append(
                {
                    "index": uid,
                    "tag": local_name(elem.tag),
                    "id": elem.get("id"),
                    "label": self.label(uid),
                    "fill": self.get_property(uid, "fill"),
                    "stroke": self.get_property(uid, "stroke"),
                    "text": self.get_text(uid) if local_name(elem.tag) == "text" else None,
                }
            )
        return infos


def cluster_boxes(
    items: Iterable[tuple[object, tuple[float, float, float, float]]],
    gap: float,
) -> list[list[object]]:
    """Group rectangles that are within ``gap`` of each other (union-find).

    ``items`` is an iterable of ``(key, (x, y, w, h))``.  Two rectangles are
    joined when the first grown by ``gap`` on every side overlaps the second.
    Returns the connected components as lists of keys; used by the GUI's smart
    grouping, kept here (Qt-free) so it can be tested directly.
    """
    entries = list(items)
    count = len(entries)
    parent = list(range(count))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int) -> None:
        root_i, root_j = find(i), find(j)
        if root_i != root_j:
            parent[root_i] = root_j

    for i in range(count):
        xi, yi, wi, hi = entries[i][1]
        for j in range(i + 1, count):
            xj, yj, wj, hj = entries[j][1]
            close = (
                xi - gap < xj + wj
                and xj - gap < xi + wi
                and yi - gap < yj + hj
                and yj - gap < yi + hi
            )
            if close:
                union(i, j)

    components: dict[int, list[object]] = {}
    for i in range(count):
        components.setdefault(find(i), []).append(entries[i][0])
    return list(components.values())


def parse_color(value: str) -> tuple[int, int, int] | None:
    """Parse ``#rgb``/``#rrggbb`` into an (r, g, b) tuple, or None for "none"."""
    if is_colorless(value):
        return None
    text = value.strip()
    if text.startswith("#"):
        hexpart = text[1:]
        if len(hexpart) == 3:
            hexpart = "".join(c * 2 for c in hexpart)
        if len(hexpart) >= 6:
            return (int(hexpart[0:2], 16), int(hexpart[2:4], 16), int(hexpart[4:6], 16))
    # A handful of named colors that show up often in hand-written SVG.
    named = {
        "black": (0, 0, 0),
        "white": (255, 255, 255),
        "red": (255, 0, 0),
        "green": (0, 128, 0),
        "blue": (0, 0, 255),
        "gray": (128, 128, 128),
        "grey": (128, 128, 128),
    }
    return named.get(text.lower())
