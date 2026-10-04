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
FIGURE = Path(__file__).resolve().parent.parent / "examples" / "figure.svg"


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

    def test_multi_select_and_group(self, window):
        canvas = window.canvas
        canvas.set_selection([1, 2])
        assert canvas.get_selection() == -1  # not a single selection
        window.group_selected()
        assert canvas.doc.count() == 6  # 5 elements + the new group
        assert len(canvas.selection) == 1
        assert canvas.doc.tag(canvas.selection[0]) == "g"
        window.ungroup_selected()
        assert canvas.doc.count() == 5

    def test_select_all_collapses_groups(self, window):
        window.canvas.doc.group([1, 2])
        window.canvas.rebuild()
        window.select_all()
        # frame is a backdrop and is skipped: 1 group + 1 path + 1 text = 3.
        assert len(window.canvas.selection) == 3

    def test_additive_selection_toggles(self, window):
        canvas = window.canvas
        canvas.select(1)
        canvas.select(2, additive=True)
        assert set(canvas.selection) == {1, 2}
        canvas.select(1, additive=True)
        assert canvas.selection == [2]

    def test_click_on_backdrop_is_ignored(self, window):
        canvas = window.canvas
        # The frame rect covers almost the whole viewBox, so it is a backdrop.
        assert canvas.is_background(0) is True
        assert canvas.element_at(canvas._user_to_scene(10, 20)) == 0
        # A click there should read as empty space, not select the backdrop...
        assert canvas.pick_uid(canvas._user_to_scene(10, 20)) == -1
        # ...while a real shape still selects.
        assert canvas.pick_uid(canvas._user_to_scene(45, 50)) == 1

    def test_background_can_be_selected_from_list(self, window):
        # Deliberate selection from the element list still works.
        window.canvas.set_selection([0])
        assert window.canvas.selection == [0]

    def test_zoom_is_clamped(self, window):
        canvas = window.canvas
        canvas.fit()
        fit_scale = canvas._fit_scale
        # Many zoom-out notches must not shrink the drawing into nothing.
        for _ in range(80):
            canvas.apply_zoom(-1)
        assert canvas.transform().m11() >= fit_scale * 0.24

    def test_smart_group_makes_groups(self, window):
        window.load(str(FIGURE))
        made = window.canvas.smart_group()
        assert made >= 1

    def test_draw_rectangle(self, window):
        canvas = window.canvas
        before = canvas.doc.count()
        canvas.set_tool("rect")
        canvas._finish_creation(canvas._user_to_scene(20, 20), canvas._user_to_scene(80, 60))
        assert canvas.doc.count() == before + 1
        assert canvas.doc.tag(canvas.selection[0]) == "rect"
        # The tool snaps back to Select after one shape.
        assert canvas.tool == "select"

    def test_draw_arrow_has_marker(self, window):
        canvas = window.canvas
        canvas.set_tool("arrow")
        canvas._finish_creation(canvas._user_to_scene(10, 10), canvas._user_to_scene(90, 40))
        uid = canvas.selection[0]
        assert canvas.doc.tag(uid) == "line"
        assert "cihuang-arrow" in (canvas.doc.element(uid).get("marker-end") or "")

    def test_connector_follows_node(self, window):
        canvas = window.canvas
        from_id = canvas.doc.ensure_id(1)
        to_id = canvas.doc.ensure_id(2)
        edge = canvas.doc.add_edge(from_id, to_id, 0, 0, 10, 10)
        canvas.rebuild()
        before = (canvas.doc.element(edge).get("x1"), canvas.doc.element(edge).get("x2"))
        canvas.doc.translate(1, 30, 0, base=canvas.doc.element(1).get("transform"))
        canvas.rebuild()
        after = (canvas.doc.element(edge).get("x1"), canvas.doc.element(edge).get("x2"))
        assert before != after
