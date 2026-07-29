"""Interfaces e implementações de provedores externos.

Toda comunicação com APIs externas (OpenAI, etc.) passa por aqui.
O resto do sistema depende APENAS de `ImageGenerationProvider` —
nunca importa SDKs externos diretamente.

Implementações concretas disponíveis:
    * `OpenAIImageGenerationProvider` — usa o SDK oficial Python
      (openai 2.50.0) via ``client.images.edit()``. Endpoint
      `/v1/images/edits` é o ÚNICO que aceita imagem de referência
      + prompt de texto. Modelos suportados: gpt-image-1, dall-e-2.
"""

from app.core.models.generation import (
    ErrorCode,
    GenerationError,
    GenerationRequest,
    GenerationResult,
    RETRYABLE_ERROR_CODES,
)
from app.core.providers.image_generation_provider import ImageGenerationProvider
from app.core.providers.openai_image_generation_provider import (
    OpenAIImageGenerationProvider,
)

__all__ = [
    "ImageGenerationProvider",
    "OpenAIImageGenerationProvider",
    "GenerationRequest",
    "GenerationResult",
    "GenerationError",
    "ErrorCode",
    "RETRYABLE_ERROR_CODES",
]