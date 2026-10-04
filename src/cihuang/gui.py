"""PySide6 graphical editor for CiHuang.

Open an SVG, click any element on the canvas, then drag it, repaint its fill or
stroke, edit its text, or edit any of its raw shape attributes.  Multiple
elements can be selected at once with Shift-click or by dragging a marquee, and
a selection can be grouped (Ctrl+G) or ungrouped (Ctrl+Shift+G).

Performance notes
-----------------
Bounding boxes come from ``QSvgRenderer.boundsOnElement`` (no rasterising), the
scene is normalised so that one scene unit equals one SVG user unit, and pixel
hit-testing only runs for the few elements whose bounds contain the click.  This
keeps dragging and marquee selection cheap even on documents with hundreds of
elements.
"""

from __future__ import annotations

import copy
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

try:
    from PySide6.QtCore import QByteArray, QPointF, QRectF, Qt, QTimer, Signal
    from PySide6.QtGui import QAction, QColor, QImage, QPainter, QPen
    from PySide6.QtSvg import QSvgRenderer
    from PySide6.QtSvgWidgets import QGraphicsSvgItem
    from PySide6.QtWidgets import (
        QApplication,
        QCheckBox,
        QColorDialog,
        QDockWidget,
        QDoubleSpinBox,
        QFileDialog,
        QFormLayout,
        QGraphicsRectItem,
        QGraphicsScene,
        QGraphicsView,
        QHBoxLayout,
        QLabel,
        QListWidget,
        QListWidgetItem,
        QMainWindow,
        QMessageBox,
        QPlainTextEdit,
        QPushButton,
        QTableWidget,
        QTableWidgetItem,
        QToolBar,
        QWidget,
    )

    PYSIDE_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised only without PySide6
    PYSIDE_AVAILABLE = False

from .core import SvgDocument, collect_drawables, format_number

HIT_SCALE = 900  # cap (px) for the raster used in pixel hit-testing


