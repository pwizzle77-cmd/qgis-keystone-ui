"""Workspace Ribbon: an ArcGIS Pro style ribbon replacing QGIS's menu bar
and toolbars.

Built on ArcSkin's ribbon (EnviroSolutions, GPL-2.0-or-later) and extended
with:

* Pro's tab order with every tool in a named group (no "everything in
  Add-ins" tab for the plugins the ribbon knows about),
* tabs for the Parcel Fabric and NENA Addressing and RCL Tools plugins, and
  Align Edge / COGO Workbench placed on the Edit tab where Pro users look,
* a Layout tab and a New Layout gallery of print-ready page templates,
* contextual tabs (Feature Layer / Raster Layer) that appear for the layer
  selected in Contents, like Pro's Appearance, Labeling and Data tabs,
* a collapsible ribbon (double-click a tab, Ctrl+F1) and a Maximize Map
  switch, to give the map the most room.

The ribbon borrows QGIS's own actions and the plugins' actions, so their
logic, shortcuts and checked state stay in one place. Icon and caption
changes go through :class:`~.util.Changes`, which reverts them.
"""

from qgis import utils as qgis_utils
from qgis.core import (
    QgsProject, QgsRasterLayer, QgsSettings, QgsVectorLayer,
)
from qgis.gui import QgsFloatingWidget, QgsStatusBar
from qgis.PyQt import sip
from qgis.PyQt.QtCore import QEvent, QObject, QRect, QSize, Qt, QTimer
from qgis.PyQt.QtGui import QAction, QColor, QKeySequence, QPainter
from qgis.PyQt.QtWidgets import (
    QApplication, QDockWidget, QFrame, QHBoxLayout, QLabel, QMenu,
    QScrollArea, QStackedWidget, QTabBar, QTabWidget, QToolBar, QToolButton,
    QVBoxLayout, QWidget, QWidgetAction,
)

from .basemaps import gallery_menu
from .layouts import (
    export_layouts_menu, import_layout_template, new_layout_menu,
    open_layouts_menu,
)
from .panes import (
    LEFT, RIGHT, active_pane, dock_floating_panes, dock_pane, reset_panes,
    reset_panes_menu,
)
from .util import pro_icon, tr

LARGE, SMALL = 32, 16
COLLAPSED_KEY = "workspace_ribbon/collapsed"
# Core toolbars where plugins put their buttons; shown on the Add-ins tab.
ADDIN_HOSTS = {"mPluginToolBar", "mWebToolBar", "mDatabaseToolBar",
               "mVectorToolBar", "mRasterToolBar"}

# --------------------------------------------------------------------------
# Ribbon specification
#
# Tab -> groups -> items. An item is (names, size, icon, caption).
# ``names`` is a space-separated list; the first action found is the
# button, the others form its drop-down. A name is:
#   mActionXxx            a QGIS action (by objectName)
#   menu:<name>           that QGIS menu (or a QGIS tool button's) as drop-down
#   dock:<name>           show/hide action of that dock widget
#   shapes:<kind>         the shape digitizing tools of that kind
#   plugin:<id>.<attr>    a QAction attribute of a loaded plugin
#   bar:<toolbar>/<text>  the action with that text on that toolbar
#   fn:<key>              one of the ribbon's own commands (see _commands)
#   gallery:basemaps, panes:reset, layouts:new|open|export  galleries
# ``icon`` None keeps the action's own icon (used for the user's plugins).
# --------------------------------------------------------------------------
PF_BAR = "ParcelFabricToolbar"
NENA_BAR = "NenaRclToolsToolbar"

