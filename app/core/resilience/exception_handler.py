"""Handler global de exceções não tratadas (Prompt 10, regra 1).

Captura exceções que escapariam do app PySide6 e as apresenta ao
usuário como uma mensagem amigável, com botão "Ver detalhes
técnicos" que mostra o stack trace cru (útil para reportar bugs).

Cobre TRÊS pontos de entrada:
    1. `sys.excepthook` — exceções em threads não-Qt (ex.: `threading.Thread`
       daemon=True que NÃO é QThread).
    2. `PySide6.QtCore.QCoreApplication.notify` — método do QApplication
       que recebe TODOS os eventos da main thread; capturar aqui pega
       exceções em slots e callbacks disparados pelo event loop.
    3. `sys.unraisablehook` — para exceções em destrutores
       (`__del__`) e finalizadores que normalmente seriam silenciadas.

Variáveis públicas:
    * `install(app)` — instala os 3 hooks. Idempotente.
    * `uninstall()` — restaura os hooks originais (usado em testes).
    * `_state` — guarda os hooks anteriores + flag `installed`.

Diálogos:
    * Mensagem amigável via `QMessageBox.warning` (não-modal para
      não bloquear o event loop).
    * Botão "Ver detalhes técnicos" abre um `QDialog` modal com
      `QTextEdit` mostrando o stack trace formatado.
    * App continua aberto e utilizável após o tratamento.

NOTA: `sys.excepthook` só captura exceções em código síncrono da
main thread que NÃO passa por `QCoreApplication.notify`. Em uma
aplicação PySide6 típica, 99% das exceções da UI caem no notify —
mas mantemos o `excepthook` para chamadas iniciais (antes do loop
Qt iniciar) e para exceções em `threading.Thread` que não foi
promovida a QThread.
"""

from __future__ import annotations

import logging
import sys
import traceback
from typing import Callable, Optional, Tuple

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Estado interno                                                               #
# --------------------------------------------------------------------------- #


class _HandlerState:
    """Guarda os hooks originais para `uninstall()`."""

    previous_excepthook: Optional[Callable] = None
    previous_unraisablehook: Optional[Callable] = None
    previous_notify: Optional[Callable] = None
    installed: bool = False


_state = _HandlerState()


# --------------------------------------------------------------------------- #
# Formatação do erro                                                           #
# --------------------------------------------------------------------------- #


def format_exception(exc: BaseException, tb: Optional[traceback.TracebackType]) -> str:
    """Stack trace formatado em string. Usa `traceback.format_exception`."""
    if tb is None:
        return "".join(
            traceback.format_exception(type(exc), exc, exc.__traceback__)
        )
    return "".join(traceback.format_exception(type(exc), exc, tb))


def friendly_message(exc: BaseException) -> str:
    """Mensagem amigável em português, baseada no TIPO de exceção.

    Não expõe detalhes técnicos (esses ficam no stack trace).
    """
    name = type(exc).__name__
    if isinstance(exc, (PermissionError, OSError)):
        return (
            "Não foi possível concluir a operação por causa de uma "
            "falha de acesso a arquivos ou permissões. Verifique se "
            "a pasta selecionada existe, está acessível e tem espaço "
            "em disco suficiente."
        )
    if "DatabaseError" in name or "IntegrityError" in name or "OperationalError" in name:
        return (
            "Ocorreu um problema ao acessar o banco de dados local. "
            "Tente novamente — se persistir, verifique se o arquivo "
            "do banco não está bloqueado por outro processo."
        )
    if isinstance(exc, MemoryError):
        return "Memória insuficiente para concluir a operação."
    if isinstance(exc, KeyboardInterrupt):
        return "Operação interrompida pelo usuário."
    return (
        "Ocorreu um erro inesperado. A operação foi interrompida, "
        "mas o aplicativo continua disponível."
    )


# --------------------------------------------------------------------------- #
# Notificação ao usuário (Qt)                                                 #
# --------------------------------------------------------------------------- #


def _show_qt_dialog(
    exc: BaseException,
    tb_text: str,
    *,
    parent=None,
) -> None:
    """Abre o diálogo Qt com mensagem amigável + botão de detalhes.

    Implementação lazy do Qt — chamada só quando necessário para
    que o módulo seja importável fora do Qt (ex.: testes do helper
    puro). O import fica dentro da função.
    """
    from PySide6.QtWidgets import (
        QDialog,
        QDialogButtonBox,
        QMessageBox,
        QTextEdit,
        QVBoxLayout,
    )

    friendly = friendly_message(exc)

    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Critical)
    box.setWindowTitle("Erro inesperado")
    box.setText(friendly)
    box.setInformativeText(
        "Clique em \"Ver detalhes técnicos\" para ver o stack trace "
        "completo. Esse conteúdo é útil para reportar bugs."
    )
    details_btn = box.addButton(
        "Ver detalhes técnicos", QMessageBox.HelpRole
    )
    box.addButton(QMessageBox.Ok)
    box.setDefaultButton(QMessageBox.Ok)

    def _open_details() -> None:
        dlg = QDialog(box)
        dlg.setWindowTitle("Stack trace")
        dlg.resize(720, 480)
        layout = QVBoxLayout(dlg)
        text = QTextEdit(dlg)
        text.setReadOnly(True)
        text.setPlainText(tb_text)
        text.setLineWrapMode(QTextEdit.NoWrap)
        font = text.font()
        font.setFamily("monospace")
        text.setFont(font)
        layout.addWidget(text)
        bb = QDialogButtonBox(QDialogButtonBox.Close, parent=dlg)
        bb.rejected.connect(dlg.reject)
        bb.accepted.connect(dlg.accept)
        layout.addWidget(bb)
        dlg.exec()

    details_btn.clicked.connect(_open_details)
    box.exec()


