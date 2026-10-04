# Changelog

All notable changes to CiHuang are documented here.

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
