"""
logger.py
Configura logger raiz do projeto e expõe helper de log.
"""
from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from typing import Optional

from config import CONFIG, PROJECT_ROOT


_FORMAT = "[%(asctime)s] [%(levelname)s] %(name)s: %(message)s"
_DATEFMT = "%H:%M:%S"

_initialized = False


def _ensure_log_dir() -> None:
    log_file = CONFIG.get("log_file_resolved") or (PROJECT_ROOT / "logs" / "log.txt")
    log_file.parent.mkdir(parents=True, exist_ok=True)


def setup_logging(level: int = logging.INFO) -> logging.Logger:
    """Configura o logger raiz e retorna o logger do projeto."""
    global _initialized

    _ensure_log_dir()

    root = logging.getLogger()
    root.setLevel(level)

    # Remove handlers duplicados em caso de reconfiguração
    for h in list(root.handlers):
        root.removeHandler(h)

    formatter = logging.Formatter(_FORMAT, datefmt=_DATEFMT)

    log_file = CONFIG.get("log_file_resolved") or (PROJECT_ROOT / "logs" / "log.txt")

    file_handler = RotatingFileHandler(
        log_file, maxBytes=2_000_000, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(level)
    root.addHandler(file_handler)

    console_handler = logging.StreamHandler(stream=sys.stdout)
    console_handler.setFormatter(formatter)
    console_handler.setLevel(level)
    root.addHandler(console_handler)

    _initialized = True
    return logging.getLogger("image-batch")


def get_logger(name: Optional[str] = None) -> logging.Logger:
    if not _initialized:
        setup_logging()
    return logging.getLogger(name or "image-batch")
