"""Modelo de domínio `ImageJob` (Prompt 7 + Prompt 8).

Representa UMA imagem dentro de lote, com seu ciclo de vida
(pendente -> em processamento -> sucesso/falha/etc.) e os hashes
que definem sua identidade de idempotência.

Máquina de estados (exatamente a do prompt):

    PENDING -> PROCESSING -> SUCCESS
    PROCESSING -> PENDING      (erro recuperável, ainda há tentativas)
    PROCESSING -> FAILED       (erro não recuperável ou sem tentativas)
    PENDING -> PAUSED          (pausa solicitada antes de iniciar)
    PAUSED -> PENDING          (retomar)
    PENDING -> CANCELLED       (cancelamento solicitado)
    PROCESSING -> CANCELLED    (cancelamento solicitado — embora, na
                                prática, o job em PROCESSING termine
                                normalmente antes do cancelamento
                                valer; ver `BatchProcessor`)

Identidade de idempotência (Prompt 8):
    A tupla (input_hash, prompt_hash, model, parameters_hash) define
    unicamente um trabalho de geração. Mudou qualquer elemento → é
    um NOVO ImageJob (nova cobrança de API).

O `BatchProcessor` é quem dispara todas as transições. Esta
dataclass NÃO tem métodos que mudam estado — ela é apenas um
"snapshot" mutável (via `with_status` / `with_attempts_count_increment`).
Quem coordena é o serviço.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

logger = logging.getLogger(__name__)


def _new_uuid() -> str:
    return str(uuid.uuid4())


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ImageJobStatus(str, Enum):
    """Estado do job dentro da máquina do BatchProcessor.

    Valores:
        PENDING: ainda não processado nesta execução do lote.
        PROCESSING: o provider foi chamado e ainda não voltou.
        SUCCESS: gerou e gravou com sucesso (NUNCA é reprocessado
            na mesma execução).
        FAILED: falhou definitivamente — sem mais tentativas.
            O provider já esgotou retries automáticos antes disso.
        PAUSED: pausa solicitada antes do job iniciar. Não é
            estado "técnico" do provider — é apenas um marcador
            para a UI exibir corretamente.
        CANCELLED: cancelamento solicitado; o job não vai mais
            rodar.
    """

    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    PAUSED = "PAUSED"
    CANCELLED = "CANCELLED"


@dataclass
class ImageJob:
    """Uma imagem individual dentro de lote.

    Attributes:
        id: UUID v4 como string. Estável entre execuções.
        batch_id: id do `Batch` (Prompt 8) ao qual pertence.
        reference_image_path: arquivo de entrada (o original).
        output_path: onde o provider deve gravar a imagem gerada.
        prompt_text: prompt a ser usado (vem do preset).
        model: id do modelo a usar.
        extra_parameters: parâmetros opcionais para o provider.
        input_hash: SHA-256 do arquivo de referência.
        prompt_hash: SHA-256 do `prompt_text` (identidade do preset).
        parameters_hash: SHA-256 da serialização canônica de
            `extra_parameters` (ordem de chaves estável).
        status: estado atual na máquina.
        attempts_count: quantas vezes o provider foi chamado para
            este job (atualizado pelo `BatchProcessor`).
        last_error_code: `ErrorCode` da última falha (vazio se
            nunca falhou).
        last_error_message: mensagem amigável da última falha.
        last_request_id: `x-request-id` da última chamada (para
            suporte/debug).
        created_at: timestamp de criação.
        updated_at: timestamp da última mudança de estado.
        started_at: timestamp da primeira chamada ao provider
            (None enquanto PENDING).
        completed_at: timestamp do término (SUCCESS/FAILED/CANCELLED).
            None enquanto o job ainda não terminou.
    """

    id: str = field(default_factory=_new_uuid)
    batch_id: str = ""
    reference_image_path: Path = field(default_factory=Path)
    output_path: Path = field(default_factory=Path)
    prompt_text: str = ""
    model: str = ""
    extra_parameters: dict[str, object] = field(default_factory=dict)
    input_hash: str = ""
    prompt_hash: str = ""
    parameters_hash: str = ""
    status: ImageJobStatus = ImageJobStatus.PENDING
    attempts_count: int = 0
    last_error_code: str = ""
    last_error_message: str = ""
    last_request_id: str = ""
    created_at: datetime = field(default_factory=_utcnow)
    updated_at: datetime = field(default_factory=_utcnow)
    started_at: datetime | None = None
    completed_at: datetime | None = None

    # ------------------------------------------------------------------ #
    # Transições controladas — apenas com `replace` (dataclass mutável)  #
    # ------------------------------------------------------------------ #

    def with_status(
        self,
        new_status: ImageJobStatus,
        *,
        error_code: str | None = None,
        error_message: str | None = None,
        request_id: str | None = None,
        now: datetime | None = None,
    ) -> "ImageJob":
        """Devolve uma cópia do job com ``status`` atualizado.

        Atualiza também ``updated_at`` e, opcionalmente, os campos
        de erro/request_id. ``attempts_count`` NÃO é tocado aqui —
        use ``with_attempts_count_increment()``.

        Também mantém ``started_at``/``completed_at`` coerentes:
        ao entrar em PROCESSING, ``started_at`` é setado; ao
        terminar (SUCCESS/FAILED/CANCELLED), ``completed_at``.
        """
        ts = now or _utcnow()
        updates: dict[str, object] = {
            "status": new_status,
            "updated_at": ts,
        }
        if error_code is not None:
            updates["last_error_code"] = error_code
        if error_message is not None:
            updates["last_error_message"] = error_message
        if request_id is not None:
            updates["last_request_id"] = request_id
        # Marca o início da primeira execução real (se ainda não
        # marcado). Idempotente: se já está setado, mantém.
        if new_status is ImageJobStatus.PROCESSING and self.started_at is None:
            updates["started_at"] = ts
        # Marca o término do job (qualquer estado terminal).
        if new_status in (
            ImageJobStatus.SUCCESS,
            ImageJobStatus.FAILED,
            ImageJobStatus.CANCELLED,
        ):
            updates["completed_at"] = ts
        return replace(self, **updates)

    def with_attempts_count_increment(self, delta: int = 1) -> "ImageJob":
        """Devolve uma cópia com ``attempts_count`` acrescido de ``delta``.

        Por padrão, ``delta=1`` — uma chamada ao provider.
        Use ``delta>1`` quando o provider reportar internamente
        múltiplas tentativas (em `result.attempts`): o contador no
        ImageJob reflete o TOTAL de tentativas."""
        if delta < 1:
            raise ValueError("delta deve ser >= 1")
        return replace(
            self, attempts_count=self.attempts_count + delta, updated_at=_utcnow()
        )


__all__ = ["ImageJob", "ImageJobStatus"]