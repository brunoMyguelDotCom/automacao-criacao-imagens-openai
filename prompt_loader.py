"""
prompt_loader.py
Carrega prompts a partir de arquivos TXT em ./prompts.

Cada arquivo é identificado pelo nome (sem extensão), ex:
    prompts/camisa.txt  -> categoria "camisa"
"""
from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from typing import Dict, Optional

from config import CONFIG
from logger import get_logger

log = get_logger("prompt_loader")


def _normalize_key(name: str) -> str:
    """Normaliza uma chave (nome de arquivo ou pasta) para minúsculas, sem acento, com _."""
    nfkd = unicodedata.normalize("NFKD", name)
    only_ascii = "".join(c for c in nfkd if not unicodedata.combining(c))
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "_", only_ascii).strip("_").lower()
    return cleaned


def load_all_prompts(prompts_dir: Optional[Path] = None) -> Dict[str, str]:
    """Lê todos os .txt em prompts_dir e retorna dict {key: prompt_text}."""
    base = prompts_dir or CONFIG.get("prompts_folder_resolved")
    if base is None:
        base = CONFIG.get("prompts_folder") or "prompts"
        base = Path(base)
        if not base.is_absolute():
            base = (Path(__file__).resolve().parent / base)
    if not base.exists():
        log.warning("Pasta de prompts não existe: %s", base)
        return {}

    result: Dict[str, str] = {}
    for txt in sorted(base.glob("*.txt")):
        key = _normalize_key(txt.stem)
        try:
            text = txt.read_text(encoding="utf-8").strip()
        except OSError as exc:
            log.error("Falha ao ler %s: %s", txt, exc)
            continue
        if not text:
            log.warning("Prompt vazio ignorado: %s", txt)
            continue
        result[key] = text
        log.debug("Prompt carregado: %s (%d chars)", key, len(text))

    if not result:
        log.warning("Nenhum prompt encontrado em %s", base)
    return result


def discover_category(image_path: Path, input_root: Path) -> str:
    """
    Descobre a categoria a partir de:
      1) pasta relativa (Método 2): input/Calças/foo.jpg -> "calcas"
      2) prefixo do nome do arquivo (Método 1): camisa_001.jpg -> "camisa"
      3) retorna "default" se nada casar
    """
    try:
        rel = image_path.relative_to(input_root)
    except ValueError:
        rel = Path(image_path.name)

    parts = rel.parts
    if len(parts) > 1:
        # Tem subpastas — a primeira é a categoria
        return _normalize_key(parts[0])

    stem = image_path.stem
    # separa por _ - espaço
    first = re.split(r"[_\-\s]+", stem)[0] if stem else ""
    return _normalize_key(first) or "default"


def get_prompt_for_category(
    category: str, prompts: Dict[str, str]
) -> Optional[str]:
    """Tenta várias formas de chave para achar um prompt compatível."""
    if not category:
        return None
    key = _normalize_key(category)
    if key in prompts:
        return prompts[key]
    # tenta sem singular/plural ingênuo
    if key.endswith("s") and key[:-1] in prompts:
        return prompts[key[:-1]]
    if (key + "s") in prompts:
        return prompts[key + "s"]
    return None
