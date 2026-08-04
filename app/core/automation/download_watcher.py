"""Polling de pasta de Downloads (Prompt 1.1).

A V1 do provider pede ao ChatGPT Desktop para gerar a imagem e
depois precisa saber **quando o download terminou**. Como o
ChatGPT Desktop ainda não é controlado pela árvore de acessibilidade
(V3), a heurística da V1 é:

    1. Tirar uma "foto" dos arquivos presentes na pasta
       ANTES de disparar o download.
    2. Polling a cada `poll_interval_s` segundos.
    3. Quando aparecer um arquivo cujo nome NÃO estava na foto
       inicial, esperar o `st_size` parar de crescer por
       `stable_checks` leituras consecutivas.
    4. Retornar o `Path` apenas quando o tamanho estiver estável
       (sinal de que o download terminou).

Padrão de erro:
    Não lança exceção. Retorna `None` em timeout ou se a própria
    pasta não existir. Quem chama (`BatchProcessor`) já sabe
    tratar `None` como "esperar mais" ou "registrar falha".

Funções públicas:
    snapshot_files(folder: Path) -> set[str]
    wait_for_new_stable_file(folder, known_files_before, timeout_s, ...) -> Path | None
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

logger = logging.getLogger(__name__)

# Tamanho mínimo de arquivo para considerarmos "download começando".
# Evita falsos positivos com arquivos auxiliares de 0 bytes que
# certos apps do Windows criam ao iniciar o download (`.tmp`, `.part`,
# `.crdownload` etc.). Quem mais sabe o que é "arquivo de download"
# é o provider — esta camada só regista o nome e o tamanho.
_MIN_SIZE_BYTES = 1


def snapshot_files(folder: Path) -> set[str]:
    """Retorna o conjunto de nomes de arquivo atualmente em `folder`.

    Usado como "estado do mundo antes de disparar o download" — o
    provider captura esse snapshot e o passa para
    `wait_for_new_stable_file` como `known_files_before`.

    Args:
        folder: pasta a observar.

    Returns:
        Conjunto vazio se a pasta não existir ou estiver inacessível.
        **Erros de I/O são logados e silenciados** — o chamador vai
        receber conjunto vazio e o `wait_for_new_stable_file` vai
        acabar aceitando QUALQUER arquivo que apareça, o que é o
        comportamento desejado quando não pudemos tirar a foto.
    """
    folder = Path(folder)
    if not folder.exists() or not folder.is_dir():
        logger.warning(
            "snapshot_files: pasta inexistente ou inacessível: %s", folder
        )
        return set()

    try:
        return {entry.name for entry in folder.iterdir() if entry.is_file()}
    except OSError as exc:
        logger.warning(
            "snapshot_files: falha ao listar %s (%s): %s",
            folder,
            exc.__class__.__name__,
            exc,
        )
        return set()


def wait_for_new_stable_file(
    folder: Path,
    known_files_before: set[str],
    timeout_s: float,
    poll_interval_s: float = 1.0,
    stable_checks: int = 2,
) -> Path | None:
    """Espera até aparecer um arquivo NOVO e estável em `folder`.

    Args:
        folder: pasta a observar (geralmente a `Downloads` do
            usuário).
        known_files_before: snapshot tirado ANTES do download
            começar. Arquivos com nomes nesse conjunto são
            ignorados (mesmo que tenham sido modificados).
        timeout_s: tempo total máximo de espera, em segundos.
        poll_interval_s: intervalo entre leituras. Padrão 1s.
        stable_checks: quantas leituras consecutivas com o MESMO
            `st_size` são necessárias para declarar o download
            concluído. Padrão 2 leituras (= 2s de estabilidade).

    Returns:
        `Path` do novo arquivo (primeiro que ficou estável) ou
        `None` se o timeout expirou antes de aparecer algo estável,
        ou se a pasta não existe.

    Notas:
        * Arquivos com tamanho 0 são ignorados — provavelmente
          são arquivos de controle (`.tmp`, `.crdownload`).
        * Se o nome sumir entre uma leitura e outra (download
          cancelado e arquivo removido), o polling reinicia.
        * Só o **primeiro** arquivo novo e estável é retornado.
          Quem processa lotes (V2) deve continuar chamando com
          `known_files_before` atualizado até esgotar a fila.
    """
    folder = Path(folder)
    if not folder.exists() or not folder.is_dir():
        logger.warning(
            "wait_for_new_stable_file: pasta inexistente: %s", folder
        )
        return None

    if timeout_s <= 0:
        return None

    stable_checks = max(1, int(stable_checks))
    poll_interval_s = max(0.05, float(poll_interval_s))

    deadline = time.monotonic() + timeout_s
    last_size: int | None = None
    stable_count = 0

    while True:
        try:
            current = {
                entry.name: entry
                for entry in folder.iterdir()
                if entry.is_file()
            }
        except OSError as exc:
            logger.warning(
                "wait_for_new_stable_file: falha ao listar %s (%s): %s",
                folder,
                exc.__class__.__name__,
                exc,
            )
            current = {}

        new_entries = [
            path
            for name, path in current.items()
            if name not in known_files_before
        ]

        # Encontramos um candidato — agora esperamos o tamanho parar.
        candidate: Path | None = None
        for path in new_entries:
            try:
                size = path.stat().st_size
            except OSError:
                continue
            if size >= _MIN_SIZE_BYTES:
                candidate = path
                break

        if candidate is not None:
            try:
                size = candidate.stat().st_size
            except OSError:
                size = None

            if size is not None and size == last_size:
                stable_count += 1
            else:
                stable_count = 1
                # size pode ser None; nesse caso `last_size=None`
                # e o `if` abaixo não dispara o retorno.
                if size is not None:
                    last_size = size

            if size is not None and stable_count >= stable_checks:
                logger.info(
                    "wait_for_new_stable_file: novo arquivo estável: %s "
                    "(size=%d, estabilidade=%d checagens)",
                    candidate,
                    size,
                    stable_count,
                )
                return candidate

        # Sem candidato ou ainda não estável — checa timeout.
        if time.monotonic() >= deadline:
            logger.warning(
                "wait_for_new_stable_file: timeout (%.1fs) sem arquivo "
                "novo e estável em %s.",
                timeout_s,
                folder,
            )
            return None

        time.sleep(poll_interval_s)


__all__ = ["snapshot_files", "wait_for_new_stable_file"]
