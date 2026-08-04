"""Localização e foco de janelas do ChatGPT Desktop (Prompt 1.1).

Responsabilidade ÚNICA: dado um trecho de título de janela
(`title_hint`, ex.: "ChatGPT"), encontrar a janela correspondente no
desktop e trazê-la ao primeiro plano.

Função pública:
    find_and_focus_window(title_hint, timeout_s=10.0) -> bool

Padrão de erro:
    Não lança exceção. Se a janela não for encontrada dentro do
    `timeout_s`, retorna `False`. Quem chama decide o que fazer
    (a V1 converte esse retorno em `GenerationError` no provider).

Restrição de plataforma:
    Só funciona no Windows (a V1 automatiza o ChatGPT Desktop).
    Em outros SOs, a função retorna `False` imediatamente após
    logar um `logger.warning(...)`. Isso mantém o módulo importável
    no ambiente de dev (Linux/macOS) sem quebrar a aplicação.
"""

from __future__ import annotations

import logging
import sys
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # evita custo de import em runtime
    from pathlib import Path  # noqa: F401  (placeholder p/ type checkers)

logger = logging.getLogger(__name__)

# Plataforma alvo da V1. Mantida como constante para ser fácil de
# auditar e de mockar em testes (`monkeypatch.setattr`).
WINDOWS_PLATFORM = "win32"

# Intervalo entre tentativas de localizar a janela (segundos).
# Curto o bastante para reagir rápido, longo o bastante para não
# gastar CPU em busy-wait.
_POLL_INTERVAL_S = 0.5


def find_and_focus_window(title_hint: str, timeout_s: float = 10.0) -> bool:
    """Encontra uma janela cujo título contém `title_hint` e a foca.

    Args:
        title_hint: trecho de título a procurar (case-sensitive na
            comparação que o `pygetwindow` usa internamente). Para o
            ChatGPT Desktop, valores razoáveis são `"ChatGPT"` ou
            `"ChatGPT Desktop"`.
        timeout_s: tempo total máximo de espera, em segundos. Padrão
            10s — cobre o tempo do Electron iniciar / restaurar.

    Returns:
        `True` se a janela foi encontrada e focada com sucesso.
        `False` se o SO não for Windows, se `pygetwindow`/`pywin32`
        não estiverem instalados, ou se a janela não apareceu
        dentro de `timeout_s`.

    Notas:
        Se a janela estiver minimizada, é restaurada antes do foco
        (`win.restore()` seguido de `win.activate()`). Esse é o
        comportamento típico do botão "abrir" da barra de tarefas.
    """
    if sys.platform != WINDOWS_PLATFORM:
        logger.warning(
            "find_and_focus_window: só funciona em Windows (SO atual: %s)",
            sys.platform,
        )
        return False

    # Imports lazy: evita quebrar o app em plataformas que não têm
    # pywin32 / pygetwindow instalados (ex.: dev Linux).
    try:
        import pygetwindow  # type: ignore[import-not-found]
    except ImportError:
        logger.warning(
            "find_and_focus_window: pygetwindow não está instalado; "
            "instale com `pip install pygetwindow`."
        )
        return False

    if not title_hint or not title_hint.strip():
        logger.warning("find_and_focus_window: title_hint vazio.")
        return False

    deadline = time.monotonic() + max(0.0, timeout_s)
    attempt = 0
    while True:
        attempt += 1
        try:
            matches = pygetwindow.getWindowsWithTitle(title_hint)
        except Exception as exc:  # noqa: BLE001 — pygetwindow pode falhar em edge cases
            logger.warning(
                "find_and_focus_window: pygetwindow falhou (tentativa %d): %s",
                attempt,
                exc,
            )
            matches = []

        for win in matches:
            # getWindowsWithTitle faz substring match, mas pode trazer
            # janelas cujo título só tangencia o hint. Pegamos a
            # primeira que tenha tamanho não-zero (janela minimizada
            # para a barra de tarefas tem dimensões reduzidas mas
            # ainda é um objeto válido; usamos `.visible` quando
            # existir como heurística).
            if _is_usable(win):
                return _activate(win)

        if time.monotonic() >= deadline:
            logger.warning(
                "find_and_focus_window: janela com título contendo %r "
                "não apareceu em %.1fs (tentativas=%d).",
                title_hint,
                timeout_s,
                attempt,
            )
            return False

        time.sleep(_POLL_INTERVAL_S)


def _is_usable(win: object) -> bool:
    """Heurística: a janela existe e tem dimensões minimamente válidas.

    Janelas-fantasma (0x0) costumam aparecer em alguns estados do
    Electron. Consideramos "usável" qualquer janela com área > 0 ou,
    na ausência de `.width`/`.height`, qualquer objeto não-nulo.
    """
    width = getattr(win, "width", 0) or 0
    height = getattr(win, "height", 0) or 0
    return (width * height) > 0 or (width == 0 and height == 0 and win is not None)


def _activate(win: object) -> bool:
    """Tenta trazer a janela ao primeiro plano. Retorna sucesso."""
    try:
        # Se estiver minimizada, restaura antes de ativar.
        is_minimized = getattr(win, "isMinimized", None)
        if callable(is_minimized) and is_minimized():
            restore = getattr(win, "restore", None)
            if callable(restore):
                restore()
        activate = getattr(win, "activate", None)
        if not callable(activate):
            logger.warning("find_and_focus_window: janela sem método activate().")
            return False
        activate()
        logger.info(
            "find_and_focus_window: janela '%s' focada.",
            getattr(win, "title", "<sem título>"),
        )
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "find_and_focus_window: falha ao ativar janela (%s): %s",
            exc.__class__.__name__,
            exc,
        )
        return False


__all__ = ["find_and_focus_window", "WINDOWS_PLATFORM"]
