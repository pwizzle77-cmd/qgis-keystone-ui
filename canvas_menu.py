"""Right-click menu on the map canvas.

QGIS already shows that menu for pan, zoom and select, and emits
``contextMenuAboutToShow`` before it opens. Digitizing tools that use the
right-click themselves never emit it, so this does not replace the map tool
or filter canvas events.

Feature actions are only added for the active vector layer when it has a
geometry (no-geometry tables are left out). The feature under the cursor is
the target; a selected feature there wins over a nearer one.
"""

import base64
import re

from qgis.core import (
    Qgis, QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsFeature,
    QgsFeatureRequest, QgsGeometry, QgsMessageLog, QgsPointXY, QgsProject,
    QgsRectangle, QgsVectorLayer, QgsWkbTypes,
)
from qgis.gui import QgsMapTool
from qgis.PyQt import sip
from qgis.PyQt.QtCore import QObject
from qgis.PyQt.QtGui import QAction
from qgis.PyQt.QtWidgets import QApplication, QInputDialog

from .explore import _label
from .util import pro_icon, tr

SCRATCH_NAME = "Scratch"
OTHER_LIMIT = 8
_HEX = re.compile(r"^(?:0x|\\x)?([0-9A-Fa-f]+)$")
_SRID = re.compile(r"^SRID\s*=\s*(\d+)\s*;(.*)$", re.IGNORECASE | re.DOTALL)


def has_geometry(layer):
    """True for a vector layer whose features have a point, line or polygon."""
    if not isinstance(layer, QgsVectorLayer) or sip.isdeleted(layer):
        return False
    return layer.geometryType() in (
        Qgis.GeometryType.Point, Qgis.GeometryType.Line,
        Qgis.GeometryType.Polygon)


def choose_feature(layer, hits):
    """The hit to edit: nearest selected feature, else the nearest hit."""
    selected = set(layer.selectedFeatureIds())
    for feature in hits:
        if feature.id() in selected:
            return feature
    return hits[0] if hits else None


def features_on_layer(canvas, layer, map_point, radius=None):
    """Features of ``layer`` within ``radius`` map units of ``map_point``.

    ``map_point`` is in the canvas CRS. Nearest first. A canvas that has not
    been shown yet reports a zero pixel radius; then 2% of the extent is used.
    """
    if not has_geometry(layer) or map_point is None:
        return []
    if radius is None:
        try:
            radius = QgsMapTool.searchRadiusMU(canvas)
        except Exception as err:  # noqa: BLE001
            QgsMessageLog.logMessage(
                f"canvas_menu: search radius failed: {err!r}",
                "Keystone", Qgis.MessageLevel.Info)
            radius = 0
        if not radius or radius <= 0:
            extent = canvas.extent()
            span = max(extent.width(), extent.height(), 1.0)
            radius = span * 0.02
    area = QgsRectangle(
        map_point.x() - radius, map_point.y() - radius,
        map_point.x() + radius, map_point.y() + radius)
    dest = canvas.mapSettings().destinationCrs()
    try:
        to_layer = QgsCoordinateTransform(
            dest, layer.crs(), QgsProject.instance())
        rect = to_layer.transformBoundingBox(area)
        probe = QgsGeometry.fromPointXY(QgsPointXY(map_point))
        probe.transform(to_layer)
        tol = max(rect.width(), rect.height()) / 2
    except Exception as err:  # noqa: BLE001 - an odd CRS
        QgsMessageLog.logMessage(
            f"canvas_menu: skipping {layer.name()}: {err!r}",
            "Keystone", Qgis.MessageLevel.Info)
        return []
    hits = []
    for feature in layer.getFeatures(QgsFeatureRequest().setFilterRect(rect)):
        geom = feature.geometry()
        if geom is None or geom.isEmpty():
            continue
        distance = geom.distance(probe)
        if distance <= tol:
            hits.append((distance, feature))
    hits.sort(key=lambda item: item[0])
    return [feature for _distance, feature in hits]


def _wkb_geometry(data):
    geom = QgsGeometry()
    geom.fromWkb(data)
    if geom.isNull() or geom.isEmpty():
        return None
    return geom


