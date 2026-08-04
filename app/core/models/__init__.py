"""Modelos de domínio (dataclasses).

Entidades como Project, Batch, ImageJob, GenerationAttempt. São
estruturas de dados puras, sem dependência de ORM ou framework de UI.
"""

from app.core.models.batch import (
    Batch,
    BatchLot,
    BatchPlan,
    BatchStatus,
)
from app.core.models.generation import (
    ErrorCode,
    GenerationAttempt,
    GenerationError,
    GenerationRequest,
    GenerationResult,
    RETRYABLE_ERROR_CODES,
)
from app.core.models.image_file_info import (
    ImageFileInfo,
    ImageStatus,
    ScanResult,
)
from app.core.models.image_job import (
    ImageJob,
    ImageJobStatus,
)
from app.core.models.project import (
    Project,
)
from app.core.models.prompt_preset import (
    DEFAULT_FACTORY_DESCRIPTION,
    DEFAULT_FACTORY_NAME,
    DEFAULT_FACTORY_PROMPT,
    DEFAULT_MODEL,
    PromptPreset,
)

__all__ = [
    "ImageFileInfo",
    "ImageStatus",
    "ScanResult",
    "PromptPreset",
    "DEFAULT_FACTORY_NAME",
    "DEFAULT_FACTORY_DESCRIPTION",
    "DEFAULT_FACTORY_PROMPT",
    "DEFAULT_MODEL",
    "Batch",
    "BatchLot",
    "BatchPlan",
    "BatchStatus",
    "ImageJob",
    "ImageJobStatus",
    "GenerationRequest",
    "GenerationResult",
    "GenerationError",
    "GenerationAttempt",
    "ErrorCode",
    "RETRYABLE_ERROR_CODES",
    "Project",
]