"""Layout gallery for map creation, like Pro's Insert > New Layout.

* New Layout: page sizes (ANSI A-E, Legal, ISO A4/A3), each landscape or
  portrait, either blank or as a finished map sheet: map frame, title,
  legend, north arrow, scale bar and a footer with the date and coordinate
  system, laid out with even margins. US page sizes get feet/miles scale
  bars, ISO sizes metres/kilometres.
* The user's own layout templates (.qpt in QGIS's template folder) are
  listed in the same gallery; any .qpt can be imported.
* Open Layout / Export Layout menus list the project's layouts.
"""

from pathlib import Path

from qgis.core import (
    Qgis, QgsApplication, QgsDistanceArea, QgsFillSymbol, QgsLayoutExporter,
    QgsLayoutItemLabel, QgsLayoutItemLegend, QgsLayoutItemMap,
    QgsLayoutItemPicture, QgsLayoutItemScaleBar, QgsLayoutItemShape,
    QgsLayoutMeasurement, QgsLayoutPoint, QgsPointXY, QgsLayoutSize,
    QgsMessageLog, QgsPrintLayout, QgsProject,
    QgsReadWriteContext, QgsTextFormat, QgsUnitTypes,
)
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QAction, QColor, QFont
from qgis.PyQt.QtWidgets import QFileDialog, QMenu, QMessageBox
from qgis.PyQt.QtXml import QDomDocument

from .util import pro_icon, tr

MM = Qgis.LayoutUnit.Millimeters
INCH = 25.4
# Gallery: (name, short side mm, long side mm, imperial units)
PAGES = [
    (tr("Letter (ANSI A)"), 8.5 * INCH, 11 * INCH, True),
    (tr("Legal"), 8.5 * INCH, 14 * INCH, True),
    (tr("Tabloid (ANSI B)"), 11 * INCH, 17 * INCH, True),
    (tr("ANSI C"), 17 * INCH, 22 * INCH, True),
    (tr("ANSI D"), 22 * INCH, 34 * INCH, True),
    (tr("ANSI E"), 34 * INCH, 44 * INCH, True),
    ("A4", 210, 297, False),
    ("A3", 297, 420, False),
]
NORTH_ARROW = ":/images/north_arrows/layout_default_north_arrow.svg"
FONT = "Segoe UI"


def _text_format(size_pt, bold=False, color="#222222"):
    fmt = QgsTextFormat()
    font = QFont(FONT)
    font.setBold(bold)
    fmt.setFont(font)
    fmt.setSize(size_pt)
    fmt.setSizeUnit(Qgis.RenderUnit.Points)
    fmt.setColor(QColor(color))
    return fmt


def _place(item, x, y, w, h):
    item.attemptMove(QgsLayoutPoint(x, y, MM))
    item.attemptResize(QgsLayoutSize(w, h, MM))


def _label(layout, text, size_pt, rect, bold=False, align=None,
           color="#222222"):
    label = QgsLayoutItemLabel(layout)
    label.setText(text)
    label.setTextFormat(_text_format(size_pt, bold, color))
    label.setHAlign(align or Qt.AlignmentFlag.AlignHCenter)
    label.setVAlign(Qt.AlignmentFlag.AlignVCenter)
    layout.addLayoutItem(label)
    _place(label, *rect)
    return label


def _frame(layout, rect, outline="#4a4a4a", width="0.35"):
    shape = QgsLayoutItemShape(layout)
    shape.setShapeType(QgsLayoutItemShape.Shape.Rectangle)
    shape.setSymbol(QgsFillSymbol.createSimple({
        "color": "255,255,255,0", "outline_color": outline,
        "outline_width": width, "outline_width_unit": "MM"}))
    layout.addLayoutItem(shape)
    _place(shape, *rect)
    shape.setLocked(True)
    return shape


def _map_width_m(canvas):
    """Ground width of the canvas extent in metres (ellipsoidal for
    geographic coordinate systems)."""
    extent = canvas.extent()
    crs = canvas.mapSettings().destinationCrs()
    if crs.isGeographic():
        area = QgsDistanceArea()
        area.setSourceCrs(crs, QgsProject.instance().transformContext())
        area.setEllipsoid(crs.ellipsoidAcronym() or "EPSG:7030")
        y = extent.center().y()
        return area.measureLine(QgsPointXY(extent.xMinimum(), y),
                                QgsPointXY(extent.xMaximum(), y))
    factor = QgsUnitTypes.fromUnitToUnitFactor(crs.mapUnits(),
                                               Qgis.DistanceUnit.Meters)
    return extent.width() * factor


