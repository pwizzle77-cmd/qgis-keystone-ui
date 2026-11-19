"""Create Features pane, like ArcGIS Pro's.

Every editable layer of the map is listed as a feature template, with its
symbol. A layer drawn by categories (unique values) gets one template per
category, which presets the category field, as Pro does. Clicking a
template:

* makes the layer active and starts editing on it if needed,
* shows the construction tools for the layer's geometry (point; line, arc,
  Bézier, freehand, trace; polygon, rectangle, circle, ellipse, regular
  polygon), and starts the first one,
* applies the template's attribute presets to each new feature (only to
  fields left empty, so the attribute form still wins).

Presets are kept in the project, per template, and edited in the pane's
Attributes table.
"""

import json

from qgis.core import (
    QgsCategorizedSymbolRenderer, QgsProject, QgsSymbolLayerUtils,
    QgsVectorLayer, QgsWkbTypes,
)
from qgis.PyQt import sip
from qgis.PyQt.QtCore import QSize, Qt, QTimer
from qgis.PyQt.QtGui import QAction, QIcon
from qgis.PyQt.QtWidgets import (
    QAbstractItemView, QDockWidget, QFrame, QGridLayout,
    QHeaderView, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QSplitter, QTableWidget, QTableWidgetItem, QToolButton, QVBoxLayout,
    QWidget,
)

from .util import pro_icon, tr

OBJECT_NAME = "WorkspaceCreateFeatures"
PRESET_KEY = "presets"           # project entry scope "keystone"
SCOPE = "keystone"
OLD_SCOPE = "workspace_ribbon"  # projects saved before the rename
LAYER_ROLE = Qt.ItemDataRole.UserRole
KEY_ROLE = Qt.ItemDataRole.UserRole + 1
PRESET_ROLE = Qt.ItemDataRole.UserRole + 2

# Construction tools: (label, icon, how). ``how`` is a list of QGIS action
# names triggered in order, or "shape:<kind>" for a shape digitizing tool.
POINT_TOOLS = [
    (tr("Point"), "create_feature", ["mActionAddFeature",
                                      "mActionDigitizeWithSegment"]),
]
LINE_TOOLS = [
    (tr("Line"), "create_feature", ["mActionAddFeature",
                                     "mActionDigitizeWithSegment"]),
    (tr("Arc"), "fillet", ["mActionAddFeature", "mActionDigitizeWithCurve"]),
    (tr("Bézier"), "reshape", ["mActionAddFeature",
                                "mActionDigitizeWithBezier"]),
    (tr("Freehand"), "simplify", ["mActionAddFeature",
                                   "mActionStreamDigitize"]),
    (tr("Trace"), "tracing", ["mActionAddFeature",
                               "mActionDigitizeWithSegment", "trace"]),
]
POLYGON_TOOLS = [
    (tr("Polygon"), "create_feature", ["mActionAddFeature",
                                        "mActionDigitizeWithSegment"]),
    (tr("Rectangle"), "rectangle", ["shape:rectangle"]),
    (tr("Circle"), "circle", ["shape:circle"]),
    (tr("Ellipse"), "ellipse", ["shape:ellipse"]),
    (tr("Regular Polygon"), "regular_polygon", ["shape:regular-polygon"]),
    (tr("Freehand"), "simplify", ["mActionAddFeature",
                                   "mActionStreamDigitize"]),
    (tr("Trace"), "tracing", ["mActionAddFeature",
                               "mActionDigitizeWithSegment", "trace"]),
    (tr("Arc"), "fillet", ["mActionAddFeature", "mActionDigitizeWithCurve"]),
]


def _geometry(layer):
    kind = layer.geometryType()
    try:
        from qgis.core import Qgis
        point, line, poly = (Qgis.GeometryType.Point, Qgis.GeometryType.Line,
                             Qgis.GeometryType.Polygon)
    except AttributeError:
        point, line, poly = (QgsWkbTypes.PointGeometry,
                             QgsWkbTypes.LineGeometry,
                             QgsWkbTypes.PolygonGeometry)
    return {point: "point", line: "line", poly: "polygon"}.get(kind)


def _symbol_icon(symbol):
    if symbol is None:
        return QIcon()
    try:
        return QgsSymbolLayerUtils.symbolPreviewIcon(symbol, QSize(24, 24))
    except Exception:  # noqa: BLE001 - an odd symbol type
        return QIcon()


