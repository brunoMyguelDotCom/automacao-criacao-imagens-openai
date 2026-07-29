"""Execução física da divisão em lotes (Prompt 5).

`BatchSplitter` é o único módulo que toca em disco para criar os
lotes. Recebe um `BatchPlan` já calculado e uma decisão do usuário
sobre colisões (lotes já existentes), e devolve a lista de entidades
`Batch` correspondentes ao que foi materializado.

Garantias:
    * **Idempotente**: rodar `split()` duas vezes seguidas com o mesmo
      `BatchPlan` não duplica arquivos nem quebra a numeração dos
      lotes. Os nomes vêm da posição no plano (lote_001, lote_002,
      ...), não da contagem de pastas no disco.
    * **Resumível**: se a cópia for interrompida no meio (energia,
      app fechado, processo morto), uma nova execução do `split()`
      detecta quais arquivos já estão corretos (comparando SHA-256
      do destino com o do original) e copia apenas o que falta. Nada
      é recopiado sem necessidade.
    * **Bit-perfect**: cada cópia é verificada por hash — qualquer
      divergência entre origem e destino é detectada e tratada como
      `LocalIOError`.

Não conhece UI. Não consulta banco. Não cria entidades persistentes
de verdade — o Prompt 8 é quem persiste `Batch` no SQLite. Aqui
devolvemos objetos `Batch` "em memória" para a UI exibir e (no
Prompt 7) alimentar o pipeline de processamento.
"""

from __future__ import annotations

import hashlib
import logging
import shutil
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from app.core.exceptions import InvalidParamsError, LocalIOError
from app.core.models import Batch, BatchLot, BatchPlan, BatchStatus

logger = logging.getLogger(__name__)


# Buffer de streaming para o hash. Mesmo tamanho do scanner — quem
# cuida do tamanho é o scanner; aqui só copiamos e verificamos.
_HASH_CHUNK = 256 * 1024


class CollisionResolution(str, Enum):
    """Decisão do usuário quando `lotes/lote_NNN/` já existe.

    Valores:
        OVERWRITE: remove o diretório existente e recria do zero.
            Usar com cuidado — o usuário está descartando trabalho.
        KEEP_AND_ADD: mantém o diretório como está e copia apenas
            os arquivos que estiverem faltando ou estiverem
            diferentes (medidos por SHA-256). Esta é a opção
            "resumível" e o default seguro.
        CANCEL: aborta toda a operação sem tocar em disco. A UI
            então mostra uma mensagem e fecha o diálogo.
    """

    OVERWRITE = "OVERWRITE"
    KEEP_AND_ADD = "KEEP_AND_ADD"
    CANCEL = "CANCEL"


@dataclass(frozen=True)
class SplitResult:
    """Resultado materializado de um `split()`.

    Attributes:
        batches: entidades `Batch` correspondentes aos lotes que
            foram efetivamente criados/atualizados no disco. A ORDEM
            é a mesma do `BatchPlan.lots`.
        cancelled: True quando o usuário escolheu `CANCEL` na decisão
            de colisão e nada foi modificado no disco.
        batches_total: total de lotes no plano (mesmo número do
            `BatchPlan.total_batches`).
    """

    batches: list[Batch]
    cancelled: bool
    batches_total: int

    @property
    def total_batches(self) -> int:
        return self.batches_total


# --------------------------------------------------------------------------- #
# Callback de colisão                                                          #
# --------------------------------------------------------------------------- #

# Função chamada quando o splitter encontra um diretório de lote já
# existente. Deve devolver uma `CollisionResolution`. Em produção é
# a UI; nos testes é um lambda.
CollisionCallback = "callable[[Path], CollisionResolution]"


