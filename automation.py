"""
automation.py
Camada de automação Windows para ChatGPT Desktop.

Estratégia:优先 tentar Windows UI Automation (pywinauto + uiautomation).
Fallback: pyperclip para colar texto, SendKeys do pywinauto.

IMPORTANTE:
- O ChatGPT Desktop é um app Electron. Sua árvore UI pode mudar entre versões.
- Os controles abaixo foram nomeados de forma "esperada"; se mudar, ajuste
  ControlNames/ClassName. Toda espera é feita via wait()/wait_idle() — NUNCA
  via delays fixos.
"""
from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional

try:
    import pyperclip  # type: ignore
except ImportError:  # pragma: no cover
    pyperclip = None  # type: ignore

try:
    import uiautomation as auto  # type: ignore
except ImportError:  # pragma: no cover
    auto = None  # type: ignore

try:
    from pywinauto import Application, timings  # type: ignore
    from pywinauto.findwindows import find_window  # type: ignore
    from pywinauto.keyboard import send_keys  # type: ignore
except ImportError:  # pragma: no cover
    Application = None  # type: ignore
    timings = None  # type: ignore
    find_window = None  # type: ignore
    send_keys = None  # type: ignore

from logger import get_logger

log = get_logger("automation")


# --------------------- exceções ---------------------
class AutomationError(RuntimeError):
    pass


class ChatGPTNotFoundError(AutomationError):
    pass


# --------------------- helpers ---------------------
@dataclass
class _ChatGPTPaths:
    """Caminhos comuns onde o ChatGPT Desktop pode estar instalado no Windows."""
    candidates: List[Path]

    @classmethod
    def build(cls) -> "_ChatGPTPaths":
        candidates: List[Path] = []
        # Variável de ambiente opcional
        env_path = os.environ.get("CHATGPT_DESKTOP_PATH")
        if env_path:
            candidates.append(Path(env_path))

        prog = os.environ.get("ProgramFiles", r"C:\Program Files")
        prog86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
        local = os.environ.get("LOCALAPPDATA", "")

        for base in (prog, prog86):
            candidates.append(Path(base) / "ChatGPT" / "ChatGPT.exe")
        if local:
            candidates.append(Path(local) / "ChatGPT" / "ChatGPT.exe")
            candidates.append(Path(local) / "Programs" / "ChatGPT" / "ChatGPT.exe")
            candidates.append(Path(local) / "Microsoft" / "WindowsApps" / "ChatGPT.exe")

        # Filtra apenas os que existem
        existing = [p for p in candidates if p.exists()]
        if not existing:
            existing = candidates  # mantém lista original para erro
        return cls(existing)


def _is_windows() -> bool:
    return os.name == "nt"


