"""Repositório de `Batch` (Prompt 8).

CRUD puro sobre a tabela `batches`. `status` é armazenado como
texto (nome do enum) — a conversão enum↔string acontece aqui, e
nenhuma outra camada precisa saber.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from app.core.models import Batch, BatchStatus
from app.data.database.connection import DatabaseConnection
from app.data.repositories._helpers import _iso, _parse_iso

logger = logging.getLogger(__name__)


def _row_to_batch(row) -> Batch:
    return Batch(
        id=row["id"],
        project_id=row["project_id"],
        name=row["name"],
        folder_path=Path(row["folder_path"]),
        preset_id=row["preset_id"],
        status=BatchStatus(row["status"]),
        created_at=_parse_iso(row["created_at"]),
        source_total=row["source_total"],
    )


class BatchRepository:
    """Persistência de `Batch` em SQLite."""

    def __init__(self, db: DatabaseConnection) -> None:
        self._db = db
        from app.data.repositories import _ensure_sqlite_pragmas

        _ensure_sqlite_pragmas(db)

    # ------------------------------------------------------------------ #
    # Escrita                                                             #
    # ------------------------------------------------------------------ #

    def create(
        self,
        *,
        project_id: str,
        name: str,
        folder_path: Path | str,
        preset_id: str,
        status: BatchStatus | str = BatchStatus.NOT_STARTED,
        source_total: int = 0,
        id: Optional[str] = None,
        created_at: Optional[datetime] = None,
    ) -> Batch:
        """Cria um Batch. Levanta `sqlite3.IntegrityError` em FK inválida."""
        status_str = status.value if isinstance(status, BatchStatus) else str(status)
        batch = Batch(
            id=id or str(uuid.uuid4()),
            project_id=project_id,
            name=name,
            folder_path=Path(folder_path),
            preset_id=preset_id,
            status=BatchStatus(status_str),
            created_at=created_at or datetime.now(timezone.utc),
            source_total=source_total,
        )
        with self._db.conn() as c:
            c.execute(
                """
                INSERT INTO batches (
                    id, project_id, name, folder_path, preset_id, status,
                    created_at, source_total
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    batch.id,
                    batch.project_id,
                    batch.name,
                    str(batch.folder_path),
                    batch.preset_id,
                    status_str,
                    _iso(batch.created_at),
                    batch.source_total,
                ),
            )
        logger.info("Batch criado: %s (project=%s)", batch.id, project_id)
        return batch

    def update_status(self, batch_id: str, status: BatchStatus | str) -> Batch:
        """Atualiza o status de um Batch. Devolve o Batch recarregado."""
        status_str = status.value if isinstance(status, BatchStatus) else str(status)
        with self._db.conn() as c:
            cur = c.execute(
                "UPDATE batches SET status = ? WHERE id = ?",
                (status_str, batch_id),
            )
            if cur.rowcount == 0:
                raise LookupError(f"Batch {batch_id} não encontrado")
        result = self.get(batch_id)
        assert result is not None  # safe pós-update
        logger.info("Batch %s -> %s", batch_id, status_str)
        return result

    def delete(self, batch_id: str) -> None:
        """Exclui o Batch. Cascateia para image_jobs e generation_attempts."""
        with self._db.conn() as c:
            cur = c.execute("DELETE FROM batches WHERE id = ?", (batch_id,))
            if cur.rowcount:
                logger.info("Batch excluído: %s", batch_id)

    # ------------------------------------------------------------------ #
    # Leitura                                                             #
    # ------------------------------------------------------------------ #

    def get(self, batch_id: str) -> Optional[Batch]:
        with self._db.conn() as c:
            cur = c.execute("SELECT * FROM batches WHERE id = ?", (batch_id,))
            row = cur.fetchone()
            return _row_to_batch(row) if row else None

    def list_by_project(self, project_id: str) -> list[Batch]:
        """Batches de um projeto, mais recentes primeiro."""
        with self._db.conn() as c:
            cur = c.execute(
                "SELECT * FROM batches WHERE project_id = ? ORDER BY created_at DESC",
                (project_id,),
            )
            return [_row_to_batch(r) for r in cur.fetchall()]


__all__ = ["BatchRepository"]
