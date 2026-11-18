"""Floating sketch toolbar at the bottom of the map, like ArcGIS Pro's.

While a feature is being drawn (any QGIS capture tool: add feature, add
part, reshape, split, the shape tools…), a small toolbar floats at the
bottom centre of the map:

    Line · Arc · Bézier · Freehand | Trace · Snapping · Advanced
    Digitizing | Undo vertex · Finish (F2) · Cancel (Esc)

The construction buttons are QGIS's own digitizing actions, so their state
stays in step with the Advanced Digitizing toolbar. Finish sends the map
the right-click that ends a sketch; F2 does the same while the map has the
keyboard focus, as in Pro.
"""

from qgis.gui import QgsMapToolCapture
from qgis.PyQt import sip
from qgis.PyQt.QtCore import QEvent, QPointF, Qt, QTimer
from qgis.PyQt.QtGui import QAction, QCursor, QKeyEvent, QKeySequence, QMouseEvent
from qgis.PyQt.QtWidgets import (
    QApplication, QDockWidget, QFrame, QHBoxLayout, QToolButton,
)

from .util import pro_icon, tr

# (QGIS action name or command, Pro icon, label)
BUTTONS = [
    ("mActionDigitizeWithSegment", "create_feature", tr("Line")),
    ("mActionDigitizeWithCurve", "fillet", tr("Arc")),
    ("mActionDigitizeWithBezier", "reshape", tr("Bézier")),
    ("mActionStreamDigitize", "simplify", tr("Freehand")),
    ("|", None, None),
    ("EnableTracingAction", "tracing", tr("Trace")),
    ("EnableSnappingAction", "snapping", tr("Snapping")),
    ("dock:AdvancedDigitizingTools", "advanced_digitizing",
     tr("Advanced Digitizing")),
    ("|", None, None),
    ("cmd:undo", "undo", tr("Undo vertex (Backspace)")),
    ("cmd:finish", "save_edits", tr("Finish (F2)")),
    ("cmd:cancel", "discard_edits", tr("Cancel (Esc)")),
]


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
        for name, icon, label in BUTTONS:
            if name == "|":
                line = QFrame(self)
                line.setFrameShape(QFrame.Shape.VLine)
                row.addWidget(line)
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
        return tool if isinstance(tool, QgsMapToolCapture) else None

    def _tool_set(self, new_tool, _old=None):
        if isinstance(new_tool, QgsMapToolCapture):
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

    def undo(self):
        """Remove the last vertex (Backspace)."""
        if self._capturing_tool() is not None:
            self._key(Qt.Key.Key_Backspace)

    def cancel(self):
        """Drop the sketch (Esc)."""
        if self._capturing_tool() is not None:
            self._key(Qt.Key.Key_Escape)

    def finish(self):
        """End the sketch: QGIS finishes a feature on a right-click."""
        if self._capturing_tool() is None:
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
        except (TypeError, RuntimeError):
            pass
        if not sip.isdeleted(self.canvas):
            self.canvas.removeEventFilter(self)
            self.canvas.removeAction(self.finish_action)
        self.hide()
        self.deleteLater()


def sketch_toolbar(iface, changes):
    bar = SketchToolbar(iface)
    changes.add(bar.stop)
    return bar
