"""Bundle de diagnóstico + sanitização de segredos (Prompt 10, regra 6).

Quando o usuário clica em "Exportar diagnóstico", geramos um arquivo
``.txt`` legível que contém:
    * Cabeçalho com versão do app, plataforma, timestamp UTC.
    * Lista de features ativas (configurações que o app consegue reler).
    * Caminho do banco de dados + tamanho do arquivo.
    * Caminho do log mais recente + tamanho.
    * Estatísticas agregadas (nº de projetos, lotes, jobs, sucessos, falhas).
    * Tail do log (sanitizado).
    * Ambiente mínimo (Python, plataforma, Qt).

Sanitização (NUNCA vaza segredos no bundle):
    * `sk-…`, `sk-proj-…` → `sk-***`
    * `Bearer <token>` → `Bearer ***`
    * Blocos hexadecimais longos (≥40 chars) → mascarados.
    * Linhas com "api_key", "secret", "password" no nome → redacted.
    * Prompts longos (>200 chars) → truncados com "…[truncated]".

Sanitização é feita por `sanitize_text()` (pura) — fácil de testar.
"""

from __future__ import annotations

import logging
import os
import platform
import re
import socket
import sys
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Sanitização                                                                  #
# --------------------------------------------------------------------------- #


# OpenAI keys: sk-...  (com sufixo opcional -proj-..., ou T... para novas keys)
_OPENAI_KEY_RE = re.compile(r"sk-(?:proj-)?[A-Za-z0-9_-]{20,}")
# Bearer tokens (genéricos — qualquer coisa após "Bearer " até whitespace)
_BEARER_RE = re.compile(r"(?i)(Bearer)\s+[A-Za-z0-9._\-]{12,}")
# Hex blocks (40+ chars) — provável API key alternativa
_HEX_LONG_RE = re.compile(r"\b[A-Fa-f0-9]{40,}\b")
# Linhas com nome de campo sensível (json-style ou key=value)
_SECRET_LINE_RE = re.compile(
    r"(?im)^\s*(?:[\"']?(?:api[_-]?key|secret|password|token|credential)[\"']?\s*[:=]\s*)"
    r"[\"']?([^\s\"',}]+)"
)


def sanitize_text(text: str) -> str:
    """Aplica TODAS as regras de sanitização. Retorna string segura para log.

    Idempotente — aplicar 2x produz o mesmo resultado (importante porque
    sanitização pode estar encadeada em múltiplos filtros).
    """
    if not text:
        return text
    out = text
    out = _OPENAI_KEY_RE.sub("sk-***", out)
    out = _BEARER_RE.sub(r"\1 ***", out)
    out = _HEX_LONG_RE.sub("***HEX***", out)
    out = _SECRET_LINE_RE.sub(r"\1***REDACTED***", out)
    return out


def sanitize_prompt(prompt: str, *, max_length: int = 200) -> str:
    """Trunca prompt longo para evitar despejo de IP em logs/diag.

    Aplicado antes de `sanitize_text` para garantir que qualquer chave
    embedada no prompt também seja sanitizada.
    """
    if len(prompt) <= max_length:
        return sanitize_text(prompt)
    truncated = prompt[:max_length] + "…[truncated]"
    return sanitize_text(truncated)


# --------------------------------------------------------------------------- #
# Bundle de diagnóstico                                                       #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class DiagnosticBundle:
    """Conteúdo do bundle de diagnóstico (após sanitização).

    Attributes:
        header: cabeçalho com versão/plataforma/timestamp.
        body: corpo principal (config, paths, stats, log tail).
        format_version: tag de versão do schema do bundle.
    """

    header: str
    body: str
    format_version: str = "v1"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _safe_read_text(path: Path, *, max_bytes: int = 4096) -> str:
    """Lê no máximo `max_bytes` do final de um arquivo texto. Tolerante a erro."""
    try:
        if not path.exists():
            return "(arquivo ausente)"
        with path.open("rb") as f:
            try:
                f.seek(-max_bytes, os.SEEK_END)
            except OSError:
                f.seek(0)
            data = f.read()
        return data.decode("utf-8", errors="replace")
    except OSError as exc:
        return f"(erro ao ler: {exc})"


def _file_size(path: Path) -> str:
    try:
        if not path.exists():
            return "ausente"
        return f"{path.stat().st_size} bytes"
    except OSError:
        return "indisponível"


def _python_version() -> str:
    return (
        f"{sys.version_info.major}.{sys.version_info.minor}."
        f"{sys.version_info.micro}"
    )


def _qt_version() -> str:
    try:
        from PySide6.QtCore import qVersion

        return qVersion()
    except Exception:  # noqa: BLE001
        return "indisponível"


def build_diagnostic_bundle(
    *,
    database_path: Optional[Path] = None,
    log_path: Optional[Path] = None,
    stats: Optional[dict[str, int]] = None,
    features: Optional[dict[str, str]] = None,
    log_tail_bytes: int = 4096,
) -> DiagnosticBundle:
    """Monta um `DiagnosticBundle` com todas as seções esperadas.

    Args:
        database_path: caminho do arquivo SQLite (opcional).
        log_path: caminho do log mais recente (opcional).
        stats: contadores pré-agregados (projetos, lotes, jobs, sucessos).
        features: pares chave→valor com flags de configuração ativa.
        log_tail_bytes: quantos bytes do final do log incluir.
    """
    stats = stats or {}
    features = features or {}

    header_lines = [
        "== DIAGNÓSTICO DO APLICATIVO ==",
        f"Gerado em: {_utc_now_iso()}",
        f"Plataforma: {platform.system()} {platform.release()} ({platform.machine()})",
        f"Hostname: {socket.gethostname()}",
        f"Python: {_python_version()}",
        f"Qt: {_qt_version()}",
        "",
    ]
    header = "\n".join(header_lines)

    sections: list[str] = []

    # 1. Caminhos
    sections.append("## Caminhos")
    sections.append(
        f"Banco de dados: {database_path or '(desconhecido)'} "
        f"({_file_size(database_path) if database_path else 'n/d'})"
    )
    sections.append(
        f"Log atual: {log_path or '(desconhecido)'} "
        f"({_file_size(log_path) if log_path else 'n/d'})"
    )
    sections.append("")

    # 2. Features ativas
    if features:
        sections.append("## Configuração ativa")
        for k, v in features.items():
            sections.append(f"  {k}: {sanitize_text(str(v))}")
        sections.append("")

    # 3. Estatísticas
    if stats:
        sections.append("## Estatísticas")
        for k, v in stats.items():
            sections.append(f"  {k}: {v}")
        sections.append("")

    # 4. Tail do log (sanitizado)
    sections.append("## Log (final, sanitizado)")
    if log_path is not None:
        tail = _safe_read_text(log_path, max_bytes=log_tail_bytes)
    else:
        tail = "(caminho do log não configurado)"
    sections.append(sanitize_text(tail))
    sections.append("")

    # 5. Fim
    sections.append("## Fim do diagnóstico")
    body = "\n".join(sections)
    return DiagnosticBundle(header=header, body=body)


def write_diagnostic_bundle(
    target: Path,
    bundle: DiagnosticBundle,
) -> Path:
    """Escreve o bundle em `target` (`.txt`). Cria diretórios pais."""
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        bundle.header + "\n" + bundle.body,
        encoding="utf-8",
        errors="replace",
    )
    return target


__all__ = [
    "DiagnosticBundle",
    "sanitize_text",
    "sanitize_prompt",
    "build_diagnostic_bundle",
    "write_diagnostic_bundle",
]