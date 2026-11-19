"""Esri-style edit sketch: the rubber band QGIS draws while you digitize.

ArcGIS Pro and ArcMap draw the edit sketch in green (green segments and
green vertex squares, the current vertex in red). QGIS's default is a thin
semi-transparent red line with a red fill. While the workspace is on, the
digitizing rubber band is a solid green 2 px line with a light green fill,
drawn at full strength, and existing vertices show as semi-transparent
circles instead of crosses.

Only settings still at QGIS's default (or at the value this plugin set) are
changed, so a colour the user picked in Options > Digitizing stays. The
values QGIS had are kept in the plugin's settings, so switching the
workspace off restores them also after a QGIS restart.
"""

import json

from qgis.core import QgsSettings
from qgis.PyQt.QtGui import QColor

ORIGINAL_KEY = "keystone/original_digitizing"

# Registry entry name -> Esri-style value
ESRI = {
    "settingsDigitizingLineColor": QColor(0, 168, 0, 255),
    "settingsDigitizingFillColor": QColor(0, 168, 0, 40),
    "settingsDigitizingLineWidth": 2,
    "settingsDigitizingLineColorAlphaScale": 1.0,
    "settingsDigitizingMarkerStyle": "SemiTransparentCircle",
}


def _entries():
    """The digitizing settings entries this QGIS has (by registry name)."""
    try:
        from qgis.core import QgsSettingsRegistryCore
    except ImportError:
        return {}
    found = {}
    for name in ESRI:
        entry = getattr(QgsSettingsRegistryCore, name, None)
        if entry is not None:
            found[name] = entry
    return found


def _encode(value):
    if isinstance(value, QColor):
        return {"color": value.name(QColor.NameFormat.HexArgb)}
    return {"value": value}


def _decode(data):
    if "color" in data:
        return QColor(data["color"])
    return data.get("value")


def _same(a, b):
    if isinstance(a, QColor) or isinstance(b, QColor):
        return QColor(a).rgba() == QColor(b).rgba()
    if isinstance(a, float) or isinstance(b, float):
        try:
            return abs(float(a) - float(b)) < 1e-9
        except (TypeError, ValueError):
            return False
    return a == b


def apply_esri_rubber_band(changes):
    """Set the Esri-style sketch; undone through ``changes``."""
    entries = _entries()
    if not entries:
        return
    settings = QgsSettings()
    stored = settings.value(ORIGINAL_KEY)
    try:
        original = json.loads(stored) if stored else {}
    except (TypeError, ValueError):
        original = {}
    changed = []
    for name, entry in entries.items():
        current = entry.value()
        ours = ESRI[name]
        if name in original or _same(current, ours):
            # set by this plugin before (also before a QGIS restart)
            changed.append(name)
            if not _same(current, ours):
                entry.setValue(ours)
            continue
        if _same(current, entry.defaultValue()):
            original[name] = _encode(current)
            entry.setValue(ours)
            changed.append(name)
        # else: the user chose this value in Options; keep it
    settings.setValue(ORIGINAL_KEY, json.dumps(original))

    def restore():
        saved = QgsSettings().value(ORIGINAL_KEY)
        try:
            back = json.loads(saved) if saved else {}
        except (TypeError, ValueError):
            back = {}
        for name in changed:
            entry = entries[name]
            if name in back and _same(entry.value(), ESRI[name]):
                entry.setValue(_decode(back[name]))
        QgsSettings().remove(ORIGINAL_KEY)

    changes.add(restore)
