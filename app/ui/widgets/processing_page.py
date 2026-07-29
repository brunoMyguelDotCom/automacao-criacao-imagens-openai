"""Página "Processamento" — integra `FolderScanWidget` + `BatchProcessingWidget`.

Esta página é o contêiner que permite ao usuário fazer o fluxo
completo em uma única aba:

    1. Selecionar pasta e escanear (`FolderScanWidget`).
    2. Visualizar a lista de imagens encontradas.
    3. Iniciar o processamento em lote (`BatchProcessingWidget`).

A `FolderScanWidget` já produz a lista de `ImageFileInfo` e a emite
via sinal `scan_finished`. Aqui convertemos essa lista em
`ImageJob` com defaults razoáveis (saída na subpasta `lotes/` da
pasta de entrada) e entregamos ao `BatchProcessingWidget` via o
método público `set_jobs` — que já é o ponto de entrada usado pelo
fluxo do dashboard (Prompt 9).

A página também expõe `set_jobs` e `set_provider` como fachada,
para que a `MainWindow` continue podendo controlar o widget
quando o usuário clica em um batch a partir do dashboard
(Não duplica a lógica de processamento em lote — o
`BatchProcessingWidget` continua sendo a fonte da verdade.)
"""

from __future__ import annotations

import logging
from typing import Iterable

from PySide6.QtCore import Slot
from PySide6.QtWidgets import QVBoxLayout, QWidget

from app.core.models import ImageFileInfo, ImageJob
from app.core.providers import ImageGenerationProvider
from app.core.services import ImageFolderScanner
from app.ui.widgets.batch_processing_widget import BatchProcessingWidget
from app.ui.widgets.folder_scan_widget import FolderScanWidget

logger = logging.getLogger(__name__)


class ProcessingPage(QWidget):
    """Página que combina o scan de pasta e o processamento em lote.

    Args:
        scanner: implementação de `ImageFolderScanner` para o
            `FolderScanWidget`. Quando None, usa o default.
        provider: `ImageGenerationProvider` para o
            `BatchProcessingWidget`. Pode ser setado depois via
            `set_provider` (mesma estratégia que o
            `BatchProcessingWidget` aceita).
        parent: widget pai (Qt).
    """

    #: Subpasta, dentro da pasta de entrada, onde os `ImageJob`
    #: terão seu `output_path` apontado por padrão. Casa com a
    #: convenção do `BatchPlanner` para evitar surpresa.
    _OUTPUT_SUBDIR = "lotes"

    def __init__(
        self,
        scanner: ImageFolderScanner | None = None,
        provider: ImageGenerationProvider | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._scan_widget = FolderScanWidget(scanner=scanner)
        self._batch_widget = BatchProcessingWidget(provider=provider)

        # Liga o sinal do scan à carga do batch. A conversão de
        # `ImageFileInfo` -> `ImageJob` é feita por `_build_jobs`.
        self._scan_widget.scan_finished.connect(self._on_scan_finished)

        # O `BatchProcessingWidget` só habilita o "Iniciar" quando
        # há jobs E provider. A conexão abaixo já cobre o caso
        # "recebeu a lista de imagens" — o widget trata o provider
        # internamente.
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._scan_widget)
        layout.addWidget(self._batch_widget, 1)

    # ------------------------------------------------------------------ #
    # Fachada — compatibilidade com MainWindow e dashboard               #
    # ------------------------------------------------------------------ #

    def set_provider(self, provider: ImageGenerationProvider) -> None:
        """Repassa o provider para o `BatchProcessingWidget` interno.

        Chamado pela `MainWindow` (lazy) após o login do usuário.
        """
        self._batch_widget.set_provider(provider)

    def set_jobs(self, jobs: Iterable[ImageJob], batch_id: str = "") -> None:
        """Repassa a lista de jobs (Prompt 9 — duplo-clique no dashboard).

        Mantém a interface que a `MainWindow` já usava quando a aba
        era apenas o `FolderScanWidget`. A página apenas delega.
        """
        self._batch_widget.set_jobs(jobs, batch_id=batch_id)

    # ------------------------------------------------------------------ #
    # Conversão scan -> jobs                                             #
    # ------------------------------------------------------------------ #

    @Slot(list)
    def _on_scan_finished(self, files: list) -> None:
        """Recebe a lista de `ImageFileInfo` e cria `ImageJob`s.

        Apenas arquivos `VALID` viram jobs — os inválidos já foram
        reportados na própria tela do scan e não devem ser enviados
        ao provider.
        """
        valid = [f for f in files if isinstance(f, ImageFileInfo) and f.status.name == "VALID"]
        if not valid:
            logger.info(
                "ProcessingPage: scan terminou sem arquivos válidos — nada a processar"
            )
            return
        jobs = self._build_jobs(valid)
        self._batch_widget.set_jobs(jobs)
        logger.info(
            "ProcessingPage: %d jobs criados a partir do scan", len(jobs)
        )

    def _build_jobs(self, files: list[ImageFileInfo]) -> list[ImageJob]:
        """Mapeia `ImageFileInfo` -> `ImageJob` com defaults mínimos.

        Mantém o contrato do `BatchProcessor`:
            * `reference_image_path` aponta para o original.
            * `output_path` fica dentro de `<pasta>/lotes/` com o
              mesmo basename + sufixo `.png` (formato nativo do
              provider). O diretório será criado pelo provider no
              momento do `generate` (ver `_validate_request`).
            * `input_hash` vem do scan (já calculado).
            * `prompt_text`/`model` ficam vazios — devem ser
              configurados pelo usuário antes do "Iniciar" (o
              `BatchProcessor` aborta cedo se estiverem vazios,
              garantindo falha explícita em vez de cobrança
              indevida à API).
        """
        if not files:
            return []
        source_folder = files[0].path.parent
        output_dir = source_folder / self._OUTPUT_SUBDIR
        jobs: list[ImageJob] = []
        for f in files:
            jobs.append(
                ImageJob(
                    reference_image_path=f.path,
                    output_path=output_dir / f"{f.path.stem}.png",
                    input_hash=f.sha256,
                )
            )
        return jobs


__all__ = ["ProcessingPage"]
