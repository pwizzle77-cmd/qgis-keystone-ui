"""Optional tool declarations shared through a main-window Qt property, without plugin imports."""
from qgis.PyQt import sip
from qgis.PyQt.QtCore import QEvent, QObject, QTimer
from qgis.PyQt.QtGui import QAction

PROPERTY = "keystone/tool_placements"


def declared_tools(win):
    registry = win.property(PROPERTY)
    if not isinstance(registry, dict):
        return []
    tools = []
    for name, declaration in registry.items():
        if not isinstance(name, str) or not name or not isinstance(declaration, dict):
            continue
        if any(not isinstance(declaration.get(key, ""), str)
               for key in ("tab", "group", "modify_group", "description")):
            continue
        menus = declaration.get("menu_actions", [])
        if not isinstance(menus, list) or any(not isinstance(value, str) for value in menus):
            continue
        action = win.findChild(QAction, name)
        if action is not None and not action.isSeparator():
            tools.append((action, declaration))
    return tools


class PlacementWatcher(QObject):
    """Coalesce registration changes and stop callbacks before their UI is removed."""
    def __init__(self, win, callback, parent=None):
        super().__init__(parent or win)
        self.win = win
        self.timer = QTimer(self, singleShot=True, interval=0)
        self.timer.timeout.connect(callback)
        win.installEventFilter(self)

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.DynamicPropertyChange and bytes(event.propertyName()) == PROPERTY.encode():
            self.timer.start()
        return False

    def stop(self):
        if sip.isdeleted(self):
            return
        self.timer.stop()
        if not sip.isdeleted(self.win):
            self.win.removeEventFilter(self)
        self.deleteLater()