def _scale_bar(layout, map_item, canvas, imperial, rect):
    bar = QgsLayoutItemScaleBar(layout)
    bar.setLinkedMap(map_item)
    bar.setStyle("Single Box")
    width_m = _map_width_m(canvas)
    if imperial:
        unit = (Qgis.DistanceUnit.Miles if width_m > 3218
                else Qgis.DistanceUnit.Feet)
    else:
        unit = (Qgis.DistanceUnit.Kilometers if width_m > 2000
                else Qgis.DistanceUnit.Meters)
    bar.applyDefaultSize(unit)
    bar.setNumberOfSegmentsLeft(0)
    bar.setTextFormat(_text_format(8))
    layout.addLayoutItem(bar)
    bar.attemptMove(QgsLayoutPoint(rect[0], rect[1], MM))
    return bar


def _north_arrow(layout, map_item, rect):
    picture = QgsLayoutItemPicture(layout)
    picture.setPicturePath(NORTH_ARROW)
    picture.setLinkedMap(map_item)
    layout.addLayoutItem(picture)
    _place(picture, *rect)
    return picture


def _legend(layout, map_item, rect, size_scale):
    legend = QgsLayoutItemLegend(layout)
    legend.setTitle(tr("Legend"))
    legend.setLinkedMap(map_item)
    legend.setLegendFilterByMapEnabled(True)   # only what the map shows
    legend.setResizeToContents(False)
    legend.setFrameEnabled(False)
    try:
        style_title = legend.style(Qgis.LegendComponent.Title)
        style_title.setTextFormat(_text_format(11 * size_scale, bold=True))
        legend.setStyle(Qgis.LegendComponent.Title, style_title)
    except (AttributeError, TypeError) as err:
        QgsMessageLog.logMessage(
            f"layouts: could not style the legend title, keeping the default: {err!r}",
            "Keystone", Qgis.MessageLevel.Info)
    layout.addLayoutItem(legend)
    _place(legend, *rect)
    return legend


def _footer_text():
    return tr("Prepared {date}  |  Coordinate system: [% @project_crs %]"
              ).format(date="[% format_date(now(), 'MMMM d, yyyy') %]")


def build_layout(iface, page_name, short, long, imperial, landscape,
                 blank=False):
    """Create a print layout and open it in the layout designer."""
    project = QgsProject.instance()
    manager = project.layoutManager()
    canvas = iface.mapCanvas()
    w, h = (long, short) if landscape else (short, long)
    layout = QgsPrintLayout(project)
    layout.initializeDefaults()
    layout.pageCollection().page(0).setPageSize(QgsLayoutSize(w, h, MM))
    orientation = tr("Landscape") if landscape else tr("Portrait")
    base = f"{page_name} {orientation}"
    name, n = base, 1
    while manager.layoutByName(name):
        n += 1
        name = f"{base} {n}"
    layout.setName(name)

    if not blank:
        k = min(w, h) / (8.5 * INCH)         # text and margins grow with page
        m = 12.7 * max(1.0, k ** 0.5)        # 0.5 in margin on letter
        gap = 4 * max(1.0, k ** 0.5)
        title = project.title() or tr("Map Title")
        map_item = QgsLayoutItemMap(layout)
        map_item.setFrameEnabled(True)
        map_item.setFrameStrokeWidth(QgsLayoutMeasurement(0.35, MM))
        layout.addLayoutItem(map_item)

        def place_map(rect):
            # Extent before the scale bar, which sizes itself from it.
            _place(map_item, *rect)
            map_item.setCrs(canvas.mapSettings().destinationCrs())
            map_item.zoomToExtent(canvas.extent())

        if landscape:
            panel_w = max(55.0, w * 0.22)
            map_rect = (m, m, w - 2 * m - panel_w - gap, h - 2 * m)
            px = m + map_rect[2] + gap
            place_map(map_rect)
            _frame(layout, (px, m, panel_w, h - 2 * m))
            pad = 3 * k ** 0.5
            inner = panel_w - 2 * pad
            y = m + pad
            _label(layout, title, 20 * k, (px + pad, y, inner, 22 * k),
                   bold=True)
            y += 22 * k + pad
            legend_h = (h - 2 * m) * 0.55
            _legend(layout, map_item, (px + pad, y, inner, legend_h), k)
            y += legend_h + pad
            arrow = 16 * k
            _north_arrow(layout, map_item,
                         (px + (panel_w - arrow) / 2, y, arrow, arrow))
            y += arrow + pad
            _scale_bar(layout, map_item, canvas, imperial,
                       (px + pad, y, inner, 12 * k))
            _label(layout, _footer_text(), 7 * k,
                   (px + pad, h - m - pad - 10 * k, inner, 10 * k),
                   color="#5a5a5a")
        else:
            title_h = 16 * k
            strip_h = max(38.0, h * 0.17)
            _label(layout, title, 22 * k, (m, m, w - 2 * m, title_h),
                   bold=True)
            map_top = m + title_h + gap
            map_rect = (m, map_top, w - 2 * m,
                        h - map_top - m - strip_h - gap)
            place_map(map_rect)
            sy = h - m - strip_h
            _frame(layout, (m, sy, w - 2 * m, strip_h))
            pad = 3 * k ** 0.5
            legend_w = (w - 2 * m) * 0.55
            _legend(layout, map_item,
                    (m + pad, sy + pad, legend_w, strip_h - 2 * pad), k)
            right = m + legend_w + 2 * pad
            right_w = w - m - pad - right
            arrow = min(16 * k, strip_h * 0.4)
            _north_arrow(layout, map_item,
                         (right + (right_w - arrow) / 2, sy + pad, arrow,
                          arrow))
            _scale_bar(layout, map_item, canvas, imperial,
                       (right, sy + pad + arrow + pad, right_w, 12 * k))
            _label(layout, _footer_text(), 7 * k,
                   (right, sy + strip_h - pad - 9 * k, right_w, 9 * k),
                   color="#5a5a5a")

    manager.addLayout(layout)
    iface.openLayoutDesigner(layout)
    return layout


