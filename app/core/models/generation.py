"""Modelos de domínio do provider de geração de imagens (Prompt 6).

São as estruturas de dados que circulam entre a UI/orquestrador e
qualquer implementação de `ImageGenerationProvider`. Não conhecem o
SDK da OpenAI — só a forma do contrato.

A dataclass `GenerationError` embute o `error_code` da taxonomia do
projeto (definida em `app.core.exceptions`) para que a camada de
orquestração possa decidir retry/cancelamento sem precisar mapear
exceções manualmente.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path


# --------------------------------------------------------------------------- #
# ErrorCode — espelha a taxonomia de `app/core/exceptions`                     #
# --------------------------------------------------------------------------- #


# Lista canônica. Mantida como string-enum para interoperar com
# serialização (logs, JSON, etc.) sem precisar de IntEnum.
class ErrorCode:
    """Códigos estáveis de erro retornados pelo provider.

    Estes valores NÃO devem ser localizados — são a chave usada em
    logs e na decisão de retry automática. As mensagens em português
    vão no campo `message` de `GenerationError`.
    """

    AUTH = "ERR_AUTH"
    RATE_LIMIT = "ERR_RATE_LIMIT"
    TIMEOUT = "ERR_TIMEOUT"
    CONNECTION = "ERR_CONNECTION"
    SERVER = "ERR_SERVER"
    CONTENT_REJECTED = "ERR_CONTENT_REJECTED"
    INVALID_PARAMS = "ERR_INVALID_PARAMS"
    QUOTA_EXCEEDED = "ERR_QUOTA_EXCEEDED"
    LOCAL_IO = "ERR_LOCAL_IO"
    UNKNOWN = "ERR_UNKNOWN"


# Conjunto de códigos que o provider trata com retry automático
# (backoff exponencial). Tudo o mais falha IMEDIATAMENTE.
RETRYABLE_ERROR_CODES: frozenset[str] = frozenset({
    ErrorCode.RATE_LIMIT,
    ErrorCode.TIMEOUT,
    ErrorCode.CONNECTION,
    ErrorCode.SERVER,
})


# --------------------------------------------------------------------------- #
# Request / Result / Error                                                     #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class GenerationRequest:
    """Tudo o que um provider precisa para tentar uma geração.

    Attributes:
        reference_image_path: caminho da imagem de referência a ser
            enviada à API junto com o prompt (obrigatório para o
            endpoint `images.edit`).
        prompt_text: instrução em texto que guia a geração.
        model: identificador do modelo (vem de configuração, NÃO é
            fixado no provider). Valores típicos no SDK 2.50.0:
            "gpt-image-1", "dall-e-2".
        output_path: caminho ABSOLUTO onde a imagem resultante deve
            ser salva. O provider grava em `.part` ao lado e move
            atomicamente para este caminho após validar.
        extra_parameters: parâmetros opcionais suportados pela API
            real. Apenas os que existem na documentação do SDK
            atual. Ex.: `{"size": "1024x1024", "quality": "high",
            "response_format": "b64_json", "background": "auto",
            "output_format": "png"}`. Chaves desconhecidas pelo
            SDK são ignoradas (ou rejeitadas via validação) — ver
            `OpenAIImageGenerationProvider._filter_kwargs`.
        api_key: chave de API a ser usada. O provider concreto
            normalmente NÃO lê isto — recebe via `OpenAI(api_key=...)`
            já configurado — mas é mantido no contrato para que
            implementações alternativas (cache local, mock, outro
            provedor) possam usá-lo. Quando vazio, o provider
            assume que a chave foi injetada pelo construtor.
        max_retries: tentativas extras em erros retryable (default 2,
            ou seja 1 chamada + 2 retries = 3 totais).
        request_timeout_s: timeout da chamada HTTP ao provedor
            (default 60s).
    """

    reference_image_path: Path
    prompt_text: str
    model: str
    output_path: Path
    extra_parameters: dict[str, object] = field(default_factory=dict)
    api_key: str = ""
    max_retries: int = 2
    request_timeout_s: float = 60.0


@dataclass(frozen=True)
class GenerationError:
    """Erro estruturado retornado pelo provider.

    Attributes:
        code: um dos `ErrorCode` acima.
        message: mensagem amigável em português, pronta para a UI.
        retryable: conveniência — `True` se o provider tentaria de
            novo automaticamente (e já esgotou as tentativas), ou
            `False` se a falha é definitiva.
        http_status: status HTTP retornado pelo provedor, quando
            aplicável. Útil para diagnóstico.
        provider_code: código bruto da exceção do SDK (ex:
            "invalid_api_key", "rate_limit_exceeded"). Quando o
            provider não tem como extrair, fica vazio.
    """

    code: str
    message: str
    retryable: bool = False
    http_status: int | None = None
    provider_code: str = ""


@dataclass(frozen=True)
class GenerationResult:
    """Resultado de uma tentativa de geração.

    Semântica:
        * `success=True`  -> `output_path` aponta para um arquivo
          válido já no disco. `error` é None.
        * `success=False` -> `error` é um `GenerationError` com a
          classificação. `output_path` aponta para o `.part` que
          foi limpo (ou para o destino pretendido se a falha
          ocorreu antes da gravação).

    Attributes:
        success: ver acima.
        output_path: caminho do arquivo de saída (final ou tentado).
        model_used: modelo efetivamente usado (eco do request).
        duration_ms: tempo total da chamada, incluindo retries.
        request_id: identificador da requisição HTTP retornado pelo
            provedor (header `x-request-id` na OpenAI). Vazio
            quando a chamada não chegou a ser enviada.
        error: presente apenas em falhas.
        bytes_written: bytes gravados no arquivo final (0 se falha).
        attempts: quantas tentativas o provider fez (1 = sem retry).
    """

    success: bool
    output_path: Path
    model_used: str
    duration_ms: int
    request_id: str = ""
    error: GenerationError | None = None
    bytes_written: int = 0
    attempts: int = 1


# --------------------------------------------------------------------------- #
# GenerationAttempt — entidade persistente (Prompt 8)                         #
# --------------------------------------------------------------------------- #


def _new_uuid() -> str:
    return str(uuid.uuid4())


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class GenerationAttempt:
    """Registro de UMA tentativa de geração dentro de um `ImageJob`.

    Cada chamada ao provider (sucesso ou falha) gera um
    `GenerationAttempt`. O histórico é preservado mesmo que o job
    eventualmente tenha sucesso após várias falhas anteriores — é a
    "linha do tempo" de execução.

    Attributes:
        id: UUID v4 como string.
        image_job_id: FK para `ImageJob.id`.
        attempt_number: ordinal dentro do job (1, 2, 3…). Garantido
            único por `(image_job_id, attempt_number)` no banco.
        started_at: timestamp do início da chamada.
        finished_at: timestamp da conclusão (None se ainda em curso).
        success: True se a tentativa terminou em SUCCESS.
        error_code: código do `ErrorCode` quando `success=False`,
            vazio caso contrário.
        error_message: mensagem amigável quando `success=False`.
        http_status: status HTTP do provider, quando aplicável.
        duration_ms: duração total da tentativa em milissegundos.
    """

    id: str = field(default_factory=_new_uuid)
    image_job_id: str = ""
    attempt_number: int = 1
    started_at: datetime = field(default_factory=_utcnow)
    finished_at: datetime | None = None
    success: bool = False
    error_code: str = ""
    error_message: str = ""
    http_status: int | None = None
    duration_ms: int = 0


__all__ = [
    "ErrorCode",
    "RETRYABLE_ERROR_CODES",
    "GenerationRequest",
    "GenerationError",
    "GenerationResult",
    "GenerationAttempt",
]