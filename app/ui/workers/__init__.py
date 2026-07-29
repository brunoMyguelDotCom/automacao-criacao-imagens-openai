"""Workers Qt para execução de tarefas fora da thread principal da UI.

Toda operação potencialmente lenta (I/O de disco, rede) deve rodar
em um `BaseWorker` (QRunnable) gerenciado por um `QThreadPool`. Os
workers emitem sinais para a thread principal, que atualiza widgets
sem nunca bloquear a UI.

Este pacote já existe no Prompt 1 para fixar o padrão arquitetural
mesmo ainda não havendo nenhuma operação lenta implementada.
"""

from __future__ import annotations

import logging
from typing import Any

from PySide6.QtCore import QObject, QRunnable, Signal, Slot

logger = logging.getLogger(__name__)


class WorkerSignals(QObject):
    """Sinais padrão emitidos por todos os workers.

    Definidos uma única vez aqui para evitar o erro clássico
    "QObject não pode ter sinais definidos fora do construtor de uma
    subclasse de QObject" — `Signal` só funciona em QObject-derived.
    """

    started = Signal()
    finished = Signal()
    progress = Signal(int, int)  # atual, total
    result = Signal(object)
    error = Signal(object)  # Exception


class BaseWorker(QRunnable):
    """Classe base para todos os workers do app.

    Subclasses devem implementar `run()` e usar `self.signals.*` para
    emitir progresso e resultado. O construtor nunca recebe objetos
    Qt — apenas dados puros (paths, ids, parâmetros).
    """

    def __init__(self) -> None:
        super().__init__()
        self.signals = WorkerSignals()

    def run(self) -> None:  # pragma: no cover - comportamento em subclasses
        self.signals.started.emit()
        try:
            result = self.do_work()
            self.signals.result.emit(result)
        except Exception as exc:  # noqa: BLE001 - última linha de defesa
            logger.exception("Worker falhou: %s", exc)
            self.signals.error.emit(exc)
        finally:
            self.signals.finished.emit()

    def do_work(self) -> Any:  # pragma: no cover
        raise NotImplementedError

    @Slot(int, int)
    def emit_progress(self, current: int, total: int) -> None:
        self.signals.progress.emit(current, total)