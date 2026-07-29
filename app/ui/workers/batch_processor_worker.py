"""Worker Qt que executa o `BatchProcessor` fora da thread da UI.

Religa os callbacks do `BatchProcessor` (que é puramente Python) em
signals Qt que a thread principal pode consumir com segurança.

Uso:

    processor = BatchProcessor(provider, jobs, on_event=...)
    worker = make_batch_processor_worker(processor)
    worker.signals.job_started.connect(...)
    QThreadPool.globalInstance().start(worker)

A UI chama `processor.start()` / `processor.pause()` /
`processor.resume()` / `processor.cancel()` da thread principal.
O loop do processor roda na thread do pool; os eventos voltam como
signals Qt (queued connections automáticos por morar em QObject).

Implementação:
    O `BatchProcessor` expõe `add_observer()` — um Observer
    público. Esta factory registra um observer que traduz cada
    `BatchEvent` em signals Qt. NÃO mexe em atributos privados.
"""

from __future__ import annotations

import logging
from typing import Any

from PySide6.QtCore import QObject, Signal

from app.core.models import ImageJob
from app.core.services import BatchEvent, BatchProcessor, EventKind
from app.core.services.batch_processor import ProgressSnapshot
from app.ui.workers import BaseWorker, WorkerSignals

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Signals                                                                      #
# --------------------------------------------------------------------------- #


class BatchProcessorSignals(WorkerSignals):
    """Sinais emitidos pelo `BatchProcessorWorker` na thread principal.

    Espelha os `EventKind` do BatchProcessor, mas como signals Qt
    para a UI poder conectar slots diretamente. Herda de
    `WorkerSignals` (started/finished/result/error/progress) para
    casar com o ciclo de vida padrão do `BaseWorker.run()`.

    Cada signal carrega a menor quantidade possível de informação —
    nada de objetos vivos do processor, só tipos serializáveis.
    """

    # Evento genérico (mantido para quem quiser logar tudo).
    event = Signal(object)  # BatchEvent

    # Eventos específicos (mais ergonômicos para a UI).
    job_started = Signal(object)        # ImageJob
    job_succeeded = Signal(object)      # ImageJob
    job_failed = Signal(object, str)    # ImageJob, error_message
    job_retried = Signal(object)        # ImageJob
    batch_started = Signal()
    batch_paused = Signal()
    batch_resumed = Signal()
    batch_cancelled = Signal()
    batch_completed = Signal()
    progress_updated = Signal(object)   # ProgressSnapshot


# --------------------------------------------------------------------------- #
# Worker                                                                       #
# --------------------------------------------------------------------------- #


class BatchProcessorWorker(BaseWorker):
    """QRunnable que executa o `BatchProcessor` e emite signals Qt."""

    def __init__(self, processor: BatchProcessor) -> None:
        super().__init__()
        # Criamos nossos próprios signals (não os do BaseWorker)
        # porque temos MUITOS sinais específicos.
        self.signals = BatchProcessorSignals()
        self._processor = processor

    def do_work(self) -> Any:  # type: ignore[override]
        """Roda o loop do BatchProcessor.

        `BatchProcessor.start()` é não-bloqueante (dispara uma
        thread interna). Aqui aguardamos a conclusão com
        `wait_until_done()` — sem timeout, porque o cancelamento
        via UI precisa chegar até a thread do loop para valer.

        O observer para signals Qt já foi registrado via
        `add_observer()` em `make_batch_processor_worker()`.
        """
        self._processor.start()
        self._processor.wait_until_done()
        return self._processor


# --------------------------------------------------------------------------- #
# Factory — junta processor + callback + worker                               #
# --------------------------------------------------------------------------- #


def make_batch_processor_worker(
    processor: BatchProcessor,
) -> BatchProcessorWorker:
    """Cria um `BatchProcessorWorker` religando os eventos do
    processor aos signals Qt.

    Registra um observer (via API pública `add_observer()`) que
    traduz cada `BatchEvent` em signals Qt. O callback principal
    do processor continua intacto.
    """

    worker = BatchProcessorWorker(processor)

    def observer(event: BatchEvent) -> None:
        _emit_qt_signal(worker.signals, event)

    processor.add_observer(observer)

    return worker


def _emit_qt_signal(signals: BatchProcessorSignals, event: BatchEvent) -> None:
    """Traduz um `BatchEvent` em signals Qt.

    Cada signal é envolvido em try/except individual — se UM
    receiver já morreu (e o GC não rodou ainda), os outros
    signals ainda são entregues.
    """

    def safe_emit(fn) -> None:
        try:
            fn()
        except Exception:  # noqa: BLE001
            logger.debug("Receiver do signal morreu (ignorado)")

    # Sinal genérico sempre primeiro — facilita log agregado.
    safe_emit(lambda: signals.event.emit(event))

    k = event.kind
    if k is EventKind.JOB_STARTED and event.job is not None:
        safe_emit(lambda: signals.job_started.emit(event.job))
    elif k is EventKind.JOB_SUCCEEDED and event.job is not None:
        safe_emit(lambda: signals.job_succeeded.emit(event.job))
    elif k is EventKind.JOB_FAILED:
        safe_emit(
            lambda: signals.job_failed.emit(
                event.job or ImageJob(),
                event.error.message if event.error else event.message,
            )
        )
    elif k is EventKind.JOB_RETRIED and event.job is not None:
        safe_emit(lambda: signals.job_retried.emit(event.job))
    elif k is EventKind.BATCH_STARTED:
        safe_emit(lambda: signals.batch_started.emit())
    elif k is EventKind.BATCH_PAUSED:
        safe_emit(lambda: signals.batch_paused.emit())
    elif k is EventKind.BATCH_RESUMED:
        safe_emit(lambda: signals.batch_resumed.emit())
    elif k is EventKind.BATCH_CANCELLED:
        safe_emit(lambda: signals.batch_cancelled.emit())
    elif k is EventKind.BATCH_COMPLETED:
        safe_emit(lambda: signals.batch_completed.emit())
    elif k is EventKind.PROGRESS_UPDATED and event.progress is not None:
        safe_emit(lambda: signals.progress_updated.emit(event.progress))


__all__ = [
    "BatchProcessorWorker",
    "BatchProcessorSignals",
    "make_batch_processor_worker",
]
