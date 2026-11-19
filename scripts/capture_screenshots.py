"""Take the README screenshots of Keystone.

Run in the QGIS Python console (Plugins > Python Console):

    exec(open(r"<path to this file>").read())

It needs an empty or saved project (it starts a new one with a small demo
dataset over OpenStreetMap), switches Keystone on if needed, steps
through the tabs and saves PNGs to docs/images next to this script's
plugin folder. QGIS must be in front and not covered while it runs (about
40 seconds); it prints DONE at the end.
"""

import os
import qgis.utils
from qgis.core import (
    QgsCoordinateReferenceSystem, QgsFeature, QgsGeometry, QgsPointXY,
    QgsProject, QgsRasterLayer, QgsSettings, QgsVectorLayer,
)
from qgis.PyQt.QtCore import QPoint, QTimer
from qgis.PyQt.QtGui import QColor, QPainter
from qgis.PyQt.QtWidgets import QApplication, QTabWidget, QToolButton

PLUGIN = "keystone"
HERE = os.path.dirname(os.path.abspath(
    globals().get("__file__", r"E:\OneDrive - Columbia County Board of County Commissioners\Repo\QGIS Plugins\keystone\scripts\capture_screenshots.py")))
OUT = os.path.join(os.path.dirname(HERE), "docs", "images")
os.makedirs(OUT, exist_ok=True)
_win = iface.mainWindow()
_project = QgsProject.instance()
_steps = []
_state = {}


def _shot(name, widget=None, popup=None):
    """Picture of the window itself (other windows on screen are never in
    it); an open popup menu is painted in at its place."""
    QApplication.processEvents()
    widget = widget or _win
    pix = widget.grab()
    if popup is not None and popup.isVisible():
        painter = QPainter(pix)
        painter.drawPixmap(widget.mapFromGlobal(popup.mapToGlobal(QPoint(0, 0))),
                           popup.grab())
        painter.end()
    path = os.path.join(OUT, name + ".png")
    pix.save(path)
    print("saved", path)


def _ribbon():
    return _win.findChild(QTabWidget, "ArcSkinRibbon")


def _select_tab(title):
    tabs = _ribbon()
    for i in range(tabs.count()):
        if tabs.tabText(i).replace("&", "") == title:
            tabs.setCurrentIndex(i)
            return True
    print("no tab", title)
    return False


def _button(text):
    tabs = _ribbon()
    page = tabs.currentWidget()
    for b in page.findChildren(QToolButton):
        if b.text().replace("\n", " ") == text:
            return b
    return None


def _demo_data():
    _project.clear()
    _project.setTitle("Keystone Demo")
    _project.setCrs(QgsCoordinateReferenceSystem("EPSG:3857"))
    osm = QgsRasterLayer(
        "type=xyz&url=https://tile.openstreetmap.org/{z}/{x}/{y}.png"
        "&zmax=19&zmin=0", "OpenStreetMap", "wms")
    _project.addMapLayer(osm)
    roads = QgsVectorLayer("LineString?crs=EPSG:4326&field=FullName:string",
                           "Road Centerlines", "memory")
    parcels = QgsVectorLayer(
        "Polygon?crs=EPSG:4326&field=ParcelID:string&field=Acres:double",
        "Parcels", "memory")
    x0, y0, dx, dy = -82.6420, 30.1880, 0.0016, 0.0013
    feats = []
    n = 0
    for row in range(3):
        for col in range(5):
            n += 1
            x = x0 + col * dx + (0.0003 if col > 2 else 0)
            y = y0 + row * dy
            ring = [QgsPointXY(x, y), QgsPointXY(x + dx * .95, y),
                    QgsPointXY(x + dx * .95, y + dy * .92),
                    QgsPointXY(x, y + dy * .92), QgsPointXY(x, y)]
            f = QgsFeature(parcels.fields())
            f.setGeometry(QgsGeometry.fromPolygonXY([ring]))
            f.setAttributes([f"{n:05d}-000", 1.2])
            feats.append(f)
    parcels.dataProvider().addFeatures(feats)
    r = QgsFeature(roads.fields())
    r.setGeometry(QgsGeometry.fromPolylineXY(
        [QgsPointXY(x0 - .001, y0 - .0004),
         QgsPointXY(x0 + 5 * dx + .0013, y0 - .0004)]))
    r.setAttributes(["NW Main St"])
    roads.dataProvider().addFeatures([r])
    _project.addMapLayers([parcels, roads])
    sym = parcels.renderer().symbol()
    sym.setColor(QColor(255, 222, 140, 150))
    sym.symbolLayer(0).setStrokeColor(QColor(150, 90, 20))
    parcels.triggerRepaint()
    _state["parcels"] = parcels
    _state["osm"] = osm
    iface.setActiveLayer(osm)
    canvas = iface.mapCanvas()
    extent = canvas.mapSettings().layerExtentToOutputExtent(
        parcels, parcels.extent())
    extent.scale(1.8)
    canvas.setExtent(extent)
    canvas.refresh()


