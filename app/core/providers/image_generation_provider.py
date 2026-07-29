"""Interface abstrata de provider de geração de imagens.

Qualquer provedor (OpenAI, Stability, mock para testes, cache local)
implementa `ImageGenerationProvider`. O restante do sistema depende
SOMENTE desta interface — nunca importa SDKs externos diretamente.

Princípios do contrato:
    * `generate()` NUNCA levanta exceção para erros previsíveis
      (auth, rate limit, content rejected, etc.). Devolve
      `GenerationResult(success=False, error=...)` classificado.
    * `generate()` SÓ levanta `RuntimeError` para bugs próprios
      (estado inconsistente) — isso é falha do programador, não
      do provedor.
    * `generate()` É thread-unsafe por padrão. O caller (UI worker)
      decide se usa uma instância por thread ou um lock.
    * O provider NÃO é responsável por idempotência de output —
      essa decisão é do orquestrador (Prompt 7/8), que decide se
      chama `generate()` ou pula com base em `request.input_hash` e
      em cache de execuções anteriores. Aqui, cada `generate()` é
      uma chamada real (ou simulada).
    * O provider CONCRETO é responsável por toda a tradução de
      exceções do SDK em `ErrorCode` — a interface nunca vê o SDK.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Iterator

from app.core.models.generation import GenerationRequest, GenerationResult


class ImageGenerationProvider(ABC):
    """Contrato para qualquer provider de geração de imagens."""

    @abstractmethod
    def generate(self, request: GenerationRequest) -> GenerationResult:
        """Executa UMA geração. Não levanta para erros previsíveis.

        Implementações devem garantir:
            * Erros previsíveis do provedor remoto (auth, rate
              limit, content rejected, etc.) -> `success=False`
              com `error.code` da taxonomia.
            * O arquivo em `request.output_path` só existe no
              disco se `success=True` E já foi validado
              internamente pelo provider.
            * `attempts >= 1` e reflete o total de tentativas
              feitas (incluindo retries automáticos).
        """

    # ------------------------------------------------------------------ #
    # Suporte a context manager                                          #
    # ------------------------------------------------------------------ #

    def __enter__(self) -> "ImageGenerationProvider":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # noqa: D401, ANN001
        self.close()

    def close(self) -> None:
        """Libera recursos (clientes HTTP, etc.). No-op por default."""

    # ------------------------------------------------------------------ #
    # Utilidade para testes / progresso                                   #
    # ------------------------------------------------------------------ #

    def iter_batches(
        self, requests: Iterator[GenerationRequest]
    ) -> Iterator[GenerationResult]:
        """Atalho: chama `generate()` para cada request.

        Útil em pipelines simples. Providers concretos podem
        sobrescrever para paralelizar ou aplicar rate limit
        inteligente entre chamadas.
        """
        for req in requests:
            yield self.generate(req)


__all__ = ["ImageGenerationProvider"]