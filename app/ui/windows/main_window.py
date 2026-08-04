"""Janela principal da aplicação (Prompt 1 + Prompt 9).

Estabelece a navegação entre as áreas do app:
    * Configuração (credenciais, presets, diagnóstico — Prompt 10).
    * Processamento (scan de pasta + lote).
    * Status Geral (dashboard agregado — Prompt 9).

A aba de dashboard é criada lazy: depende do `DatabaseConnection`,
que por sua vez precisa do `get_database_path()`. Instanciamos
apenas quando o usuário abre a aba, para não forçar a resolução
do caminho no construtor (que é usado em testes sem setup de
config).

O visual é controlado por `app/ui/theme.py` — esta janela não
declarar nenhum `setStyleSheet` próprio: herda o tema escuro
global.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFileDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStatusBar,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from app.config.constants import (
    APP_TITLE,
    WINDOW_MIN_HEIGHT,
    WINDOW_MIN_WIDTH,
)
from app.config.paths import (
    get_database_path,
    get_logs_dir,
)
from app.core.services import ImageFolderScanner
from app.data.database.connection import DatabaseConnection
from app.data.repositories import ProjectRepository
from app.data.storage import CredentialManager
from app.ui.widgets import DashboardWidget
from app.ui.widgets import ProcessingPage

logger = logging.getLogger(__name__)


def _build_placeholder_tab(title: str, hint: str) -> QWidget:
    """Cria uma aba vazia com um rótulo informativo."""
    widget = QWidget()
    layout = QVBoxLayout(widget)
    layout.setContentsMargins(24, 24, 24, 24)
    label_title = QLabel(title)
    label_title.setProperty("heading", True)
    label_title.setStyleSheet("font-size: 20px; font-weight: 700;")
    layout.addWidget(label_title)
    label_hint = QLabel(hint)
    label_hint.setWordWrap(True)
    label_hint.setProperty("hint", True)
    layout.addWidget(label_hint)
    layout.addStretch(1)
    return widget


class MainWindow(QMainWindow):
    """Janela principal com navegação por abas."""

    def __init__(
        self,
        credential_manager: CredentialManager | None = None,
        image_scanner: ImageFolderScanner | None = None,
    ) -> None:
        super().__init__()
        self.setWindowTitle(APP_TITLE)
        self.setMinimumSize(WINDOW_MIN_WIDTH, WINDOW_MIN_HEIGHT)

        # O CredentialManager é instanciado lazy para que o construtor
        # da MainWindow continue fácil de usar em testes sem precisar
        # configurar backend.
        self._cred = credential_manager or CredentialManager()
        self._scanner = image_scanner or ImageFolderScanner()

        # Caches lazy: abertos sob demanda para evitar acoplamento
        # com `DatabaseConnection` no construtor (testes sem setup).
        self._db: DatabaseConnection | None = None
        self._project_repo: ProjectRepository | None = None
        self._dashboard_widget: DashboardWidget | None = None
        self._processing_page: ProcessingPage | None = None
        # Provider de geração (criado lazy quando o usuário salva
        # a chave ou quando a página de processamento é aberta).
        self._image_provider = None

        self._tabs = QTabWidget(self)
        self._tabs.setTabPosition(QTabWidget.North)
        self._tabs.setMovable(False)
        self._tabs.setDocumentMode(True)

        self._tabs.addTab(self._build_config_tab(), "Configuração")
        self._tabs.addTab(
            _build_placeholder_tab(
                "Processamento",
                "Carregando widgets de processamento…",
            ),
            "Processamento",
        )
        self._tabs.addTab(
            _build_placeholder_tab(
                "Status Geral",
                "Carregando dashboard…",
            ),
            "Status Geral",
        )
        # Quando a aba de Processamento ou a de Status Geral for
        # exibida pela primeira vez, substituímos o placeholder
        # pelo widget real.
        self._tabs.currentChanged.connect(self._on_tab_changed)

        self.setCentralWidget(self._tabs)

        status = QStatusBar(self)
        self.setStatusBar(status)
        status.showMessage("Pronto.", 5000)

        logger.info(
            "MainWindow inicializada: %dx%d mínimo", WINDOW_MIN_WIDTH, WINDOW_MIN_HEIGHT
        )

    # ------------------------------------------------------------------ #
    # Setup lazy de banco + repositórios                                  #
    # ------------------------------------------------------------------ #

    def _ensure_preset_store(self):
        """Instancia o `PromptPresetStore` (lazy, junto com o DB)."""
        if not hasattr(self, "_preset_store") or self._preset_store is None:
            from app.data.storage import PromptPresetStore
            db = self._ensure_db()
            self._preset_store = PromptPresetStore(db)
        return self._preset_store

    def _ensure_db(self) -> DatabaseConnection:
        if self._db is None:
            self._db = DatabaseConnection(get_database_path())
            self._project_repo = ProjectRepository(self._db)
        assert self._db is not None
        return self._db

    def _ensure_dashboard(self) -> DashboardWidget:
        if self._dashboard_widget is not None:
            return self._dashboard_widget
        db = self._ensure_db()
        assert self._project_repo is not None
        from app.core.services import DashboardService

        projects = self._project_repo.list()
        self._dashboard_widget = DashboardWidget(
            dashboard_service=DashboardService(db),
            projects=projects,
        )
        # Duplo clique → troca para a aba de Processamento e
        # sinaliza qual batch abrir (Prompt 9, regra 4).
        self._dashboard_widget.batch_double_clicked.connect(self._on_batch_activated)
        return self._dashboard_widget

    def _ensure_processing_page(self) -> ProcessingPage:
        """Instancia o `ProcessingPage` (lazy).

        Garante o `PromptPresetStore` antes para que o
        `_build_jobs` consiga puxar o preset padrão e preencher
        `prompt_text`/`model`/`extra_parameters` dos jobs.
        """
        if self._processing_page is None:
            preset_store = None
            try:
                preset_store = self._ensure_preset_store()
            except Exception:  # noqa: BLE001
                logger.warning(
                    "PromptPresetStore indisponível — jobs serão criados sem preset",
                    exc_info=True,
                )
            self._processing_page = ProcessingPage(
                scanner=self._scanner,
                provider=self._image_provider,
                preset_store=preset_store,
            )
        return self._processing_page

    def _ensure_image_provider(self):
        """Garante um `ImageGenerationProvider` configurado.

        V1 — MVP de automação:
            O motor agora é o `ChatGPTDesktopAutomationProvider`, que
            NÃO depende da chave da OpenAI (automa o ChatGPT Desktop
            via clipboard + atalhos). Por isso, a checagem
            ``self._cred.has_key()`` foi REMOVIDA deste método.

        V2 vai simplificar isto: o provider é instanciado uma vez no
        ``__init__`` (sem lazy), e o ``CredentialManager`` sai do
        caminho. Por enquanto mantemos o lazy para evitar mexer na
        sequência de inicialização dos testes.
        """
        if self._image_provider is not None:
            return self._image_provider
        # TODO(V2): remover este lazy — provider será atributo de
        # instância, criado no __init__.
        from app.core.providers.chatgpt_desktop_automation_provider import (
            ChatGPTDesktopAutomationProvider,
        )

        self._image_provider = ChatGPTDesktopAutomationProvider()
        logger.info("ChatGPTDesktopAutomationProvider instanciado e pronto")
        return self._image_provider

    def _openai_client_factory(self, api_key_from_request: str, timeout: float):
        """Factory injetado no `OpenAIImageGenerationProvider`.

        O provider chama `(api_key, timeout)` na criação do cliente.
        Aqui ignoramos a chave recebida por argumento (que vem
        sempre vazia — o `BatchProcessor` não a propaga) e usamos
        a chave persistida no `CredentialManager`.

        Se a chave mudar entre chamadas (caso raro), o provider
        recria o cliente automaticamente porque comparamos
        `request.api_key` com `self._api_key_used` — mas como o
        argumento é sempre vazio, na prática a chave efetiva é a
        mesma durante toda a vida do provider, então o cliente
        também é criado uma única vez (o que casa com o comentário
        do provider sobre reusar o pool HTTP/2).
        """
        from openai import OpenAI

        from app.core.providers.openai_image_generation_provider import (
            OPENAI_API_BASE_URL,
            _assert_openai_base_url,
        )

        key = self._cred.get_key() or ""
        client = OpenAI(
            api_key=key,
            timeout=timeout,
            base_url=OPENAI_API_BASE_URL,
        )
        _assert_openai_base_url(client)
        return client

    def _refresh_provider_on_processing_page(self) -> None:
        """Propaga o provider atual (se houver) para a página.

        Chamado em três pontos:
          * lazy-load da aba Processamento (chave já estava salva);
          * após salvar a chave no diálogo de Configuração;
          * após remover a chave (limpa o provider na página).

        Se a página ainda não foi instanciada, não faz nada —
        o lazy-load cuidará do repasse quando ela for criada.
        """
        if self._processing_page is None:
            return
        # Provider pode ser None (sem chave) — `set_provider`
        # aceita isso e o widget reage desligando o botão.
        try:
            self._processing_page.set_provider(self._image_provider)
        except Exception:  # noqa: BLE001
            logger.debug("Falha ao repassar provider (ignorado)", exc_info=True)

    # ------------------------------------------------------------------ #
    # Tabs                                                                #
    # ------------------------------------------------------------------ #

    def _on_tab_changed(self, index: int) -> None:
        # Lazy-load do dashboard.
        if self._tabs.tabText(index) == "Status Geral" and self._dashboard_widget is None:
            widget = self._ensure_dashboard()
            self._tabs.removeTab(index)
            self._tabs.insertTab(index, widget, "Status Geral")
            self._tabs.setCurrentIndex(index)
            return
        # Lazy-load do ProcessingPage — também tenta injetar o
        # provider caso a chave já esteja salva.
        if self._tabs.tabText(index) == "Processamento" and self._processing_page is None:
            # Garante o provider ANTES de criar a página, para que
            # o `ProcessingPage` já receba o provider no construtor
            # (caso comum: chave salva antes da primeira abertura).
            self._ensure_image_provider()
            self._ensure_processing_page()
            page = self._processing_page
            assert page is not None
            self._tabs.removeTab(index)
            self._tabs.insertTab(index, page, "Processamento")
            self._tabs.setCurrentIndex(index)
            self._refresh_provider_on_processing_page()

    def _on_batch_activated(self, batch_id: str) -> None:
        """Sinal vindo do duplo-clique no dashboard: navegar para
        Processamento e carregar o lote.

        O `BatchProcessingWidget` (Prompt 7) já sabe rodar jobs. Aqui
        apenas disparamos o `set_jobs` com a lista do batch.
        """
        logger.info("Duplo-clique no dashboard: batch %s solicitado", batch_id)
        # Troca para a aba de Processamento.
        for i in range(self._tabs.count()):
            if self._tabs.tabText(i) == "Processamento":
                self._tabs.setCurrentIndex(i)
                break
        # Carrega o lote no widget de processamento.
        from app.data.repositories import BatchRepository, ImageJobRepository

        db = self._ensure_db()
        batch = BatchRepository(db).get(batch_id)
        if batch is None:
            self.statusBar().showMessage(f"Lote {batch_id} não encontrado.", 5000)
            return
        jobs = ImageJobRepository(db).list_by_batch(batch_id)
        # Encontra o BatchProcessingWidget ativo (filho da aba atual).
        proc_widget = self._tabs.currentWidget()
        if hasattr(proc_widget, "set_jobs"):
            proc_widget.set_jobs(jobs, batch_id=batch_id)
        self.statusBar().showMessage(
            f"Lote '{batch.name}' carregado — {len(jobs)} jobs.", 5000
        )

    # ------------------------------------------------------------------ #
    # Builders das outras abas                                            #
    # ------------------------------------------------------------------ #

    def _build_config_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        title = QLabel("Configuração")
        title.setProperty("heading", True)
        title.setStyleSheet("font-size: 20px; font-weight: 700;")
        layout.addWidget(title)

        hint = QLabel(
            "Gerencie a chave da API da OpenAI e os parâmetros gerais "
            "do aplicativo."
        )
        hint.setWordWrap(True)
        hint.setProperty("hint", True)
        layout.addWidget(hint)

        # Container agrupa ações de configuração.
        actions_container = QWidget()
        actions_layout = QVBoxLayout(actions_container)
        actions_layout.setContentsMargins(0, 0, 0, 0)
        actions_layout.setSpacing(8)

        open_btn = QPushButton("🔑  Configurar chave da OpenAI…")
        open_btn.setMinimumHeight(38)
        open_btn.clicked.connect(self._open_credentials_dialog)
        actions_layout.addWidget(open_btn)

        preset_btn = QPushButton("📝  Gerenciar presets de prompt…")
        preset_btn.setMinimumHeight(38)
        preset_btn.clicked.connect(self._open_presets_dialog)
        actions_layout.addWidget(preset_btn)

        # Diagnóstico (Prompt 10): abrir pasta de logs + exportar bundle.
        log_btn = QPushButton("📂  Abrir pasta de logs…")
        log_btn.setMinimumHeight(38)
        log_btn.clicked.connect(self._open_logs_folder)
        actions_layout.addWidget(log_btn)

        diag_btn = QPushButton("🛠  Exportar diagnóstico…")
        diag_btn.setMinimumHeight(38)
        diag_btn.clicked.connect(self._export_diagnostic)
        actions_layout.addWidget(diag_btn)

        layout.addWidget(actions_container)
        layout.addStretch(1)
        return widget

    def _open_presets_dialog(self) -> None:
        from app.data.storage import PromptPresetStore
        from app.ui.dialogs.prompt_preset_dialog import PromptPresetDialog

        if not hasattr(self, "_preset_store") or self._preset_store is None:
            db = DatabaseConnection(get_database_path())
            self._preset_store = PromptPresetStore(db)

        dlg = PromptPresetDialog(self._preset_store, parent=self)
        dlg.exec()
        if dlg.result().default_changed:
            self.statusBar().showMessage("Preset de prompt atualizado.", 5000)

    def _open_credentials_dialog(self) -> None:
        from app.ui.dialogs.settings_dialog import SettingsDialog

        dlg = SettingsDialog(self._cred, parent=self)
        dlg.exec()
        result = dlg.result()
        if result.saved:
            # Chave salva: instancia o provider (lazy) e propaga
            # para o `ProcessingPage` se já tiver sido construído.
            # Isso faz o botão "Iniciar" destravar automaticamente
            # assim que a chave é cadastrada.
            self._ensure_image_provider()
            self._refresh_provider_on_processing_page()
            self.statusBar().showMessage("Credencial salva.", 5000)
        elif result.deleted:
            # Chave removida: zera o provider e propaga None.
            if self._image_provider is not None:
                try:
                    self._image_provider.close()
                except Exception:  # noqa: BLE001
                    logger.debug("Falha ao fechar provider (ignorado)", exc_info=True)
                self._image_provider = None
            self._refresh_provider_on_processing_page()
            self.statusBar().showMessage("Credencial removida.", 5000)

    # ------------------------------------------------------------------ #
    # Diagnóstico (Prompt 10, regras 5 e 6)                              #
    # ------------------------------------------------------------------ #

    def _open_logs_folder(self) -> None:
        """Abre a pasta de logs no explorador de arquivos padrão."""
        logs_dir = get_logs_dir()
        # Garante que o diretório existe antes de pedir para o SO abrir.
        try:
            logs_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            QMessageBox.warning(
                self,
                "Erro ao abrir pasta de logs",
                f"Não foi possível acessar a pasta de logs:\n{exc}",
            )
            return
        from app.ui.paths import open_path_in_shell

        open_path_in_shell(logs_dir)
        self.statusBar().showMessage(f"Pasta de logs aberta: {logs_dir}", 5000)

    def _export_diagnostic(self) -> None:
        """Gera e salva um bundle de diagnóstico sanitizado."""
        from app.core.resilience import (
            build_diagnostic_bundle,
            write_diagnostic_bundle,
        )

        db_path = get_database_path()
        logs_dir = get_logs_dir()
        log_path = logs_dir / "app.log"

        stats: dict[str, int] = {}
        try:
            db = DatabaseConnection(db_path)
            projects = ProjectRepository(db).list()
            stats["projetos"] = len(projects)
        except Exception:  # noqa: BLE001
            stats["projetos"] = 0

        features = {
            "log_level": "INFO",
            "max_log_bytes": 5 * 1024 * 1024,
            "max_log_backups": 5,
        }

        bundle = build_diagnostic_bundle(
            database_path=db_path,
            log_path=log_path,
            stats=stats,
            features=features,
        )

        from datetime import datetime

        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        default_name = f"diagnostico-{ts}.txt"
        target_str, _ = QFileDialog.getSaveFileName(
            self,
            "Exportar diagnóstico",
            default_name,
            "Arquivos de texto (*.txt)",
        )
        if not target_str:
            return
        from pathlib import Path

        target = Path(target_str)
        try:
            write_diagnostic_bundle(target, bundle)
        except OSError as exc:
            QMessageBox.warning(
                self,
                "Erro ao exportar diagnóstico",
                f"Não foi possível salvar o arquivo:\n{exc}",
            )
            return
        self.statusBar().showMessage(f"Diagnóstico salvo em {target}", 5000)

    def closeEvent(self, event) -> None:  # noqa: N802 (override Qt)
        logger.info("MainWindow encerrando")
        super().closeEvent(event)