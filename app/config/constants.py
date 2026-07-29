"""Constantes e valores padrão do aplicativo.

Concentre aqui nomes canônicos, tamanhos de UI, e qualquer literal
compartilhado entre camadas. Nada de caminhos de SO hardcoded —
use `app.config.paths` para isso.
"""

# UI
APP_TITLE = "Gerador de Imagens de Produto"
WINDOW_MIN_WIDTH = 1000
WINDOW_MIN_HEIGHT = 700

# Banco de dados
SCHEMA_VERSION = 2

# Logging
LOG_LEVEL_DEFAULT = "INFO"

# Configurações de lote (Prompt 5).
# O "tamanho máximo de lote" é um limite OPERACIONAL — não é
# contagem de tokens (o custo real depende de modelo/resolução/
# qualidade e não é determinístico apenas pelo número de imagens).
MAX_BATCH_SIZE_DEFAULT = 20
MAX_BATCH_SIZE_MIN = 1
MAX_BATCH_SIZE_MAX = 200
