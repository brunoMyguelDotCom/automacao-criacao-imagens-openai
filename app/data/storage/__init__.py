"""Persistência de artefatos não-relacionais e relacional.

- `CredentialManager` (keyring + fallback criptografado) — chave da
  API. NUNCA toca SQLite.
- `PromptPresetStore` — presets de prompt, em SQLite, em tabela
  própria. NUNCA toca a credencial.
- `AppConfigStore` — configurações gerais chave/valor (ex:
  max_batch_size). Tabela separada.

A camada `storage` é a única que precisa conhecer keyring, arquivos
de configuração e o banco SQLite.
"""

from app.data.storage.app_config_store import (
    AppConfigError,
    AppConfigStore,
    KEY_MAX_BATCH_SIZE,
)
from app.data.storage.credential_manager import (
    CredentialBackendUnavailable,
    CredentialManager,
    CredentialStatus,
    CredentialTestResult,
    FORCE_FALLBACK_ENV,
    SERVICE_NAME,
)
from app.data.storage.prompt_preset_store import (
    PresetStoreError,
    PromptPresetStore,
)

__all__ = [
    "CredentialManager",
    "CredentialStatus",
    "CredentialTestResult",
    "CredentialBackendUnavailable",
    "SERVICE_NAME",
    "FORCE_FALLBACK_ENV",
    "PromptPresetStore",
    "PresetStoreError",
    "AppConfigStore",
    "AppConfigError",
    "KEY_MAX_BATCH_SIZE",
]