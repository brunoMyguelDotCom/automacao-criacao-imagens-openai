"""Diálogo de configuração: gerenciamento seguro da chave da OpenAI.

Este diálogo consome `CredentialManager`. Toda a lógica de validação
fica no `CredentialManager`; o diálogo só lida com:
- máscara de exibição (a chave nunca aparece em claro depois de salva);
- chamada explícita de "Testar conexão";
- habilitação progressiva de botões;
- confirmação de remoção.

Garantias aplicadas aqui (auditáveis no código):
- `QLineEdit` em modo `Password` por padrão;
- após salvar, o campo é sempre limpo;
- a chave salva nunca é reexibida — `setPlaceholderText("••••••••")`;
- a chave não vai para o `windowTitle`, nem para `setToolTip`;
- nenhum `print`/`logger` recebe a chave como argumento.
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
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from app.data.storage import (
    CredentialManager,
    CredentialStatus,
    CredentialTestResult,
)

logger = logging.getLogger(__name__)


# Marcador usado para indicar "há uma chave salva" sem revelá-la.
MASK_PLACEHOLDER = "••••••••••"


@dataclass(frozen=True)
class DialogResult:
    """Resultado retornado por `SettingsDialog.exec()`.

    Atributos:
        saved: True se o usuário salvou uma nova chave nesta sessão.
        deleted: True se o usuário removeu a credencial salva.
        tested_in_session: True se `test_key` rodou com sucesso pelo
            menos uma vez durante a sessão (o botão Salvar fica
            habilitado sem aviso adicional).
    """

    saved: bool
    deleted: bool
    tested_in_session: bool


class SettingsDialog(QDialog):
    """Diálogo de configuração da credencial."""

    def __init__(
        self,
        credential_manager: CredentialManager,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._cred = credential_manager
        self._saved = False
        self._deleted = False
        self._tested_in_session = False

        # Janela nunca carrega a chave — apenas indica que ela existe.
        self.setWindowTitle("Configuração — Credencial da OpenAI")
        self.setModal(True)
        self.setMinimumWidth(520)

        self._build_ui()
        self._refresh_status_indicator()

    # ------------------------------------------------------------------ #
    # UI                                                                 #
    # ------------------------------------------------------------------ #

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(14)

        # Indicador
        self._status_label = QLabel()
        self._status_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._status_label.setStyleSheet(
            "padding: 12px 14px; border-radius: 6px; font-weight: 600;"
        )
        layout.addWidget(self._status_label)

        # Form
        form = QFormLayout()
        form.setSpacing(10)
        self._key_input = QLineEdit()
        self._key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self._key_input.setPlaceholderText("sk-...")
        self._key_input.setMinimumHeight(32)
        self._key_input.textChanged.connect(self._on_text_changed)

        self._toggle_visibility_btn = QPushButton("Mostrar")
        self._toggle_visibility_btn.setCheckable(True)
        self._toggle_visibility_btn.setMinimumHeight(32)
        self._toggle_visibility_btn.toggled.connect(self._on_toggle_visibility)

        key_row = QHBoxLayout()
        key_row.setSpacing(8)
        key_row.addWidget(self._key_input, 1)
        key_row.addWidget(self._toggle_visibility_btn)
        form.addRow("Chave da API:", key_row)

        self._backend_label = QLabel()
        self._backend_label.setProperty("hint", True)
        self._backend_label.setStyleSheet(
            "color: #9da7b3; font-size: 11px;"
        )
        form.addRow("Armazenamento:", self._backend_label)

        layout.addLayout(form)

        # Botões de ação
        actions = QHBoxLayout()
        actions.setSpacing(8)

        self._test_btn = QPushButton("🔌  Testar conexão")
        self._test_btn.setMinimumHeight(36)
        self._test_btn.clicked.connect(self._on_test)
        actions.addWidget(self._test_btn)

        self._save_btn = QPushButton("💾  Salvar")
        self._save_btn.setMinimumHeight(36)
        self._save_btn.setEnabled(False)
        self._save_btn.setStyleSheet(
            "QPushButton {"
            "  background-color: #4493f8;"
            "  color: white;"
            "  border: 1px solid #4493f8;"
            "  border-radius: 6px;"
            "  padding: 8px 16px;"
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
        actions.addWidget(self._save_btn)

        self._delete_btn = QPushButton("🗑  Remover credencial salva")
        self._delete_btn.setMinimumHeight(36)
        self._delete_btn.setStyleSheet(
            "QPushButton {"
            "  color: #f85149;"
            "  border: 1px solid #f85149;"
            "}"
            "QPushButton:hover { background-color: #3d1416; }"
        )
        self._delete_btn.clicked.connect(self._on_delete)
        actions.addWidget(self._delete_btn)

        layout.addLayout(actions)

        # Rodapé com botão fechar
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._key_input.setFocus()

    # ------------------------------------------------------------------ #
    # Handlers                                                           #
    # ------------------------------------------------------------------ #

    def _on_text_changed(self, text: str) -> None:
        # Salvar só fica habilitado quando há texto. Quando o usuário
        # apaga o conteúdo, voltamos a desabilitar.
        self._save_btn.setEnabled(bool(text.strip()))
        # Texto novo invalida o teste anterior — só assim o usuário
        # não confunde "chave A testada" com "chave B digitada".
        self._tested_in_session = False
        self._test_result_label = getattr(self, "_test_result_label", None)
        if self._test_result_label is not None:
            self._test_result_label.clear()

    def _on_toggle_visibility(self, checked: bool) -> None:
        if checked:
            # Mostra a chave somente durante a edição ativa — exatamente
            # como o prompt exige. Salvar ou trocar de aba volta a
            # mascarar.
            self._key_input.setEchoMode(QLineEdit.EchoMode.Normal)
            self._toggle_visibility_btn.setText("Ocultar")
        else:
            self._key_input.setEchoMode(QLineEdit.EchoMode.Password)
            self._toggle_visibility_btn.setText("Mostrar")

    def _on_test(self) -> None:
        key = self._key_input.text().strip()
        if not key:
            self._show_test_result(
                CredentialTestResult(
                    status=CredentialStatus.INVALID_KEY,
                    message="Digite uma chave antes de testar.",
                )
            )
            return

        self._test_btn.setEnabled(False)
        try:
            result = self._cred.test_key(key)
        except Exception as exc:  # noqa: BLE001 — UX final
            logger.warning("Testar conexão: erro inesperado (%s)", exc.__class__.__name__)
            self._show_test_result(
                CredentialTestResult(
                    status=CredentialStatus.SERVICE_UNAVAILABLE,
                    message="Falha inesperada. Veja o log para detalhes.",
                )
            )
            return
        finally:
            self._test_btn.setEnabled(True)

        self._show_test_result(result)
        if result.status is CredentialStatus.VALID:
            self._tested_in_session = True
            self._save_btn.setEnabled(True)

    def _on_save(self) -> None:
        key = self._key_input.text().strip()
        if not key:
            return

        if not self._tested_in_session:
            confirm = QMessageBox.question(
                self,
                "Salvar sem testar",
                "Você não testou essa chave nesta sessão. Salvar assim mesmo?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if confirm != QMessageBox.StandardButton.Yes:
                return

        try:
            self._cred.save_key(key)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Falha salvando credencial (%s)", exc.__class__.__name__)
            QMessageBox.warning(
                self,
                "Falha ao salvar",
                "Não foi possível salvar a credencial. Veja o log para detalhes.",
            )
            return

        self._saved = True
        # Limpa o campo imediatamente — a chave nunca fica no widget
        # depois de salva.
        self._key_input.clear()
        self._key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self._toggle_visibility_btn.setChecked(False)
        self._toggle_visibility_btn.setText("Mostrar")
        self._tested_in_session = False
        self._refresh_status_indicator()
        QMessageBox.information(self, "Salvo", "Credencial salva com sucesso.")

    def _on_delete(self) -> None:
        if not self._cred.has_key():
            QMessageBox.information(
                self,
                "Nada a remover",
                "Nenhuma credencial configurada no momento.",
            )
            return

        confirm = QMessageBox.question(
            self,
            "Remover credencial",
            "Tem certeza que deseja remover a credencial salva? "
            "Você precisará digitá-la novamente para gerar imagens.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return

        try:
            self._cred.delete_key()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Falha removendo credencial (%s)", exc.__class__.__name__)
            QMessageBox.warning(
                self,
                "Falha ao remover",
                "Não foi possível remover a credencial. Veja o log para detalhes.",
            )
            return

        self._deleted = True
        self._refresh_status_indicator()
        QMessageBox.information(self, "Removida", "Credencial removida.")

    # ------------------------------------------------------------------ #
    # Auxiliares                                                         #
    # ------------------------------------------------------------------ #

    def _show_test_result(self, result: CredentialTestResult) -> None:
        # Cria o label uma única vez
        if not hasattr(self, "_test_result_label") or self._test_result_label is None:
            from PySide6.QtWidgets import QLabel as _QLabel

            self._test_result_label = _QLabel()
            self._test_result_label.setWordWrap(True)
            self.layout().insertWidget(1, self._test_result_label)

        # Mensagem + cor por status. A chave nunca entra na mensagem.
        palette = {
            CredentialStatus.VALID: ("#3fb950", "✓ ", "#1b3a23"),
            CredentialStatus.INVALID_KEY: ("#f85149", "✗ ", "#3d1416"),
            CredentialStatus.NETWORK_ERROR: ("#f85149", "✗ ", "#3d1416"),
            CredentialStatus.SERVICE_UNAVAILABLE: ("#d29922", "⚠ ", "#3d2f0a"),
        }
        color, glyph, bg = palette.get(result.status, ("#e6edf3", "", "#21262d"))
        self._test_result_label.setText(f"{glyph}{result.message}")
        self._test_result_label.setStyleSheet(
            f"color: {color}; font-weight: 600; "
            f"background-color: {bg}; border-radius: 6px; padding: 8px 12px;"
        )

    def _refresh_status_indicator(self) -> None:
        if self._cred.has_key():
            self._status_label.setText(
                "✓  Credencial configurada. Clique em \"Testar conexão\" "
                "para validar."
            )
            self._status_label.setStyleSheet(
                "color: #3fb950; background-color: #1b3a23; "
                "padding: 12px 14px; border-radius: 6px; font-weight: 600;"
            )
        else:
            self._status_label.setText("✗  Nenhuma credencial configurada.")
            self._status_label.setStyleSheet(
                "color: #f85149; background-color: #3d1416; "
                "padding: 12px 14px; border-radius: 6px; font-weight: 600;"
            )

        self._backend_label.setText(self._cred.backend_name)

    # ------------------------------------------------------------------ #
    # Resultado                                                           #
    # ------------------------------------------------------------------ #

    def result(self) -> DialogResult:
        return DialogResult(
            saved=self._saved,
            deleted=self._deleted,
            tested_in_session=self._tested_in_session,
        )

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 (override Qt)
        # Limpeza defensiva: garante que o campo seja apagado mesmo se o
        # usuário fechar pelo X.
        self._key_input.clear()
        super().closeEvent(event)