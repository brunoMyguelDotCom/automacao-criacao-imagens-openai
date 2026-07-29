"""Widget "Status Geral" do dashboard (Prompt 9).

Exibe o `DashboardSnapshot` produzido pelo `DashboardService`.
A UI NÃO faz cálculo de agregação — apenas reflete os modelos
imutáveis que o serviço devolve (regra principal do prompt).

Seções:
    1. Resumo consolidado (cards com TOTAL/GERADAS/FALTAM/FALHARAM/
       PROCESSANDO/CANCELADAS).
    2. Botão "Atualizar status" + seletor de projeto.
    3. Tabela de lotes (QTableWidget) com colunas:
       nome, total, sucesso, falhas, pendentes, % concluído, status.
       Duplo clique → sinal `batch_double_clicked(batch_id)` que a
       MainWindow intercepta para navegar ao BatchProcessingWidget.
    4. Histórico de gerações com filtro (ComboBox) + tabela + detalhes
       do job selecionado + botões de ação (Tentar novamente /
       Abrir arquivo / Abrir pasta).

Threading:
    * Construção e updates rodam na thread da UI.
    * Refresh do snapshot (`Atualizar status`) é síncrono (SQLite é
      local) — não precisa de QRunnable. Para volumes muito grandes
      de histórico (>10k jobs) a otimização viria depois, com
      worker QRunnable.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from app.core.models import BatchStatus, ImageJobStatus, Project
from app.core.services import (
    HISTORY_FILTERABLE_STATUSES,
    BatchSummary,
    DashboardService,
    DashboardSnapshot,
    DashboardSummary,
    JobHistoryEntry,
)

logger = logging.getLogger(__name__)


# Mapeamento "label legível" → valor estável (enum value).
HISTORY_FILTER_LABELS: list[tuple[str, Optional[str]]] = [
    ("Todos", None),
    ("Sucesso", ImageJobStatus.SUCCESS.value),
    ("Falha", ImageJobStatus.FAILED.value),
    ("Pendente", ImageJobStatus.PENDING.value),
]


class DashboardWidget(QWidget):
    """UI do dashboard consolidado.

    Sinais:
        batch_double_clicked(str): emitido quando o usuário dá duplo
            clique numa linha de lote. `str` é o `batch.id`. A
            `MainWindow` consome para navegar ao
            `BatchProcessingWidget`.
        refresh_requested(): emitido quando o usuário clica em
            "Atualizar status". A MainWindow pode (opcionalmente)
            usá-lo para invalidar caches antes do refresh, ou pode
            simplesmente chamar `refresh()` direto.
    """

    batch_double_clicked = Signal(str)
    refresh_requested = Signal()

    def __init__(
        self,
        dashboard_service: DashboardService,
        projects: list[Project],
        *,
        current_project_id: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._service = dashboard_service
        self._projects = list(projects)
        self._current_project_id = current_project_id or (
            self._projects[0].id if self._projects else ""
        )
        self._snapshot: Optional[DashboardSnapshot] = None
        self._history_rows: list[JobHistoryEntry] = []
        self._batch_rows: list[BatchSummary] = []

        self._build_ui()
        if self._current_project_id:
            self.refresh()

    # ------------------------------------------------------------------ #
    # API pública                                                         #
    # ------------------------------------------------------------------ #

    def set_projects(self, projects: list[Project]) -> None:
        """Atualiza a lista de projetos disponíveis no seletor."""
        self._projects = list(projects)
        # Repopula o ComboBox mantendo a seleção atual se possível.
        current_text = self._project_combo.currentText()
        self._project_combo.blockSignals(True)
        self._project_combo.clear()
        for proj in self._projects:
            self._project_combo.addItem(proj.name, proj.id)
        self._project_combo.blockSignals(False)
        # Restaura seleção: pelo id anterior, ou pelo texto, ou pelo primeiro.
        if self._current_project_id:
            idx = self._project_combo.findData(self._current_project_id)
            if idx >= 0:
                self._project_combo.setCurrentIndex(idx)
        elif current_text:
            idx = self._project_combo.findText(current_text)
            if idx >= 0:
                self._project_combo.setCurrentIndex(idx)
        if self._current_project_id:
            self.refresh()

    def set_current_project(self, project_id: str) -> None:
        """Troca o projeto atual e re-renderiza."""
        if project_id == self._current_project_id:
            return
        self._current_project_id = project_id
        idx = self._project_combo.findData(project_id)
        if idx >= 0:
            self._project_combo.blockSignals(True)
            self._project_combo.setCurrentIndex(idx)
            self._project_combo.blockSignals(False)
        self.refresh()

    def refresh(self) -> None:
        """Relê o banco e atualiza TODA a UI."""
        if not self._current_project_id:
            self._render_empty()
            return
        try:
            history_filter = self._current_filter_value()
            snap = self._service.get_snapshot(
                self._current_project_id,
                history_filter=history_filter,
            )
        except Exception:  # noqa: BLE001
            logger.exception(
                "DashboardWidget: falha ao carregar snapshot do projeto %s",
                self._current_project_id,
            )
            self._render_empty()
            return
        self._snapshot = snap
        self._batch_rows = list(snap.batches)
        self._history_rows = list(snap.history)
        self._render_summary(snap.summary)
        self._render_batches(snap.batches)
        self._render_history(snap.history)
        self._render_details(None)

    # ------------------------------------------------------------------ #
    # UI                                                                 #
    # ------------------------------------------------------------------ #

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        # Header: seletor de projeto + botão atualizar.
        header = QHBoxLayout()
        title = QLabel("Status Geral do Projeto")
        title.setProperty("heading", True)
        title.setStyleSheet("font-size: 20px; font-weight: 700;")
        header.addWidget(title)
        header.addStretch(1)

        header.addWidget(QLabel("Projeto:"))
        self._project_combo = QComboBox()
        for proj in self._projects:
            self._project_combo.addItem(proj.name, proj.id)
        self._project_combo.currentIndexChanged.connect(self._on_project_changed)
        header.addWidget(self._project_combo)

        self._refresh_btn = QPushButton("🔄  Atualizar status")
        self._refresh_btn.setMinimumHeight(36)
        self._refresh_btn.clicked.connect(self._on_refresh_clicked)
        header.addWidget(self._refresh_btn)
        layout.addLayout(header)

        # Seção 1: resumo consolidado.
        layout.addWidget(self._build_summary_group())

        # Seção 2: tabela de lotes.
        layout.addWidget(self._build_batches_group(), 1)

        # Seção 3: histórico de gerações.
        layout.addWidget(self._build_history_group(), 1)

    def _build_summary_group(self) -> QGroupBox:
        group = QGroupBox("Resumo consolidado")
        outer = QVBoxLayout(group)
        grid = QHBoxLayout()

        self._total_card = self._make_card("TOTAL")
        self._success_card = self._make_card("GERADAS")
        self._pending_card = self._make_card("FALTAM")
        self._failed_card = self._make_card("FALHARAM")
        self._processing_card = self._make_card("EM PROCESSAMENTO")
        self._cancelled_card = self._make_card("CANCELADAS")

        for card in (
            self._total_card,
            self._success_card,
            self._pending_card,
            self._failed_card,
            self._processing_card,
            self._cancelled_card,
        ):
            grid.addWidget(card)

        outer.addLayout(grid)
        self._percent_label = QLabel("")
        self._percent_label.setProperty("hint", True)
        outer.addWidget(self._percent_label)
        return group

    def _build_batches_group(self) -> QGroupBox:
        group = QGroupBox("Lotes do projeto (clique duplo para abrir)")
        outer = QVBoxLayout(group)

        self._batches_table = QTableWidget(0, 7)
        self._batches_table.setHorizontalHeaderLabels(
            ["Lote", "Total", "Sucesso", "Falhas", "Pendentes", "% Concluído", "Status"]
        )
        self._batches_table.verticalHeader().setVisible(False)
        self._batches_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._batches_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._batches_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self._batches_table.setAlternatingRowColors(True)
        header = self._batches_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        for col in range(1, 7):
            header.setSectionResizeMode(col, QHeaderView.ResizeToContents)
        self._batches_table.cellDoubleClicked.connect(self._on_batch_double_clicked)
        outer.addWidget(self._batches_table)
        return group

    def _build_history_group(self) -> QGroupBox:
        group = QGroupBox("Histórico de gerações")
        outer = QVBoxLayout(group)

        # Linha de filtro.
        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("Filtrar por status:"))
        self._history_filter_combo = QComboBox()
        for label, value in HISTORY_FILTER_LABELS:
            self._history_filter_combo.addItem(label, value)
        self._history_filter_combo.currentIndexChanged.connect(self._on_history_filter_changed)
        filter_row.addWidget(self._history_filter_combo)
        filter_row.addStretch(1)
        outer.addLayout(filter_row)

        # Tabela.
        self._history_table = QTableWidget(0, 5)
        self._history_table.setHorizontalHeaderLabels(
            ["Arquivo de entrada", "Status", "Saída", "Tentativas", "Última atividade"]
        )
        self._history_table.verticalHeader().setVisible(False)
        self._history_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._history_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._history_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self._history_table.setAlternatingRowColors(True)
        h2 = self._history_table.horizontalHeader()
        h2.setSectionResizeMode(0, QHeaderView.Stretch)
        for col in range(1, 5):
            h2.setSectionResizeMode(col, QHeaderView.ResizeToContents)
        self._history_table.itemSelectionChanged.connect(self._on_history_selection_changed)
        outer.addWidget(self._history_table)

        # Detalhes + botões de ação.
        details_row = QHBoxLayout()
        details_left = QVBoxLayout()
        self._details_view = QTextEdit()
        self._details_view.setReadOnly(True)
        self._details_view.setMaximumHeight(120)
        self._details_view.setPlaceholderText(
            "Selecione uma entrada do histórico para ver os detalhes."
        )
        details_left.addWidget(self._details_view)
        details_row.addLayout(details_left, 1)

        action_box = QFormLayout()
        action_box.setSpacing(8)
        self._retry_btn = QPushButton("🔁  Tentar novamente")
        self._retry_btn.setMinimumHeight(32)
        self._retry_btn.setEnabled(False)
        self._retry_btn.clicked.connect(self._on_retry_clicked)
        action_box.addRow(self._retry_btn)

        self._open_output_btn = QPushButton("🖼  Abrir arquivo gerado")
        self._open_output_btn.setMinimumHeight(32)
        self._open_output_btn.setEnabled(False)
        self._open_output_btn.clicked.connect(self._on_open_output_clicked)
        action_box.addRow(self._open_output_btn)

        self._open_outdir_btn = QPushButton("📂  Abrir pasta de saída")
        self._open_outdir_btn.setMinimumHeight(32)
        self._open_outdir_btn.setEnabled(False)
        self._open_outdir_btn.clicked.connect(self._on_open_outdir_clicked)
        action_box.addRow(self._open_outdir_btn)

        self._open_indir_btn = QPushButton("📁  Abrir pasta de entrada")
        self._open_indir_btn.setMinimumHeight(32)
        self._open_indir_btn.setEnabled(False)
        self._open_indir_btn.clicked.connect(self._on_open_indir_clicked)
        action_box.addRow(self._open_indir_btn)
        details_row.addLayout(action_box)
        outer.addLayout(details_row)
        return group

    # ------------------------------------------------------------------ #
    # Render helpers                                                       #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _make_card(label: str) -> QGroupBox:
        box = QGroupBox(label)
        v = QVBoxLayout(box)
        v.setContentsMargins(12, 12, 12, 12)
        number = QLabel("0")
        number.setStyleSheet(
            "font-size: 28px; font-weight: 700; color: #58a6ff;"
        )
        number.setAlignment(Qt.AlignCenter)
        v.addWidget(number)
        box.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        # Anexa o label do número como atributo acessível.
        box._value_label = number  # type: ignore[attr-defined]
        return box

    @staticmethod
    def _set_card(box: QGroupBox, value: int) -> None:
        box._value_label.setText(str(value))  # type: ignore[attr-defined]

    def _render_summary(self, s: DashboardSummary) -> None:
        self._set_card(self._total_card, s.total)
        self._set_card(self._success_card, s.success)
        self._set_card(self._pending_card, s.pending)
        self._set_card(self._failed_card, s.failed)
        self._set_card(self._processing_card, s.processing)
        self._set_card(self._cancelled_card, s.cancelled)
        self._percent_label.setText(
            f"Concluído: {s.percent_complete}% (somatório de sucesso + falha + cancelado)"
        )

    def _render_batches(self, batches: list[BatchSummary]) -> None:
        self._batches_table.setRowCount(len(batches))
        for row, b in enumerate(batches):
            cells = [
                b.batch.name,
                str(b.total),
                str(b.success),
                str(b.failed),
                str(b.pending),
                f"{b.percent_complete}%",
                _batch_status_label(b.batch.status),
            ]
            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if col > 0:
                    item.setTextAlignment(Qt.AlignCenter)
                self._batches_table.setItem(row, col, item)
        # Persiste o batch_id na linha (UserRole) para o slot de double-click.
        for row, b in enumerate(batches):
            id_item = self._batches_table.item(row, 0)
            if id_item is not None:
                id_item.setData(Qt.UserRole, b.batch.id)

    def _render_history(self, entries: list[JobHistoryEntry]) -> None:
        self._history_table.setRowCount(len(entries))
        for row, e in enumerate(entries):
            file_name = Path(e.job.reference_image_path).name
            status_label = _job_status_label(e.job.status)
            output_txt = (
                "disponível"
                if e.output_available
                else ("ausente" if e.job.status is ImageJobStatus.SUCCESS else "—")
            )
            attempts_txt = (
                f"{e.attempt_count}" if e.attempt_count else "—"
            )
            last_txt = (
                e.last_attempt_at.strftime("%Y-%m-%d %H:%M:%S")
                if e.last_attempt_at
                else "—"
            )
            cells = [file_name, status_label, output_txt, attempts_txt, last_txt]
            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if col > 0:
                    item.setTextAlignment(Qt.AlignCenter)
                self._history_table.setItem(row, col, item)
            # Persiste o job_id (UserRole) e o índice (entrada completa)
            # para os slots de detalhe.
            id_item = self._history_table.item(row, 0)
            if id_item is not None:
                id_item.setData(Qt.UserRole, e.job.id)

    def _render_empty(self) -> None:
        """Estado vazio: nenhum projeto selecionado ou falha no refresh."""
        self._snapshot = None
        self._batch_rows = []
        self._history_rows = []
        for card in (
            self._total_card,
            self._success_card,
            self._pending_card,
            self._failed_card,
            self._processing_card,
            self._cancelled_card,
        ):
            self._set_card(card, 0)
        self._percent_label.setText("")
        self._batches_table.setRowCount(0)
        self._history_table.setRowCount(0)
        self._details_view.clear()

    # ------------------------------------------------------------------ #
    # Filtro                                                               #
    # ------------------------------------------------------------------ #

    def _current_filter_value(self) -> Optional[str]:
        return self._history_filter_combo.currentData()

    @Slot(int)
    def _on_history_filter_changed(self, _idx: int) -> None:
        # Reexecuta o snapshot com o novo filtro.
        if self._current_project_id:
            self.refresh()

    @Slot(int)
    def _on_project_changed(self, _idx: int) -> None:
        new_id = self._project_combo.currentData()
        if new_id and new_id != self._current_project_id:
            self._current_project_id = new_id
            self.refresh()

    @Slot()
    def _on_refresh_clicked(self) -> None:
        self.refresh_requested.emit()
        self.refresh()

    # ------------------------------------------------------------------ #
    # Slots de interação                                                   #
    # ------------------------------------------------------------------ #

    @Slot(int, int)
    def _on_batch_double_clicked(self, row: int, _col: int) -> None:
        item = self._batches_table.item(row, 0)
        if item is None:
            return
        batch_id = item.data(Qt.UserRole)
        if batch_id:
            self.batch_double_clicked.emit(str(batch_id))

    @Slot()
    def _on_history_selection_changed(self) -> None:
        rows = self._history_table.selectionModel().selectedRows()
        if not rows:
            self._render_details(None)
            return
        row_idx = rows[0].row()
        if row_idx < 0 or row_idx >= len(self._history_rows):
            self._render_details(None)
            return
        self._render_details(self._history_rows[row_idx])

    def _render_details(self, entry: Optional[JobHistoryEntry]) -> None:
        if entry is None:
            self._details_view.clear()
            self._retry_btn.setEnabled(False)
            self._open_output_btn.setEnabled(False)
            self._open_outdir_btn.setEnabled(False)
            self._open_indir_btn.setEnabled(False)
            return

        job = entry.job
        availability = "disponível" if entry.output_available else "AUSENTE"
        if job.status is not ImageJobStatus.SUCCESS:
            availability = "—"
        last = (
            entry.last_attempt_at.strftime("%Y-%m-%d %H:%M:%S")
            if entry.last_attempt_at
            else "—"
        )
        detail = (
            f"Arquivo de origem: {job.reference_image_path}\n"
            f"Arquivo de saída: {job.output_path} ({availability})\n"
            f"Status: {_job_status_label(job.status)}\n"
            f"Tentativas: {entry.attempt_count}\n"
            f"Última atividade: {last}\n"
        )
        if job.last_error_code or job.last_error_message:
            detail += (
                f"Erro: {job.last_error_code or '—'} — {job.last_error_message or '—'}\n"
            )
        self._details_view.setPlainText(detail)

        # Habilita botões conforme contexto.
        self._retry_btn.setEnabled(job.status is ImageJobStatus.FAILED)
        self._open_output_btn.setEnabled(
            job.status is ImageJobStatus.SUCCESS and entry.output_available
        )
        self._open_outdir_btn.setEnabled(True)  # sempre habilitado se há entry
        self._open_indir_btn.setEnabled(True)

    @Slot()
    def _on_retry_clicked(self) -> None:
        rows = self._history_table.selectionModel().selectedRows()
        if not rows:
            return
        row_idx = rows[0].row()
        if row_idx < 0 or row_idx >= len(self._history_rows):
            return
        entry = self._history_rows[row_idx]
        if entry.job.status is not ImageJobStatus.FAILED:
            return
        confirm = QMessageBox.question(
            self,
            "Confirmar nova tentativa",
            "Esta ação vai gerar uma NOVA chamada à API para esse job. "
            "O histórico de tentativas anteriores é preservado. Continuar?",
        )
        if confirm != QMessageBox.Yes:
            return
        try:
            self._service.retry_failed_job(entry.job.id)
        except Exception:  # noqa: BLE001
            logger.exception("Falha ao resetar job %s", entry.job.id)
            QMessageBox.warning(self, "Erro", "Não foi possível resetar o job.")
            return
        QMessageBox.information(
            self,
            "Job resetado",
            "O job voltou para PENDING. Inicie um lote para reprocessá-lo.",
        )
        self.refresh()

    # ------------------------------------------------------------------ #
    # Abertura de arquivos/pastas (cross-platform)                          #
    # ------------------------------------------------------------------ #

    @Slot()
    def _on_open_output_clicked(self) -> None:
        rows = self._history_table.selectionModel().selectedRows()
        if not rows:
            return
        entry = self._history_rows[rows[0].row()]
        self._open_path(Path(entry.job.output_path))

    @Slot()
    def _on_open_outdir_clicked(self) -> None:
        rows = self._history_table.selectionModel().selectedRows()
        if not rows:
            return
        entry = self._history_rows[rows[0].row()]
        self._open_path(Path(entry.job.output_path).parent, is_dir=True)

    @Slot()
    def _on_open_indir_clicked(self) -> None:
        rows = self._history_table.selectionModel().selectedRows()
        if not rows:
            return
        entry = self._history_rows[rows[0].row()]
        self._open_path(Path(entry.job.reference_image_path).parent, is_dir=True)

    @staticmethod
    def _open_path(path: Path, *, is_dir: bool = False) -> None:
        """Abre arquivo OU pasta usando o app nativo do SO.

        Reusa helper compartilhado em `app.ui.paths`.
        """
        from app.ui.paths import open_path_in_shell

        open_path_in_shell(path)


# --------------------------------------------------------------------------- #
# Helpers de rótulo                                                             #
# --------------------------------------------------------------------------- #


def _job_status_label(status: ImageJobStatus) -> str:
    return {
        ImageJobStatus.PENDING: "Pendente",
        ImageJobStatus.PROCESSING: "Processando",
        ImageJobStatus.SUCCESS: "Sucesso",
        ImageJobStatus.FAILED: "Falha",
        ImageJobStatus.PAUSED: "Pausado",
        ImageJobStatus.CANCELLED: "Cancelado",
    }.get(status, status.value)


def _batch_status_label(status: BatchStatus) -> str:
    return {
        BatchStatus.NOT_STARTED: "Não iniciado",
        BatchStatus.IN_PROGRESS: "Em andamento",
        BatchStatus.PAUSED: "Pausado",
        BatchStatus.COMPLETED: "Concluído",
        BatchStatus.COMPLETED_WITH_ERRORS: "Concluído com erros",
        BatchStatus.CANCELLED: "Cancelado",
    }.get(status, status.value)


__all__ = ["DashboardWidget", "HISTORY_FILTER_LABELS"]
