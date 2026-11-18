"""Controls in the contextual Feature Layer / Raster Layer tabs, as in Pro.

Pro's Appearance and Labeling tabs change the selected layer right from
the ribbon, without opening Layer Properties. These are those controls:

* Appearance: a Transparency slider, the visibility range (Out Beyond / In
  Beyond scales, with Clear) and Swipe, which peels the layer back with a
  line you drag across the map to compare it with what is under it.
* Labeling: the label field (or expression), font, size, colour and
  placement. Changing one turns the layer's labels on.

All controls follow the layer selected in Contents.
"""

from qgis.core import (
    Qgis, QgsMapRendererParallelJob, QgsPalLayerSettings,
    QgsRasterLayer, QgsTextFormat, QgsVectorLayer,
    QgsVectorLayerSimpleLabeling,
)
from qgis.gui import (
    QgsColorButton, QgsFieldExpressionWidget, QgsMapCanvasItem, QgsMapTool,
    QgsScaleWidget,
)
from qgis.PyQt import sip
from qgis.PyQt.QtCore import QPointF, QRectF, Qt, QTimer
from qgis.PyQt.QtGui import QColor, QFont, QPen
from qgis.PyQt.QtWidgets import (
    QComboBox, QFontComboBox, QGridLayout, QLabel, QSlider, QSpinBox,
    QToolButton, QWidget,
)

from .util import pro_icon, tr

LP = Qgis.LabelPlacement
GT = Qgis.GeometryType
PLACEMENTS = {
    GT.Point: [(tr("Around point"), LP.OrderedPositionsAroundPoint),
               (tr("Offset from point"), LP.AroundPoint),
               (tr("Over point"), LP.OverPoint)],
    GT.Line: [(tr("Parallel"), LP.Line),
              (tr("Curved"), LP.Curved),
              (tr("Horizontal"), LP.Horizontal)],
    GT.Polygon: [(tr("Horizontal (centered)"), LP.OverPoint),
                 (tr("Around centroid"), LP.AroundPoint),
                 (tr("Horizontal (free)"), LP.Horizontal),
                 (tr("Free angle"), LP.Free),
                 (tr("Along perimeter"), LP.Line)],
}


def _row():
    box = QWidget()
    grid = QGridLayout(box)
    grid.setContentsMargins(2, 2, 2, 2)
    grid.setHorizontalSpacing(4)
    grid.setVerticalSpacing(2)
    return box, grid


# ------------------------------------------------------------------ swipe
class SwipeItem(QgsMapCanvasItem):
    """The map drawn without the swiped layer, over the part of the canvas
    past the swipe line, so the layer shows only before the line."""

    def __init__(self, canvas):
        super().__init__(canvas)
        self.image = None
        self.at = 0.5          # line position, fraction of the width
        self.vertical = True   # a vertical line, swiping sideways
        self.setZValue(1000)

    def set_image(self, image):
        self.image = image
        self.setRect(self.mapCanvas().extent())
        self.update()

    def paint(self, painter, *_):  # noqa: D401 (Qt API)
        canvas = self.mapCanvas()
        w, h = canvas.width(), canvas.height()
        if self.vertical:
            cut = QRectF(self.at * w, 0, w - self.at * w, h)
            line = (QPointF(self.at * w, 0), QPointF(self.at * w, h))
        else:
            cut = QRectF(0, self.at * h, w, h - self.at * h)
            line = (QPointF(0, self.at * h), QPointF(w, self.at * h))
        if self.image is not None:
            ratio = self.image.devicePixelRatio() or 1
            source = QRectF(cut.x() * ratio, cut.y() * ratio,
                            cut.width() * ratio, cut.height() * ratio)
            painter.drawImage(cut, self.image, source)
        painter.setPen(QPen(QColor(255, 255, 255), 4))
        painter.drawLine(*line)
        painter.setPen(QPen(QColor(0, 121, 193), 2))
        painter.drawLine(*line)


