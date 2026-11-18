"""Workspace Ribbon - an ArcGIS Pro style workspace for QGIS.

Based on ArcSkin by EnviroSolutions (GPL-2.0-or-later).
"""


def classFactory(iface):  # noqa: N802 (QGIS API name)
    from .plugin import WorkspaceRibbonPlugin

    return WorkspaceRibbonPlugin(iface)
