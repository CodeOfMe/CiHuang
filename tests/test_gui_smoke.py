"""Head-less GUI smoke test: load, hit-test, move, recolor, edit text.

Skipped automatically when PySide6 is unavailable.  Uses Qt's offscreen
platform plugin so it can run on a machine without a display.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication  # noqa: E402

from cihuang.gui import MainWindow  # noqa: E402

SAMPLES = Path(__file__).resolve().parent.parent / "examples" / "sample.svg"


@pytest.fixture(scope="module")
def app():
    instance = QApplication.instance() or QApplication([])
    yield instance


@pytest.fixture()
def window(app):
    win = MainWindow()
    win.resize(900, 600)
    win.load(str(SAMPLES))
    app.processEvents()
    return win


class TestGuiSmoke:
    def test_loads_all_elements(self, window):
        assert window.canvas.doc.count() == 5

    def test_hit_test_picks_topmost_shape(self, window):
        canvas = window.canvas
        # Circle center (45, 50) belongs to element 1.
        assert canvas.element_at(canvas._user_to_scene(45, 50)) == 1
        # Bar center (107, 65) belongs to element 2.
        assert canvas.element_at(canvas._user_to_scene(107, 65)) == 2
        # A point on the frame but not on any child belongs to element 0.
        assert canvas.element_at(canvas._user_to_scene(10, 20)) == 0

    def test_drag_translate_and_recolor(self, window):
        canvas = window.canvas
        canvas.select(1)
        canvas.doc.push_undo()
        canvas.doc.translate(1, 15, 5)
        canvas.doc.set_color(1, "fill", "#cc3366")
        assert "translate(15 5)" in canvas.doc.element(1).get("transform")
        assert canvas.doc.get_property(1, "fill") == "#cc3366"

    def test_undo_restores(self, window):
        canvas = window.canvas
        canvas.doc.push_undo()
        canvas.doc.set_color(1, "fill", "#000000")
        assert canvas.doc.undo() is True
        canvas.rebuild()
        assert canvas.doc.get_property(1, "fill") == "#2c6fbb"

    def test_text_edit(self, window):
        window.canvas.doc.set_text(4, "改过的标题")
        assert window.canvas.doc.get_text(4) == "改过的标题"

    def test_attribute_table_populated(self, window):
        window.canvas.select(1)
        window._populate_properties()
        names = [
            window.attr_table.item(row, 0).text()
            for row in range(window.attr_table.rowCount())
        ]
        assert {"id", "cx", "cy", "r", "fill"} <= set(names)