def _templates(layer):
    """(name, icon, presets) for a layer: one per category of a unique-value
    renderer, otherwise one for the layer."""
    renderer = layer.renderer()
    if isinstance(renderer, QgsCategorizedSymbolRenderer):
        field = renderer.classAttribute()
        if layer.fields().lookupField(field) >= 0:
            out = []
            for cat in renderer.categories():
                if cat.value() in (None, "") or not cat.renderState():
                    continue
                label = cat.label() or str(cat.value())
                out.append((label, _symbol_icon(cat.symbol()),
                            {field: str(cat.value())}))
            if out:
                return out
    symbol = None
    try:
        symbols = renderer.symbols(layer.createExpressionContext()) \
            if hasattr(renderer, "symbols") else []
        symbol = symbols[0] if symbols else None
    except Exception:  # noqa: BLE001
        symbol = None
    return [(layer.name(), _symbol_icon(symbol), {})]


def load_presets():
    raw, ok = QgsProject.instance().readEntry(SCOPE, PRESET_KEY, "")
    if not (ok and raw):
        raw, ok = QgsProject.instance().readEntry(OLD_SCOPE, PRESET_KEY, "")
    try:
        data = json.loads(raw) if ok and raw else {}
    except (TypeError, ValueError):
        data = {}
    return data if isinstance(data, dict) else {}


def save_presets(data):
    QgsProject.instance().writeEntry(SCOPE, PRESET_KEY, json.dumps(data))


