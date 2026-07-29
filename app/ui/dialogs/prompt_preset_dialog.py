"""Diálogo de gerenciamento de presets de prompt (Prompt 4).

Consome `PromptPresetStore`. Não toca SQLite diretamente: tudo
passa pela store.

Operações: criar, editar, duplicar, excluir (com confirmação,
impedindo o último), ativar (marcar como default), restaurar
o preset de fábrica.

Aviso visível (regra 9): o diálogo deixa claro que o prompt
é uma instrução textual e o resultado é uma interpretação
variável — sem prometer preservação de cor/textura.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from app.core.exceptions import AppError
from app.core.models import PromptPreset
from app.data.storage import PromptPresetStore

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PresetDialogResult:
    """Resultado da interação do usuário."""

    selected_preset_id: str | None
    default_changed: bool


class PromptPresetDialog(QDialog):
    """Diálogo de edição de presets."""

    _DEFAULT_TITLE = "Presets de prompt"

    def __init__(
        self,
        store: PromptPresetStore,
        initial_selected: str | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._store = store
        self._initial_selected = initial_selected
        self._default_changed = False

        self.setWindowTitle(self._DEFAULT_TITLE)
        self.setModal(True)
        self.resize(900, 600)

        # Garante que sempre exista um preset (idempotente).
        self._store.ensure_default()

        self._build_ui()
        self._refresh_list()

    # ------------------------------------------------------------------ #
    # UI                                                                 #
    # ------------------------------------------------------------------ #

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        # Aviso persistente (regra 9 do prompt) — badge amarelo no
        # tema escuro.
        warning = QLabel(
            "⚠ O prompt é uma instrução textual — o resultado da geração "
            "é uma interpretação da imagem de referência e não preserva "
            "exatamente cor, textura ou proporções. Pequenas variações "
            "são esperadas."
        )
        warning.setWordWrap(True)
        warning.setStyleSheet(
            "color: #d29922; background: #3d2f0a; "
            "border: 1px solid #5c4a18; padding: 10px 12px; "
            "border-radius: 6px;"
        )
        layout.addWidget(warning)

        splitter = QSplitter(Qt.Horizontal)
        splitter.setHandleWidth(1)

        # Coluna esquerda: lista
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(8)

        self._list = QListWidget()
        self._list.currentItemChanged.connect(self._on_selection_changed)
        left_layout.addWidget(self._list, 1)

        left_buttons = QHBoxLayout()
        left_buttons.setSpacing(6)
        self._new_btn = QPushButton("＋  Novo")
        self._new_btn.setMinimumHeight(32)
        self._new_btn.clicked.connect(self._on_new)
        left_buttons.addWidget(self._new_btn)

        self._dup_btn = QPushButton("⎘  Duplicar")
        self._dup_btn.setMinimumHeight(32)
        self._dup_btn.clicked.connect(self._on_duplicate)
        left_buttons.addWidget(self._dup_btn)

        self._del_btn = QPushButton("🗑  Excluir")
        self._del_btn.setMinimumHeight(32)
        self._del_btn.setStyleSheet(
            "QPushButton { color: #f85149; border: 1px solid #f85149; }"
            "QPushButton:hover { background-color: #3d1416; }"
            "QPushButton:disabled { color: #484f58; border-color: #30363d; }"
        )
        self._del_btn.clicked.connect(self._on_delete)
        left_buttons.addWidget(self._del_btn)
        left_layout.addLayout(left_buttons)

        self._activate_btn = QPushButton("★  Marcar como ativo")
        self._activate_btn.setMinimumHeight(34)
        self._activate_btn.setStyleSheet(
            "QPushButton {"
            "  background-color: #4493f8;"
            "  color: white;"
            "  border: 1px solid #4493f8;"
            "  font-weight: 600;"
            "}"
            "QPushButton:hover { background-color: #58a6ff; border-color: #58a6ff; }"
            "QPushButton:pressed { background-color: #2f7be0; }"
            "QPushButton:disabled {"
            "  background-color: #21262d;"
            "  color: #484f58;"
            "  border-color: #30363d;"
            "}"
        )
        self._activate_btn.clicked.connect(self._on_activate)
        left_layout.addWidget(self._activate_btn)

        self._restore_btn = QPushButton("↺  Restaurar padrão de fábrica")
        self._restore_btn.setMinimumHeight(34)
        self._restore_btn.clicked.connect(self._on_restore)
        left_layout.addWidget(self._restore_btn)

        splitter.addWidget(left)

        # Coluna direita: editor
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(10)

        form = QFormLayout()
        form.setSpacing(8)
        self._name_input = QLineEdit()
        self._name_input.setMaxLength(120)
        self._name_input.setMinimumHeight(32)
        form.addRow("Nome:", self._name_input)

        self._desc_input = QLineEdit()
        self._desc_input.setMaxLength(200)
        self._desc_input.setMinimumHeight(32)
        form.addRow("Descrição:", self._desc_input)

        right_layout.addLayout(form)

        prompt_label = QLabel("Texto do prompt:")
        prompt_label.setProperty("hint", True)
        right_layout.addWidget(prompt_label)
        self._prompt_edit = QPlainTextEdit()
        self._prompt_edit.setPlaceholderText(
            "Digite aqui o texto do prompt. Pode ter qualquer tamanho."
        )
        right_layout.addWidget(self._prompt_edit, 1)

        save_row = QHBoxLayout()
        save_row.addStretch(1)
        self._save_btn = QPushButton("💾  Salvar alterações")
        self._save_btn.setMinimumHeight(36)
        self._save_btn.setStyleSheet(
            "QPushButton {"
            "  background-color: #4493f8;"
            "  color: white;"
            "  border: 1px solid #4493f8;"
            "  font-weight: 600;"
            "}"
            "QPushButton:hover { background-color: #58a6ff; border-color: #58a6ff; }"
            "QPushButton:pressed { background-color: #2f7be0; }"
            "QPushButton:disabled {"
            "  background-color: #21262d;"
            "  color: #484f58;"
            "  border-color: #30363d;"
            "}"
        )
        self._save_btn.clicked.connect(self._on_save)
        save_row.addWidget(self._save_btn)
        right_layout.addLayout(save_row)

        splitter.addWidget(right)
        splitter.setSizes([320, 580])
        layout.addWidget(splitter, 1)

        # Botão fechar
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    # ------------------------------------------------------------------ #
    # Helpers de UI                                                       #
    # ------------------------------------------------------------------ #

    def _refresh_list(self) -> None:
        previous_id = self._current_preset_id()
        self._list.blockSignals(True)
        self._list.clear()
        presets = self._store.list()
        for p in presets:
            label = f"★ {p.name}" if p.is_default else f"   {p.name}"
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, p.id)
            self._list.addItem(item)

        # Restaura a seleção anterior, ou usa o initial_selected, ou o
        # primeiro item.
        target_id = previous_id or self._initial_selected
        if target_id is not None:
            for i in range(self._list.count()):
                if self._list.item(i).data(Qt.UserRole) == target_id:
                    self._list.setCurrentRow(i)
                    break
        if self._list.currentRow() < 0 and self._list.count() > 0:
            self._list.setCurrentRow(0)
        self._list.blockSignals(False)

        # Força o display do item atual
        self._on_selection_changed(self._list.currentItem(), None)

        # Atualiza estado dos botões
        self._del_btn.setEnabled(self._store.count() > 1)
        self._activate_btn.setEnabled(self._list.currentItem() is not None)

    def _current_preset_id(self) -> str | None:
        item = self._list.currentItem()
        if item is None:
            return None
        return item.data(Qt.UserRole)

    def _current_preset(self) -> PromptPreset | None:
        pid = self._current_preset_id()
        if pid is None:
            return None
        return self._store.get(pid)

    def _load_into_form(self, preset: PromptPreset | None) -> None:
        if preset is None:
            self._name_input.clear()
            self._desc_input.clear()
            self._prompt_edit.clear()
            self._save_btn.setEnabled(False)
            return
        self._name_input.setText(preset.name)
        self._desc_input.setText(preset.description)
        self._prompt_edit.setPlainText(preset.prompt_text)
        self._save_btn.setEnabled(True)

    # ------------------------------------------------------------------ #
    # Slots                                                              #
    # ------------------------------------------------------------------ #

    def _on_selection_changed(self, current, _previous) -> None:
        self._load_into_form(self._current_preset())

    def _show_error(self, action: str, exc: Exception) -> None:
        logger.warning("%s falhou (%s)", action, exc.__class__.__name__)
        QMessageBox.warning(
            self,
            f"Falha: {action}",
            f"Não foi possível {action}. {exc}",
        )

    def _on_new(self) -> None:
        try:
            preset = self._store.create(
                name="Novo preset",
                description="",
                prompt_text="",
            )
            self._refresh_list()
            self._select_by_id(preset.id)
        except AppError as exc:
            self._show_error("criar o preset", exc)

    def _on_duplicate(self) -> None:
        pid = self._current_preset_id()
        if pid is None:
            return
        try:
            copy = self._store.duplicate(pid)
            self._refresh_list()
            self._select_by_id(copy.id)
        except AppError as exc:
            self._show_error("duplicar o preset", exc)

    def _on_delete(self) -> None:
        pid = self._current_preset_id()
        if pid is None:
            return
        if self._store.count() <= 1:
            QMessageBox.information(
                self,
                "Não é possível excluir",
                "Você não pode excluir o único preset do sistema. "
                "Crie outro antes de excluir este.",
            )
            return

        preset = self._store.get(pid)
        if preset is None:
            return
        confirm = QMessageBox.question(
            self,
            "Excluir preset",
            f"Tem certeza que deseja excluir o preset '{preset.name}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return

        try:
            self._store.delete(pid)
            self._refresh_list()
        except AppError as exc:
            self._show_error("excluir o preset", exc)

    def _on_activate(self) -> None:
        pid = self._current_preset_id()
        if pid is None:
            return
        try:
            self._store.set_default(pid)
            self._default_changed = True
            self._refresh_list()
        except AppError as exc:
            self._show_error("ativar o preset", exc)

    def _on_restore(self) -> None:
        try:
            self._store.restore_factory_default()
            self._default_changed = True
            self._refresh_list()
        except AppError as exc:
            self._show_error("restaurar o padrão de fábrica", exc)

    def _on_save(self) -> None:
        pid = self._current_preset_id()
        if pid is None:
            return
        name = self._name_input.text().strip()
        if not name:
            QMessageBox.warning(
                self, "Nome vazio", "O nome do preset não pode estar vazio."
            )
            return

        try:
            self._store.update(
                pid,
                name=name,
                description=self._desc_input.text(),
                prompt_text=self._prompt_edit.toPlainText(),
            )
            self._refresh_list()
        except AppError as exc:
            self._show_error("salvar o preset", exc)

    def _select_by_id(self, preset_id: str) -> None:
        for i in range(self._list.count()):
            if self._list.item(i).data(Qt.UserRole) == preset_id:
                self._list.setCurrentRow(i)
                return

    # ------------------------------------------------------------------ #
    # Resultado                                                           #
    # ------------------------------------------------------------------ #

    def result(self) -> PresetDialogResult:
        return PresetDialogResult(
            selected_preset_id=self._current_preset_id(),
            default_changed=self._default_changed,
        )

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        super().closeEvent(event)


__all__ = ["PromptPresetDialog", "PresetDialogResult"]
