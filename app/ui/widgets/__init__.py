"""Widgets reutilizáveis: tabelas, cards de status, barras de progresso.

Widgets podem ter lógica de apresentação, mas delegam decisões de
negócio para a janela/serviço que os contém.
"""

from app.ui.widgets.batch_processing_widget import BatchProcessingWidget
from app.ui.widgets.dashboard_widget import DashboardWidget
from app.ui.widgets.folder_scan_widget import FolderScanWidget
from app.ui.widgets.processing_page import ProcessingPage

__all__ = [
    "BatchProcessingWidget",
    "DashboardWidget",
    "FolderScanWidget",
    "ProcessingPage",
]