"""Resolução de caminhos para assets empacotados pelo PyInstaller.

Reúne as duas origens possíveis de arquivos da aplicação (assets
somente leitura, ícones, schema SQL inicial, etc.) atrás de uma
única camada:

- Em **desenvolvimento** (`python main.py`): o caminho é relativo
  à raiz do repositório, descoberta a partir deste arquivo.
- Em **build empacotado** (PyInstaller `--onefile` ou `--onedir`):
  o caminho aponta para `sys._MEIPASS`, o diretório temporário
  onde o bootloader do PyInstaller descompacta os arquivos de
  dados.

Por que isto importa:
    Sem este helper, qualquer código que fizer
    `Path(__file__).parent / "icons/foo.png"` ainda funciona
    durante o desenvolvimento, mas QUEBRA no executável —
    porque o arquivo `__file__` apontará para uma pasta
    temporária do PyInstaller diferente da esperada, e os
    assets não estarão lá. O helper detecta `_MEIPASS` e troca
    a raiz.

Separação entre aplicação e dados do usuário:
    Esta camada resolve APENAS assets somente-leitura da
    aplicação. Dados do usuário (banco SQLite, logs, presets
    editados pelo usuário, credenciais) continuam indo para
    `app.config.paths.get_app_data_dir()` — %APPDATA% no Windows,
    $XDG_DATA_HOME no Linux — nunca para o diretório do
    executável. Os dois caminhos são complementares, não
    conflitantes.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# `sys._MEIPASS` é injetado pelo PyInstaller no momento do boot
# do executável, apontando para a pasta temporária onde os
# arquivos de dados foram extraídos. Em desenvolvimento normal
# o atributo simplesmente não existe — usamos `getattr` defensivo.
_MEIPASS = getattr(sys, "_MEIPASS", None)

# PROJECT_ROOT é a raiz do repositório (a pasta que contém
# `main.py` e `assets/`). Em modo de desenvolvimento, esta é a
# raiz de resolução. No PyInstaller, o equivalente é `_MEIPASS`.
#
# Como `main.py` está sempre na raiz do repo, `__file__` deste
# módulo dá `.../app/config/resources.py`, e subimos 3 níveis
# para chegar ao repo root. Isso é robusto a symlinks porque
# usamos `.resolve()`.
_THIS_FILE = Path(__file__).resolve()
_PROJECT_ROOT_DEV = _THIS_FILE.parents[2]


def get_resource_root() -> Path:
    """Retorna a raiz onde assets somente-leitura estão localizados.

    Em desenvolvimento: a raiz do repositório.
    Em build PyInstaller: o `_MEIPASS`.

    Tudo em `assets/` do repositório é empacotado como `datas`
    no spec do PyInstaller, e esta função os expõe no runtime
    — tanto dev quanto frozen.
    """
    if _MEIPASS is not None:
        return Path(_MEIPASS)
    return _PROJECT_ROOT_DEV


def get_resource_path(*parts: str | os.PathLike[str]) -> Path:
    """Resolve um asset empacotado a partir de `get_resource_root()`.

    Uso::

        icon_path = get_resource_path("assets", "icons", "app.png")
        schema_path = get_resource_path("app", "data", "database", "migrations", "v001_initial.sql")

    Em desenvolvimento, isso aponta para `<repo>/assets/...`.
    No executável empacotado, aponta para `<_MEIPASS>/assets/...`.
    """
    return get_resource_root().joinpath(*parts)


def is_frozen() -> bool:
    """True quando rodando dentro de um bundle PyInstaller."""
    return _MEIPASS is not None


__all__ = [
    "get_resource_root",
    "get_resource_path",
    "is_frozen",
]