def parse_geometry(payload):
    """``(geometry, EPSG code or None)`` from WKT, EWKT or WKB text/bytes.

    Accepts a hex string (``0101…``, ``0x0101…``), Postgres ``\\x`` bytea hex,
    and base64 WKB. ``(None, None)`` when nothing in the text is a geometry.
    """
    if payload is None:
        return None, None
    if isinstance(payload, (bytes, bytearray, memoryview)):
        return _wkb_geometry(bytes(payload)), None
    text = str(payload).strip()
    if not text:
        return None, None
    srid = None
    found = _SRID.match(text)
    if found:
        srid = int(found.group(1))
        text = found.group(2).strip()
    compact = re.sub(r"\s+", "", text)
    hex_body = _HEX.match(compact)
    if hex_body and len(hex_body.group(1)) >= 10 and len(hex_body.group(1)) % 2 == 0:
        geom = _wkb_geometry(bytes.fromhex(hex_body.group(1)))
        if geom is not None:
            return geom, srid
    geom = QgsGeometry.fromWkt(text)
    if geom is not None and not geom.isNull() and not geom.isEmpty():
        return geom, srid
    try:
        raw = base64.b64decode(compact, validate=True)
    except Exception:  # noqa: BLE001 - not base64
        raw = b""
    if raw:
        geom = _wkb_geometry(raw)
        if geom is not None:
            return geom, srid
    return None, None


def _crs_for(srid, active):
    if srid:
        crs = QgsCoordinateReferenceSystem(f"EPSG:{srid}")
        if crs.isValid():
            return crs
    if has_geometry(active) and active.crs().isValid():
        return active.crs()
    return QgsCoordinateReferenceSystem("EPSG:4326")


def _same_crs(left, right):
    if left == right:
        return True
    return (left.isValid() and right.isValid()
            and left.authid() and left.authid() == right.authid())


def _memory_layer(wkb_type, crs, name):
    kind = QgsWkbTypes.displayString(wkb_type)
    auth = crs.authid() or "EPSG:4326"
    layer = QgsVectorLayer(f"{kind}?crs={auth}", name, "memory")
    if not layer.isValid():
        return None
    return layer


def _unique_name(project, base):
    names = {layer.name() for layer in project.mapLayers().values()}
    if base not in names:
        return base
    number = 2
    while f"{base} {number}" in names:
        number += 1
    return f"{base} {number}"


def _scratch_target(project, active, wkb_type, crs):
    """A memory layer of this type and CRS to append to, or None."""
    def matches(layer):
        return (isinstance(layer, QgsVectorLayer)
                and not sip.isdeleted(layer)
                and layer.providerType() == "memory"
                and layer.wkbType() == wkb_type
                and _same_crs(layer.crs(), crs))

    if matches(active):
        return active
    for layer in project.mapLayers().values():
        if matches(layer) and (
                layer.name() == SCRATCH_NAME
                or layer.name().startswith(SCRATCH_NAME + " ")):
            return layer
    return None


def copy_to_scratch(project, layer, features):
    """New memory layer with ``features`` cloned from ``layer``. None if empty."""
    clones = [feature for feature in features
              if feature is not None and feature.hasGeometry()
              and not feature.geometry().isEmpty()]
    if not clones or not has_geometry(layer):
        return None
    scratch = _memory_layer(
        layer.wkbType(), layer.crs(),
        _unique_name(project, f"{layer.name()} scratch"))
    if scratch is None:
        return None
    scratch.dataProvider().addAttributes(list(layer.fields()))
    scratch.updateFields()
    out = []
    for feature in clones:
        copy = QgsFeature(scratch.fields())
        copy.setGeometry(feature.geometry())
        copy.setAttributes(feature.attributes())
        out.append(copy)
    ok, added = scratch.dataProvider().addFeatures(out)
    if not ok or not added:
        return None
    scratch.updateExtents()
    project.addMapLayer(scratch)
    return scratch


def paste_geometry(project, canvas, payload, active):
    """Append one geometry to a matching scratch layer, or create one.

    Returns ``(layer, feature id)`` or ``(None, None)``.
    """
    geom, srid = parse_geometry(payload)
    if geom is None:
        return None, None
    crs = _crs_for(srid, active)
    layer = _scratch_target(project, active, geom.wkbType(), crs)
    created = layer is None
    if created:
        layer = _memory_layer(
            geom.wkbType(), crs, _unique_name(project, SCRATCH_NAME))
        if layer is None:
            return None, None
    feature = QgsFeature(layer.fields())
    feature.setGeometry(geom)
    ok, added = layer.dataProvider().addFeatures([feature])
    if not ok or not added:
        return None, None
    layer.updateExtents()
    if created:
        project.addMapLayer(layer)
    fid = added[0].id()
    _frame(canvas, layer, fid)
    return layer, fid


