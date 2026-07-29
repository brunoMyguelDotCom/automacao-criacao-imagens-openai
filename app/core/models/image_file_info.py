"""Modelo de domínio `ImageFileInfo` e tipos auxiliares.

Representa o resultado da análise de UM arquivo de imagem pela
etapa de scanner (Prompt 3). Não conhece UI nem persistência — é
apenas uma estrutura de dados pura que pode ser serializada,
persistida e exibida em prompts seguintes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class ImageStatus(str, Enum):
    """Status de validação de um arquivo de imagem.

    Valores:
        VALID: Pillow abriu, decodificou e reabriu o arquivo com sucesso.
        UNSUPPORTED_EXTENSION: extensão não está na lista suportada.
        CORRUPTED: Pillow identificou o arquivo mas não conseguiu
            decodificá-lo (cabeçalho OK, corpo corrompido).
        UNREADABLE: Pillow não conseguiu abrir o arquivo por motivo
            não-classificável nos anteriores (formato Pillow
            desconhecido, plugin ausente, etc.).
        PERMISSION_ERROR: o sistema de arquivos negou a leitura.
    """

    VALID = "VALID"
    UNSUPPORTED_EXTENSION = "UNSUPPORTED_EXTENSION"
    CORRUPTED = "CORRUPTED"
    UNREADABLE = "UNREADABLE"
    PERMISSION_ERROR = "PERMISSION_ERROR"


@dataclass(frozen=True)
class ImageFileInfo:
    """Resultado da análise de um único arquivo.

    Attributes:
        name: nome do arquivo (sem diretórios).
        path: caminho absoluto (pathlib.Path).
        extension: extensão normalizada em minúsculas, com ponto
            (ex: ".jpg"). Vazia quando o arquivo não tem extensão.
        size_bytes: tamanho em bytes.
        width: largura detectada pelo Pillow. `None` quando o
            arquivo é inválido (não foi possível decodificar).
        height: idem para altura.
        format: formato reportado pelo Pillow (ex: "JPEG", "PNG").
            `None` quando o arquivo é inválido.
        status: classificação do arquivo.
        error_reason: descrição curta do motivo do erro (somente
            quando status != VALID).
        sha256: hash SHA-256 em hexadecimal, minúsculas, 64 chars.
            Calculado em streaming. SEMPRE presente, mesmo para
            arquivos inválidos — é a identidade usada para
            idempotência em prompts seguintes.
    """

    name: str
    path: Path
    extension: str
    size_bytes: int
    width: int | None
    height: int | None
    format: str | None
    status: ImageStatus
    error_reason: str | None
    sha256: str


@dataclass(frozen=True)
class ScanResult:
    """Resultado agregado de um scan completo.

    Attributes:
        folder: pasta escaneada.
        files: lista de `ImageFileInfo`, na ordem em que foram
            encontrados.
        subfolders_ignored: contagem de subpastas encontradas no
            primeiro nível e intencionalmente ignoradas.
        folder_exists: `False` quando a pasta sumiu entre a seleção
            e o scan. Nesse caso `files` será vazio.
    """

    folder: Path
    files: list[ImageFileInfo] = field(default_factory=list)
    subfolders_ignored: int = 0
    folder_exists: bool = True

    # ------------------------------------------------------------------ #
    # Agregações usadas pela UI                                          #
    # ------------------------------------------------------------------ #

    @property
    def total(self) -> int:
        return len(self.files)

    @property
    def total_valid(self) -> int:
        return sum(1 for f in self.files if f.status is ImageStatus.VALID)

    @property
    def total_invalid(self) -> int:
        return self.total - self.total_valid

    def count_by_status(self) -> dict[ImageStatus, int]:
        out: dict[ImageStatus, int] = {s: 0 for s in ImageStatus}
        for f in self.files:
            out[f.status] += 1
        return out

    def count_by_extension_valid(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for f in self.files:
            if f.status is ImageStatus.VALID:
                out[f.extension] = out.get(f.extension, 0) + 1
        return out


__all__ = ["ImageFileInfo", "ImageStatus", "ScanResult"]