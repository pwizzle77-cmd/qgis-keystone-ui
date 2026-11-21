"""Explore tool and pop-ups, like ArcGIS Pro's.

Explore pans (drag) and zooms (wheel) like QGIS's Pan tool; a click
without dragging opens a pop-up with the features under the cursor on all
visible layers: a list (layer and the feature's display name) and the
picked feature's fields, with value maps and other widgets shown as their
labels. The feature is highlighted on the map; Zoom To, Flash, Select and
Attributes act on it.
"""

from qgis.core import (
    Qgis, QgsApplication, QgsCoordinateTransform, QgsExpression,
    QgsExpressionContext, QgsExpressionContextUtils, QgsFeatureRequest,
    QgsGeometry, QgsMessageLog, QgsPointXY, QgsProject, QgsRectangle,
    QgsVectorLayer,
)
from qgis.gui import QgsHighlight, QgsMapTool, QgsMapToolPan
from qgis.PyQt import sip
from qgis.PyQt.QtCore import QPoint, Qt
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtWidgets import (
    QDialog, QDockWidget, QHBoxLayout, QHeaderView, QLabel, QListWidget,
    QListWidgetItem, QSplitter, QTableWidget, QTableWidgetItem, QToolButton,
    QVBoxLayout,
)

from .util import pro_icon, tr

MAX_FEATURES = 50
CLICK_TOLERANCE = 4   # pixels a press may move and still be a click


def _display_value(layer, idx, value):
    try:
        setup = layer.editorWidgetSetup(idx)
        formatter = QgsApplication.fieldFormatterRegistry().fieldFormatter(
            setup.type())
        return str(formatter.representValue(layer, idx, setup.config(),
                                            None, value))
    except Exception:  # noqa: BLE001
        return "" if value is None else str(value)


def _label(layer, feature):
    try:
        expression = QgsExpression(layer.displayExpression())
        context = QgsExpressionContext(
            QgsExpressionContextUtils.globalProjectLayerScopes(layer))
        context.setFeature(feature)
        text = expression.evaluate(context)
        if text not in (None, ""):
            return str(text)
    except Exception as err:  # noqa: BLE001
        QgsMessageLog.logMessage(
            f"explore._label: display expression failed for feature {feature.id()}: {err!r}",
            "Keystone", Qgis.MessageLevel.Info)
    return str(feature.id())


class PopupWindow(QDialog):
    """The pop-up: features found on the left, fields on the right."""

    def __init__(self, iface):
        super().__init__(iface.mainWindow(), Qt.WindowType.Tool)
        self.setObjectName("WorkspacePopup")
        self.setWindowTitle(tr("Pop-up"))
        self.iface = iface
        self.canvas = iface.mapCanvas()
        self.results = []
        self.highlight = None
        box = QVBoxLayout(self)
        box.setContentsMargins(4, 4, 4, 4)
        split = QSplitter(Qt.Orientation.Vertical)
        self.list = QListWidget()
        self.list.currentRowChanged.connect(self.show_feature)
        split.addWidget(self.list)
        self.table = QTableWidget(0, 2)
        self.table.horizontalHeader().setVisible(False)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        split.addWidget(self.table)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 3)
        box.addWidget(split, 1)
        row = QHBoxLayout()
        self.title = QLabel()
        row.addWidget(self.title, 1)
        for icon, tip, slot in (
                ("zoom_selected", tr("Zoom to"), self.zoom_to),
                ("identify", tr("Flash"), self.flash),
                ("select", tr("Select"), self.select),
                ("multi_edit", tr("Edit in the Attributes pane"),
                 self.attributes)):
            button = QToolButton(autoRaise=True, toolTip=tip)
            button.setIcon(pro_icon(icon))
            button.clicked.connect(slot)
            row.addWidget(button)
        box.addLayout(row)
        self.resize(360, 420)

    def set_results(self, results, at):
        self.results = results
        self.list.blockSignals(True)
        self.list.clear()
        for layer, feature in results:
            QListWidgetItem(f"{layer.name()}: {_label(layer, feature)}",
                            self.list)
        self.list.blockSignals(False)
        self.list.setVisible(len(results) > 1)
        self.list.setCurrentRow(0)
        self.show_feature(0)
        if not self.isVisible():
            self.move(at + QPoint(16, 16))
        self.show()
        self.raise_()

    def current(self):
        row = self.list.currentRow()
        if 0 <= row < len(self.results):
            layer, feature = self.results[row]
            if not sip.isdeleted(layer):
                return layer, feature
        return None, None

    def show_feature(self, _row):
        layer, feature = self.current()
        self.clear_highlight()
        self.table.setRowCount(0)
        if layer is None:
            return
        self.title.setText(f"<b>{_label(layer, feature)}</b> "
                           f"({layer.name()})")
        fields = layer.fields()
        for idx, field in enumerate(fields):
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setItem(row, 0, QTableWidgetItem(field.displayName()))
            self.table.setItem(row, 1, QTableWidgetItem(
                _display_value(layer, idx, feature.attribute(idx))))
        if feature.hasGeometry():
            self.highlight = QgsHighlight(self.canvas, feature.geometry(),
                                          layer)
            self.highlight.setColor(QColor(0, 255, 255, 220))
            self.highlight.setFillColor(QColor(0, 255, 255, 60))
            self.highlight.setWidth(3)
            self.highlight.show()

    def clear_highlight(self):
        if self.highlight is not None and not sip.isdeleted(self.highlight):
            self.highlight.hide()
            self.canvas.scene().removeItem(self.highlight)
        self.highlight = None

    def zoom_to(self):
        layer, feature = self.current()
        if layer is not None:
            self.canvas.zoomToFeatureIds(layer, [feature.id()])

    def flash(self):
        layer, feature = self.current()
        if layer is not None:
            self.canvas.flashFeatureIds(layer, [feature.id()])

    def select(self):
        layer, feature = self.current()
        if layer is not None:
            layer.selectByIds([feature.id()])

    def attributes(self):
        layer, feature = self.current()
        if layer is None:
            return
        layer.selectByIds([feature.id()])
        pane = self.iface.mainWindow().findChild(QDockWidget,
                                                 "WorkspaceAttributes")
        if pane is not None:
            pane.show()
            pane.raise_()
            pane.refresh()

    def closeEvent(self, event):  # noqa: N802 (Qt API name)
        self.clear_highlight()
        super().closeEvent(event)


