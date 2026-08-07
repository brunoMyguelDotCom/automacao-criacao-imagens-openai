"""
config.py
Carrega configurações do arquivo config.json e expõe como dicionário.
Suporta hot-reload simples (pode ser recarregado a qualquer momento).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict


PROJECT_ROOT: Path = Path(__file__).resolve().parent
CONFIG_PATH: Path = PROJECT_ROOT / "config.json"

_DEFAULTS: Dict[str, Any] = {
    "input_folder": "input",
    "output_folder": "output",
    "download_folder": "downloaded",
    "failed_folder": "failed",
    "processed_folder": "processed",
    "prompts_folder": "prompts",
    "logs_folder": "logs",
    "log_file": "logs/log.txt",
    "max_retries": 3,
    "wait_generation_timeout": 180,
    "wait_generation_poll": 2,
    "wait_download_timeout": 120,
    "wait_download_poll": 2,
    "watcher_enabled": False,
    "processed_action": "move",
    "use_clipboard_for_prompt": True,
    "chatgpt_window_title_substring": "ChatGPT",
    "image_extensions": [".jpg", ".jpeg", ".png", ".webp", ".bmp"],
}


def load_config(path: Path = CONFIG_PATH) -> Dict[str, Any]:
    """Lê o config.json e mescla com defaults. Sempre retorna dict."""
    if not path.exists():
        cfg = dict(_DEFAULTS)
    else:
        try:
            with path.open("r", encoding="utf-8") as fh:
                loaded = json.load(fh)
            if not isinstance(loaded, dict):
                loaded = {}
        except (json.JSONDecodeError, OSError):
            loaded = {}

        cfg = dict(_DEFAULTS)
        cfg.update(loaded)

    # Resolve paths relativos à raiz do projeto
    for key in (
        "input_folder",
        "output_folder",
        "download_folder",
        "failed_folder",
        "processed_folder",
        "prompts_folder",
        "logs_folder",
    ):
        val = cfg.get(key)
        if isinstance(val, str) and val:
            p = Path(val)
            if not p.is_absolute():
                p = PROJECT_ROOT / p
            cfg[f"{key}_resolved"] = p
        else:
            cfg[f"{key}_resolved"] = PROJECT_ROOT

    log_file = cfg.get("log_file")
    if isinstance(log_file, str) and log_file:
        lf = Path(log_file)
        if not lf.is_absolute():
            lf = PROJECT_ROOT / lf
        cfg["log_file_resolved"] = lf
    else:
        cfg["log_file_resolved"] = PROJECT_ROOT / "logs" / "log.txt"

    return cfg


CONFIG: Dict[str, Any] = load_config()


def reload_config() -> Dict[str, Any]:
    """Recarrega a config do disco e atualiza o módulo."""
    global CONFIG
    CONFIG = load_config()
    return CONFIG
