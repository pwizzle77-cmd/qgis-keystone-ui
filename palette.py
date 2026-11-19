"""A light application palette while the light skin is on.

The skin's style sheet paints panels light, but some of QGIS's own views
take their text colour from the application palette, not from the style
sheet: the Layers panel colours each layer name with the palette's Text
colour. When Windows is in dark mode, Qt 6 gives QGIS 4 a dark palette,
so those names came out white on the skin's white panels.

So while the skin is on, the application gets a light palette built from
the theme's colours, and switching the skin off gives back the palette
QGIS had. When Windows switches between light and dark while the skin is
on, Qt hands QGIS a new palette; the light one is put back on the next
turn of the event loop.
"""

from qgis.PyQt.QtCore import QEvent, QObject, QTimer
from qgis.PyQt.QtGui import QColor, QPalette
from qgis.PyQt.QtWidgets import QApplication

# Palette role -> theme variable (see resources/themes/<theme>/variables.qss)
ROLE_VARIABLES = (
    ("Window", "bg-window"),
    ("WindowText", "text"),
    ("Base", "bg-panel"),
    ("AlternateBase", "bg-header"),
    ("ToolTipBase", "bg-panel"),
    ("ToolTipText", "text-emphasis"),
    ("PlaceholderText", "text-disabled"),
    ("Text", "text"),
    ("Button", "bg-header"),
    ("ButtonText", "text"),
    ("BrightText", "bg-panel"),
    ("Highlight", "selected"),
    ("HighlightedText", "text-emphasis"),
    ("Link", "accent"),
    ("LinkVisited", "accent-dark"),
    ("Light", "bg-panel"),
    ("Midlight", "border-light"),
    ("Mid", "border"),
    ("Dark", "border-input"),
    ("Shadow", "text-disabled"),
)


def _role(name):
    roles = getattr(QPalette, "ColorRole", QPalette)
    return getattr(roles, name, None)


def _group(name):
    groups = getattr(QPalette, "ColorGroup", QPalette)
    return getattr(groups, name)


def light_palette(variables, base=None):
    """A palette with the theme's colours; roles the theme has no colour for keep ``base``'s."""
    pal = QPalette(base) if base is not None else QPalette()
    for role_name, var in ROLE_VARIABLES:
        role = _role(role_name)
        value = variables.get(var)
        if role is None or not value:
            continue
        color = QColor(value.strip())
        if not color.isValid():
            continue
        for group in ("Active", "Inactive", "Disabled"):
            pal.setColor(_group(group), role, color)
    disabled = QColor(variables.get("text-disabled", "#959595"))
    if disabled.isValid():
        for role_name in ("WindowText", "Text", "ButtonText"):
            pal.setColor(_group("Disabled"), _role(role_name), disabled)
    return pal


class LightPalette(QObject):
    """Keep a light application palette until :meth:`remove`."""

    def __init__(self, win, variables):
        super().__init__(win)
        app = QApplication.instance()
        self.original = QPalette(app.palette())
        self.palette = light_palette(variables, self.original)
        self.active = True
        self.pending = False
        self.setting = False
        self._apply()
        app.installEventFilter(self)

    def _apply(self):
        self.setting = True
        try:
            QApplication.setPalette(self.palette)
        finally:
            self.setting = False

    def eventFilter(self, watched, event):  # noqa: N802 (Qt API name)
        if (event.type() == QEvent.Type.ApplicationPaletteChange and self.active
                and not self.setting and not self.pending):
            self.pending = True
            QTimer.singleShot(0, self.resync)
        return False

    def resync(self):
        """Put the light palette back after Windows (or anyone) replaced it."""
        self.pending = False
        if not self.active:
            return
        app = QApplication.instance()
        role = _role("Text")
        if app.palette().color(role) != self.palette.color(role):
            self.original = QPalette(app.palette())     # what to give back later
            self.palette = light_palette_from(self.palette, self.original)
            self._apply()

    def remove(self):
        """Give back the palette QGIS had."""
        self.active = False
        app = QApplication.instance()
        app.removeEventFilter(self)
        QApplication.setPalette(self.original)
        self.deleteLater()


def light_palette_from(light, base):
    """``light``'s themed roles over a new ``base``."""
    pal = QPalette(base)
    for role_name, _var in ROLE_VARIABLES:
        role = _role(role_name)
        if role is None:
            continue
        for group in ("Active", "Inactive", "Disabled"):
            pal.setColor(_group(group), role, light.color(_group(group), role))
    return pal
