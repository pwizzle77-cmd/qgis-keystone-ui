"""Keystone plugin: an organized, ribbon-based workspace for QGIS, fully
reverted when switched off.

Based on ArcSkin by EnviroSolutions (GPL-2.0-or-later): its theme, Pro-style
icons, panes, map status bar, basemaps and symbology are used here with a
reorganised ribbon (see ribbon.py) built around native QGIS map creation
and editing. Installed plugin toolbars use generic Add-ins groups.

A theme is a directory under ``resources/themes/<name>/`` with two files:

* ``style.qss``     - Qt style sheet that may reference ``@name`` variables,
* ``variables.qss`` - ``@name: value;`` lines (same convention as QGIS themes).
"""

import re
from pathlib import Path

from qgis import utils as qgis_utils
from qgis.core import Qgis, QgsSettings
from qgis.PyQt.QtCore import QByteArray
from qgis.PyQt.QtGui import QAction, QIcon
from qgis.PyQt.QtWidgets import QToolBar

from .attributes_pane import attributes_pane
from .create_features import create_features_pane
from .docked_tables import docked_tables
from .layout_ribbon import layout_ribbons
from .locate import locate_pane
from .modify_features import modify_features_pane
from .selection_chip import selection_chip
from .sketch_toolbar import sketch_toolbar
from .dialogs import WindowDresser
from .icon_theme import apply_icon_theme
from .mapview import frame_map
from .panes import arrange_panes
from .progress import Progress
from .rubber_band import apply_esri_rubber_band
from .ribbon import build_ribbon
from .palette import LightPalette
from .stylesheet import StyleSheetBlock
from .symbology import ProSymbology
from .util import Changes, tr

PLUGIN_DIR = Path(__file__).resolve().parent
MENU_NAME = "&Keystone"
THEME = "pro_light"
VARIABLE_RE = re.compile(r"^\s*@([\w-]+)\s*:\s*(.+?)\s*;\s*$", re.MULTILINE)
# Window layout before the ribbon, kept in settings so that switching it
# off after a QGIS restart still brings the original toolbars back.
STATE_KEY = "keystone/original_window_state"
# The user's own on/off choice. Written only on a click.
ENABLED_KEY = "keystone/enabled"
# ArcSkin does the same job; both at once would fight over the window.
ARCSKIN = "arcskin"
ARCSKIN_ENABLED_KEY = "arcskin/enabled"


# Keystone was called Workspace Ribbon up to 0.3.3. Its settings carry over.
OLD_PREFIX = "workspace_ribbon"
NEW_PREFIX = "keystone"


def migrate_old_settings() -> int:
    """Copy the Workspace Ribbon settings to Keystone once (the window layout
    to restore, the on/off choice, collapsed ribbon …). Returns how many."""
    settings = QgsSettings()
    if settings.value(NEW_PREFIX + "/migrated", False, type=bool):
        return 0
    settings.beginGroup(OLD_PREFIX)
    keys = settings.allKeys()
    settings.endGroup()
    copied = 0
    for key in keys:
        new = f"{NEW_PREFIX}/{key}"
        if not settings.contains(new):
            settings.setValue(new, settings.value(f"{OLD_PREFIX}/{key}"))
            copied += 1
    settings.setValue(NEW_PREFIX + "/migrated", True)
    return copied


def theme_variables(theme: str) -> dict:
    """The theme's ``@name: value;`` colours from its ``variables.qss``."""
    theme_dir = PLUGIN_DIR / "resources" / "themes" / theme
    return dict(
        VARIABLE_RE.findall(
            (theme_dir / "variables.qss").read_text(encoding="utf-8")
        )
    )


def load_qss(theme: str) -> str:
    """Return the theme's style sheet with ``@name`` variables substituted.

    ``@icons`` expands to the plugin's icon directory, for ``url()`` values.
    """
    theme_dir = PLUGIN_DIR / "resources" / "themes" / theme
    variables = theme_variables(theme)
    variables["icons"] = (PLUGIN_DIR / "resources" / "icons").as_posix()
    qss = (theme_dir / "style.qss").read_text(encoding="utf-8")
    return re.sub(
        r"@([\w-]+)", lambda m: variables.get(m.group(1), m.group(0)), qss
    )