class SwipeTool(QgsMapTool):
    """Drag across the map to move the swipe line; Shift+drag for a
    horizontal line. Another tool (or Esc) ends the swipe."""

    def __init__(self, canvas, layer):
        super().__init__(canvas)
        self.layer = layer
        self.item = SwipeItem(canvas)
        self.job = None
        self.setCursor(Qt.CursorShape.SplitHCursor)
        canvas.mapCanvasRefreshed.connect(self.render)
        self.render()

    def render(self):
        canvas = self.canvas()
        if self.layer is None or sip.isdeleted(self.layer):
            return
        settings = canvas.mapSettings()
        settings.setLayers([lyr for lyr in settings.layers()
                            if lyr.id() != self.layer.id()])
        if self.job is not None:
            self.job.cancelWithoutBlocking()
        self.job = QgsMapRendererParallelJob(settings)
        self.job.finished.connect(lambda job=self.job: self._done(job))
        self.job.start()

    def _done(self, job):
        if job is self.job and not sip.isdeleted(self.item):
            self.item.set_image(job.renderedImage())
            self.job = None

    def _move(self, event):
        canvas = self.canvas()
        self.item.vertical = not (event.modifiers()
                                  & Qt.KeyboardModifier.ShiftModifier)
        pos = event.pos()
        self.item.at = (pos.x() / max(canvas.width(), 1) if self.item.vertical
                        else pos.y() / max(canvas.height(), 1))
        self.setCursor(Qt.CursorShape.SplitHCursor if self.item.vertical
                       else Qt.CursorShape.SplitVCursor)
        self.item.update()

    def canvasPressEvent(self, event):  # noqa: N802 (QGIS API name)
        self._move(event)

    def canvasMoveEvent(self, event):  # noqa: N802 (QGIS API name)
        if event.buttons() & Qt.MouseButton.LeftButton:
            self._move(event)

    def keyPressEvent(self, event):  # noqa: N802 (QGIS API name)
        if event.key() == Qt.Key.Key_Escape:
            self.canvas().unsetMapTool(self)

    def deactivate(self):
        canvas = self.canvas()
        try:
            canvas.mapCanvasRefreshed.disconnect(self.render)
        except (TypeError, RuntimeError):
            pass
        if self.job is not None:
            self.job.cancelWithoutBlocking()
            self.job = None
        if not sip.isdeleted(self.item):
            canvas.scene().removeItem(self.item)
        super().deactivate()


