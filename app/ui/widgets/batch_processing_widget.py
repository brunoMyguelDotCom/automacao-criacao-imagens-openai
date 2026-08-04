"""Widget de processamento em lote (Prompt 7).

Concentra:

    * Botões Iniciar / Pausar / Retomar / Cancelar (com habilitação
      correta conforme estado do `BatchProcessor`).
    * Barra de progresso + label "arquivo atual".
    * Contadores SUCCESS / FAILED / CANCELLED / PENDING em tempo real.
    * Plug no `BatchProcessorWorker` (QRunnable) para não bloquear a
      UI — toda chamada ao provider acontece na thread do pool.

Não conhece a rede nem o `OpenAIImageGenerationProvider`. O caller
passa qualquer `ImageGenerationProvider` já configurado.

Threading:
    * Slot chamado por sinais Qt rodam na thread principal.
    * O processor roda na thread do pool. A comunicação entre eles é
      via Qt signals (queued connections automáticos por morarem em
      QObjects diferentes).

Visual: herda do tema escuro global em `app/ui/theme.py`.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterable

from PySide6.QtCore import Qt, QThreadPool, Slot
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.core.models import ImageJob
from app.core.providers import ImageGenerationProvider
from app.core.services import BatchProcessor
from app.ui.workers.batch_processor_worker import (
    BatchProcessorSignals,
    make_batch_processor_worker,
)

logger = logging.getLogger(__name__)


# Estilo local para o botão "Iniciar" (primário) e "Cancelar" (perigoso).
PRIMARY_BTN_STYLE = """
QPushButton {
    background-color: #4493f8;
    color: white;
    border: 1px solid #4493f8;
    border-radius: 6px;
    padding: 8px 18px;
    font-weight: 600;
}
QPushButton:hover { background-color: #58a6ff; border-color: #58a6ff; }
QPushButton:pressed { background-color: #2f7be0; }
QPushButton:disabled {
    background-color: #21262d;
    color: #484f58;
    border-color: #30363d;
}
"""

DANGER_BTN_STYLE = """
QPushButton {
    background-color: #21262d;
    color: #f85149;
    border: 1px solid #f85149;
    border-radius: 6px;
    padding: 8px 18px;
    font-weight: 600;
}
QPushButton:hover { background-color: #3d1416; }
QPushButton:pressed { background-color: #5a1f1f; }
QPushButton:disabled {
    background-color: #21262d;
    color: #484f58;
    border-color: #30363d;
}
"""

CARD_STYLE = (
    "QFrame#controlCard {"
    "  background-color: #161b22;"
    "  border: 1px solid #30363d;"
    "  border-radius: 10px;"
    "}"
)


class BatchProcessingWidget(QWidget):
    """UI do motor de processamento assíncrono.

    Uso típico:

        widget = BatchProcessingWidget(provider=provider)
        widget.set_jobs(jobs, batch_id="lote-1")
        widget.start_batch()  # ou o usuário clica "Iniciar"
    """

    def __init__(
        self,
        provider: ImageGenerationProvider | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        # O provider pode ser setado depois (set_provider) para
        # permitir lazy init vindo da MainWindow.
        self._provider = provider
        self._pool = QThreadPool.globalInstance()

        self._pending_jobs: list[ImageJob] = []
        self._batch_id: str = ""
        self._processor: BatchProcessor | None = None
        self._signals: BatchProcessorSignals | None = None
        # Trabalhamos com `self._signals` separado do worker para que
        # os slots não dependam do QRunnable em si (que some quando
        # o pool termina).

        self._build_ui()
        self._apply_button_state("idle")  # estado inicial

    # ------------------------------------------------------------------ #
    # Configuração                                                        #
    # ------------------------------------------------------------------ #

    def set_provider(self, provider: ImageGenerationProvider | None) -> None:
        """Define o provider (chamado pela MainWindow após init).

        Aceita `None` — usado quando o usuário remove a chave da
        OpenAI. O widget reage desligando o botão Iniciar e
        exibindo a nota informativa.
        """
        self._provider = provider
        # Reavalia habilitação do botão Iniciar.
        self._start_btn.setEnabled(
            bool(self._pending_jobs) and self._provider is not None
        )
        self._refresh_start_hint()

    def set_jobs(self, jobs: Iterable[ImageJob], batch_id: str = "") -> None:
        """Define a lista de jobs a processar.

        Pode ser chamado várias vezes — o widget só usa a lista no
        momento de `start_batch()`. Substitui qualquer fila anterior.
        """
        self._pending_jobs = list(jobs)
        self._batch_id = batch_id
        # Atualiza label de total.
        self._total_label.setText(f"{len(self._pending_jobs)} jobs configurados")
        self._start_btn.setEnabled(bool(self._pending_jobs) and self._provider is not None)
        self._refresh_start_hint()

    # ------------------------------------------------------------------ #
    # API pública — start / pause / resume / cancel                       #
    # ------------------------------------------------------------------ #

    def start_batch(self) -> None:
        """Inicia o processamento do lote atual (não bloqueante)."""
        if self._processor is not None:
            logger.debug("BatchProcessingWidget: já rodando — ignorando start_batch")
            return
        if self._provider is None or not self._pending_jobs:
            return

        processor = BatchProcessor(
            provider=self._provider,
            jobs=self._pending_jobs,
            on_event=self._on_processor_event,
            batch_id=self._batch_id,
        )
        self._processor = processor

        worker = make_batch_processor_worker(processor)
        # Guardamos os signals antes de soltar o worker para o pool.
        self._signals = worker.signals
        self._wire_signals(self._signals)

        # Reset de estado visual.
        self._progress_bar.setValue(0)
        self._current_file_label.setText("(nenhum)")
        self._counters_label.setText(
            self._format_counters(0, 0, 0)
        )

        self._apply_button_state("running")
        self._pool.start(worker)

    def pause_batch(self) -> None:
        if self._processor is not None:
            self._processor.pause()
            self._apply_button_state("pausing")

    def resume_batch(self) -> None:
        if self._processor is not None:
            self._processor.resume()
            self._apply_button_state("running")

    def cancel_batch(self) -> None:
        if self._processor is not None:
            self._processor.cancel()
            # O botão "Cancelar" fica desabilitado — a operação não
            # tem volta dentro desta execução.
            self._cancel_btn.setEnabled(False)

    # ------------------------------------------------------------------ #
    # UI                                                                 #
    # ------------------------------------------------------------------ #

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        title = QLabel("Processamento em lote")
        title.setProperty("heading", True)
        title.setStyleSheet("font-size: 20px; font-weight: 700;")
        layout.addWidget(title)

        # ----- Card 1: status ----------------------------------------- #
        status_card = QFrame()
        status_card.setObjectName("statusCard")
        status_card.setStyleSheet(CARD_STYLE)
        sc_layout = QVBoxLayout(status_card)
        sc_layout.setContentsMargins(16, 16, 16, 16)
        sc_layout.setSpacing(10)

        # Linha 1: total configurado.
        self._total_label = QLabel("0 jobs configurados")
        self._total_label.setProperty("hint", True)
        self._total_label.setStyleSheet(
            "color: #9da7b3; font-size: 12px; font-weight: 600; "
            "text-transform: uppercase; letter-spacing: 0.5px;"
        )
        sc_layout.addWidget(self._total_label)

        # Linha 2: arquivo atual.
        self._current_file_label = QLabel("(nenhum)")
        self._current_file_label.setStyleSheet(
            "color: #e6edf3; font-weight: 600; font-size: 14px;"
        )
        sc_layout.addWidget(self._current_file_label)

        # Linha 3: barra de progresso.
        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(0)
        self._progress_bar.setFormat("%p% (%v / %m)")
        sc_layout.addWidget(self._progress_bar)

        # Linha 4: contadores (em badges).
        self._counters_label = QLabel(self._format_counters(0, 0, 0))
        self._counters_label.setTextFormat(Qt.RichText)
        sc_layout.addWidget(self._counters_label)

        layout.addWidget(status_card)

        # ----- Card 2: controles --------------------------------------- #
        control_card = QFrame()
        control_card.setObjectName("controlCard")
        control_card.setStyleSheet(CARD_STYLE)
        cc_layout = QHBoxLayout(control_card)
        cc_layout.setContentsMargins(16, 16, 16, 16)
        cc_layout.setSpacing(10)

        self._start_btn = QPushButton("▶  Iniciar")
        self._start_btn.setMinimumHeight(38)
        self._start_btn.setStyleSheet(PRIMARY_BTN_STYLE)
        self._start_btn.clicked.connect(self.start_batch)
        cc_layout.addWidget(self._start_btn)

        self._pause_btn = QPushButton("⏸  Pausar")
        self._pause_btn.setMinimumHeight(38)
        self._pause_btn.clicked.connect(self.pause_batch)
        cc_layout.addWidget(self._pause_btn)

        self._resume_btn = QPushButton("▶  Retomar")
        self._resume_btn.setMinimumHeight(38)
        self._resume_btn.clicked.connect(self.resume_batch)
        cc_layout.addWidget(self._resume_btn)

        self._cancel_btn = QPushButton("■  Cancelar")
        self._cancel_btn.setMinimumHeight(38)
        self._cancel_btn.setStyleSheet(DANGER_BTN_STYLE)
        self._cancel_btn.clicked.connect(self.cancel_batch)
        cc_layout.addWidget(self._cancel_btn)

        # Empurra a nota para a direita do botão "Iniciar".
        # Mantida na MESMA linha horizontal para que o vínculo
        # visual entre nota e botão seja óbvio em qualquer SO.
        cc_layout.addStretch(1)

        # Nota informativa: explica por que o botão "Iniciar" pode
        # estar desabilitado. Some quando o botão é habilitado
        # (atualizada em `_refresh_start_hint`).
        self._start_hint = QLabel()
        self._start_hint.setTextFormat(Qt.RichText)
        self._start_hint.setWordWrap(True)
        self._start_hint.setAlignment(
            Qt.AlignRight | Qt.AlignVCenter
        )
        self._start_hint.setStyleSheet(
            "color: #9da7b3; font-size: 12px;"
        )
        self._start_hint.setVisible(False)
        cc_layout.addWidget(self._start_hint)

        layout.addWidget(control_card)
        layout.addStretch(1)

    # ------------------------------------------------------------------ #
    # Formatação de contadores                                            #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _format_counters(success: int, failed: int, cancelled: int) -> str:
        return (
            f"<span style='background-color:#1b3a23; color:#3fb950; "
            f"padding: 4px 10px; border-radius: 9999px; "
            f"font-size: 12px; font-weight: 600;'>"
            f"✓ Sucesso: {success}</span>"
            f"&nbsp;&nbsp;"
            f"<span style='background-color:#3d1416; color:#f85149; "
            f"padding: 4px 10px; border-radius: 9999px; "
            f"font-size: 12px; font-weight: 600;'>"
            f"✗ Falha: {failed}</span>"
            f"&nbsp;&nbsp;"
            f"<span style='background-color:#3d2f0a; color:#d29922; "
            f"padding: 4px 10px; border-radius: 9999px; "
            f"font-size: 12px; font-weight: 600;'>"
            f"⊘ Cancelado: {cancelled}</span>"
        )

    # ------------------------------------------------------------------ #
    # Botões — habilitação conforme estado                                #
    # ------------------------------------------------------------------ #

    def _apply_button_state(self, state: str) -> None:
        """Habilita/desabilita botões conforme o estado.

        Estados:
            idle: nada rodando. Só Iniciar (se há jobs).
            running: lote em andamento. Pausar + Cancelar ativos.
            pausing: pause solicitado. Retomar + Cancelar ativos.
            cancelled/finished: estado terminal. Iniciar desabilitado
                (caller deve criar novo processor).
        """
        if state == "idle":
            has_jobs = bool(self._pending_jobs) and self._provider is not None
            self._start_btn.setEnabled(has_jobs)
            self._pause_btn.setEnabled(False)
            self._resume_btn.setEnabled(False)
            self._cancel_btn.setEnabled(False)
        elif state == "running":
            self._start_btn.setEnabled(False)
            self._pause_btn.setEnabled(True)
            self._resume_btn.setEnabled(False)
            self._cancel_btn.setEnabled(True)
        elif state == "pausing":
            self._start_btn.setEnabled(False)
            self._pause_btn.setEnabled(False)
            self._resume_btn.setEnabled(True)
            self._cancel_btn.setEnabled(True)
        elif state in ("cancelled", "finished"):
            self._start_btn.setEnabled(False)
            self._pause_btn.setEnabled(False)
            self._resume_btn.setEnabled(False)
            self._cancel_btn.setEnabled(False)

        # Atualiza a nota informativa sobre os requisitos.
        self._refresh_start_hint()

    # ------------------------------------------------------------------ #
    # Nota informativa sobre o botão "Iniciar"                            #
    # ------------------------------------------------------------------ #

    def _refresh_start_hint(self) -> None:
        """Mostra/oculta a nota ao lado do botão Iniciar.

        Aparece somente quando o botão está desabilitado por
        FALTA de requisito (sem jobs ou sem provider). Some quando
        o lote está rodando/pausado/cancelado/terminado — nesses
        estados o botão fica desabilitado por outro motivo
        (já existe um processor ativo) e a nota só geraria ruído.
        """
        if self._start_btn.isEnabled():
            self._start_hint.setVisible(False)
            return

        # Só exibimos a nota no estado idle (sem processor ativo).
        processor_active = self._processor is not None
        if processor_active:
            self._start_hint.setVisible(False)
            return

        missing: list[str] = []
        # TODO(V1): checagem de API key desativada — motor agora é
        # automação local. A linha abaixo fica morta na V1 porque
        # ``self._provider`` deixa de ser ``None`` por falta de chave
        # (a ``MainWindow._ensure_image_provider`` agora sempre
        # instancia o ``ChatGPTDesktopAutomationProvider``). Removida
        # definitivamente na V2 junto com o ``CredentialManager``.
        if False and self._provider is None:  # noqa: V1 — desativada
            missing.append(
                "chave da OpenAI configurada (aba Configuração)"
            )
        if not self._pending_jobs:
            missing.append("imagens carregadas (clique em Escanear)")

        if missing:
            bullets = "".join(
                f"<li style='margin:2px 0;'>{item}</li>" for item in missing
            )
            self._start_hint.setText(
                "<span style='color:#d29922;'>ⓘ</span>&nbsp;"
                "Para <b>Iniciar</b> é preciso:<br>"
                f"<ul style='margin:4px 0 0 0; padding-left:18px;'>{bullets}</ul>"
            )
            self._start_hint.setVisible(True)
        else:
            self._start_hint.setVisible(False)

    # ------------------------------------------------------------------ #
    # Signals — religa eventos do processor aos slots                     #
    # ------------------------------------------------------------------ #

    def _wire_signals(self, signals: BatchProcessorSignals) -> None:
        signals.job_started.connect(self._on_job_started)
        signals.job_succeeded.connect(self._on_job_succeeded)
        signals.job_failed.connect(self._on_job_failed)
        signals.progress_updated.connect(self._on_progress_updated)
        signals.batch_paused.connect(self._on_batch_paused)
        signals.batch_resumed.connect(self._on_batch_resumed)
        signals.batch_cancelled.connect(self._on_batch_cancelled)
        signals.batch_completed.connect(self._on_batch_completed)

    # ------------------------------------------------------------------ #
    # Slots — recebidos na thread principal (queued connection automática) #
    # ------------------------------------------------------------------ #

    @Slot()
    def _on_batch_paused(self) -> None:
        self._apply_button_state("pausing")
        self._current_file_label.setText("(pausado)")

    @Slot()
    def _on_batch_resumed(self) -> None:
        self._apply_button_state("running")

    @Slot()
    def _on_batch_cancelled(self) -> None:
        # O lote vai emitir BATCH_COMPLETED logo em seguida; só
        # desabilita o Cancelar aqui (não troca o estado geral
        # ainda).
        self._cancel_btn.setEnabled(False)

    @Slot()
    def _on_batch_completed(self) -> None:
        # Limpa referências: o processor não pode ser reutilizado
        # depois de terminar.
        self._processor = None
        self._signals = None
        self._apply_button_state("finished")

    @Slot(object)
    def _on_job_started(self, job: ImageJob) -> None:
        name = Path(job.reference_image_path).name
        self._current_file_label.setText(f"⚙  Processando: {name}")

    @Slot(object)
    def _on_job_succeeded(self, job: ImageJob) -> None:
        # O contador agregado vem via progress_updated, mas
        # atualizamos o arquivo atual aqui.
        name = Path(job.reference_image_path).name
        self._current_file_label.setText(f"✓  Concluído: {name}")

    @Slot(object, str)
    def _on_job_failed(self, job: ImageJob, error_message: str) -> None:
        # Marca como última falha; o arquivo atual volta a ser
        # atualizado pelo próximo job_started.
        logger.warning("Falha em %s: %s", job.id, error_message)
        # Não alteramos _current_file_label — o próximo
        # JOB_STARTED sobrescreve.

    @Slot(object)
    def _on_progress_updated(self, snapshot) -> None:  # type: ignore[no-untyped-def]
        # Atualiza barra e contadores.
        self._progress_bar.setValue(snapshot.percent)
        self._counters_label.setText(
            self._format_counters(snapshot.success, snapshot.failed, snapshot.cancelled)
        )

    # ------------------------------------------------------------------ #
    # Callback direto do BatchProcessor (mesma thread do loop)            #
    # ------------------------------------------------------------------ #

    def _on_processor_event(self, event) -> None:  # type: ignore[no-untyped-def]
        """Callback passado ao BatchProcessor — disparado NA thread do
        loop, NÃO na thread da UI.

        Aqui só logamos (a UI é atualizada via signals Qt). Mantemos
        o callback vivo porque o BatchProcessor obriga `on_event`
        não-None."""
        logger.debug("BatchProcessor event: %s", event.kind)


__all__ = ["BatchProcessingWidget"]