def _template_folders():
    folders = [Path(QgsApplication.qgisSettingsDirPath())
               / "composer_templates"]
    try:
        for path in QgsApplication.layoutTemplatePaths():
            folders.append(Path(path))
    except AttributeError as err:
        QgsMessageLog.logMessage(
            f"layouts: this QGIS has no layoutTemplatePaths(), skipping: {err!r}",
            "Keystone", Qgis.MessageLevel.Info)
    seen, result = set(), []
    for folder in folders:
        key = str(folder).lower()
        if key not in seen and folder.is_dir():
            seen.add(key)
            result.append(folder)
    return result


def load_template(iface, path):
    """New layout from a .qpt template file."""
    project = QgsProject.instance()
    doc = QDomDocument()
    doc.setContent(Path(path).read_text(encoding="utf-8"))
    layout = QgsPrintLayout(project)
    layout.initializeDefaults()
    result = layout.loadFromTemplate(doc, QgsReadWriteContext())
    ok = result[1] if isinstance(result, tuple) else bool(result)
    if not ok:
        iface.messageBar().pushWarning(
            tr("Keystone"),
            tr("Could not read the layout template {}").format(path))
        return None
    manager = project.layoutManager()
    base, name, n = Path(path).stem, Path(path).stem, 1
    while manager.layoutByName(name):
        n += 1
        name = f"{base} {n}"
    layout.setName(name)
    manager.addLayout(layout)
    iface.openLayoutDesigner(layout)
    return layout


def import_layout_template(iface):
    folders = _template_folders()
    start = str(folders[0]) if folders else ""
    path, _ = QFileDialog.getOpenFileName(
        iface.mainWindow(), tr("Import Layout Template"), start,
        tr("Layout templates (*.qpt)"))
    if path:
        load_template(iface, path)


def new_layout_menu(iface, parent):
    """Pro's New Layout gallery: page sizes x orientation, and templates."""
    menu = QMenu(parent)
    menu.setToolTipsVisible(True)
    menu.addSection(tr("Map sheets (map, title, legend, north arrow, "
                       "scale bar)"))
    for name, short, long, imperial in PAGES:
        sub = menu.addMenu(pro_icon("new_layout"), name)
        for landscape in (True, False):
            text = tr("Landscape") if landscape else tr("Portrait")
            sub.addAction(
                text, lambda n=name, s=short, lg=long, im=imperial,
                ls=landscape: build_layout(iface, n, s, lg, im, ls))
        sub.addSeparator()
        for landscape in (True, False):
            text = (tr("Blank landscape") if landscape
                    else tr("Blank portrait"))
            sub.addAction(
                text, lambda n=name, s=short, lg=long, im=imperial,
                ls=landscape: build_layout(iface, n, s, lg, im, ls,
                                           blank=True))
    templates_menu = menu.addMenu(pro_icon("layout_manager"),
                                  tr("My Templates"))

    def fill_templates():
        templates_menu.clear()
        found = False
        for folder in _template_folders():
            for path in sorted(folder.glob("*.qpt")):
                found = True
                templates_menu.addAction(
                    path.stem, lambda p=str(path): load_template(iface, p))
        if not found:
            empty = templates_menu.addAction(
                tr("No templates yet (save one from a layout: "
                   "Layout > Save as Template)"))
            empty.setEnabled(False)

    templates_menu.aboutToShow.connect(fill_templates)
    menu.addSeparator()
    menu.addAction(pro_icon("open_project"), tr("Import Layout Template…"),
                   lambda: import_layout_template(iface))
    return menu


