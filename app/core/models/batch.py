"""Modelo de domínio `Batch` e tipos auxiliares (Prompt 5).

Aqui ficam apenas estruturas de dados puras:
- `BatchPlan`: planejamento AINDA NÃO confirmado (pré-visualização).
- `Batch`: entidade persistente, ainda sem operações reais no banco
  (Prompt 8 é quem formaliza o schema completo de Project/Batch/
  ImageJob/GenerationAttempt).
- `BatchStatus`: enumeração do estado de um Batch.

Importante: o BatchPlan NÃO toca o disco. O BatchSplitter é quem
depois materializa o plano em cópias — esta separação é o que permite
"mostrar uma prévia ANTES de qualquer alteração em disco" (regra 2 do
prompt).
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

logger = logging.getLogger(__name__)


def _new_uuid() -> str:
    return str(uuid.uuid4())


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class BatchStatus(str, Enum):
    """Status de um Batch ao longo do tempo.

    Apenas um subconjunto é exercitado neste prompt (a parte de
    processamento vem nos Prompts 7-8). Os outros valores já existem
    para que a transição seja trivial depois.
    """

    NOT_STARTED = "NOT_STARTED"
    IN_PROGRESS = "IN_PROGRESS"
    PAUSED = "PAUSED"
    COMPLETED = "COMPLETED"
    COMPLETED_WITH_ERRORS = "COMPLETED_WITH_ERRORS"
    CANCELLED = "CANCELLED"


@dataclass(frozen=True)
class BatchLot:
    """Representa UM lote dentro de um `BatchPlan`.

    Attributes:
        name: nome da subpasta (ex: "lote_001").
        folder: caminho absoluto da subpasta.
        source_paths: lista ordenada de arquivos a copiar (caminhos
            absolutos na pasta original).
    """

    name: str
    folder: Path
    source_paths: list[Path]


@dataclass(frozen=True)
class BatchPlan:
    """Planejamento de divisão em lotes, ANTES de qualquer cópia.

    Attributes:
        source_folder: pasta de entrada com os arquivos originais.
        batches_dir: pasta `lotes/` onde as cópias serão feitas.
        lots: lista ordenada de lotes.
        total_images: total de imagens no plano.
        batch_size: tamanho máximo do lote configurado.
    """

    source_folder: Path
    batches_dir: Path
    lots: list[BatchLot]
    total_images: int
    batch_size: int

    @property
    def total_batches(self) -> int:
        return len(self.lots)

    def total_source_bytes(self) -> int:
        total = 0
        for lot in self.lots:
            for src in lot.source_paths:
                try:
                    total += src.stat().st_size
                except OSError:
                    # Arquivo sumiu entre o scan e o split — não
                    # conseguimos estimar; ignoramos para o cálculo
                    # de espaço (regra 5: estimativa simples).
                    continue
        return total


@dataclass(frozen=True)
class Batch:
    """Entidade persistente (pré-forma do Prompt 8).

    Attributes:
        id: UUID v4 como string.
        project_id: FK para o Project (Prompt 8).
        name: nome visível (ex: "lote_001").
        folder_path: caminho absoluto da subpasta do lote.
        preset_id: FK para o preset de prompt ativo no momento do
            planejamento.
        status: estado do Batch (enum BatchStatus).
        created_at: timestamp de criação.
        source_total: total de imagens da fonte (para o caso de o
            preset mudar depois).
    """

    id: str = field(default_factory=_new_uuid)
    project_id: str = ""
    name: str = ""
    folder_path: Path = field(default_factory=Path)
    preset_id: str = ""
    status: BatchStatus = BatchStatus.NOT_STARTED
    created_at: datetime = field(default_factory=_utcnow)
    source_total: int = 0


__all__ = ["Batch", "BatchLot", "BatchPlan", "BatchStatus"]