class SvgCanvas(QGraphicsView):
    """The drawing surface: renders the document and handles select/drag."""

    selectionChanged = Signal()
    documentChanged = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.doc: SvgDocument | None = None
        self.selection: list[int] = []
        self.select_whole_group = True

        self._renderer = QSvgRenderer()
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        self.setDragMode(QGraphicsView.NoDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setBackgroundBrush(QColor("#f6f7f9"))

        self._svg_item = QGraphicsSvgItem()
        self._svg_item.setZValue(0)
        self._scene.addItem(self._svg_item)

        self._vb_rect = QRectF(0, 0, 1, 1)
        self._uid_to_id: list[str] = []
        self._masks: dict[int, QImage] = {}
        self._bounds: dict[int, QRectF] = {}

        self._highlights: list[QGraphicsRectItem] = []
        self._marquee = QGraphicsRectItem()
        marquee_pen = QPen(QColor("#2c6fbb"), 0)
        marquee_pen.setCosmetic(True)
        self._marquee.setPen(marquee_pen)
        self._marquee.setBrush(QColor(44, 111, 187, 40))
        self._marquee.setZValue(999)
        self._marquee.setVisible(False)
        self._scene.addItem(self._marquee)

        self._drag_origin = QPointF()
        self._drag_base: dict[int, str] = {}
        self._drag_pushed = False
        self._dragging = False
        self._marquee_origin: QPointF | None = None
        self._pan_origin: QPointF | None = None

    # --------------------------------------------------------------- loading
    def load_path(self, path: str | Path) -> None:
        self.doc = SvgDocument.load(path)
        self.selection = []
        self.rebuild()
        self.fit()

    # --------------------------------------------------------------- render
    def _prepared(self, hide_except: int | None = None) -> tuple[QByteArray, list[str]]:
        """Return a normalised, id-annotated copy of the document as bytes.

        The copy gets ``width``/``height`` equal to the viewBox so one scene
        unit is one user unit; elements without an ``id`` get a synthetic one so
        ``boundsOnElement`` can address them.  Existing ids are never touched,
        because gradients and ``use`` may reference them.
        """
        assert self.doc is not None and self.doc.root is not None
        root = copy.deepcopy(self.doc.root)
        drawables = collect_drawables(root)
        ids: list[str] = []
        for uid, elem in enumerate(drawables):
            ident = elem.get("id")
            if not ident:
                ident = f"__cihuang_uid_{uid}__"
                elem.set("id", ident)
            ids.append(ident)
        if hide_except is not None:
            keep = {id(drawables[hide_except])}
            keep |= {id(node) for node in drawables[hide_except].iter()}
            parents = {id(child): parent for parent in root.iter() for child in parent}
            node: ET.Element | None = drawables[hide_except]
            while node is not None:
                keep.add(id(node))
                node = parents.get(id(node))
            for elem in drawables:
                if id(elem) not in keep:
                    elem.set("display", "none")
        vb = self.doc.viewbox
        root.set("width", format_number(vb.width))
        root.set("height", format_number(vb.height))
        if root.get("viewBox") is None:
            root.set("viewBox", vb.to_string())
        return QByteArray(ET.tostring(root)), ids

    def rebuild(self) -> None:
        """Reload the renderer from the current model."""
        if self.doc is None:
            return
        data, ids = self._prepared()
        self._uid_to_id = ids
        self._renderer.load(data)
        self._svg_item.setSharedRenderer(self._renderer)
        self._svg_item.update()
        vb = self.doc.viewbox
        self._vb_rect = QRectF(vb.x, vb.y, vb.width, vb.height)
        unit_rect = QRectF(0, 0, vb.width, vb.height)
        self._svg_item.setPos(0, 0)
        self._scene.setSceneRect(unit_rect)
        self._masks.clear()
        self._bounds.clear()
        self._update_highlights()

    def refresh(self) -> None:
        selection = list(self.selection)
        self.rebuild()
        self.selection = [uid for uid in selection if uid < (self.doc.count() if self.doc else 0)]
        self._update_highlights()
        self.documentChanged.emit()

    def fit(self) -> None:
        if self.doc is None:
            return
        self.resetTransform()
        self.fitInView(self._scene.sceneRect(), Qt.KeepAspectRatio)

    # ------------------------------------------------------------ coordinates
    def _user_to_scene(self, ux: float, uy: float) -> QPointF:
        return QPointF(ux - self._vb_rect.left(), uy - self._vb_rect.top())

    def _scene_to_user(self, pt: QPointF) -> QPointF:
        return QPointF(pt.x() + self._vb_rect.left(), pt.y() + self._vb_rect.top())

    # ----------------------------------------------------------------- bounds
    def bounds_scene(self, uid: int) -> QRectF:
        """Element bounding box in scene coordinates (cached per rebuild)."""
        if uid in self._bounds:
            return self._bounds[uid]
        rect = QRectF()
        if 0 <= uid < len(self._uid_to_id):
            rect = QRectF(self._renderer.boundsOnElement(self._uid_to_id[uid]))
        self._bounds[uid] = rect
        return rect

    # -------------------------------------------------------------- hit-test
    def _mask(self, uid: int) -> QImage | None:
        if uid in self._masks:
            return self._masks[uid]
        if self.doc is None:
            return None
        vb = self._vb_rect
        k = min(1.0, HIT_SCALE / max(vb.width(), vb.height(), 1.0))
        w = max(1, round(vb.width() * k))
        h = max(1, round(vb.height() * k))
        try:
            data, _ = self._prepared(hide_except=uid)
        except Exception:  # noqa: BLE001
            return None
        renderer = QSvgRenderer(data)
        image = QImage(w, h, QImage.Format_ARGB32)
        image.fill(0)
        painter = QPainter(image)
        renderer.render(painter, QRectF(0, 0, w, h))
        painter.end()
        self._masks[uid] = image
        return image

    def element_at(self, scene_pos: QPointF) -> int:
        """Index of the visually top-most element at a scene point, or -1."""
        if self.doc is None:
            return -1
        user = self._scene_to_user(scene_pos)
        vb = self._vb_rect
        k = min(1.0, HIT_SCALE / max(vb.width(), vb.height(), 1.0))
        px = int((user.x() - vb.left()) * k)
        py = int((user.y() - vb.top()) * k)
        # Only elements whose bounds contain the point can possibly be hit.
        candidates = [
            uid for uid in range(self.doc.count()) if self.bounds_scene(uid).contains(scene_pos)
        ]
        for uid in reversed(candidates):
            image = self._mask(uid)
            if image is None:
                continue
            if 0 <= px < image.width() and 0 <= py < image.height():
                if (image.pixel(px, py) >> 24) & 0xFF > 8:
                    return uid
        # Fall back to the top-most element whose bounds contain the point.
        return candidates[-1] if candidates else -1

    def pick_uid(self, scene_pos: QPointF, leaf: bool = False) -> int:
        """Map a click to a top-level group (default) or the leaf element."""
        uid = self.element_at(scene_pos)
        if uid < 0 or leaf or not self.select_whole_group:
            return uid
        return self.doc.top_level_uid(uid)

    # -------------------------------------------------------------- selection
    def set_selection(self, uids: list[int]) -> None:
        unique = list(dict.fromkeys(uid for uid in uids if 0 <= uid))
        if unique == self.selection:
            return
        self.selection = unique
        self._update_highlights()
        self.selectionChanged.emit()

    def select(self, uid: int, additive: bool = False) -> None:
        if uid < 0:
            if not additive:
                self.set_selection([])
            return
        if additive:
            if uid in self.selection:
                self.set_selection([u for u in self.selection if u != uid])
            else:
                self.set_selection([*self.selection, uid])
        else:
            self.set_selection([uid])

    def get_selection(self) -> int:
        """Single selected index, or -1 when zero or many are selected."""
        return self.selection[0] if len(self.selection) == 1 else -1

    def _update_highlights(self) -> None:
        pen = QPen(QColor("#2c6fbb"), 0)
        pen.setStyle(Qt.DashLine)
        pen.setCosmetic(True)
        pen.setWidth(2)
        while len(self._highlights) < len(self.selection):
            item = QGraphicsRectItem()
            item.setZValue(1000)
            self._scene.addItem(item)
            self._highlights.append(item)
        while len(self._highlights) > len(self.selection):
            self._scene.removeItem(self._highlights.pop())
        for item, uid in zip(self._highlights, self.selection, strict=False):
            rect = self.bounds_scene(uid)
            item.setPen(pen)
            item.setBrush(Qt.NoBrush)
            item.setRect(rect)
            item.setVisible(not rect.isNull())

    # ------------------------------------------------------------ interaction
    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if event.button() == Qt.MiddleButton:
            self._pan_origin = event.position()
            self.setCursor(Qt.ClosedHandCursor)
            return
        if event.button() != Qt.LeftButton or self.doc is None:
            super().mousePressEvent(event)
            return
        scene_pos = self.mapToScene(event.position().toPoint())
        uid = self.pick_uid(scene_pos)
        additive = bool(event.modifiers() & Qt.ShiftModifier)
        if uid >= 0:
            if additive:
                self.select(uid, additive=True)
            elif uid not in self.selection:
                self.select(uid)
            self._begin_drag(scene_pos)
        else:
            if not additive:
                self.set_selection([])
            self._marquee_origin = scene_pos
            self._marquee.setRect(QRectF(scene_pos, scene_pos))
            self._marquee.setVisible(True)
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton and self.doc is not None:
            scene_pos = self.mapToScene(event.position().toPoint())
            uid = self.pick_uid(scene_pos, leaf=True)
            if uid >= 0:
                self.select(uid)
        super().mouseDoubleClickEvent(event)

    def _begin_drag(self, scene_pos: QPointF) -> None:
        self._dragging = True
        self._drag_origin = self._scene_to_user(scene_pos)
        self._drag_base = {
            uid: (self.doc.element(uid).get("transform") or "") for uid in self.selection
        }
        self._drag_pushed = False
        self.setCursor(Qt.SizeAllCursor)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._pan_origin is not None:
            delta = event.position() - self._pan_origin
            self._pan_origin = event.position()
            self.horizontalScrollBar().setValue(
                self.horizontalScrollBar().value() - int(delta.x())
            )
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - int(delta.y()))
            return
        scene_pos = self.mapToScene(event.position().toPoint())
        if self._marquee_origin is not None:
            self._marquee.setRect(QRectF(self._marquee_origin, scene_pos).normalized())
            return
        if self._dragging and self.doc is not None:
            user = self._scene_to_user(scene_pos)
            dx = user.x() - self._drag_origin.x()
            dy = user.y() - self._drag_origin.y()
            if not self._drag_pushed:
                self.doc.push_undo()
                self._drag_pushed = True
            for uid, base in self._drag_base.items():
                self.doc.translate(uid, dx, dy, base=base)
            self.refresh()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MiddleButton:
            self._pan_origin = None
            self.unsetCursor()
        if event.button() == Qt.LeftButton:
            if self._marquee_origin is not None:
                rect = self._marquee.rect()
                self._marquee.setVisible(False)
                self._marquee_origin = None
                chosen = set(self.selection) if event.modifiers() & Qt.ShiftModifier else set()
                for uid in range(self.doc.count() if self.doc else 0):
                    if self.bounds_scene(uid).intersects(rect):
                        chosen.add(self.doc.top_level_uid(uid) if self.select_whole_group else uid)
                self.set_selection(sorted(chosen))
            if self._dragging:
                self._dragging = False
                self._drag_base = {}
                self._drag_pushed = False
                self.unsetCursor()
                self.documentChanged.emit()
        super().mouseReleaseEvent(event)

    def wheelEvent(self, event) -> None:  # noqa: N802
        factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
        self.scale(factor, factor)