# --------------------------------------------------------------- controls
class LayerControls:
    """Builds the ribbon controls and keeps them on the active layer."""

    def __init__(self, iface, changes):
        self.iface = iface
        self.canvas = iface.mapCanvas()
        self.layer = None
        self.updating = False
        self.opacity, self.scales, self.labels = [], [], []
        self.swipe_buttons = []
        self.swipe = None
        iface.currentLayerChanged.connect(self.set_layer)
        self.canvas.mapToolSet.connect(self._tool_set)
        changes.add(self.stop)

    def stop(self):
        for signal, slot in ((self.iface.currentLayerChanged, self.set_layer),
                             (self.canvas.mapToolSet, self._tool_set)):
            try:
                signal.disconnect(slot)
            except (TypeError, RuntimeError):
                pass
        if self.swipe is not None and self.canvas.mapTool() is self.swipe:
            self.canvas.unsetMapTool(self.swipe)
        self.swipe = None
        self._watch(None)

    # ------------------------------------------------------------ widgets
    def widget(self, key):
        make = {"transparency": self._transparency,
                "visibility_range": self._visibility_range,
                "swipe": self._swipe_button,
                "label_text": self._label_text,
                "label_symbol": self._label_symbol,
                "label_placement": self._label_placement}.get(key)
        if make is None:
            return None
        widget = make()
        widget.setObjectName("WorkspaceRibbonControl")
        QTimer.singleShot(0, lambda: self.set_layer(
            self.iface.activeLayer()))
        return widget

    def _transparency(self):
        box, grid = _row()
        grid.addWidget(QLabel(tr("Transparency:")), 0, 0)
        slider = QSlider(Qt.Orientation.Horizontal, minimum=0, maximum=100)
        slider.setFixedWidth(110)
        slider.setToolTip(tr("Transparency of the layer (0% = opaque)"))
        value = QLabel("0%")
        value.setMinimumWidth(32)
        grid.addWidget(slider, 1, 0)
        grid.addWidget(value, 1, 1)
        grid.setRowStretch(2, 1)

        def changed(v):
            value.setText(f"{v}%")
            if not self.updating and self.layer is not None:
                self.layer.setOpacity(1 - v / 100)
                self.layer.triggerRepaint()
                self.layer.emitStyleChanged()
        slider.valueChanged.connect(changed)
        self.opacity.append((slider, value))
        return box

    def _visibility_range(self):
        box, grid = _row()
        out_beyond, in_beyond = QgsScaleWidget(), QgsScaleWidget()
        for row, (text, widget, tip) in enumerate((
                (tr("Out Beyond:"), out_beyond,
                 tr("Hide the layer when zoomed out beyond this scale")),
                (tr("In Beyond:"), in_beyond,
                 tr("Hide the layer when zoomed in beyond this scale")))):
            grid.addWidget(QLabel(text), row, 0)
            widget.setMapCanvas(self.canvas)
            widget.setShowCurrentScaleButton(True)
            widget.setAllowNull(True)
            widget.setFixedWidth(150)
            widget.setToolTip(tip)
            grid.addWidget(widget, row, 1)
        clear = QToolButton(autoRaise=True, text=tr("Clear Limits"))
        clear.setIcon(pro_icon("clear_selection"))
        clear.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        grid.addWidget(clear, 2, 1)

        def apply():
            if self.updating or self.layer is None:
                return
            low = 0 if out_beyond.isNull() else out_beyond.scale()
            high = 0 if in_beyond.isNull() else in_beyond.scale()
            self.layer.setMinimumScale(low)
            self.layer.setMaximumScale(high)
            self.layer.setScaleBasedVisibility(bool(low or high))
            self.layer.triggerRepaint()
            self.iface.layerTreeView().refreshLayerSymbology(self.layer.id())

        def clear_limits():
            self.updating = True
            out_beyond.setNull()
            in_beyond.setNull()
            self.updating = False
            apply()
        out_beyond.scaleChanged.connect(lambda _s: apply())
        in_beyond.scaleChanged.connect(lambda _s: apply())
        clear.clicked.connect(clear_limits)
        self.scales.append((out_beyond, in_beyond))
        return box

    def _swipe_button(self):
        button = QToolButton(autoRaise=True, text=tr("Swipe"),
                             checkable=True)
        button.setIcon(pro_icon("swipe"))
        button.setToolTip(tr("Swipe: drag across the map to peel the "
                             "selected layer back and see what is under "
                             "it. Shift+drag swipes up and down."))
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
        button.toggled.connect(self._swipe_toggled)
        self.swipe_buttons.append(button)
        return button

    def _swipe_toggled(self, on):
        if self.updating:
            return
        if on and self.layer is not None:
            self.swipe = SwipeTool(self.canvas, self.layer)
            self.canvas.setMapTool(self.swipe)
        elif not on and self.swipe is not None \
                and self.canvas.mapTool() is self.swipe:
            self.canvas.unsetMapTool(self.swipe)

    def _tool_set(self, new, _old=None):
        if self.swipe is not None and new is not self.swipe:
            self.swipe = None
        self._sync_swipe()

    def _sync_swipe(self):
        self.updating = True
        for button in self.swipe_buttons:
            if not sip.isdeleted(button):
                button.setChecked(self.swipe is not None)
        self.updating = False

    def _label_text(self):
        box, grid = _row()
        grid.addWidget(QLabel(tr("Field:")), 0, 0)
        field = QgsFieldExpressionWidget()
        field.setFixedWidth(170)
        field.setToolTip(tr("Field or expression to label with"))
        grid.addWidget(field, 0, 1)
        grid.setRowStretch(1, 1)
        field.fieldChanged[str].connect(lambda _t: self._apply_labels())
        self.labels.append(("field", field))
        return box

    def _label_symbol(self):
        box, grid = _row()
        font = QFontComboBox()
        font.setFixedWidth(150)
        size = QSpinBox(minimum=4, maximum=96, suffix=" pt")
        color = QgsColorButton()
        color.setFixedWidth(48)
        color.setToolTip(tr("Text color"))
        bold = QToolButton(autoRaise=True, text="B", checkable=True)
        bold.setStyleSheet("font-weight: bold")
        bold.setToolTip(tr("Bold"))
        grid.addWidget(font, 0, 0, 1, 3)
        grid.addWidget(size, 1, 0)
        grid.addWidget(bold, 1, 1)
        grid.addWidget(color, 1, 2)
        grid.setRowStretch(2, 1)
        font.currentFontChanged.connect(lambda _f: self._apply_labels())
        size.valueChanged.connect(lambda _v: self._apply_labels())
        color.colorChanged.connect(lambda _c: self._apply_labels())
        bold.toggled.connect(lambda _b: self._apply_labels())
        self.labels += [("font", font), ("size", size), ("color", color),
                        ("bold", bold)]
        return box

    def _label_placement(self):
        box, grid = _row()
        grid.addWidget(QLabel(tr("Placement:")), 0, 0)
        combo = QComboBox()
        combo.setFixedWidth(160)
        grid.addWidget(combo, 1, 0)
        grid.setRowStretch(2, 1)
        combo.currentIndexChanged.connect(lambda _i: self._apply_labels())
        self.labels.append(("placement", combo))
        return box

    # -------------------------------------------------------------- state
    def _watch(self, layer):
        old = self.layer
        if old is not None and not sip.isdeleted(old):
            try:
                old.styleChanged.disconnect(self._refresh)
            except (TypeError, RuntimeError):
                pass
        self.layer = layer
        if layer is not None:
            layer.styleChanged.connect(self._refresh)

    def set_layer(self, layer):
        if layer is not None and (sip.isdeleted(layer) or not isinstance(
                layer, (QgsVectorLayer, QgsRasterLayer))):
            layer = None
        if layer is not self.layer:
            self._watch(layer)
            if self.swipe is not None and \
                    self.canvas.mapTool() is self.swipe:
                self.canvas.unsetMapTool(self.swipe)
        self._refresh()

    def _alive(self, widget):
        return widget is not None and not sip.isdeleted(widget)

    def _refresh(self):
        layer = self.layer
        self.updating = True
        try:
            for slider, value in self.opacity:
                if self._alive(slider) and layer is not None:
                    slider.setValue(round((1 - layer.opacity()) * 100))
                    value.setText(f"{slider.value()}%")
            for out_beyond, in_beyond in self.scales:
                if not self._alive(out_beyond) or layer is None:
                    continue
                on = layer.hasScaleBasedVisibility()
                for widget, scale in ((out_beyond, layer.minimumScale()),
                                      (in_beyond, layer.maximumScale())):
                    if on and scale:
                        widget.setScale(scale)
                    else:
                        widget.setNull()
            if isinstance(layer, QgsVectorLayer) and layer.isSpatial():
                self._refresh_labels(layer)
        finally:
            self.updating = False

    def _label_settings(self, layer):
        labeling = layer.labeling()
        if isinstance(labeling, QgsVectorLayerSimpleLabeling):
            return QgsPalLayerSettings(labeling.settings())
        return None

    def _refresh_labels(self, layer):
        settings = self._label_settings(layer)
        fmt = settings.format() if settings else QgsTextFormat()
        for kind, widget in self.labels:
            if not self._alive(widget):
                continue
            widget.setEnabled(not (layer.labeling() is not None
                                   and settings is None))
            if kind == "field":
                widget.setLayer(layer)
                widget.setField(settings.fieldName if settings else "")
            elif kind == "font":
                widget.setCurrentFont(fmt.font())
            elif kind == "size":
                widget.setValue(round(fmt.size()) if settings else 10)
            elif kind == "color":
                widget.setColor(fmt.color())
            elif kind == "bold":
                widget.setChecked(fmt.font().bold())
            elif kind == "placement":
                widget.clear()
                options = PLACEMENTS.get(layer.geometryType(), [])
                for text, value in options:
                    widget.addItem(text, value)
                if settings:
                    index = widget.findData(settings.placement)
                    widget.setCurrentIndex(max(index, 0))

    def _apply_labels(self):
        layer = self.layer
        if self.updating or not isinstance(layer, QgsVectorLayer):
            return
        if layer.labeling() is not None and \
                not isinstance(layer.labeling(), QgsVectorLayerSimpleLabeling):
            return   # rule-based labels: edit them in Label Settings
        settings = self._label_settings(layer) or QgsPalLayerSettings()
        fmt = settings.format()
        values = {kind: w for kind, w in self.labels if self._alive(w)}
        if "field" in values:
            text, is_expression, valid = values["field"].currentField()
            if not text:
                return
            settings.fieldName = text
            settings.isExpression = is_expression
        if "font" in values:
            font = QFont(values["font"].currentFont())
            if "bold" in values:
                font.setBold(values["bold"].isChecked())
            fmt.setFont(font)
            if "bold" in values:
                fmt.setNamedStyle("Bold" if font.bold() else "Regular")
        if "size" in values:
            fmt.setSize(values["size"].value())
            fmt.setSizeUnit(Qgis.RenderUnit.Points)
        if "color" in values:
            fmt.setColor(values["color"].color())
        settings.setFormat(fmt)
        combo = values.get("placement")
        if combo is not None and combo.currentData() is not None:
            settings.placement = combo.currentData()
        layer.setLabeling(QgsVectorLayerSimpleLabeling(settings))
        layer.setLabelsEnabled(True)
        layer.triggerRepaint()
        self.iface.layerTreeView().refreshLayerSymbology(layer.id())

