"""Repositório de `ImageJob` (Prompt 8) — o central.

CRUD puro sobre a tabela `image_jobs`. Implementa também:

    * `find_successful_by_identity` — chave de idempotência. Busca
      um ImageJob com status SUCCESS e a tupla 4-campos
      `(input_hash, prompt_hash, model, parameters_hash)` igual à
      fornecida. O `BatchProcessor` complementa com validação de
      arquivo de saída via Pillow antes de reaproveitar.
    * `recover(batch_id)` — crash recovery. Reseta qualquer
      `ImageJob` em PROCESSING de volta para PENDING. Retorna o
      número de linhas alteradas.

`status` é armazenado como texto (nome do enum).
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from app.core.models import ImageJob, ImageJobStatus
from app.data.database.connection import DatabaseConnection
from app.data.repositories._helpers import _iso, _parse_iso

logger = logging.getLogger(__name__)


def _row_to_image_job(row) -> ImageJob:
    started_at = _parse_iso(row["started_at"]) if row["started_at"] else None
    completed_at = _parse_iso(row["completed_at"]) if row["completed_at"] else None
    return ImageJob(
        id=row["id"],
        batch_id=row["batch_id"],
        reference_image_path=Path(row["input_path"]),
        output_path=Path(row["output_path"]),
        prompt_text="",  # não persistido (vem do preset)
        model=row["model"],
        extra_parameters={},  # reconstruir a partir de parameters_hash
        input_hash=row["input_hash"],
        prompt_hash=row["prompt_hash"],
        parameters_hash=row["parameters_hash"],
        status=ImageJobStatus(row["status"]),
        attempts_count=row["attempts_count"],
        last_error_code=row["error_code"],
        last_error_message=row["error_message"],
        last_request_id=row["last_request_id"],
        created_at=_parse_iso(row["created_at"]),
        updated_at=_parse_iso(row["created_at"]),  # criado == updated no fresh load
        started_at=started_at,
        completed_at=completed_at,
    )


class ImageJobRepository:
    """Persistência de `ImageJob` em SQLite."""

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
        batch_id: str,
        input_path: Path | str,
        input_hash: str,
        prompt_hash: str,
        model: str,
        parameters_hash: str,
        output_path: Path | str,
        status: ImageJobStatus | str = ImageJobStatus.PENDING,
        attempts_count: int = 0,
        error_code: str = "",
        error_message: str = "",
        last_request_id: str = "",
        id: Optional[str] = None,
        created_at: Optional[datetime] = None,
        started_at: Optional[datetime] = None,
        completed_at: Optional[datetime] = None,
    ) -> ImageJob:
        """Cria um ImageJob. Levanta `IntegrityError` em FK inválida."""
        status_str = status.value if isinstance(status, ImageJobStatus) else str(status)
        job = ImageJob(
            id=id or str(uuid.uuid4()),
            batch_id=batch_id,
            reference_image_path=Path(input_path),
            output_path=Path(output_path),
            model=model,
            input_hash=input_hash,
            prompt_hash=prompt_hash,
            parameters_hash=parameters_hash,
            status=ImageJobStatus(status_str),
            attempts_count=attempts_count,
            last_error_code=error_code,
            last_error_message=error_message,
            last_request_id=last_request_id,
            created_at=created_at or datetime.now(timezone.utc),
            updated_at=created_at or datetime.now(timezone.utc),
            started_at=started_at,
            completed_at=completed_at,
        )
        with self._db.conn() as c:
            c.execute(
                """
                INSERT INTO image_jobs (
                    id, batch_id, input_path, input_hash, prompt_hash,
                    model, parameters_hash, output_path, status,
                    attempts_count, error_code, error_message, last_request_id,
                    created_at, started_at, completed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job.id,
                    job.batch_id,
                    str(job.reference_image_path),
                    job.input_hash,
                    job.prompt_hash,
                    job.model,
                    job.parameters_hash,
                    str(job.output_path),
                    status_str,
                    job.attempts_count,
                    job.last_error_code,
                    job.last_error_message,
                    job.last_request_id,
                    _iso(job.created_at),
                    _iso(job.started_at) if job.started_at else None,
                    _iso(job.completed_at) if job.completed_at else None,
                ),
            )
        logger.info(
            "ImageJob criado: %s (batch=%s, hash=%s…)",
            job.id,
            batch_id,
            input_hash[:8],
        )
        return job

    def update(self, job: ImageJob) -> ImageJob:
        """Full-row update por `job.id`. Usado após mudanças de estado.

        Não altera `created_at` (data de criação é imutável). O schema
        v003 não tem coluna `updated_at` em `image_jobs` — usamos
        `completed_at`/`started_at` para capturar o progresso.
        """
        with self._db.conn() as c:
            cur = c.execute(
                """
                UPDATE image_jobs SET
                    batch_id = ?, input_path = ?, input_hash = ?, prompt_hash = ?,
                    model = ?, parameters_hash = ?, output_path = ?, status = ?,
                    attempts_count = ?, error_code = ?, error_message = ?,
                    last_request_id = ?, started_at = ?, completed_at = ?
                WHERE id = ?
                """,
                (
                    job.batch_id,
                    str(job.reference_image_path),
                    job.input_hash,
                    job.prompt_hash,
                    job.model,
                    job.parameters_hash,
                    str(job.output_path),
                    job.status.value,
                    job.attempts_count,
                    job.last_error_code,
                    job.last_error_message,
                    job.last_request_id,
                    _iso(job.started_at) if job.started_at else None,
                    _iso(job.completed_at) if job.completed_at else None,
                    job.id,
                ),
            )
            if cur.rowcount == 0:
                raise LookupError(f"ImageJob {job.id} não encontrado")
        logger.info("ImageJob atualizado: %s (status=%s)", job.id, job.status.value)
        return job

    def delete(self, job_id: str) -> None:
        """Exclui o ImageJob. Cascateia para generation_attempts."""
        with self._db.conn() as c:
            cur = c.execute("DELETE FROM image_jobs WHERE id = ?", (job_id,))
            if cur.rowcount:
                logger.info("ImageJob excluído: %s", job_id)

    # ------------------------------------------------------------------ #
    # Leitura                                                             #
    # ------------------------------------------------------------------ #

    def get(self, job_id: str) -> Optional[ImageJob]:
        with self._db.conn() as c:
            cur = c.execute("SELECT * FROM image_jobs WHERE id = ?", (job_id,))
            row = cur.fetchone()
            return _row_to_image_job(row) if row else None

    def list_by_batch(self, batch_id: str) -> list[ImageJob]:
        """Jobs de um Batch, mais antigos primeiro (ordem de criação)."""
        with self._db.conn() as c:
            cur = c.execute(
                "SELECT * FROM image_jobs WHERE batch_id = ? ORDER BY created_at ASC",
                (batch_id,),
            )
            return [_row_to_image_job(r) for r in cur.fetchall()]

    def find_successful_by_identity(
        self,
        input_hash: str,
        prompt_hash: str,
        model: str,
        parameters_hash: str,
    ) -> Optional[ImageJob]:
        """Cache hit: o mais recente ImageJob SUCCESS com a tupla 4-campos.

        Usado pelo `BatchProcessor` para reaproveitar resultados
        anteriores. Retorna só o registro — a verificação de que o
        `output_path` continua válido (Pillow) é da camada de serviço.
        """
        with self._db.conn() as c:
            cur = c.execute(
                """
                SELECT * FROM image_jobs
                WHERE input_hash = ? AND prompt_hash = ?
                  AND model = ? AND parameters_hash = ?
                  AND status = ?
                ORDER BY completed_at DESC
                LIMIT 1
                """,
                (input_hash, prompt_hash, model, parameters_hash, ImageJobStatus.SUCCESS.value),
            )
            row = cur.fetchone()
            return _row_to_image_job(row) if row else None

    # ------------------------------------------------------------------ #
    # Crash recovery                                                      #
    # ------------------------------------------------------------------ #

    def recover(self, batch_id: str) -> int:
        """Reseta PROCESSING → PENDING para o batch.

        Cobre o caso: app morreu no meio de um lote. Jobs que estavam
        em PROCESSING ficam órfãos. No próximo start, eles voltam a
        PENDING e o `BatchProcessor` os reprocessa.

        Retorna o número de linhas alteradas (0 se nada precisava).
        """
        with self._db.conn() as c:
            cur = c.execute(
                """
                UPDATE image_jobs
                SET status = ?
                WHERE batch_id = ? AND status = ?
                """,
                (
                    ImageJobStatus.PENDING.value,
                    batch_id,
                    ImageJobStatus.PROCESSING.value,
                ),
            )
            recovered = cur.rowcount
        if recovered:
            logger.warning(
                "Crash recovery: %d job(s) órfão(s) em PROCESSING → PENDING (batch=%s)",
                recovered,
                batch_id,
            )
        return recovered


__all__ = ["ImageJobRepository"]
