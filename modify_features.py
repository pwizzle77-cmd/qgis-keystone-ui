"""Modify Features pane, like ArcGIS Pro's.

All the editing tools in one searchable list, grouped as in Pro (General,
Alignment, Construct, Divide, Reshape and Attributes). Clicking a tool
starts it; the panel under the list says how to use it on the map.
Tools QGIS doesn't have in this session are left out.
"""

from qgis.PyQt import sip
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QAction
from qgis.PyQt.QtWidgets import (
    QDockWidget, QLabel, QLineEdit, QSplitter, QTreeWidget,
    QTreeWidgetItem, QVBoxLayout, QWidget,
)

from .util import pro_icon, tr

OBJECT_NAME = "WorkspaceModifyFeatures"
ACTION_ROLE = Qt.ItemDataRole.UserRole
HELP_ROLE = Qt.ItemDataRole.UserRole + 1

# Group -> [(source, Pro icon or None for the action's own, name, help)].
# source: QGIS action name, "dock:<name>" or "pane:<object name>".
TOOLS = [
    (tr("General"), [
        ("mActionMoveFeature", "move", tr("Move"),
         tr("Drag the selected features (or click one) to a new place.")),
        ("mActionMoveFeatureCopy", "copy", tr("Copy and Move"),
         tr("Drag a copy of the selected features to a new place.")),
        ("mActionRotateFeature", "rotate", tr("Rotate"),
         tr("Click a feature, then move the mouse to rotate it. "
            "Ctrl+click sets the anchor; type an angle in the box.")),
        ("mActionScaleFeature", "scale", tr("Scale"),
         tr("Click a feature, then move the mouse to resize it, or type "
            "a factor.")),
        ("mActionVertexTool", "vertex_tool", tr("Edit Vertices"),
         tr("Drag, add (double-click a segment) or delete (Del) vertices of "
            "features on all editable layers.")),
        ("dock:VertexEditor", "vertex_tool", tr("Vertex Editor table"),
         tr("Edit the coordinates of the selected feature's vertices in a "
            "table.")),
        ("mActionDeleteSelected", "delete", tr("Delete"),
         tr("Delete the selected features.")),
    ]),
    (tr("Alignment"), [
        ("TopologicalEditingAction", "topological_editing",
         tr("Topological Editing"),
         tr("Shared edges and vertices move together when you edit one "
            "feature.")),
    ]),
    (tr("Construct"), [
        ("mActionOffsetCurve", "offset", tr("Copy Parallel (Offset)"),
         tr("Click a line or polygon edge and move the mouse, or type the "
            "distance. Ctrl keeps the original.")),
        ("mActionFeatureArray", "copy", tr("Feature Array"),
         tr("Copy a feature several times at a set spacing.")),
        ("mActionAddPart", "add_part", tr("Add Part"),
         tr("Draw another part of the selected feature (multipart).")),
        ("mActionAddRing", "add_ring", tr("Add Ring (hole)"),
         tr("Draw a hole inside a polygon.")),
        ("mActionFillRing", "fill_ring", tr("Fill Ring"),
         tr("Draw a hole and fill it with a new feature.")),
        ("mActionMergeFeatures", "merge", tr("Merge"),
         tr("Merge the selected features into one; choose which attributes "
            "to keep.")),
    ]),
    (tr("Divide"), [
        ("mActionSplitFeatures", "split", tr("Split"),
         tr("Draw a line across features to split them.")),
        ("mActionSplitParts", "split_parts", tr("Split Parts"),
         tr("Draw a line to split parts of a multipart feature.")),
    ]),
    (tr("Reshape"), [
        ("mActionReshapeFeatures", "reshape", tr("Reshape"),
         tr("Draw a line that crosses the feature's edge to reshape it.")),
        ("mActionTrimExtendFeature", "trim_extend", tr("Trim/Extend"),
         tr("Click the limit, then the segment to trim or extend to it.")),
        ("mActionSimplifyFeature", "simplify", tr("Simplify"),
         tr("Click or drag over features to remove vertices within a "
            "tolerance.")),
        ("mActionChamferFillet", "fillet", tr("Fillet / Chamfer"),
         tr("Round or bevel the corner at a vertex.")),
        ("mActionReverseLine", "reverse_line", tr("Reverse Direction"),
         tr("Click a line to reverse its direction.")),
        ("mActionDeletePart", "delete_part", tr("Delete Part"),
         tr("Click a part of a multipart feature to delete it.")),
        ("mActionDeleteRing", "delete_ring", tr("Delete Ring"),
         tr("Click inside a hole to remove it.")),
    ]),
    (tr("Attributes"), [
        ("pane:WorkspaceAttributes", "multi_edit", tr("Attributes pane"),
         tr("Edit the selected features' fields in the Attributes pane.")),
        ("mActionMultiEditAttributes", "multi_edit",
         tr("Edit attributes of selected features"),
         tr("Change a field on all selected features at once.")),
        ("mActionMergeFeatureAttributes", "merge_attributes",
         tr("Merge Attributes"),
         tr("Give the selected features the same attribute values.")),
        ("mActionOpenFieldCalc", "field_calculator", tr("Calculate Field"),
         tr("Compute field values with an expression.")),
    ]),
]


