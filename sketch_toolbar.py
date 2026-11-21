"""Floating sketch toolbar at the bottom of the map, like ArcGIS Pro's.

While a feature is being drawn (any QGIS capture tool: add feature, add
part, reshape, split, the shape tools…), a small toolbar floats at the
bottom centre of the map:

    Line · Arc · Bézier · Freehand | Trace · Snapping · Advanced
    Digitizing | Undo vertex · Finish (F2) · Cancel (Esc)

While the Vertex tool is active (Pro's Edit Vertices) it shows the vertex
version instead:

    Snapping · Advanced Digitizing · Vertex Editor | Delete vertex ·
    Undo · Finish (F2) · Cancel (Esc)

QGIS's own tools are C++ classes that PyQGIS hands back as a plain
QgsMapTool, so isinstance() cannot recognise them: the tool's Qt class
chain (metaObject) is checked instead.

The construction buttons are QGIS's own digitizing actions, so their state
stays in step with the Advanced Digitizing toolbar. Finish sends the map
the right-click that ends a sketch; F2 does the same while the map has the
keyboard focus, as in Pro.
"""

from qgis.core import Qgis, QgsMessageLog
from qgis.gui import QgsMapToolCapture
from qgis.PyQt import sip
from qgis.PyQt.QtCore import QEvent, QPointF, Qt, QTimer
from qgis.PyQt.QtGui import QAction, QCursor, QKeyEvent, QKeySequence, QMouseEvent
from qgis.PyQt.QtWidgets import (
    QApplication, QDockWidget, QFrame, QHBoxLayout, QToolButton,
)

from .util import pro_icon, tr

CAPTURE, VERTEX = "capture", "vertex"
C, V, CV = {CAPTURE}, {VERTEX}, {CAPTURE, VERTEX}

# (QGIS action name or command, Pro icon, label, modes shown in)
BUTTONS = [
    ("mActionDigitizeWithSegment", "create_feature", tr("Line"), C),
    ("mActionDigitizeWithCurve", "fillet", tr("Arc"), C),
    ("mActionDigitizeWithBezier", "reshape", tr("Bézier"), C),
    ("mActionStreamDigitize", "simplify", tr("Freehand"), C),
    ("|", None, None, C),
    ("EnableTracingAction", "tracing", tr("Trace"), C),
    ("EnableSnappingAction", "snapping", tr("Snapping"), CV),
    ("dock:AdvancedDigitizingTools", "advanced_digitizing",
     tr("Advanced Digitizing"), CV),
    ("dock:VertexEditor", "vertex_tool", tr("Vertex Editor"), V),
    ("|", None, None, CV),
    ("cmd:delete_vertex", "delete", tr("Delete selected vertices (Del)"), V),
    ("cmd:undo", "undo", tr("Undo vertex (Backspace)"), C),
    ("cmd:undo_edit", "undo", tr("Undo (Ctrl+Z)"), V),
    ("cmd:finish", "save_edits", tr("Finish (F2)"), CV),
    ("cmd:cancel", "discard_edits", tr("Cancel (Esc)"), CV),
]

# Qt class names of QGIS's vertex tools (app classes, not in PyQGIS).
VERTEX_CLASSES = {"QgsVertexTool"}
VERTEX_ACTIONS = {"mActionVertexTool", "mActionVertexToolActiveLayer"}


def _class_chain(tool):
    """Qt class names of ``tool`` and its base classes."""
    names = []
    try:
        meta = tool.metaObject()
    except (AttributeError, RuntimeError):
        return names
    while meta is not None:
        names.append(meta.className())
        meta = meta.superClass()
    return names


def tool_mode(tool):
    """CAPTURE for a drawing tool, VERTEX for the Vertex tool, else None."""
    if tool is None or sip.isdeleted(tool):
        return None
    if isinstance(tool, QgsMapToolCapture):
        return CAPTURE
    chain = _class_chain(tool)
    if "QgsMapToolCapture" in chain:
        return CAPTURE
    if VERTEX_CLASSES.intersection(chain):
        return VERTEX
    action = tool.action()
    if action is not None and action.objectName() in VERTEX_ACTIONS:
        return VERTEX
    return None


