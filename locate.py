"""Locate pane, like ArcGIS Pro's: one search box for the whole county.

Type and press Enter. Searched, in this order:

* **Coordinates** — latitude/longitude (decimal or degrees-minutes-seconds,
  latitude first as in Google Maps), MGRS / US National Grid, UTM.
* **Addresses** — the address-point layer of the NENA Addressing and RCL
  Tools plugin (its full-address field, or house number + street name),
  every word must match ("123 main" finds 123 NW Main St).
* **Streets** — the NENA centerline layer: each street name once, zooming to
  all of its segments.
* **Parcels** — polygon layers with a parcel-number field (ParcelNumber,
  ParcelID, PIN… or Name on a layer called Parcels), e.g. the Parcel Fabric
  plugin's Parcels: the number as typed, with or without the
  section-township-range part.
* **OpenStreetMap** — when nothing local matches, a button searches
  OpenStreetMap (Nominatim) for a place or address.

Click a result to zoom to it; it flashes, and a pin marks coordinates and
places.
"""

import re

from qgis.core import (
    Qgis,
    QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsExpression,
    QgsExpressionContext, QgsExpressionContextUtils,
    QgsFeatureRequest, QgsGeometry, QgsPointXY, QgsProject, QgsRectangle,
    QgsVectorLayer,
)
from qgis.gui import QgsVertexMarker
from qgis.PyQt import sip
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtWidgets import (
    QApplication, QDockWidget, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from . import coords
from .util import pro_icon, tr

OBJECT_NAME = "WorkspaceLocate"
MAX_RESULTS = 25
RESULT_ROLE = Qt.ItemDataRole.UserRole
WGS84 = QgsCoordinateReferenceSystem("EPSG:4326")
PARCEL_FIELDS = ("parcelnumber", "parcel_number", "parcelno", "parcel_no",
                 "parcelid", "parcel_id", "pin", "parcel", "strap", "apn",
                 "parcel_num")
FULL_ADDRESS_FIELDS = ("fulladdr", "full_addr", "fulladdress",
                       "full_address", "address", "siteaddress", "addr_full")
FULL_NAME_FIELDS = ("fullname", "full_name", "st_fullname", "streetname",
                    "street_name", "fullstreet", "rd_name", "label")
ADDRESS_PARTS = ("Add_Number", "AddNum_Suf", "St_PreMod", "St_PreDir",
                 "St_PreTyp", "St_PreSep", "St_Name", "St_PosTyp",
                 "St_PosDir", "St_PosMod")
STREET_PARTS = ("St_PreMod", "St_PreDir", "St_PreTyp", "St_PreSep",
                "St_Name", "St_PosTyp", "St_PosDir", "St_PosMod")


def _field(layer, names):
    """The first field of ``layer`` whose name (any case) is in ``names``."""
    lowered = {f.name().lower(): f.name() for f in layer.fields()}
    for name in names:
        if name.lower() in lowered:
            return lowered[name.lower()]
    return None


def _nena_layers():
    """(address layer, address field mapping, centerline layer) from the
    NENA Addressing and RCL Tools project settings, when that plugin is
    installed."""
    try:
        from nena_addressing_and_rcl_tools.core.project_config import (
            layer_for, load_project_config,
        )
        pcfg = load_project_config()
        return (layer_for(pcfg.streets, "address"),
                dict(pcfg.streets.address_mapping or {}),
                layer_for(pcfg.streets, "centerline"))
    except Exception:  # noqa: BLE001 - plugin missing or not set up
        return None, {}, None


def _vector_layers(geometry=None):
    for layer in QgsProject.instance().mapLayers().values():
        if isinstance(layer, QgsVectorLayer) and layer.isSpatial() and (
                geometry is None or layer.geometryType() == geometry):
            yield layer


def _address_layers():
    """The NENA address layer, else point layers with an address field."""
    layer, mapping, _ = _nena_layers()
    if isinstance(layer, QgsVectorLayer):
        return [(layer, mapping)]
    return [(lyr, {}) for lyr in _vector_layers(Qgis.GeometryType.Point)
            if _field(lyr, FULL_ADDRESS_FIELDS)
            or lyr.fields().lookupField("Add_Number") >= 0]


def _street_layers():
    """The NENA centerline layer, else line layers with a street name."""
    _, _, layer = _nena_layers()
    if isinstance(layer, QgsVectorLayer):
        return [layer]
    return [lyr for lyr in _vector_layers(Qgis.GeometryType.Line)
            if _field(lyr, FULL_NAME_FIELDS)
            or lyr.fields().lookupField("St_Name") >= 0]


def _looks_like_parcel(text):
    """One token with a digit, like 12-4S-16-02942-000 or 02942-000."""
    return bool(re.fullmatch(r"[\w.\-/]+", text.strip())) and \
        any(c.isdigit() for c in text)


def _words(text):
    return [w for w in re.split(r"\s+", text.strip().lower()) if w]


def _concat(layer, parts, mapping=None):
    """Expression joining the name parts the layer has, lower-cased."""
    mapping = mapping or {}
    present = []
    for part in parts:
        name = mapping.get(part) or part
        if layer.fields().lookupField(name) >= 0:
            present.append(f"coalesce(to_string({QgsExpression.quotedColumnRef(name)}),'')")
    if not present:
        return None
    return "lower(concat(" + ", ' ', ".join(present) + "))"


def _all_words(expression, words):
    return " AND ".join(
        f"{expression} ILIKE {QgsExpression.quotedString('%' + w + '%')}"
        for w in words)


class LocatePane(QDockWidget):
    def __init__(self, iface):
        super().__init__(tr("Locate"), iface.mainWindow())
        self.setObjectName(OBJECT_NAME)
        self.iface = iface
        self.canvas = iface.mapCanvas()
        self.marker = None
        body = QWidget()
        box = QVBoxLayout(body)
        box.setContentsMargins(4, 4, 4, 4)
        row = QHBoxLayout()
        self.search = QLineEdit(
            placeholderText=tr("Address, street, parcel, or coordinates"),
            clearButtonEnabled=True)
        self.search.addAction(pro_icon("search"),
                              QLineEdit.ActionPosition.LeadingPosition)
        self.search.returnPressed.connect(self.run)
        row.addWidget(self.search, 1)
        go = QPushButton(tr("Search"))
        go.clicked.connect(self.run)
        row.addWidget(go)
        box.addLayout(row)
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.itemClicked.connect(self.go_to)
        self.tree.itemActivated.connect(self.go_to)
        box.addWidget(self.tree, 1)
        self.status = QLabel(tr("Examples: 123 Main, Main St, 02942-000, "
                                "30.19, -82.64"))
        self.status.setWordWrap(True)
        box.addWidget(self.status)
        self.web = QPushButton(tr("Search OpenStreetMap"))
        self.web.setToolTip(tr("Look the text up on OpenStreetMap "
                               "(Nominatim). Needs an internet connection."))
        self.web.clicked.connect(self.search_web)
        self.web.hide()
        box.addWidget(self.web)
        self.setWidget(body)

    # ------------------------------------------------------------ searching
    def run(self):
        text = self.search.text().strip()
        self.tree.clear()
        self.clear_marker()
        self.web.hide()
        if not text:
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            total = 0
            for title, results in (
                    (tr("Coordinates"), self.find_coordinates(text)),
                    (tr("Addresses"), self.find_addresses(text)),
                    (tr("Streets"), self.find_streets(text)),
                    (tr("Parcels"), self.find_parcels(text))):
                if results:
                    total += len(results)
                    self._add_group(title, results)
        finally:
            QApplication.restoreOverrideCursor()
        if total:
            self.status.setText(tr("{} found. Click one to go there.")
                                .format(total))
            first = self.tree.topLevelItem(0).child(0)
            if total == 1 and first is not None:
                self.tree.setCurrentItem(first)
                self.go_to(first)
        else:
            self.status.setText(tr("Nothing found in the map's layers."))
            self.web.show()

    def _add_group(self, title, results):
        node = QTreeWidgetItem([f"{title} ({len(results)})"])
        node.setFlags(Qt.ItemFlag.ItemIsEnabled)
        for label, result in results:
            item = QTreeWidgetItem(node, [label])
            item.setData(0, RESULT_ROLE, result)
        self.tree.addTopLevelItem(node)
        node.setExpanded(True)

    def find_coordinates(self, text):
        crs = self.canvas.mapSettings().destinationCrs()
        nums = re.fullmatch(r"\s*(-?\d+(?:\.\d+)?)\s*[,;\s]\s*"
                            r"(-?\d+(?:\.\d+)?)\s*", text)
        if nums:
            a, b = float(nums.group(1)), float(nums.group(2))
            # latitude first (as in Google Maps) unless it can't be one
            lat, lon = (a, b) if abs(a) <= 90 else (b, a)
            if abs(lat) <= 90 and abs(lon) <= 180:
                point = QgsCoordinateTransform(
                    WGS84, crs, QgsProject.instance()).transform(
                        QgsPointXY(lon, lat))
                return [(f"{lat:.6f}, {lon:.6f}", ("point", point))]
            return []
        for unit in ("dms", "mgrs", "utm"):
            try:
                point = coords.parse_point(text, unit, crs)
            except Exception:  # noqa: BLE001 - not this format
                continue
            return [(text, ("point", point))]
        return []

    def _query(self, layer, expression, label, limit=MAX_RESULTS):
        request = QgsFeatureRequest().setFilterExpression(expression)
        request.setLimit(limit)
        out = []
        for feature in layer.getFeatures(request):
            out.append((label(feature), ("feature", layer.id(),
                                         [feature.id()])))
        return out

    def find_addresses(self, text):
        words = _words(text)
        if not words or not words[0][0].isdigit():
            return []   # an address search starts with a house number
        out = []
        for layer, mapping in _address_layers():
            full = _field(layer, FULL_ADDRESS_FIELDS)
            expr = (QgsExpression.quotedColumnRef(full)
                    if full else _concat(layer, ADDRESS_PARTS, mapping))
            if not expr:
                continue
            evaluator = QgsExpression(expr)

            def label(feature, layer=layer, evaluator=evaluator):
                ctx = QgsExpressionContext(
                    QgsExpressionContextUtils.globalProjectLayerScopes(layer))
                ctx.setFeature(feature)
                value = evaluator.evaluate(ctx)
                return re.sub(r"\s+", " ", str(value or "")).strip() or \
                    str(feature.id())
            out += self._query(layer, _all_words(expr, words), label)
        return out[:MAX_RESULTS]

    def find_streets(self, text):
        words = _words(text)
        if not words or words[0][0].isdigit() and len(words) > 1:
            return []   # "123 Main" is an address
        out = []
        for layer in _street_layers():
            out += self._streets_in(layer, words)
        return out[:MAX_RESULTS]

    def _streets_in(self, layer, words):
        full = _field(layer, FULL_NAME_FIELDS)
        expr = (QgsExpression.quotedColumnRef(full)
                if full else _concat(layer, STREET_PARTS))
        if not expr:
            return []
        evaluator = QgsExpression(expr)
        ctx = QgsExpressionContext(
            QgsExpressionContextUtils.globalProjectLayerScopes(layer))
        names = {}
        request = QgsFeatureRequest().setFilterExpression(
            _all_words(expr, words))
        request.setLimit(2000)
        for feature in layer.getFeatures(request):
            ctx.setFeature(feature)
            name = re.sub(r"\s+", " ", str(evaluator.evaluate(ctx) or "")) \
                .strip()
            if name:
                names.setdefault(name, []).append(feature.id())
            if len(names) > MAX_RESULTS:
                break
        return [(name.title(), ("feature", layer.id(), fids))
                for name, fids in sorted(names.items())][:MAX_RESULTS]

    def find_parcels(self, text):
        token = text.strip()
        if not _looks_like_parcel(token):
            return []
        out = []
        for layer in QgsProject.instance().mapLayers().values():
            if not isinstance(layer, QgsVectorLayer) or not layer.isSpatial():
                continue
            field = _field(layer, PARCEL_FIELDS)
            if field is None and "parcel" in layer.name().lower():
                field = _field(layer, ("name",))
            if field is None:
                continue
            column = QgsExpression.quotedColumnRef(field)
            expr = (f"to_string({column}) ILIKE "
                    f"{QgsExpression.quotedString('%' + token + '%')}")
            out += self._query(
                layer, expr,
                lambda f, fld=field, lyr=layer:
                f"{f.attribute(fld)}  ({lyr.name()})")
            if len(out) >= MAX_RESULTS:
                break
        return out[:MAX_RESULTS]

    def search_web(self):
        text = self.search.text().strip()
        if not text:
            return
        try:
            from qgis.core import QgsGeocoderContext, QgsNominatimGeocoder
        except ImportError:
            self.status.setText(tr("This QGIS has no OpenStreetMap search."))
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            geocoder = QgsNominatimGeocoder()
            context = QgsGeocoderContext(QgsProject.instance()
                                         .transformContext())
            context.setAreaOfInterest(QgsGeometry.fromRect(
                self._extent_wgs84()))
            found = geocoder.geocodeString(text, context)
        except Exception as exc:  # noqa: BLE001 - no network
            found = []
            self.status.setText(tr("OpenStreetMap search failed: {}")
                                .format(exc))
        finally:
            QApplication.restoreOverrideCursor()
        crs = self.canvas.mapSettings().destinationCrs()
        results = []
        for item in found[:MAX_RESULTS]:
            if not item.isValid():
                continue
            geom = QgsGeometry(item.geometry())
            geom.transform(QgsCoordinateTransform(item.crs(), crs,
                                                  QgsProject.instance()))
            results.append((item.identifier(),
                            ("point", geom.centroid().asPoint())))
        if results:
            self._add_group(tr("OpenStreetMap"), results)
            self.status.setText(tr("Search results from OpenStreetMap "
                                   "(© OpenStreetMap contributors)."))
            self.web.hide()
        elif not found:
            self.status.setText(tr("OpenStreetMap found nothing either."))

    def _extent_wgs84(self):
        crs = self.canvas.mapSettings().destinationCrs()
        try:
            return QgsCoordinateTransform(crs, WGS84, QgsProject.instance()) \
                .transformBoundingBox(self.canvas.extent())
        except Exception:  # noqa: BLE001
            return QgsRectangle(-180, -90, 180, 90)

    # -------------------------------------------------------------- results
    def go_to(self, item, _column=0):
        result = item.data(0, RESULT_ROLE)
        if not result:
            return
        self.clear_marker()
        if result[0] == "point":
            point = result[1]
            self.canvas.setCenter(point)
            if self.canvas.scale() > 5000:
                self.canvas.zoomScale(2400)
            self.canvas.refresh()
            self.marker = QgsVertexMarker(self.canvas)
            self.marker.setCenter(point)
            self.marker.setIconType(QgsVertexMarker.IconType.ICON_CIRCLE)
            self.marker.setColor(QColor(0, 122, 194))
            self.marker.setFillColor(QColor(0, 122, 194, 90))
            self.marker.setIconSize(18)
            self.marker.setPenWidth(3)
            return
        _kind, layer_id, fids = result
        layer = QgsProject.instance().mapLayer(layer_id)
        if layer is None:
            return
        self.canvas.zoomToFeatureIds(layer, fids)
        geometry_point = len(fids) == 1 and \
            layer.geometryType() == Qgis.GeometryType.Point
        if geometry_point or self.canvas.scale() < 600:
            self.canvas.zoomScale(1200)
        self.canvas.refresh()
        self.canvas.flashFeatureIds(layer, fids)

    def clear_marker(self):
        if self.marker is not None and not sip.isdeleted(self.marker):
            self.canvas.scene().removeItem(self.marker)
        self.marker = None

    def stop(self):
        self.clear_marker()


def locate_pane(iface, changes):
    win = iface.mainWindow()
    pane = LocatePane(iface)
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
