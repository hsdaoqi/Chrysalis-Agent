"""Chrysalis desktop entry point."""

from __future__ import annotations

import sys
from pathlib import Path


def main() -> None:
    import os

    os.environ.setdefault("QT_QUICK_CONTROLS_STYLE", "Basic")

    try:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QGuiApplication, QIcon
        from PySide6.QtQml import QQmlApplicationEngine
    except Exception as exc:  # pragma: no cover
        raise SystemExit(
            "PySide6 is not installed. Install with: pip install -e \".[desktop]\""
        ) from exc

    from chrysalis.desktop.backend import DesktopBackend

    app = QGuiApplication(sys.argv)
    app.setApplicationName("Chrysalis")
    app.setOrganizationName("Chrysalis")
    icon_path = _icon_path()
    if icon_path:
        app.setWindowIcon(QIcon(icon_path))

    backend = DesktopBackend()
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("backend", backend)
    engine.rootContext().setContextProperty("appVersion", "0.1")
    engine.load(QUrl.fromLocalFile(str(_qml_path())))

    if not engine.rootObjects():
        raise SystemExit("Failed to load desktop UI")

    sys.exit(app.exec())


def _qml_path() -> str:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        base = Path(sys._MEIPASS)  # type: ignore[attr-defined]
    else:
        base = Path(__file__).resolve().parent
    return str(base / "qml" / "Main.qml")


def _icon_path() -> str | None:
    candidates = []
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        base = Path(sys._MEIPASS)  # type: ignore[attr-defined]
        candidates.append(base / "assets" / "images" / "chrysalis-icon.ico")
    base = Path(__file__).resolve().parents[2]
    candidates.append(base / "assets" / "images" / "chrysalis-icon.ico")
    for path in candidates:
        if path.exists():
            return str(path)
    return None


if __name__ == "__main__":
    main()
