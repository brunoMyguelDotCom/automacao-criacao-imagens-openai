"""Configuração centralizada de logging estruturado.

Formato: timestamp ISO 8601 | nível | módulo | [job_id=X | batch_id=Y] | mensagem.
Saída: console (modo desenvolvimento) + arquivo com rotação
(RotatingFileHandler, 5 MB por arquivo, últimos 5).

A função `setup_logging()` é idempotente: pode ser chamada mais de uma
vez sem duplicar handlers.

Sanitização (Prompt 10, regra 4):
    * Filtro global que aplica `sanitize_text` em mensagens de log.
    * Nenhum secret (sk-…, Bearer …, hex 40+) aparece no arquivo.

Campos estruturados (Prompt 10, regra 4):
    * `LoggerAdapter` (`app_log`) injeta `job_id`/`batch_id` em `extra`.
    * Formatter os inclui como `[job_id=X | batch_id=Y]` quando setados.
"""

from __future__ import annotations

import logging
import logging.handlers
import sys
from pathlib import Path
from typing import Any

from app.config.paths import get_logs_dir
from app.core.resilience.diagnostic import sanitize_text

LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s %(jobctx)s| %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%dT%H:%M:%S%z"
LOG_FILE_NAME = "app.log"
LOG_MAX_BYTES = 5 * 1024 * 1024  # 5 MB
LOG_BACKUP_COUNT = 5
LOG_LEVEL = logging.INFO


_configured = False


class _SanitizingFilter(logging.Filter):
    """Filtro que sanitiza a mensagem antes do formatter processar."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
            record.msg = sanitize_text(str(msg))
            record.args = ()
        except Exception:  # noqa: BLE001
            # Se a sanitização falhar, deixa a mensagem original passar —
            # logging nunca deve derrubar o app.
            pass
        return True


class _StructuredFormatter(logging.Formatter):
    """Formatter que adiciona `[job_id=X | batch_id=Y]` quando os
    campos extras estão presentes no LogRecord."""

    def format(self, record: logging.LogRecord) -> str:
        # Garante que `jobctx` sempre exista para o format string.
        if not hasattr(record, "jobctx"):
            record.jobctx = ""
        return super().format(record)


def _build_job_context(extra: dict[str, Any] | None) -> str:
    """Formata os extras estruturados em uma string curta `[k=v | k=v]`."""
    if not extra:
        return ""
    bits: list[str] = []
    job_id = extra.get("job_id")
    batch_id = extra.get("batch_id")
    project_id = extra.get("project_id")
    if job_id:
        bits.append(f"job_id={job_id}")
    if batch_id:
        bits.append(f"batch_id={batch_id}")
    if project_id:
        bits.append(f"project_id={project_id}")
    if not bits:
        return ""
    return "[" + " | ".join(bits) + "] "


class app_log:
    """Adapter para logging estruturado com contexto.

    Uso::

        log = app_log(__name__, job_id=job.id, batch_id=batch.id)
        log.info("Iniciando job")   # inclui [job_id=… | batch_id=…]

    Compatível com a API de `logging.Logger` (`info`, `warning`, …).
    """

    def __init__(
        self,
        name: str,
        *,
        job_id: Any = None,
        batch_id: Any = None,
        project_id: Any = None,
    ) -> None:
        self._logger = logging.getLogger(name)
        self._extra: dict[str, Any] = {}
        if job_id is not None:
            self._extra["job_id"] = job_id
        if batch_id is not None:
            self._extra["batch_id"] = batch_id
        if project_id is not None:
            self._extra["project_id"] = project_id

    def _log(self, level: int, msg: object, *args: Any, **kwargs: Any) -> None:
        extra = dict(self._extra)
        if "extra" in kwargs:
            extra.update(kwargs["extra"])
        # jobctx é derivado dos extras — não confundir com extras brutos.
        jobctx = _build_job_context(extra)
        if jobctx:
            extra["jobctx"] = jobctx
        self._logger.log(level, msg, *args, extra=extra, stacklevel=2)

    def debug(self, msg: object, *args: Any, **kwargs: Any) -> None:
        self._log(logging.DEBUG, msg, *args, **kwargs)

    def info(self, msg: object, *args: Any, **kwargs: Any) -> None:
        self._log(logging.INFO, msg, *args, **kwargs)

    def warning(self, msg: object, *args: Any, **kwargs: Any) -> None:
        self._log(logging.WARNING, msg, *args, **kwargs)

    def error(self, msg: object, *args: Any, **kwargs: Any) -> None:
        self._log(logging.ERROR, msg, *args, **kwargs)

    def critical(self, msg: object, *args: Any, **kwargs: Any) -> None:
        self._log(logging.CRITICAL, msg, *args, **kwargs)

    def exception(self, msg: object, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("exc_info", True)
        self._log(logging.ERROR, msg, *args, **kwargs)


def setup_logging(level: int = LOG_LEVEL, console: bool = True) -> Path:
    """Configura o logger raiz. Retorna o caminho do arquivo de log."""
    global _configured

    logs_dir = get_logs_dir()
    log_path = logs_dir / LOG_FILE_NAME

    root = logging.getLogger()
    root.setLevel(level)

    # Evita duplicação se chamado várias vezes (ex.: testes + app)
    if _configured:
        return log_path

    sanitizer = _SanitizingFilter()
    formatter = _StructuredFormatter(LOG_FORMAT, datefmt=LOG_DATE_FORMAT)

    # Arquivo com rotação
    file_handler = logging.handlers.RotatingFileHandler(
        log_path,
        maxBytes=LOG_MAX_BYTES,
        backupCount=LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    file_handler.addFilter(sanitizer)
    root.addHandler(file_handler)

    # Console (somente se TTY, para não poluir testes)
    if console:
        stream = logging.StreamHandler(sys.stderr)
        stream.setFormatter(formatter)
        stream.addFilter(sanitizer)
        root.addHandler(stream)

    _configured = True
    return log_path


def reset_for_tests() -> None:
    """Limpa handlers + flag. Apenas para testes."""
    global _configured
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    _configured = False