"""Selection chip, like ArcGIS Pro's.

A click with the Select tool where features of several layers (or several
features of one layer) overlap:

* selects the top-most feature, on any selectable layer, as Pro does (QGIS
  alone only looks at the active layer). If QGIS already selected one of
  the features under the click on the active layer, that one is kept;
* shows a small chip next to the cursor with the number of features found
  there. Click the chip for a list; each entry shows the layer name (and
  the feature's display name when it has one). Hovering an entry
  highlights the feature on the map, clicking it selects that feature
  instead (and makes its layer the active layer).

The chip goes away at the next click on the map, when the map moves or
when another tool is picked. Clicks with Shift or Ctrl (add to / remove
from the selection) are left to QGIS and get no chip.
"""

from qgis.core import Qgis, QgsMapLayer, QgsMessageLog, QgsVectorLayer
from qgis.gui import QgsHighlight
from qgis.PyQt import sip
from qgis.PyQt.QtCore import QEvent, QObject, QPoint, Qt, QTimer
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtWidgets import QMenu, QToolButton

from .explore import CLICK_TOLERANCE, _label, features_at
from .util import pro_icon, tr

SELECT_ACTION = "mActionSelectFeatures"
CHIP_OFFSET = QPoint(14, 14)   # from the click, like Pro's chip


def menu_labels(hits):
    """Text for each (layer, feature). The layer name is the choice.

    A display name is added when the layer has one. Two features that
    would read the same get the feature id in parentheses. Menu section
    titles are not used: the skin draws every separator as a 1px line, so
    a section's layer name would not show.
    """
    labels = []
    for layer, feature in hits:
        label = _label(layer, feature)
        name = layer.name() or tr("(unnamed layer)")
        if not label or label == str(feature.id()):
            labels.append(name)
        else:
            labels.append(f"{name}: {label}")
    counts = {}
    for text in labels:
        counts[text] = counts.get(text, 0) + 1
    out = []
    for (_layer, feature), text in zip(hits, labels):
        if counts[text] > 1:
            text = f"{text} ({feature.id()})"
        out.append(text)
    return out


def _selectable(layer):
    try:
        return bool(layer.flags() & QgsMapLayer.LayerFlag.Selectable)
    except AttributeError:   # older enum spelling
        return bool(layer.flags() & QgsMapLayer.Selectable)


def _pos(event):
    """Event position as a QPoint (Qt 6 and Qt 5)."""
    try:
        return event.position().toPoint()
    except AttributeError:
        return event.pos()


