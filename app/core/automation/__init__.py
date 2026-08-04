"""Automação de desktop (V1 — Prova de Integração).

Isola as primitivas de baixo nível usadas pelo
`ChatGPTDesktopAutomationProvider` para controlar a janela do
ChatGPT Desktop, manipular a área de transferência e detectar
arquivos novos na pasta de Downloads.

Os módulos daqui são:
    - window_control   — localizar e focar uma janela por trecho de título.
    - clipboard_utils  — copiar arquivos (CF_HDROP) e texto para o clipboard,
                         e disparar Ctrl+V / Enter via pyautogui.
    - download_watcher — observar uma pasta até aparecer um arquivo novo
                         e estável (download concluído).

Restrição de plataforma
-----------------------
A V1 automatiza o **ChatGPT Desktop** (aplicativo Windows), então as
funções utilitárias só fazem sentido no Windows. Para manter o pacote
importável em qualquer SO (útil para testes e para o ambiente de
desenvolvimento Linux), os imports de `pywin32`, `pygetwindow`,
`pyautogui` e `pyperclip` são feitos **dentro** das funções, não no
topo do módulo. Cada função que depende do backend Windows:

    * Documenta isso na docstring.
    * Loga um `logger.warning(...)` e retorna um valor "sem efeito"
      (`False` / `None`) quando o SO não é Windows, em vez de
      levantar `ImportError` ou `NotImplementedError`.

Erros previsíveis (clipboard ocupado, janela não encontrada, etc.)
não são convertidos em exceção — viram `False` / `None`, seguindo o
padrão de "falhas viram retorno" do resto do projeto
(`ImageFolderScanner`, `BatchPlanner`, etc.).
"""

from app.core.automation.clipboard_utils import (
    copy_file_to_clipboard,
    copy_text_to_clipboard,
    send_enter,
    send_paste,
)
from app.core.automation.download_watcher import (
    snapshot_files,
    wait_for_new_stable_file,
)
from app.core.automation.window_control import find_and_focus_window

__all__ = [
    "find_and_focus_window",
    "copy_file_to_clipboard",
    "copy_text_to_clipboard",
    "send_paste",
    "send_enter",
    "snapshot_files",
    "wait_for_new_stable_file",
]
