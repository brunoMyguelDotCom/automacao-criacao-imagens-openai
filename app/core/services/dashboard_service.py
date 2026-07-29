"""Serviço de dashboard agregado do projeto (Prompt 9).

Consolida o progresso de TODOS os lotes de um projeto a partir do
SQLite (Prompt 8) — a UI apenas exibe os modelos aqui retornados,
sem fazer agregação própria.

Princípios:
    * `app/core/services` é puro Python (sem PySide6).
    * Toda agregação é feita via SQL (`GROUP BY status`) — a soma
      TOTAL = SUCCESS + PENDING + FAILED + PROCESSING + CANCELLED
      é garantida pela query, não por cálculo posterior na UI.
    * Nenhuma mutação aqui além de `retry_failed_job` (regra 5.2 do
      prompt: resetar um job FAILED → PENDING). Outras mutações
      (criar batch, criar job, atualizar status) continuam
      exclusivas dos repositórios.

Regra de "arquivo ausente" (regra 6 do prompt, decisão documentada):
    Quando um job está em SUCCESS mas o `output_path` foi removido
    do disco externamente, o serviço:

      1. NÃO mexe no status no banco (mantém SUCCESS — preserva
         histórico e respeita idempotência).
      2. Marca `output_available=False` no `JobHistoryEntry` que
         devolve ao dashboard. A UI exibe "arquivo indisponível".
      3. Na próxima execução de lote, o `BatchProcessor` (regra de
         idempotência do Prompt 8) já trata `output_path` inválido
         como cache miss → reprocessa o job, criando nova cobrança.
         Comportamento coerente: o dashboard NÃO reverte o status,
         mas a próxima execução enxerga que precisa reprocessar.

Por que não reverter automaticamente para PENDING? Revertera o
status no banco poderia ser visto pelo usuário como "o sistema
apagou meu sucesso" — a UI ficaria confusa. Manter SUCCESS + flag
"arquivo indisponível" é mais conservador: o dashboard reflete o
estado do banco, e a ação de reprocessamento parte do usuário
(explícita via "Tentar novamente") ou da próxima execução do lote.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterable

from app.core.models import (
    Batch,
    BatchStatus,
    ImageJob,
    ImageJobStatus,
)
from app.data.database.connection import DatabaseConnection
from app.data.repositories import (
    BatchRepository,
    ImageJobRepository,
)

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Dataclasses de retorno                                                       #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class JobHistoryEntry:
    """Uma entrada do histórico de gerações para a UI.

    Atributos derivados (computados pelo serviço, NÃO pelo Qt):
        output_available: True se o `output_path` existe no disco
            (a UI não precisa conferir de novo). False significa
            que o arquivo foi removido externamente — preservado
            aqui pela regra 6 do prompt.
        last_attempt_at: timestamp da última tentativa registrada
            no histórico (ou `None` se nunca houve tentativa).
        attempt_count: total de tentativas registradas em
            `generation_attempts` (não confundir com `attempts_count`
            do job, que é a contagem do último run).
    """

    job: ImageJob
    output_available: bool
    last_attempt_at: datetime | None
    attempt_count: int


@dataclass(frozen=True)
class BatchSummary:
    """Uma linha da tabela de lotes do dashboard."""

    batch: Batch
    total: int
    success: int
    failed: int
    pending: int
    processing: int
    cancelled: int
    percent_complete: int

    @property
    def is_done(self) -> bool:
        """Lote pronto (não há mais nada a processar)."""
        return self.pending == 0 and self.processing == 0


@dataclass(frozen=True)
class DashboardSummary:
    """Resumo consolidado do projeto (regra 1 do prompt).

    Invariante mantida pelo serviço (verificada em testes):
        total == success + pending + failed + processing + cancelled

    Construída via UMA única query `GROUP BY status` no SQL —
    a soma bate com TOTAL porque vem da mesma agregação, não de
    consultas separadas que poderiam ficar inconsistentes.
    """

    project_id: str
    total: int
    success: int
    pending: int
    failed: int
    processing: int
    cancelled: int

    @property
    def percent_complete(self) -> int:
        """0..100 — total de trabalhos que já saíram do estado pendente.

        Consideramos "concluído" SUCCESS + FAILED + CANCELLED (jobs
        que já terminaram, com sucesso ou não). PROCESSING fica
        fora — está em curso, não conta como progresso.
        """
        if self.total == 0:
            return 100
        done = self.success + self.failed + self.cancelled
        return int(round(100 * done / self.total))


@dataclass(frozen=True)
class DashboardSnapshot:
    """Pacote completo que o serviço devolve para a UI renderizar."""

    summary: DashboardSummary
    batches: list[BatchSummary]
    history: list[JobHistoryEntry]


# --------------------------------------------------------------------------- #
# Filtros do histórico                                                         #
# --------------------------------------------------------------------------- #


# Conjunto de status que fazem sentido como filtro da UI. Excluímos
# PAUSED (estado transitório raro) e PROCESSING (job em andamento —
# a UI provavelmente vai usar o evento live do processor para
# mostrar; o dashboard é uma fotografia).
HISTORY_FILTERABLE_STATUSES: frozenset[str] = frozenset({
    ImageJobStatus.SUCCESS.value,
    ImageJobStatus.FAILED.value,
    ImageJobStatus.PENDING.value,
})


# --------------------------------------------------------------------------- #
# Serviço                                                                       #
# --------------------------------------------------------------------------- #


class DashboardService:
    """Consultas agregadas do dashboard.

    Recebe um `DatabaseConnection` por injeção (mesmo padrão dos
    repositórios). Não mantém estado — toda chamada é uma fotografia
    nova do banco.
    """

    def __init__(self, db: DatabaseConnection) -> None:
        self._db = db
        self._batch_repo = BatchRepository(db)
        self._job_repo = ImageJobRepository(db)

    # ------------------------------------------------------------------ #
    # API pública                                                         #
    # ------------------------------------------------------------------ #

    def get_snapshot(
        self,
        project_id: str,
        *,
        history_filter: str | None = None,
        history_limit: int = 100,
    ) -> DashboardSnapshot:
        """Foto completa do projeto: summary + batches + history.

        Args:
            project_id: id do Project (Prompt 8).
            history_filter: filtro do histórico. Aceita um dos
                `ImageJobStatus.value` (SUCCESS/FAILED/PENDING) ou
                None para "todos".
            history_limit: máximo de entradas no histórico (default
                100; entradas são as mais recentes por
                `completed_at`/`updated_at`/`created_at` DESC).

        Returns:
            `DashboardSnapshot` com todos os contadores.
        """
        summary = self._build_summary(project_id)
        batches = self._build_batch_summaries(project_id)
        history = self._build_history(project_id, history_filter, history_limit)
        return DashboardSnapshot(summary=summary, batches=batches, history=history)

    def retry_failed_job(self, job_id: str) -> ImageJob:
        """Reseta um job FAILED → PENDING (regra 5.2 do prompt).

        Garante que:
            * O job só é resetado se estiver FAILED (regra do prompt
              — "Tentar novamente" é específico para FAILED).
            * O histórico de `GenerationAttempt` é PRESERVADO
              (verificado no teste 6).

        Returns:
            O `ImageJob` recarregado após o reset.

        Raises:
            LookupError: se o job não existir.
            ValueError: se o job não estiver em FAILED.
        """
        job = self._job_repo.get(job_id)
        if job is None:
            raise LookupError(f"ImageJob {job_id} não encontrado")
        if job.status is not ImageJobStatus.FAILED:
            raise ValueError(
                f"ImageJob {job_id} não está em FAILED (status={job.status.value})"
            )

        # Reset para PENDING limpando as marcas de falha. Não
        # apagamos `last_error_*` — o histórico do último erro
        # continua visível no dashboard até a próxima tentativa.
        reset = job.with_status(ImageJobStatus.PENDING)
        self._job_repo.update(reset)
        logger.warning(
            "Dashboard: retry solicitado para job %s — status resetado para PENDING "
            "(gera nova chamada à API)",
            job_id,
        )
        return reset

    # ------------------------------------------------------------------ #
    # Construção dos blocos                                               #
    # ------------------------------------------------------------------ #

    def _build_summary(self, project_id: str) -> DashboardSummary:
        """Agrega TODOS os jobs do projeto em UMA query SQL.

        A soma das categorias BATE com `total` por construção: a
        query produz `(status, count)` para CADA status, e o
        `total` é a soma desses counts. Não existe caminho onde a
        soma diverge — vem do mesmo GROUP BY.
        """
        with self._db.conn() as c:
            # Join image_jobs ↔ batches, filtrando por project_id.
            cur = c.execute(
                """
                SELECT ij.status AS status, COUNT(*) AS n
                FROM image_jobs ij
                JOIN batches b ON b.id = ij.batch_id
                WHERE b.project_id = ?
                GROUP BY ij.status
                """,
                (project_id,),
            )
            rows = cur.fetchall()

        counts = {row["status"]: int(row["n"]) for row in rows}
        success = counts.get(ImageJobStatus.SUCCESS.value, 0)
        pending = counts.get(ImageJobStatus.PENDING.value, 0)
        failed = counts.get(ImageJobStatus.FAILED.value, 0)
        processing = counts.get(ImageJobStatus.PROCESSING.value, 0)
        cancelled = counts.get(ImageJobStatus.CANCELLED.value, 0)
        # PAUSED existe no enum mas é raro; conta como pending na
        # agregação (ainda não terminou).
        paused = counts.get(ImageJobStatus.PAUSED.value, 0)

        total = success + pending + failed + processing + cancelled + paused

        return DashboardSummary(
            project_id=project_id,
            total=total,
            success=success,
            pending=pending + paused,
            failed=failed,
            processing=processing,
            cancelled=cancelled,
        )

    def _build_batch_summaries(self, project_id: str) -> list[BatchSummary]:
        """Uma linha por Batch, agregada via SQL."""
        batches = self._batch_repo.list_by_project(project_id)
        if not batches:
            return []

        summaries: list[BatchSummary] = []
        for batch in batches:
            with self._db.conn() as c:
                cur = c.execute(
                    """
                    SELECT status, COUNT(*) AS n
                    FROM image_jobs
                    WHERE batch_id = ?
                    GROUP BY status
                    """,
                    (batch.id,),
                )
                rows = cur.fetchall()

            counts = {row["status"]: int(row["n"]) for row in rows}
            success = counts.get(ImageJobStatus.SUCCESS.value, 0)
            pending = counts.get(ImageJobStatus.PENDING.value, 0)
            failed = counts.get(ImageJobStatus.FAILED.value, 0)
            processing = counts.get(ImageJobStatus.PROCESSING.value, 0)
            cancelled = counts.get(ImageJobStatus.CANCELLED.value, 0)
            paused = counts.get(ImageJobStatus.PAUSED.value, 0)
            total = (
                success + pending + failed + processing + cancelled + paused
            )
            done = success + failed + cancelled
            percent = int(round(100 * done / total)) if total else 100
            summaries.append(
                BatchSummary(
                    batch=batch,
                    total=total,
                    success=success,
                    failed=failed,
                    pending=pending + paused,
                    processing=processing,
                    cancelled=cancelled,
                    percent_complete=percent,
                )
            )
        return summaries

    def _build_history(
        self,
        project_id: str,
        history_filter: str | None,
        history_limit: int,
    ) -> list[JobHistoryEntry]:
        """Histórico dos ImageJobs mais recentes, com filtro opcional."""
        # Validação/normalização do filtro.
        if history_filter is not None and history_filter not in HISTORY_FILTERABLE_STATUSES:
            raise ValueError(
                f"Filtro de histórico inválido: {history_filter!r}. "
                f"Esperado um de {sorted(HISTORY_FILTERABLE_STATUSES)} ou None."
            )

        with self._db.conn() as c:
            if history_filter is None:
                cur = c.execute(
                    """
                    SELECT ij.* FROM image_jobs ij
                    JOIN batches b ON b.id = ij.batch_id
                    WHERE b.project_id = ?
                    ORDER BY ij.created_at DESC
                    LIMIT ?
                    """,
                    (project_id, history_limit),
                )
            else:
                cur = c.execute(
                    """
                    SELECT ij.* FROM image_jobs ij
                    JOIN batches b ON b.id = ij.batch_id
                    WHERE b.project_id = ? AND ij.status = ?
                    ORDER BY ij.created_at DESC
                    LIMIT ?
                    """,
                    (project_id, history_filter, history_limit),
                )
            rows = cur.fetchall()

        entries: list[JobHistoryEntry] = []
        for row in rows:
            job = self._job_repo.get(row["id"])
            if job is None:
                # Não deve acontecer, mas defensivo: linha órfã.
                continue
            entries.append(self._build_history_entry(job))
        return entries

    def _build_history_entry(self, job: ImageJob) -> JobHistoryEntry:
        """Monta um `JobHistoryEntry` com availability + last attempt."""
        output_available = _output_path_exists(job.output_path)
        last_attempt_at, attempt_count = self._last_attempt_info(job.id)
        return JobHistoryEntry(
            job=job,
            output_available=output_available,
            last_attempt_at=last_attempt_at,
            attempt_count=attempt_count,
        )

    def _last_attempt_info(self, job_id: str) -> tuple[datetime | None, int]:
        """Último timestamp de tentativa e total registrado.

        Retorna (None, 0) se o job nunca teve tentativa (ex:
        PENDING novo, ainda não rodou).
        """
        with self._db.conn() as c:
            cur = c.execute(
                """
                SELECT finished_at, started_at
                FROM generation_attempts
                WHERE image_job_id = ?
                ORDER BY attempt_number DESC
                LIMIT 1
                """,
                (job_id,),
            )
            row = cur.fetchone()
            count_cur = c.execute(
                "SELECT COUNT(*) FROM generation_attempts WHERE image_job_id = ?",
                (job_id,),
            )
            count = int(count_cur.fetchone()[0])

        if row is None:
            return None, 0
        # finished_at pode ser None se a tentativa estiver em curso.
        ts_str = row["finished_at"] or row["started_at"]
        if ts_str is None:
            return None, count
        from datetime import datetime as _dt

        return _dt.fromisoformat(ts_str), count


# --------------------------------------------------------------------------- #
# Helpers                                                                       #
# --------------------------------------------------------------------------- #


def _output_path_exists(path: Path) -> bool:
    """True se `path` aponta para um arquivo regular no disco.

    Falha silenciosa: se o caminho sumiu (Pillow não é chamado
    aqui — só checamos `exists`/`is_file`; a checagem completa
    com Pillow é feita pelo `BatchProcessor` antes de reprocessar).
    """
    try:
        return path.exists() and path.is_file()
    except OSError:
        return False


def summarize_history(
    entries: Iterable[JobHistoryEntry],
) -> dict[str, int]:
    """Helper para UI: contagem rápida por status dentro do histórico."""
    out: dict[str, int] = {
        ImageJobStatus.SUCCESS.value: 0,
        ImageJobStatus.FAILED.value: 0,
        ImageJobStatus.PENDING.value: 0,
    }
    for e in entries:
        out[e.job.status.value] = out.get(e.job.status.value, 0) + 1
    return out


__all__ = [
    "DashboardService",
    "DashboardSnapshot",
    "DashboardSummary",
    "BatchSummary",
    "JobHistoryEntry",
    "HISTORY_FILTERABLE_STATUSES",
    "summarize_history",
]