class SelectionChip(QObject):
    """Watches clicks on the map while the Select tool is active."""

    def __init__(self, iface):
        canvas = iface.mapCanvas()
        super().__init__(canvas)
        self.iface = iface
        self.canvas = canvas
        self.hits = []
        self.press = None
        self.highlight = None

        self.chip = QToolButton(canvas, objectName="WorkspaceSelectionChip")
        self.chip.setIcon(pro_icon("select"))
        self.chip.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.chip.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.chip.setCursor(Qt.CursorShape.ArrowCursor)
        self.chip.setStyleSheet(
            "QToolButton#WorkspaceSelectionChip {"
            " background: white; border: 1px solid #8a8a8a;"
            " border-radius: 3px; padding: 1px 4px; }"
            "QToolButton#WorkspaceSelectionChip:hover {"
            " background: #e5f1fb; border-color: #0078d4; }")
        self.menu = QMenu(self.chip)
        self.menu.hovered.connect(self._hovered)
        self.menu.aboutToHide.connect(self._clear_highlight)
        self.chip.setMenu(self.menu)
        self.chip.hide()

        canvas.viewport().installEventFilter(self)
        canvas.mapToolSet.connect(self.hide)
        canvas.extentsChanged.connect(self.hide)

    # ------------------------------------------------------------ clicks
    def _select_tool_active(self):
        tool = self.canvas.mapTool()
        if tool is None or sip.isdeleted(tool):
            return False
        action = tool.action()
        return action is not None and action.objectName() == SELECT_ACTION

    def eventFilter(self, watched, event):  # noqa: N802 (Qt API name)
        kind = event.type()
        if kind == QEvent.Type.MouseButtonPress:
            self.hide()
            plain = (event.button() == Qt.MouseButton.LeftButton
                     and event.modifiers() == Qt.KeyboardModifier.NoModifier)
            self.press = (_pos(event) if plain and self._select_tool_active()
                          else None)
        elif kind == QEvent.Type.MouseButtonRelease and self.press is not None:
            press, self.press = self.press, None
            pos = _pos(event)
            if (pos - press).manhattanLength() <= CLICK_TOLERANCE:
                # After QGIS's own Select tool has handled the click.
                QTimer.singleShot(0, lambda: self._clicked(pos))
        return False

    def _clicked(self, pos):
        if sip.isdeleted(self.canvas) or not self._select_tool_active():
            return
        self.hits = features_at(self.canvas, pos, _selectable)
        if not self.hits:
            return
        if not self._qgis_picked_a_hit():
            self._select(*self.hits[0])
        if len(self.hits) > 1:
            self._show_chip(pos)

    def _qgis_picked_a_hit(self):
        """True if the selection already holds a feature under the click."""
        for layer, feature in self.hits:
            if feature.id() in layer.selectedFeatureIds():
                return True
        return False

    # -------------------------------------------------------------- chip
    def _show_chip(self, pos):
        self.menu.clear()
        for (layer, feature), text in zip(self.hits, menu_labels(self.hits)):
            action = self.menu.addAction(text)
            action.setData((layer.id(), feature.id()))
            action.setCheckable(True)
            action.setChecked(feature.id() in layer.selectedFeatureIds())
            action.triggered.connect(
                lambda _on=False, a=action: self._picked(a))
        layers = len({layer.id() for layer, _f in self.hits})
        self.chip.setText(str(len(self.hits)))
        self.chip.setToolTip(
            tr("{0} features from {1} layer(s) here. Click to choose which "
               "one to select.").format(len(self.hits), layers))
        self.chip.adjustSize()
        spot = self.canvas.viewport().mapTo(self.canvas, pos) + CHIP_OFFSET
        rect = self.canvas.rect()
        size = self.chip.sizeHint()
        spot.setX(min(spot.x(), rect.width() - size.width() - 2))
        spot.setY(min(spot.y(), rect.height() - size.height() - 2))
        self.chip.move(spot)
        self.chip.show()
        self.chip.raise_()

    def hide(self, *_):
        if not sip.isdeleted(self.chip):
            self.chip.hide()
        self._clear_highlight()

    def _feature(self, action):
        data = action.data() if action is not None else None
        if not data:
            return None, None
        layer_id, fid = data
        for layer, feature in self.hits:
            if layer.id() == layer_id and feature.id() == fid:
                return layer, feature
        return None, None

    def _hovered(self, action):
        self._clear_highlight()
        layer, feature = self._feature(action)
        if layer is None or not feature.hasGeometry():
            return
        self.highlight = QgsHighlight(self.canvas, feature.geometry(), layer)
        self.highlight.setColor(QColor(0, 255, 255, 220))
        self.highlight.setFillColor(QColor(0, 255, 255, 60))
        self.highlight.setWidth(3)
        self.highlight.show()

    def _clear_highlight(self):
        if self.highlight is not None and not sip.isdeleted(self.highlight):
            self.highlight.hide()
            self.canvas.scene().removeItem(self.highlight)
        self.highlight = None

    def _picked(self, action):
        layer, feature = self._feature(action)
        if layer is not None:
            self._select(layer, feature)
        self.hide()

    def _select(self, layer, feature):
        """Make ``feature`` the only selected feature, as Pro does."""
        for other in self.canvas.layers():
            if isinstance(other, QgsVectorLayer) and other is not layer \
                    and other.selectedFeatureCount():
                other.removeSelection()
        layer.selectByIds([feature.id()])
        self.iface.setActiveLayer(layer)

    def stop(self):
        try:
            self.canvas.mapToolSet.disconnect(self.hide)
            self.canvas.extentsChanged.disconnect(self.hide)
        except (TypeError, RuntimeError) as err:
            QgsMessageLog.logMessage(
                f"selection_chip.stop: a canvas signal was already disconnected: {err!r}",
                "Keystone", Qgis.MessageLevel.Info)
        if not sip.isdeleted(self.canvas):
            self.canvas.viewport().removeEventFilter(self)
        self.hide()
        if not sip.isdeleted(self.chip):
            self.chip.deleteLater()
        self.deleteLater()


def selection_chip(iface, changes):
    chip = SelectionChip(iface)
    changes.add(chip.stop)
    return chip