RIBBON = [
    (tr("Map"), [
        (tr("Clipboard"), [
            ("mActionPasteFeatures menu:mMenuPasteAs", "L", "paste",
             tr("Paste")),
            ("mActionCutFeatures", "S", "cut", tr("Cut")),
            ("mActionCopyFeatures", "S", "copy", tr("Copy")),
        ]),
        (tr("Navigate"), [
            ("mActionPan", "L", "pan", tr("Explore")),
            ("mActionZoomFullExtent", "S", "full_extent", tr("Full Extent")),
            ("mActionZoomIn", "S", "zoom_in", tr("Zoom In")),
            ("mActionZoomOut", "S", "zoom_out", tr("Zoom Out")),
            ("mActionZoomLast", "S", "prev_extent", tr("Previous")),
            ("mActionZoomNext", "S", "next_extent", tr("Next")),
            ("mActionZoomToLayers", "S", "zoom_layer", tr("To Layer")),
            ("mActionShowBookmarks mActionNewBookmark", "L", "bookmarks",
             tr("Bookmarks")),
        ]),
        (tr("Layer"), [
            ("gallery:basemaps", "L", "basemap", tr("Basemap")),
            ("mActionDataSourceManager menu:mAddLayerMenu", "L", "add_data",
             tr("Add Data")),
            ("mActionAddOgrLayer", "S", "add_vector", tr("Vector")),
            ("mActionAddRasterLayer", "S", "add_raster", tr("Raster")),
            ("mActionAddWmsLayer", "S", "add_wms", "WMS/WMTS"),
        ]),
        (tr("Selection"), [
            ("mActionSelectFeatures menu:mMenuSelect", "L", "select",
             tr("Select")),
            ("mActionSelectByExpression mActionSelectByForm", "L",
             "select_expression", tr("Select By\nAttributes")),
            ("mProcessingAlg_native:selectbylocation "
             "mProcessingAlg_native:selectwithindistance", "L",
             "select_location", tr("Select By\nLocation")),
            ("mActionDeselectAll", "S", "clear_selection", tr("Clear")),
            ("mActionZoomToSelected mActionPanToSelected", "S",
             "zoom_selected", tr("Zoom To")),
            ("mActionInvertSelection", "S", "invert_selection", tr("Switch")),
            ("fn:attributes_pane", "L", "multi_edit", tr("Attributes")),
        ]),
        (tr("Inquiry"), [
            ("mActionIdentify", "L", "identify", tr("Explore\nIdentify")),
            ("mActionMeasure mActionMeasureArea mActionMeasureAngle "
             "mActionMeasureBearing", "L", "measure", tr("Measure")),
            ("mActionStatisticalSummary", "S", "statistics",
             tr("Statistics")),
            ("mActionMapTips", "S", "map_tips", tr("Map Tips")),
            ("mActionDraw", "S", "refresh", tr("Refresh")),
        ]),
        (tr("Labeling"), [
            ("mActionLabeling", "L", "labeling", tr("Label\nSettings")),
            ("mActionMoveLabel mActionRotateLabel", "S", "label_move",
             tr("Move")),
            ("mActionShowHideLabels", "S", "visibility", tr("Show/Hide")),
            ("mActionChangeLabelProperties", "S", "multi_edit",
             tr("Properties")),
        ]),
    ]),
    (tr("Insert"), [
        (tr("Project"), [
            ("mActionNewMapCanvas mActionNew3DMapCanvas", "L", "new_map",
             tr("New Map")),
            ("layouts:new", "L", "new_layout", tr("New Layout")),
            ("fn:import_layout", "S", "open_project", tr("Import Layout")),
            ("mActionShowLayoutManager", "S", "layout_manager",
             tr("Layouts")),
        ]),
        (tr("Layer Templates"), [
            ("mActionNewGeoPackageLayer menu:mNewLayerMenu", "L",
             "new_layer", tr("New Layer")),
        ]),
        (tr("Map Elements"), [
            ("menu:mMenuDecorations", "L", "decorations", tr("Decorations")),
            ("mActionShowBookmarks mActionNewBookmark", "S", "new_bookmark",
             tr("Bookmark")),
        ]),
        (tr("Styles"), [
            ("mActionStyleManager", "L", "style_manager", tr("Styles")),
        ]),
    ]),
    (tr("Layout"), [
        (tr("Page Layout"), [
            ("layouts:new", "L", "new_layout", tr("New Layout")),
            ("layouts:open", "L", "layout_manager", tr("Open Layout")),
            ("fn:import_layout", "S", "open_project", tr("Import Template")),
            ("mActionShowLayoutManager", "S", "layout_manager",
             tr("Layout Manager")),
        ]),
        (tr("Map View"), [
            ("mActionNewMapCanvas mActionNew3DMapCanvas", "L", "new_map",
             tr("New Map")),
            ("menu:mMenuDecorations", "S", "decorations",
             tr("Decorations")),
            ("mActionStyleManager", "S", "style_manager", tr("Styles")),
        ]),
        (tr("Export"), [
            ("layouts:export", "L", "export_pdf", tr("Export\nLayout")),
            ("mActionSaveMapAsPdf", "S", "export_pdf", tr("Map to PDF")),
            ("mActionSaveMapAsImage", "S", "export_image",
             tr("Map to Image")),
        ]),
    ]),
    (tr("Analysis"), [
        (tr("Geoprocessing"), [
            ("toolboxAction", "L", "toolbox", tr("Tools")),
            ("modelerAction", "L", "model_builder", "ModelBuilder"),
            ("historyAction", "S", "history", tr("History")),
            ("mActionShowPythonDialog", "S", "python", "Python"),
            ("dock:ResultsViewer", "S", "processing_results", tr("Results")),
        ]),
        (tr("Tools"), [
            ("menu:mVectorMenu", "L", "vector_tools", tr("Vector")),
            ("menu:mRasterMenu", "L", "raster_tools", tr("Raster")),
            ("menu:mDatabaseMenu", "S", "database", tr("Database")),
            ("menu:mWebMenu", "S", "web", "Web"),
            ("menu:mMeshMenu", "S", "mesh", tr("Mesh")),
        ]),
    ]),
    (tr("View"), [
        (tr("Windows"), [
            ("panes:reset", "L", "reset_panes", tr("Reset\nPanes")),
            ("menu:mPanelMenu", "L", "panes", tr("Panes")),
            ("dock:Layers", "S", "contents", tr("Contents")),
            ("dock:Browser", "S", "catalog", tr("Catalog")),
            ("dock:ProcessingToolbox", "S", "geoprocessing",
             tr("Geoprocessing")),
            ("dock:LayerStyling", "S", "symbology", tr("Symbology")),
            ("dock:LayerOrder", "S", "layer_order", tr("Layer Order")),
            ("dock:MessageLog", "S", "message_log", tr("Messages")),
            ("menu:mToolbarMenu", "S", "toolbars", tr("Toolbars")),
        ]),
        (tr("Docking"), [
            ("fn:dock_floating", "L", "panes", tr("Dock Floating\nPanes")),
            ("fn:dock_left", "S", "contents", tr("Dock Left")),
            ("fn:dock_right", "S", "catalog", tr("Dock Right")),
        ]),
        (tr("Workspace"), [
            ("fn:maximize_map", "L", "full_extent", tr("Maximize\nMap")),
            ("fn:collapse_ribbon", "S", "collapse_all",
             tr("Collapse Ribbon")),
            ("mActionToggleMapOnly", "S", "overview", tr("Map Only")),
            ("mActionToggleFullScreen", "S", "full_extent",
             tr("Full Screen")),
        ]),
        (tr("View"), [
            ("mActionNew3DMapCanvas menu:m3DMapViewsMenu", "L", "map_3d",
             tr("3D View")),
            ("dock:Overview", "S", "overview", tr("Overview")),
            ("mActionTemporalController", "S", "temporal", tr("Time")),
        ]),
    ]),
    (tr("Edit"), [
        (tr("Clipboard"), [
            ("mActionPasteFeatures menu:mMenuPasteAs", "L", "paste",
             tr("Paste")),
            ("mActionCutFeatures", "S", "cut", tr("Cut")),
            ("mActionCopyFeatures", "S", "copy", tr("Copy")),
        ]),
        (tr("Manage Edits"), [
            ("mActionToggleEditing", "L", "edit", tr("Edit")),
            ("mActionSaveLayerEdits menu:AllEditsMenu", "L", "save_edits",
             tr("Save")),
            ("fn:save_all", "L", "save_all", tr("Save\nAll")),
            ("mActionRollbackEdits mActionRollbackAllEdits", "L",
             "discard_edits", tr("Discard")),
            ("mActionUndo", "S", "undo", tr("Undo")),
            ("mActionRedo", "S", "redo", tr("Redo")),
        ]),
        (tr("Snapping"), [
            ("EnableSnappingAction menu:SnappingTypeButton", "L", "snapping",
             tr("Snapping")),
            ("menu:SnappingModeButton", "S", "snap_mode", tr("Mode")),
            ("EnableTracingAction", "S", "tracing", tr("Tracing")),
            ("TopologicalEditingAction", "S", "topological_editing",
             tr("Topology")),
            ("IntersectionSnappingAction", "S", "snap_intersection",
             tr("Intersections")),
            ("SelfSnappingAction", "S", "self_snapping", tr("Self-snapping")),
            ("menu:AvoidIntersectionsModeButton", "S", "avoid_overlap",
             tr("Overlap")),
        ]),
        (tr("Features"), [
            ("mActionAddFeature mActionDigitizeWithSegment "
             "mActionDigitizeWithCurve mActionStreamDigitize "
             "mActionDigitizeWithBezier mActionDigitizeWithNurbs "
             "mActionDigitizeShape", "L", "create_feature", tr("Create")),
            ("mActionVertexTool mActionVertexToolActiveLayer "
             "dock:VertexEditor", "L", "vertex_tool", tr("Modify")),
            ("mActionDeleteSelected", "L", "delete", tr("Delete")),
            ("fn:attributes_pane mActionMultiEditAttributes", "L",
             "multi_edit",
             tr("Attributes")),
        ]),
        (tr("Tools"), [
            ("mActionAlignEdge plugin:align_edge.settings_action", "L",
             None, tr("Align\nEdge")),
            ("dock:AdvancedDigitizingTools", "L", "advanced_digitizing",
             tr("Advanced\nDigitizing")),
            ("mActionMoveFeature mActionMoveFeatureCopy "
             "mActionFeatureArray", "S", "move", tr("Move")),
            ("mActionRotateFeature", "S", "rotate", tr("Rotate")),
            ("mActionScaleFeature", "S", "scale", tr("Scale")),
            ("mActionSplitFeatures", "S", "split", tr("Split")),
            ("mActionMergeFeatures", "S", "merge", tr("Merge")),
            ("mActionReshapeFeatures", "S", "reshape", tr("Reshape")),
            ("mActionTrimExtendFeature", "S", "trim_extend",
             tr("Trim/Extend")),
            ("mActionOffsetCurve", "S", "offset", tr("Offset")),
            ("mActionReverseLine", "S", "reverse_line", tr("Reverse")),
        ]),
        (tr("COGO"), [
            ("plugin:cogo_workbench.action", "L", None, tr("COGO\nWorkbench")),
            ("bar:" + PF_BAR + "/Traverse", "L", None, tr("Traverse")),
        ]),
        (tr("Modify"), [
            ("mActionAddPart", "S", "add_part", tr("Add Part")),
            ("mActionDeletePart", "S", "delete_part", tr("Delete Part")),
            ("mActionSplitParts", "S", "split_parts", tr("Split Parts")),
            ("mActionAddRing", "S", "add_ring", tr("Add Ring")),
            ("mActionFillRing", "S", "fill_ring", tr("Fill Ring")),
            ("mActionDeleteRing", "S", "delete_ring", tr("Delete Ring")),
            ("mActionSimplifyFeature", "S", "simplify", tr("Simplify")),
            ("mActionChamferFillet", "S", "fillet", tr("Fillet")),
            ("mActionMergeFeatureAttributes", "S", "merge_attributes",
             tr("Merge Attributes")),
        ]),
        (tr("Shapes"), [
            ("shapes:circle", "S", "circle", tr("Circle")),
            ("shapes:ellipse", "S", "ellipse", tr("Ellipse")),
            ("shapes:rectangle", "S", "rectangle", tr("Rectangle")),
            ("shapes:regular-polygon", "S", "regular_polygon",
             tr("Polygon")),
        ]),
    ]),
    (tr("Imagery"), [
        (tr("Add"), [
            ("mActionAddRasterLayer", "L", "add_raster", tr("Add Raster")),
            ("mActionAddWmsLayer", "S", "add_wms", "WMS/WMTS"),
        ]),
        (tr("Tools"), [
            ("mActionShowGeoreferencer", "L", "coordinates",
             tr("Georeference")),
            ("mActionShowRasterCalculator", "L", "field_calculator",
             tr("Raster\nCalculator")),
            ("mActionShowAlignRasterTool", "S", "layer_order",
             tr("Align Rasters")),
            ("menu:mRasterMenu", "S", "raster_tools", tr("Raster Tools")),
        ]),
    ]),
    (tr("Share"), [
        (tr("Export"), [
            ("layouts:export", "L", "export_pdf", tr("Export\nLayout")),
            ("mActionSaveMapAsImage", "L", "export_image", tr("Map\nImage")),
            ("mActionSaveMapAsPdf", "L", "export_pdf", tr("Map\nPDF")),
            ("mActionDxfExport", "S", "export_image", "DXF"),
            ("fn:package_layers", "S", "database", tr("Package Layers")),
        ]),
        (tr("Print"), [
            ("layouts:open", "L", "print", tr("Print\nLayout")),
        ]),
    ]),
    # Parcel Fabric and NENA tabs are built from their toolbars here.
    ("@plugin_tabs", None),
    (tr("Help"), [
        (tr("Help"), [
            ("mActionHelpContents menu:mHelpMenu", "L", "help", tr("Help")),
        ]),
        (tr("Customize"), [
            ("mActionManagePlugins menu:mPluginMenu", "L", "plugins",
             tr("Plugins")),
            ("mActionOptions", "L", "options", tr("Options")),
            ("mActionCustomization", "S", "tool", tr("Customization")),
        ]),
    ]),
]