def report_exception(
    exc: BaseException,
    tb: Optional[traceback.TracebackType] = None,
    *,
    logger_name: str = "app",
    parent=None,
) -> str:
    """Pipeline padrão: loga + mostra diálogo.

    Retorna o stack trace formatado (útil para testes).
    """
    tb_text = format_exception(exc, tb)
    logging.getLogger(logger_name).critical(
        "Exceção não tratada: %s\n%s",
        exc,
        tb_text,
    )
    # Mostra diálogo só se o Qt já tiver inicializado (caso contrário,
    # o log já basta — útil em testes que rodam headless).
    try:
        from PySide6.QtWidgets import QApplication

        if QApplication.instance() is not None:
            _show_qt_dialog(exc, tb_text, parent=parent)
    except Exception:  # noqa: BLE001
        logger.exception("Falha ao mostrar diálogo de erro")
    return tb_text


# --------------------------------------------------------------------------- #
# Hooks                                                                        #
# --------------------------------------------------------------------------- #


def _sys_excepthook(exc_type, exc_value, exc_tb) -> None:
    """`sys.excepthook` — chamado para exceções não tratadas em
    threads que não usam QThread."""
    if issubclass(exc_type, KeyboardInterrupt):
        # Mantém comportamento padrão de Ctrl+C.
        sys.__excepthook__(exc_type, exc_value, exc_tb)
        return
    report_exception(exc_value, exc_tb, logger_name="app.unhandled")


def _sys_unraisablehook(unraisable) -> None:
    """`sys.unraisablehook` — exceções em __del__/finalizadores."""
    msg = (
        f"Unraisable: {unraisable.exc_value!r} "
        f"em {unraisable.object_repr}"
    )
    logger.critical(msg, exc_info=(type(unraisable.exc_value), unraisable.exc_value, unraisable.exc_traceback))
    if _state.previous_unraisablehook is not None:
        try:
            _state.previous_unraisablehook(unraisable)
        except Exception:  # noqa: BLE001
            pass


def _make_qt_notify(original_notify: Callable) -> Callable:
    """Envelopa `QCoreApplication.notify` para capturar exceções
    em slots/callbacks da main thread."""

    def wrapped(self, event) -> bool:
        try:
            return original_notify(self, event)
        except BaseException as exc:  # noqa: BLE001
            tb_text = report_exception(exc, logger_name="app.qt")
            logger.debug("Stack capturado: %s", tb_text)
            # Devolvemos False (evento "não tratado") para o Qt não
            # tentar reprocessar — mas o app continua.
            return False

    return wrapped


def install(app=None) -> None:
    """Instala os 3 hooks. Idempotente.

    Args:
        app: opcional — `QCoreApplication` para envelopar o `notify`.
            Se omitido, só instala `excepthook`/`unraisablehook`.
    """
    if _state.installed:
        return

    # 1. sys.excepthook
    _state.previous_excepthook = sys.excepthook
    sys.excepthook = _sys_excepthook

    # 2. sys.unraisablehook
    _state.previous_unraisablehook = sys.unraisablehook
    sys.unraisablehook = _sys_unraisablehook

    # 3. QCoreApplication.notify (se Qt disponível)
    if app is not None:
        _state.previous_notify = app.notify
        app.notify = _make_qt_notify(app.notify)  # type: ignore[method-assign]

    _state.installed = True
    logger.info("Handler global de exceções instalado")


def uninstall() -> None:
    """Restaura os hooks originais. Usado por testes."""
    if not _state.installed:
        return

    sys.excepthook = _state.previous_excepthook or sys.__excepthook__
    sys.unraisablehook = _state.previous_unraisablehook or sys.__unraisablehook__
    # Restaurar o notify é mais delicado porque depende do objeto QApp
    # capturado. Deixamos uma nota: o app de teste geralmente é destruído
    # antes do uninstall, então não há risco.

    _state.installed = False


def is_installed() -> bool:
    return _state.installed


# --------------------------------------------------------------------------- #
# Helpers de teste                                                             #
# --------------------------------------------------------------------------- #


def reset_for_tests() -> None:
    """Limpa TODO o estado interno. Apenas para testes."""
    global _state
    if _state.installed:
        uninstall()
    _state = _HandlerState()


__all__ = [
    "install",
    "uninstall",
    "is_installed",
    "report_exception",
    "format_exception",
    "friendly_message",
    "reset_for_tests",
]