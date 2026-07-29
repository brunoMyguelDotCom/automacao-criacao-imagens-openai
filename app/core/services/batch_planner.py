"""Planejamento da divisão de imagens em lotes (Prompt 5).

Responsabilidade ÚNICA: dado o total de imagens válidas e o tamanho
máximo do lote, calcular quantos lotes serão criados, quais arquivos
entram em cada um, e onde cada lote vai morar.

Não toca em disco. Não lê arquivos. Não consulta banco. A função
`plan()` é determinística e pura — recebe uma lista ordenada de
caminhos e devolve um `BatchPlan` que pode ser inspecionado antes de
qualquer ação destrutiva (regra 2 do prompt).

Quem executa as cópias é o `BatchSplitter` (batch_splitter.py).
"""

from __future__ import annotations

import logging
import math
from pathlib import Path
from typing import Sequence

from app.core.exceptions import InvalidParamsError
from app.core.models import BatchLot, BatchPlan

logger = logging.getLogger(__name__)


class BatchPlanner:
    """Calcula um `BatchPlan` a partir dos arquivos válidos.

    Uso típico:
        planner = BatchPlanner()
        plan = planner.plan(
            source_files=[p1, p2, ..., p100],
            source_folder=Path("/entrada"),
            batch_size=30,
        )
        # mostrar `plan` na UI, esperar confirmação do usuário...

        splitter = BatchSplitter()
        batches = splitter.split(plan, on_collision=...)
    """

    #: Pasta onde os lotes serão criados (relativa à pasta de entrada).
    BATCHES_SUBDIR = "lotes"

    #: Largura fixa do número no nome do lote (lote_001, lote_010, ...).
    _LOT_NAME_WIDTH = 3

    def plan(
        self,
        source_files: Sequence[Path],
        source_folder: Path,
        batch_size: int,
    ) -> BatchPlan:
        """Calcula o plano de divisão. Não toca em disco.

        Args:
            source_files: caminhos dos arquivos a dividir (na ordem em
                que aparecerão nos lotes). NÃO precisam existir — o
                planner não os lê; apenas os referencia.
            source_folder: pasta de entrada onde os originais vivem
                (e onde a subpasta `lotes/` será criada).
            batch_size: tamanho máximo de cada lote. Deve ser >= 1.

        Returns:
            `BatchPlan` com `total_batches == 0` quando não há
            arquivos. Caso contrário, lotes com tamanho `batch_size`,
            exceto o último (que pode ser menor).

        Raises:
            InvalidParamsError: se `batch_size < 1`.
        """
        if batch_size < 1:
            raise InvalidParamsError(
                f"Tamanho máximo de lote deve ser >= 1 (recebido: {batch_size})."
            )

        source_folder = Path(source_folder)
        batches_dir = source_folder / self.BATCHES_SUBDIR

        # Sequência é materializada em lista para podermos fatiar.
        # A ordem de entrada é a ordem dos lotes — esta é a
        # contrapartida da "ordem do scan" do Prompt 3.
        files: list[Path] = list(source_files)
        total = len(files)

        if total == 0:
            logger.info(
                "BatchPlanner: 0 imagens -> nenhum lote (pasta=%s)", source_folder
            )
            return BatchPlan(
                source_folder=source_folder,
                batches_dir=batches_dir,
                lots=[],
                total_images=0,
                batch_size=batch_size,
            )

        # ceil(total / batch_size) — usando math.ceil para evitar
        # divisão inteira de Python que trunca para baixo.
        n_batches = math.ceil(total / batch_size)
        lots: list[BatchLot] = []
        for i in range(n_batches):
            start = i * batch_size
            end = min(start + batch_size, total)
            slice_ = files[start:end]
            lot_name = f"lote_{i + 1:0{self._LOT_NAME_WIDTH}d}"
            lots.append(
                BatchLot(
                    name=lot_name,
                    folder=batches_dir / lot_name,
                    source_paths=list(slice_),
                )
            )

        logger.info(
            "BatchPlanner: %d imagens -> %d lotes (max=%d) em %s",
            total,
            n_batches,
            batch_size,
            batches_dir,
        )
        return BatchPlan(
            source_folder=source_folder,
            batches_dir=batches_dir,
            lots=lots,
            total_images=total,
            batch_size=batch_size,
        )


__all__ = ["BatchPlanner"]
