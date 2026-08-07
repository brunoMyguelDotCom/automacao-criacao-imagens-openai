"""
watcher.py
Watcher opcional: monitora a pasta de downloads para detectar novos arquivos
vindos do ChatGPT e dispará-los ao pipeline.

Usa watchdog quando disponível; senão, faz polling.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Callable, Optional

from logger import get_logger

log = get_logger("watcher")

try:
    from watchdog.events import FileSystemEventHandler  # type: ignore
    from watchdog.observers import Observer  # type: ignore
    _WATCHDOG_OK = True
except ImportError:  # pragma: no cover
    _WATCHDOG_OK = False


class _Handler(FileSystemEventHandler if _WATCHDOG_OK else object):
    def __init__(self, on_new: Callable[[Path], None]) -> None:
        super().__init__()
        self.on_new = on_new

    def on_created(self, event):  # type: ignore[override]
        if event.is_directory:
            return
        path = Path(event.src_path)
        # pequeno delay para o arquivo terminar de ser gravado
        threading.Timer(0.5, self.on_new, args=[path]).start()


class DownloadWatcher:
    """Monitora uma pasta; chama callback para cada novo arquivo."""

    def __init__(self, folder: Path, on_new_file: Callable[[Path], None]) -> None:
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        self.on_new_file = on_new_file
        self._observer = None
        self._polling = False
        self._poll_thread: Optional[threading.Thread] = None
        self._known: set = set()
        self._stop_event = threading.Event()

    def start(self) -> None:
        if _WATCHDOG_OK:
            log.info("Iniciando watchdog em: %s", self.folder)
            self._observer = Observer()
            self._observer.schedule(_Handler(self.on_new_file), str(self.folder), recursive=False)
            self._observer.start()
        else:
            log.warning("watchdog não instalado; usando polling em %s", self.folder)
            self._polling = True
            self._known = {p.resolve() for p in self.folder.iterdir() if p.is_file()}
            self._poll_thread = threading.Thread(target=self._poll_loop, daemon=True)
            self._poll_thread.start()

    def stop(self) -> None:
        if self._observer is not None:
            try:
                self._observer.stop()
                self._observer.join(timeout=3)
            except Exception as exc:  # noqa: BLE001
                log.warning("Erro parando observer: %s", exc)
        self._stop_event.set()
        if self._poll_thread is not None:
            self._poll_thread.join(timeout=2)

    def _poll_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                for p in self.folder.iterdir():
                    if not p.is_file():
                        continue
                    rp = p.resolve()
                    if rp in self._known:
                        continue
                    # espera o arquivo parar de crescer
                    size1 = p.stat().st_size
                    time.sleep(0.5)
                    size2 = p.stat().st_size if p.exists() else 0
                    if size1 == size2 and size1 > 0:
                        self._known.add(rp)
                        try:
                            self.on_new_file(p)
                        except Exception as exc:  # noqa: BLE001
                            log.error("on_new_file falhou: %s", exc)
            except FileNotFoundError:
                pass
            self._stop_event.wait(timeout=2.0)
