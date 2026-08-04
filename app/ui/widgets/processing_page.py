"""Página "Processamento" — integra `FolderScanWidget` + `BatchProcessingWidget`.

Esta página é o contêiner que permite ao usuário fazer o fluxo
completo em uma única aba:

    1. Selecionar pasta e escanear (`FolderScanWidget`).
    2. Visualizar a lista de imagens encontradas.
    3. Iniciar o processamento em lote (`BatchProcessingWidget`).

A `FolderScanWidget` já produz a lista de `ImageFileInfo` e a emite
via sinal `scan_finished`. Aqui convertemos essa lista em
`ImageJob` usando o **preset de prompt padrão** (do
`PromptPresetStore`) como fonte do `prompt_text`, `model` e
parâmetros extra — assim o `BatchProcessor` recebe jobs completos
e o `OpenAIImageGenerationProvider._validate_request` não aborta
com "prompt_text vazio" antes mesmo de chamar a API.

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

from app.core.models import (
    DEFAULT_MODEL,
    ImageFileInfo,
    ImageJob,
)
from app.core.providers import ImageGenerationProvider
from app.core.services import ImageFolderScanner
from app.data.storage.prompt_preset_store import PromptPresetStore
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
            `set_provider`.
        preset_store: `PromptPresetStore` para resolver o preset
            padrão no momento de converter `ImageFileInfo` em
            `ImageJob`. Se None, os jobs são criados sem prompt —
            nesse caso a nota informativa do `BatchProcessingWidget`
            deve continuar apontando "preset configurado" como
            requisito faltante.
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
        preset_store: PromptPresetStore | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._preset_store = preset_store
        self._scan_widget = FolderScanWidget(scanner=scanner)
        self._batch_widget = BatchProcessingWidget(provider=provider)

        # Liga o sinal do scan à carga do batch. A conversão de
        # `ImageFileInfo` -> `ImageJob` é feita por `_build_jobs`.
        self._scan_widget.scan_finished.connect(self._on_scan_finished)

        # O `BatchProcessingWidget` só habilita o "Iniciar" quando
        # há jobs E provider. O preset entra como "terceiro
        # requisito" verificado via nota informativa.
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._scan_widget)
        layout.addWidget(self._batch_widget, 1)

    # ------------------------------------------------------------------ #
    # Fachada — compatibilidade com MainWindow e dashboard               #
    # ------------------------------------------------------------------ #

    def set_provider(self, provider: ImageGenerationProvider | None) -> None:
        """Repassa o provider para o `BatchProcessingWidget` interno.

        Chamado pela `MainWindow` (lazy) após o login do usuário.
        Aceita `None` — nesse caso o widget reage desligando o
        botão "Iniciar" e exibindo a nota informativa.
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
        preset = self._resolve_default_preset()
        jobs = self._build_jobs(valid, preset=preset)
        self._batch_widget.set_jobs(jobs)
        if preset is not None:
            logger.info(
                "ProcessingPage: %d jobs criados a partir do scan (preset='%s')",
                len(jobs),
                preset.name,
            )
        else:
            logger.warning(
                "ProcessingPage: %d jobs criados SEM preset (provável erro no Iniciar)",
                len(jobs),
            )

    def _resolve_default_preset(self):
        """Devolve o preset padrão ou None se não houver store/disponível.

        Encapsula a chamada para que `__init__` continue sem
        dependência rígida do `PromptPresetStore` (a `MainWindow`
        injeta lazy, depois da primeira abertura da aba).
        """
        if self._preset_store is None:
            return None
        try:
            return self._preset_store.get_default()
        except Exception:  # noqa: BLE001 — store indisponível
            logger.warning(
                "ProcessingPage: PromptPresetStore indisponível — jobs sem prompt",
                exc_info=True,
            )
            return None

    def _build_jobs(
        self,
        files: list[ImageFileInfo],
        *,
        preset=None,
    ) -> list[ImageJob]:
        """Mapeia `ImageFileInfo` -> `ImageJob` com defaults mínimos.

        Comportamento:
            * `reference_image_path` aponta para o original.
            * `output_path` fica dentro de `<pasta>/lotes/` com o
              mesmo basename + sufixo `.png`. O diretório será criado
              pelo provider no momento do `generate`
              (ver `_validate_request`).
            * `input_hash` vem do scan (já calculado).
            * `prompt_text`/`model`/`prompt_hash`/`extra_parameters`
              vêm do **preset padrão** quando há um configurado.
              Sem preset, ficam vazios — o provider barra com
              erro claro em vez de mandar prompt vazio pra API.
            * `prompt_hash` e `parameters_hash` são consistentes com
              o que o `BatchProcessor` espera (Prompt 8).
        """
        if not files:
            return []

        source_folder = files[0].path.parent
        output_dir = source_folder / self._OUTPUT_SUBDIR

        prompt_text = getattr(preset, "prompt_text", "") or ""
        prompt_hash = (
            preset.prompt_hash() if preset is not None and hasattr(preset, "prompt_hash") else ""
        )
        # Casa com o docstring do `PromptPreset.model`: `None` (ou
        # string vazia, configurada no diálogo mas não preenchida)
        # significa "usar o model default do app".
        raw_model = getattr(preset, "model", None) if preset is not None else None
        model = raw_model if (isinstance(raw_model, str) and raw_model.strip()) else DEFAULT_MODEL
        extra = self._preset_extra_params(preset)

        jobs: list[ImageJob] = []
        for f in files:
            jobs.append(
                ImageJob(
                    reference_image_path=f.path,
                    output_path=output_dir / f"{f.path.stem}.png",
                    input_hash=f.sha256,
                    prompt_text=prompt_text,
                    prompt_hash=prompt_hash,
                    model=model,
                    extra_parameters=extra,
                )
            )
        return jobs

    @staticmethod
    def _preset_extra_params(preset) -> dict:
        """Extrai `extra_parameters` do preset (campos opcionais do
        `PromptPreset` que viram kwargs do `images.edit`).

        Não filtra por whitelist aqui — quem decide é o
        `OpenAIImageGenerationProvider._filter_kwargs` (Prompt 6).
        """
        if preset is None:
            return {}
        out: dict = {}
        for key in (
            "resolution",
            "quality",
            "output_format",
            "background",
            "n_variations",
        ):
            v = getattr(preset, key, None)
            if v is None:
                continue
            if key == "resolution" and isinstance(v, tuple):
                w, h = v
                out["size"] = f"{w}x{h}"
            elif key == "n_variations":
                # O SDK/API chama esse parâmetro de ``n``, não
                # ``n_variations`` — sem este mapeamento a chave cai
                # fora do whitelist do provider e é silenciosamente
                # ignorada (o usuário configura variações no preset e
                # elas nunca são aplicadas).
                out["n"] = v
            else:
                out[key] = v
        return out


__all__ = ["ProcessingPage"]