class KeystonePlugin:
    """QGIS plugin entry point: a checkable action toggles the workspace."""

    def __init__(self, iface):
        self.iface = iface
        self.changes = None  # undo log while the ribbon is on
        self.ribbon = None

    def initGui(self):  # noqa: N802 (QGIS API name)
        migrate_old_settings()
        icon = QIcon(str(PLUGIN_DIR / "resources" / "icons" / "icon.svg"))
        self.action = QAction(
            icon, tr("Keystone"), self.iface.mainWindow(),
            checkable=True)
        self.action.setToolTip(tr("Switch the Pro-style workspace on or off"))
        self.action.toggled.connect(self.set_enabled)
        self.action.triggered.connect(
            lambda on: QgsSettings().setValue(ENABLED_KEY, on))
        self.rebuild_action = QAction(
            tr("Rebuild Ribbon (after enabling plugins)"),
            self.iface.mainWindow())
        self.rebuild_action.triggered.connect(self.rebuild)
        self.customize_action = QAction(icon, tr("Customize Ribbon…"), self.iface.mainWindow())
        self.customize_action.setObjectName("KeystoneCustomizeRibbon")
        self.customize_action.triggered.connect(self.customize_ribbon)
        self.iface.addPluginToMenu(MENU_NAME, self.action)
        self.iface.addPluginToMenu(MENU_NAME, self.rebuild_action)
        self.iface.addPluginToMenu(MENU_NAME, self.customize_action)
        if not QgsSettings().value(ENABLED_KEY, True, type=bool):
            return
        # At QGIS start-up wait until all plugins are loaded, so that their
        # toolbars and actions reach the ribbon.
        if self.iface.mainWindow().isVisible():
            self.action.setChecked(True)
        else:
            self.iface.initializationCompleted.connect(
                lambda: self.action.setChecked(True))

    def unload(self):
        # QGIS freezes the canvas before unloading plugins when it quits;
        # then the workspace is left recorded as on for the next start.
        try:
            if not self.iface.mapCanvas().isFrozen():
                self.set_enabled(False)
        finally:
            self.iface.removePluginMenu(MENU_NAME, self.action)
            self.iface.removePluginMenu(MENU_NAME, self.rebuild_action)
            self.iface.removePluginMenu(MENU_NAME, self.customize_action)
            self.customize_action.deleteLater()

    def customize_ribbon(self):
        from .custom_ribbon import CustomizeRibbonDialog
        from .ribbon import RIBBON
        reserved = [title for title, _ in RIBBON] + [tr("Project"), tr("Add-ins")]
        if CustomizeRibbonDialog(self.iface.mainWindow(), reserved).exec() and self.ribbon is not None:
            self.ribbon.refresh_plugin_tabs()

    def rebuild(self):
        """Switch off and on: picks up plugins enabled since."""
        if self.action.isChecked():
            self.action.setChecked(False)
            self.action.setChecked(True)

    def _switch_off_arcskin(self):
        """Turn ArcSkin's look off (and keep it off) while this is on."""
        plugin = qgis_utils.plugins.get(ARCSKIN)
        action = getattr(plugin, "action", None)
        if action is not None and action.isChecked():
            action.setChecked(False)
            QgsSettings().setValue(ARCSKIN_ENABLED_KEY, False)
            self.iface.messageBar().pushMessage(
                tr("Keystone"),
                tr("ArcSkin's look was switched off; Keystone "
                   "replaces it. You can disable the ArcSkin plugin."),
                Qgis.MessageLevel.Info, 8)
        elif plugin is not None:
            QgsSettings().setValue(ARCSKIN_ENABLED_KEY, False)

    def set_enabled(self, enabled: bool) -> None:
        """Apply the workspace or restore the previous QGIS interface."""
        win = self.iface.mainWindow()
        settings = QgsSettings()
        if enabled and self.changes is None:
            self._switch_off_arcskin()
            # Panes go to Pro's places only the first time, so that panes
            # the user moved stay where QGIS restored them.
            first = settings.value(STATE_KEY) is None
            if first:
                settings.setValue(STATE_KEY, win.saveState())
            changes = self.changes = Changes()

            def ribbon_step():
                self.ribbon = build_ribbon(self.iface, changes, self.action)
                self.ribbon.docker = docked_tables(self.iface, changes)

            steps = [
                (8, lambda: changes.add(
                    StyleSheetBlock(win, load_qss(THEME)).remove)),
                # Light text colours for views that read the palette, not the
                # sheet (layer names in the Layers panel under Windows dark mode).
                (1, lambda: changes.add(
                    LightPalette(win, theme_variables(THEME)).remove)),
                (5, lambda: apply_icon_theme(self.iface, changes)),
                (1, lambda: WindowDresser(self.iface, changes)),
                (1, lambda: attributes_pane(self.iface, changes)),
                (1, lambda: create_features_pane(self.iface, changes)),
                (1, lambda: modify_features_pane(self.iface, changes)),
                (1, lambda: locate_pane(self.iface, changes)),
                (1, ribbon_step),
                (1, lambda: sketch_toolbar(self.iface, changes)),
                (1, lambda: selection_chip(self.iface, changes)),
                (1, lambda: layout_ribbons(self.iface, changes)),
                (1, lambda: arrange_panes(self.iface, changes, first)),
                (1, lambda: frame_map(self.iface, changes)),
                (3, lambda: ProSymbology(changes, self.iface.mapCanvas())),
                (1, lambda: apply_esri_rubber_band(changes)),
            ]
            total = sum(weight for weight, _ in steps)
            progress = Progress(win, tr("Setting up the workspace…"))
            try:
                done = 0
                for weight, step in steps:
                    step()
                    done += weight
                    progress.set(done / total)
            except Exception:
                # Half a workspace is worse than none: back to plain QGIS.
                changes.undo()
                self.changes = None
                self.ribbon = None
                self._restore_window()
                self.action.blockSignals(True)
                self.action.setChecked(False)
                self.action.blockSignals(False)
                raise
            finally:
                progress.done()
        elif not enabled and self.changes is not None:
            progress = Progress(win, tr("Restoring the QGIS look…"))
            try:
                self.changes.undo(progress.set)
            finally:
                progress.done()
                self.changes = None
                self.ribbon = None
                self._restore_window()

    def _restore_window(self):
        """Toolbars and panes as they were before the workspace went on."""
        win = self.iface.mainWindow()
        settings = QgsSettings()
        state = settings.value(STATE_KEY)
        if isinstance(state, QByteArray):
            win.restoreState(state)
        settings.remove(STATE_KEY)
        bars = [b for b in win.findChildren(QToolBar)
                if b.parent() is win and b.actions()]
        if not any(b.isVisible() for b in bars):
            for bar in bars:
                bar.show()
