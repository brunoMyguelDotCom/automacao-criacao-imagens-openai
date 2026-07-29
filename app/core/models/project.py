"""Modelo de domínio `Project` (Prompt 8).

Estrutura persistente que agrupa N `Batch`s sob uma mesma
configuração de pastas (entrada/saída). Foi introduzido agora para
formalizar a hierarquia:

    Project 1───* Batch 1───* ImageJob 1───* GenerationAttempt

Esta dataclass é FROZEN — qualquer "mutação" gera nova instância via
`replace()` (dataclasses.replace). Não conhece o banco.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path


def _new_uuid() -> str:
    return str(uuid.uuid4())


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class Project:
    """Projeto persistido.

    Attributes:
        id: UUID v4 como string.
        name: nome visível do projeto (definido pelo usuário).
        input_folder_path: pasta de entrada — onde estão as imagens
            originais.
        output_folder_path: pasta de saída — onde os lotes gravam.
        created_at: timestamp de criação (UTC, ISO 8601).
    """

    id: str = field(default_factory=_new_uuid)
    name: str = ""
    input_folder_path: Path = field(default_factory=Path)
    output_folder_path: Path = field(default_factory=Path)
    created_at: datetime = field(default_factory=_utcnow)


__all__ = ["Project"]