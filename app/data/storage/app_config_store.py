"""Configurações gerais persistentes (chave/valor).

Tabela `app_config` no SQLite. Apenas valores escalares — credenciais
sempre em outro lugar (keyring/arquivo criptografado).

Uso típico:
    store = AppConfigStore(db)
    max_batch = store.get_int("max_batch_size", default=20)
    store.set("max_batch_size", "30")
"""

from __future__ import annotations

import logging
from typing import Optional

from app.core.exceptions import AppError, InvalidParamsError
from app.data.database.connection import DatabaseConnection

logger = logging.getLogger(__name__)


class AppConfigError(AppError):
    error_code = "ERR_DB"


class AppConfigStore:
    """Ponto único de entrada para configurações gerais."""

    def __init__(self, db: DatabaseConnection) -> None:
        self._db = db

    # ------------------------------------------------------------------ #
    # Leitura                                                             #
    # ------------------------------------------------------------------ #

    def get(self, key: str, default: Optional[str] = None) -> Optional[str]:
        with self._db.conn() as c:
            cur = c.execute("SELECT value FROM app_config WHERE key = ?", (key,))
            row = cur.fetchone()
            return row["value"] if row else default

    def get_int(self, key: str, default: int = 0) -> int:
        raw = self.get(key)
        if raw is None:
            return default
        try:
            return int(raw)
        except ValueError as exc:
            raise AppConfigError(f"configuração '{key}' não é inteiro: {raw!r}") from exc

    # ------------------------------------------------------------------ #
    # Escrita                                                             #
    # ------------------------------------------------------------------ #

    def set(self, key: str, value: str) -> None:
        if not isinstance(value, str):
            raise InvalidParamsError("O valor deve ser string")
        with self._db.conn() as c:
            c.execute(
                """
                INSERT INTO app_config (key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (key, value),
            )
        logger.info("Config '%s' atualizada", key)

    def set_int(self, key: str, value: int) -> None:
        self.set(key, str(int(value)))

    def delete(self, key: str) -> None:
        with self._db.conn() as c:
            c.execute("DELETE FROM app_config WHERE key = ?", (key,))


# Chaves canônicas (constantes para evitar strings espalhadas).
KEY_MAX_BATCH_SIZE = "max_batch_size"


__all__ = ["AppConfigStore", "AppConfigError", "KEY_MAX_BATCH_SIZE"]