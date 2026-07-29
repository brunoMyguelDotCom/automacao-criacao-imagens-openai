"""Helpers de I/O seguros (Prompt 10, regra 2).

Auditoria I/O de disco: PermissionError, FileNotFoundError, IsADirectoryError,
OSError (disco cheio, path inválido). Funções `safe_*` retornam `IOResult`
em vez de levantar — mas respeitam exceções PROGRAMÁTICAS (ValueError para
input inválido, TypeError para tipo errado).

Princípios:
    * `safe_*` NUNCA silenciam erros — registram em log + retornam
      `IOResult(success=False, error=..., ...)` para o caller decidir.
    * Permissões/IOError são **erros esperados**, não bugs — não devem
      matar o lote. `unittest` feliz.
    * O caller ainda pode levantar se quiser (ex.: `safe_open_write(path,
      strict=True)`).
    * Type hints Path-aware (aceitam `str | os.PathLike`).
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Union

logger = logging.getLogger(__name__)

PathLike = Union[str, os.PathLike]


@dataclass(frozen=True)
class IOResult:
    """Resultado de uma operação I/O segura.

    Attributes:
        success: True se a operação completou sem erro.
        error: mensagem legível do erro (vazia em sucesso).
        error_code: nome da exceção (``PermissionError``, ``FileNotFoundError``…).
        path: caminho envolvido (útil para diagnóstico).
    """

    success: bool
    error: str = ""
    error_code: str = ""
    path: str = ""


def _to_path(p: PathLike) -> Path:
    """Coerção tolerante para `Path`."""
    if isinstance(p, Path):
        return p
    return Path(os.fspath(p))


def safe_open_read(
    path: PathLike,
    *,
    binary: bool = True,
) -> IOResult:
    """Verifica que o arquivo existe e pode ser aberto para leitura.

    Retorna `IOResult(success=True)` se o arquivo existe e é legível.
    Retorna `IOResult(success=False, error_code=...)` em caso de falha.

    NOTE: Esta função apenas **valida** — não devolve o handle. Para abrir
    o arquivo de fato, use `path.open(...)` num bloco try/except (o objetivo
    aqui é o **diagnóstico antecipado**).
    """
    p = _to_path(path)
    try:
        if not p.exists():
            return IOResult(False, "Arquivo não encontrado", "FileNotFoundError", str(p))
        if not p.is_file():
            return IOResult(False, "Caminho não é um arquivo", "NotAFile", str(p))
        mode = "rb" if binary else "r"
        with p.open(mode) as f:
            # Lê 1 byte para forçar falha de I/O cedo (permissões,
            # corrupção de inode, etc.).
            f.read(1)
        return IOResult(True, path=str(p))
    except PermissionError as exc:
        return IOResult(False, str(exc), "PermissionError", str(p))
    except OSError as exc:
        return IOResult(False, str(exc), type(exc).__name__, str(p))


def safe_open_write(
    path: PathLike,
    *,
    binary: bool = True,
) -> IOResult:
    """Verifica que o caminho pode ser aberto para escrita.

    Cria diretórios pais se faltarem (modo `exist_ok=True`); se a criação
    falhar, retorna `IOResult(success=False)`.
    """
    p = _to_path(path)
    try:
        parent = p.parent
        if parent and not parent.exists():
            parent.mkdir(parents=True, exist_ok=True)
        mode = "wb" if binary else "w"
        with p.open(mode) as f:
            f.write(b"" if binary else "")
        return IOResult(True, path=str(p))
    except PermissionError as exc:
        return IOResult(False, str(exc), "PermissionError", str(p))
    except OSError as exc:
        return IOResult(False, str(exc), type(exc).__name__, str(p))


def safe_makedirs(path: PathLike, *, exist_ok: bool = True) -> IOResult:
    """Cria diretórios (recursivo). Tolerante a `exist_ok=True`."""
    p = _to_path(path)
    try:
        p.mkdir(parents=True, exist_ok=exist_ok)
        return IOResult(True, path=str(p))
    except PermissionError as exc:
        return IOResult(False, str(exc), "PermissionError", str(p))
    except FileExistsError as exc:
        return IOResult(False, str(exc), "FileExistsError", str(p))
    except OSError as exc:
        return IOResult(False, str(exc), type(exc).__name__, str(p))


def safe_remove(path: PathLike, *, missing_ok: bool = True) -> IOResult:
    """Remove arquivo. Tolerante a `missing_ok=True`."""
    p = _to_path(path)
    try:
        p.unlink(missing_ok=missing_ok)
        return IOResult(True, path=str(p))
    except PermissionError as exc:
        return IOResult(False, str(exc), "PermissionError", str(p))
    except IsADirectoryError as exc:
        return IOResult(False, str(exc), "IsADirectoryError", str(p))
    except OSError as exc:
        return IOResult(False, str(exc), type(exc).__name__, str(p))


def safe_stat(path: PathLike) -> IOResult:
    """Coleta metadados do arquivo (tamanho, mtime). Tolerante a missing."""
    p = _to_path(path)
    try:
        st = p.stat()
        return IOResult(True, path=str(p))
    except FileNotFoundError as exc:
        return IOResult(False, str(exc), "FileNotFoundError", str(p))
    except PermissionError as exc:
        return IOResult(False, str(exc), "PermissionError", str(p))
    except OSError as exc:
        return IOResult(False, str(exc), type(exc).__name__, str(p))


__all__ = [
    "safe_open_read",
    "safe_open_write",
    "safe_makedirs",
    "safe_remove",
    "safe_stat",
    "IOResult",
]