# Changelog

All notable changes to CiHuang are documented here.

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
