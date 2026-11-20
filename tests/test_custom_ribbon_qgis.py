"""Custom ribbon settings, dialog and live refresh using real Qt/QGIS objects."""
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

root = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('keystone_customization_test', root / '__init__.py',
                                            submodule_search_locations=[str(root)])
package = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = package
spec.loader.exec_module(package)
from qgis.core import QgsApplication, QgsVectorLayer
from qgis.gui import QgsMapCanvas
from qgis.PyQt import sip
from qgis.PyQt.QtCore import QEvent, Qt
from qgis.PyQt.QtGui import QAction
from qgis.PyQt.QtWidgets import QMainWindow, QMenu, QToolBar, QToolButton, QWidget
from keystone_customization_test import custom_ribbon as C, plugin, ribbon as R
from keystone_customization_test import modify_features as M, tool_placements as P
from keystone_customization_test.util import Changes


class Settings:
    def __init__(self):
        self.data = {}

    def value(self, key, default=None, type=None):
        return self.data.get(key, default)

    def setValue(self, key, value):
        self.data[key] = value


class CustomRibbonTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QgsApplication.instance() or QgsApplication([], False)
        cls.app.initQgis()

    def setUp(self):
        self.win = QMainWindow()
        self.canvas = QgsMapCanvas(self.win)
        self.settings = Settings()
        self.settings_patch = patch.object(C, 'QgsSettings', return_value=self.settings)
        self.settings_patch.start()
        self.changes = Changes()
        self.iface = Mock()
        self.iface.mainWindow.return_value = self.win
        self.iface.mapCanvas.return_value = self.canvas
        self.iface.activeLayer.return_value = None

    def tearDown(self):
        self.changes.undo()
        self.settings_patch.stop()
        self.win.deleteLater()
        self.app.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def toolbar(self, name, title=None):
        bar = QToolBar(title or name, self.win)
        bar.setObjectName(name)
        action = QAction('Run ' + name, self.win)
        action.setCheckable(True)
        bar.addAction(action)
        self.win.addToolBar(bar)
        return bar, action

    def configure(self, tabs):
        self.settings.setValue(C.SETTINGS_KEY, json.dumps(tabs))

    def build(self):
        toggle = QAction('Keystone', self.win)
        with patch.object(R, 'QgsSettings', return_value=self.settings):
            return R.build_ribbon(self.iface, self.changes, toggle)

    def test_settings_validate_titles_and_toolbar_ownership(self):
        valid = [{'title': ' Tools ', 'toolbars': ['a']}]
        self.assertEqual(C.validate_tabs(valid)[0]['title'], 'Tools')
        invalid = [None, [{'title': '', 'toolbars': ['a']}], [{'title': 'Map', 'toolbars': ['a']}],
                   [{'title': 'One', 'toolbars': []}], [{'title': 'One', 'toolbars': ['a', 'a']}],
                   [{'title': 'One', 'toolbars': ['a']}, {'title': 'one', 'toolbars': ['b']}],
                   [{'title': 'One', 'toolbars': ['a']}, {'title': 'Two', 'toolbars': ['a']}]]
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValueError):
                C.validate_tabs(value, ['Map'])

    def test_saved_tabs_survive_missing_toolbars_and_bad_settings_do_not_crash(self):
        tabs = [{'title': 'Optional tools', 'toolbars': ['not-loaded']}]
        self.configure(tabs)
        self.assertEqual(C.load_tabs(), tabs)
        self.settings.setValue(C.SETTINGS_KEY, 'not JSON')
        self.assertEqual(C.load_tabs(), [])

    def test_inventory_includes_named_plugin_and_native_bars_but_not_skin_or_child_bars(self):
        self.toolbar('CustomTools')
        self.toolbar('mExampleTools')
        self.toolbar(C.OWN_TOOLBAR)
        child = QToolBar('Child', QWidget(self.win))
        child.setObjectName('ChildTools')
        child.addAction('Child command')
        self.assertEqual(set(C.named_toolbars(self.win)), {'CustomTools', 'mExampleTools'})

    def test_dialog_saves_named_tabs_assignments_and_order(self):
        self.toolbar('a', 'First toolbar')
        self.toolbar('b', 'Second toolbar')
        dialog = C.CustomizeRibbonDialog(self.win, ['Map'])
        dialog._append('First', ['a'])
        dialog._append('Second', ['b'])
        dialog.tabs.setCurrentRow(1)
        dialog._move(-1)
        dialog.tabs.item(0).setText('Renamed')
        dialog._save()
        self.assertEqual(C.load_tabs(), [{'title': 'Renamed', 'toolbars': ['b']},
                                        {'title': 'First', 'toolbars': ['a']}])

    def test_dialog_cancel_and_reset_do_not_write_preferences(self):
        original = [{'title': 'Saved', 'toolbars': ['a']}]
        self.configure(original)
        dialog = C.CustomizeRibbonDialog(self.win)
        dialog._reset()
        dialog.reject()
        self.assertEqual(C.load_tabs(), original)
        dialog = C.CustomizeRibbonDialog(self.win)
        dialog._reset()
        dialog._save()
        self.assertEqual(C.load_tabs(), [])

    def test_dialog_checkbox_assignment_and_duplicate_ownership(self):
        self.toolbar('a')
        self.toolbar('b')
        dialog = C.CustomizeRibbonDialog(self.win)
        dialog._append('First', [])
        dialog.tabs.setCurrentRow(0)
        dialog.toolbars.item(0).setCheckState(Qt.CheckState.Checked)
        self.assertEqual(dialog.tabs.item(0).data(Qt.ItemDataRole.UserRole), ['a'])
        dialog._append('Second', ['b'])
        dialog.tabs.setCurrentRow(1)
        assigned = next(dialog.toolbars.item(i) for i in range(dialog.toolbars.count())
                        if dialog.toolbars.item(i).data(Qt.ItemDataRole.UserRole) == 'a')
        self.assertFalse(assigned.flags() & Qt.ItemFlag.ItemIsEnabled)

    def test_dialog_rejects_reserved_names_without_changing_saved_settings(self):
        dialog = C.CustomizeRibbonDialog(self.win, ['Map'])
        dialog._append('Map', ['a'])
        with patch.object(C.QMessageBox, 'warning') as warning:
            dialog._save()
        warning.assert_called_once()
        self.assertEqual(self.settings.data, {})

    def test_custom_tabs_and_addins_use_original_actions_and_keep_native_pages(self):
        _, action = self.toolbar('a', 'First toolbar')
        self.toolbar('b', 'Unassigned toolbar')
        handler = Mock()
        action.triggered.connect(handler)
        self.configure([{'title': 'My tools', 'toolbars': ['a']}])
        owner = self.build()
        titles = [owner.tabs.tabText(i) for i in range(owner.tabs.count())]
        self.assertIn('My tools', titles)
        self.assertIn('Add-ins', titles)
        page = owner.tabs.widget(titles.index('My tools'))
        button = next(button for button in page.findChildren(QToolButton) if button.defaultAction() is action)
        button.click()
        handler.assert_called_once()
        action.setEnabled(False)
        self.assertFalse(button.isEnabled())
        native_page = owner.tabs.widget(0)
        editor = QWidget(self.win)
        self.iface.currentLayerChanged.connect.reset_mock()
        self.configure([{'title': 'Renamed tools', 'toolbars': ['a', 'b']}])
        owner.refresh_plugin_tabs()
        self.assertIs(owner.tabs.widget(0), native_page)
        self.assertFalse(sip.isdeleted(editor))
        self.iface.currentLayerChanged.connect.assert_not_called()
        self.assertNotIn('Add-ins', [owner.tabs.tabText(i) for i in range(owner.tabs.count())])

    def test_missing_toolbar_tab_appears_and_recovers_when_toolbar_is_loaded(self):
        self.configure([{'title': 'Optional', 'toolbars': ['later']}])
        owner = self.build()
        owner.tabs.setCurrentWidget(owner.plugin_pages[0])
        self.assertEqual(owner.plugin_pages[0].findChildren(QToolButton), [])
        _, action = self.toolbar('later', 'Loaded later')
        owner.refresh_plugin_tabs()
        self.assertEqual(owner.tabs.tabText(owner.tabs.currentIndex()), 'Optional')
        self.assertTrue(any(button.defaultAction() is action for button in owner.plugin_pages[0].findChildren(QToolButton)))
        self.assertEqual(C.load_tabs(), [{'title': 'Optional', 'toolbars': ['later']}])

    def test_menu_buttons_survive_repeated_refresh_and_keeper_is_replaced(self):
        bar, _ = self.toolbar('menus')
        widget = QToolButton(bar)
        menu = QMenu(widget)
        command = menu.addAction('Menu command')
        handler = Mock()
        command.triggered.connect(handler)
        widget.setText('Choose command')
        widget.setMenu(menu)
        bar.addWidget(widget)
        self.configure([{'title': 'Menus', 'toolbars': ['menus']}])
        owner = self.build()
        for _ in range(3):
            owner.refresh_plugin_tabs()
            self.app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        mirrored = next(button for button in owner.plugin_pages[0].findChildren(QToolButton) if button.menu() is menu)
        mirrored.menu().actions()[0].trigger()
        handler.assert_called_once()
        self.assertEqual(len(self.win.findChildren(R.ToolbarKeeper)), 1)

    def test_plugin_dialog_applies_live_without_rebuilding_skin_and_cancel_does_nothing(self):
        controller = plugin.KeystonePlugin(self.iface)
        controller.ribbon = SimpleNamespace(refresh_plugin_tabs=Mock())
        controller.rebuild = Mock()
        with patch.object(C, 'CustomizeRibbonDialog') as dialog:
            dialog.return_value.exec.return_value = 1
            controller.customize_ribbon()
            controller.ribbon.refresh_plugin_tabs.assert_called_once()
            controller.rebuild.assert_not_called()
            controller.ribbon.refresh_plugin_tabs.reset_mock()
            dialog.return_value.exec.return_value = 0
            controller.customize_ribbon()
            controller.ribbon.refresh_plugin_tabs.assert_not_called()

    def test_refresh_keeps_contextual_page_and_collapsed_state(self):
        self.toolbar('a')
        self.configure([{'title': 'Custom', 'toolbars': ['a']}])
        owner = self.build()
        layer = QgsVectorLayer('Point?crs=EPSG:3857&field=name:string', 'Selected layer', 'memory')
        self.iface.activeLayer.return_value = layer
        owner.layer_changed(layer)
        self.app.processEvents()
        context_page = owner.context_pages['vector'][0][0]
        owner.tabs.setCurrentWidget(context_page)
        owner.set_collapsed(True, remember=False)
        owner.refresh_plugin_tabs()
        self.assertIs(owner.tabs.currentWidget(), context_page)
        self.assertEqual(owner.context_kind, 'vector')
        self.assertTrue(owner.commands['collapse_ribbon'].isChecked())

    def test_customize_menu_exists_even_when_skin_is_disabled(self):
        self.settings.data['keystone/enabled'] = False
        controller = plugin.KeystonePlugin(self.iface)
        controller.customize_ribbon = Mock()
        with patch.object(plugin, 'migrate_old_settings'), \
                patch.object(plugin, 'QgsSettings', return_value=self.settings):
            controller.initGui()
        action = self.win.findChild(QAction, 'KeystoneCustomizeRibbon')
        self.assertIs(action, controller.customize_action)
        action.trigger()
        controller.customize_ribbon.assert_called_once()
        self.assertEqual(self.iface.addPluginToMenu.call_count, 3)
        self.assertIsNone(controller.ribbon)

    def declare(self, action, **overrides):
        metadata = dict(tab='Edit', group='Tools', modify_group='Alignment', description='Example tool help')
        metadata.update(overrides)
        self.win.setProperty(P.PROPERTY, {action.objectName(): metadata})

    def test_declared_tools_appear_automatically_in_edit_and_modify_and_unload_cleanly(self):
        owner = self.build()
        pane = M.modify_features_pane(self.iface, self.changes)
        action = QAction('Independent tool', self.win)
        action.setObjectName('independentTool')
        action.setCheckable(True)
        handler = Mock()
        action.triggered.connect(handler)
        self.declare(action)
        self.app.processEvents()
        self.assertEqual(len(owner._registered_buttons), 1)
        button = owner._registered_buttons[0][1]
        self.assertIs(button.defaultAction(), action)
        self.assertEqual(owner._core_pages['Edit']._ribbon_groups['Tools']._button_row.count(), 1)
        group = next(pane.tree.topLevelItem(i) for i in range(pane.tree.topLevelItemCount())
                     if pane.tree.topLevelItem(i).text(0) == 'Alignment')
        item = group.child(0)
        self.assertEqual(item.data(0, M.ACTION_ROLE), 'independentTool')
        button.click()
        self.assertTrue(action.isChecked())
        pane._clicked(item, 0)
        self.assertFalse(action.isChecked())
        self.assertEqual(handler.call_count, 2)
        action.setEnabled(False)
        self.assertFalse(button.isEnabled())
        pane._clicked(item, 0)
        self.assertEqual(handler.call_count, 2)
        self.win.setProperty(P.PROPERTY, {})
        self.app.processEvents()
        self.assertEqual(owner._registered_buttons, [])
        self.assertEqual(pane.registered_names, set())
        self.assertNotIn('Tools', owner._core_pages['Edit']._ribbon_groups)

    def test_declarations_before_skin_start_and_settings_menu_use_existing_actions(self):
        self.toolbar('exampleToolbar')
        action = QAction('Independent tool', self.win)
        action.setObjectName('independentTool')
        settings = QAction('Settings', self.win)
        settings.setObjectName('independentToolSettings')
        handler = Mock()
        settings.triggered.connect(handler)
        self.declare(action, menu_actions=['independentToolSettings', 'missingSettings'])
        owner = self.build()
        button = owner._registered_buttons[0][1]
        self.assertIs(button.menu().actions()[0], settings)
        button.menu().actions()[0].trigger()
        handler.assert_called_once()
        self.assertIsNone(action.menu())

    def test_registry_changes_preserve_native_groups_custom_tabs_and_context(self):
        self.toolbar('exampleToolbar')
        self.configure([{'title': 'Custom', 'toolbars': ['exampleToolbar']}])
        move = QAction('Move', self.win)
        move.setObjectName('mActionMoveFeature')
        owner = self.build()
        page = owner._core_pages['Edit']
        original_group = page._ribbon_groups['Tools']
        original_buttons = list(original_group.findChildren(QToolButton))
        action = QAction('Independent tool', self.win)
        action.setObjectName('independentTool')
        for _ in range(3):
            self.declare(action)
            self.app.processEvents()
            self.assertIs(owner._core_pages['Edit'], page)
            self.assertIs(page._ribbon_groups['Tools'], original_group)
            self.win.setProperty(P.PROPERTY, {})
            self.app.processEvents()
        self.app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.assertEqual(original_group.findChildren(QToolButton), original_buttons)
        self.assertIn('Custom', [owner.tabs.tabText(i) for i in range(owner.tabs.count())])

    def test_bad_declarations_and_missing_actions_are_ignored(self):
        action = QAction('Independent tool', self.win)
        action.setObjectName('independentTool')
        for registry in ([], {'missing': {'tab': 'Edit'}}, {'independentTool': []},
                         {'independentTool': {'tab': []}}, {'independentTool': {'menu_actions': ['valid', 3]}}):
            self.win.setProperty(P.PROPERTY, registry)
            self.assertEqual(P.declared_tools(self.win), [])

    def test_placement_watcher_coalesces_updates_and_stops_pending_callbacks(self):
        callback = Mock()
        watcher = P.PlacementWatcher(self.win, callback)
        self.win.setProperty(P.PROPERTY, {'first': {}})
        self.win.setProperty(P.PROPERTY, {'second': {}})
        self.app.processEvents()
        callback.assert_called_once()
        callback.reset_mock()
        self.win.setProperty(P.PROPERTY, {})
        watcher.stop()
        self.app.processEvents()
        callback.assert_not_called()

    def test_late_registered_tool_does_not_leave_a_duplicate_plugin_toolbar(self):
        owner = self.build()
        bar, action = self.toolbar('ExampleRegisteredTools')
        action.setObjectName('independentTool')
        self.declare(action)
        self.app.processEvents()
        self.assertEqual(len(owner._registered_buttons), 1)
        self.assertIn(bar.objectName(), owner.absorbed)
        self.assertTrue(bar.isHidden())


if __name__ == '__main__':
    unittest.main()