# The user's plugins with a toolbar of their own: tab title, toolbar name,
# group captions (the toolbar's separators split the groups), short button
# captions, extra groups of (caption, [names]) appended to the tab.
PLUGIN_TABS = [
    (tr("Parcels"), PF_BAR,
     [tr("Fabric"), tr("Records"), tr("Parcel Tools"),
      tr("Legal & Analysis"), tr("Numbering & QA"), tr("Settings")],
     {"New Fabric...": tr("New Fabric"),
      "Add Fabric to Map...": tr("Add Fabric"),
      "Build Active Record": tr("Build"),
      "Split Parcel": tr("Split"),
      "Divide Parcel": tr("Divide"),
      "Merge Parcels": tr("Merge"),
      "Copy Parallel": tr("Copy Parallel"),
      "Parcel Lineage": tr("Lineage"),
      "Parcel History": tr("History"),
      "Write Legal Description": tr("Legal\nDescription"),
      "Compare with Existing Parcels": tr("Compare"),
      "Least-Squares Adjustment": tr("Adjust"),
      "Subdivide Block": tr("Subdivide"),
      "Address Lots (NENA)": tr("Address Lots"),
      "Assign Parcel Numbers": tr("Assign\nNumbers"),
      "Check Fabric": tr("Check\nFabric"),
      "Import / Export...": tr("Import/Export"),
      "Fabric Settings...": tr("Fabric\nSettings")},
     [(tr("COGO"), ["plugin:cogo_workbench.action"])]),
    (tr("Addressing"), NENA_BAR,
     [tr("Windows"), tr("Centerlines"), tr("Addressing")],
     {},
     [(tr("Setup"), [
         "plugin:nena_addressing_and_rcl_tools.act_style",
         "plugin:nena_addressing_and_rcl_tools.act_schema",
         "plugin:nena_addressing_and_rcl_tools.act_import",
         "plugin:nena_addressing_and_rcl_tools.act_archive",
         "plugin:nena_addressing_and_rcl_tools.act_lookups",
         "plugin:nena_addressing_and_rcl_tools.act_settings",
     ])]),
]