def _ensure_plugin():
    if PLUGIN not in qgis.utils.plugins:
        qgis.utils.loadPlugin(PLUGIN)
        qgis.utils.startPlugin(PLUGIN)
        QgsSettings().setValue("PythonPlugins/" + PLUGIN, True)
    plugin = qgis.utils.plugins[PLUGIN]
    if not plugin.action.isChecked():
        plugin.action.trigger()


def _run():
    if not _steps:
        print("DONE")
        return
    delay, step = _steps.pop(0)
    try:
        step()
    except Exception as e:  # noqa: BLE001
        print("step failed:", e)
    QTimer.singleShot(delay, _run)


def step(delay=1500):
    def deco(fn):
        _steps.append((delay, fn))
        return fn
    return deco


if _project.isDirty():
    print("The current project has unsaved changes: save it first, "
          "then run this again.")
else:
    @step(2500)
    def _s0():
        _win.showMaximized()
        _ensure_plugin()

    @step(6000)
    def _s1():
        _demo_data()

    for _title, _name in [("Map", "tab-map"), ("Edit", "tab-edit"),
                          ("Layout", "tab-layout"), ("Insert", "tab-insert"),
                          ("View", "tab-view"), ("Parcels", "tab-parcels"),
                          ("Addressing", "tab-addressing")]:
        _steps.append((300, lambda t=_title: _select_tab(t)))
        _steps.append((1200, lambda n=_name: _shot(n)))

    @step(1500)
    def _c1():
        iface.setActiveLayer(_state["parcels"])
        _select_tab("Appearance")

    @step(1200)
    def _c2():
        _shot("contextual-feature-layer")

    @step(1200)
    def _c3():
        _select_tab("Data")

    @step(1200)
    def _c4():
        _shot("contextual-data")

    @step(400)
    def _g1():
        _select_tab("Layout")
        button = _button("New Layout")
        if button:
            QTimer.singleShot(1500, lambda: _shot("new-layout-gallery",
                                                  popup=button.menu()))
            QTimer.singleShot(2500, lambda: button.menu().close())
            button.showMenu()

    @step(1200)
    def _k1():
        _select_tab("Map")
        qgis.utils.plugins[PLUGIN].ribbon.commands[
            "collapse_ribbon"].setChecked(True)

    @step(1200)
    def _k2():
        _shot("collapsed-ribbon")

    @step(800)
    def _k3():
        qgis.utils.plugins[PLUGIN].ribbon.commands[
            "collapse_ribbon"].setChecked(False)

    @step(7000)
    def _l1():
        from keystone.layouts import build_layout
        iface.setActiveLayer(_state["osm"])
        build_layout(iface, "Tabloid (ANSI B)", 11 * 25.4, 17 * 25.4, True,
                     True)

    @step(1500)
    def _l2():
        designers = iface.openLayoutDesigners()
        if designers:
            window = designers[-1].window()
            window.showMaximized()
            QApplication.processEvents()
            QTimer.singleShot(4000, lambda: _shot("layout-map-sheet", window))

    @step(6000)
    def _l3():
        pass

    @step(500)
    def _l4():
        for d in iface.openLayoutDesigners():
            d.close()
        _project.setDirty(False)

    _run()