class CreateFeaturesPane(QDockWidget):
    """The Create Features dock widget."""

    def __init__(self, iface):
        super().__init__(tr("Create Features"), iface.mainWindow())
        self.setObjectName(OBJECT_NAME)
        self.iface = iface
        self.win = iface.mainWindow()
        self.layers = []           # layers whose signals are connected
        self._slots = {}           # layer id -> feature-added slot
        self.active = None         # (layer, key) of the chosen template
        self.tool_buttons = []

        body = QWidget()
        box = QVBoxLayout(body)
        box.setContentsMargins(4, 4, 4, 4)
        box.setSpacing(4)
        self.search = QLineEdit(placeholderText=tr("Search templates"),
                                clearButtonEnabled=True)
        self.search.addAction(pro_icon("search"),
                              QLineEdit.ActionPosition.LeadingPosition)
        self.search.textChanged.connect(self._filter)
        box.addWidget(self.search)

        split = QSplitter(Qt.Orientation.Vertical)
        self.list = QListWidget()
        self.list.setIconSize(QSize(24, 24))
        self.list.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection)
        self.list.itemClicked.connect(self._template_clicked)
        split.addWidget(self.list)

        lower = QWidget()
        low = QVBoxLayout(lower)
        low.setContentsMargins(0, 0, 0, 0)
        self.heading = QLabel(objectName="WorkspacePaneHeading")
        self.heading.setWordWrap(True)
        low.addWidget(self.heading)
        self.tools = QFrame()
        self.tools_grid = QGridLayout(self.tools)
        self.tools_grid.setContentsMargins(0, 0, 0, 0)
        self.tools_grid.setSpacing(2)
        low.addWidget(self.tools)
        low.addWidget(QLabel(tr("Attributes for new features")))
        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels([tr("Field"), tr("Value")])
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.itemChanged.connect(self._preset_edited)
        low.addWidget(self.table, 1)
        self.hint = QLabel(tr("Pick a template, then draw on the map. "
                              "Values entered here go into empty fields of "
                              "each new feature."))
        self.hint.setWordWrap(True)
        low.addWidget(self.hint)
        split.addWidget(lower)
        split.setStretchFactor(0, 2)
        split.setStretchFactor(1, 3)
        box.addWidget(split, 1)
        self.setWidget(body)

        self.refresh_timer = QTimer(self, singleShot=True, interval=150)
        self.refresh_timer.timeout.connect(self.refresh)
        project = QgsProject.instance()
        project.layersAdded.connect(self._watch)
        project.layersRemoved.connect(self._schedule)
        project.readProject.connect(self._schedule)
        project.cleared.connect(self._schedule)
        root = project.layerTreeRoot()
        root.layerOrderChanged.connect(self._schedule)
        self._watch(project.mapLayers().values())
        self.visibilityChanged.connect(
            lambda shown: shown and self._schedule())
        self.refresh()

    # ------------------------------------------------------------ layers
    def _watch(self, layers):
        for layer in layers:
            if isinstance(layer, QgsVectorLayer) and layer not in self.layers:
                layer.rendererChanged.connect(self._schedule)
                slot = (lambda fid, lyr=layer: self._feature_added(lyr, fid))
                layer.featureAdded.connect(slot)
                self._slots[layer.id()] = slot
                self.layers.append(layer)
        self._schedule()

    def _schedule(self, *_):
        if not sip.isdeleted(self.refresh_timer):
            self.refresh_timer.start()

    def stop(self):
        project = QgsProject.instance()
        for signal, slot in ((project.layersAdded, self._watch),
                             (project.layersRemoved, self._schedule),
                             (project.readProject, self._schedule),
                             (project.cleared, self._schedule)):
            try:
                signal.disconnect(slot)
            except (TypeError, RuntimeError):
                pass
        try:
            project.layerTreeRoot().layerOrderChanged.disconnect(
                self._schedule)
        except (TypeError, RuntimeError):
            pass
        for layer in self.layers:
            if sip.isdeleted(layer):
                continue
            for signal, slot in ((layer.rendererChanged, self._schedule),
                                 (layer.featureAdded,
                                  self._slots.get(layer.id()))):
                if slot is None:
                    continue
                try:
                    signal.disconnect(slot)
                except (TypeError, RuntimeError):
                    pass
        self.layers = []
        self._slots = {}

    # ---------------------------------------------------------- templates
    def _ordered_layers(self):
        root = QgsProject.instance().layerTreeRoot()
        out = []
        for node in root.findLayers():
            layer = node.layer()
            if (isinstance(layer, QgsVectorLayer) and layer.isSpatial()
                    and layer.supportsEditing() and _geometry(layer)):
                out.append(layer)
        return out

    def refresh(self):
        if sip.isdeleted(self) or not self.isVisible():
            return
        keep = self.active[1] if self.active else None
        self.list.clear()
        presets = load_presets()
        target = None
        for layer in self._ordered_layers():
            for name, icon, base in _templates(layer):
                key = f"{layer.id()}|{name}"
                item = QListWidgetItem(icon, name)
                item.setToolTip(tr("{} ({})").format(name, layer.name()))
                item.setData(LAYER_ROLE, layer.id())
                item.setData(KEY_ROLE, key)
                merged = dict(base)
                merged.update(presets.get(key, {}))
                item.setData(PRESET_ROLE, merged)
                if name != layer.name():
                    item.setText(f"{name}  —  {layer.name()}")
                self.list.addItem(item)
                if key == keep:
                    target = item
        if target is not None:
            self.list.setCurrentItem(target)
        self._filter(self.search.text())
        if not self.list.count():
            self.heading.setText(tr("No editable layers in the map."))

    def _filter(self, text):
        text = (text or "").lower()
        for i in range(self.list.count()):
            item = self.list.item(i)
            item.setHidden(bool(text) and text not in item.text().lower())

    def _template_clicked(self, item):
        layer = QgsProject.instance().mapLayer(item.data(LAYER_ROLE))
        if layer is None:
            return
        self.active = (layer, item.data(KEY_ROLE))
        self.iface.setActiveLayer(layer)
        if not layer.isEditable() and layer.supportsEditing():
            layer.startEditing()
        self.heading.setText(f"<b>{item.text()}</b>")
        self._show_tools(layer)
        self._show_presets(layer, item.data(PRESET_ROLE) or {})
        if self.tool_buttons:
            self.tool_buttons[0].click()

    # --------------------------------------------------------------- tools
    def _show_tools(self, layer):
        while self.tools_grid.count():
            widget = self.tools_grid.takeAt(0).widget()
            if widget is not None:
                widget.deleteLater()
        self.tool_buttons = []
        kind = _geometry(layer)
        tools = {"point": POINT_TOOLS, "line": LINE_TOOLS,
                 "polygon": POLYGON_TOOLS}.get(kind, [])
        for i, (label, icon, how) in enumerate(tools):
            button = QToolButton(autoRaise=True, checkable=True,
                                 text=label, toolTip=label)
            button.setIcon(pro_icon(icon))
            button.setIconSize(QSize(24, 24))
            button.setToolButtonStyle(
                Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
            button.setAutoExclusive(True)
            button.clicked.connect(lambda _c, h=how: self._start_tool(h))
            self.tools_grid.addWidget(button, i // 4, i % 4)
            self.tool_buttons.append(button)

    def _action(self, name):
        return self.win.findChild(QAction, name)

    def _shape_action(self, kind):
        from qgis.PyQt.QtWidgets import QToolBar
        bar = self.win.findChild(QToolBar, "mShapeDigitizeToolBar")
        for button in (bar.findChildren(QToolButton) if bar else []):
            if button.menu():
                for action in button.menu().actions():
                    if str(action.data()).startswith(kind):
                        return action
        return None

    def _start_tool(self, how):
        if self.active is None:
            return
        layer = self.active[0]
        if sip.isdeleted(layer):
            return
        self.iface.setActiveLayer(layer)
        tracing = self._action("EnableTracingAction")
        for step in how:
            if step == "trace":
                if tracing is not None and not tracing.isChecked():
                    tracing.trigger()
                continue
            if step.startswith("shape:"):
                action = self._shape_action(step[6:])
            else:
                action = self._action(step)
            if action is None or not action.isEnabled():
                continue
            if action.isCheckable() and action.isChecked() \
                    and step != "mActionAddFeature":
                continue
            action.trigger()
        if "trace" not in how and tracing is not None and tracing.isChecked():
            tracing.trigger()   # a plain line tool does not trace

    # ------------------------------------------------------------- presets
    def _show_presets(self, layer, presets):
        self.table.blockSignals(True)
        self.table.setRowCount(0)
        for field in layer.fields():
            if field.name().lower() in ("fid", "ogc_fid", "objectid"):
                continue
            row = self.table.rowCount()
            self.table.insertRow(row)
            name = QTableWidgetItem(field.displayName())
            name.setData(Qt.ItemDataRole.UserRole, field.name())
            name.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self.table.setItem(row, 0, name)
            self.table.setItem(row, 1, QTableWidgetItem(
                str(presets.get(field.name(), ""))))
        self.table.blockSignals(False)

    def _preset_edited(self, item):
        if self.active is None or item.column() != 1:
            return
        field = self.table.item(item.row(), 0).data(Qt.ItemDataRole.UserRole)
        data = load_presets()
        key = self.active[1]
        entry = data.setdefault(key, {})
        value = item.text().strip()
        if value:
            entry[field] = value
        else:
            entry.pop(field, None)
        save_presets(data)
        current = self.list.currentItem()
        if current is not None and current.data(KEY_ROLE) == key:
            merged = dict(current.data(PRESET_ROLE) or {})
            if value:
                merged[field] = value
            else:
                merged.pop(field, None)
            current.setData(PRESET_ROLE, merged)

    def _current_presets(self):
        current = self.list.currentItem()
        if current is None or self.active is None \
                or current.data(KEY_ROLE) != self.active[1]:
            return {}
        return current.data(PRESET_ROLE) or {}

    def _feature_added(self, layer, fid):
        if self.active is None or self.active[0] is not layer:
            return
        presets = self._current_presets()
        if presets:
            QTimer.singleShot(0, lambda: self._apply(layer, fid, presets))

    def _apply(self, layer, fid, presets):
        if sip.isdeleted(layer) or not layer.isEditable():
            return
        feature = layer.getFeature(fid)
        if not feature.isValid():
            return
        layer.beginEditCommand(tr("Template attributes"))
        for field, value in presets.items():
            idx = layer.fields().lookupField(field)
            if idx < 0:
                continue
            old = feature.attribute(idx)
            if old is None or str(old) in ("", "NULL"):
                layer.changeAttributeValue(fid, idx, value)
        layer.endEditCommand()


def create_features_pane(iface, changes):
    """Create the pane, tabbed with the Catalog on the right."""
    win = iface.mainWindow()
    pane = CreateFeaturesPane(iface)
    win.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, pane)
    catalog = win.findChild(QDockWidget, "Browser")
    if catalog is not None and not catalog.isFloating():
        win.tabifyDockWidget(catalog, pane)
    pane.hide()

    def remove():
        pane.stop()
        win.removeDockWidget(pane)
        pane.deleteLater()

    changes.add(remove)
    return pane


def show_pane(win, name):
    """Show and raise one of the workspace panes."""
    pane = win.findChild(QDockWidget, name)
    if pane is not None:
        pane.show()
        pane.raise_()
        refresh = getattr(pane, "refresh", None)
        if callable(refresh):
            refresh()
    return pane