# Contextual tabs, shown for the layer selected in Contents, as in Pro.
FEATURE_LAYER = [
    (tr("Appearance"), [
        (tr("Visibility"), [
            ("fn:layer_symbology", "L", "symbology", tr("Symbology")),
            ("fn:layer_rendering", "L", "transparency",
             tr("Visibility\nRange")),
            ("mActionZoomToLayers", "S", "zoom_layer", tr("Zoom To Layer")),
            ("fn:layer_properties", "S", "project_properties",
             tr("Properties")),
        ]),
        (tr("Effects"), [
            ("fn:layer_diagrams", "S", "diagram", tr("Diagrams")),
            ("fn:layer_masks", "S", "mask", tr("Masking")),
        ]),
    ]),
    (tr("Labeling"), [
        (tr("Layer"), [
            ("fn:toggle_labels", "L", "labeling", tr("Label")),
            ("mActionLabeling", "L", "label_move", tr("Label\nSettings")),
        ]),
        (tr("Label Tools"), [
            ("mActionMoveLabel", "S", "label_move", tr("Move")),
            ("mActionRotateLabel", "S", "rotate", tr("Rotate")),
            ("mActionShowHideLabels", "S", "visibility", tr("Show/Hide")),
            ("mActionChangeLabelProperties", "S", "multi_edit",
             tr("Properties")),
        ]),
    ]),
    (tr("Data"), [
        (tr("Table"), [
            ("mActionOpenTable menu:mMenuFilterTable", "L",
             "attribute_table", tr("Attribute\nTable")),
            ("mActionOpenFieldCalc", "L", "field_calculator",
             tr("Calculate\nField")),
            ("fn:layer_fields", "S", "attribute_table", tr("Fields")),
            ("fn:layer_form", "S", "multi_edit", tr("Form")),
        ]),
        (tr("Definition Query"), [
            ("mActionLayerSubsetString", "L", "filter_expression",
             tr("Definition\nQuery")),
        ]),
        (tr("Relationships"), [
            ("fn:layer_joins", "L", "merge_attributes", tr("Joins")),
            ("fn:layer_relations", "S", "merge", tr("Relates")),
        ]),
        (tr("Export"), [
            ("mActionLayerSaveAs", "L", "save_as", tr("Export\nFeatures")),
        ]),
    ]),
]
RASTER_LAYER = [
    (tr("Appearance"), [
        (tr("Rendering"), [
            ("fn:layer_symbology", "L", "symbology", tr("Symbology")),
            ("fn:layer_rendering", "L", "transparency",
             tr("Visibility\nRange")),
            ("mActionZoomToLayers", "S", "zoom_layer", tr("Zoom To Layer")),
            ("mActionZoomActualSize", "S", "zoom_in", tr("Native\nResolution")),
            ("fn:layer_properties", "S", "project_properties",
             tr("Properties")),
        ]),
    ]),
    (tr("Data"), [
        (tr("Data"), [
            ("fn:layer_histogram", "L", "histogram", tr("Histogram")),
            ("mActionLayerSaveAs", "L", "save_as", tr("Export\nRaster")),
        ]),
    ]),
]
CONTEXT_TABS = {
    "vector": (tr("Feature Layer"), "#5fa82e", FEATURE_LAYER),
    "raster": (tr("Raster Layer"), "#9b59b6", RASTER_LAYER),
    "table": (tr("Standalone Table"), "#e08a1e", FEATURE_LAYER[2:]),
}

# Blue Project button: (action name or "menu:<QGIS menu>", Pro icon or None
# for QGIS's own).
PROJECT_MENU = [
    ("mActionNewProject", "new_project"),
    ("mActionOpenProject", "open_project"),
    ("menu:mRecentProjectsMenu", None),
    ("mActionSaveProject", "save_project"),
    ("fn:save_all", None),
    ("mActionSaveProjectAs", "save_as"),
    ("mActionRevertProject", None),
    ("mActionCloseProject", None),
    ("menu:menuImport_Export", None),
    ("mActionProjectProperties", "project_properties"),
    ("mActionOptions", "options"),
    ("menu:mSettingsMenu", None),
]
# Quick Access Toolbar, as in Pro's title row, with the editing essentials.
QUICK_ACCESS = ["mActionNewProject", "mActionOpenProject",
                "mActionSaveProject", "fn:save_all", "mActionUndo", "mActionRedo",
                "|", "mActionToggleEditing", "mActionSaveLayerEdits"]


# --------------------------------------------------------------------------
# Lookups
# --------------------------------------------------------------------------
def find_action(win, name):
    """QGIS action by object name; ``dock:<name>`` is a dock's toggle."""
    if name.startswith("dock:"):
        dock = win.findChild(QDockWidget, name[5:])
        return dock.toggleViewAction() if dock else None
    return win.findChild(QAction, name)


def find_menu(win, name):
    """QGIS menu by object name, or the menu of a QGIS tool button."""
    menu = win.findChild(QMenu, name)
    if not menu and (button := win.findChild(QToolButton, name)):
        menu = button.menu()
    return menu


def plugin_action(spec):
    """``<plugin id>.<attribute>`` of a loaded plugin, if it is a QAction."""
    plugin_id, _, path = spec.partition(".")
    obj = qgis_utils.plugins.get(plugin_id)
    for part in path.split("."):
        if obj is None:
            return None
        obj = getattr(obj, part, None)
    if isinstance(obj, QAction) and not sip.isdeleted(obj):
        return obj
    return None


def _plain(text):
    return text.replace("&&", "\0").replace("&", "").replace("\0", "&")


def bar_action(win, spec):
    """``<toolbar name>/<action text>``: an action on a plugin toolbar."""
    bar_name, _, text = spec.partition("/")
    bar = win.findChild(QToolBar, bar_name)
    if bar is None:
        return None
    for action in bar.actions():
        if _plain(action.text()).rstrip(".…") == text:
            return action
    return None


def _shape_actions(win, kind):
    """Shape digitizing tools of a kind ("circle", ...): QGIS gives them no
    object names, only their shape id as data."""
    bar = win.findChild(QToolBar, "mShapeDigitizeToolBar")
    buttons = bar.findChildren(QToolButton) if bar else []
    return [a for b in buttons if b.menu() for a in b.menu().actions()
            if str(a.data()).startswith(kind)]


def restyle(changes, action, icon, caption=None):
    """Give an action a Pro icon (and short caption) until undone."""
    if icon:
        changes.set(action, "icon", pro_icon(icon))
    if caption:
        changes.set(action, "iconText", caption)


