"""Repositórios de domínio (Prompt 8).

Traduzem operações de domínio (criar Batch, atualizar ImageJob,
consultar histórico) em operações sobre o SQLite. Fornecem uma API
orientada a objetos para `core`, sem expor detalhes de SQL.

Cada repositório:
- Recebe um `DatabaseConnection` por injeção (mirror do
  `PromptPresetStore`).
- Conhece APENAS SQL + dataclass mapping. Nenhuma regra de negócio
  (idempotência, recovery, retry) mora aqui — quem decide é o
  `BatchProcessor` e o `Worker`.
- Pode ser instanciado várias vezes para o mesmo `DatabaseConnection`
  sem efeitos colaterais (os PRAGMAs de PRAGMA sticky são idempotentes).

PRAGMAs de SQLite:
    O `DatabaseConnection._open()` já ativa `PRAGMA foreign_keys = ON`
    por conexão. Para concorrência entre threads (teste 12 exige
    escritas simultâneas), ativamos `PRAGMA journal_mode = WAL` e
    `PRAGMA busy_timeout = 5000` — uma única vez, na primeira
    instanciação de qualquer repositório. Esses PRAGMAs são sticky
    (persistidos no header do arquivo), então funcionam mesmo depois
    de fechar e reabrir o banco.
"""

from __future__ import annotations

import logging

from app.data.database.connection import DatabaseConnection
from app.data.repositories.batch_repository import BatchRepository
from app.data.repositories.generation_attempt_repository import (
    GenerationAttemptRepository,
)
from app.data.repositories.image_job_repository import ImageJobRepository
from app.data.repositories.project_repository import ProjectRepository

logger = logging.getLogger(__name__)


_PRAGMAS_APPLIED: set[str] = set()


def _ensure_sqlite_pragmas(db: DatabaseConnection) -> None:
    """Ativa WAL + busy_timeout uma única vez por caminho de arquivo.

    Idempotente. `PRAGMA journal_mode = WAL` é sticky: depois de
    executado uma vez, o `.sqlite3` mantém o journal mode mesmo
    quando reaberto por outro processo / conexão. `PRAGMA
    busy_timeout` é per-connection, mas setamos em cada `conn()`
    nova via este hook single-shot.
    """
    path_key = str(db.path)
    if path_key in _PRAGMAS_APPLIED:
        return
    with db.conn() as c:
        # WAL: leituras concorrentes + escritas sem bloquearem leitor.
        c.execute("PRAGMA journal_mode = WAL")
        # busy_timeout: 5s antes de abortar com `database is locked`.
        c.execute("PRAGMA busy_timeout = 5000")
    _PRAGMAS_APPLIED.add(path_key)
    logger.info("PRAGMAs SQLite aplicados (WAL + busy_timeout=5000) em %s", path_key)


def reset_pragmas_cache() -> None:
    """Para testes: limpa o cache de PRAGMAs já aplicados.

    Útil em testes que abrem múltiplos arquivos temporários e
    querem garantir que cada um recebe seus PRAGMAs.
    """
    _PRAGMAS_APPLIED.clear()


__all__ = [
    "ProjectRepository",
    "BatchRepository",
    "ImageJobRepository",
    "GenerationAttemptRepository",
    "_ensure_sqlite_pragmas",
    "reset_pragmas_cache",
]
