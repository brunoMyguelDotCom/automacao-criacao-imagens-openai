"""Primitivas de área de transferência (Prompt 1.1).

A V1 do provider copia o caminho do arquivo de imagem para o
clipboard no formato `CF_HDROP` (o mesmo que o Explorer usa ao
pressionar Ctrl+C em um arquivo) e depois dispara `Ctrl+V` para
colar dentro do campo de anexo do ChatGPT Desktop.

Por que `CF_HDROP` e não uma imagem binária?
    Colar a imagem direto (via `setClipboardData(CF_BITMAP, ...)`)
    funciona em muitos campos, mas o ChatGPT Desktop trata o
    anexo como "arquivo" e mostra nome/tamanho. Copiar o caminho
    no formato de dropagem replica o comportamento do Explorer —
    é o que está validado no plano (regra 2).

Funções públicas:
    copy_file_to_clipboard(path: Path) -> bool
    copy_text_to_clipboard(text: str) -> bool
    send_paste() -> bool
    send_enter() -> bool

Padrão de erro:
    Não lança exceção. Retorna `False` em qualquer falha previsível
    (SO não-Windows, clipboard ocupado, módulo ausente). Quem
    chama converte `False` em `GenerationError`.

Restrição de plataforma:
    `copy_file_to_clipboard` exige Windows + pywin32.
    `copy_text_to_clipboard`, `send_paste` e `send_enter` exigem
    pyautogui e pyperclip (ambos multiplataforma, mas a V1 só
    usa em Windows). Em outros SOs todas retornam `False`.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

WINDOWS_PLATFORM = "win32"

# Terminador da lista de paths em CF_HDROP: cada path em UTF-16
# termina com NULO, e a LISTA termina com um NULO extra.
_DOUBLE_NULL_TERMINATOR = "\0\0"


def copy_file_to_clipboard(path: Path) -> bool:
    """Copia `path` para o clipboard no formato `CF_HDROP`.

    Cobre o caso de o usuário ter selecionado "Copiar" em um arquivo
    no Explorer e colar (Ctrl+V) no campo de anexo do ChatGPT
    Desktop. Múltiplos arquivos podem ser concatenados separados
    por NULO; aqui só colocamos um.

    Args:
        path: caminho absoluto do arquivo a copiar.

    Returns:
        `True` se o clipboard foi preenchido; `False` em qualquer
        falha (SO não-Windows, pywin32 ausente, clipboard ocupado,
        path inexistente).
    """
    if sys.platform != WINDOWS_PLATFORM:
        logger.warning(
            "copy_file_to_clipboard: só funciona em Windows (SO atual: %s)",
            sys.platform,
        )
        return False

    path = Path(path)
    if not path.exists() or not path.is_file():
        logger.warning(
            "copy_file_to_clipboard: arquivo inexistente ou inválido: %s", path
        )
        return False

    try:
        import win32clipboard  # type: ignore[import-not-found]
    except ImportError:
        logger.warning(
            "copy_file_to_clipboard: pywin32 não está instalado; "
            "instale com `pip install pywin32`."
        )
        return False

    # DROPFILES é um payload binário. Montamos manualmente para
    # evitar uma dependência extra (`pywin32` não traz um helper
    # pronto para CF_HDROP).
    payload = _build_dropfiles_payload(path)

    opened = False
    try:
        win32clipboard.OpenClipboard()
        opened = True
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(win32clipboard.CF_HDROP, payload)
        logger.info("copy_file_to_clipboard: %s copiado como CF_HDROP.", path)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "copy_file_to_clipboard: falha ao escrever no clipboard (%s): %s",
            exc.__class__.__name__,
            exc,
        )
        return False
    finally:
        if opened:
            try:
                win32clipboard.CloseClipboard()
            except Exception as exc:  # noqa: BLE001
                # Fechar o clipboard raramente falha; só loga.
                logger.debug(
                    "copy_file_to_clipboard: CloseClipboard falhou: %s", exc
                )


def _build_dropfiles_payload(path: Path) -> bytes:
    """Monta o payload binário de CF_HDROP com UM path.

    Formato:
        [DROPFILES header de 16 bytes]
        [path UTF-16 little-endian, terminado em NULO]
        [NULO extra para fechar a lista]
    """
    import struct

    # wchar_t terminado em NULO + NULO extra para encerrar a lista.
    wide_path = str(path) + _DOUBLE_NULL_TERMINATOR
    wide_bytes = wide_path.encode("utf-16-le")

    # Estrutura DROPFILES (WinUser.h):
    #   DWORD pFiles;  // offset, em bytes, ao início dos paths
    #   POINT pt;      // { LONG x; LONG y; }  → coordenadas do drop
    #   BOOL  fNC;     // drop na área de não-cliente? 0
    #   BOOL  fWide;   // paths em wchar_t? 1 (WinXP+)
    # Total: 20 bytes. pFiles=20 significa "paths começam logo após
    # este header".
    header = struct.pack("<IiiII", 20, 0, 0, 0, 1)
    return header + wide_bytes


def copy_text_to_clipboard(text: str) -> bool:
    """Copia `text` (UTF-8) para o clipboard de texto.

    Args:
        text: conteúdo a copiar. Strings longas são aceitas; quem
            chama é o `provider`, que vai preenchendo com o prompt
            editado.

    Returns:
        `True` se o clipboard foi preenchido; `False` em falha.
    """
    if sys.platform != WINDOWS_PLATFORM:
        logger.warning(
            "copy_text_to_clipboard: só funciona em Windows (SO atual: %s)",
            sys.platform,
        )
        return False

    try:
        import pyperclip  # type: ignore[import-not-found]
    except ImportError:
        logger.warning(
            "copy_text_to_clipboard: pyperclip não está instalado; "
            "instale com `pip install pyperclip`."
        )
        return False

    try:
        pyperclip.copy(text)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "copy_text_to_clipboard: pyperclip falhou (%s): %s",
            exc.__class__.__name__,
            exc,
        )
        return False


def send_paste() -> bool:
    """Dispara Ctrl+V na janela em foco.

    Returns:
        `True` se o atalho foi enviado; `False` se pyautogui não
        está instalado ou se o SO não for Windows.
    """
    return _send_hotkey("ctrl", "v")


def send_enter() -> bool:
    """Dispara Enter na janela em foco.

    Returns:
        `True` se a tecla foi enviada; `False` em falha.
    """
    return _send_hotkey("enter")


def _send_hotkey(*keys: str) -> bool:
    """Atalho de teclado genérico via pyautogui. Só Windows."""
    if sys.platform != WINDOWS_PLATFORM:
        logger.warning(
            "_send_hotkey: só funciona em Windows (SO atual: %s)", sys.platform
        )
        return False

    try:
        import pyautogui  # type: ignore[import-not-found]
    except ImportError:
        logger.warning(
            "_send_hotkey: pyautogui não está instalado; "
            "instale com `pip install pyautogui`."
        )
        return False

    # `failSafe=False` evita o canto-da-tela que aborta tudo.
    # Para a V1 isso não é problema porque Control+V/Enter são
    # ações instantâneas; se virar problema na V3, ajustamos.
    try:
        pyautogui.hotkey(*keys) if len(keys) > 1 else pyautogui.press(keys[0])
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "_send_hotkey(%s): pyautogui falhou (%s): %s",
            keys,
            exc.__class__.__name__,
            exc,
        )
        return False


__all__ = [
    "copy_file_to_clipboard",
    "copy_text_to_clipboard",
    "send_paste",
    "send_enter",
    "WINDOWS_PLATFORM",
]
