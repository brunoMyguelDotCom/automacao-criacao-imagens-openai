"""Conexão SQLite e aplicação do schema.

API mínima usada no Prompt 4 (e reutilizável no Prompt 8):
- `DatabaseConnection`: abre conexão, aplica o schema se necessário,
  expõe `conn()` (context manager) para uso transacional.
- `default_database_path()`: o mesmo caminho retornado por
  `app.config.paths.get_database_path()`.

Por que um simples wrapper e não um ORM:
    O app é pequeno, opera em processo único, e a fonte de verdade
    é o SQLite lido diretamente. Um ORM só esconderia o que já é
    simples e adicionaria uma dependência. Quando o schema crescer
    no Prompt 8, continuaremos com SQL explícito + dataclasses em
    `app.core.models`.

Schema migrations:
    O schema cresce incrementalmente. A versão registrada em
    `schema_version` é consultada e, para cada versão já aplicada,
    pulamos o SQL correspondente. O arquivo `migrations/` traz as
    instruções por versão.
"""

from __future__ import annotations

import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from app.config.paths import get_database_path
from app.config.resources import get_resource_path

logger = logging.getLogger(__name__)

# Caminho para o diretório `migrations/`. Em desenvolvimento fica
# em `app/data/database/migrations/` (ao lado deste .py). No
# build PyInstaller, PyInstaller preserva módulos `.py`, mas as
# migrations precisam estar acessíveis via filesystem — então
# listamos duas localizações e usamos a que existir.
_MIGRATIONS_DIR_CANDIDATES = (
    Path(__file__).parent / "migrations",
    get_resource_path("app", "data", "database", "migrations"),
)


def _resolve_migrations_dir() -> Path:
    for candidate in _MIGRATIONS_DIR_CANDIDATES:
        if candidate.exists():
            return candidate
    # fallback explícito para o primeiro candidato — o warning
    # abaixo indicará a inconsistência.
    return _MIGRATIONS_DIR_CANDIDATES[0]


_MIGRATIONS_DIR = _resolve_migrations_dir()


class DatabaseConnection:
    """Gerencia a conexão SQLite única por caminho de arquivo."""

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._initialized = False

    @property
    def path(self) -> Path:
        return self._path

    def _open(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _ensure_schema(self, conn: sqlite3.Connection) -> None:
        if self._initialized:
            return

        # 1. Garante que a tabela de versão existe.
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS schema_version (
                version INTEGER PRIMARY KEY,
                applied_at TEXT NOT NULL
            );
            """
        )

        # 2. Descobre a versão atual.
        cur = conn.execute("SELECT COALESCE(MAX(version), 0) FROM schema_version")
        current = int(cur.fetchone()[0])

        # 3. Aplica migrations incrementais em ordem.
        if not _MIGRATIONS_DIR.exists():
            logger.warning("Diretório de migrations ausente: %s", _MIGRATIONS_DIR)
        else:
            for path in sorted(_MIGRATIONS_DIR.glob("v*.sql")):
                version = int(path.stem.lstrip("v").split("_")[0])
                if version <= current:
                    continue
                sql = path.read_text(encoding="utf-8")
                conn.executescript(sql)
                conn.execute(
                    "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
                    (version, datetime.now(timezone.utc).isoformat()),
                )
                logger.info("Migration v%d aplicada (%s)", version, path.name)

        conn.commit()
        self._initialized = True
        logger.info("Schema SQLite em %s pronto (versão atual=%d)", self._path, current)

    @contextmanager
    def conn(self) -> Iterator[sqlite3.Connection]:
        connection = self._open()
        try:
            self._ensure_schema(connection)
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()


def default_database_path() -> Path:
    return get_database_path()


__all__ = ["DatabaseConnection", "default_database_path"]
