"""Profile-local custom tabs built from named toolbars, independent of other plugins."""
import json

from qgis.core import QgsSettings
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import (
    QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
    QMessageBox, QPushButton, QToolBar, QVBoxLayout,
)

from .util import tr

SETTINGS_KEY = "keystone/custom_ribbon_tabs"
OWN_TOOLBAR = "ArcSkinRibbonBar"


def named_toolbars(win):
    """Only main-window toolbars with stable names can be restored after a restart."""
    bars = {}
    for bar in win.findChildren(QToolBar):
        name = bar.objectName()
        if bar.parent() is win and name and name != OWN_TOOLBAR and bar.actions():
            bars.setdefault(name, bar)
    return bars


def validate_tabs(tabs, reserved=()):
    if not isinstance(tabs, list):
        raise ValueError(tr("Custom tabs must be a list."))
    titles = {title.casefold() for title in reserved}
    assigned, result = set(), []
    for tab in tabs:
        if not isinstance(tab, dict) or not isinstance(tab.get("title"), str):
            raise ValueError(tr("Give every custom tab a name."))
        title, names = tab["title"].strip(), tab.get("toolbars")
        if not title or title.casefold() in titles:
            raise ValueError(tr("Tab names must be unique and different from the built-in tabs."))
        if (not isinstance(names, list) or not names
                or any(not isinstance(name, str) or not name.strip() for name in names)):
            raise ValueError(tr("Assign at least one toolbar to each custom tab."))
        names = [name.strip() for name in names]
        if len(set(names)) != len(names) or assigned.intersection(names):
            raise ValueError(tr("Assign each toolbar to only one custom tab."))
        titles.add(title.casefold())
        assigned.update(names)
        result.append({"title": title, "toolbars": names})
    return result


def load_tabs(settings=None, reserved=()):
    settings = QgsSettings() if settings is None else settings
    try:
        return validate_tabs(json.loads(settings.value(SETTINGS_KEY, "[]", type=str)), reserved)
    except (ValueError, TypeError):
        return []


class CustomizeRibbonDialog(QDialog):
    def __init__(self, win, reserved=()):
        super().__init__(win)
        self.setWindowTitle(tr("Customize Ribbon"))
        self.resize(720, 440)
        self.reserved = reserved
        self.available = named_toolbars(win)
        self._filling = False
        layout = QVBoxLayout(self)
        intro = QLabel(tr("Create tabs and assign toolbars from enabled plugins or QGIS. "
                          "Double-click a tab to rename it. Changes are saved in your QGIS profile."))
        intro.setWordWrap(True)
        layout.addWidget(intro)
        lists = QHBoxLayout()
        left, right = QVBoxLayout(), QVBoxLayout()
        left.addWidget(QLabel(tr("Custom tabs (in ribbon order)")))
        self.tabs = QListWidget()
        self.tabs.setMinimumWidth(200)
        self.tabs.currentItemChanged.connect(lambda *_: self._show_toolbars())
        left.addWidget(self.tabs)
        buttons = QHBoxLayout()
        for title, slot in ((tr("Add"), self._add), (tr("Remove"), self._remove),
                            (tr("Up"), lambda: self._move(-1)), (tr("Down"), lambda: self._move(1))):
            button = QPushButton(title)
            button.clicked.connect(slot)
            buttons.addWidget(button)
        left.addLayout(buttons)
        right.addWidget(QLabel(tr("Toolbars on the selected tab")))
        self.toolbars = QListWidget()
        self.toolbars.itemChanged.connect(self._toolbar_changed)
        right.addWidget(self.toolbars)
        note = QLabel(tr("Unassigned plugin toolbars stay on Add-ins. A toolbar can belong to one "
                         "custom tab. Missing toolbars are remembered until their plugin is enabled again."))
        note.setWordWrap(True)
        right.addWidget(note)
        lists.addLayout(left, 1)
        lists.addLayout(right, 2)
        layout.addLayout(lists)
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        reset = box.addButton(tr("Reset Custom Tabs"), QDialogButtonBox.ButtonRole.ResetRole)
        reset.clicked.connect(self._reset)
        box.accepted.connect(self._save)
        box.rejected.connect(self.reject)
        layout.addWidget(box)
        for tab in load_tabs(reserved=reserved):
            self._append(tab["title"], tab["toolbars"])
        self.tabs.setCurrentRow(0)
        self._show_toolbars()

    def _append(self, title, names):
        item = QListWidgetItem(title)
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
        item.setData(Qt.ItemDataRole.UserRole, list(names))
        self.tabs.addItem(item)
        return item

    def _add(self):
        names = {self.tabs.item(i).text().casefold() for i in range(self.tabs.count())}
        title, number = tr("New Tab"), 2
        while title.casefold() in names:
            title = tr("New Tab {}").format(number)
            number += 1
        item = self._append(title, [])
        self.tabs.setCurrentItem(item)
        self.tabs.editItem(item)

    def _remove(self):
        row = self.tabs.currentRow()
        if row >= 0:
            self.tabs.takeItem(row)
        self._show_toolbars()

    def _move(self, offset):
        row, target = self.tabs.currentRow(), self.tabs.currentRow() + offset
        if row >= 0 and 0 <= target < self.tabs.count():
            item = self.tabs.takeItem(row)
            self.tabs.insertItem(target, item)
            self.tabs.setCurrentItem(item)

    def _reset(self):
        self.tabs.clear()
        self._show_toolbars()

    def _show_toolbars(self):
        self._filling = True
        self.toolbars.clear()
        current = self.tabs.currentItem()
        chosen = set(current.data(Qt.ItemDataRole.UserRole) or []) if current else set()
        owners = {name: self.tabs.item(i).text() for i in range(self.tabs.count())
                  if self.tabs.item(i) is not current
                  for name in self.tabs.item(i).data(Qt.ItemDataRole.UserRole) or []}
        names = set(self.available) | chosen | set(owners)
        ordered = sorted(names, key=lambda key: (
            self.available[key].windowTitle() if key in self.available else key).casefold())
        for name in ordered:
            label = self.available[name].windowTitle() if name in self.available else name + tr(" [not loaded]")
            item = QListWidgetItem(label, self.toolbars)
            item.setData(Qt.ItemDataRole.UserRole, name)
            item.setToolTip(name)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if name in chosen else Qt.CheckState.Unchecked)
            if name in owners:
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
                item.setToolTip(tr("Assigned to {}. Remove it there to move it here.").format(owners[name]))
        self.toolbars.setEnabled(current is not None)
        self._filling = False

    def _toolbar_changed(self, _item):
        if not self._filling and self.tabs.currentItem() is not None:
            chosen = [self.toolbars.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.toolbars.count())
                      if self.toolbars.item(i).checkState() == Qt.CheckState.Checked]
            self.tabs.currentItem().setData(Qt.ItemDataRole.UserRole, chosen)

    def _save(self):
        tabs = [{"title": self.tabs.item(i).text(),
                 "toolbars": self.tabs.item(i).data(Qt.ItemDataRole.UserRole)} for i in range(self.tabs.count())]
        try:
            tabs = validate_tabs(tabs, self.reserved)
        except ValueError as error:
            QMessageBox.warning(self, self.windowTitle(), str(error))
            return
        QgsSettings().setValue(SETTINGS_KEY, json.dumps(tabs))
        self.accept()
