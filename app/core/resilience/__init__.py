"""Resilience layer (Prompt 10).

Tratamento defensivo de exceções em fronteiras (I/O, API, banco) +
handler global + diagnóstico. Tudo aqui é puro Python (sem PySide6),
exceto o módulo `exception_handler` que importa Qt lazy.
"""

from app.core.resilience.diagnostic import (
    DiagnosticBundle,
    sanitize_text,
    build_diagnostic_bundle,
)
from app.core.resilience.exception_handler import (
    install,
    uninstall,
    is_installed,
    report_exception,
    friendly_message,
    format_exception,
    reset_for_tests as reset_exception_handler_for_tests,
)
from app.core.resilience.io_safety import (
    safe_open_read,
    safe_open_write,
    safe_makedirs,
    safe_remove,
    safe_stat,
    IOResult,
)

__all__ = [
    "install",
    "uninstall",
    "is_installed",
    "report_exception",
    "friendly_message",
    "format_exception",
    "reset_exception_handler_for_tests",
    "DiagnosticBundle",
    "sanitize_text",
    "build_diagnostic_bundle",
    "safe_open_read",
    "safe_open_write",
    "safe_makedirs",
    "safe_remove",
    "safe_stat",
    "IOResult",
]