class SketchToolbar(QFrame):
    """The floating toolbar, a child of the map canvas."""

    def __init__(self, iface):
        canvas = iface.mapCanvas()
        super().__init__(canvas, objectName="WorkspaceSketchToolbar")
        self.iface = iface
        self.canvas = canvas
        win = iface.mainWindow()
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setAutoFillBackground(True)
        row = QHBoxLayout(self)
        row.setContentsMargins(4, 2, 4, 2)
        row.setSpacing(1)
        self.mode = None
        self.parts = []   # (widget, modes)
        for name, icon, label, modes in BUTTONS:
            if name == "|":
                line = QFrame(self)
                line.setFrameShape(QFrame.Shape.VLine)
                row.addWidget(line)
                self.parts.append((line, modes))
                continue
            button = QToolButton(self, autoRaise=True)
            if name.startswith("cmd:"):
                button.setIcon(pro_icon(icon))
                button.setToolTip(label)
                button.clicked.connect(getattr(self, name[4:]))
            else:
                if name.startswith("dock:"):
                    dock = win.findChild(QDockWidget, name[5:])
                    action = dock.toggleViewAction() if dock else None
                else:
                    action = win.findChild(QAction, name)
                if action is None:
                    button.deleteLater()
                    continue
                button.setDefaultAction(action)
                button.setIcon(pro_icon(icon))
                button.setToolTip(label)
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
            row.addWidget(button)
            self.parts.append((button, modes))
        self.hide()

        # F2 finishes the sketch while the map has the focus (not app-wide:
        # F2 also renames layers in the Layers panel).
        self.finish_action = QAction(tr("Finish sketch"), canvas)
        self.finish_action.setShortcut(QKeySequence("F2"))
        self.finish_action.setShortcutContext(
            Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self.finish_action.triggered.connect(self.finish)
        canvas.addAction(self.finish_action)

        canvas.mapToolSet.connect(self._tool_set)
        canvas.installEventFilter(self)
        self._tool_set(canvas.mapTool(), None)

    # ------------------------------------------------------------- showing
    def _capturing_tool(self):
        tool = self.canvas.mapTool()
        return tool if tool_mode(tool) == CAPTURE else None

    def _tool_set(self, new_tool, _old=None):
        mode = tool_mode(new_tool)
        if mode is not None:
            if mode != self.mode:
                self.mode = mode
                for widget, modes in self.parts:
                    widget.setVisible(mode in modes)
            self.adjustSize()
            self._place()
            self.show()
            self.raise_()
        else:
            self.hide()

    def _place(self):
        size = self.sizeHint()
        rect = self.canvas.rect()
        self.setGeometry((rect.width() - size.width()) // 2,
                         rect.height() - size.height() - 28,
                         size.width(), size.height())

    def eventFilter(self, watched, event):  # noqa: N802 (Qt API name)
        if watched is self.canvas and event.type() == QEvent.Type.Resize \
                and self.isVisible():
            QTimer.singleShot(0, self._place)
        return False

    # ------------------------------------------------------------ commands
    def _key(self, key):
        self.canvas.setFocus()
        for kind in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease):
            QApplication.sendEvent(
                self.canvas, QKeyEvent(kind, key,
                                       Qt.KeyboardModifier.NoModifier))

    def _mode(self):
        return tool_mode(self.canvas.mapTool())

    def undo(self):
        """Remove the last vertex (Backspace)."""
        if self._capturing_tool() is not None:
            self._key(Qt.Key.Key_Backspace)

    def undo_edit(self):
        """Undo the last edit of the active layer (Ctrl+Z)."""
        action = self.iface.mainWindow().findChild(QAction, "mActionUndo")
        if action is not None and action.isEnabled():
            action.trigger()

    def delete_vertex(self):
        """Delete the selected vertices (Del), Vertex tool only."""
        if self._mode() == VERTEX:
            self._key(Qt.Key.Key_Delete)

    def cancel(self):
        """Drop the sketch / the vertex selection (Esc)."""
        if self._mode() is not None:
            self._key(Qt.Key.Key_Escape)

    def finish(self):
        """End the sketch: QGIS finishes a feature on a right-click.

        With the Vertex tool (whose edits are already applied), Finish
        clears the vertex selection and goes back to the Select tool, as
        Pro's Finish ends Edit Vertices.
        """
        mode = self._mode()
        if mode == VERTEX:
            self._key(Qt.Key.Key_Escape)
            select = self.iface.mainWindow().findChild(
                QAction, "mActionSelectFeatures")
            if select is not None:
                select.trigger()
            return
        if mode != CAPTURE:
            return
        viewport = self.canvas.viewport()
        pos = viewport.mapFromGlobal(QCursor.pos())
        if not viewport.rect().contains(pos):
            pos = viewport.rect().center()
        point = QPointF(pos)
        for kind in (QEvent.Type.MouseButtonPress,
                     QEvent.Type.MouseButtonRelease):
            buttons = (Qt.MouseButton.RightButton
                       if kind == QEvent.Type.MouseButtonPress
                       else Qt.MouseButton.NoButton)
            event = QMouseEvent(kind, point, Qt.MouseButton.RightButton,
                                buttons, Qt.KeyboardModifier.NoModifier)
            QApplication.sendEvent(viewport, event)

    def stop(self):
        try:
            self.canvas.mapToolSet.disconnect(self._tool_set)
        except (TypeError, RuntimeError) as err:
            QgsMessageLog.logMessage(
                f"sketch_toolbar.stop: mapToolSet was already disconnected: {err!r}",
                "Keystone", Qgis.MessageLevel.Info)
        if not sip.isdeleted(self.canvas):
            self.canvas.removeEventFilter(self)
            self.canvas.removeAction(self.finish_action)
        self.hide()
        self.deleteLater()


def sketch_toolbar(iface, changes):
    bar = SketchToolbar(iface)
    changes.add(bar.stop)
    return bar
