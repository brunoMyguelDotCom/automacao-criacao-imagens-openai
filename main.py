"""Entry point do aplicativo GeradorImagensProduto.

Uso: python main.py

Este arquivo é deliberadamente fino: configura logging, instancia a
QApplication, cria a MainWindow e entra no loop de eventos. Toda a
lógica de domínio vive em `app/`.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

# Garante que a raiz do projeto esteja no sys.path, mesmo quando o app
# for chamado a partir de outro cwd (`python /caminho/main.py`).
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config.logging_setup import setup_logging  # noqa: E402
from app.config.resources import get_resource_path, is_frozen  # noqa: E402
from app.ui.windows.main_window import MainWindow  # noqa: E402


def main() -> int:
    log_path = setup_logging(console=True)
    logging.info("Iniciando GeradorImagensProduto. Log em %s", log_path)
    logging.info(
        "Modo de execução: %s",
        "empacotado (PyInstaller)" if is_frozen() else "desenvolvimento (python main.py)",
    )

    # Importação tardia do Qt: queremos que o logging já esteja
    # configurado antes do Qt emitir qualquer mensagem.
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("GeradorImagensProduto")

    # Tema escuro centralizado — ver `app/ui/theme.py`.
    from app.ui.theme import apply_app_theme

    apply_app_theme(app)

    # Handler global de exceções (Prompt 10, regra 1): captura
    # exceções não tratadas em main thread, threads não-Qt e
    # destrutores — mostra QMessageBox amigável + "Ver detalhes".
    from app.core.resilience import install as install_exception_handler

    install_exception_handler(app)

    # Ícone do aplicativo — mesma logo que vai no .exe e no
    # .desktop. Em dev aponta para `assets/icons/app.png`; no
    # executável empacotado aponta para o `_MEIPASS`.
    _set_app_icon(app)

    window = MainWindow()
    window.show()
    return app.exec()


def _set_app_icon(app) -> None:
    """Aplica o ícone do app na QApplication e na MainWindow."""
    from PySide6.QtGui import QIcon

    icon_path = get_resource_path("assets", "icons", "app.png")
    if not icon_path.exists():
        # Em desenvolvimento normal, o ícone é gerado por
        # `scripts/build/build_icons.py`. Se estiver faltando,
        # logamos e seguimos — o app abre sem ícone.
        logging.warning("Ícone do app não encontrado em %s", icon_path)
        return
    app.setWindowIcon(QIcon(str(icon_path)))


if __name__ == "__main__":
    raise SystemExit(main())