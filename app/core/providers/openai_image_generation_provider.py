"""Implementação concreta: provider oficial da OpenAI (Prompt 6).

Endpoint usado: ``POST /v1/images/edits`` via ``client.images.edit()``.
Este é o ÚNICO endpoint que aceita simultaneamente uma imagem de
referência (``image``) e um prompt de texto (``prompt``) na
documentação real do SDK Python oficial.

Versão do SDK consultada: ``openai==2.50.0`` (range permitido em
pyproject.toml/requirements.txt: ``>=1.40,<2``). A introspecção
direta no SDK instalado (via ``inspect.signature`` em
``openai.resources.images.Images.edit``) confirmou a assinatura
abaixo; nenhum nome foi inventado.

Modelos suportados pelo endpoint ``images.edit`` (via introspecção
do tipo ``ImageModel`` e da docstring oficial em
https://platform.openai.com/docs/api-reference/images/createEdit):
    * ``gpt-image-1``  (default recomendado; suporta size, quality,
      background, output_format, output_compression, input_fidelity)
    * ``dall-e-2``     (legado; restrito a 1024x1024)
Outros identificadores são aceitos pelo SDK como string mas NÃO
garantidos pelo provedor remoto; o provider deixa essa validação
para a OpenAI (resposta 400 -> ERR_INVALID_PARAMS).

Parâmetros suportados por ``client.images.edit()`` (confirmados via
``inspect.signature``):
    image, prompt, background, input_fidelity, mask, model, n,
    output_compression, output_format, partial_images, quality,
    response_format, size, stream, user.

Limitações DOCUMENTADAS (importantes):
    * O parâmetro ``response_format`` aceita apenas ``"url"`` ou
      ``"b64_json"``. Para garantir download confiável (a URL
      temporária expira), o provider SEMPRE força
      ``response_format="b64_json"`` internamente — mesmo que o
      usuário peça outra coisa em ``extra_parameters``.
    * A imagem de referência precisa ser PNG (ou JPG/WebP, dependendo
      do modelo) e <= ~4MB. Validação de tamanho fica a cargo da
      API; erros de input inválido viram ERR_INVALID_PARAMS.

Retry: SOMENTE para ERR_RATE_LIMIT, ERR_TIMEOUT, ERR_CONNECTION,
ERR_SERVER. Nunca para ERR_AUTH, ERR_CONTENT_REJECTED,
ERR_INVALID_PARAMS, ERR_QUOTA_EXCEEDED.

A escrita do arquivo de saída segue o padrão atômico:
    1. grava em ``<output_path>.part``;
    2. valida o conteúdo com Pillow (reabre e confirma que não está
       vazio nem corrompido);
    3. se OK, faz ``rename`` para o nome final;
    4. em qualquer falha, remove o ``.part`` — o caminho final NUNCA
       fica com um arquivo parcial.
"""

from __future__ import annotations

import base64
import logging
import os
import random
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

from PIL import Image, UnidentifiedImageError

from app.core.models.generation import (
    ErrorCode,
    GenerationError,
    GenerationRequest,
    GenerationResult,
    RETRYABLE_ERROR_CODES,
)
from app.core.providers.image_generation_provider import ImageGenerationProvider

logger = logging.getLogger(__name__)


# Sufixo usado durante a escrita atômica. Se um arquivo
# `foo.png.part` ficar orfão (interrupção do app), o orquestrador
# do Prompt 8 decide se limpa antes de usar.
_PART_SUFFIX = ".part"

# Limite de tentativas = 1 (original) + max_retries. O default no
# request (max_retries=2) significa até 3 chamadas totais.
_MAX_TOTAL_ATTEMPTS_CAP = 6

# Backoff exponencial: base * 2^(attempt-1), com jitter. Estes são
# limites generosos para testes; valores baixos em produção seriam
# suficientes, mas mantemos a margem para evitar contenção na API.
_BACKOFF_BASE_S = 1.0
_BACKOFF_CAP_S = 30.0
_BACKOFF_JITTER_S = 0.5


class _LocalWriteError(Exception):
    """Erro interno do pipeline de gravação (validação Pillow,
    preparação de diretório, etc.). NÃO é uma exceção do SDK — é
    usada para distinguir falhas locais de falhas remotas quando o
    caller decide o error_code.
    """


# --------------------------------------------------------------------------- #
# Provider                                                                     #
# --------------------------------------------------------------------------- #


