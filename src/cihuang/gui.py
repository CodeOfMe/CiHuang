"""PySide6 graphical editor for CiHuang.

Open an SVG, click any element on the canvas, then drag it, repaint its fill or
stroke, edit its text, or edit any of its raw shape attributes.  Undo/redo,
duplicate and delete are available, and the result can be saved as SVG or
exported to PNG.

Selection hit-testing is pixel-based: for a click we render candidates in
reverse paint order and take the first whose alpha is set at that point, so
clicks land on the visually top-most shape rather than its bounding box.
"""

from __future__ import annotations

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

MASK_MAX = 1600  # cap for the raster used in pixel hit-testing


class SvgCanvas(QGraphicsView):
    """The drawing surface: renders the document and handles select/drag."""

    elementSelected = Signal(int)  # uid, or -1
    documentChanged = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.doc: SvgDocument | None = None
        self.selected: int = -1
        self._renderer: QSvgRenderer | None = None
        self._svg_item: QGraphicsSvgItem | None = None
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        self.setDragMode(QGraphicsView.NoDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setBackgroundBrush(QColor("#f6f7f9"))

        self._item_rect = QRectF()
        self._vb_rect = QRectF(0, 0, 1, 1)
        self._masks: dict[int, QImage] = {}
        self._bboxes: dict[int, QRectF] = {}

        self._highlight = QGraphicsRectItem()
        pen = QPen(QColor("#2c6fbb"), 0)
        pen.setStyle(Qt.DashLine)
        pen.setCosmetic(True)
        pen.setWidth(2)
        self._highlight.setPen(pen)
        self._highlight.setBrush(Qt.NoBrush)
        self._highlight.setZValue(1000)
        self._highlight.setVisible(False)
        self._scene.addItem(self._highlight)

        self._drag_uid = -1
        self._drag_origin = QPointF()
        self._drag_base_transform = ""
        self._drag_pushed = False
        self._pan_origin = None

    # --------------------------------------------------------------- loading
    def load_path(self, path: str | Path) -> None:
        self.doc = SvgDocument.load(path)
        self.selected = -1
        self.rebuild()
        self.fit()

    def rebuild(self) -> None:
        """Re-create the SVG renderer from the current model."""
        if self.doc is None:
            return
        if self._svg_item is not None:
            self._scene.removeItem(self._svg_item)
            self._svg_item = None
        data = QByteArray(self.doc.to_string().encode("utf-8"))
        self._renderer = QSvgRenderer(data)
        self._svg_item = QGraphicsSvgItem()
        self._svg_item.setSharedRenderer(self._renderer)
        self._svg_item.setZValue(0)
        self._scene.addItem(self._svg_item)
        self._item_rect = self._svg_item.sceneBoundingRect()
        vb = self.doc.viewbox
        self._vb_rect = QRectF(vb.x, vb.y, vb.width, vb.height)
        self._scene.setSceneRect(self._item_rect)
        self._masks.clear()
        self._bboxes.clear()
        self._update_highlight()

    def refresh(self) -> None:
        """Re-render after a model edit, keeping the selection."""
        sel = self.selected
        self.rebuild()
        if 0 <= sel < (self.doc.count() if self.doc else 0):
            self.select(sel)
        self.documentChanged.emit()

    def fit(self) -> None:
        if self._svg_item is None:
            return
        self.resetTransform()
        self.fitInView(self._item_rect, Qt.KeepAspectRatio)

    # ------------------------------------------------------------ coordinates
    def _user_to_scene(self, ux: float, uy: float) -> QPointF:
        r, v = self._item_rect, self._vb_rect
        if v.width() == 0 or v.height() == 0:
            return QPointF(ux, uy)
        sx = r.left() + (ux - v.left()) / v.width() * r.width()
        sy = r.top() + (uy - v.top()) / v.height() * r.height()
        return QPointF(sx, sy)

    def _scene_to_user(self, pt: QPointF) -> QPointF:
        r, v = self._item_rect, self._vb_rect
        if r.width() == 0 or r.height() == 0:
            return pt
        ux = v.left() + (pt.x() - r.left()) / r.width() * v.width()
        uy = v.top() + (pt.y() - r.top()) / r.height() * v.height()
        return QPointF(ux, uy)

    def _mask_scale(self) -> tuple[int, int]:
        v = self._vb_rect
        k = min(1.0, MASK_MAX / max(v.width(), v.height(), 1.0))
        return max(1, round(v.width() * k)), max(1, round(v.height() * k))

    # -------------------------------------------------------------- hit-test
    def _mask(self, uid: int) -> QImage | None:
        if uid in self._masks:
            return self._masks[uid]
        if self.doc is None:
            return None
        w, h = self._mask_scale()
        try:
            root = ET.fromstring(self.doc.to_string())
            drawables = collect_drawables(root)
            if uid >= len(drawables):
                return None
            target = drawables[uid]
        except ET.ParseError:
            return None

        parents = {id(child): parent for parent in root.iter() for child in parent}
        keep = {id(target)} | {id(node) for node in target.iter()}
        node = target
        while node is not None:
            keep.add(id(node))
            node = parents.get(id(node))
        for element in drawables:
            if id(element) not in keep:
                element.set("display", "none")

        renderer = QSvgRenderer(QByteArray(ET.tostring(root)))
        image = QImage(w, h, QImage.Format_ARGB32)
        image.fill(0)
        painter = QPainter(image)
        renderer.render(painter, QRectF(0, 0, w, h))
        painter.end()
        self._masks[uid] = image
        return image

    def _bbox_user(self, uid: int) -> QRectF | None:
        if uid in self._bboxes:
            return self._bboxes[uid]
        image = self._mask(uid)
        if image is None:
            return None
        # Scan rows/columns for the first/last set pixel.
        left, top, right, bottom = image.width(), image.height(), -1, -1
        for y in range(image.height()):
            for x in range(image.width()):
                if (image.pixel(x, y) >> 24) & 0xFF > 8:
                    if x < left:
                        left = x
                    if x > right:
                        right = x
                    if y < top:
                        top = y
                    if y > bottom:
                        bottom = y
        if right < 0:
            rect = QRectF()
        else:
            w, h = image.width(), image.height()
            v = self._vb_rect
            rect = QRectF(
                v.left() + left / w * v.width(),
                v.top() + top / h * v.height(),
                (right - left + 1) / w * v.width(),
                (bottom - top + 1) / h * v.height(),
            )
        self._bboxes[uid] = rect
        return rect

    def element_at(self, scene_pos: QPointF) -> int:
        if self.doc is None:
            return -1
        user = self._scene_to_user(scene_pos)
        v = self._vb_rect
        w, h = self._mask_scale()
        px = int((user.x() - v.left()) / v.width() * w)
        py = int((user.y() - v.top()) / v.height() * h)
        for uid in range(self.doc.count() - 1, -1, -1):
            image = self._mask(uid)
            if image is None:
                continue
            if 0 <= px < image.width() and 0 <= py < image.height():
                if (image.pixel(px, py) >> 24) & 0xFF > 8:
                    return uid
        return -1

    # -------------------------------------------------------------- selection
    def select(self, uid: int) -> None:
        self.selected = uid
        self._update_highlight()
        self.elementSelected.emit(uid)

    def _update_highlight(self) -> None:
        if self.doc is None or self.selected < 0:
            self._highlight.setVisible(False)
            return
        rect = self._bbox_user(self.selected)
        if rect is None or rect.isNull():
            self._highlight.setVisible(False)
            return
        tl = self._user_to_scene(rect.left(), rect.top())
        br = self._user_to_scene(rect.right(), rect.bottom())
        self._highlight.setRect(QRectF(tl, br).normalized())
        self._highlight.setVisible(True)

    # ------------------------------------------------------------ interaction
    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if event.button() == Qt.MiddleButton:
            self._pan_origin = event.position()
            self.setCursor(Qt.ClosedHandCursor)
            return
        if event.button() == Qt.LeftButton and self.doc is not None:
            scene_pos = self.mapToScene(event.position().toPoint())
            uid = self.element_at(scene_pos)
            self.select(uid)
            if uid >= 0:
                self._drag_uid = uid
                self._drag_origin = self._scene_to_user(scene_pos)
                self._drag_base_transform = self.doc.element(uid).get("transform") or ""
                self.setCursor(Qt.SizeAllCursor)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._pan_origin is not None:
            delta = event.position() - self._pan_origin
            self._pan_origin = event.position()
            self.horizontalScrollBar().setValue(
                self.horizontalScrollBar().value() - int(delta.x())
            )
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - int(delta.y()))
            return
        if self._drag_uid >= 0 and self.doc is not None:
            user = self._scene_to_user(self.mapToScene(event.position().toPoint()))
            dx = user.x() - self._drag_origin.x()
            dy = user.y() - self._drag_origin.y()
            if not self._drag_pushed:
                # Snapshot once, on the first real movement of this drag.
                self.doc.push_undo()
                self._drag_pushed = True
            self.doc.translate(self._drag_uid, dx, dy, base=self._drag_base_transform)
            self.refresh()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MiddleButton:
            self._pan_origin = None
            self.unsetCursor()
        if event.button() == Qt.LeftButton and self._drag_uid >= 0:
            self._drag_uid = -1
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
        self.canvas.elementSelected.connect(self._on_selected)
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

        self.act_fit = QAction("Fit", self)
        self.act_fit.triggered.connect(self.canvas.fit)

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
            self.act_fit,
        ):
            if action is None:
                bar.addSeparator()
            else:
                bar.addAction(action)

    def _build_docks(self) -> None:
        # Left: element list.
        self.element_list = QListWidget()
        self.element_list.currentRowChanged.connect(self._list_row_changed)
        dock_left = QDockWidget("Elements", self)
        dock_left.setWidget(self.element_list)
        dock_left.setAllowedAreas(Qt.LeftDockWidgetArea | Qt.RightDockWidgetArea)
        self.addDockWidget(Qt.LeftDockWidgetArea, dock_left)

        # Right: properties + attributes.
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
        self.text_edit.setFixedHeight(72)
        self.text_apply = QPushButton("Apply text")
        self.text_apply.clicked.connect(self.apply_text)

        self.attr_table = QTableWidget(0, 2)
        self.attr_table.setHorizontalHeaderLabels(["name", "value"])
        self.attr_table.horizontalHeader().setStretchLastSection(True)
        self.attr_table.itemChanged.connect(self._attr_edited)

        panel = QWidget()
        form = QFormLayout(panel)
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
        form.addRow(QLabel("<b>Text</b>"))
        form.addRow(self.text_edit)
        form.addRow(self.text_apply)
        form.addRow(QLabel("<b>Attributes</b> (edit to reshape)"))
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

    def delete_selected(self) -> None:
        if self.canvas.doc is None or self.canvas.selected < 0:
            return
        self._push_undo()
        self.canvas.doc.delete(self.canvas.selected)
        self.canvas.selected = -1
        self.canvas.rebuild()
        self._refresh_lists()

    def duplicate_selected(self) -> None:
        if self.canvas.doc is None or self.canvas.selected < 0:
            return
        self._push_undo()
        new_uid = self.canvas.doc.duplicate(self.canvas.selected)
        self.canvas.rebuild()
        self.canvas.select(new_uid)
        self._refresh_lists()

    def pick_color(self, prop: str) -> None:
        if self.canvas.doc is None or self.canvas.selected < 0:
            return
        current = self.canvas.doc.get_property(self.canvas.selected, prop) or "#000000"
        color = QColorDialog.getColor(QColor(current) if QColor(current).isValid() else QColor("#000000"), self)
        if color.isValid():
            self._push_undo()
            self.canvas.doc.set_color(self.canvas.selected, prop, color.name())
            self.canvas.refresh()
            self._populate_properties()

    def toggle_none(self, prop: str) -> None:
        if self.canvas.doc is None or self.canvas.selected < 0:
            return
        checkbox = self.fill_none if prop == "fill" else self.stroke_none
        if checkbox.isChecked():
            self._push_undo()
            self.canvas.doc.set_color(self.canvas.selected, prop, None)
            self.canvas.refresh()
            self._populate_properties()

    def _stroke_width_changed(self, value: float) -> None:
        if self._updating or self.canvas.doc is None or self.canvas.selected < 0:
            return
        self._push_undo()
        self.canvas.doc.set_property(
            self.canvas.selected, "stroke-width", format_number(value)
        )
        self.canvas.refresh()

    def apply_text(self) -> None:
        if self.canvas.doc is None or self.canvas.selected < 0:
            return
        if self.canvas.doc.tag(self.canvas.selected) != "text":
            return
        self._push_undo()
        self.canvas.doc.set_text(self.canvas.selected, self.text_edit.toPlainText())
        self.canvas.refresh()

    def _attr_edited(self, item: QTableWidgetItem) -> None:
        if self._updating or self.canvas.doc is None or self.canvas.selected < 0:
            return
        row = item.row()
        name_item = self.attr_table.item(row, 0)
        value_item = self.attr_table.item(row, 1)
        if name_item is None:
            return
        name = name_item.text().strip()
        value = value_item.text() if value_item else ""
        if not name:
            return
        self._push_undo()
        self.canvas.doc.set_attribute(self.canvas.selected, name, value)
        self.canvas.refresh()

    # ------------------------------------------------------------------ sync
    def _list_row_changed(self, row: int) -> None:
        if self._updating:
            return
        if row >= 0 and self.canvas.doc is not None:
            self.canvas.select(row)

    def _on_selected(self, uid: int) -> None:
        self._updating = True
        if 0 <= uid < self.element_list.count():
            self.element_list.setCurrentRow(uid)
        self._updating = False
        self._populate_properties()

    def _refresh_lists(self) -> None:
        self._updating = True
        self.element_list.clear()
        if self.canvas.doc is not None:
            for uid in range(self.canvas.doc.count()):
                self.element_list.addItem(
                    QListWidgetItem(f"{uid}: {self.canvas.doc.label(uid)}")
                )
        self._updating = False
        self._populate_properties()

    def _populate_properties(self) -> None:
        self._updating = True
        doc = self.canvas.doc
        uid = self.canvas.selected
        enabled = doc is not None and uid >= 0
        for widget in (
            self.fill_button,
            self.fill_none,
            self.stroke_button,
            self.stroke_none,
            self.stroke_width,
            self.text_edit,
            self.text_apply,
            self.attr_table,
        ):
            widget.setEnabled(enabled)

        self.attr_table.setRowCount(0)
        if enabled:
            fill = doc.get_property(uid, "fill")
            stroke = doc.get_property(uid, "stroke")
            self._set_color_button(self.fill_button, fill)
            self._set_color_button(self.stroke_button, stroke)
            self.fill_none.setChecked(fill is not None and fill.strip().lower() == "none")
            self.stroke_none.setChecked(stroke is not None and stroke.strip().lower() == "none")
            try:
                self.stroke_width.setValue(float(doc.get_property(uid, "stroke-width") or 1.0))
            except ValueError:
                self.stroke_width.setValue(1.0)
            is_text = doc.tag(uid) == "text"
            self.text_apply.setEnabled(is_text)
            self.text_edit.setPlainText(doc.get_text(uid) if is_text else "")
            attrs = doc.attributes(uid)
            self.attr_table.setRowCount(len(attrs))
            for row, (name, value) in enumerate(attrs):
                self.attr_table.setItem(row, 0, QTableWidgetItem(name))
                self.attr_table.setItem(row, 1, QTableWidgetItem(value))
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
    # argv may carry a path (from the CLI); skip flags.
    for arg in argv[1:]:
        if not arg.startswith("-") and Path(arg).exists():
            window.load(arg)
            break
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
