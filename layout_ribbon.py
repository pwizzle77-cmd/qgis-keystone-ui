"""A ribbon in the layout designer, like ArcGIS Pro's layout view.

When a layout opens, its menu bar and toolbars give way to a ribbon with
Pro's layout tabs: Layout (clipboard, navigate, page, show), Insert (map
frames, map surrounds, graphics and text), Arrange (group, order, align,
distribute, size), Map Series (QGIS's atlas) and Share (export, print,
templates). The designer's menus stay under its Layout button.

The ribbon uses the designer's own actions. Toolbars come back when the
designer closes (so QGIS remembers them) and when the workspace is off.
"""

from qgis.PyQt import sip
from qgis.PyQt.QtCore import QObject, Qt, QTimer
from qgis.PyQt.QtGui import QAction
from qgis.PyQt.QtWidgets import (
    QMainWindow, QMenu, QTabWidget, QToolBar, QToolButton,
)

from .ribbon import RibbonTabBar, _style, _tab
from .util import pro_icon, tr

# Tab -> groups -> items (names, size, caption). ``names``: designer action
# object names or "text:<action text>" with _ for spaces (item tools are made
# at run time and have no object names); a|b: the first of these that
# exists. The first found is the button, the rest its menu.
LAYOUT_RIBBON = [
    (tr("Layout"), [
        (tr("Clipboard"), [
            ("mActionPaste|text:Paste mActionPasteInPlace", "L",
             tr("Paste")),
            ("mActionCut|text:Cut", "S", tr("Cut")),
            ("mActionCopy|text:Copy", "S", tr("Copy")),
            ("mActionDeleteSelection", "S", tr("Delete")),
        ]),
        (tr("Navigate"), [
            ("mActionPan", "L", tr("Explore")),
            ("mActionSelectMoveItem", "L", tr("Select")),
            ("mActionZoomAll", "S", tr("Full Extent")),
            ("mActionZoomIn", "S", tr("Zoom In")),
            ("mActionZoomOut", "S", tr("Zoom Out")),
            ("mActionZoomActual", "S", tr("100%")),
            ("mActionZoomToWidth", "S", tr("Page Width")),
            ("mActionMoveItemContent", "S", tr("Move Content")),
            ("mActionEditNodesItem", "S", tr("Edit Vertices")),
        ]),
        (tr("Page Setup"), [
            ("mActionPageSetup mActionAddPages", "L", tr("Size")),
            ("mActionLayoutProperties", "L", tr("Properties")),
        ]),
        (tr("Show"), [
            ("mActionShowRulers", "S", tr("Rulers")),
            ("mActionShowGuides mActionSnapGuides mActionManageGuides",
             "S", tr("Guides")),
            ("mActionShowGrid mActionSnapGrid", "S", tr("Grid")),
            ("mActionSmartGuides", "S", tr("Smart Guides")),
            ("mActionShowBoxes", "S", tr("Bounding Boxes")),
            ("mActionRefreshView", "S", tr("Refresh")),
        ]),
        (tr("Edit"), [
            ("mActionUndo|text:Undo", "S", tr("Undo")),
            ("mActionRedo|text:Redo", "S", tr("Redo")),
            ("mActionSelectAll mActionDeselectAll mActionInvertSelection",
             "S", tr("Select All")),
        ]),
    ]),
    (tr("Insert"), [
        (tr("Map Frames"), [
            ("text:Add_Map text:Add_3D_Map", "L", tr("Map\nFrame")),
        ]),
        (tr("Map Surrounds"), [
            ("text:Add_Legend", "L", tr("Legend")),
            ("text:Add_North_Arrow", "L", tr("North\nArrow")),
            ("text:Add_Scale_Bar", "L", tr("Scale\nBar")),
            ("text:Add_Attribute_Table text:Add_Fixed_Table", "L",
             tr("Table")),
            ("text:Add_Chart", "S", tr("Chart")),
            ("text:Add_Elevation_Profile", "S", tr("Profile")),
        ]),
        (tr("Graphics and Text"), [
            ("text:Add_Label text:Add_Dynamic_Text", "L", tr("Text")),
            ("text:Add_Picture", "L", tr("Picture")),
            ("text:Add_Rectangle text:Add_Ellipse text:Add_Triangle", "S",
             tr("Rectangle")),
            ("text:Add_Polygon", "S", tr("Polygon")),
            ("text:Add_Polyline text:Add_Arrow", "S", tr("Line")),
            ("text:Add_Marker", "S", tr("Marker")),
            ("text:Add_HTML", "S", tr("HTML")),
        ]),
    ]),
    (tr("Arrange"), [
        (tr("Group"), [
            ("mActionGroupItems", "L", tr("Group")),
            ("mActionUngroupItems", "L", tr("Ungroup")),
        ]),
        (tr("Order"), [
            ("mActionMoveItemsToTop", "S", tr("Bring to Front")),
            ("mActionRaiseItems", "S", tr("Bring Forward")),
            ("mActionLowerItems", "S", tr("Send Backward")),
            ("mActionMoveItemsToBottom", "S", tr("Send to Back")),
        ]),
        (tr("Align"), [
            ("mActionAlignLeft", "S", tr("Left")),
            ("mActionAlignHCenter", "S", tr("Center")),
            ("mActionAlignRight", "S", tr("Right")),
            ("mActionAlignTop", "S", tr("Top")),
            ("mActionAlignVCenter", "S", tr("Middle")),
            ("mActionAlignBottom", "S", tr("Bottom")),
        ]),
        (tr("Distribute"), [
            ("mActionDistributeLeft mActionDistributeHCenter "
             "mActionDistributeRight mActionDistributeHSpace", "S",
             tr("Horizontally")),
            ("mActionDistributeTop mActionDistributeVCenter "
             "mActionDistributeBottom mActionDistributeVSpace", "S",
             tr("Vertically")),
        ]),
        (tr("Size"), [
            ("mActionResizeNarrowest mActionResizeWidest", "S",
             tr("Same Width")),
            ("mActionResizeShortest mActionResizeTallest", "S",
             tr("Same Height")),
            ("mActionResizeToSquare", "S", tr("Square")),
        ]),
        (tr("Lock"), [
            ("mActionLockItems", "S", tr("Lock")),
            ("mActionUnlockAll", "S", tr("Unlock All")),
        ]),
    ]),
    (tr("Map Series"), [
        (tr("Map Series"), [
            ("mActionAtlasSettings", "L", tr("Settings")),
            ("mActionAtlasPreview", "L", tr("Preview")),
        ]),
        (tr("Pages"), [
            ("mActionAtlasFirst", "S", tr("First")),
            ("mActionAtlasPrev", "S", tr("Previous")),
            ("mActionAtlasNext", "S", tr("Next")),
            ("mActionAtlasLast", "S", tr("Last")),
        ]),
        (tr("Export"), [
            ("mActionExportAtlasAsPDF", "L", tr("Export\nPDF")),
            ("mActionExportAtlasAsImage mActionExportAtlasAsSVG", "S",
             tr("Export Images")),
            ("mActionPrintAtlas", "S", tr("Print Series")),
        ]),
    ]),
    (tr("Share"), [
        (tr("Output"), [
            ("mActionExportAsPDF", "L", tr("Export\nPDF")),
            ("mActionExportAsImage", "L", tr("Export\nImage")),
            ("mActionExportAsSVG", "S", tr("SVG")),
            ("mActionPrint", "L", tr("Print")),
        ]),
        (tr("Save As"), [
            ("mActionSaveAsTemplate", "L", tr("Layout\nTemplate")),
            ("mActionLoadFromTemplate", "S", tr("Add from Template")),
            ("mActionDuplicateLayout", "S", tr("Duplicate")),
        ]),
        (tr("Manage"), [
            ("mActionSaveProject", "L", tr("Save\nProject")),
            ("mActionLayoutManager", "S", tr("Layout Manager")),
            ("mActionRenameLayout", "S", tr("Rename")),
        ]),
    ]),
]
# Pro icons for designer actions that have a Pro twin.
PRO_ICONS = {
    "mActionPan": "pan", "mActionZoomIn": "zoom_in",
    "mActionZoomOut": "zoom_out", "mActionZoomAll": "full_extent",
    "mActionPaste": "paste", "mActionCut": "cut", "mActionCopy": "copy",
    "mActionDeleteSelection": "delete", "mActionUndo": "undo",
    "mActionRedo": "redo", "mActionSelectMoveItem": "select",
    "mActionExportAsPDF": "export_pdf", "mActionExportAsImage":
    "export_image", "mActionPrint": "print", "mActionSaveProject":
    "save_project", "mActionLayoutManager": "layout_manager",
    "mActionShowGrid": "grid", "mActionRefreshView": "refresh",
    "mActionExportAtlasAsPDF": "export_pdf", "mActionPrintAtlas": "print",
}


