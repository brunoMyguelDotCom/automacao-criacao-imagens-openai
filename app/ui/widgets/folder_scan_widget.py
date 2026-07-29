"""Aba "Processamento": seleção de pasta de entrada + execução do scan.

Aqui mora apenas a integração com `QFileDialog` e a exibição do
resultado. Toda a lógica de classificação/hash está em
`app.core.services.image_folder_scanner`. O scan roda num
`ScanWorker` (QRunnable) para não bloquear a UI mesmo em pastas
grandes.

O visual é controlado por `app/ui/theme.py` — esta janela não
declarar nenhum `setStyleSheet` próprio: herda o tema escuro
global.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import Qt, QThreadPool, Slot
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from app.core.models import ImageStatus, ScanResult
from app.core.services import ImageFolderScanner
from app.ui.workers.scan_worker import ScanWorker

logger = logging.getLogger(__name__)


class FolderScanWidget(QWidget):
    """Widget que permite escolher uma pasta e exibe o resultado do scan."""

    def __init__(self, scanner: ImageFolderScanner | None = None) -> None:
        super().__init__()
        self._scanner = scanner or ImageFolderScanner()
        self._pool = QThreadPool.globalInstance()
        self._current_worker: ScanWorker | None = None

        self._build_ui()

    # ------------------------------------------------------------------ #
    # UI                                                                 #
    # ------------------------------------------------------------------ #

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        title = QLabel("Processamento")
        title.setProperty("heading", True)
        title.setStyleSheet("font-size: 20px; font-weight: 700;")
        layout.addWidget(title)

        # Card com a seleção de pasta — visual destacado, com fundo
        # um nível acima do base.
        folder_card = QFrame()
        folder_card.setObjectName("folderCard")
        folder_card.setStyleSheet(
            f"QFrame#folderCard {{"
            f"  background-color: #161b22;"
            f"  border: 1px solid #30363d;"
            f"  border-radius: 10px;"
            f"}}"
        )
        card_layout = QVBoxLayout(folder_card)
        card_layout.setContentsMargins(16, 16, 16, 16)
        card_layout.setSpacing(12)

        folder_caption = QLabel("Pasta de imagens de entrada")
        folder_caption.setProperty("hint", True)
        folder_caption.setStyleSheet(
            "color: #9da7b3; font-size: 12px; font-weight: 600; "
            "text-transform: uppercase; letter-spacing: 0.5px;"
        )
        card_layout.addWidget(folder_caption)

        # Linha com campo de pasta + botão "Selecionar…"
        row = QHBoxLayout()
        row.setSpacing(8)
        self._folder_label = QLabel("(nenhuma pasta selecionada)")
        self._folder_label.setProperty("hint", True)
        self._folder_label.setStyleSheet("color: #6e7681; font-style: italic;")
        self._folder_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        row.addWidget(self._folder_label, 1)

        self._select_btn = QPushButton("📁  Selecionar pasta…")
        self._select_btn.setMinimumHeight(36)
        self._select_btn.clicked.connect(self._on_select_folder)
        row.addWidget(self._select_btn)

        self._scan_btn = QPushButton("▶  Escanear")
        self._scan_btn.setMinimumHeight(36)
        self._scan_btn.setEnabled(False)
        # Botão primário (destacado) quando há pasta selecionada.
        self._scan_btn.setProperty("class", "primary")
        self._scan_btn.setStyleSheet(
            f"QPushButton {{"
            f"  background-color: #4493f8;"
            f"  color: white;"
            f"  border: 1px solid #4493f8;"
            f"  border-radius: 6px;"
            f"  padding: 8px 16px;"
            f"  font-weight: 600;"
            f"}}"
            f"QPushButton:hover {{ background-color: #58a6ff; border-color: #58a6ff; }}"
            f"QPushButton:pressed {{ background-color: #2f7be0; }}"
            f"QPushButton:disabled {{"
            f"  background-color: #21262d;"
            f"  color: #484f58;"
            f"  border-color: #30363d;"
            f"}}"
        )
        self._scan_btn.clicked.connect(self._on_scan_clicked)
        row.addWidget(self._scan_btn)
        card_layout.addLayout(row)
        layout.addWidget(folder_card)

        # Card de resumo (resultado do scan).
        self._summary_label = QLabel("")
        self._summary_label.setWordWrap(True)
        self._summary_label.setTextFormat(Qt.RichText)
        self._summary_label.setStyleSheet(
            f"background-color: #161b22;"
            f"border: 1px solid #30363d;"
            f"border-radius: 10px;"
            f"padding: 14px 16px;"
            f"color: #e6edf3;"
        )
        layout.addWidget(self._summary_label)

        # Detalhamento dos inválidos (mantém estilo nativo, já tem tema).
        self._details_view = QTextEdit()
        self._details_view.setReadOnly(True)
        self._details_view.setPlaceholderText(
            "Selecione uma pasta de imagens e clique em \"Escanear\" "
            "para ver o resultado aqui."
        )
        layout.addWidget(self._details_view, 1)

    # ------------------------------------------------------------------ #
    # Slots                                                              #
    # ------------------------------------------------------------------ #

    @Slot()
    def _on_select_folder(self) -> None:
        # `QFileDialog.getExistingDirectory` usa o diretório inicial
        # do SO. Não fixamos — o usuário escolhe.
        folder = QFileDialog.getExistingDirectory(
            self,
            "Selecione a pasta com as imagens de entrada",
        )
        if not folder:
            return  # usuário cancelou
        self._selected_folder = Path(folder)
        self._folder_label.setText(str(self._selected_folder))
        self._folder_label.setStyleSheet("color: #e6edf3; font-style: normal;")
        self._scan_btn.setEnabled(True)
        self._summary_label.clear()
        self._details_view.clear()

    @Slot()
    def _on_scan_clicked(self) -> None:
        if not hasattr(self, "_selected_folder") or self._selected_folder is None:
            return

        folder = self._selected_folder
        # UI: desabilita enquanto roda
        self._select_btn.setEnabled(False)
        self._scan_btn.setEnabled(False)
        self._summary_label.setText(
            "<span style='color:#9da7b3;'>⏳ Escaneando…</span>"
        )
        self._details_view.clear()

        worker = ScanWorker(folder, scanner=self._scanner)
        worker.signals.result.connect(self._on_scan_result)
        worker.signals.error.connect(self._on_scan_error)
        worker.signals.finished.connect(self._on_scan_finished)
        self._current_worker = worker
        self._pool.start(worker)

    @Slot(object)
    def _on_scan_result(self, result: ScanResult) -> None:
        self._render_result(result)

    @Slot(object)
    def _on_scan_error(self, exc: Exception) -> None:
        logger.warning("Erro inesperado no scan: %s", exc)
        self._summary_label.setText(
            f"<span style='color:#f85149; font-weight:600;'>"
            f"✗ Erro inesperado: {exc.__class__.__name__}</span>"
        )

    @Slot()
    def _on_scan_finished(self) -> None:
        self._select_btn.setEnabled(True)
        self._scan_btn.setEnabled(True)
        self._current_worker = None

    # ------------------------------------------------------------------ #
    # Render                                                             #
    # ------------------------------------------------------------------ #

    def _render_result(self, result: ScanResult) -> None:
        if not result.folder_exists:
            self._summary_label.setText(
                f"<span style='color:#f85149; font-weight:600;'>"
                f"✗ A pasta {result.folder} não existe mais ou está "
                f"inacessível.</span>"
            )
            return

        total = result.total
        valid = result.total_valid
        invalid = result.total_invalid

        # Linha-resumo como badges coloridas.
        parts = [
            "<div style='line-height:1.7'>",
            f"<div style='font-size:14px; font-weight:600; "
            f"color:#e6edf3; margin-bottom:8px;'>📂 {result.folder}</div>",
            _badge(f"{total} encontrados", "#1f3a5f", "#58a6ff"),
            "&nbsp;&nbsp;",
            _badge(f"{valid} válidos", "#1b3a23", "#3fb950"),
            "&nbsp;&nbsp;",
            _badge(f"{invalid} inválidos", "#3d1416", "#f85149"),
            "</div>",
        ]
        if result.subfolders_ignored:
            parts.append(
                f"<div style='margin-top:6px; color:#9da7b3; font-size:12px;'>"
                f"Subpastas ignoradas: {result.subfolders_ignored}</div>"
            )
        self._summary_label.setText("".join(parts))

        # Detalhamento dos inválidos por motivo + extensões dos válidos
        lines: list[str] = []
        if invalid:
            counts = result.count_by_status()
            bits = []
            if counts[ImageStatus.UNSUPPORTED_EXTENSION]:
                bits.append(
                    f"{counts[ImageStatus.UNSUPPORTED_EXTENSION]} com extensão não suportada"
                )
            if counts[ImageStatus.CORRUPTED]:
                bits.append(f"{counts[ImageStatus.CORRUPTED]} corrompido(s)")
            if counts[ImageStatus.UNREADABLE]:
                bits.append(f"{counts[ImageStatus.UNREADABLE]} ilegível(is)")
            if counts[ImageStatus.PERMISSION_ERROR]:
                bits.append(
                    f"{counts[ImageStatus.PERMISSION_ERROR]} sem permissão"
                )
            lines.append("<b>Inválidos por motivo:</b> " + ", ".join(bits) + ".")

            # Lista nominal dos inválidos (até 50 para não poluir)
            invalid_files = [f for f in result.files if f.status is not ImageStatus.VALID]
            if invalid_files:
                lines.append("<br><b>Arquivos inválidos:</b>")
                for f in invalid_files[:50]:
                    reason = f.error_reason or ""
                    lines.append(
                        f"&nbsp;&nbsp;• {f.name} "
                        f"<span style='color:#9da7b3'>({reason})</span>"
                    )
                if len(invalid_files) > 50:
                    lines.append(
                        f"&nbsp;&nbsp;… e mais {len(invalid_files) - 50}."
                    )
        else:
            lines.append("<b style='color:#3fb950;'>✓ Todos os arquivos são válidos.</b>")

        ext_counts = result.count_by_extension_valid()
        if ext_counts:
            bits = ", ".join(
                f"{count}× {ext}" for ext, count in sorted(ext_counts.items())
            )
            lines.append(f"<br><b>Válidos por extensão:</b> {bits}.")

        self._details_view.setHtml("<br>".join(lines))


def _badge(text: str, bg: str, fg: str) -> str:
    """Renderiza um badge inline com HTML."""
    return (
        f"<span style='background-color:{bg}; color:{fg}; "
        f"padding: 4px 10px; border-radius: 9999px; "
        f"font-size: 12px; font-weight: 600; display: inline-block;'>"
        f"{text}</span>"
    )


__all__ = ["FolderScanWidget"]