class MainWindow(QMainWindow):
    """Top-level window with canvas, element list and property panels."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("CiHuang (雌黄) - SVG element editor")
        self.resize(1180, 780)
        self.canvas = SvgCanvas(self)
        self.setCentralWidget(self.canvas)
        self._updating = False

        self._build_actions()
        self._build_toolbar()
        self._build_docks()
        self.canvas.selectionChanged.connect(self._on_selection_changed)
        self.canvas.documentChanged.connect(self._refresh_lists)
        self.statusBar().showMessage("Open an SVG to begin (Ctrl+O)")

    # ------------------------------------------------------------------ setup
    def _build_actions(self) -> None:
        self.act_open = QAction("Open", self)
        self.act_open.setShortcut("Ctrl+O")
        self.act_open.triggered.connect(self.open_file)

        self.act_save = QAction("Save", self)
        self.act_save.setShortcut("Ctrl+S")
        self.act_save.triggered.connect(self.save_file)

        self.act_save_as = QAction("Save As", self)
        self.act_save_as.setShortcut("Ctrl+Shift+S")
        self.act_save_as.triggered.connect(lambda: self.save_file(force_dialog=True))

        self.act_export = QAction("Export PNG", self)
        self.act_export.triggered.connect(self.export_png)

        self.act_undo = QAction("Undo", self)
        self.act_undo.setShortcut("Ctrl+Z")
        self.act_undo.triggered.connect(self.undo)

        self.act_redo = QAction("Redo", self)
        self.act_redo.setShortcut("Ctrl+Shift+Z")
        self.act_redo.triggered.connect(self.redo)

        self.act_delete = QAction("Delete", self)
        self.act_delete.setShortcut("Del")
        self.act_delete.triggered.connect(self.delete_selected)

        self.act_duplicate = QAction("Duplicate", self)
        self.act_duplicate.setShortcut("Ctrl+D")
        self.act_duplicate.triggered.connect(self.duplicate_selected)

        self.act_group = QAction("Group", self)
        self.act_group.setShortcut("Ctrl+G")
        self.act_group.triggered.connect(self.group_selected)

        self.act_ungroup = QAction("Ungroup", self)
        self.act_ungroup.setShortcut("Ctrl+Shift+G")
        self.act_ungroup.triggered.connect(self.ungroup_selected)

        self.act_select_all = QAction("Select All", self)
        self.act_select_all.setShortcut("Ctrl+A")
        self.act_select_all.triggered.connect(self.select_all)

        self.act_fit = QAction("Fit", self)
        self.act_fit.triggered.connect(self.canvas.fit)

        self.act_whole_group = QAction("Click selects group", self)
        self.act_whole_group.setCheckable(True)
        self.act_whole_group.setChecked(True)
        self.act_whole_group.toggled.connect(
            lambda checked: setattr(self.canvas, "select_whole_group", checked)
        )

    def _build_toolbar(self) -> None:
        bar = QToolBar("Main")
        bar.setMovable(False)
        self.addToolBar(bar)
        for action in (
            self.act_open,
            self.act_save,
            self.act_save_as,
            self.act_export,
            None,
            self.act_undo,
            self.act_redo,
            None,
            self.act_duplicate,
            self.act_delete,
            None,
            self.act_group,
            self.act_ungroup,
            None,
            self.act_fit,
            self.act_whole_group,
        ):
            if action is None:
                bar.addSeparator()
            else:
                bar.addAction(action)

    def _build_docks(self) -> None:
        self.element_list = QListWidget()
        self.element_list.setSelectionMode(QListWidget.ExtendedSelection)
        self.element_list.itemSelectionChanged.connect(self._list_selection_changed)
        dock_left = QDockWidget("Elements", self)
        dock_left.setWidget(self.element_list)
        dock_left.setAllowedAreas(Qt.LeftDockWidgetArea | Qt.RightDockWidgetArea)
        self.addDockWidget(Qt.LeftDockWidgetArea, dock_left)

        self.selection_label = QLabel("no selection")
        self.fill_button = QPushButton("choose color")
        self.fill_button.clicked.connect(lambda: self.pick_color("fill"))
        self.fill_none = QCheckBox("none")
        self.fill_none.stateChanged.connect(lambda: self.toggle_none("fill"))

        self.stroke_button = QPushButton("choose color")
        self.stroke_button.clicked.connect(lambda: self.pick_color("stroke"))
        self.stroke_none = QCheckBox("none")
        self.stroke_none.stateChanged.connect(lambda: self.toggle_none("stroke"))

        self.stroke_width = QDoubleSpinBox()
        self.stroke_width.setRange(0, 1000)
        self.stroke_width.valueChanged.connect(self._stroke_width_changed)

        self.text_edit = QPlainTextEdit()
        self.text_edit.setFixedHeight(64)
        self.text_apply = QPushButton("Apply text")
        self.text_apply.clicked.connect(self.apply_text)

        self.attr_table = QTableWidget(0, 2)
        self.attr_table.setHorizontalHeaderLabels(["name", "value"])
        self.attr_table.horizontalHeader().setStretchLastSection(True)
        self.attr_table.itemChanged.connect(self._attr_edited)

        panel = QWidget()
        form = QFormLayout(panel)
        form.addRow(self.selection_label)
        form.addRow(QLabel("<b>Fill</b>"))
        row = QHBoxLayout()
        row.addWidget(self.fill_button)
        row.addWidget(self.fill_none)
        form.addRow(row)
        form.addRow(QLabel("<b>Stroke</b>"))
        row2 = QHBoxLayout()
        row2.addWidget(self.stroke_button)
        row2.addWidget(self.stroke_none)
        form.addRow(row2)
        form.addRow("Width", self.stroke_width)
        form.addRow(QLabel("<b>Text</b> (single element)"))
        form.addRow(self.text_edit)
        form.addRow(self.text_apply)
        form.addRow(QLabel("<b>Attributes</b> (single element)"))
        form.addRow(self.attr_table)

        dock_right = QDockWidget("Properties", self)
        dock_right.setWidget(panel)
        self.addDockWidget(Qt.RightDockWidgetArea, dock_right)

    # ------------------------------------------------------------- file menu
    def open_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Open SVG", "", "SVG files (*.svg)")
        if path:
            self.load(path)

    def load(self, path: str) -> None:
        try:
            self.canvas.load_path(path)
            self.setWindowTitle(f"CiHuang - {Path(path).name}")
            self._refresh_lists()
            self.statusBar().showMessage(f"Loaded {path} ({self.canvas.doc.count()} elements)")
            # If the load happens before the window is shown the viewport has no
            # size yet, so re-fit on the next event-loop turn.
            QTimer.singleShot(0, self.canvas.fit)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "Open failed", str(exc))

    def save_file(self, force_dialog: bool = False) -> None:
        if self.canvas.doc is None:
            return
        path = self.canvas.doc.path
        if force_dialog or path is None:
            path, _ = QFileDialog.getSaveFileName(self, "Save SVG", "edited.svg", "SVG (*.svg)")
            if not path:
                return
        try:
            self.canvas.doc.save(path)
            self.statusBar().showMessage(f"Saved {path}")
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "Save failed", str(exc))

    def export_png(self) -> None:
        if self.canvas.doc is None:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export PNG", "export.png", "PNG (*.png)")
        if not path:
            return
        vb = self.canvas.doc.viewbox
        width = max(1, int(vb.width))
        height = max(1, int(vb.height))
        renderer = QSvgRenderer(QByteArray(self.canvas.doc.to_string().encode("utf-8")))
        image = QImage(width, height, QImage.Format_ARGB32)
        image.fill(0)
        painter = QPainter(image)
        renderer.render(painter, QRectF(0, 0, width, height))
        painter.end()
        image.save(path)
        self.statusBar().showMessage(f"Exported {path}")

    # ---------------------------------------------------------------- editing
    def _push_undo(self) -> None:
        if self.canvas.doc is not None:
            self.canvas.doc.push_undo()

    def undo(self) -> None:
        if self.canvas.doc and self.canvas.doc.undo():
            self.canvas.rebuild()
            self._refresh_lists()

    def redo(self) -> None:
        if self.canvas.doc and self.canvas.doc.redo():
            self.canvas.rebuild()
            self._refresh_lists()

    def select_all(self) -> None:
        if self.canvas.doc is None:
            return
        if self.canvas.select_whole_group:
            uids = {
                self.canvas.doc.top_level_uid(uid) for uid in range(self.canvas.doc.count())
            }
            self.canvas.set_selection(sorted(uids))
        else:
            self.canvas.set_selection(list(range(self.canvas.doc.count())))

    def delete_selected(self) -> None:
        if self.canvas.doc is None or not self.canvas.selection:
            return
        self._push_undo()
        for uid in sorted(self.canvas.selection, reverse=True):
            try:
                self.canvas.doc.delete(uid)
            except ValueError:
                continue
        self.canvas.selection = []
        self.canvas.rebuild()
        self._refresh_lists()

    def duplicate_selected(self) -> None:
        if self.canvas.doc is None or not self.canvas.selection:
            return
        self._push_undo()
        new_uids = []
        for uid in sorted(self.canvas.selection):
            try:
                new_uids.append(self.canvas.doc.duplicate(uid))
            except ValueError:
                continue
        self.canvas.rebuild()
        self.canvas.set_selection(new_uids)
        self._refresh_lists()

    def group_selected(self) -> None:
        if self.canvas.doc is None or len(self.canvas.selection) < 1:
            return
        self._push_undo()
        try:
            new_uid = self.canvas.doc.group(self.canvas.selection)
        except ValueError as exc:
            self.statusBar().showMessage(f"Cannot group: {exc}")
            return
        self.canvas.rebuild()
        self.canvas.set_selection([new_uid])
        self._refresh_lists()

    def ungroup_selected(self) -> None:
        if self.canvas.doc is None or not self.canvas.selection:
            return
        self._push_undo()
        changed = False
        for uid in sorted(self.canvas.selection, reverse=True):
            try:
                self.canvas.doc.ungroup(uid)
                changed = True
            except ValueError:
                continue
        if not changed:
            self.statusBar().showMessage("No group selected")
            return
        self.canvas.rebuild()
        self.canvas.selection = []
        self._refresh_lists()

    def pick_color(self, prop: str) -> None:
        if self.canvas.doc is None or not self.canvas.selection:
            return
        uid = self.canvas.selection[0]
        current = self.canvas.doc.get_property(uid, prop) or "#000000"
        color = QColor(current)
        chosen = QColorDialog.getColor(color if color.isValid() else QColor("#000000"), self)
        if chosen.isValid():
            self._push_undo()
            for target in self.canvas.selection:
                self.canvas.doc.set_color(target, prop, chosen.name())
            self.canvas.refresh()
            self._populate_properties()

    def toggle_none(self, prop: str) -> None:
        if self.canvas.doc is None or not self.canvas.selection:
            return
        checkbox = self.fill_none if prop == "fill" else self.stroke_none
        if checkbox.isChecked():
            self._push_undo()
            for target in self.canvas.selection:
                self.canvas.doc.set_color(target, prop, None)
            self.canvas.refresh()
            self._populate_properties()

    def _stroke_width_changed(self, value: float) -> None:
        if self._updating or self.canvas.doc is None or not self.canvas.selection:
            return
        self._push_undo()
        for target in self.canvas.selection:
            self.canvas.doc.set_property(target, "stroke-width", format_number(value))
        self.canvas.refresh()

    def apply_text(self) -> None:
        uid = self.canvas.get_selection()
        if self.canvas.doc is None or uid < 0:
            return
        if self.canvas.doc.tag(uid) != "text":
            return
        self._push_undo()
        self.canvas.doc.set_text(uid, self.text_edit.toPlainText())
        self.canvas.refresh()

    def _attr_edited(self, item: QTableWidgetItem) -> None:
        uid = self.canvas.get_selection()
        if self._updating or self.canvas.doc is None or uid < 0:
            return
        name_item = self.attr_table.item(item.row(), 0)
        value_item = self.attr_table.item(item.row(), 1)
        if name_item is None:
            return
        name = name_item.text().strip()
        value = value_item.text() if value_item else ""
        if not name:
            return
        self._push_undo()
        self.canvas.doc.set_attribute(uid, name, value)
        self.canvas.refresh()

    # ------------------------------------------------------------------ sync
    def _list_selection_changed(self) -> None:
        if self._updating:
            return
        self.canvas.set_selection([i.row() for i in self.element_list.selectedIndexes()])

    def _on_selection_changed(self) -> None:
        self._updating = True
        chosen = set(self.canvas.selection)
        for row in range(self.element_list.count()):
            self.element_list.item(row).setSelected(row in chosen)
        self._updating = False
        self._update_actions()
        self._populate_properties()

    def _update_actions(self) -> None:
        enabled = self.canvas.doc is not None
        n = len(self.canvas.selection)
        self.act_delete.setEnabled(enabled and n > 0)
        self.act_duplicate.setEnabled(enabled and n > 0)
        self.act_group.setEnabled(enabled and n > 0)
        self.act_ungroup.setEnabled(enabled and n > 0)
        self.act_select_all.setEnabled(enabled)

    def _refresh_lists(self) -> None:
        self._updating = True
        self.element_list.clear()
        if self.canvas.doc is not None:
            for uid in range(self.canvas.doc.count()):
                self.element_list.addItem(
                    QListWidgetItem(f"{uid}: {self.canvas.doc.label(uid)}")
                )
        self._updating = False
        self._on_selection_changed()

    def _populate_properties(self) -> None:
        self._updating = True
        doc = self.canvas.doc
        n = len(self.canvas.selection)
        uid = self.canvas.get_selection()
        single = doc is not None and uid >= 0
        any_selection = doc is not None and n > 0

        if n == 0:
            self.selection_label.setText("no selection")
        elif n == 1:
            self.selection_label.setText(f"selected: {doc.label(uid)}")
        else:
            self.selection_label.setText(f"{n} elements selected")

        self.fill_button.setEnabled(any_selection)
        self.fill_none.setEnabled(any_selection)
        self.stroke_button.setEnabled(any_selection)
        self.stroke_none.setEnabled(any_selection)
        self.stroke_width.setEnabled(any_selection)
        self.text_edit.setEnabled(single)
        self.text_apply.setEnabled(single and doc.tag(uid) == "text")
        self.attr_table.setEnabled(single)

        self.attr_table.setRowCount(0)
        if any_selection:
            sample = self.canvas.selection[0]
            fill = doc.get_property(sample, "fill")
            stroke = doc.get_property(sample, "stroke")
            self._set_color_button(self.fill_button, fill)
            self._set_color_button(self.stroke_button, stroke)
            self.fill_none.setChecked(fill is not None and fill.strip().lower() == "none")
            self.stroke_none.setChecked(stroke is not None and stroke.strip().lower() == "none")
            try:
                self.stroke_width.setValue(float(doc.get_property(sample, "stroke-width") or 1.0))
            except ValueError:
                self.stroke_width.setValue(1.0)
        if single:
            is_text = doc.tag(uid) == "text"
            self.text_edit.setPlainText(doc.get_text(uid) if is_text else "")
            attrs = doc.attributes(uid)
            self.attr_table.setRowCount(len(attrs))
            for row, (name, value) in enumerate(attrs):
                self.attr_table.setItem(row, 0, QTableWidgetItem(name))
                self.attr_table.setItem(row, 1, QTableWidgetItem(value))
        else:
            self.text_edit.setPlainText("")
        self._updating = False

    @staticmethod
    def _set_color_button(button: QPushButton, value: str | None) -> None:
        if value is None or value.strip().lower() in ("", "none", "transparent"):
            button.setText("none")
            button.setStyleSheet("")
            return
        color = QColor(value)
        if color.isValid():
            button.setText(value)
            text_color = "#ffffff" if color.lightness() < 128 else "#000000"
            button.setStyleSheet(f"background-color: {color.name()}; color: {text_color};")
        else:
            button.setText(value)


def main(argv: list[str] | None = None) -> int:
    if not PYSIDE_AVAILABLE:
        print("PySide6 is required for the GUI. Install with: pip install PySide6")
        return 1
    argv = list(sys.argv if argv is None else argv)
    app = QApplication.instance() or QApplication(argv)
    window = MainWindow()
    for arg in argv[1:]:
        if not arg.startswith("-") and Path(arg).exists():
            window.load(arg)
            break
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