def _plain(text):
    return text.replace("&", "").rstrip(".…").strip()


class DesignerRibbon:
    """The ribbon of one layout designer window.

    A plain Python object (no event filter or slots on the designer), so
    nothing of ours runs while QGIS tears a designer down.
    """

    def __init__(self, window):
        self.window = window
        win = self.window
        self.restyled = []   # (action, icon, iconText) to put back
        self.hidden = [b for b in win.findChildren(QToolBar)
                       if b.parent() is win and b.isVisible()]
        for bar in self.hidden:
            bar.hide()
        self.menu_visible = win.menuBar().isVisible()
        win.menuBar().hide()
        self.tabs = QTabWidget(objectName="ArcSkinRibbon")
        self.tabs.setTabBar(RibbonTabBar(self.tabs))
        self.tabs.setCornerWidget(self._layout_button(),
                                  Qt.Corner.TopLeftCorner)
        for title, groups in LAYOUT_RIBBON:
            built = self._groups(groups)
            if built:
                self.tabs.addTab(_tab(built), title)
        self.bar = QToolBar(tr("Layout Ribbon"), win,
                            objectName="WorkspaceLayoutRibbon")
        self.bar.setMovable(False)
        self.bar.setFloatable(False)
        self.bar.addWidget(self.tabs)
        win.addToolBar(Qt.ToolBarArea.TopToolBarArea, self.bar)

    def _actions(self):
        return {a.objectName(): a for a in self.window.findChildren(QAction)
                if a.objectName()}

    def _find(self, names, by_name, by_text):
        """``a|b``: the first of the alternatives found."""
        for name in names.split("|"):
            if name.startswith("text:"):
                action = by_text.get(name[5:].replace("_", " ").lower())
            else:
                action = by_name.get(name)
            if action is not None:
                return action
        return None

    def _groups(self, spec):
        by_name = self._actions()
        by_text = {}
        for action in self.window.findChildren(QAction):
            by_text.setdefault(_plain(action.text()).lower(), action)
        built = []
        for caption, items in spec:
            buttons = []
            for names, size, text in items:
                actions = [a for n in names.split()
                           if (a := self._find(n, by_name, by_text))]
                if not actions:
                    continue
                first = actions[0]
                self.restyled.append((first, first.icon(), first.iconText()))
                icon = PRO_ICONS.get(first.objectName())
                if icon:
                    first.setIcon(pro_icon(icon))
                first.setIconText(text)
                button = QToolButton(autoRaise=True)
                button.setDefaultAction(first)
                if len(actions) > 1:
                    menu = QMenu(button)
                    menu.addActions(actions)
                    button.setMenu(menu)
                    button.setPopupMode(
                        QToolButton.ToolButtonPopupMode.MenuButtonPopup)
                large = size == "L"
                buttons.append((_style(button, large), large))
            if buttons:
                built.append((caption, buttons))
        return built

    def _layout_button(self):
        button = QToolButton(objectName="ArcSkinProjectButton",
                             text=tr("Layout"))
        menu = QMenu(button)
        menu.addActions(self.window.menuBar().actions())
        button.setMenu(menu)
        button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        return button

    def restore_toolbars(self):
        for bar in self.hidden:
            if not sip.isdeleted(bar):
                bar.show()
        self.hidden = []

    def remove(self):
        if sip.isdeleted(self.window):
            return
        self.restore_toolbars()
        self.window.menuBar().setVisible(self.menu_visible)
        for action, icon, text in self.restyled:
            if not sip.isdeleted(action):
                action.setIcon(icon)
                action.setIconText(text)
        if not sip.isdeleted(self.bar):
            self.window.removeToolBar(self.bar)
            self.bar.deleteLater()