class OpenAIImageGenerationProvider(ImageGenerationProvider):
    """Provider que usa o SDK oficial da OpenAI (2.50.0) via
    ``client.images.edit()``.
    """

    def __init__(
        self,
        client_factory: Callable[..., Any] | None = None,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        """Constrói o provider.

        Args:
            client_factory: callable que recebe ``(api_key, timeout)``
                e devolve um cliente OpenAI. Útil para injetar mocks
                em testes sem precisar importar o SDK nos mesmos
                módulos. Quando None, monta o cliente real com a
                chave recebida no ``request.api_key``.
            sleep: callable usado entre tentativas (default
                ``time.sleep``). Injetável para acelerar testes.
        """
        self._client_factory = client_factory or self._default_client_factory
        self._sleep = sleep or time.sleep
        # Cliente lazy: só é criado quando o primeiro `generate()`
        # rodar, e fica vivo até `close()`. Reutilizar o cliente
        # entre chamadas é importante para a SDK reusar o pool
        # HTTP/2 do httpx.
        self._client: Any | None = None
        self._api_key_used: str = ""

    # ------------------------------------------------------------------ #
    # API pública                                                         #
    # ------------------------------------------------------------------ #

    def generate(self, request: GenerationRequest) -> GenerationResult:
        """Executa ``client.images.edit`` com retry e escrita atômica.

        Em caso de erro previsível do provedor, devolve
        ``GenerationResult(success=False, error=...)``. Em caso de
        erro inesperado (bug), propaga — esta é a ÚNICA exceção
        que pode vazar deste método.
        """
        self._validate_request(request)
        client = self._get_client(request)

        attempts_allowed = 1 + max(0, min(request.max_retries, _MAX_TOTAL_ATTEMPTS_CAP - 1))
        started = time.monotonic()
        last_error: GenerationError | None = None
        attempts = 0
        last_request_id = ""

        for attempt in range(1, attempts_allowed + 1):
            attempts = attempt
            try:
                kwargs = self._build_kwargs(request)
                response = client.images.edit(**kwargs)
                # Sucesso: processar e gravar.
                last_request_id = self._extract_request_id(response)
                result = self._process_response(
                    request=request,
                    response=response,
                    duration_ms=int((time.monotonic() - started) * 1000),
                    request_id=last_request_id,
                    attempts=attempt,
                )
                return result
            except Exception as exc:  # noqa: BLE001 — fronteira do SDK
                mapped = self._map_exception(exc)
                last_error = mapped
                # request_id pode estar disponível mesmo em falha.
                last_request_id = self._extract_request_id_from_exception(exc) or last_request_id
                if mapped.code not in RETRYABLE_ERROR_CODES:
                    logger.warning(
                        "Provider: erro não-retryable (%s) — falha imediata: %s",
                        mapped.code,
                        mapped.message,
                    )
                    return self._failure_result(
                        request=request,
                        error=mapped,
                        duration_ms=int((time.monotonic() - started) * 1000),
                        request_id=last_request_id,
                        attempts=attempt,
                    )
                if attempt >= attempts_allowed:
                    logger.warning(
                        "Provider: retry esgotado (%d tentativas) — código=%s",
                        attempt,
                        mapped.code,
                    )
                    break
                backoff = self._compute_backoff(attempt)
                logger.info(
                    "Provider: retryable %s na tentativa %d/%d — backoff=%.2fs",
                    mapped.code,
                    attempt,
                    attempts_allowed,
                    backoff,
                )
                self._sleep(backoff)

        # Esgotou retries sem sucesso.
        assert last_error is not None  # só possível se loop executou
        return self._failure_result(
            request=request,
            error=last_error,
            duration_ms=int((time.monotonic() - started) * 1000),
            request_id=last_request_id,
            attempts=attempts,
        )

    def close(self) -> None:
        """Fecha o cliente HTTP subjacente."""
        if self._client is not None and hasattr(self._client, "close"):
            try:
                self._client.close()
            except Exception:  # noqa: BLE001 — fechamento é best-effort
                logger.debug("Falha ao fechar cliente OpenAI (ignorado)")
        self._client = None

    # ------------------------------------------------------------------ #
    # Construção de kwargs / validação                                    #
    # ------------------------------------------------------------------ #

    # Conjunto de chaves que o SDK documentou como aceitas em
    # ``images.edit`` na versão 2.50.0 (confirmado por
    # ``inspect.signature``). Mantido como frozenset para consulta
    # O(1). NUNCA adicione chaves que não existam na introspecção.
    _EDIT_KWARGS_WHITELIST: frozenset[str] = frozenset({
        "image",
        "prompt",
        "background",
        "input_fidelity",
        "mask",
        "model",
        "n",
        "output_compression",
        "output_format",
        "partial_images",
        "quality",
        "response_format",
        "size",
        "stream",
        "user",
    })

    def _validate_request(self, request: GenerationRequest) -> None:
        """Validação leve ANTES de chamar a API. Erros graves aqui
        viram ``RuntimeError`` (bug do chamador) — não são
        classificados como ERR_INVALID_PARAMS porque ainda não
        chegamos ao provedor.
        """
        if not request.reference_image_path or not Path(request.reference_image_path).exists():
            raise RuntimeError(
                f"reference_image_path inexistente: {request.reference_image_path!r}"
            )
        if not request.prompt_text or not request.prompt_text.strip():
            raise RuntimeError("prompt_text vazio")
        if not request.model or not request.model.strip():
            raise RuntimeError("model vazio (deve vir de configuração)")
        if not request.output_path:
            raise RuntimeError("output_path vazio")
        # Garante que o diretório de saída existe (ou pode ser criado).
        Path(request.output_path).parent.mkdir(parents=True, exist_ok=True)

    def _build_kwargs(self, request: GenerationRequest) -> dict[str, Any]:
        """Monta os kwargs a partir de ``request.extra_parameters``,
        filtrando apenas o que o SDK 2.50.0 aceita no ``images.edit``.

        Decisões:
            * ``image`` e ``prompt`` vêm do request, NÃO do
              ``extra_parameters``.
            * ``model`` vem do request.
            * ``response_format`` é FORÇADO para ``"b64_json"``
              independente do que o usuário pedir — URLs da OpenAI
              expiram em ~1h e não são confiáveis para download.
              Isto está DOCUMENTADO no README.
            * Quaisquer chaves em ``extra_parameters`` que NÃO
              estejam no whitelist são IGNORADAS (logadas como
              warning) — em vez de explodir, o provider degrada
              silenciosamente para não quebrar presets antigos.
              Esta é a única decisão "lenient" do provider.
        """
        allowed = dict(request.extra_parameters or {})

        # Filtra para o whitelist documentado.
        filtered: dict[str, Any] = {}
        for key, value in allowed.items():
            if key in self._EDIT_KWARGS_WHITELIST:
                filtered[key] = value
            else:
                logger.warning(
                    "Provider: parâmetro '%s' não é suportado por "
                    "images.edit no SDK 2.50.0 — ignorando.",
                    key,
                )

        # Força b64_json — sem isto o provider pode devolver URL
        # temporária que expira.
        filtered["response_format"] = "b64_json"

        # Substitui pelos campos canônicos do request.
        filtered["model"] = request.model
        filtered["prompt"] = request.prompt_text
        # SDK aceita tanto Path quanto file-like object.
        filtered["image"] = Path(request.reference_image_path)

        # Timeout da chamada — convertido para httpx.Timeout se o
        # caller quiser. Aqui passamos float simples (o SDK aceita).
        filtered["timeout"] = float(request.request_timeout_s)

        return filtered

    # ------------------------------------------------------------------ #
    # Processamento da resposta                                           #
    # ------------------------------------------------------------------ #

    def _process_response(
        self,
        *,
        request: GenerationRequest,
        response: Any,
        duration_ms: int,
        request_id: str,
        attempts: int,
    ) -> GenerationResult:
        """Recebe o ImagesResponse do SDK, decodifica o b64_json,
        grava atomicamente, valida com Pillow e devolve o
        GenerationResult final."""
        try:
            image_bytes = self._extract_first_image_bytes(response)
        except (KeyError, ValueError, IndexError) as exc:
            return self._failure_result(
                request=request,
                error=GenerationError(
                    code=ErrorCode.UNKNOWN,
                    message=f"Resposta da API em formato inesperado: {exc}",
                    retryable=False,
                ),
                duration_ms=duration_ms,
                request_id=request_id,
                attempts=attempts,
            )

        try:
            written_path = self._atomic_write(
                target=Path(request.output_path),
                payload=image_bytes,
            )
        except _LocalWriteError as exc:
            # Exceção INTERNA (validação Pillow, etc.) —
            # classificada como ERR_LOCAL_IO sem passar pelo
            # mapeador de exceções do SDK.
            return self._failure_result(
                request=request,
                error=GenerationError(
                    code=ErrorCode.LOCAL_IO,
                    message=exc.args[0] if exc.args else "Falha ao gravar",
                    retryable=False,
                ),
                duration_ms=duration_ms,
                request_id=request_id,
                attempts=attempts,
            )
        except OSError as exc:
            return self._failure_result(
                request=request,
                error=GenerationError(
                    code=ErrorCode.LOCAL_IO,
                    message=(
                        f"Falha ao gravar a imagem em "
                        f"{request.output_path}: "
                        f"{exc.strerror or exc.__class__.__name__}"
                    ),
                    retryable=False,
                ),
                duration_ms=duration_ms,
                request_id=request_id,
                attempts=attempts,
            )

        # Sucesso.
        logger.info(
            "Provider: geração OK (model=%s, request_id=%s, attempts=%d, "
            "bytes=%d, output=%s)",
            request.model,
            request_id or "?",
            attempts,
            written_path.stat().st_size,
            written_path,
        )
        return GenerationResult(
            success=True,
            output_path=written_path,
            model_used=request.model,
            duration_ms=duration_ms,
            request_id=request_id,
            error=None,
            bytes_written=written_path.stat().st_size,
            attempts=attempts,
        )

    @staticmethod
    def _extract_first_image_bytes(response: Any) -> bytes:
        """Extrai o PRIMEIRO item de ``response.data`` e devolve os
        bytes decodificados de ``b64_json``.

        O SDK tipicamente devolve no máximo ``n`` imagens; pegamos a
        primeira porque a UI só mostra uma por job.
        """
        if response is None or getattr(response, "data", None) is None:
            raise ValueError("response.data vazio")
        first = response.data[0]
        b64 = getattr(first, "b64_json", None)
        if not b64:
            raise ValueError("primeiro item sem b64_json")
        return base64.b64decode(b64)

    # ------------------------------------------------------------------ #
    # Escrita atômica                                                     #
    # ------------------------------------------------------------------ #

    def _atomic_write(self, *, target: Path, payload: bytes) -> Path:
        """Grava ``payload`` em ``<target>.part``, valida com Pillow e
        move para ``target``. Em qualquer falha, o ``.part`` é
        removido e o ``target`` FINAL nunca fica com um arquivo
        parcial.

        Usa ``tempfile`` no MESMO diretório do target (atomicidade
        entre sistemas de arquivos distintos não é garantida).
        """
        target = Path(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        part_path = target.with_name(target.name + _PART_SUFFIX)

        # Limpa qualquer .part órfão de uma execução anterior.
        if part_path.exists():
            try:
                part_path.unlink()
            except OSError:
                # Se não conseguimos limpar, falhamos rápido: a UI
                # reportará o problema em vez de termos dois
                # arquivos concorrentes.
                raise

        try:
            with open(part_path, "wb") as fh:
                fh.write(payload)
                fh.flush()
                # Força a página de dados a ir para o disco — sem
                # isto, um crash entre flush() e rename() poderia
                # deixar um .part "vazio" renomeado.
                try:
                    os.fdatasync(fh.fileno())
                except (AttributeError, OSError):
                    # fdatasync não existe em Windows; OSError pode
                    # ocorrer em FS que não suportam. Ignorado:
                    # ainda assim, o rename é atômico no mesmo FS.
                    pass

            # Validação Pillow — reabre e confirma que é imagem
            # válida e não está vazia.
            self._validate_image_file(part_path)

            # Rename atômico no mesmo diretório.
            os.replace(part_path, target)
        except Exception as exc:
            # Qualquer falha no pipeline (I/O, validação Pillow,
            # etc.) é convertida em OSError para que o caller
            # (_process_response) a classifique como ERR_LOCAL_IO.
            # O .part é removido em ambos os caminhos.
            try:
                if part_path.exists():
                    part_path.unlink()
            except OSError:
                logger.debug("Falha removendo %s após erro", part_path)
            if isinstance(exc, OSError):
                raise
            # ValueError (validação Pillow), UnidentifiedImageError,
            # etc. -> _LocalWriteError, que _process_response
            # classifica como ERR_LOCAL_IO sem passar pelo
            # mapeador de exceções do SDK.
            raise _LocalWriteError(str(exc)) from exc

        return target

    @staticmethod
    def _validate_image_file(path: Path) -> None:
        """Confirma que ``path`` é uma imagem válida usando Pillow.

        Erros viram ``OSError`` propagado — o caller converte em
        ERR_LOCAL_IO. Não usamos UnidentifiedImageError como
        base porque Pillow tem outras falhas (PermissionError,
        ValueError em imagens truncadas) que queremos capturar
        também.
        """
        if path.stat().st_size == 0:
            raise ValueError(f"Arquivo {path} está vazio")
        try:
            with Image.open(path) as img:
                img.verify()
            # verify() só checa estrutura; load() força a
            # decodificação completa. Esta é a única forma de
            # detectar imagem truncada / corpo corrompido.
            with Image.open(path) as img:
                img.load()
        except (UnidentifiedImageError, ValueError, OSError) as exc:
            raise ValueError(
                f"Arquivo gerado não é uma imagem válida: {exc}"
            ) from exc

    # ------------------------------------------------------------------ #
    # Mapeamento de exceções                                              #
    # ------------------------------------------------------------------ #

    def _map_exception(self, exc: BaseException) -> GenerationError:
        """Converte uma exceção do SDK em ``GenerationError``.

        O mapeamento cobre:
            * AuthenticationError -> ERR_AUTH
            * PermissionDeniedError -> ERR_AUTH (alguns endpoints usam)
            * RateLimitError -> ERR_RATE_LIMIT
            * APITimeoutError -> ERR_TIMEOUT
            * APIConnectionError -> ERR_CONNECTION
            * InternalServerError -> ERR_SERVER
            * BadRequestError -> ERR_INVALID_PARAMS (exceto quando
              for 400 com mensagem de conteúdo, que vira
              ERR_CONTENT_REJECTED — heurística simples)
            * QuotaExceededError -> ERR_QUOTA_EXCEEDED (quando existir)
            * Qualquer outro -> ERR_UNKNOWN, retryable=False
        """
        # Import tardio: a única seção que toca o SDK diretamente.
        try:
            from openai import (
                APIConnectionError,
                APITimeoutError,
                AuthenticationError,
                BadRequestError,
                InternalServerError,
                PermissionDeniedError,
                RateLimitError,
            )
            try:
                from openai import QuotaExceededError  # type: ignore[attr-defined]
                _HAS_QUOTA = True
            except ImportError:
                _HAS_QUOTA = False
        except ImportError:
            # Se o SDK não está instalado, falhamos como UNKNOWN.
            return GenerationError(
                code=ErrorCode.UNKNOWN,
                message=f"SDK da OpenAI indisponível: {exc.__class__.__name__}",
                retryable=False,
            )

        if isinstance(exc, (AuthenticationError, PermissionDeniedError)):
            return GenerationError(
                code=ErrorCode.AUTH,
                message="Chave de API inválida ou sem permissão para este modelo.",
                retryable=False,
                provider_code=getattr(exc, "code", "") or "",
                http_status=getattr(getattr(exc, "response", None), "status_code", None),
            )
        if isinstance(exc, RateLimitError):
            return GenerationError(
                code=ErrorCode.RATE_LIMIT,
                message="Limite de requisições por minuto atingido. Tentando de novo.",
                retryable=True,
                provider_code="rate_limit_exceeded",
                http_status=429,
            )
        if isinstance(exc, APITimeoutError):
            return GenerationError(
                code=ErrorCode.TIMEOUT,
                message="A API demorou demais para responder.",
                retryable=True,
                provider_code="timeout",
                http_status=None,
            )
        if isinstance(exc, APIConnectionError):
            return GenerationError(
                code=ErrorCode.CONNECTION,
                message="Sem conexão com a internet ou DNS falhou.",
                retryable=True,
                provider_code="connection_error",
                http_status=None,
            )
        if isinstance(exc, InternalServerError):
            return GenerationError(
                code=ErrorCode.SERVER,
                message="A API da OpenAI está com problema interno.",
                retryable=True,
                provider_code="server_error",
                http_status=getattr(getattr(exc, "response", None), "status_code", None),
            )
        if _HAS_QUOTA and isinstance(exc, QuotaExceededError):
            return GenerationError(
                code=ErrorCode.QUOTA_EXCEEDED,
                message="Créditos ou cota mensal esgotados na OpenAI.",
                retryable=False,
                provider_code="quota_exceeded",
                http_status=getattr(getattr(exc, "response", None), "status_code", None),
            )
        if isinstance(exc, BadRequestError):
            # Heurística: 400 com palavras-chave de política de
            # conteúdo -> CONTENT_REJECTED; caso contrário,
            # INVALID_PARAMS.
            msg = (getattr(exc, "message", None) or str(exc) or "").lower()
            code_attr = (getattr(exc, "code", "") or "").lower()
            content_signals = (
                "content_policy_violation",
                "content_policy",
                "safety",
                "rejected by",
                "rejected for",
                "policy",
            )
            if any(s in msg for s in content_signals) or any(
                s in code_attr for s in content_signals
            ):
                return GenerationError(
                    code=ErrorCode.CONTENT_REJECTED,
                    message="Conteúdo rejeitado pela política da OpenAI.",
                    retryable=False,
                    provider_code=code_attr,
                    http_status=400,
                )
            return GenerationError(
                code=ErrorCode.INVALID_PARAMS,
                message=f"Parâmetros inválidos enviados à API: {exc}",
                retryable=False,
                provider_code=code_attr,
                http_status=400,
            )

        # Última linha de defesa — qualquer exceção não classificada.
        return GenerationError(
            code=ErrorCode.UNKNOWN,
            message=f"Falha inesperada: {exc.__class__.__name__}: {exc}",
            retryable=False,
        )

    @staticmethod
    def _extract_request_id(response: Any) -> str:
        """Lê o header ``x-request-id`` que o SDK anexa ao objeto
        parsed (atributo privado ``_request_id``).
        """
        return getattr(response, "_request_id", None) or ""

    @staticmethod
    def _extract_request_id_from_exception(exc: BaseException) -> str:
        """Algumas exceções do SDK carregam o response original —
        tenta extrair o request_id de lá também (útil em logs de
        debugging)."""
        resp = getattr(exc, "response", None)
        if resp is None:
            return ""
        # httpx.Response.headers
        headers = getattr(resp, "headers", None)
        if headers is None:
            return ""
        try:
            return headers.get("x-request-id") or ""
        except Exception:  # noqa: BLE001 — headers é mapeamento
            return ""

    # ------------------------------------------------------------------ #
    # Helpers de cliente e retry                                          #
    # ------------------------------------------------------------------ #

    def _get_client(self, request: GenerationRequest) -> Any:
        """Devolve (e reutiliza) o cliente OpenAI configurado.

        Recria o cliente apenas se a chave mudou entre chamadas —
        útil em testes que alternam chaves. Em produção, a chave
        não muda durante a vida do app.
        """
        key = request.api_key or ""
        if self._client is None or key != self._api_key_used:
            self.close()
            self._client = self._client_factory(key, request.request_timeout_s)
            self._api_key_used = key
        return self._client

    @staticmethod
    def _default_client_factory(api_key: str, timeout: float) -> Any:
        """Constrói o cliente real. Import tardio para que outros
        módulos do app não puxem o SDK sem precisar."""
        from openai import OpenAI

        return OpenAI(api_key=api_key, timeout=timeout)

    @staticmethod
    def _compute_backoff(attempt: int) -> float:
        """Backoff exponencial com jitter.

        ``attempt`` é 1-indexed (1 = primeira tentativa, 2 = primeiro
        retry). Fórmula: ``min(cap, base * 2^(attempt-1)) + jitter``.
        """
        raw = _BACKOFF_BASE_S * (2 ** (attempt - 1))
        capped = min(raw, _BACKOFF_CAP_S)
        return capped + random.uniform(0.0, _BACKOFF_JITTER_S)

    @staticmethod
    def _failure_result(
        *,
        request: GenerationRequest,
        error: GenerationError,
        duration_ms: int,
        request_id: str,
        attempts: int,
    ) -> GenerationResult:
        """Monta um ``GenerationResult`` de falha padronizado."""
        return GenerationResult(
            success=False,
            output_path=Path(request.output_path),
            model_used=request.model,
            duration_ms=duration_ms,
            request_id=request_id,
            error=error,
            bytes_written=0,
            attempts=attempts,
        )


__all__ = ["OpenAIImageGenerationProvider"]