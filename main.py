"""
main.py
Pipeline principal de geração em lote.

Loop:
  1) lista imagens em ./input
  2) para cada imagem:
       - descobre categoria
       - carrega prompt
       - abre/anexa o ChatGPT Desktop
       - anexa imagem
       - envia prompt
       - aguarda geração
       - baixa imagem
       - move para ./output
       - marca como processada
       - trata erros com retry
  3) ao fim, loga resumo
"""
from __future__ import annotations

import argparse
import sys
import time
import traceback
from pathlib import Path
from typing import Dict, List, Optional

from config import CONFIG, PROJECT_ROOT, reload_config
from downloader import (
    find_newest_in,
    mark_processed,
    move_to_downloaded,
    move_to_failed,
    move_to_output,
)
from logger import get_logger, setup_logging
from prompt_loader import (
    discover_category,
    get_prompt_for_category,
    load_all_prompts,
)

log = get_logger("main")


# ----------------- helpers -----------------
def _list_input_images(input_dir: Path, exts: List[str]) -> List[Path]:
    if not input_dir.exists():
        log.error("Pasta de input não existe: %s", input_dir)
        return []
    exts = [e.lower() for e in exts]
    found: List[Path] = []
    for p in input_dir.rglob("*"):
        if p.is_file() and p.suffix.lower() in exts:
            found.append(p)
    found.sort()
    return found


def _summarize(success: int, failed: int, skipped: int, total: int) -> None:
    log.info("=" * 50)
    log.info("RESUMO DA EXECUÇÃO")
    log.info("Total      : %d", total)
    log.info("Sucesso    : %d", success)
    log.info("Falhas     : %d", failed)
    log.info("Sem prompt : %d", skipped)
    log.info("=" * 50)


def _process_single(
    image: Path,
    input_root: Path,
    prompts: Dict[str, str],
    bot,
) -> bool:
    """
    Processa UMA imagem. Retorna True se a geração foi concluída
    com sucesso (imagem final em output).
    """
    name = image.name
    log.info("-" * 40)
    log.info("Processando: %s", name)

    category = discover_category(image, input_root)
    log.info("Categoria detectada: %s", category)

    prompt_text = get_prompt_for_category(category, prompts)
    if not prompt_text:
        log.error(
            "Nenhum prompt para categoria '%s'. "
            "Crie prompts/%s.txt e tente novamente.",
            category,
            category,
        )
        move_to_failed(image)
        return False

    max_retries = int(CONFIG.get("max_retries") or 3)
    last_err: Optional[Exception] = None

    for attempt in range(1, max_retries + 1):
        log.info("Tentativa %d/%d para %s", attempt, max_retries, name)
        try:
            # 1. garantir janela
            bot.ensure_started()

            # 2. anexar imagem
            bot.attach_image(image)

            # 3. enviar prompt
            bot.send_prompt(prompt_text)

            # 4. esperar geração
            ok = bot.wait_for_generation(
                timeout=int(CONFIG.get("wait_generation_timeout") or 180),
                poll=float(CONFIG.get("wait_generation_poll") or 2),
            )
            if not ok:
                raise TimeoutError("Timeout aguardando geração.")

            # 5. clicar em download (aciona o download no app)
            bot.click_download_latest(
                timeout=int(CONFIG.get("wait_download_timeout") or 120),
                poll=float(CONFIG.get("wait_download_poll") or 2),
            )

            # 6. arquivo cairá em ./downloaded (configurável). Localizar.
            # O app pode demorar para gravar; esperamos um pouco.
            download_dir = CONFIG.get("download_folder_resolved")
            image_exts = CONFIG.get("image_extensions") or [".png", ".jpg"]
            newest: Optional[Path] = None
            deadline = time.time() + 60
            while time.time() < deadline:
                newest = find_newest_in(download_dir, image_exts)
                if newest:
                    break
                time.sleep(1.0)
            if not newest:
                raise FileNotFoundError(
                    f"Nenhum arquivo novo em {download_dir}"
                )

            # 7. mover para ./downloaded (idempotente) e depois para output
            moved = move_to_downloaded(newest)
            if not moved:
                raise IOError(f"Falha ao mover {newest} para downloaded/")

            # renomeia com base no nome de entrada (mantém rastreabilidade)
            new_name = f"{image.stem}__{moved.stem}{moved.suffix}"
            final = move_to_output(moved, new_name=new_name)
            if not final:
                raise IOError("Falha ao mover para output/")

            # 8. marcar imagem original como processada
            mark_processed(image)

            log.info("SUCESSO: %s -> %s", name, final.name)
            return True

        except Exception as exc:  # noqa: BLE001
            last_err = exc
            log.error("Erro em %s (tentativa %d): %s", name, attempt, exc)
            log.debug("Traceback:\n%s", traceback.format_exc())
            time.sleep(2)  # backoff curto
            continue

    log.error("Todas as %d tentativas falharam para %s. Último erro: %s",
              max_retries, name, last_err)
    move_to_failed(image)
    return False


# ----------------- entrypoint -----------------
def run(headless_dry_run: bool = False) -> int:
    setup_logging()
    log.info("Iniciando GPT Image Batch Generator")
    log.info("Raiz do projeto: %s", PROJECT_ROOT)

    cfg = reload_config()
    input_dir = cfg["input_folder_resolved"]
    prompts = load_all_prompts()

    if not prompts:
        log.error("Nenhum prompt carregado. Abortando.")
        return 2

    images = _list_input_images(
        input_dir, cfg.get("image_extensions") or [".jpg", ".png"]
    )
    total = len(images)
    log.info("Encontradas %d imagens em %s", total, input_dir)

    if total == 0:
        log.info("Nada para processar. Saindo.")
        return 0

    bot = None
    success = failed = skipped = 0

    if not headless_dry_run:
        # Lazy import para não exigir pywinauto em dry-run
        from automation import ChatGPTAutomation
        try:
            bot = ChatGPTAutomation(
                window_title_substring=cfg.get(
                    "chatgpt_window_title_substring", "ChatGPT"
                )
            )
        except Exception as exc:  # noqa: BLE001
            log.error("Falha ao inicializar automação: %s", exc)
            return 3
    else:
        log.info("Modo dry-run: nenhuma automação será executada.")

    try:
        for idx, image in enumerate(images, start=1):
            log.info("[%d/%d] %s", idx, total, image.name)
            if headless_dry_run:
                # valida apenas categoria + existência do prompt
                cat = discover_category(image, input_dir)
                if get_prompt_for_category(cat, prompts):
                    log.info("DRY: OK - categoria=%s", cat)
                    success += 1
                else:
                    log.warning("DRY: sem prompt para %s (cat=%s)", image.name, cat)
                    skipped += 1
                continue

            try:
                ok = _process_single(image, input_dir, prompts, bot)
                if ok:
                    success += 1
                else:
                    failed += 1
            except KeyboardInterrupt:
                log.warning("Interrompido pelo usuário.")
                break
            except Exception as exc:  # noqa: BLE001
                log.error("Erro inesperado em %s: %s", image.name, exc)
                failed += 1
    finally:
        _summarize(success, failed, skipped, total)

    return 0 if failed == 0 else 1


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="GPT Image Batch Generator — automação para ChatGPT Desktop."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Não executa a automação; só valida categorias e prompts.",
    )
    parser.add_argument(
        "--reload-config",
        action="store_true",
        help="Recarrega config.json antes de iniciar.",
    )
    args = parser.parse_args(argv)
    if args.reload_config:
        reload_config()
    return run(headless_dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
