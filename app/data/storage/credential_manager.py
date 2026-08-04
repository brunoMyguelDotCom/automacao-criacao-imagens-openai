"""Armazenamento seguro da chave de API da OpenAI.

Dois backends:
1. **Primário: `keyring`** (Credential Manager no Windows, Secret
   Service/libsecret no Linux, Keychain no macOS). É o caminho
   preferido — o cofre do próprio sistema operacional guarda a chave
   e o nosso processo só guarda uma referência.
2. **Fallback: arquivo criptografado com Fernet** em
   `app.config.paths.get_app_data_dir()`. Usado **somente** se o
   backend keyring não estiver disponível no ambiente (detectado
   automaticamente). A chave de criptografia fica em arquivo
   separado do arquivo que guarda a API key criptografada.

O fallback é explicitamente secundário: quem tiver acesso de leitura
à pasta de dados do usuário E souber onde está a chave de
criptografia pode decifrar. É mais forte que texto puro, mas mais
fraco que o cofre do SO. Isso é documentado em código e no README.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Protocol

import keyring
from cryptography.fernet import Fernet, InvalidToken
from keyring.errors import KeyringError, NoKeyringError

from app.config.paths import get_app_data_dir
from app.core.exceptions import (
    AppError,
    AuthError,
    ConnectionAppError,
    InvalidParamsError,
    ServerError,
)

logger = logging.getLogger(__name__)

# Nome único do app no cofre do SO. Nunca reaproveite um nome genérico
# ("api_keys", "openai", etc.) — outro software pode usar o mesmo.
SERVICE_NAME = "GeradorImagensProduto"
KEYRING_USERNAME = "openai_api_key"

# Arquivos do fallback criptografado. Em arquivos SEPARADOS — se
# ficassem juntos, um único backup seu já entregaria chave + cifrador.
_FALLBACK_KEY_FILENAME = "credential.key"
_FALLBACK_DATA_FILENAME = "credential.bin"

# Nome da env var que pode forçar o fallback (usada em testes e em
# ambientes sem cofre do SO, tipo CI headless sem Secret Service).
FORCE_FALLBACK_ENV = "GERADORIMAGENS_FORCE_FILE_BACKEND"


# --------------------------------------------------------------------------- #
# Tipos públicos                                                              #
# --------------------------------------------------------------------------- #


class CredentialStatus(str, Enum):
    """Resultado da validação da credencial contra a API da OpenAI."""

    VALID = "VALID"
    INVALID_KEY = "INVALID_KEY"
    NETWORK_ERROR = "NETWORK_ERROR"
    SERVICE_UNAVAILABLE = "SERVICE_UNAVAILABLE"


@dataclass(frozen=True)
class CredentialTestResult:
    """Resultado de `CredentialManager.test_key()`.

    `message` é uma frase amigável em português, pronta para mostrar
    na UI. Nunca inclui a chave.
    """

    status: CredentialStatus
    message: str


class CredentialBackendUnavailable(AppError):
    """Backend keyring indisponível e o fallback também falhou."""

    error_code = "ERR_LOCAL_IO"


# --------------------------------------------------------------------------- #
# Protocolo interno                                                           #
# --------------------------------------------------------------------------- #


class _Backend(Protocol):
    """Contrato mínimo dos dois backends suportados."""

    backend_name: str

    def get(self) -> str | None: ...
    def set(self, key: str) -> None: ...
    def delete(self) -> None: ...


# --------------------------------------------------------------------------- #
# Backend primário: keyring                                                   #
# --------------------------------------------------------------------------- #


class _KeyringBackend:
    backend_name = "keyring"

    def __init__(self, service: str = SERVICE_NAME, username: str = KEYRING_USERNAME) -> None:
        self._service = service
        self._username = username

    def get(self) -> str | None:
        try:
            return keyring.get_password(self._service, self._username)
        except (KeyringError, NoKeyringError) as exc:
            # Detecta indisponibilidade e propaga — CredentialManager
            # decide se cai para o fallback.
            logger.debug("keyring indisponível no get(): %s", exc.__class__.__name__)
            raise CredentialBackendUnavailable("keyring indisponível") from exc

    def set(self, key: str) -> None:
        try:
            keyring.set_password(self._service, self._username, key)
        except (KeyringError, NoKeyringError) as exc:
            logger.debug("keyring indisponível no set(): %s", exc.__class__.__name__)
            raise CredentialBackendUnavailable("keyring indisponível") from exc

    def delete(self) -> None:
        try:
            keyring.delete_password(self._service, self._username)
        except (KeyringError, NoKeyringError) as exc:
            logger.debug("keyring indisponível no delete(): %s", exc.__class__.__name__)
            raise CredentialBackendUnavailable("keyring indisponível") from exc


# --------------------------------------------------------------------------- #
# Backend fallback: arquivo criptografado                                     #
# --------------------------------------------------------------------------- #


class _EncryptedFileBackend:
    backend_name = "encrypted-file"

    def __init__(
        self,
        data_dir: Path | None = None,
        key_filename: str = _FALLBACK_KEY_FILENAME,
        data_filename: str = _FALLBACK_DATA_FILENAME,
    ) -> None:
        self._dir = data_dir or get_app_data_dir()
        self._key_path = self._dir / key_filename
        self._data_path = self._dir / data_filename

    def _load_or_create_fernet(self) -> Fernet:
        """Garante um Fernet operacional, criando a chave se preciso.

        A chave de criptografia é gerada com `Fernet.generate_key()`,
        que usa os.tandon via `secrets.token_bytes` — sem nada
        determinístico.
        """
        try:
            if self._key_path.exists():
                key_material = self._key_path.read_bytes()
            else:
                key_material = Fernet.generate_key()
                # Cria o arquivo com permissões somente-leitura-para-o-dono
                # (best-effort — em Windows isso é parcialmente respeitado).
                fd = os.open(
                    self._key_path,
                    flags=os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
                    mode=0o600,
                )
                with os.fdopen(fd, "wb") as f:
                    f.write(key_material)
            return Fernet(key_material)
        except OSError as exc:
            raise CredentialBackendUnavailable(
                "Não foi possível preparar o fallback criptografado"
            ) from exc

    def get(self) -> str | None:
        if not self._data_path.exists():
            return None
        try:
            fernet = self._load_or_create_fernet()
            token = self._data_path.read_bytes()
            return fernet.decrypt(token).decode("utf-8")
        except InvalidToken:
            logger.warning("Token do arquivo de credencial é inválido — descartando")
            return None
        except OSError as exc:
            raise CredentialBackendUnavailable(
                "Falha lendo credencial criptografada"
            ) from exc

    def set(self, key: str) -> None:
        try:
            fernet = self._load_or_create_fernet()
            token = fernet.encrypt(key.encode("utf-8"))
            fd = os.open(
                self._data_path,
                flags=os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
                mode=0o600,
            )
            with os.fdopen(fd, "wb") as f:
                f.write(token)
        except OSError as exc:
            raise CredentialBackendUnavailable(
                "Falha gravando credencial criptografada"
            ) from exc

    def delete(self) -> None:
        for path in (self._data_path, self._key_path):
            try:
                if path.exists():
                    path.unlink()
            except OSError as exc:
                raise CredentialBackendUnavailable(
                    "Falha removendo credencial criptografada"
                ) from exc


# --------------------------------------------------------------------------- #
# Fachada pública                                                             #
# --------------------------------------------------------------------------- #


class CredentialManager:
    """Ponto único de entrada para a chave da OpenAI.

    Responsabilidades:
        - persistir/recuperar/remover a chave (escolhendo backend);
        - validar a chave com uma chamada real à API;
        - NUNCA logar, expor em exceção, ou persistir em texto puro.

    Não conhece UI. O `SettingsDialog` (app/ui/dialogs) consome este
    objeto e formata a saída.
    """

    def __init__(
        self,
        backend: _Backend | None = None,
        openai_factory=None,
    ) -> None:
        # Injeção para testes; o default monta a hierarquia real.
        if backend is not None:
            self._backend: _Backend = backend
        else:
            self._backend = self._build_default_backend_chain()

        # `openai_factory()` deve devolver um cliente `openai.OpenAI`
        # já configurado. Em produção, lê a chave via `get_key()`; em
        # testes, recebe um mock.
        self._openai_factory = openai_factory or self._default_openai_factory

    # ------------------------------------------------------------------ #
    # Backend                                                            #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _build_default_backend_chain() -> _Backend:
        """Tenta keyring; se indisponível ou se a env var de override
        estiver setada, vai direto para o arquivo criptografado.
        """
        if os.environ.get(FORCE_FALLBACK_ENV) == "1":
            logger.info("Backend forçado para arquivo criptografado por env var")
            return _EncryptedFileBackend()

        try:
            kr = keyring.get_keyring()
            # `get_keyring()` no Linux pode devolver um backend
            # "fail" (keyring.backends.fail.Keyring) quando não há
            # Secret Service. Esse objeto NÃO levanta exceção ao
            # ser instanciado — só ao usar. Testamos isso aqui:
            kr.get_password(SERVICE_NAME, "__probe__")
        except Exception as exc:  # noqa: BLE001 — probing de backend
            logger.info("keyring indisponível, usando arquivo criptografado (%s)", exc.__class__.__name__)
            return _EncryptedFileBackend()
        else:
            return _KeyringBackend()

    @property
    def backend_name(self) -> str:
        return self._backend.backend_name

    # ------------------------------------------------------------------ #
    # CRUD                                                               #
    # ------------------------------------------------------------------ #

    def save_key(self, key: str) -> None:
        self._validate_format(key)
        self._backend.set(key)
        # Log intencionalmente sem nenhum fragmento da chave — usamos
        # apenas o backend e o tamanho, jamais qualquer substring.
        logger.info("Credencial salva (backend=%s, length=%d)", self.backend_name, len(key))

    def get_key(self) -> str | None:
        return self._backend.get()

    def has_key(self) -> bool:
        return self.get_key() is not None

    def delete_key(self) -> None:
        self._backend.delete()
        logger.info("Credencial removida (backend=%s)", self.backend_name)

    # ------------------------------------------------------------------ #
    # Validação contra a API                                             #
    # ------------------------------------------------------------------ #

    def test_key(self, key: str) -> CredentialTestResult:
        """Chamada mínima e barata à API para classificar a chave.

        Usa `client.models.list()` com limite de 1 item. Classifica o
        resultado nos 4 status possíveis. NÃO valida por heurística
        de prefixo/tamanho.
        """
        self._validate_format(key)

        try:
            client = self._openai_factory(key)
            client.models.list()
        except _AUTH_EXCEPTIONS as exc:
            logger.info("test_key() -> chave inválida (%s)", exc.__class__.__name__)
            return CredentialTestResult(
                status=CredentialStatus.INVALID_KEY,
                message="Chave inválida ou revogada pela OpenAI.",
            )
        except _NETWORK_EXCEPTIONS as exc:
            logger.info("test_key() -> erro de rede (%s)", exc.__class__.__name__)
            return CredentialTestResult(
                status=CredentialStatus.NETWORK_ERROR,
                message="Sem conexão com a internet ou DNS falhou.",
            )
        except _SERVER_EXCEPTIONS as exc:
            logger.info("test_key() -> serviço indisponível (%s)", exc.__class__.__name__)
            return CredentialTestResult(
                status=CredentialStatus.SERVICE_UNAVAILABLE,
                message="Serviço da OpenAI temporariamente indisponível.",
            )
        except Exception as exc:  # noqa: BLE001 — última linha de defesa
            # Qualquer outra coisa: tratar como indisponibilidade para
            # não bloquear o usuário; o erro completo vai para o log
            # sem nenhum fragmento da chave.
            logger.warning("test_key() falhou de forma inesperada: %s", exc.__class__.__name__)
            return CredentialTestResult(
                status=CredentialStatus.SERVICE_UNAVAILABLE,
                message="Não foi possível validar a chave agora. Tente novamente.",
            )

        logger.info("test_key() -> chave válida")
        return CredentialTestResult(
            status=CredentialStatus.VALID,
            message="Chave válida.",
        )

    def test_saved_key(self) -> CredentialTestResult | None:
        """Atalho: testa a chave já salva, se houver. Retorna None
        quando não há chave salva."""
        key = self.get_key()
        if key is None:
            return None
        return self.test_key(key)

    # ------------------------------------------------------------------ #
    # Helpers                                                            #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _validate_format(key: str) -> None:
        """Validação de formato MÍNIMA — só bloqueia entradas
        claramente inúteis. A validação real acontece em `test_key`.
        """
        if not isinstance(key, str) or not key.strip():
            raise InvalidParamsError("A chave não pode estar vazia.")

    def _default_openai_factory(self, key: str):
        """Importação tardia para que `app.data.storage` não importe
        `openai` quando o usuário só usa o backend de credenciais.
        """
        from openai import OpenAI

        from app.core.providers.openai_image_generation_provider import (
            OPENAI_API_BASE_URL,
            _assert_openai_base_url,
        )

        client = OpenAI(
            api_key=key,
            timeout=10.0,
            base_url=OPENAI_API_BASE_URL,
        )
        _assert_openai_base_url(client)
        return client


# --------------------------------------------------------------------------- #
# Classes de exceção da OpenAI (resolvidas tardiamente para não amarrar       #
# `app.data.storage` ao SDK na importação do módulo — só quando alguém       #
# chama `test_key`).                                                          #
# --------------------------------------------------------------------------- #

_AUTH_EXCEPTIONS: tuple[type[BaseException], ...] = ()
_NETWORK_EXCEPTIONS: tuple[type[BaseException], ...] = ()
_SERVER_EXCEPTIONS: tuple[type[BaseException], ...] = ()


def _resolve_openai_exceptions() -> None:
    global _AUTH_EXCEPTIONS, _NETWORK_EXCEPTIONS, _SERVER_EXCEPTIONS
    if _AUTH_EXCEPTIONS:
        return
    from openai import (
        APIConnectionError,
        APITimeoutError,
        AuthenticationError,
        BadRequestError,
        InternalServerError,
    )

    _AUTH_EXCEPTIONS = (AuthenticationError, BadRequestError)
    _NETWORK_EXCEPTIONS = (APIConnectionError, APITimeoutError)
    _SERVER_EXCEPTIONS = (InternalServerError,)


# Re-export dos erros tipados do app, para que quem consome este
# módulo tenha um ponto único de importação.
__all__ = [
    "CredentialManager",
    "CredentialStatus",
    "CredentialTestResult",
    "CredentialBackendUnavailable",
    "SERVICE_NAME",
    "FORCE_FALLBACK_ENV",
    # mapeamento útil para a UI (não estritamente necessário,
    # mas ajuda quem for montar o diálogo)
    "AuthError",
    "ConnectionAppError",
    "ServerError",
]


# Inicializa o mapeamento de exceções na importação — barato e
# garante que `test_key` funcione sem passos extras.
_resolve_openai_exceptions()