"""Abertura cross-platform de paths no app nativo do SO.

Suporta Windows (`os.startfile`), macOS (`open`), Linux/BSD
(`xdg-open`), com fallback `QDesktopServices.openUrl` quando o
launcher nativo falha.

Centralizado aqui para que MainWindow, DashboardWidget e
qualquer outro componente possa reutilizar sem duplicar código.
"""

from __future__ import annotations

import logging
import os
import platform
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


def open_path_in_shell(path: Path | str) -> bool:
    """Abre `path` no app nativo do SO. Retorna True se tentou abrir.

    `False` apenas quando o caminho não existe — nesse caso,
    mostra um `QMessageBox` informativo se houver QApplication.
    """
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QDesktopServices

    p = Path(path) if not isinstance(path, Path) else path

    if not p.exists():
        try:
            from PySide6.QtWidgets import QApplication, QMessageBox

            if QApplication.instance() is not None:
                QMessageBox.information(
                    None,
                    "Caminho indisponível",
                    f"O caminho não existe mais:\n{p}",
                )
        except Exception:  # noqa: BLE001
            pass
        return False

    system = platform.system()
    try:
        if system == "Windows":
            os_startfile = getattr(os, "startfile", None)
            if os_startfile is not None:
                os_startfile(str(p))  # type: ignore[call-arg]
                return True
        elif system == "Darwin":
            subprocess.run(["open", str(p)], check=False)
            return True
        else:
            subprocess.run(["xdg-open", str(p)], check=False)
            return True
    except Exception:  # noqa: BLE001
        logger.exception("Falha ao abrir caminho %s", p)

    # Fallback Qt.
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(p)))
    return True


__all__ = ["open_path_in_shell"]