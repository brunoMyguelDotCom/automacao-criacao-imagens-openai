"""Exceções tipadas do domínio.

Toda falha previsível do sistema deve ser representada por uma classe
deste módulo — nunca por um `except Exception` genérico seguido de
`pass`. A classe base `AppError` carrega um `error_code` estável, que
será usado para classificação, retry automático e exibição na UI.

A taxonomia reflete os códigos previstos em
`estrutura_geracao_imagens_v2.md`.
"""

from __future__ import annotations


class AppError(Exception):
    """Classe base de todas as exceções da aplicação.

    Attributes:
        error_code: código estável e estável para classificação
            (ex.: "ERR_AUTH", "ERR_LOCAL_IO"). Nunca localize este
            código — é a chave usada em logs e em decisões de retry.
        message: mensagem amigável para o usuário (em português).
        cause: exceção original, se houver.
    """

    error_code: str = "ERR_UNKNOWN"

    def __init__(self, message: str = "", *, cause: Exception | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.cause = cause


class AuthError(AppError):
    """Chave de API inválida, ausente ou revogada. Sem retry."""

    error_code = "ERR_AUTH"


class RateLimitError(AppError):
    """API retornou 429 (Too Many Requests). Retry com backoff."""

    error_code = "ERR_RATE_LIMIT"


class TimeoutError(AppError):
    """Timeout de rede ao chamar a API. Retry."""

    error_code = "ERR_TIMEOUT"


class ConnectionAppError(AppError):
    """Falha de conexão (DNS, sem internet). Retry."""

    error_code = "ERR_CONNECTION"


class ServerError(AppError):
    """Erro 5xx da API. Retry limitado."""

    error_code = "ERR_SERVER"


class ContentRejectedError(AppError):
    """Conteúdo rejeitado pela política da OpenAI. Sem retry."""

    error_code = "ERR_CONTENT_REJECTED"


class InvalidParamsError(AppError):
    """Parâmetros de geração inválidos/mal formados. Sem retry."""

    error_code = "ERR_INVALID_PARAMS"


class QuotaExceededError(AppError):
    """Créditos/limite mensal esgotado. Sem retry."""

    error_code = "ERR_QUOTA_EXCEEDED"


class LocalIOError(AppError):
    """Erro de I/O local (disco cheio, permissão, arquivo corrompido). Sem retry."""

    error_code = "ERR_LOCAL_IO"


class DatabaseError(AppError):
    """Erro de banco (banco bloqueado/corrompido). Interrompe e alerta."""

    error_code = "ERR_DB"


__all__ = [
    "AppError",
    "AuthError",
    "RateLimitError",
    "TimeoutError",
    "ConnectionAppError",
    "ServerError",
    "ContentRejectedError",
    "InvalidParamsError",
    "QuotaExceededError",
    "LocalIOError",
    "DatabaseError",
]