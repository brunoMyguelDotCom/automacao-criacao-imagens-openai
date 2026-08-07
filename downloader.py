"""
downloader.py
Lida com o download (via interface do ChatGPT) e o arquivamento na pasta
configurada. Como o download do ChatGPT Desktop abre o navegador/Explorer
padrão, monitoramos uma pasta de downloads e movemos os arquivos novos
para ./downloaded.
"""
from __future__ import annotations

import shutil
import time
from pathlib import Path
from typing import Optional

from config import CONFIG
from logger import get_logger

log = get_logger("downloader")


def move_to_downloaded(src: Path, dest_dir: Optional[Path] = None) -> Optional[Path]:
    """Move um arquivo para a pasta de downloads do projeto."""
    dest_dir = dest_dir or CONFIG.get("download_folder_resolved")
    if dest_dir is None:
        dest_dir = Path("downloaded")
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    if not src.exists():
        log.error("Arquivo de origem não existe: %s", src)
        return None
    dest = dest_dir / src.name
    counter = 1
    while dest.exists():
        dest = dest_dir / f"{src.stem}_{counter}{src.suffix}"
        counter += 1
    try:
        shutil.move(str(src), str(dest))
        log.info("Movido para downloaded: %s", dest.name)
        return dest
    except OSError as exc:
        log.error("Falha ao mover %s -> %s: %s", src, dest, exc)
        return None


def move_to_output(
    src: Path, output_dir: Optional[Path] = None, new_name: Optional[str] = None
) -> Optional[Path]:
    """Move um arquivo para a pasta output do projeto."""
    output_dir = output_dir or CONFIG.get("output_folder_resolved")
    if output_dir is None:
        output_dir = Path("output")
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    if not src.exists():
        log.error("Arquivo de origem não existe: %s", src)
        return None
    dest = output_dir / (new_name or src.name)
    counter = 1
    while dest.exists():
        dest = output_dir / f"{dest.stem}_{counter}{dest.suffix}"
        counter += 1
    try:
        shutil.move(str(src), str(dest))
        log.info("Movido para output: %s", dest.name)
        return dest
    except OSError as exc:
        log.error("Falha ao mover %s -> %s: %s", src, dest, exc)
        return None


def mark_processed(
    src: Path,
    action: Optional[str] = None,
    processed_dir: Optional[Path] = None,
) -> None:
    """Move o arquivo original para ./processed, ou apaga, conforme config."""
    action = action or CONFIG.get("processed_action") or "move"
    if action == "delete":
        try:
            src.unlink()
            log.info("Arquivo original apagado: %s", src.name)
        except OSError as exc:
            log.warning("Falha ao apagar %s: %s", src, exc)
        return

    processed_dir = processed_dir or CONFIG.get("processed_folder_resolved")
    if processed_dir is None:
        processed_dir = Path("processed")
    processed_dir = Path(processed_dir)
    processed_dir.mkdir(parents=True, exist_ok=True)
    dest = processed_dir / src.name
    counter = 1
    while dest.exists():
        dest = processed_dir / f"{src.stem}_{counter}{src.suffix}"
        counter += 1
    try:
        shutil.move(str(src), str(dest))
        log.info("Original movido para processed: %s", dest.name)
    except OSError as exc:
        log.warning("Falha ao mover para processed: %s", exc)


def move_to_failed(src: Path, failed_dir: Optional[Path] = None) -> None:
    """Move a imagem para ./failed para reprocessamento posterior."""
    failed_dir = failed_dir or CONFIG.get("failed_folder_resolved")
    if failed_dir is None:
        failed_dir = Path("failed")
    failed_dir = Path(failed_dir)
    failed_dir.mkdir(parents=True, exist_ok=True)
    dest = failed_dir / src.name
    counter = 1
    while dest.exists():
        dest = failed_dir / f"{src.stem}_{counter}{src.suffix}"
        counter += 1
    try:
        shutil.move(str(src), str(dest))
        log.warning("Imagem movida para FAILED: %s", dest.name)
    except OSError as exc:
        log.error("Falha ao mover para failed: %s", exc)


def find_newest_in(dirpath: Path, exts: Optional[list] = None) -> Optional[Path]:
    """Retorna o arquivo mais recente em dirpath que case com exts."""
    if not dirpath.exists():
        return None
    exts = [e.lower() for e in (exts or CONFIG.get("image_extensions") or [])]
    candidates = []
    for f in dirpath.iterdir():
        if not f.is_file():
            continue
        if exts and f.suffix.lower() not in exts:
            continue
        try:
            candidates.append((f.stat().st_mtime, f))
        except OSError:
            continue
    if not candidates:
        return None
    candidates.sort(reverse=True)
    return candidates[0][1]