def open_layouts_menu(iface, parent):
    """The project's layouts, to open in the layout designer."""
    menu = QMenu(parent)

    def fill():
        menu.clear()
        layouts = QgsProject.instance().layoutManager().printLayouts()
        for layout in sorted(layouts, key=lambda lay: lay.name().lower()):
            menu.addAction(pro_icon("new_layout"), layout.name(),
                           lambda lay=layout: iface.openLayoutDesigner(lay))
        if not layouts:
            empty = menu.addAction(tr("No layouts in this project"))
            empty.setEnabled(False)
        menu.addSeparator()
        manager = iface.mainWindow().findChild(
            QAction, "mActionShowLayoutManager")
        if manager:
            menu.addAction(manager)

    menu.aboutToShow.connect(fill)
    return menu


def _export_pdf(iface, layout, path):
    exporter = QgsLayoutExporter(layout)
    settings = QgsLayoutExporter.PdfExportSettings()
    result = exporter.exportToPdf(path, settings)
    return result == QgsLayoutExporter.ExportResult.Success


def export_layouts_menu(iface, parent):
    """Export one layout, or all of them, to PDF."""
    menu = QMenu(parent)
    bar = iface.messageBar()

    def export_one(layout):
        path, _ = QFileDialog.getSaveFileName(
            iface.mainWindow(), tr("Export Layout to PDF"),
            str(_default_folder() / f"{_safe(layout.name())}.pdf"), "PDF (*.pdf)")
        if not path:
            return
        if _export_pdf(iface, layout, path):
            bar.pushSuccess(tr("Export Layout"),
                            tr("Exported to {}").format(path))
        else:
            bar.pushWarning(tr("Export Layout"),
                            tr("Could not export {}").format(layout.name()))

    def export_all():
        layouts = QgsProject.instance().layoutManager().printLayouts()
        folder = QFileDialog.getExistingDirectory(
            iface.mainWindow(), tr("Export All Layouts to PDF"),
            str(_default_folder()))
        if not folder:
            return
        targets = _pdf_targets(layouts, folder)
        existing = [path.name for _, path in targets if path.exists()]
        if existing:
            answer = QMessageBox.question(
                iface.mainWindow(), tr("Replace existing PDFs?"),
                tr("These files already exist:\n{}\n\nReplace them?").format("\n".join(existing)),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes:
                return
        failed = [lay.name() for lay, path in targets if not _export_pdf(iface, lay, str(path))]
        if failed:
            bar.pushWarning(tr("Export Layout"),
                            tr("Not exported: {}").format(", ".join(failed)))
        else:
            bar.pushSuccess(tr("Export Layout"),
                            tr("{} layouts exported to {}").format(
                                len(layouts), folder))

    def fill():
        menu.clear()
        layouts = sorted(
            QgsProject.instance().layoutManager().printLayouts(),
            key=lambda lay: lay.name().lower())
        for layout in layouts:
            menu.addAction(pro_icon("export_pdf"), layout.name() + "…",
                           lambda lay=layout: export_one(lay))
        if layouts:
            menu.addSeparator()
            menu.addAction(tr("Export All Layouts to PDF…"), export_all)
        else:
            empty = menu.addAction(tr("No layouts in this project"))
            empty.setEnabled(False)

    menu.aboutToShow.connect(fill)
    return menu


def _default_folder():
    home = QgsProject.instance().homePath()
    return Path(home) if home else Path.home()


def _safe(name):
    name = "".join(c if ord(c) >= 32 and c not in '<>:"/\\|?*' else "_" for c in name).strip().rstrip(" .")
    name = name or "Layout"
    if name.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", "CLOCK$", "CONIN$", "CONOUT$"} \
            or name.split(".")[0].upper() in {f"{prefix}{n}" for prefix in ("COM", "LPT") for n in range(1, 10)}:
        name = "_" + name
    return name


def _pdf_targets(layouts, folder):
    """Reserve unique filenames for the whole batch, including case-insensitive filesystems."""
    used = set()
    targets = []
    for layout in layouts:
        stem = _safe(layout.name())
        filename, index = stem + ".pdf", 2
        while filename.casefold() in used:
            filename = f"{stem} ({index}).pdf"
            index += 1
        used.add(filename.casefold())
        targets.append((layout, Path(folder) / filename))
    return targets