class BatchSplitter:
    """Materializa um `BatchPlan` em cópias reais no disco.

    Uso:
        splitter = BatchSplitter()
        result = splitter.split(plan, on_collision=lambda d: CollisionResolution.KEEP_AND_ADD)

    Após `split()`, cada `Batch` retornado tem:
        * `id` novo (UUID v4)
        * `name` = nome do lote (ex: "lote_001")
        * `folder_path` = caminho absoluto do diretório criado
        * `preset_id` = o do `BatchPlan` (a definir; ver `split_with_preset`)
        * `status` = NOT_STARTED (o processamento vem no Prompt 7)
    """

    def split(
        self,
        plan: BatchPlan,
        on_collision,
        *,
        preset_id: str = "",
        project_id: str = "",
        copy_executor=None,
    ) -> SplitResult:
        """Cria/atualiza os diretórios de lote do plano.

        Args:
            plan: plano calculado por `BatchPlanner.plan()`. Deve ter
                `total_batches > 0` (chamadas com 0 imagens não devem
                chegar aqui — a UI deve detectar isso antes).
            on_collision: callable `Path -> CollisionResolution` que
                decide o que fazer quando o diretório do lote já
                existe. **Obrigatório** — não há decisão silenciosa.
            preset_id: id do preset selecionado no momento do
                planejamento. Vai para o `Batch` retornado (regra 4).
            project_id: id do projeto (Prompt 8). Vazio por enquanto.
            copy_executor: injeção opcional para testes — recebe
                `(src: Path, dst: Path) -> None`. Default usa
                `shutil.copy2`.

        Returns:
            `SplitResult` com os `Batch`es materializados.

        Raises:
            InvalidParamsError: se algum parâmetro essencial estiver
                ausente (ex: `on_collision` None).
            LocalIOError: erro de disco durante a cópia/verificação.
        """
        if on_collision is None:
            raise InvalidParamsError(
                "É obrigatório fornecer on_collision (nenhuma colisão pode ser decidida silenciosamente)."
            )
        if plan.total_batches == 0:
            # Nada a fazer — devolve resultado vazio sem tocar em disco.
            logger.info("BatchSplitter.split: plano vazio -> nada a fazer")
            return SplitResult(batches=[], cancelled=False, batches_total=0)

        copy_fn = copy_executor or self._default_copy

        # Criar a pasta `lotes/` apenas uma vez.
        plan.batches_dir.mkdir(parents=True, exist_ok=True)
        logger.info("Pasta de lotes garantida em %s", plan.batches_dir)

        batches: list[Batch] = []
        for lot in plan.lots:
            # Decisão de colisão POR LOTE — o usuário pode querer
            # sobrescrever um e manter outro.
            resolution = self._resolve_collision(lot, on_collision)
            if resolution is CollisionResolution.CANCEL:
                logger.warning(
                    "BatchSplitter: usuário cancelou no lote %s — abortando",
                    lot.name,
                )
                # Nada foi criado/atualizado antes do CANCEL porque
                # processamos lotes em ordem: o cancelamento impede
                # processar o lote ATUAL e os seguintes, mas os
                # anteriores já foram materializados.
                return SplitResult(
                    batches=batches,
                    cancelled=True,
                    batches_total=plan.total_batches,
                )

            if resolution is CollisionResolution.OVERWRITE:
                if lot.folder.exists():
                    shutil.rmtree(lot.folder)
                    logger.info("Lote %s: diretório existente removido (OVERWRITE)", lot.name)

            # Garante o diretório do lote (recriado após rmtree ou
            # criado pela primeira vez).
            lot.folder.mkdir(parents=True, exist_ok=True)

            copied, reused = self._materialize_lot(lot, copy_fn)

            batch = Batch(
                project_id=project_id,
                name=lot.name,
                folder_path=lot.folder,
                preset_id=preset_id,
                status=BatchStatus.NOT_STARTED,
                source_total=len(lot.source_paths),
            )
            logger.info(
                "Lote %s materializado: %d copiados, %d reutilizados (total=%d)",
                lot.name,
                copied,
                reused,
                len(lot.source_paths),
            )
            batches.append(batch)

        return SplitResult(
            batches=batches,
            cancelled=False,
            batches_total=plan.total_batches,
        )

    # ------------------------------------------------------------------ #
    # Helpers                                                            #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _resolve_collision(lot: BatchLot, on_collision) -> CollisionResolution:
        """Aplica o callback se a pasta já existir; devolve KEEP_AND_ADD
        como decisão padrão quando a pasta NÃO existe.

        Esta é a única "decisão" que o splitter toma sozinho — e ela é
        segura: se a pasta não existe, manter-e-adicionar é sinônimo
        de "criar do zero" (não há nada a remover).
        """
        if not lot.folder.exists():
            return CollisionResolution.KEEP_AND_ADD
        # Já existe — perguntar.
        try:
            resolution = on_collision(lot.folder)
        except Exception:
            # Qualquer erro no callback é tratado como CANCEL — é o
            # caminho mais conservador e nunca corrompe dados.
            logger.exception(
                "Callback de colisão falhou em %s — tratando como CANCEL",
                lot.folder,
            )
            return CollisionResolution.CANCEL
        if not isinstance(resolution, CollisionResolution):
            raise InvalidParamsError(
                f"on_collision deve devolver CollisionResolution, recebeu {type(resolution).__name__}"
            )
        return resolution

    @staticmethod
    def _materialize_lot(lot: BatchLot, copy_fn) -> tuple[int, int]:
        """Copia/verifica cada arquivo do lote. Devolve (copiados, reutilizados).

        Regras de idempotência/resumibilidade:
            * Se o destino NÃO existe -> copia.
            * Se o destino existe e o SHA-256 bate com o da origem
              -> nada a fazer (conta como "reutilizado").
            * Se o destino existe mas o hash DIFERE -> sobrescreve
              (a fonte de verdade é sempre o arquivo original).

        O nome do arquivo no destino é o basename do `source_path` —
        isso preserva a relação "1 original -> 1 cópia" mesmo que
        dois originais (em pastas diferentes, embora o scanner não
        recurse) tenham o mesmo nome.

        Erros de I/O durante a cópia são traduzidos para
        `LocalIOError`, MESMO quando o `copy_fn` foi injetado — é o
        contrato do splitter.
        """
        copied = 0
        reused = 0
        for src in lot.source_paths:
            dst = lot.folder / src.name
            try:
                if not dst.exists():
                    copy_fn(src, dst)
                    copied += 1
                    continue
                # Existe — verificar bit-perfect via hash.
                src_hash = _sha256_of(src)
                dst_hash = _sha256_of(dst)
                if src_hash == dst_hash:
                    reused += 1
                    continue
                # Hash divergente -> origem é a verdade, sobrescreve.
                logger.warning(
                    "Hash divergente em %s (src=%s dst=%s) — sobrescrevendo",
                    dst,
                    src_hash[:12],
                    dst_hash[:12],
                )
                copy_fn(src, dst)
                copied += 1
            except OSError as exc:
                raise LocalIOError(
                    f"Falha ao copiar {src.name} para {dst.parent}: "
                    f"{exc.strerror or exc.__class__.__name__}"
                ) from exc
        return copied, reused

    @staticmethod
    def _default_copy(src: Path, dst: Path) -> None:
        """Cópia padrão: `shutil.copy2` (preserva metadados).

        Erros de I/O aqui são traduzidos para `LocalIOError` para
        manter a tipagem do domínio — a UI não precisa conhecer
        `OSError`.
        """
        try:
            shutil.copy2(src, dst)
        except OSError as exc:
            raise LocalIOError(
                f"Falha ao copiar {src.name} para {dst.parent}: {exc.strerror or exc.__class__.__name__}"
            ) from exc


def _sha256_of(path: Path) -> str:
    """SHA-256 streaming de um arquivo. Retorna zeros se o arquivo sumir
    entre o scan e a cópia — a divergência será detectada e o
    `BatchSplitter` sobrescreverá.
    """
    h = hashlib.sha256()
    try:
        with path.open("rb") as fh:
            while True:
                chunk = fh.read(_HASH_CHUNK)
                if not chunk:
                    break
                h.update(chunk)
    except OSError:
        return "0" * 64
    return h.hexdigest()


__all__ = [
    "BatchSplitter",
    "CollisionResolution",
    "SplitResult",
]