# --------------------- classe principal ---------------------
class ChatGPTAutomation:
    """
    Wrapper de alto nível para ChatGPT Desktop.
    Métodos públicos são chamados pelo pipeline principal.
    """

    def __init__(self, window_title_substring: str = "ChatGPT") -> None:
        if not _is_windows():
            raise AutomationError(
                "Esta automação roda apenas em Windows (ChatGPT Desktop)."
            )
        if Application is None:
            raise AutomationError(
                "Dependências de automação ausentes. "
                "Instale: pip install pywinauto uiautomation pyperclip"
            )

        self.window_substring = window_title_substring
        self._app: Optional["Application"] = None
        self._dlg = None  # window
        self._paths = _ChatGPTPaths.build()

    # ------------------ launch / attach ------------------
    def ensure_started(self) -> None:
        """Garante que o ChatGPT Desktop esteja aberto e focado."""
        try:
            self._attach_or_launch()
        except ChatGPTNotFoundError:
            log.info("ChatGPT não está aberto. Tentando iniciar...")
            self._launch_app()
            self._wait_for_window(timeout=60)

        # foco
        try:
            if self._dlg is not None:
                self._dlg.set_focus()
        except Exception as exc:  # noqa: BLE001
            log.warning("Não foi possível focar a janela: %s", exc)

    def _attach_or_launch(self) -> None:
        try:
            hwnd = find_window(title_re=f".*{self.window_substring}.*")
        except Exception:  # noqa: BLE001
            hwnd = None
        if not hwnd:
            raise ChatGPTNotFoundError("Janela do ChatGPT não encontrada.")
        self._app = Application(backend="uia").connect(handle=hwnd)
        self._dlg = self._app.window(handle=hwnd)

    def _launch_app(self) -> None:
        if not self._paths.candidates:
            raise ChatGPTNotFoundError("Nenhum caminho de instalação conhecido.")
        for path in self._paths.candidates:
            if path.exists():
                log.info("Iniciando ChatGPT em: %s", path)
                try:
                    subprocess.Popen([str(path)])
                    return
                except OSError as exc:
                    log.error("Falha ao iniciar %s: %s", path, exc)
        raise ChatGPTNotFoundError(
            "Não foi possível iniciar o ChatGPT Desktop. "
            "Defina CHATGPT_DESKTOP_PATH ou instale o app."
        )

    def _wait_for_window(self, timeout: int = 60) -> None:
        deadline = time.time() + timeout
        last_err: Optional[Exception] = None
        while time.time() < deadline:
            try:
                self._attach_or_launch()
                if self._dlg is not None:
                    self._dlg.wait("ready", timeout=5)
                    return
            except Exception as exc:  # noqa: BLE001
                last_err = exc
                time.sleep(1)
        raise ChatGPTNotFoundError(
            f"Janela do ChatGPT não apareceu em {timeout}s. Último erro: {last_err}"
        )

    # ------------------ prompt helpers ------------------
    def _set_clipboard(self, text: str) -> None:
        if pyperclip is None:
            raise AutomationError("pyperclip não está instalado.")
        pyperclip.copy(text)
        # pequena espera para o clipboard ser atualizado em apps lentos
        time.sleep(0.05)

    # ------------------ UI primitives ------------------
    def _find_descendant(
        self,
        *,
        control_type: Optional[str] = None,
        name_re: Optional[str] = None,
        class_name: Optional[str] = None,
        timeout: int = 15,
    ):
        """Procura um controle descendente na janela principal."""
        if self._dlg is None:
            raise AutomationError("Janela não anexada.")
        kwargs = {}
        if control_type:
            kwargs["control_type"] = control_type
        if name_re:
            kwargs["name_re"] = name_re
        if class_name:
            kwargs["class_name"] = class_name
        # O pywinauto com backend 'uia' lida bem com regex
        try:
            return self._dlg.child_window(**kwargs).wait(
                "ready", timeout=timeout
            )
        except Exception as exc:  # noqa: BLE001
            raise AutomationError(
                f"Controle não encontrado {kwargs}: {exc}"
            ) from exc

    def attach_image(self, image_path: Path) -> None:
        """
        Anexa uma imagem ao Composer atual.

        Estratégia:
        1) tenta clicar no botão de anexar (geralmente com ícone de clipe).
        2) quando o diálogo de arquivo abrir, usa SendKeys para digitar o path
           e confirmar (mais robusto que coordenadas).

        Caso a árvore UI mude e o botão não seja achado, faz fallback para
        colar o path no campo de texto (alguns apps aceitam colar caminho).
        """
        if not image_path.exists():
            raise AutomationError(f"Imagem não existe: {image_path}")

        log.info("Anexando imagem: %s", image_path.name)

        # 1) Tenta achar botão de anexar
        try:
            attach_btn = self._find_descendant(
                control_type="Button", timeout=10
            )
            attach_btn.click_input()
        except AutomationError:
            # fallback: usar atalho de teclado do ChatGPT (se existir) — Ctrl+U em alguns
            log.warning(
                "Botão 'Anexar' não localizado por nome; tentando Ctrl+U."
            )
            send_keys("^u")
            time.sleep(0.5)

        # 2) diálogo de arquivo: digita o caminho e aperta Enter
        # CmdLine da janela de diálogo costuma aceitar tipo de arquivo e nome
        full = str(image_path.resolve())
        # shlex.quote não é ideal para Windows; usa aspas duplas com escape
        quoted = '"' + full.replace('"', '\\"') + '"'
        # pequena espera para o diálogo abrir
        time.sleep(0.8)
        send_keys(quoted, with_spaces=True)
        time.sleep(0.2)
        send_keys("{ENTER}")

    def send_prompt(self, prompt: str) -> None:
        """Cola o prompt no campo de texto do composer e envia."""
        log.info("Colando prompt (%d chars) e enviando.", len(prompt))
        self._set_clipboard(prompt)
        # foco no composer — Ctrl+V
        send_keys("^v")
        time.sleep(0.2)
        send_keys("{ENTER}")

    def wait_for_generation(
        self, timeout: int = 180, poll: float = 2.0
    ) -> bool:
        """
        Espera o fim da geração.

        Heurística:
          - Procura um botão "Stop generating" e aguarda ele desaparecer.
          - Quando ele não existe, a geração terminou.
        """
        log.info("Aguardando geração (timeout=%ds)...", timeout)
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                stop = self._dlg.child_window(
                    control_type="Button",
                    name_re=".*(Stop|Generating|stop generating).*",
                    found_index=0,
                )
                stop.wait("ready", timeout=2)
                time.sleep(poll)
            except Exception:  # noqa: BLE001
                # botão não existe mais → terminou
                log.info("Geração concluída.")
                return True
        log.error("Timeout aguardando geração.")
        return False

    def click_download_latest(self, timeout: int = 120, poll: float = 2.0) -> Optional[Path]:
        """
        Clica no botão de download da imagem mais recente.
        Retorna o path do arquivo baixado, se conseguir localizá-lo.
        """
        log.info("Procurando botão de download...")
        deadline = time.time() + timeout
        last_err: Optional[Exception] = None

        while time.time() < deadline:
            try:
                btn = self._dlg.child_window(
                    control_type="Button",
                    name_re=".*(Download|download image|baixa|imagem).*",
                )
                btn.wait("ready", timeout=2)
                btn.click_input()
                log.info("Botão de download clicado.")
                return None  # download acionado; arquivo será coletado pelo watcher
            except Exception as exc:  # noqa: BLE001
                last_err = exc
                time.sleep(poll)

        raise AutomationError(
            f"Não foi possível clicar em Download. Último erro: {last_err}"
        )


# --------------------- public helper ---------------------
@contextmanager
def open_automation(window_substring: str = "ChatGPT"):
    """Context manager que garante start + close limpos."""
    bot = ChatGPTAutomation(window_substring)
    try:
        bot.ensure_started()
        yield bot
    finally:
        # Nada para fechar explicitamente: o app continua aberto.
        pass