def _frame(canvas, layer, fid):
    if canvas is None or sip.isdeleted(canvas):
        return
    layers = list(canvas.layers())
    if layer not in layers:
        canvas.setLayers(layers + [layer])
    layer.selectByIds([fid])
    try:
        canvas.zoomToFeatureIds(layer, [fid])
    except Exception as err:  # noqa: BLE001
        QgsMessageLog.logMessage(
            f"canvas_menu: zoom failed: {err!r}",
            "Keystone", Qgis.MessageLevel.Info)
        canvas.setExtent(layer.extent())
    canvas.refresh()


def read_clipboard():
    """Clipboard text, or raw bytes when the clipboard has no text."""
    clipboard = QApplication.clipboard()
    text = clipboard.text()
    if text and text.strip():
        return text
    mime = clipboard.mimeData()
    if mime is None:
        return ""
    raw = bytes(mime.data("application/octet-stream"))
    return raw or ""


def _iface_action(iface, method_name, object_name):
    method = getattr(iface, method_name, None)
    found = method() if callable(method) else None
    if isinstance(found, QAction):
        return found
    window = iface.mainWindow() if callable(getattr(iface, "mainWindow", None)) else None
    if window is None:
        return None
    return window.findChild(QAction, object_name)


def _vertex_action(iface):
    action = _iface_action(iface, "actionVertexTool", "mActionVertexTool")
    if action is not None:
        return action
    return _iface_action(
        iface, "actionVertexToolActiveLayer", "mActionVertexToolActiveLayer")


def _trigger(action):
    """Fire a QGIS tool action. A tool that is already on stays on."""
    if action is None or not action.isEnabled():
        return
    if action.isCheckable() and action.isChecked():
        return
    action.trigger()


