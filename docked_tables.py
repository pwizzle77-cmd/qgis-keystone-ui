"""Attribute tables docked under the map, as tabs, as in ArcGIS Pro.

QGIS opens each attribute table as its own window unless docking is on.
QGIS 3 reads "qgis/dockAttributeTable". QGIS 4 reads
"attribute-table/attribute-table-default-docked" and ignores the old key,
so a table stayed a dialog and never reached the bottom of the window.
While the workspace is on, both are on. Each new table dock goes to the
bottom, tabbed with the tables already there; the newest is in front.
The table's own Dock/Undock button still floats one table out. Only the
main window is watched (for new docks).
"""

from qgis.core import QgsSettings
from qgis.PyQt import sip
from qgis.PyQt.QtCore import QEvent, QObject, Qt, QTimer
from qgis.PyQt.QtGui import QAction
from qgis.PyQt.QtWidgets import QDockWidget, QWidget

ON_KEY = "keystone/dock_tables"
LEGACY_KEY = "qgis/dockAttributeTable"          # QGIS 3
DOCK_KEY = "attribute-table/attribute-table-default-docked"  # QGIS 4
DIALOG_CLASS = "QgsAttributeTableDialog"


def _is_table(widget):
    try:
        return widget.metaObject().className() == DIALOG_CLASS
    except RuntimeError:
        return False


def _dock_of(widget):
    parent = widget.parentWidget()
    while parent is not None:
        if isinstance(parent, QDockWidget):
            return parent
        parent = parent.parentWidget()
    return None


class TableDocker(QObject):
    def __init__(self, iface):
        super().__init__(iface.mainWindow())
        self.win = iface.mainWindow()
        settings = QgsSettings()
        self.on = settings.value(ON_KEY, True, type=bool)
        self._saved = {
            LEGACY_KEY: settings.contains(LEGACY_KEY),
            DOCK_KEY: settings.contains(DOCK_KEY),
        }
        self._previous = {
            key: settings.value(key) for key, present in self._saved.items()
            if present
        }
        if self.on:
            self._write_docked(True)
        self.win.installEventFilter(self)

    def _write_docked(self, on):
        settings = QgsSettings()
        settings.setValue(LEGACY_KEY, bool(on))
        settings.setValue(DOCK_KEY, bool(on))

    def set_on(self, on):
        self.on = bool(on)
        QgsSettings().setValue(ON_KEY, self.on)
        self._write_docked(self.on)

    def eventFilter(self, obj, event):  # noqa: N802 (Qt API name)
        if self.on and obj is self.win \
                and event.type() == QEvent.Type.ChildAdded:
            child = event.child()
            if isinstance(child, QDockWidget):
                QTimer.singleShot(0, lambda d=child: self.place_dock(d))
        return False

    def place_dock(self, dock):
        if sip.isdeleted(dock):
            return
        table = next((w for w in dock.findChildren(QWidget)
                      if _is_table(w)), None)
        if table is not None:
            self.place(table)

    def tables(self):
        return [d for d in self.win.findChildren(QDockWidget)
                if d.isVisible() and not d.isFloating()
                and self.win.dockWidgetArea(d)
                == Qt.DockWidgetArea.BottomDockWidgetArea
                and any(_is_table(w) for w in d.findChildren(QWidget))]

    def place(self, table):
        if sip.isdeleted(table):
            return
        dock = _dock_of(table)
        if dock is None:
            toggle = table.findChild(QAction, "mActionDockUndock")
            if toggle is None or toggle.isChecked():
                return
            toggle.trigger()
            QTimer.singleShot(0, lambda: self.place(table))
            return
        if getattr(dock, "_workspace_placed", False):
            return   # the user moved it; leave it there
        bottom = Qt.DockWidgetArea.BottomDockWidgetArea
        # tables() only sees docks already on the bottom, so collect them
        # before this one is moved there.
        others = [d for d in self.tables() if d is not dock]
        if dock.isFloating() or self.win.dockWidgetArea(dock) != bottom:
            dock.setFloating(False)
            self.win.addDockWidget(bottom, dock)
        if others:
            # QGIS 4 adds each table on its own and does not tabify them.
            self.win.tabifyDockWidget(others[-1], dock)
        dock._workspace_placed = True
        dock.show()
        dock.raise_()

    def stop(self):
        if not sip.isdeleted(self.win):
            self.win.removeEventFilter(self)
        settings = QgsSettings()
        for key, present in self._saved.items():
            if present:
                settings.setValue(key, self._previous[key])
            else:
                settings.remove(key)


def docked_tables(iface, changes):
    docker = TableDocker(iface)
    changes.add(docker.stop)
    return docker