class LayoutRibbons(QObject):
    """Gives every layout designer, open now or later, a ribbon."""

    def __init__(self, iface):
        super().__init__(iface.mainWindow())
        self.iface = iface
        self.ribbons = []
        iface.layoutDesignerOpened.connect(self.add)
        iface.layoutDesignerWillBeClosed.connect(self.closing)
        for designer in iface.openLayoutDesigners():
            self.add(designer)

    def add(self, designer):
        window = designer.window()
        if not isinstance(window, QMainWindow):
            return
        # The designer adds its item tools (Add Map, Add Legend…) after it
        # announces itself: build once they are there.
        QTimer.singleShot(0, lambda: self._build(window))

    def _build(self, window):
        if sip.isdeleted(window) or window.findChild(
                QToolBar, "WorkspaceLayoutRibbon") is not None:
            return
        self.ribbons = [r for r in self.ribbons
                        if not sip.isdeleted(r.window)]
        self.ribbons.append(DesignerRibbon(window))

    def closing(self, designer):
        """Toolbars back before QGIS saves the designer's window state, so
        the designer keeps them when the workspace is off."""
        window = designer.window()
        for ribbon in self.ribbons:
            if ribbon.window is window:
                ribbon.restore_toolbars()
        self.ribbons = [r for r in self.ribbons
                        if r.window is not window
                        and not sip.isdeleted(r.window)]

    def stop(self):
        for signal, slot in ((self.iface.layoutDesignerOpened, self.add),
                             (self.iface.layoutDesignerWillBeClosed,
                              self.closing)):
            try:
                signal.disconnect(slot)
            except (TypeError, RuntimeError):
                pass
        for ribbon in self.ribbons:
            ribbon.remove()
        self.ribbons = []


def layout_ribbons(iface, changes):
    ribbons = LayoutRibbons(iface)
    changes.add(ribbons.stop)
    return ribbons