class CanvasMenu(QObject):
    """Adds Keystone actions to the map canvas context menu."""

    def __init__(self, iface):
        canvas = iface.mapCanvas()
        super().__init__(canvas)
        self.iface = iface
        self.canvas = canvas
        canvas.contextMenuAboutToShow.connect(self.dress)

    def stop(self):
        try:
            self.canvas.contextMenuAboutToShow.disconnect(self.dress)
        except (TypeError, RuntimeError) as err:
            QgsMessageLog.logMessage(
                f"canvas_menu.stop: {err!r}",
                "Keystone", Qgis.MessageLevel.Info)
        self.deleteLater()

    def dress(self, menu, event):
        layer = self.iface.activeLayer()
        point = event.mapPoint() if event is not None else None
        hits = features_on_layer(self.canvas, layer, point) if has_geometry(layer) else []
        target = choose_feature(layer, hits) if hits else None
        first = menu.actions()[0] if menu.actions() else None
        actions = self._actions(menu, layer, hits, target)
        for action in actions:
            menu.insertAction(first, action)
        if actions:
            menu.insertSeparator(first)

    def _actions(self, menu, layer, hits, target):
        actions = []
        if has_geometry(layer):
            actions.extend(self._feature_actions(menu, layer, hits, target))
        paste = QAction(pro_icon("paste"), tr("Paste Geometry"), menu)
        paste.setToolTip(tr(
            "Paste WKT, EWKT or hex WKB from the clipboard into a scratch "
            "layer. A matching scratch layer is reused."))
        paste.triggered.connect(lambda _on=False: self._paste())
        actions.append(paste)
        return actions

    def _feature_actions(self, menu, layer, hits, target):
        actions = []
        editing = QAction(pro_icon("edit"), tr(
            "Stop Editing" if layer.isEditable() else "Start Editing"), menu)
        if not layer.supportsEditing():
            editing.setEnabled(False)
            editing.setToolTip(tr("This layer cannot be edited."))
        editing.triggered.connect(
            lambda _on=False, lay=layer: self._toggle_editing(lay))
        actions.append(editing)

        attributes = QAction(pro_icon("attribute_table"), tr("Edit Attributes"), menu)
        vertices = QAction(pro_icon("vertex_tool"), tr("Edit Vertices"), menu)
        reshape = QAction(pro_icon("reshape"), tr("Reshape Features"), menu)
        if target is None:
            for action in (attributes, vertices, reshape):
                action.setEnabled(False)
                action.setToolTip(tr("Right-click a feature on the active layer."))
        else:
            label = _label(layer, target)
            attributes.setToolTip(tr("Open the form for {0}.").format(label))
            vertices.setToolTip(tr("Edit vertices of {0}.").format(label))
            attributes.triggered.connect(
                lambda _on=False, lay=layer, feat=target: self._attributes(lay, feat))
            vertices.triggered.connect(
                lambda _on=False, lay=layer, feat=target: self._vertices(lay, feat))
        point = layer.geometryType() == Qgis.GeometryType.Point
        reshape_action = _iface_action(
            self.iface, "actionReshapeFeatures", "mActionReshapeFeatures")
        if point:
            reshape.setEnabled(False)
            reshape.setToolTip(tr(
                "Reshape works on lines and polygons, not points."))
        elif reshape_action is None:
            reshape.setEnabled(False)
            reshape.setToolTip(tr("This QGIS build has no Reshape tool."))
        elif target is not None:
            reshape.setToolTip(tr("Reshape {0}.").format(_label(layer, target)))
            reshape.triggered.connect(
                lambda _on=False, lay=layer, feat=target: self._reshape(lay, feat))
        actions.extend((attributes, vertices, reshape))

        copy = QAction(pro_icon("copy"), tr("Copy to Scratch Layer"), menu)
        selected = (list(layer.getSelectedFeatures())
                    if layer.selectedFeatureCount() else [])
        clones = selected or ([target] if target is not None else [])
        if not clones:
            copy.setEnabled(False)
            copy.setToolTip(tr("Select a feature, or right-click one."))
        else:
            copy.setToolTip(tr("Copy {0} feature(s) into a new scratch layer.")
                            .format(len(clones)))
            copy.triggered.connect(
                lambda _on=False, lay=layer, feats=clones: self._copy(lay, feats))
        actions.append(copy)

        others = [feature for feature in hits
                  if target is None or feature.id() != target.id()]
        for feature in others[:OTHER_LIMIT]:
            label = _label(layer, feature)
            pick = QAction(tr("Select {0}").format(label), menu)
            pick.triggered.connect(
                lambda _on=False, lay=layer, feat=feature: self._select(lay, feat))
            actions.append(pick)
        return actions

    def _toggle_editing(self, layer):
        if sip.isdeleted(layer):
            return
        self.iface.setActiveLayer(layer)
        _trigger(_iface_action(
            self.iface, "actionToggleEditing", "mActionToggleEditing"))

    def _select(self, layer, feature):
        if sip.isdeleted(layer):
            return
        self.iface.setActiveLayer(layer)
        layer.selectByIds([feature.id()])

    def _prepare(self, layer, feature):
        if sip.isdeleted(layer):
            return False
        self.iface.setActiveLayer(layer)
        layer.selectByIds([feature.id()])
        if not layer.isEditable() and not layer.startEditing():
            return False
        return True

    def _attributes(self, layer, feature):
        if sip.isdeleted(layer):
            return
        self._select(layer, feature)
        self.iface.openFeatureForm(layer, feature)

    def _vertices(self, layer, feature):
        if not self._prepare(layer, feature):
            return
        _trigger(_vertex_action(self.iface))

    def _reshape(self, layer, feature):
        if layer.geometryType() == Qgis.GeometryType.Point:
            return
        action = _iface_action(
            self.iface, "actionReshapeFeatures", "mActionReshapeFeatures")
        if action is None:
            self.iface.messageBar().pushMessage(
                tr("Keystone"),
                tr("This QGIS build has no Reshape tool."),
                Qgis.MessageLevel.Info, 6)
            return
        if not self._prepare(layer, feature):
            return
        _trigger(action)

    def _copy(self, layer, features):
        scratch = copy_to_scratch(QgsProject.instance(), layer, features)
        if scratch is None:
            self.iface.messageBar().pushMessage(
                tr("Keystone"),
                tr("Nothing with a geometry was copied."),
                Qgis.MessageLevel.Warning, 6)

    def _paste(self):
        payload = read_clipboard()
        geom, _srid = parse_geometry(payload)
        if geom is None:
            text, ok = QInputDialog.getMultiLineText(
                self.iface.mainWindow(), tr("Paste geometry"),
                tr("The clipboard has no WKT or WKB. Paste a geometry:"))
            if not ok:
                return
            payload = text
        layer, _fid = paste_geometry(
            QgsProject.instance(), self.canvas, payload,
            self.iface.activeLayer())
        if layer is None:
            self.iface.messageBar().pushMessage(
                tr("Keystone"),
                tr("That text is not WKT, EWKT or WKB."),
                Qgis.MessageLevel.Warning, 6)


def canvas_menu(iface, changes):
    menu = CanvasMenu(iface)
    changes.add(menu.stop)
    return menu
