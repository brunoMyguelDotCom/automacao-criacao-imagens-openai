"""Worker que executa o scan fora da thread principal da UI.

Encapsula `ImageFolderScanner` num `BaseWorker` (QRunnable). A UI
submete ao `QThreadPool.globalInstance()` e conecta os sinais
(`started`, `progress`, `result`, `error`, `finished`) aos slots
apropriados.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from app.core.models import ScanResult
from app.core.services import ImageFolderScanner
from app.ui.workers import BaseWorker

logger = logging.getLogger(__name__)


class ScanWorker(BaseWorker):
    """QRunnable que escaneia uma pasta e emite o `ScanResult`."""

    def __init__(
        self,
        folder: Path,
        scanner: ImageFolderScanner | None = None,
    ) -> None:
        super().__init__()
        self._folder = Path(folder)
        self._scanner = scanner or ImageFolderScanner()

    def do_work(self) -> ScanResult:
        logger.info("ScanWorker iniciando em %s", self._folder)
        # O scanner atual não emite progresso intermediário; o
        # BaseWorker emite `started`/`finished` automaticamente.
        result = self._scanner.scan(self._folder)
        # Emite progresso "100% completo" para a UI.
        self.emit_progress(1, 1)
        return result


__all__ = ["ScanWorker"]