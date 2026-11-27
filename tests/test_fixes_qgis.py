"""Run with QGIS Python: unittest discover -s workspace_ribbon/tests -v."""
from pathlib import Path
import importlib.util
import shutil
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4

plugin_root = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('keystone_under_test', plugin_root / '__init__.py',
                                            submodule_search_locations=[str(plugin_root)])
package = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = package
spec.loader.exec_module(package)
from qgis.core import QgsApplication, QgsCoordinateReferenceSystem, QgsFeature, QgsFieldConstraints, QgsGeometry, QgsPointXY, QgsPrintLayout
from qgis.core import QgsProject, QgsRectangle, QgsVectorLayer
from qgis.gui import QgsGui, QgsMapCanvas, QgsMessageBar
from qgis.PyQt.QtGui import QAction
from qgis.PyQt.QtWidgets import (
    QLineEdit, QMainWindow, QMenu, QToolBar, QWidget,
)
from qgis.PyQt.QtTest import QTest
from keystone_under_test import attributes_pane as A, basemaps as B, coords, layouts as L, locate, modify_features, ribbon
from keystone_under_test.util import Changes


class Form(QWidget):
    def __init__(self, saved):
        super().__init__()
        self.save = Mock(return_value=saved)
        self.hideButtonBox = Mock()
        self.resetValues = Mock()


class KeystoneFixesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QgsApplication.instance() or QgsApplication([], False)
        cls.app.initQgis()
        registry = QgsGui.editorWidgetRegistry()
        if not registry.factories():
            registry.initEditors()

    def setUp(self):
        self.project = QgsProject.instance()
        self.project.clear()
        self.win = QMainWindow()
        self.canvas = QgsMapCanvas(self.win)
        self.iface = Mock()
        self.iface.mainWindow.return_value = self.win
        self.iface.mapCanvas.return_value = self.canvas
        self.iface.messageBar.return_value = QgsMessageBar(self.win)
        self.iface.vectorLayerTools.return_value = None
        self.pane = None
        self.tmp = Path(__file__).resolve().parents[2] / '_scratch' / ('keystone-test-' + uuid4().hex)
        self.tmp.mkdir(parents=True)

    def tearDown(self):
        if self.pane is not None:
            self.pane._clear_form()
            self.pane.stop()
        self.project.clear()
        self.win.deleteLater()
        self.app.processEvents()
        self.assertEqual(self.tmp.parent, Path(__file__).resolve().parents[2] / '_scratch')
        self.assertTrue(self.tmp.name.startswith('keystone-test-'))
        shutil.rmtree(self.tmp)

    def layer(self, name='Features', field='name'):
        layer = QgsVectorLayer('Point?crs=EPSG:3857&field=%s:string' % field, name, 'memory')
        for i in range(2):
            feature = QgsFeature(layer.fields())
            feature[field] = 'before %d' % i
            feature.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(i, i)))
            layer.dataProvider().addFeatures([feature])
        self.project.addMapLayer(layer)
        return layer

    def attributes(self, saved=False):
        layer = self.layer()
        layer.startEditing()
        layer.selectAll()
        settings = Mock()
        settings.value.return_value = True
        with patch.object(A, 'QgsSettings', return_value=settings):
            self.pane = A.AttributesPane(self.iface)
        self.win.show()
        self.pane.show()
        self.pane.refresh()
        node = self.pane.tree.topLevelItem(0)
        self.pane.tree.setCurrentItem(node.child(0))
        form = Form(saved)
        self.pane._set_form(form, layer, node.child(0).data(0, A.FID_ROLE))
        return layer, form, node

    def test_failed_save_keeps_form_and_restores_tree_selection(self):
        layer, form, node = self.attributes()
        original = node.child(0)
        self.pane.tree.setCurrentItem(node.child(1))
        self.assertIs(self.pane.form, form)
        self.assertIs(self.pane.tree.currentItem(), original)
        self.assertIn('not applied', self.pane.hint.text())
        self.assertFalse(self.pane.discard_button.isHidden())
        self.assertFalse(layer.isModified())

    def test_failed_save_also_blocks_selection_list_refresh(self):
        _, form, node = self.attributes()
        self.pane.refresh()
        self.assertIs(self.pane.form, form)
        self.assertIs(self.pane.tree.topLevelItem(0), node)

    def test_correcting_failed_save_allows_switching_features(self):
        _, form, node = self.attributes()
        self.assertFalse(self.pane.apply())
        form.save.return_value = True
        self.pane.tree.setCurrentItem(node.child(1))
        self.assertIsNot(self.pane.form, form)
        self.assertEqual(self.pane.form_fid, node.child(1).data(0, A.FID_ROLE))
        self.assertTrue(self.pane.discard_button.isHidden())

    def test_discard_reloads_values_without_discarding_layer_edits(self):
        layer, form, _ = self.attributes()
        other = next(fid for fid in layer.selectedFeatureIds() if fid != self.pane.form_fid)
        layer.changeAttributeValue(other, 0, 'already applied')
        self.assertFalse(self.pane.apply())
        self.pane.discard_button.click()
        self.assertIsNot(self.pane.form, form)
        self.assertEqual(layer.getFeature(other)['name'], 'already applied')
        self.assertTrue(layer.isModified())

    def test_real_qgis_hard_constraint_failure_is_retained(self):
        layer, _, node = self.attributes(True)
        layer.setConstraintExpression(0, "\"name\" <> 'invalid'", 'Use a different name')
        layer.setFieldConstraint(0, QgsFieldConstraints.ConstraintExpression, QgsFieldConstraints.ConstraintStrengthHard)
        fid = node.child(0).data(0, A.FID_ROLE)
        self.pane._show_feature(layer, fid)
        native_form = self.pane.form
        editor = native_form.findChild(QLineEdit, 'name')
        editor.selectAll()
        QTest.keyClicks(editor, 'invalid')
        self.assertFalse(self.pane.apply())
        self.assertIs(self.pane.form, native_form)
        self.assertIsNotNone(layer.getFeature(fid)['name'])
        editor.selectAll()
        QTest.keyClicks(editor, 'valid')
        self.assertTrue(self.pane.apply())
        self.assertEqual(layer.getFeature(fid)['name'], 'valid')

    def test_existing_invalid_value_does_not_trap_a_user_who_has_not_edited_it(self):
        layer, _, node = self.attributes(True)
        fid = node.child(0).data(0, A.FID_ROLE)
        layer.changeAttributeValue(fid, 0, 'invalid')
        layer.setConstraintExpression(0, "\"name\" <> 'invalid'", 'Use a different name')
        layer.setFieldConstraint(0, QgsFieldConstraints.ConstraintExpression, QgsFieldConstraints.ConstraintStrengthHard)
        self.pane._show_feature(layer, fid)
        self.assertTrue(self.pane.apply())
        self.pane.tree.setCurrentItem(node.child(1))
        self.assertEqual(self.pane.form_fid, node.child(1).data(0, A.FID_ROLE))

    def test_failed_basemap_replacement_keeps_previous_layer(self):
        old = self.layer('Old basemap')
        old.setCustomProperty(B.MARKER, True)
        old_id = old.id()
        with patch.object(B, 'QgsRasterLayer', return_value=SimpleNamespace(isValid=lambda: False)):
            B.set_basemap(self.iface, 'Unavailable', 'invalid', '')
        self.assertIs(self.project.mapLayer(old_id), old)

    def test_valid_basemap_replacement_removes_only_previous_basemaps(self):
        old = self.layer('Old basemap')
        old.setCustomProperty(B.OLD_MARKER, True)
        old_id = old.id()
        data = self.layer('Keep this data')
        replacement = QgsVectorLayer('Point?crs=EPSG:3857', 'New basemap', 'memory')
        with patch.object(B, 'QgsRasterLayer', return_value=replacement):
            B.set_basemap(self.iface, 'New basemap', 'synthetic', 'Test attribution')
        self.assertIsNone(self.project.mapLayer(old_id))
        self.assertIs(self.project.mapLayer(data.id()), data)
        self.assertIs(self.project.mapLayer(replacement.id()), replacement)
        self.assertEqual(replacement.metadata().rights(), ['Test attribution'])

    def test_explicit_remove_basemap_still_works(self):
        old = self.layer('Old basemap')
        old.setCustomProperty(B.MARKER, True)
        old_id = old.id()
        B.set_basemap(self.iface, 'None', None, '')
        self.assertIsNone(self.project.mapLayer(old_id))

    def test_basemap_registration_failure_also_keeps_previous_layer(self):
        old = self.layer('Old basemap')
        old.setCustomProperty(B.MARKER, True)
        old_id = old.id()
        replacement = QgsVectorLayer('Point?crs=EPSG:3857', 'New basemap', 'memory')
        with patch.object(B, 'QgsRasterLayer', return_value=replacement), \
                patch.object(self.project, 'addMapLayer', return_value=None):
            B.set_basemap(self.iface, 'New basemap', 'synthetic', '')
        self.assertIs(self.project.mapLayer(old_id), old)

    def layouts(self, *names):
        layouts = []
        for name in names:
            layout = QgsPrintLayout(self.project)
            layout.initializeDefaults()
            layout.setName(name)
            self.project.layoutManager().addLayout(layout)
            layouts.append(layout)
        return layouts

    def export_all(self):
        menu = L.export_layouts_menu(self.iface, self.win)
        menu.aboutToShow.emit()
        menu.actions()[-1].trigger()

    def test_batch_names_are_unique_after_sanitizing_and_case_folding(self):
        layouts = self.layouts('Map:A', 'Map?A', 'MAP_A', 'Map_A (2)', 'CON', 'Map .')
        targets = L._pdf_targets(layouts, self.tmp)
        names = [path.name for _, path in targets]
        self.assertEqual(len(set(name.casefold() for name in names)), len(layouts))
        self.assertIn('_CON.pdf', names)
        self.assertTrue(all(path.parent == self.tmp for _, path in targets))
        self.assertEqual(L._safe('Map .'), 'Map')

    def test_export_cancel_preserves_existing_pdf_without_any_export(self):
        self.layouts('Map')
        path = self.tmp / 'Map.pdf'
        path.write_bytes(b'existing PDF')
        with patch.object(L.QFileDialog, 'getExistingDirectory', return_value=str(self.tmp)), \
                patch.object(L.QMessageBox, 'question', return_value=L.QMessageBox.StandardButton.No) as question, \
                patch.object(L, '_export_pdf') as export:
            self.export_all()
        export.assert_not_called()
        self.assertEqual(path.read_bytes(), b'existing PDF')
        self.assertEqual(question.call_args.args[-1], L.QMessageBox.StandardButton.No)

    def test_authorized_replace_and_colliding_layouts_get_distinct_targets(self):
        self.layouts('Map:A', 'Map?A')
        (self.tmp / 'Map_A.pdf').write_bytes(b'existing PDF')
        with patch.object(L.QFileDialog, 'getExistingDirectory', return_value=str(self.tmp)), \
                patch.object(L.QMessageBox, 'question', return_value=L.QMessageBox.StandardButton.Yes), \
                patch.object(L, '_export_pdf', return_value=True) as export:
            self.export_all()
        self.assertEqual(export.call_count, 2)
        self.assertEqual({Path(call.args[2]).name for call in export.call_args_list}, {'Map_A.pdf', 'Map_A (2).pdf'})

    def test_real_batch_export_writes_two_distinct_valid_pdf_files(self):
        self.layouts('Map:A', 'Map?A')
        with patch.object(L.QFileDialog, 'getExistingDirectory', return_value=str(self.tmp)), \
                patch.object(L.QMessageBox, 'question') as question:
            self.export_all()
        question.assert_not_called()
        self.assertEqual({path.name for path in self.tmp.iterdir()}, {'Map_A.pdf', 'Map_A (2).pdf'})
        for path in self.tmp.iterdir():
            self.assertTrue(path.read_bytes().startswith(b'%PDF-'))

    def test_generic_plugin_actions_keep_their_original_state_and_handler(self):
        bar = QToolBar('Any plugin', self.win)
        bar.setObjectName('ExamplePluginTools')
        action = QAction('Run tool', self.win)
        action.setCheckable(True)
        handler = Mock()
        action.triggered.connect(handler)
        bar.addAction(action)
        owner = SimpleNamespace(absorbed=set(), placed=[], changes=Changes())
        owner.mirror = lambda *args, **kwargs: ribbon.Ribbon.mirror(owner, *args, **kwargs)
        groups = ribbon.Ribbon.addin_groups(owner, [bar])
        button = groups[0][1][0][0]
        self.assertIs(button.defaultAction(), action)
        button.click()
        handler.assert_called_once()
        self.assertTrue(action.isChecked())
        action.setEnabled(False)
        self.assertFalse(button.isEnabled())
        self.assertIn('ExamplePluginTools', owner.absorbed)

    def test_core_specs_have_no_plugin_specific_actions_and_locate_uses_project_layers(self):
        self.assertNotIn('@plugin_tabs', [title for title, _ in ribbon.RIBBON])
        specs = [item[0] for _, groups in ribbon.RIBBON for _, items in groups for item in items]
        specs += [item[0] for _, items in modify_features.TOOLS for item in items]
        self.assertFalse(any('plugin:' in source or 'bar:' in source for source in specs))
        first = self.layer('First address layer', 'fulladdr')
        second = self.layer('Second address layer', 'fulladdr')
        self.assertEqual({layer.id() for layer, _ in locate._address_layers()}, {first.id(), second.id()})

    def test_selection_chip_lists_layer_names(self):
        from keystone_under_test import selection_chip as chip
        roads = self.layer('Roads')
        parcels = self.layer('Parcels', 'pin')
        parcels.setDisplayExpression('"pin"')
        road_hits = [(roads, feature) for feature in roads.getFeatures()]
        parcel = next(parcels.getFeatures())
        labels = chip.menu_labels(road_hits + [(parcels, parcel)])
        self.assertEqual(len(labels), 3)
        self.assertTrue(labels[0].startswith('Roads'))
        self.assertTrue(labels[1].startswith('Roads'))
        self.assertNotEqual(labels[0], labels[1])
        self.assertNotIn(roads.id(), labels[0])
        self.assertTrue(labels[2].startswith('Parcels'))
        self.assertNotEqual(labels[2], str(parcel.id()))

    def test_attribute_table_dock_setting_turns_on_and_restores(self):
        from qgis.core import QgsSettings
        from keystone_under_test import docked_tables as tables
        settings = QgsSettings()
        before = {key: (settings.contains(key), settings.value(key))
                  for key in (tables.LEGACY_KEY, tables.DOCK_KEY)}
        docker = tables.TableDocker(self.iface)
        try:
            for key in (tables.LEGACY_KEY, tables.DOCK_KEY):
                self.assertEqual(settings.value(key, False, type=bool), True)
        finally:
            docker.stop()
        for key, (present, value) in before.items():
            self.assertEqual(settings.contains(key), present)
            if present:
                self.assertEqual(settings.value(key), value)

    def test_vector_layer_menu_starts_editing_and_the_vertex_tool(self):
        from qgis.core import QgsLayerTreeModel
        from qgis.gui import QgsLayerTreeView
        from keystone_under_test.contents_views import LayerMenuIcons
        layer = QgsVectorLayer(
            'Polygon?crs=EPSG:3857&field=name:string', 'Lots', 'memory')
        self.project.addMapLayer(layer)
        for name in (
                'mActionVertexToolActiveLayer', 'mActionMoveFeature',
                'mActionSplitFeatures', 'mActionReshapeFeatures'):
            action = QAction(self.win)
            action.setObjectName(name)
            action.setCheckable(True)
        view = QgsLayerTreeView(self.win)
        view.setModel(QgsLayerTreeModel(self.project.layerTreeRoot(), view))
        view.setCurrentLayer(layer)
        menu = QMenu()
        menu.addAction('Zoom')
        icons = LayerMenuIcons(view, Changes())
        try:
            icons.dress(menu)
            texts = [a.text() for a in menu.actions() if not a.isSeparator()]
            self.assertEqual(texts[0], 'Edit Vertices')
            self.assertIn('Move', texts)
            self.assertIn('Split', texts)
            self.assertIn('Reshape', texts)
            vertex = self.win.findChild(
                QAction, 'mActionVertexToolActiveLayer')
            hit = Mock()
            vertex.triggered.connect(hit)
            menu.actions()[0].trigger()
            self.assertTrue(layer.isEditable())
            hit.assert_called_once()
            point = self.layer('Points')
            view.setCurrentLayer(point)
            point_menu = QMenu()
            point_menu.addAction('Zoom')
            icons.dress(point_menu)
            point_texts = [a.text() for a in point_menu.actions()
                           if not a.isSeparator()]
            self.assertIn('Edit Vertices', point_texts)
            self.assertNotIn('Split', point_texts)
            self.assertNotIn('Reshape', point_texts)
        finally:
            icons.stop()
            view.deleteLater()

    def _menu_iface(self, layer):
        self.iface.activeLayer.return_value = layer
        vertex = QAction('Vertices', self.win)
        vertex.setObjectName('mActionVertexTool')
        reshape = QAction('Reshape', self.win)
        reshape.setObjectName('mActionReshapeFeatures')
        toggle = QAction('Toggle', self.win)
        toggle.setObjectName('mActionToggleEditing')
        self.iface.actionVertexTool.return_value = vertex
        self.iface.actionReshapeFeatures.return_value = reshape
        self.iface.actionToggleEditing.return_value = toggle
        self.iface.openFeatureForm = Mock()
        return vertex, reshape, toggle

    def _polygon(self):
        layer = QgsVectorLayer(
            'Polygon?crs=EPSG:3857&field=name:string', 'Lots', 'memory')
        feature = QgsFeature(layer.fields())
        feature['name'] = 'A'
        feature.setGeometry(QgsGeometry.fromWkt(
            'POLYGON((0 0, 10 0, 10 10, 0 10, 0 0))'))
        layer.dataProvider().addFeatures([feature])
        feature = QgsFeature(layer.fields())
        feature['name'] = 'B'
        feature.setGeometry(QgsGeometry.fromWkt(
            'POLYGON((30 30, 40 30, 40 40, 30 40, 30 30))'))
        layer.dataProvider().addFeatures([feature])
        self.project.addMapLayer(layer)
        self.canvas.setDestinationCrs(QgsCoordinateReferenceSystem('EPSG:3857'))
        self.canvas.setExtent(QgsRectangle(-10, -10, 80, 80))
        self.canvas.setLayers([layer])
        return layer

    def test_parse_wkt_ewkt_and_wkb(self):
        from keystone_under_test import canvas_menu as menu
        geom, srid = menu.parse_geometry('POINT(1 2)')
        self.assertEqual(srid, None)
        self.assertAlmostEqual(geom.asPoint().x(), 1)
        self.assertAlmostEqual(geom.asPoint().y(), 2)
        geom, srid = menu.parse_geometry('SRID=4326;POINT(1 2)')
        self.assertEqual(srid, 4326)
        self.assertFalse(geom.isEmpty())
        raw = bytes(geom.asWkb())
        parsed, _srid = menu.parse_geometry(raw.hex())
        self.assertFalse(parsed.isEmpty())
        parsed, _srid = menu.parse_geometry('0x' + raw.hex())
        self.assertFalse(parsed.isEmpty())
        parsed, _srid = menu.parse_geometry('\\x' + raw.hex())
        self.assertFalse(parsed.isEmpty())
        import base64
        parsed, _srid = menu.parse_geometry(base64.b64encode(raw).decode())
        self.assertFalse(parsed.isEmpty())
        parsed, parsed_srid = menu.parse_geometry(raw)
        self.assertFalse(parsed.isEmpty())
        self.assertIsNone(parsed_srid)
        self.assertEqual(menu.parse_geometry('not a geometry'), (None, None))
        self.assertEqual(menu.parse_geometry(''), (None, None))

    def test_dms_without_hemisphere_letters_reads_latitude_first_only_for_locate(self):
        crs = QgsCoordinateReferenceSystem("EPSG:4326")
        # New York, DMS, no N/S/E/W letters: Locate must read it latitude
        # first (as Google Maps does); the status bar's own coordinate box
        # keeps reading the same text longitude first, matching what it shows.
        text = "40 42 46 -74 0 22"
        ny_lat_first = coords.parse_point(text, "dms", crs, lat_first=True)
        self.assertAlmostEqual(ny_lat_first.x(), -74.0061, places=3)
        self.assertAlmostEqual(ny_lat_first.y(), 40.7128, places=3)
        ny_lon_first = coords.parse_point(text, "dms", crs)
        self.assertAlmostEqual(ny_lon_first.x(), 40.7128, places=3)
        self.assertAlmostEqual(ny_lon_first.y(), -74.0061, places=3)
        # An explicit hemisphere letter always wins, whichever order it's in.
        lettered = coords.parse_point("40 42 46N 74 0 22W", "dms", crs)
        self.assertAlmostEqual(lettered.x(), -74.0061, places=3)
        self.assertAlmostEqual(lettered.y(), 40.7128, places=3)

    def test_hit_prefers_the_selected_feature(self):
        from keystone_under_test import canvas_menu as menu
        layer = self._polygon()
        ids = [feature.id() for feature in layer.getFeatures()]
        near, far = [layer.getFeature(fid) for fid in ids]
        self.assertEqual(menu.choose_feature(layer, [near, far]).id(), near.id())
        layer.selectByIds([far.id()])
        self.assertEqual(menu.choose_feature(layer, [near, far]).id(), far.id())
        hits = menu.features_on_layer(
            self.canvas, layer, QgsPointXY(1, 1), radius=5)
        self.assertEqual([feature.id() for feature in hits], [near.id()])
        table = QgsVectorLayer('None?field=name:string', 'Table', 'memory')
        self.assertFalse(menu.has_geometry(table))

    def test_canvas_menu_edits_copies_and_pastes(self):
        from keystone_under_test import canvas_menu as menu
        from keystone_under_test.util import Changes
        layer = self._polygon()
        vertex, reshape, toggle = self._menu_iface(layer)
        hook = menu.CanvasMenu(self.iface)
        changes = Changes()
        changes.add(hook.stop)
        try:
            popup = QMenu()
            popup.addAction('Copy Coordinate')
            hook.dress(popup, SimpleNamespace(mapPoint=lambda: QgsPointXY(1, 1)))
            texts = [action.text() for action in popup.actions()
                     if not action.isSeparator()]
            self.assertEqual(texts[0], 'Start Editing')
            self.assertIn('Edit Attributes', texts)
            self.assertIn('Edit Vertices', texts)
            self.assertIn('Reshape Features', texts)
            self.assertIn('Copy to Scratch Layer', texts)
            self.assertIn('Paste Geometry', texts)
            self.assertEqual(texts[-1], 'Copy Coordinate')
            reshape_item = next(
                action for action in popup.actions()
                if action.text() == 'Reshape Features')
            self.assertTrue(reshape_item.isEnabled())
            edit_item = next(
                action for action in popup.actions()
                if action.text() == 'Edit Attributes')
            edit_item.trigger()
            self.iface.openFeatureForm.assert_called_once()
            vertex_item = next(
                action for action in popup.actions()
                if action.text() == 'Edit Vertices')
            vertex_hit = Mock()
            vertex.triggered.connect(vertex_hit)
            vertex_item.trigger()
            self.assertTrue(layer.isEditable())
            self.assertEqual(layer.selectedFeatureCount(), 1)
            vertex_hit.assert_called_once()
            toggle_hit = Mock()
            toggle.triggered.connect(toggle_hit)
            popup.actions()[0].trigger()
            toggle_hit.assert_called_once()

            point = self.layer('Points')
            self.iface.activeLayer.return_value = point
            point_menu = QMenu()
            hook.dress(point_menu, SimpleNamespace(mapPoint=lambda: QgsPointXY(0, 0)))
            point_reshape = next(
                action for action in point_menu.actions()
                if action.text() == 'Reshape Features')
            self.assertFalse(point_reshape.isEnabled())
            self.assertIn('points', point_reshape.toolTip())

            table = QgsVectorLayer('None?field=name:string', 'Table', 'memory')
            self.project.addMapLayer(table)
            self.iface.activeLayer.return_value = table
            table_menu = QMenu()
            hook.dress(table_menu, SimpleNamespace(mapPoint=lambda: QgsPointXY(0, 0)))
            table_texts = [action.text() for action in table_menu.actions()
                           if not action.isSeparator()]
            self.assertEqual(table_texts, ['Paste Geometry'])

            self.iface.activeLayer.return_value = layer
            copied = menu.copy_to_scratch(
                self.project, layer, list(layer.getFeatures()))
            self.assertEqual(copied.featureCount(), 2)
            self.assertEqual(copied.fields().names(), ['name'])
            self.assertEqual(copied.providerType(), 'memory')
            pasted, fid = menu.paste_geometry(
                self.project, self.canvas, 'SRID=3857;POINT(4 4)', layer)
            self.assertIsNotNone(fid)
            self.assertEqual(pasted.geometryType(), point.geometryType())
            self.assertIn(pasted, self.project.mapLayers().values())
            again, _fid = menu.paste_geometry(
                self.project, self.canvas, 'POINT(6 6)', pasted)
            self.assertEqual(again.id(), pasted.id())
            self.assertEqual(pasted.featureCount(), 2)
            self.assertIsNone(menu.paste_geometry(
                self.project, self.canvas, 'nope', layer)[0])
            reshape_hit = Mock()
            reshape.triggered.connect(reshape_hit)
            reshape_item.trigger()
            reshape_hit.assert_called_once()
        finally:
            changes.undo()


if __name__ == '__main__':
    unittest.main()