class ExploreTool(QgsMapToolPan):
    """Pan and zoom; click to see a pop-up of the features there."""

    def __init__(self, iface):
        super().__init__(iface.mapCanvas())
        self.iface = iface
        self.popup = PopupWindow(iface)
        self._press = None

    def canvasPressEvent(self, event):  # noqa: N802 (QGIS API name)
        self._press = event.pos() if event.button() == \
            Qt.MouseButton.LeftButton else None
        super().canvasPressEvent(event)

    def canvasReleaseEvent(self, event):  # noqa: N802 (QGIS API name)
        press, self._press = self._press, None
        super().canvasReleaseEvent(event)
        if press is None or event.button() != Qt.MouseButton.LeftButton:
            return
        if (event.pos() - press).manhattanLength() > CLICK_TOLERANCE:
            return   # a drag: panned
        results = self.features_at(event.pos())
        if results:
            self.popup.set_results(results,
                                   self.canvas().mapToGlobal(event.pos()))
        elif self.popup.isVisible():
            self.popup.close()

    def features_at(self, pos):
        return features_at(self.canvas(), pos)

    def stop(self):
        if not sip.isdeleted(self.popup):
            self.popup.close()
            self.popup.deleteLater()
        if self.canvas().mapTool() is self:
            self.canvas().unsetMapTool(self)

def features_at(canvas, pos, accept=None):
    """(layer, feature) pairs under the canvas pixel ``pos`` on the visible
    vector layers, top layer first and the nearest feature first within a
    layer. ``accept(layer)`` may leave layers out."""
    point = canvas.getCoordinateTransform().toMapCoordinates(
        pos.x(), pos.y())
    radius = QgsMapTool.searchRadiusMU(canvas)
    area = QgsRectangle(point.x() - radius, point.y() - radius,
                        point.x() + radius, point.y() + radius)
    here = QgsGeometry.fromPointXY(QgsPointXY(point))
    dest = canvas.mapSettings().destinationCrs()
    out = []
    for layer in canvas.layers():
        if not isinstance(layer, QgsVectorLayer) or not layer.isSpatial():
            continue
        if not layer.isInScaleRange(canvas.scale()):
            continue
        try:
            to_layer = QgsCoordinateTransform(dest, layer.crs(),
                                              QgsProject.instance())
            rect = to_layer.transformBoundingBox(area)
            probe = QgsGeometry(here)
            probe.transform(to_layer)
            tol = max(rect.width(), rect.height()) / 2
        except Exception as err:  # noqa: BLE001 - an odd CRS
            QgsMessageLog.logMessage(
                f"explore: skipping {layer.name()}, could not transform the probe area: {err!r}",
                "Keystone", Qgis.MessageLevel.Info)
            continue
        request = QgsFeatureRequest().setFilterRect(rect)
        hits = []
        for feature in layer.getFeatures(request):
            geom = feature.geometry()
            if geom is None or geom.isEmpty():
                continue
            if geom.distance(probe) <= tol:
                hits.append((geom.distance(probe), feature))
        hits.sort(key=lambda h: h[0])
        out.extend((layer, f) for _d, f in hits)
        if len(out) >= MAX_FEATURES:
            break
    return out[:MAX_FEATURES]
