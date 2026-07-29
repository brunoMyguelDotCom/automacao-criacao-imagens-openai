"""Resolução de diretórios de dados do usuário, por sistema operacional.

Estas funções decidem onde o aplicativo grava logs, banco SQLite e demais
arquivos de configuração, seguindo as convenções nativas de cada SO:

- Windows: %APPDATA%/GeradorImagensProduto
- Linux:   ~/.local/share/GeradorImagensProduto  (respeitando XDG)

Elas criam o diretório automaticamente na primeira chamada, e nunca
dependem do diretório de trabalho atual (cwd).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "GeradorImagensProduto"


def get_app_data_dir() -> Path:
    """Retorna o diretório raiz de dados do usuário, criando-o se necessário.

    Windows: %APPDATA%/GeradorImagensProduto
    Linux:   $XDG_DATA_HOME/GeradorImagensProduto  ou
             ~/.local/share/GeradorImagensProduto
    macOS (fallback de segurança, fora do escopo atual): ~/Library/...
    """
    if sys.platform.startswith("win"):
        base = os.environ.get("APPDATA")
        if not base:
            base = str(Path.home() / "AppData" / "Roaming")
        root = Path(base) / APP_NAME
    else:
        # Unix-like (Linux é o alvo oficial, mas cobrimos outros
        # para evitar falhas em ambientes de desenvolvimento)
        xdg = os.environ.get("XDG_DATA_HOME")
        base = Path(xdg) if xdg else Path.home() / ".local" / "share"
        root = base / APP_NAME

    root.mkdir(parents=True, exist_ok=True)
    return root


def get_logs_dir() -> Path:
    """Retorna o diretório de logs, criando-o se necessário."""
    logs = get_app_data_dir() / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    return logs


def get_database_path() -> Path:
    """Retorna o caminho completo do arquivo SQLite (sem criá-lo).

    O arquivo só será criado de fato pela camada `data/database` no
    Prompt 8. Aqui devolvemos apenas o caminho resolvido.
    """
    return get_app_data_dir() / "database" / "app.sqlite3"


def get_config_dir() -> Path:
    """Retorna o diretório de arquivos de configuração persistentes."""
    cfg = get_app_data_dir() / "config"
    cfg.mkdir(parents=True, exist_ok=True)
    return cfg