def resolve_source(win, source):
    """QAction for a TOOLS source, or None."""
    if source.startswith("pane:"):
        return None
    if source.startswith("dock:"):
        dock = win.findChild(QDockWidget, source[5:])
        return dock.toggleViewAction() if dock else None
    return win.findChild(QAction, source)


class ModifyFeaturesPane(QDockWidget):
    def __init__(self, iface):
        super().__init__(tr("Modify Features"), iface.mainWindow())
        self.setObjectName(OBJECT_NAME)
        self.iface = iface
        self.win = iface.mainWindow()
        body = QWidget()
        box = QVBoxLayout(body)
        box.setContentsMargins(4, 4, 4, 4)
        self.search = QLineEdit(placeholderText=tr("Find a tool"),
                                clearButtonEnabled=True)
        self.search.addAction(pro_icon("search"),
                              QLineEdit.ActionPosition.LeadingPosition)
        self.search.textChanged.connect(self._filter)
        box.addWidget(self.search)
        split = QSplitter(Qt.Orientation.Vertical)
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.itemClicked.connect(self._clicked)
        self.tree.currentItemChanged.connect(self._describe)
        split.addWidget(self.tree)
        self.help = QLabel(tr("Pick a tool to start it. Most tools work on "
                              "the selected features of layers in edit "
                              "mode."))
        self.help.setWordWrap(True)
        self.help.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.help.setMargin(6)
        split.addWidget(self.help)
        split.setStretchFactor(0, 4)
        split.setStretchFactor(1, 1)
        box.addWidget(split, 1)
        self.setWidget(body)
        self.visibilityChanged.connect(lambda shown: shown and self.refresh())

    def refresh(self):
        if sip.isdeleted(self):
            return
        self.tree.clear()
        for group, tools in TOOLS:
            node = QTreeWidgetItem([group])
            node.setFlags(Qt.ItemFlag.ItemIsEnabled)
            for source, icon, name, text in tools:
                action = resolve_source(self.win, source)
                if action is None and not source.startswith("pane:"):
                    continue
                if source.startswith("pane:") and self.win.findChild(
                        QDockWidget, source[5:]) is None:
                    continue
                item = QTreeWidgetItem([name])
                if icon:
                    item.setIcon(0, pro_icon(icon))
                elif action is not None:
                    item.setIcon(0, action.icon())
                item.setData(0, ACTION_ROLE, source)
                item.setData(0, HELP_ROLE, text)
                item.setToolTip(0, text)
                node.addChild(item)
            if node.childCount():
                self.tree.addTopLevelItem(node)
                node.setExpanded(True)
        self._filter(self.search.text())

    def _filter(self, text):
        text = (text or "").lower()
        for i in range(self.tree.topLevelItemCount()):
            node = self.tree.topLevelItem(i)
            shown = 0
            for j in range(node.childCount()):
                child = node.child(j)
                hit = (not text or text in child.text(0).lower()
                       or text in (child.data(0, HELP_ROLE) or "").lower())
                child.setHidden(not hit)
                shown += hit
            node.setHidden(shown == 0)

    def _describe(self, item, _previous=None):
        if item is not None and item.data(0, HELP_ROLE):
            self.help.setText(f"<b>{item.text(0)}</b><br>"
                              f"{item.data(0, HELP_ROLE)}")

    def _clicked(self, item, _column):
        source = item.data(0, ACTION_ROLE)
        if not source:
            return
        self._describe(item)
        if source.startswith("pane:"):
            pane = self.win.findChild(QDockWidget, source[5:])
            if pane is not None:
                pane.show()
                pane.raise_()
            return
        action = resolve_source(self.win, source)
        if action is None:
            return
        if not action.isEnabled():
            self.help.setText(self.help.text() + "<br><i>" + tr(
                "Not available now: select a layer in edit mode (and "
                "features, for tools that need them).") + "</i>")
            return
        if action.isCheckable() and action.isChecked() \
                and not source.startswith("mAction"):
            return
        action.trigger()


def modify_features_pane(iface, changes):
    win = iface.mainWindow()
    pane = ModifyFeaturesPane(iface)
    win.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, pane)
    catalog = win.findChild(QDockWidget, "Browser")
    if catalog is not None and not catalog.isFloating():
        win.tabifyDockWidget(catalog, pane)
    pane.hide()

    def remove():
        win.removeDockWidget(pane)
        pane.deleteLater()

    changes.add(remove)
    return pane
