"""Repositório de `GenerationAttempt` (Prompt 8).

Cada tentativa de geração (sucesso ou falha) é registrada. O
histórico é preservado mesmo após o job eventualmente terminar com
sucesso após N falhas — é a "linha do tempo" de execução.

`UNIQUE(image_job_id, attempt_number)` no schema garante que não
haja duas tentativas com o mesmo número para o mesmo job. O
provider layer é responsável por incrementar `attempt_number`.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from app.core.models import GenerationAttempt
from app.data.database.connection import DatabaseConnection
from app.data.repositories._helpers import _iso, _parse_iso

logger = logging.getLogger(__name__)


def _row_to_attempt(row) -> GenerationAttempt:
    finished_at = _parse_iso(row["finished_at"]) if row["finished_at"] else None
    return GenerationAttempt(
        id=row["id"],
        image_job_id=row["image_job_id"],
        attempt_number=row["attempt_number"],
        started_at=_parse_iso(row["started_at"]),
        finished_at=finished_at,
        success=bool(row["success"]),
        error_code=row["error_code"],
        error_message=row["error_message"],
        http_status=row["http_status"],
        duration_ms=row["duration_ms"],
    )


class GenerationAttemptRepository:
    """Persistência de `GenerationAttempt` em SQLite."""

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
        image_job_id: str,
        attempt_number: int,
        started_at: Optional[datetime] = None,
        finished_at: Optional[datetime] = None,
        success: bool = False,
        error_code: str = "",
        error_message: str = "",
        http_status: Optional[int] = None,
        duration_ms: int = 0,
        id: Optional[str] = None,
    ) -> GenerationAttempt:
        """Cria um registro de tentativa.

        Levanta `IntegrityError` em FK inválida (image_job_id) ou em
        colisão de `UNIQUE(image_job_id, attempt_number)`.
        """
        attempt = GenerationAttempt(
            id=id or str(uuid.uuid4()),
            image_job_id=image_job_id,
            attempt_number=attempt_number,
            started_at=started_at or datetime.now(timezone.utc),
            finished_at=finished_at,
            success=success,
            error_code=error_code,
            error_message=error_message,
            http_status=http_status,
            duration_ms=duration_ms,
        )
        with self._db.conn() as c:
            c.execute(
                """
                INSERT INTO generation_attempts (
                    id, image_job_id, attempt_number, started_at, finished_at,
                    success, error_code, error_message, http_status, duration_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    attempt.id,
                    attempt.image_job_id,
                    attempt.attempt_number,
                    _iso(attempt.started_at),
                    _iso(attempt.finished_at) if attempt.finished_at else None,
                    1 if attempt.success else 0,
                    attempt.error_code,
                    attempt.error_message,
                    attempt.http_status,
                    attempt.duration_ms,
                ),
            )
        logger.info(
            "GenerationAttempt #%d criado (job=%s, success=%s)",
            attempt_number,
            image_job_id,
            success,
        )
        return attempt

    # ------------------------------------------------------------------ #
    # Leitura                                                             #
    # ------------------------------------------------------------------ #

    def list_by_image_job(self, image_job_id: str) -> list[GenerationAttempt]:
        """Histórico completo de tentativas, ordenado por número."""
        with self._db.conn() as c:
            cur = c.execute(
                """
                SELECT * FROM generation_attempts
                WHERE image_job_id = ?
                ORDER BY attempt_number ASC
                """,
                (image_job_id,),
            )
            return [_row_to_attempt(r) for r in cur.fetchall()]

    def count_by_image_job(self, image_job_id: str) -> int:
        """Quantas tentativas já foram registradas para o job."""
        with self._db.conn() as c:
            cur = c.execute(
                "SELECT COUNT(*) FROM generation_attempts WHERE image_job_id = ?",
                (image_job_id,),
            )
            return int(cur.fetchone()[0])


__all__ = ["GenerationAttemptRepository"]
