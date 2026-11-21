"""Attributes pane, like ArcGIS Pro's: the selected features of every map
layer in a list, and the fields of the one picked in that list stacked
vertically for quick editing.

* The list groups the selected features by layer ("Parcels (3)"), each
  named by the layer's display expression. Click a feature to edit it;
  click a layer to change a field on all its selected features at once
  (QGIS's multi-edit form).
* The fields use the layer's own attribute form (value maps, lookup
  drop-downs, date pickers, constraints), so the pane edits exactly as the
  layer's form does.
* Auto Apply (on by default): a changed value is written to the layer as
  soon as you stop typing; a click in the fields of a layer that is not in
  edit mode starts editing on it. Save Edits still commits to the data
  source, as in Pro. With Auto Apply off, Apply writes the changes.
* The pane opens by itself when features are selected with a Select tool
  or Select By Attributes / Form; while open it follows every selection.
"""

from qgis.core import (
    Qgis, QgsExpression, QgsExpressionContext, QgsExpressionContextUtils,
    QgsFeatureRequest, QgsMessageLog, QgsProject, QgsSettings, QgsVectorLayer,
)
from qgis.gui import QgsAttributeEditorContext, QgsAttributeForm, QgsEditorWidgetWrapper
from qgis.PyQt import sip
from qgis.PyQt.QtCore import QEvent, QObject, Qt, QTimer
from qgis.PyQt.QtGui import QAction
from qgis.PyQt.QtWidgets import (
    QApplication, QCheckBox, QDockWidget, QLabel, QScrollArea, QSplitter,
    QToolButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QHBoxLayout,
    QWidget,
)

from .util import pro_icon, tr

OBJECT_NAME = "WorkspaceAttributes"
AUTO_KEY = "keystone/attributes_auto_apply"
MAX_LISTED = 1000          # features listed per layer
SAVE_DELAY = 700           # ms after the last keystroke before applying
SELECT_TOOLS = {
    "mActionSelectFeatures", "mActionSelectPolygon", "mActionSelectFreehand",
    "mActionSelectRadius", "mActionSelectByExpression",
    "mActionSelectByForm",
}
SELECT_DIALOGS = {"QgsExpressionSelectionDialog", "QgsSelectByFormDialog"}
LAYER_ROLE = Qt.ItemDataRole.UserRole
FID_ROLE = Qt.ItemDataRole.UserRole + 1


def _layer_editable(layer):
    return layer.supportsEditing() and not layer.readOnly()


class _EditOnClick(QObject):
    """Application event filter: a press inside the pane's fields while
    the layer is not editable starts editing (QGIS disables the widgets of
    a read-only layer, so they would never see the click themselves)."""

    def __init__(self, pane):
        super().__init__(pane)
        self.pane = pane
        QApplication.instance().installEventFilter(self)

    def eventFilter(self, watched, event):  # noqa: N802 (Qt API name)
        if (event.type() == QEvent.Type.MouseButtonPress
                and isinstance(watched, QWidget)):
            self.pane.pressed_in(watched)
        return False

    def stop(self):
        QApplication.instance().removeEventFilter(self)