def _style(button, large):
    side = LARGE if large else SMALL
    button.setIconSize(QSize(side, side))
    button.setToolButtonStyle(
        Qt.ToolButtonStyle.ToolButtonTextUnderIcon if large
        else Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
    return button


# --------------------------------------------------------------------------
# Widgets
# --------------------------------------------------------------------------
class RibbonTabBar(QTabBar):
    """Tab bar that marks contextual tabs with a coloured band on top, as
    Pro colours its Feature Layer / Raster Layer tab sets."""

    def paintEvent(self, event):  # noqa: N802 (Qt API name)
        super().paintEvent(event)
        painter = QPainter(self)
        for i in range(self.count()):
            color = self.tabData(i)
            if color:
                rect = self.tabRect(i)
                painter.fillRect(QRect(rect.left(), rect.top(),
                                       rect.width(), 3), QColor(color))
        painter.end()


class ToolbarKeeper(QObject):
    """Keeps toolbars that now live in the ribbon hidden, also when their
    plugin shows them again (NENA re-places its bar after start-up)."""

    def __init__(self, parent, bars):
        super().__init__(parent)
        self.bars = [b for b in bars if b is not None]
        for bar in self.bars:
            bar.installEventFilter(self)

    def eventFilter(self, watched, event):  # noqa: N802 (Qt API name)
        if event.type() == QEvent.Type.Show:
            QTimer.singleShot(0, lambda b=watched: sip.isdeleted(b)
                              or b.hide())
        return False

    def stop(self):
        for bar in self.bars:
            if not sip.isdeleted(bar):
                bar.removeEventFilter(self)
        self.bars = []


class CanvasWatcher(QObject):
    """Folds a temporarily opened collapsed ribbon away on a map click."""

    def __init__(self, ribbon, canvas):
        super().__init__(ribbon.tabs)
        self.ribbon = ribbon
        self.viewport = canvas.viewport()
        self.viewport.installEventFilter(self)

    def eventFilter(self, watched, event):  # noqa: N802 (Qt API name)
        if event.type() == QEvent.Type.MouseButtonPress:
            self.ribbon.fold_if_peeking()
        return False

    def stop(self):
        if not sip.isdeleted(self.viewport):
            self.viewport.removeEventFilter(self)


def _group(caption, buttons):
    """Ribbon group: large buttons, small ones stacked by three."""
    group = QWidget(objectName="ArcSkinRibbonGroup")
    row = QHBoxLayout()
    row.setSpacing(2)
    column = None
    for button, large in buttons:
        if large:
            row.addWidget(button, 0, Qt.AlignmentFlag.AlignTop)
            column = None
            continue
        if column is None or column.count() == 3:
            column = QVBoxLayout()
            column.setSpacing(0)
            column.setAlignment(Qt.AlignmentFlag.AlignTop)
            row.addLayout(column)
        column.addWidget(button)
    label = QLabel(caption, objectName="ArcSkinRibbonCaption")
    label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    box = QVBoxLayout(group)
    box.setContentsMargins(4, 2, 4, 0)
    box.setSpacing(0)
    box.addLayout(row)
    box.addStretch()
    box.addWidget(label)
    return group


def _tab(groups):
    """Ribbon tab page: the groups in a row, separated by thin lines. It
    scrolls sideways in a window narrower than the groups."""
    page = QWidget()
    row = QHBoxLayout(page)
    row.setContentsMargins(2, 2, 2, 2)
    row.setSpacing(0)
    for caption, buttons in groups:
        row.addWidget(_group(caption, buttons))
        line = QFrame(objectName="ArcSkinRibbonSeparator")
        line.setFrameShape(QFrame.Shape.VLine)
        row.addWidget(line)
    row.addStretch()
    scroll = QScrollArea(objectName="ArcSkinRibbonPage",
                         widgetResizable=True)
    scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    scroll.setWidget(page)
    scroll.setMinimumHeight(page.sizeHint().height()
                            + scroll.horizontalScrollBar().sizeHint().height())
    return scroll


def _drop_down_results(changes):
    """Open the locator's results below the search box, not above it."""
    for widget in QApplication.allWidgets():
        if widget.metaObject().className() == "QgsLocatorResultsView":
            popup = sip.cast(widget.parent(), QgsFloatingWidget)
            point = QgsFloatingWidget.AnchorPoint
            changes.set(popup, "anchorPoint", point.TopLeft)
            changes.set(popup, "anchorWidgetPoint", point.BottomLeft)


class TitleRow(QWidget):
    """Row above the ribbon, like Pro's title bar: Quick Access Toolbar and
    project name on the left, QGIS's locator centred (Command Search),
    messages and the ribbon collapse switch on the right."""

    def __init__(self, iface, changes, collapse_action, resolve=None):
        super().__init__(objectName="ArcSkinTitleRow")
        win = iface.mainWindow()
        row = QHBoxLayout(self)
        row.setContentsMargins(6, 2, 6, 2)
        row.setSpacing(2)
        for name in QUICK_ACCESS:
            if name == "|":
                line = QFrame(objectName="ArcSkinRibbonSeparator")
                line.setFrameShape(QFrame.Shape.VLine)
                row.addWidget(line)
                continue
            action = resolve(name) if resolve else find_action(win, name)
            if action:
                button = QToolButton(autoRaise=True)
                button.setDefaultAction(action)
                button.setToolButtonStyle(
                    Qt.ToolButtonStyle.ToolButtonIconOnly)
                row.addWidget(button)
        self.project = QLabel(objectName="ArcSkinProjectName")
        row.addWidget(self.project)
        row.addStretch(1)
        self.locator = win.statusBar().findChild(QWidget, "LocatorWidget")
        if self.locator:
            changes.set(self.locator, "minimumWidth", 420)
            changes.set(self.locator, "maximumWidth", 420)
            row.addWidget(self.locator)  # moved out of the status bar
            changes.add(lambda: iface.statusBarIface().addPermanentWidget(
                self.locator, 0, QgsStatusBar.Anchor.AnchorLeft))
        row.addStretch(1)
        for action in (find_action(win, "dock:MessageLog"), collapse_action):
            if action:
                button = QToolButton(autoRaise=True)
                button.setDefaultAction(action)
                button.setToolButtonStyle(
                    Qt.ToolButtonStyle.ToolButtonIconOnly)
                row.addWidget(button)
        project = QgsProject.instance()
        self._signals = [project.readProject, project.cleared,
                         project.fileNameChanged, project.titleChanged]
        for signal in self._signals:
            signal.connect(self.show_project)
        changes.add(self.disconnect_project)
        self.show_project()

    def disconnect_project(self):
        for signal in self._signals:
            try:
                signal.disconnect(self.show_project)
            except (TypeError, RuntimeError):
                pass

    def show_project(self, *_):
        """Project title (or file name) as Pro shows the project's name."""
        if sip.isdeleted(self.project):
            return
        project = QgsProject.instance()
        name = project.title() or project.baseName() or tr("Untitled")
        self.project.setText(name)


# --------------------------------------------------------------------------
# The ribbon
# --------------------------------------------------------------------------
class Ribbon(QObject):
    """Builds the ribbon, its contextual tabs and the workspace commands."""

    def __init__(self, iface, changes, toggle_action):
        super().__init__(iface.mainWindow())
        self.iface = iface
        self.win = iface.mainWindow()
        self.changes = changes
        self.toggle_action = toggle_action
        self.placed = []          # actions given a ribbon button
        self.restyled = []        # actions whose icon/caption were set
        self.absorbed = set()     # toolbar names turned into ribbon tabs
        self.context_kind = None
        self.context_pages = {}   # kind -> [(page, title)]
        self.peeking = False      # collapsed ribbon opened for one use
        self.hidden_docks = []
        self.commands = self._commands()

    # ---------------------------------------------------------- commands
    def _command(self, text, slot, icon=None, checkable=False):
        action = QAction(pro_icon(icon) if icon else pro_icon("tool"), text,
                         self.win)
        action.setCheckable(checkable)
        if checkable:
            action.toggled.connect(slot)
        else:
            action.triggered.connect(lambda *_: slot())
        self.changes.add(lambda a=action: sip.isdeleted(a) or a.deleteLater())
        return action

    def _commands(self):
        layer_page = self._layer_page
        cmds = {
            "import_layout": self._command(
                tr("Import Layout Template…"),
                lambda: import_layout_template(self.iface), "open_project"),
            "package_layers": self._command(
                tr("Package Layers…"), self._package_layers, "database"),
            "maximize_map": self._command(
                tr("Maximize Map (hide panes)"), self.set_map_maximized,
                "full_extent", checkable=True),
            "save_all": self._command(
                tr("Save All: save the edits of every layer and the project "
                   "(Ctrl+Alt+S)"), self.save_all, "save_all"),
            "collapse_ribbon": self._command(
                tr("Collapse the Ribbon (Ctrl+F1)"), self.set_collapsed,
                "collapse_all", checkable=True),
            "layer_symbology": self._command(
                tr("Symbology"), self._show_symbology, "symbology"),
            "layer_properties": self._command(
                tr("Layer Properties"), lambda: layer_page(None),
                "project_properties"),
            "layer_rendering": self._command(
                tr("Visibility Range"),
                lambda: layer_page("mOptsPage_Rendering"), "transparency"),
            "layer_diagrams": self._command(
                tr("Diagrams"), lambda: layer_page("mOptsPage_Diagrams"),
                "diagram"),
            "layer_masks": self._command(
                tr("Masking"), lambda: layer_page("mOptsPage_Masks"), "mask"),
            "layer_fields": self._command(
                tr("Fields"), lambda: layer_page("mOptsPage_SourceFields"),
                "attribute_table"),
            "layer_form": self._command(
                tr("Attributes Form"),
                lambda: layer_page("mOptsPage_AttributesForm"), "multi_edit"),
            "layer_joins": self._command(
                tr("Joins"), lambda: layer_page("mOptsPage_Joins"),
                "merge_attributes"),
            "layer_relations": self._command(
                tr("Relations"),
                lambda: self._trigger("mActionProjectProperties"), "merge"),
            "layer_histogram": self._command(
                tr("Histogram"), lambda: layer_page("mOptsPage_Histogram"),
                "histogram"),
            "attributes_pane": self._command(
                tr("Attributes pane: edit the selected features"),
                self._show_attributes, "multi_edit"),
            "dock_floating": self._command(
                tr("Dock all floating panes back on their side"),
                lambda: dock_floating_panes(self.win), "panes"),
            "dock_left": self._command(
                tr("Dock the active pane on the left"),
                lambda: dock_pane(self.win, active_pane(self.win), LEFT),
                "contents"),
            "dock_right": self._command(
                tr("Dock the active pane on the right"),
                lambda: dock_pane(self.win, active_pane(self.win), RIGHT),
                "catalog"),
            "toggle_labels": self._command(
                tr("Label the layer"), self._toggle_labels, "labeling"),
        }
        for key, keys in (("collapse_ribbon", "Ctrl+F1"),
                          ("save_all", "Ctrl+Alt+S")):
            action = cmds[key]
            action.setShortcut(QKeySequence(keys))
            action.setShortcutContext(Qt.ShortcutContext.ApplicationShortcut)
            self.win.addAction(action)
            self.changes.add(lambda a=action: sip.isdeleted(a)
                             or self.win.removeAction(a))
        return cmds

    def save_all(self):
        """Pro's Save All: save the edits of every layer being edited (they
        stay in edit mode), then save the project."""
        saved, failed = [], []
        for layer in QgsProject.instance().mapLayers().values():
            if not isinstance(layer, QgsVectorLayer):
                continue
            if not (layer.isEditable() and layer.isModified()):
                continue
            if layer.commitChanges(False):
                saved.append(layer.name())
            else:
                failed.append((layer.name(), layer.commitErrors()))
                layer.triggerRepaint()
        for layer in QgsProject.instance().mapLayers().values():
            if isinstance(layer, QgsVectorLayer) and saved:
                layer.triggerRepaint()
        bar = self.iface.messageBar()
        if failed:
            name, errors = failed[0]
            detail = "; ".join(errors[:3]) if errors else ""
            bar.pushWarning(
                tr("Save All"),
                tr("Could not save the edits of {}: {}").format(
                    ", ".join(n for n, _ in failed), detail))
        # QGIS's own Save Project: asks for a file name for a new project.
        self._trigger("mActionSaveProject")
        project = QgsProject.instance()
        if not failed and not project.isDirty():
            what = (tr("Saved the edits of {} layer(s) and the project.").format(
                len(saved)) if saved else tr("Saved the project."))
            bar.pushSuccess(tr("Save All"), what)

    def _trigger(self, name):
        action = find_action(self.win, name)
        if action:
            action.trigger()

    def _layer_page(self, page):
        layer = self.iface.activeLayer()
        if layer is None:
            return
        try:
            if page:
                self.iface.showLayerProperties(layer, page)
            else:
                self.iface.showLayerProperties(layer)
        except TypeError:  # older signature without the page
            self.iface.showLayerProperties(layer)

    def _show_attributes(self):
        pane = self.win.findChild(QDockWidget, "WorkspaceAttributes")
        if pane is not None:
            pane.show()
            pane.raise_()
            pane.refresh()

    def _show_symbology(self):
        dock = self.win.findChild(QDockWidget, "LayerStyling")
        if dock:
            dock.show()
            dock.raise_()

    def _toggle_labels(self):
        layer = self.iface.activeLayer()
        if not isinstance(layer, QgsVectorLayer):
            return
        if layer.labeling() is None:
            # Nothing to switch on yet: open the label settings instead.
            self._trigger("mActionLabeling")
            return
        layer.setLabelsEnabled(not layer.labelsEnabled())
        layer.triggerRepaint()
        self.iface.layerTreeView().refreshLayerSymbology(layer.id())

    def _package_layers(self):
        try:
            import processing
            processing.execAlgorithmDialog("native:package")
        except Exception:  # noqa: BLE001 (processing missing or failed)
            self.iface.messageBar().pushWarning(
                tr("Workspace Ribbon"),
                tr("The Package Layers tool is not available."))

    # ----------------------------------------------------- map workspace
    def set_map_maximized(self, on):
        """Hide all docked panes (Pro's map-maximised workspace), or bring
        back the ones that were open."""
        if on:
            self.hidden_docks = [
                d for d in self.win.findChildren(QDockWidget)
                if d.isVisible() and not d.isFloating()]
            for dock in self.hidden_docks:
                dock.hide()
        else:
            for dock in self.hidden_docks:
                if not sip.isdeleted(dock):
                    dock.show()
            self.hidden_docks = []

    def _pages(self):
        return self.tabs.findChild(QStackedWidget)

    def set_collapsed(self, on, remember=True):
        """Show only the tab names; a click on a tab opens the ribbon until
        the next click in the map."""
        self.peeking = False
        stack = self._pages()
        if stack is not None:
            stack.setVisible(not on)
        self.tabs.setMaximumHeight(
            self.tabs.tabBar().sizeHint().height() + 2 if on else 16777215)
        action = self.commands["collapse_ribbon"]
        if action.isChecked() != on:
            action.blockSignals(True)
            action.setChecked(on)
            action.blockSignals(False)
        if remember:
            QgsSettings().setValue(COLLAPSED_KEY, on)

    def _tab_clicked(self, _index):
        if self.commands["collapse_ribbon"].isChecked():
            stack = self._pages()
            if stack is not None and not stack.isVisible():
                stack.setVisible(True)
                self.tabs.setMaximumHeight(16777215)
                self.peeking = True

    def fold_if_peeking(self):
        if self.peeking:
            self.set_collapsed(True, remember=False)

    # ------------------------------------------------------------ buttons
    def resolve(self, name):
        """One action for a ribbon item name (see RIBBON)."""
        if name.startswith("fn:"):
            return self.commands.get(name[3:])
        if name.startswith("plugin:"):
            return plugin_action(name[7:])
        if name.startswith("bar:"):
            return bar_action(self.win, name[4:])
        return find_action(self.win, name)

    def button(self, names, size, icon, caption):
        """Ribbon button for an item; None when nothing it names exists."""
        large = size == "L"
        button = QToolButton(autoRaise=True)
        win = self.win
        if names in ("gallery:basemaps", "panes:reset", "layouts:new",
                     "layouts:open", "layouts:export"):
            button.setText(caption)
            button.setIcon(pro_icon(icon))
            popup = QToolButton.ToolButtonPopupMode.InstantPopup
            if names == "gallery:basemaps":
                menu = gallery_menu(self.iface, button)
            elif names == "panes:reset":
                button.clicked.connect(lambda: reset_panes(win))
                menu = reset_panes_menu(win, button)
                popup = QToolButton.ToolButtonPopupMode.MenuButtonPopup
            elif names == "layouts:new":
                menu = new_layout_menu(self.iface, button)
            elif names == "layouts:open":
                menu = open_layouts_menu(self.iface, button)
            else:
                menu = export_layouts_menu(self.iface, button)
            button.setMenu(menu)
            button.setPopupMode(popup)
            return _style(button, large)
        actions, menu = [], None
        for name in names.split():
            if name.startswith("menu:"):
                menu = menu or find_menu(win, name[5:])
            elif name.startswith("shapes:"):
                actions += _shape_actions(win, name[7:])
            elif (action := self.resolve(name)) is not None:
                actions.append(action)
        if actions:
            # The first ribbon place of an action names it; later places
            # (e.g. a small copy on another tab) keep that caption.
            if actions[0] not in self.restyled:
                restyle(self.changes, actions[0], icon, caption)
                self.restyled.append(actions[0])
            button.setDefaultAction(actions[0])
            self.placed.extend(actions)
        elif menu:
            button.setText(caption)
            button.setIcon(pro_icon(icon))
        else:
            return None
        if not menu and len(actions) > 1:
            menu = QMenu(button)
            menu.addActions(actions)
        if menu:
            button.setMenu(menu)
            button.setPopupMode(
                QToolButton.ToolButtonPopupMode.MenuButtonPopup if actions
                else QToolButton.ToolButtonPopupMode.InstantPopup)
        return _style(button, large)

    def groups(self, spec):
        built = []
        for caption, items in spec:
            buttons = [(b, item[1] == "L") for item in items
                       if (b := self.button(*item))]
            if buttons:
                built.append((caption, buttons))
        return built

    def mirror(self, bar, action, short=None, skip_placed=False):
        """Ribbon button for an action on a plugin toolbar: the action
        itself, or a copy of a plain tool button the plugin put there."""
        if isinstance(action, QWidgetAction):
            widget = action.defaultWidget() or bar.widgetForAction(action)
            if not isinstance(widget, QToolButton):
                return None  # spacers and other widgets
            if widget.defaultAction():
                return self.mirror(bar, widget.defaultAction(), short,
                                   skip_placed)
            button = QToolButton(autoRaise=True)
            button.setText(widget.text())
            button.setIcon(widget.icon())
            button.setToolTip(widget.toolTip())
            if widget.menu():
                button.setMenu(widget.menu())
                button.setPopupMode(widget.popupMode())
            else:
                button.clicked.connect(widget.click)
            return button
        if action.isSeparator() or not action.text():
            return None
        if skip_placed and action in self.placed:
            return None
        if short and (caption := short.get(_plain(action.text()))):
            self.changes.set(action, "iconText", caption)
        button = QToolButton(autoRaise=True)
        button.setDefaultAction(action)
        self.placed.append(action)
        return button

    def plugin_tab(self, bar, captions, short, extra):
        """Groups of a plugin toolbar, split at its separators: the first
        three buttons of a group large, the rest small, as in Pro."""
        runs, current = [], []
        for action in bar.actions():
            if action.isSeparator():
                if current:
                    runs.append(current)
                current = []
                continue
            if (button := self.mirror(bar, action, short)) is not None:
                current.append(button)
        if current:
            runs.append(current)
        built = []
        for i, buttons in enumerate(runs):
            caption = captions[i] if i < len(captions) else bar.windowTitle()
            built.append((caption, [(_style(b, j < 3), j < 3)
                                    for j, b in enumerate(buttons)]))
        for caption, names in extra:
            buttons = []
            for name in names:
                action = self.resolve(name)
                if action is None:
                    continue
                button = QToolButton(autoRaise=True)
                button.setDefaultAction(action)
                self.placed.append(action)
                large = len(names) <= 2
                buttons.append((_style(button, large), large))
            if buttons:
                built.append((caption, buttons))
        return built

    def addin_groups(self, bars):
        """One group per remaining plugin toolbar: Pro's Add-ins tab."""
        groups = []
        for bar in bars:
            name = bar.objectName()
            if name in self.absorbed:
                continue
            if name.startswith("m") and name not in ADDIN_HOSTS:
                continue
            buttons = []
            for action in bar.actions():
                button = self.mirror(bar, action, skip_placed=True)
                if button is not None:
                    buttons.append((_style(button, True), True))
            if buttons:
                groups.append((bar.windowTitle(), buttons))
        return groups

    def project_button(self):
        """Blue "Project" tab: project actions plus the hidden QGIS menus."""
        win = self.win
        button = QToolButton(objectName="ArcSkinProjectButton",
                             text=tr("Project"))
        menu = QMenu(button)
        for name, icon in PROJECT_MENU:
            if name.startswith("menu:"):
                submenu = find_menu(win, name[5:])
                if submenu:
                    menu.addMenu(submenu)
                continue
            action = self.resolve(name)
            if action:
                if icon:
                    restyle(self.changes, action, icon)
                menu.addAction(action)
        menu.addSeparator()
        qgis_menu = menu.addMenu(tr("QGIS Menu"))
        qgis_menu.addActions(win.menuBar().actions())
        menu.addAction(self.toggle_action)
        exit_action = find_action(win, "mActionExit")
        if exit_action:
            menu.addSeparator()
            menu.addAction(exit_action)
        button.setMenu(menu)
        button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        return button

    # -------------------------------------------------------- context tabs
    def layer_changed(self, layer):
        kind = None
        if layer is not None and not sip.isdeleted(layer):
            if isinstance(layer, QgsVectorLayer) and layer.isSpatial():
                kind = "vector"
            elif isinstance(layer, QgsVectorLayer):
                kind = "table"
            elif isinstance(layer, QgsRasterLayer):
                kind = "raster"
        if kind == self.context_kind:
            return
        current = self.tabs.currentWidget()
        for page, _ in self.context_pages.get(self.context_kind, []):
            index = self.tabs.indexOf(page)
            if index >= 0:
                self.tabs.removeTab(index)
        self.context_kind = kind
        if kind is None:
            return
        if kind not in self.context_pages:
            group_name, color, spec = CONTEXT_TABS[kind]
            pages = []
            for title, groups in spec:
                built = self.groups(groups)
                if built:
                    pages.append((_tab(built), title))
            self.context_pages[kind] = pages
        group_name, color, _ = CONTEXT_TABS[kind]
        for page, title in self.context_pages[kind]:
            index = self.tabs.addTab(page, title)
            self.tabs.tabBar().setTabData(index, color)
            self.tabs.setTabToolTip(index, f"{group_name}: {title}")
        if self.tabs.indexOf(current) < 0:
            self.tabs.setCurrentIndex(0)

    # ---------------------------------------------------------------- build
    def build(self):
        """Hide the menu bar and toolbars and put the ribbon in their place.
        Toolbar visibility comes back with the saved window state."""
        win = self.win
        bars = [b for b in win.findChildren(QToolBar) if b.parent() is win]
        for bar in bars:
            bar.hide()
        self.changes.set(win.menuBar(), "visible", False)
        tabs = self.tabs = QTabWidget(objectName="ArcSkinRibbon")
        tabs.setTabBar(RibbonTabBar(tabs))
        tabs.setCornerWidget(self.project_button(), Qt.Corner.TopLeftCorner)

        for title, groups in RIBBON:
            if title == "@plugin_tabs":
                for (ptitle, bar_name, captions, short,
                     extra) in PLUGIN_TABS:
                    bar = win.findChild(QToolBar, bar_name)
                    if bar is None or bar.parent() is not win:
                        continue
                    built = self.plugin_tab(bar, captions, short, extra)
                    if built:
                        self.absorbed.add(bar_name)
                        tabs.addTab(_tab(built), ptitle)
                continue
            built = self.groups(groups)
            if built:
                tabs.addTab(_tab(built), title)
        addins = self.addin_groups(bars)
        if addins:
            tabs.addTab(_tab(addins), tr("Add-ins"))

        ribbon = QToolBar("Workspace Ribbon", win,
                          objectName="ArcSkinRibbonBar")
        ribbon.setMovable(False)
        ribbon.setFloatable(False)
        frame = QWidget()
        stack = QVBoxLayout(frame)
        stack.setContentsMargins(0, 0, 0, 0)
        stack.setSpacing(0)
        title = TitleRow(self.iface, self.changes,
                         self.commands["collapse_ribbon"], self.resolve)
        stack.addWidget(title)
        stack.addWidget(tabs)
        ribbon.addWidget(frame)
        win.addToolBar(Qt.ToolBarArea.TopToolBarArea, ribbon)
        self.changes.add(lambda: (win.removeToolBar(ribbon),
                                  ribbon.deleteLater()))
        if title.locator:
            _drop_down_results(self.changes)

        # Plugin toolbars now in the ribbon stay hidden.
        keeper = ToolbarKeeper(win, [win.findChild(QToolBar, n) for n in
                                     self.absorbed | {"mPluginToolBar"}])
        self.changes.add(keeper.stop)

        # Collapsing and peeking.
        tabs.tabBarDoubleClicked.connect(
            lambda _i: self.commands["collapse_ribbon"].toggle())
        tabs.tabBarClicked.connect(self._tab_clicked)
        watcher = CanvasWatcher(self, self.iface.mapCanvas())
        self.changes.add(watcher.stop)
        if QgsSettings().value(COLLAPSED_KEY, False, type=bool):
            self.set_collapsed(True, remember=False)
        self.changes.add(lambda: self.set_map_maximized(False)
                         if self.hidden_docks else None)

        # Contextual tabs follow the layer selected in Contents.
        self.iface.currentLayerChanged.connect(self.layer_changed)
        self.changes.add(self._disconnect_layer)
        self.layer_changed(self.iface.activeLayer())

    def _disconnect_layer(self):
        try:
            self.iface.currentLayerChanged.disconnect(self.layer_changed)
        except (TypeError, RuntimeError):
            pass


def build_ribbon(iface, changes, toggle_action):
    """Build the Workspace Ribbon; everything is undone through changes."""
    ribbon = Ribbon(iface, changes, toggle_action)
    ribbon.build()
    changes.add(lambda: sip.isdeleted(ribbon) or ribbon.deleteLater())
    return ribbon
