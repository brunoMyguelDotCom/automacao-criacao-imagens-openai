"""Pacote raiz da aplicação GeradorImagensProduto.

Este pacote contém todas as camadas do sistema (core, data, ui, config).
A camada `core` é o coração do domínio e nunca deve importar PySide6;
a camada `ui` consome `core`; `data` é a única que toca SQLite/keyring/
arquivos de configuração.
"""

__version__ = "0.1.0"