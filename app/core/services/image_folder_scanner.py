"""Scanner de pasta de imagens (Prompt 3).

Responsabilidades:
    - Ler o primeiro nível de uma pasta, sem recursão.
    - Para cada arquivo: extrair metadados (tamanho, dimensões,
      formato), validar integridade com Pillow e calcular o SHA-256
      em streaming.
    - Classificar cada arquivo em um dos status previstos.
    - Reportar quantas subpastas foram ignoradas.
    - NUNCA modificar, mover, renomear ou apagar nada no disco.

Esta classe é deliberadamente headless — não conhece UI. O worker
(`app/ui/workers/scan_worker.py`) é quem a executa fora da thread
principal da UI e expõe o resultado via sinais Qt.
"""

from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from app.core.models import ImageFileInfo, ImageStatus, ScanResult

logger = logging.getLogger(__name__)

# Extensões suportadas, em minúsculas e com ponto. A comparação é
# case-insensitive (regra 2 do prompt).
SUPPORTED_EXTENSIONS: tuple[str, ...] = (".jpg", ".jpeg", ".png", ".webp")

# Tamanho do buffer de streaming para o cálculo do SHA-256.
# 256 KB equilibra throughput de disco e uso de memória.
_HASH_CHUNK = 256 * 1024


class ImageFolderScanner:
    """Lê uma pasta, classifica cada arquivo e devolve um `ScanResult`.

    Uso:
        scanner = ImageFolderScanner()
        result = scanner.scan(Path("/alguma/pasta"))

    O método `scan()` é seguro de chamar com qualquer caminho —
    pasta inexistente, vazia ou com permissão negada não levanta
    exceção, apenas é reportado em `ScanResult`.
    """

    def scan(self, folder: Path) -> ScanResult:
        folder = Path(folder)
        if not folder.exists() or not folder.is_dir():
            logger.warning("Pasta inexistente ou inacessível: %s", folder)
            return ScanResult(folder=folder, files=[], folder_exists=False)

        logger.info("Iniciando scan de %s", folder)

        files: list[ImageFileInfo] = []
        subfolders_ignored = 0

        # Listar com `iterdir` é não-recursivo por definição.
        try:
            entries = list(folder.iterdir())
        except PermissionError:
            logger.warning("Sem permissão de leitura em %s", folder)
            return ScanResult(folder=folder, files=[], folder_exists=True)

        for entry in entries:
            if entry.is_dir():
                subfolders_ignored += 1
                continue

            info = self._analyze(entry)
            files.append(info)

        result = ScanResult(
            folder=folder,
            files=files,
            subfolders_ignored=subfolders_ignored,
            folder_exists=True,
        )
        logger.info(
            "Scan concluído em %s: total=%d válidos=%d inválidos=%d subpastas_ignoradas=%d",
            folder,
            result.total,
            result.total_valid,
            result.total_invalid,
            subfolders_ignored,
        )
        return result

    # ------------------------------------------------------------------ #
    # Análise de UM arquivo                                               #
    # ------------------------------------------------------------------ #

    def _analyze(self, path: Path) -> ImageFileInfo:
        # Nome/extensão primeiro — se a extensão já indica que não
        # vale a pena abrir, marcamos como UNSUPPORTED_EXTENSION sem
        # gastar I/O ou hashing pesado.
        name = path.name
        ext = path.suffix.lower()
        try:
            size = path.stat().st_size
        except OSError as exc:
            logger.debug("stat() falhou em %s: %s", path, exc.__class__.__name__)
            return ImageFileInfo(
                name=name,
                path=path,
                extension=ext,
                size_bytes=0,
                width=None,
                height=None,
                format=None,
                status=ImageStatus.PERMISSION_ERROR,
                error_reason="não foi possível ler o arquivo",
                sha256=self._empty_sha256(),
            )

        # Extensão não suportada → marca sem abrir nem hashear (a
        # identidade via hash só faz sentido para arquivos que
        # podem vir a ser processados).
        if ext not in SUPPORTED_EXTENSIONS:
            sha = self._hash_file(path)
            return ImageFileInfo(
                name=name,
                path=path,
                extension=ext,
                size_bytes=size,
                width=None,
                height=None,
                format=None,
                status=ImageStatus.UNSUPPORTED_EXTENSION,
                error_reason=f"extensão '{ext or '(sem extensão)'}' não suportada",
                sha256=sha,
            )

        # Hash é SEMPRE calculado, mesmo em imagens inválidas — ele
        # será usado pela idempotência em prompts seguintes.
        sha = self._hash_file(path)

        # Permissão de leitura (checada via open) — antes de tentar
        # abrir com Pillow.
        try:
            with path.open("rb") as fh:
                head = fh.read(64)
        except PermissionError as exc:
            return ImageFileInfo(
                name=name,
                path=path,
                extension=ext,
                size_bytes=size,
                width=None,
                height=None,
                format=None,
                status=ImageStatus.PERMISSION_ERROR,
                error_reason=f"sem permissão de leitura ({exc.strerror or 'OSError'})",
                sha256=sha,
            )
        except OSError as exc:
            return ImageFileInfo(
                name=name,
                path=path,
                extension=ext,
                size_bytes=size,
                width=None,
                height=None,
                format=None,
                status=ImageStatus.UNREADABLE,
                error_reason=f"erro de I/O: {exc.__class__.__name__}",
                sha256=sha,
            )

        # Tentar abrir com Pillow. Distinguimos:
        #   - UNREADABLE: Pillow não reconhece nem o header.
        #   - CORRUPTED:  Pillow reconheceu o header mas falhou ao
        #                 reabrir/extrair dimensões.
        #   - PERMISSION_ERROR: o sistema de arquivos negou a leitura
        #                 em alguma etapa (Pillow também pode falhar
        #                 com PermissionError na decodificação).
        try:
            with Image.open(path) as img:
                img.verify()  # só valida estrutura/decodificação leve
        except UnidentifiedImageError:
            return self._info_invalid(
                path, name, ext, size, sha,
                status=ImageStatus.UNREADABLE,
                reason="Pillow não identificou o formato",
            )
        except PermissionError as exc:
            return self._info_invalid(
                path, name, ext, size, sha,
                status=ImageStatus.PERMISSION_ERROR,
                reason=f"sem permissão de leitura ({exc.strerror or 'PermissionError'})",
            )
        except Exception as exc:  # noqa: BLE001 — qualquer falha de Pillow
            return self._info_invalid(
                path, name, ext, size, sha,
                status=ImageStatus.CORRUPTED,
                reason=f"Pillow falhou no verify(): {exc.__class__.__name__}",
            )

        # verify() passou — agora reabrimos para extrair dimensões
        # reais. Esta segunda abertura é o que detecta "cabeçalho OK
        # mas corpo corrompido" (regra 3 do prompt).
        try:
            with Image.open(path) as img:
                img.load()  # força decodificação completa
                width, height = img.size
                fmt = img.format
        except UnidentifiedImageError:
            return self._info_invalid(
                path, name, ext, size, sha,
                status=ImageStatus.UNREADABLE,
                reason="Pillow não conseguiu reabrir o arquivo",
            )
        except PermissionError as exc:
            return self._info_invalid(
                path, name, ext, size, sha,
                status=ImageStatus.PERMISSION_ERROR,
                reason=f"sem permissão de leitura ({exc.strerror or 'PermissionError'})",
            )
        except Exception as exc:  # noqa: BLE001
            return self._info_invalid(
                path, name, ext, size, sha,
                status=ImageStatus.CORRUPTED,
                reason=f"corpo corrompido ({exc.__class__.__name__})",
            )

        # Tudo OK — registramos o head que já lemos (evita warning
        # de variável não usada).
        del head
        return ImageFileInfo(
            name=name,
            path=path,
            extension=ext,
            size_bytes=size,
            width=width,
            height=height,
            format=fmt,
            status=ImageStatus.VALID,
            error_reason=None,
            sha256=sha,
        )

    # ------------------------------------------------------------------ #
    # Helpers                                                            #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _hash_file(path: Path) -> str:
        """SHA-256 em streaming. Lê o arquivo em blocos para não
        carregar tudo na memória de uma vez.

        Falhas de I/O resultam em um hash placeholder (todos zeros) —
        o erro real já é capturado por `ImageFileInfo.error_reason`.
        """
        h = hashlib.sha256()
        try:
            with path.open("rb") as fh:
                while True:
                    chunk = fh.read(_HASH_CHUNK)
                    if not chunk:
                        break
                    h.update(chunk)
        except OSError:
            return "0" * 64
        return h.hexdigest()

    @staticmethod
    def _info_invalid(
        path: Path,
        name: str,
        ext: str,
        size: int,
        sha: str,
        *,
        status: ImageStatus,
        reason: str,
    ) -> ImageFileInfo:
        """Constrói um `ImageFileInfo` para um arquivo inválido."""
        return ImageFileInfo(
            name=name,
            path=path,
            extension=ext,
            size_bytes=size,
            width=None,
            height=None,
            format=None,
            status=status,
            error_reason=reason,
            sha256=sha,
        )

    @staticmethod
    def _empty_sha256() -> str:
        return "0" * 64


__all__ = ["ImageFolderScanner", "SUPPORTED_EXTENSIONS"]