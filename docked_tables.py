"""Attribute tables docked under the map, as tabs, as in ArcGIS Pro.

QGIS opens each attribute table as its own window unless "Open attribute
table in a dock window" is set. While the workspace is on, that setting is
on, and each new table dock goes to the bottom of the main window, tabbed
with the tables already there; the newest table is in front. The table's
own Dock/Undock button still floats one table out when you want it beside
the map. Only the main window is watched (for new docks), not every
widget of the application.
"""

from qgis.core import QgsSettings
from qgis.PyQt import sip
from qgis.PyQt.QtCore import QEvent, QObject, Qt, QTimer
from qgis.PyQt.QtGui import QAction
from qgis.PyQt.QtWidgets import QDockWidget, QWidget

ON_KEY = "keystone/dock_tables"
LEGACY_KEY = "qgis/dockAttributeTable"
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
        self.on = QgsSettings().value(ON_KEY, True, type=bool)
        settings = QgsSettings()
        self._legacy = settings.value(LEGACY_KEY)
        if self.on:
            settings.setValue(LEGACY_KEY, True)
        self.win.installEventFilter(self)

    def set_on(self, on):
        self.on = bool(on)
        QgsSettings().setValue(ON_KEY, self.on)
        QgsSettings().setValue(LEGACY_KEY, self.on)

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
        if dock.isFloating() or self.win.dockWidgetArea(dock) != \
                Qt.DockWidgetArea.BottomDockWidgetArea:
            if getattr(dock, "_workspace_placed", False):
                return   # the user moved it; leave it there
            others = [d for d in self.tables() if d is not dock]
            dock.setFloating(False)
            self.win.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea,
                                   dock)
            if others:
                self.win.tabifyDockWidget(others[-1], dock)
        dock._workspace_placed = True
        dock.show()
        dock.raise_()

    def stop(self):
        if not sip.isdeleted(self.win):
            self.win.removeEventFilter(self)
        settings = QgsSettings()
        if self._legacy is None:
            settings.remove(LEGACY_KEY)
        else:
            settings.setValue(LEGACY_KEY, self._legacy)


def docked_tables(iface, changes):
    docker = TableDocker(iface)
    changes.add(docker.stop)
    return docker
