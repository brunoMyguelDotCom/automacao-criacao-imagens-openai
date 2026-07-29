"""Persistência de presets de prompt (Prompt 4).

Camada que olha para a tabela `prompt_presets` do SQLite. Não
conhece UI. Não conhece a `CredentialManager`. As credenciais
moram em arquivos/cofres distintos — esta classe apenas consulta
o banco de presets.

Invariantes mantidas aqui:
- Sempre existe ao menos um preset no banco.
- No máximo um preset tem `is_default = True`.
- `id`, `created_at`, `updated_at` são controlados por aqui — a
  UI nunca os inventa.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from app.core.exceptions import AppError, InvalidParamsError
from app.core.models import (
    DEFAULT_FACTORY_DESCRIPTION,
    DEFAULT_FACTORY_NAME,
    DEFAULT_FACTORY_PROMPT,
    PromptPreset,
)
from app.data.database.connection import DatabaseConnection

logger = logging.getLogger(__name__)


class PresetStoreError(AppError):
    """Erros do store (DB, invariante violada, etc.)."""

    error_code = "ERR_DB"


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _parse_iso(s: str) -> datetime:
    # `datetime.fromisoformat` lida bem com `+00:00` (ISO 8601).
    return datetime.fromisoformat(s)


def _row_to_preset(row) -> PromptPreset:
    resolution = None
    if row["resolution_w"] is not None and row["resolution_h"] is not None:
        resolution = (row["resolution_w"], row["resolution_h"])
    return PromptPreset(
        id=row["id"],
        name=row["name"],
        description=row["description"] or "",
        prompt_text=row["prompt_text"],
        created_at=_parse_iso(row["created_at"]),
        updated_at=_parse_iso(row["updated_at"]),
        is_default=bool(row["is_default"]),
        model=row["model"],
        resolution=resolution,
        quality=row["quality"],
        output_format=row["output_format"],
        background=row["background"],
        n_variations=row["n_variations"],
    )


def _factory_default() -> PromptPreset:
    return PromptPreset(
        id=str(uuid.uuid4()),
        name=DEFAULT_FACTORY_NAME,
        description=DEFAULT_FACTORY_DESCRIPTION,
        prompt_text=DEFAULT_FACTORY_PROMPT,
        is_default=True,
    )


# --------------------------------------------------------------------------- #
# Store                                                                        #
# --------------------------------------------------------------------------- #


class PromptPresetStore:
    """Ponto de entrada para a tabela `prompt_presets`.

    Recebe um `DatabaseConnection` para permitir troca em testes.
    """

    def __init__(self, db: DatabaseConnection) -> None:
        self._db = db

    # ------------------------------------------------------------------ #
    # Leitura                                                             #
    # ------------------------------------------------------------------ #

    def list(self) -> list[PromptPreset]:
        with self._db.conn() as c:
            cur = c.execute(
                "SELECT * FROM prompt_presets ORDER BY is_default DESC, name ASC"
            )
            return [_row_to_preset(r) for r in cur.fetchall()]

    def get(self, preset_id: str) -> Optional[PromptPreset]:
        with self._db.conn() as c:
            cur = c.execute(
                "SELECT * FROM prompt_presets WHERE id = ?", (preset_id,)
            )
            row = cur.fetchone()
            return _row_to_preset(row) if row else None

    def get_default(self) -> Optional[PromptPreset]:
        with self._db.conn() as c:
            cur = c.execute(
                "SELECT * FROM prompt_presets WHERE is_default = 1 LIMIT 1"
            )
            row = cur.fetchone()
            return _row_to_preset(row) if row else None

    def count(self) -> int:
        with self._db.conn() as c:
            cur = c.execute("SELECT COUNT(*) FROM prompt_presets")
            return int(cur.fetchone()[0])

    # ------------------------------------------------------------------ #
    # Escrita                                                             #
    # ------------------------------------------------------------------ #

    def create(
        self,
        *,
        name: str,
        description: str = "",
        prompt_text: str = "",
        make_default: bool = False,
    ) -> PromptPreset:
        """Cria um preset novo. Se for default, desmarca qualquer outro.

        Levanta `InvalidParamsError` se o nome for vazio.
        """
        self._validate_name(name)
        now = datetime.now(timezone.utc)
        preset = PromptPreset(
            id=str(uuid.uuid4()),
            name=name.strip(),
            description=description,
            prompt_text=prompt_text,
            created_at=now,
            updated_at=now,
            is_default=make_default,
        )

        with self._db.conn() as c:
            if make_default:
                c.execute("UPDATE prompt_presets SET is_default = 0")
            c.execute(
                """
                INSERT INTO prompt_presets (
                    id, name, description, prompt_text, created_at, updated_at,
                    is_default, model, resolution_w, resolution_h, quality,
                    output_format, background, n_variations
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                (
                    preset.id,
                    preset.name,
                    preset.description,
                    preset.prompt_text,
                    _iso(preset.created_at),
                    _iso(preset.updated_at),
                    1 if preset.is_default else 0,
                    preset.model,
                    preset.resolution[0] if preset.resolution else None,
                    preset.resolution[1] if preset.resolution else None,
                    preset.quality,
                    preset.output_format,
                    preset.background,
                    preset.n_variations,
                ),
            )

        logger.info("Preset criado: %s (default=%s)", preset.id, preset.is_default)
        return preset

    def update(
        self,
        preset_id: str,
        *,
        name: Optional[str] = None,
        description: Optional[str] = None,
        prompt_text: Optional[str] = None,
    ) -> PromptPreset:
        """Edita nome/descrição/prompt_text. `id` e `created_at` ficam.

        Para trocar o `is_default`, use `set_default()`.
        """
        existing = self.get(preset_id)
        if existing is None:
            raise PresetStoreError(f"Preset {preset_id} não encontrado")

        if name is not None:
            self._validate_name(name)
            name = name.strip()

        merged = existing.with_updates(
            name=name if name is not None else existing.name,
            description=description if description is not None else existing.description,
            prompt_text=prompt_text if prompt_text is not None else existing.prompt_text,
        )

        with self._db.conn() as c:
            c.execute(
                """
                UPDATE prompt_presets
                SET name = ?, description = ?, prompt_text = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    merged.name,
                    merged.description,
                    merged.prompt_text,
                    _iso(merged.updated_at),
                    preset_id,
                ),
            )

        logger.info("Preset atualizado: %s", preset_id)
        return merged

    def duplicate(self, preset_id: str) -> PromptPreset:
        """Cria uma cópia com novo id e nome 'Cópia de X'."""
        existing = self.get(preset_id)
        if existing is None:
            raise PresetStoreError(f"Preset {preset_id} não encontrado")

        now = datetime.now(timezone.utc)
        # Resolve colisões de nome: "Cópia de X", "Cópia de X (2)", ...
        base_name = f"Cópia de {existing.name}"
        new_name = self._next_unique_name(base_name)

        copy = PromptPreset(
            id=str(uuid.uuid4()),
            name=new_name,
            description=existing.description,
            prompt_text=existing.prompt_text,
            created_at=now,
            updated_at=now,
            is_default=False,  # duplicata nunca é default
            model=existing.model,
            resolution=existing.resolution,
            quality=existing.quality,
            output_format=existing.output_format,
            background=existing.background,
            n_variations=existing.n_variations,
        )

        with self._db.conn() as c:
            c.execute(
                """
                INSERT INTO prompt_presets (
                    id, name, description, prompt_text, created_at, updated_at,
                    is_default, model, resolution_w, resolution_h, quality,
                    output_format, background, n_variations
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                (
                    copy.id,
                    copy.name,
                    copy.description,
                    copy.prompt_text,
                    _iso(copy.created_at),
                    _iso(copy.updated_at),
                    0,
                    copy.model,
                    copy.resolution[0] if copy.resolution else None,
                    copy.resolution[1] if copy.resolution else None,
                    copy.quality,
                    copy.output_format,
                    copy.background,
                    copy.n_variations,
                ),
            )

        logger.info("Preset duplicado: %s -> %s", preset_id, copy.id)
        return copy

    def delete(self, preset_id: str) -> None:
        """Exclui o preset. Impede se ele for o ÚNICO restante."""
        if self.count() <= 1:
            raise PresetStoreError(
                "Não é possível excluir o único preset do sistema. "
                "Crie outro antes de excluir este."
            )

        with self._db.conn() as c:
            cur = c.execute(
                "DELETE FROM prompt_presets WHERE id = ?", (preset_id,)
            )
            if cur.rowcount == 0:
                raise PresetStoreError(f"Preset {preset_id} não encontrado")

        logger.info("Preset excluído: %s", preset_id)

    def set_default(self, preset_id: str) -> PromptPreset:
        """Torna o preset default. Desmarca qualquer outro."""
        target = self.get(preset_id)
        if target is None:
            raise PresetStoreError(f"Preset {preset_id} não encontrado")

        with self._db.conn() as c:
            c.execute("UPDATE prompt_presets SET is_default = 0")
            c.execute(
                "UPDATE prompt_presets SET is_default = 1, updated_at = ? WHERE id = ?",
                (_iso(datetime.now(timezone.utc)), preset_id),
            )

        logger.info("Preset %s marcado como default", preset_id)
        # Recarrega para devolver o estado pós-update
        updated = self.get(preset_id)
        assert updated is not None
        return updated

    # ------------------------------------------------------------------ #
    # Fábrica                                                             #
    # ------------------------------------------------------------------ #

    def ensure_default(self) -> PromptPreset:
        """Garante que existe o preset padrão de fábrica.

        Idempotente. Se já houver algum preset marcado como default,
        mantém o atual. Se o banco estiver vazio, cria o default
        de fábrica. Se não houver default entre os presets existentes,
        marca o primeiro como default.
        """
        if self.count() == 0:
            factory = _factory_default()
            with self._db.conn() as c:
                c.execute(
                    """
                    INSERT INTO prompt_presets (
                        id, name, description, prompt_text, created_at, updated_at,
                        is_default, model, resolution_w, resolution_h, quality,
                        output_format, background, n_variations
                    ) VALUES (
                        ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?
                    )
                    """,
                    (
                        factory.id,
                        factory.name,
                        factory.description,
                        factory.prompt_text,
                        _iso(factory.created_at),
                        _iso(factory.updated_at),
                        factory.model,
                        factory.resolution[0] if factory.resolution else None,
                        factory.resolution[1] if factory.resolution else None,
                        factory.quality,
                        factory.output_format,
                        factory.background,
                        factory.n_variations,
                    ),
                )
            logger.info("Preset default de fábrica criado: %s", factory.id)
            return factory

        existing_default = self.get_default()
        if existing_default is not None:
            return existing_default

        # Tem presets, mas nenhum marcado: marca o mais antigo como default.
        with self._db.conn() as c:
            cur = c.execute(
                "SELECT id FROM prompt_presets ORDER BY created_at ASC LIMIT 1"
            )
            row = cur.fetchone()
            if row is None:
                # Não deveria acontecer (count() > 0), mas defensivo.
                factory = _factory_default()
                with self._db.conn() as c2:
                    c2.execute(
                        "INSERT INTO prompt_presets (id, name, description, prompt_text, created_at, updated_at, is_default) VALUES (?, ?, ?, ?, ?, ?, 1)",
                        (
                            factory.id,
                            factory.name,
                            factory.description,
                            factory.prompt_text,
                            _iso(factory.created_at),
                            _iso(factory.updated_at),
                        ),
                    )
                return factory
            first_id = row["id"]
            c.execute(
                "UPDATE prompt_presets SET is_default = 1 WHERE id = ?",
                (first_id,),
            )
        result = self.get(first_id)
        assert result is not None
        return result

    def restore_factory_default(self) -> PromptPreset:
        """Restaura o preset de fábrica (Prompt 4, item 6).

        - Se o preset de fábrica (mesmo nome) já existe: ele volta
          a ser o default. Outros defaults são desmarcados.
        - Se não existe: cria um novo e o marca como default.
        """
        with self._db.conn() as c:
            cur = c.execute(
                "SELECT id FROM prompt_presets WHERE name = ?",
                (DEFAULT_FACTORY_NAME,),
            )
            row = cur.fetchone()
            if row is not None:
                # Já existe — só garante que está marcado como default.
                c.execute("UPDATE prompt_presets SET is_default = 0")
                c.execute(
                    "UPDATE prompt_presets SET is_default = 1, updated_at = ? WHERE id = ?",
                    (_iso(datetime.now(timezone.utc)), row["id"]),
                )
                # Reabrir pela MESMA conexão: abrir outra conexão
                # concorrente leria o estado pré-transação por causa
                # do journaling/locking do SQLite.
                cur2 = c.execute(
                    "SELECT * FROM prompt_presets WHERE id = ?", (row["id"],)
                )
                result = _row_to_preset(cur2.fetchone())
                assert result is not None
                logger.info("Preset de fábrica restaurado (já existia): %s", row["id"])
                return result

        # Não existe: criar.
        factory = _factory_default()
        with self._db.conn() as c:
            c.execute("UPDATE prompt_presets SET is_default = 0")
            c.execute(
                """
                INSERT INTO prompt_presets (
                    id, name, description, prompt_text, created_at, updated_at,
                    is_default, model, resolution_w, resolution_h, quality,
                    output_format, background, n_variations
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                (
                    factory.id,
                    factory.name,
                    factory.description,
                    factory.prompt_text,
                    _iso(factory.created_at),
                    _iso(factory.updated_at),
                    factory.model,
                    factory.resolution[0] if factory.resolution else None,
                    factory.resolution[1] if factory.resolution else None,
                    factory.quality,
                    factory.output_format,
                    factory.background,
                    factory.n_variations,
                ),
            )
        logger.info("Preset de fábrica criado a partir de restore: %s", factory.id)
        return factory

    # ------------------------------------------------------------------ #
    # Helpers privados                                                   #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _validate_name(name: str) -> None:
        if not isinstance(name, str) or not name.strip():
            raise InvalidParamsError("O nome do preset não pode estar vazio.")

    def _next_unique_name(self, base: str) -> str:
        """'Cópia de X' → 'Cópia de X (2)' → 'Cópia de X (3)' …"""
        existing = {p.name for p in self.list()}
        if base not in existing:
            return base
        i = 2
        while True:
            candidate = f"{base} ({i})"
            if candidate not in existing:
                return candidate
            i += 1


__all__ = [
    "PromptPresetStore",
    "PresetStoreError",
]
