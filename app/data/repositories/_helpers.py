"""Helpers compartilhados pelos repositórios.

Conversões de data/hora e serialização de listas para a forma
esperada no SQLite.

Os repositórios individuais importam diretamente daí — sem
re-export, sem camada de facade.
"""

from __future__ import annotations

from datetime import datetime, timezone


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(s)
