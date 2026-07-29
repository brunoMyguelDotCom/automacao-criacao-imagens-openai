"""Serviços de domínio.

Casos de uso do sistema: varredura de pastas, divisão em lotes,
orquestração de processamento, agregação de dashboard. Não conhecem
a UI nem o banco diretamente — recebem dependências por injeção.
"""

from app.core.services.batch_planner import BatchPlanner
from app.core.services.batch_processor import (
    BatchEvent,
    BatchProcessor,
    EventCallback,
    EventKind,
    JobExecutor,
    ProgressSnapshot,
    SequentialJobExecutor,
)
from app.core.services.batch_splitter import (
    BatchSplitter,
    CollisionResolution,
    SplitResult,
)
from app.core.services.dashboard_service import (
    HISTORY_FILTERABLE_STATUSES,
    BatchSummary,
    DashboardService,
    DashboardSnapshot,
    DashboardSummary,
    JobHistoryEntry,
    summarize_history,
)
from app.core.services.image_folder_scanner import (
    SUPPORTED_EXTENSIONS,
    ImageFolderScanner,
)

__all__ = [
    "ImageFolderScanner",
    "SUPPORTED_EXTENSIONS",
    "BatchPlanner",
    "BatchSplitter",
    "CollisionResolution",
    "SplitResult",
    "BatchProcessor",
    "BatchEvent",
    "EventKind",
    "ProgressSnapshot",
    "JobExecutor",
    "SequentialJobExecutor",
    "EventCallback",
    "DashboardService",
    "DashboardSnapshot",
    "DashboardSummary",
    "BatchSummary",
    "JobHistoryEntry",
    "HISTORY_FILTERABLE_STATUSES",
    "summarize_history",
]