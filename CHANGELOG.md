# Changelog

All notable changes to CiHuang are documented here.

## [0.2.2] - 2026-10-04

Drawing tools, in the spirit of yEd.

- Toolbar with Select, Rect, Ellipse, Line, Arrow, Text and Connect tools. Pick a
  shape and drag on the canvas to draw it; a plain click drops a default-sized one.
- The Text tool places a `<text>` element you then edit in the Properties panel.
- A `cihuang-arrow` marker is written into `<defs>` the first time you draw an arrow.
- Connectors: with the Connect tool, drag from one shape to another to draw an arrow
  between them. The edge remembers its endpoints (`data-edge-from` / `data-edge-to`)
  and re-routes automatically whenever you move a connected shape.
- `Esc` returns to the Select tool.

## [0.2.1] - 2026-10-04

- A click or marquee no longer picks the full-canvas backdrop, so clicking empty
  space deselects instead of grabbing the background. A backdrop can still be
  selected from the element list, and the behaviour is a toggle ("Ignore background").
- Added `Smart Group` (`Ctrl+Alt+G`): objects whose bounding boxes sit close together
  are clustered (union-find) and wrapped in a `<g>`, which handles the common
  "figure plus its caption" case. Backdrops are excluded.
- Gentler wheel zoom (1.08 per notch, down from 1.15) and limits so you can neither
  zoom the drawing away to nothing (min 25% of the fitted size) nor lose it at the
  other extreme (max 40x).

## [0.2.0] - 2026-10-04

Performance and multi-selection release.

- Rewrote hit-testing to use `QSvgRenderer.boundsOnElement`; bounding boxes are now
  O(1)-ish instead of a full-document pixel scan (roughly a 1000x speed-up on
  documents with a few hundred elements, so dragging no longer stutters).
- Pixel-level hit tests only run for elements whose bounding box contains the click.
- Multi-selection: Shift-click to add/remove, drag on empty space for a marquee box,
  Ctrl+A to select all (collapsing to top-level groups).
- Group (`Ctrl+G`) and ungroup (`Ctrl+Shift+G`).
- Single click selects a whole group; double click drills into the leaf element.
- Moving a multi-selection moves every selected element together.
- The element list uses extended selection and mirrors the canvas selection.

## [0.1.0] - 2026-10-04

Initial release.

- Open SVG files and list every editable element (shapes, text, images, groups).
- Pixel-based hit-testing that selects the visually top-most element.
- Drag to move an element (written as a per-element `translate`).
- Recolor `fill` / `stroke`, including clearing to `none`.
- Edit any raw shape attribute (`cx`, `r`, `d`, `points`, `transform`, ...).
- Edit the content of `<text>` elements.
- Duplicate, delete, undo and redo; save as SVG and export PNG.
- CLI (`info`, `fill`, `move`, `text`, `remove`, `gui`), Python API returning
  `ToolResult`, and an OpenAI function-calling tool set.
