"""Repositório de `Project` (Prompt 8).

CRUD puro sobre a tabela `projects`. Não valida regras de negócio
("nome único", "pastas existem") — isso é responsabilidade da camada
de serviço. Tudo o que o repo faz é SQL + dataclass mapping.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from app.core.models import Project
from app.data.database.connection import DatabaseConnection
from app.data.repositories._helpers import _iso, _parse_iso

logger = logging.getLogger(__name__)


def _row_to_project(row) -> Project:
    return Project(
        id=row["id"],
        name=row["name"],
        input_folder_path=Path(row["input_folder_path"]),
        output_folder_path=Path(row["output_folder_path"]),
        created_at=_parse_iso(row["created_at"]),
    )


class ProjectRepository:
    """Persistência de `Project` em SQLite."""

    def __init__(self, db: DatabaseConnection) -> None:
        self._db = db
        # Garante PRAGMAs (WAL + busy_timeout) na primeira abertura.
        from app.data.repositories import _ensure_sqlite_pragmas

        _ensure_sqlite_pragmas(db)

    # ------------------------------------------------------------------ #
    # Escrita                                                             #
    # ------------------------------------------------------------------ #

    def create(
        self,
        *,
        name: str,
        input_folder_path: Path | str,
        output_folder_path: Path | str,
        id: Optional[str] = None,
        created_at: Optional[datetime] = None,
    ) -> Project:
        """Cria um projeto. Auto-gera `id` UUID e `created_at` se omitidos.

        Aceitar `id`/`created_at` é o padrão dos repositórios daqui
        para permitir testes determinísticos.
        """
        project = Project(
            id=id or str(uuid.uuid4()),
            name=name,
            input_folder_path=Path(input_folder_path),
            output_folder_path=Path(output_folder_path),
            created_at=created_at or datetime.now(timezone.utc),
        )
        with self._db.conn() as c:
            c.execute(
                """
                INSERT INTO projects (id, name, input_folder_path, output_folder_path, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    project.id,
                    project.name,
                    str(project.input_folder_path),
                    str(project.output_folder_path),
                    _iso(project.created_at),
                ),
            )
        logger.info("Project criado: %s (name=%s)", project.id, project.name)
        return project

    def delete(self, project_id: str) -> None:
        """Exclui o projeto. Cascateia para batches e image_jobs.

        Se o id não existir, é no-op (idempotente).
        """
        with self._db.conn() as c:
            cur = c.execute("DELETE FROM projects WHERE id = ?", (project_id,))
            if cur.rowcount:
                logger.info("Project excluído: %s", project_id)

    # ------------------------------------------------------------------ #
    # Leitura                                                             #
    # ------------------------------------------------------------------ #

    def get(self, project_id: str) -> Optional[Project]:
        with self._db.conn() as c:
            cur = c.execute("SELECT * FROM projects WHERE id = ?", (project_id,))
            row = cur.fetchone()
            return _row_to_project(row) if row else None

    def list(self) -> list[Project]:
        with self._db.conn() as c:
            cur = c.execute(
                "SELECT * FROM projects ORDER BY created_at DESC"
            )
            return [_row_to_project(r) for r in cur.fetchall()]

    def find_by_name(self, name: str) -> Optional[Project]:
        """Primeiro projeto com nome igual. None se não houver."""
        with self._db.conn() as c:
            cur = c.execute(
                "SELECT * FROM projects WHERE name = ? ORDER BY created_at DESC LIMIT 1",
                (name,),
            )
            row = cur.fetchone()
            return _row_to_project(row) if row else None


__all__ = ["ProjectRepository"]