class AttributesPane(QDockWidget):
    """The Attributes dock widget."""

    def __init__(self, iface):
        super().__init__(tr("Attributes"), iface.mainWindow())
        self.setObjectName(OBJECT_NAME)
        self.iface = iface
        self.canvas = iface.mapCanvas()
        self.layers = []       # layers whose selectionChanged is connected
        self.form = None
        self.form_layer = None
        self.form_fid = None
        self._form_pending = False
        self._opening = False  # a select tool / dialog made the selection
        self._dirty = False    # selection changed while the pane was hidden

        body = QWidget()
        box = QVBoxLayout(body)
        box.setContentsMargins(4, 4, 4, 4)
        box.setSpacing(4)

        bar = QHBoxLayout()
        bar.setSpacing(2)
        self.zoom_button = self._tool("zoom_selected", tr("Zoom to"),
                                      self.zoom_to)
        self.flash_button = self._tool("identify", tr("Flash"), self.flash)
        self.unselect_button = self._tool(
            "clear_selection", tr("Remove from selection"), self.unselect)
        self.table_button = self._tool(
            "attribute_table", tr("Open the attribute table (selected)"),
            self.open_table)
        for button in (self.zoom_button, self.flash_button,
                       self.unselect_button, self.table_button):
            bar.addWidget(button)
        bar.addStretch(1)
        self.auto = QCheckBox(tr("Auto Apply"))
        self.auto.setToolTip(tr("Write each change to the layer right away; "
                                "a click in the fields starts editing"))
        self.auto.setChecked(QgsSettings().value(AUTO_KEY, True, type=bool))
        self.auto.toggled.connect(self._auto_toggled)
        bar.addWidget(self.auto)
        self.apply_button = QToolButton(text=tr("Apply"))
        self.apply_button.clicked.connect(self.apply)
        bar.addWidget(self.apply_button)
        self.discard_button = QToolButton(text=tr("Discard pending values"))
        self.discard_button.setToolTip(tr("Reload from the layer. Previously applied layer edits are kept."))
        self.discard_button.clicked.connect(self._discard_pending)
        self.discard_button.hide()
        bar.addWidget(self.discard_button)
        box.addLayout(bar)

        self.splitter = QSplitter(Qt.Orientation.Vertical)
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setRootIsDecorated(True)
        self.tree.currentItemChanged.connect(self._item_changed)
        self.splitter.addWidget(self.tree)
        self.scroll = QScrollArea(widgetResizable=True)
        self.scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.splitter.addWidget(self.scroll)
        self.splitter.setStretchFactor(0, 1)
        self.splitter.setStretchFactor(1, 3)
        box.addWidget(self.splitter, 1)

        self.hint = QLabel(objectName="WorkspaceAttributesHint")
        self.hint.setWordWrap(True)
        box.addWidget(self.hint)
        self.setWidget(body)
        self.visibilityChanged.connect(
            lambda shown: shown and self._dirty and self.refresh())

        self.save_timer = QTimer(self, singleShot=True, interval=SAVE_DELAY)
        self.save_timer.timeout.connect(self.apply)
        self.refresh_timer = QTimer(self, singleShot=True, interval=80)
        self.refresh_timer.timeout.connect(self.refresh)
        self.click_filter = _EditOnClick(self)

        project = QgsProject.instance()
        project.layersAdded.connect(self._watch)
        project.layersWillBeRemoved.connect(self._unwatch)
        self._watch(project.mapLayers().values())
        self._auto_toggled(self.auto.isChecked())
        self.refresh()

    # ------------------------------------------------------------ helpers
    def _tool(self, icon, tip, slot):
        button = QToolButton(autoRaise=True, toolTip=tip)
        button.setIcon(pro_icon(icon))
        button.clicked.connect(slot)
        return button

    def _watch(self, layers):
        for layer in layers:
            if isinstance(layer, QgsVectorLayer) and layer not in self.layers:
                layer.selectionChanged.connect(self._selection_changed)
                self.layers.append(layer)

    def _unwatch(self, layer_ids):
        for layer in list(self.layers):
            if sip.isdeleted(layer) or layer.id() in layer_ids:
                self._disconnect(layer)
                self.layers.remove(layer)
        if self.form_layer is not None and (
                sip.isdeleted(self.form_layer)
                or self.form_layer.id() in layer_ids):
            self._clear_form()
        self.refresh_timer.start()

    def _disconnect(self, layer):
        if sip.isdeleted(layer):
            return
        try:
            layer.selectionChanged.disconnect(self._selection_changed)
        except (TypeError, RuntimeError) as err:
            QgsMessageLog.logMessage(
                f"attributes_pane._disconnect: selectionChanged was already disconnected: {err!r}",
                "Keystone", Qgis.MessageLevel.Info)

    def stop(self):
        """Disconnect everything (the pane is being removed)."""
        self.save_timer.stop()
        self.apply()
        self.click_filter.stop()
        project = QgsProject.instance()
        for signal, slot in ((project.layersAdded, self._watch),
                             (project.layersWillBeRemoved, self._unwatch)):
            try:
                signal.disconnect(slot)
            except (TypeError, RuntimeError) as err:
                QgsMessageLog.logMessage(
                    f"attributes_pane.stop: a project signal was already disconnected: {err!r}",
                    "Keystone", Qgis.MessageLevel.Info)
        for layer in self.layers:
            self._disconnect(layer)
        self.layers = []
        self._clear_form()

    # ---------------------------------------------------------- selection
    def _selection_changed(self, *_):
        if not self.isVisible() and self._made_by_select_tool():
            self._opening = True
        self.refresh_timer.start()

    def _made_by_select_tool(self):
        tool = self.canvas.mapTool()
        action = tool.action() if tool is not None else None
        if action is not None and action.objectName() in SELECT_TOOLS:
            return True
        window = QApplication.activeWindow()
        return (window is not None
                and window.metaObject().className() in SELECT_DIALOGS)

    def refresh(self):
        """Rebuild the list from the current selections."""
        if sip.isdeleted(self):
            return
        if not self.isVisible() and not self._opening:
            self._dirty = True   # rebuilt when the pane is shown
            return
        if not self.apply():
            return
        self._dirty = False
        keep = (self.form_layer, self.form_fid)
        self.tree.blockSignals(True)
        self.tree.clear()
        first = None
        target = None
        total = 0
        for layer in self.layers:
            if sip.isdeleted(layer) or layer.selectedFeatureCount() == 0:
                continue
            count = layer.selectedFeatureCount()
            total += count
            node = QTreeWidgetItem([f"{layer.name()} ({count})"])
            node.setData(0, LAYER_ROLE, layer.id())
            node.setToolTip(0, tr("Click to edit all {} selected features "
                                  "of {} at once").format(count, layer.name()))
            self.tree.addTopLevelItem(node)
            expression = QgsExpression(layer.displayExpression())
            context = QgsExpressionContext(
                QgsExpressionContextUtils.globalProjectLayerScopes(layer))
            request = QgsFeatureRequest().setFilterFids(
                layer.selectedFeatureIds()[:MAX_LISTED])
            for feature in layer.getFeatures(request):
                context.setFeature(feature)
                label = expression.evaluate(context)
                label = str(label) if label not in (None, "") else str(
                    feature.id())
                item = QTreeWidgetItem(node, [label])
                item.setData(0, LAYER_ROLE, layer.id())
                item.setData(0, FID_ROLE, feature.id())
                if first is None:
                    first = item
                if keep == (layer, feature.id()):
                    target = item
            if count > MAX_LISTED:
                more = QTreeWidgetItem(node, [tr("… {} more").format(
                    count - MAX_LISTED)])
                more.setFlags(Qt.ItemFlag.NoItemFlags)
            node.setExpanded(True)
        self.tree.blockSignals(False)
        if total == 0:
            self._clear_form()
            self.hint.setText(tr("Select features to see their attributes."))
        else:
            self.tree.setCurrentItem(target or first)
        for button in (self.zoom_button, self.flash_button,
                       self.unselect_button, self.table_button):
            button.setEnabled(total > 0)
        if self._opening and total:
            self._opening = False
            self.show()
            self.raise_()
        self._opening = False

    def _item_changed(self, current, previous):
        self.save_timer.stop()
        if not self.apply():
            self.tree.blockSignals(True)
            self.tree.setCurrentItem(previous)
            self.tree.blockSignals(False)
            return
        if current is None:
            self._clear_form()
            return
        layer = QgsProject.instance().mapLayer(current.data(0, LAYER_ROLE))
        if layer is None:
            self._clear_form()
            return
        fid = current.data(0, FID_ROLE)
        if fid is None:
            self._show_multi(layer)
        else:
            self._show_feature(layer, fid)

    # --------------------------------------------------------------- forms
    def _clear_form(self):
        self.discard_button.hide()
        self._form_pending = False
        if self.form is not None and not sip.isdeleted(self.form):
            self.form.deleteLater()
        self.form = None
        self.form_layer = None
        self.form_fid = None
        self.scroll.takeWidget()

    def _context(self):
        context = QgsAttributeEditorContext()
        context.setVectorLayerTools(self.iface.vectorLayerTools())
        context.setMapCanvas(self.canvas)
        context.setMainMessageBar(self.iface.messageBar())
        return context

    def _set_form(self, form, layer, fid):
        self._clear_form()
        form.hideButtonBox()
        self.form, self.form_layer, self.form_fid = form, layer, fid
        self.scroll.setWidget(form)
        self._update_hint()

    def _show_feature(self, layer, fid):
        feature = layer.getFeature(fid)
        form = QgsAttributeForm(layer, feature, self._context())
        form.widgetValueChanged.connect(self._value_changed)
        self._set_form(form, layer, fid)

    def _show_multi(self, layer):
        form = QgsAttributeForm(layer, layer.getFeature(
            layer.selectedFeatureIds()[0]), self._context())
        form.setMode(QgsAttributeEditorContext.Mode.MultiEditMode)
        form.setMultiEditFeatureIds(layer.selectedFeatureIds())
        self._set_form(form, layer, None)

    def _update_hint(self):
        layer = self.form_layer
        if layer is None:
            return
        if self.form_fid is None:
            text = tr("Editing all selected features of {}: change a field, "
                      "then Apply changes.").format(layer.name())
        elif layer.isEditable():
            text = tr("Changes are applied as you go; Save Edits writes "
                      "them to the data.") if self.auto.isChecked() else tr(
                "Press Apply to write your changes to the layer.")
        elif _layer_editable(layer):
            text = tr("Click a field to start editing {}.").format(
                layer.name())
        else:
            text = tr("{} cannot be edited.").format(layer.name())
        self.hint.setText(text)

    def pressed_in(self, widget):
        """Start editing when the fields of a read-only layer are clicked."""
        form, layer = self.form, self.form_layer
        if (form is None or layer is None or sip.isdeleted(form)
                or sip.isdeleted(layer) or layer.isEditable()
                or not self.auto.isChecked() or not _layer_editable(layer)):
            return
        if widget is form or form.isAncestorOf(widget):
            layer.startEditing()   # the form enables its widgets itself
            self.iface.setActiveLayer(layer)
            self._update_hint()

    def _value_changed(self, _attribute, _value, changed):
        if changed:
            self._form_pending = True
            if self.auto.isChecked() and self.form_fid is not None:
                self.save_timer.start()

    def apply(self):
        """Write the form's changes to the layer (edit buffer)."""
        form, layer = self.form, self.form_layer
        if (form is None or layer is None or sip.isdeleted(form)
                or sip.isdeleted(layer) or self.form_fid is None
                or not layer.isEditable()):
            return True
        # Embedded QGIS forms can return True without saving when a hard constraint fails.
        blocked = self._form_pending and any(
            editor.isBlockingCommit() for editor in form.findChildren(QgsEditorWidgetWrapper))
        if blocked or not form.save():
            self.hint.setText(tr("Changes were not applied. Correct the highlighted values, "
                                 "then Apply, or discard the pending values before switching features."))
            self.discard_button.show()
            return False
        self.discard_button.hide()
        self._form_pending = False
        self._update_hint()
        layer.triggerRepaint()
        return True

    def _discard_pending(self):
        self.save_timer.stop()
        if self.form is not None and not sip.isdeleted(self.form):
            self.form.resetValues()
        self._clear_form()
        self.refresh()

    def _auto_toggled(self, on):
        QgsSettings().setValue(AUTO_KEY, on)
        self.apply_button.setVisible(not on)
        self._update_hint()

    # ------------------------------------------------------------- toolbar
    def _current(self):
        item = self.tree.currentItem()
        if item is None:
            return None, []
        layer = QgsProject.instance().mapLayer(item.data(0, LAYER_ROLE))
        if layer is None:
            return None, []
        fid = item.data(0, FID_ROLE)
        return layer, ([fid] if fid is not None
                       else layer.selectedFeatureIds())

    def zoom_to(self):
        layer, fids = self._current()
        if layer is not None and fids:
            self.canvas.zoomToFeatureIds(layer, fids)

    def flash(self):
        layer, fids = self._current()
        if layer is not None and fids:
            self.canvas.flashFeatureIds(layer, fids)

    def unselect(self):
        layer, fids = self._current()
        if layer is not None and fids:
            layer.deselect(fids)

    def open_table(self):
        layer, _ = self._current()
        if layer is None:
            return
        self.iface.setActiveLayer(layer)
        action = self.iface.mainWindow().findChild(
            QAction, "mActionOpenTableSelected")
        if action is not None:
            action.trigger()
        else:
            self.iface.showAttributeTable(layer)


def attributes_pane(iface, changes):
    """Create the Attributes pane, docked right and tabbed with the
    Catalog like Pro's; removed when the workspace is switched off."""
    win = iface.mainWindow()
    pane = AttributesPane(iface)
    win.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, pane)
    catalog = win.findChild(QDockWidget, "Browser")
    if catalog is not None and not catalog.isFloating():
        win.tabifyDockWidget(catalog, pane)
    pane.hide()

    def remove():
        pane.stop()
        win.removeDockWidget(pane)
        pane.deleteLater()

    changes.add(remove)
    return pane
