"""Provider de geração via automação do ChatGPT Desktop (V1 — MVP).

Este provider implementa o mesmo contrato
`ImageGenerationProvider` que o `OpenAIImageGenerationProvider`,
mas em vez de chamar a API REST da OpenAI ele **dirige** o ChatGPT
Desktop aberto no Windows:

    1. Foca a janela do ChatGPT Desktop.
    2. Cola a imagem de referência no campo de anexo (Ctrl+V).
    3. Cola o prompt e envia com Enter.
    4. Espera um tempo fixo (`wait_generation_s`) — placeholder
       que será substituído pela detecção via árvore de
       acessibilidade na V3.
    5. Espera um arquivo novo e estável aparecer na pasta de
       Downloads (`wait_download_s`). **O clique manual no botão
       "baixar" da imagem gerada dentro do ChatGPT Desktop é
       feito pelo usuário** — o provider só observa o resultado.
    6. Move esse arquivo para `request.output_path`, sem
       sobrescrever arquivos existentes.

Por que o download é manual?
    Clicar no botão de download exige localizar um elemento visual
    específico no ChatGPT Desktop — exatamente o tipo de automação
    frágil por coordenadas fixas que o plano original quis evitar.
    Na V1, automatizamos o que é fácil (montar a mensagem) e
    deixamos o clique manual. Na V3 isso vira automação via árvore
    de acessibilidade.

Restrição de plataforma:
    Toda a automação é Windows-only. Em outros SOs, todas as
    operações retornam `False`/`None` (ver `app.core.automation`).
    Aqui, isso se traduz em `GenerationResult(success=False, error=...)`
    com `ErrorCode.CONNECTION` ou `ErrorCode.TIMEOUT`, sem
    levantar exceção.
"""

from __future__ import annotations

import logging
import shutil
import time
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from app.core.automation import (
    copy_file_to_clipboard,
    copy_text_to_clipboard,
    find_and_focus_window,
    send_enter,
    send_paste,
    snapshot_files,
    wait_for_new_stable_file,
)
from app.core.models.generation import (
    ErrorCode,
    GenerationError,
    GenerationRequest,
    GenerationResult,
)
from app.core.providers.image_generation_provider import ImageGenerationProvider

logger = logging.getLogger(__name__)


# Pastas padrão usadas quando o caller não fornece.
_DOWNLOADS_DIRNAME = "Downloads"
_TEMP_SUBDIR_PARTS = ("GPT Automator", "Temporario")

# Marca que escrevemos no log para o usuário identificar que precisa
# clicar manualmente no botão de download dentro do ChatGPT Desktop.
_MANUAL_DOWNLOAD_HINT = (
    "[ChatGPT Desktop] A imagem foi solicitada. Clique manualmente no "
    "botão de download da imagem gerada dentro do ChatGPT Desktop para "
    "que o provider possa detectá-la na pasta de Downloads."
)

# Atrasos fixos dentro de `generate()`. São curtos o bastante para
# manter o ritmo da fila e longos o bastante para a UI do Electron
# processar o anexo / enviar a mensagem sem race condition.
_ATTACH_SETTLE_S = 1.5  # tempo para a UI processar o anexo colado
_PROMPT_SETTLE_S = 0.5  # tempo antes do Enter após colar o prompt

# Sufixo usado para validar downloads que estão em formato PNG/JPG.
# Se o ChatGPT Desktop baixar como `.png` ou `.jpg`, mantemos; se
# vier com outro nome (ex.: `download`), o provider segue — quem
# processa depois (UI) decide o que fazer com extensões incomuns.
_VALID_IMAGE_SUFFIXES: frozenset[str] = frozenset({
    ".png", ".jpg", ".jpeg", ".webp",
})


class ChatGPTDesktopAutomationProvider(ImageGenerationProvider):
    """Provider que automatiza o ChatGPT Desktop para gerar imagens.

    Args:
        window_title_hint: trecho do título da janela a procurar.
            Padrão ``"ChatGPT"``. Use ``"ChatGPT - "`` (com espaço e
            hífen) se o app estiver exibindo o nome da conversa no
            título, ou o nome exato do executável se você souber.
        downloads_folder: pasta onde o ChatGPT Desktop salva
            downloads (Windows: ``%USERPROFILE%\\Downloads``).
            ``None`` usa o default.
        temp_folder: pasta reservada para arquivos temporários do
            provider (logs, anexos intermediários). Padrão:
            ``%USERPROFILE%/GPT Automator/Temporario``.
        wait_generation_s: espera fixa entre enviar o prompt e
            procurar pelo download. Placeholder da V1 — a V3
            substitui isso por detecção real (botão "Stop" sumir /
            botão "Download" aparecer).
        wait_download_s: quanto tempo esperar por um arquivo novo e
            estável na pasta de Downloads antes de falhar com
            `ErrorCode.TIMEOUT`.
    """

    def __init__(
        self,
        window_title_hint: str = "ChatGPT",
        downloads_folder: Path | None = None,
        temp_folder: Path | None = None,
        wait_generation_s: float = 90.0,
        wait_download_s: float = 30.0,
    ) -> None:
        self._window_title_hint = window_title_hint or "ChatGPT"
        self._downloads_folder: Path = (
            Path(downloads_folder) if downloads_folder
            else (Path.home() / _DOWNLOADS_DIRNAME)
        )
        self._temp_folder: Path = (
            Path(temp_folder) if temp_folder
            else Path.home().joinpath(*_TEMP_SUBDIR_PARTS)
        )
        # Timeouts: clamp para evitar valores negativos ou absurdos
        # que travariam a fila sem motivo.
        self._wait_generation_s = max(1.0, float(wait_generation_s))
        self._wait_download_s = max(1.0, float(wait_download_s))

    # ------------------------------------------------------------------ #
    # API pública                                                         #
    # ------------------------------------------------------------------ #

    def generate(self, request: GenerationRequest) -> GenerationResult:
        """Executa a automação completa. NUNCA levanta exceção.

        Erros previsíveis (janela ausente, clipboard falhou, download
        não apareceu, arquivo de saída já existe) viram
        ``GenerationResult(success=False, error=...)``. O
        ``try/except`` amplo é só rede de segurança para bugs
        internos — converte qualquer surpresa em
        ``ErrorCode.UNKNOWN``.
        """
        started = time.monotonic()
        try:
            return self._generate(request=request, started=started)
        except Exception as exc:  # noqa: BLE001 — fronteira do provider
            logger.exception(
                "ChatGPTDesktopAutomationProvider.generate: exceção inesperada"
            )
            return self._failure_result(
                request=request,
                error=GenerationError(
                    code=ErrorCode.UNKNOWN,
                    message=f"Falha inesperada na automação: {exc}",
                    retryable=False,
                ),
                duration_ms=int((time.monotonic() - started) * 1000),
            )

    def close(self) -> None:
        """Libera recursos. No-op — não há cliente HTTP / conexão."""
        return None

    # ------------------------------------------------------------------ #
    # Pipeline                                                            #
    # ------------------------------------------------------------------ #

    def _generate(
        self, *, request: GenerationRequest, started: float
    ) -> GenerationResult:
        # 0. Validação leve — bugs do chamador viram UNKNOWN com
        #    mensagem específica. Mantida simples: a UI/orquestrador
        #    já fez validação pesada antes de chegar aqui.
        self._validate_request(request)

        # 1. Focar a janela do ChatGPT Desktop. Se não aparecer,
        #    falhamos com ERR_CONNECTION (retryable) — talvez o
        #    usuário ainda esteja abrindo o app.
        if not find_and_focus_window(
            self._window_title_hint, timeout_s=10.0
        ):
            return self._failure_result(
                request=request,
                error=GenerationError(
                    code=ErrorCode.CONNECTION,
                    message=(
                        "Janela do ChatGPT Desktop não encontrada. "
                        "Verifique se o aplicativo está aberto."
                    ),
                    retryable=True,
                ),
                duration_ms=int((time.monotonic() - started) * 1000),
            )

        # 2. Snapshot da pasta de Downloads ANTES de qualquer coisa
        #    que possa disparar download. Mesmo que a V1 peça
        #    download manual, o snapshot é importante para que o
        #    polling ignore arquivos antigos (ex.: outros downloads
        #    que o usuário já tinha na pasta).
        try:
            self._downloads_folder.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return self._failure_result(
                request=request,
                error=GenerationError(
                    code=ErrorCode.LOCAL_IO,
                    message=(
                        f"Não foi possível preparar a pasta de Downloads "
                        f"({self._downloads_folder}): "
                        f"{exc.strerror or exc.__class__.__name__}"
                    ),
                    retryable=False,
                ),
                duration_ms=int((time.monotonic() - started) * 1000),
            )
        before = snapshot_files(self._downloads_folder)

        # 3. Colar a imagem de referência no campo de anexo.
        if not copy_file_to_clipboard(request.reference_image_path):
            return self._failure_result(
                request=request,
                error=GenerationError(
                    code=ErrorCode.LOCAL_IO,
                    message=(
                        "Não foi possível copiar a imagem de referência "
                        "para o clipboard (CF_HDROP falhou)."
                    ),
                    retryable=False,
                ),
                duration_ms=int((time.monotonic() - started) * 1000),
            )
        if not send_paste():
            return self._failure_result(
                request=request,
                error=GenerationError(
                    code=ErrorCode.CONNECTION,
                    message=(
                        "Não foi possível colar (Ctrl+V) a imagem no "
                        "ChatGPT Desktop."
                    ),
                    retryable=True,
                ),
                duration_ms=int((time.monotonic() - started) * 1000),
            )
        time.sleep(_ATTACH_SETTLE_S)

        # 4. Colar o prompt e enviar.
        if not copy_text_to_clipboard(request.prompt_text):
            return self._failure_result(
                request=request,
                error=GenerationError(
                    code=ErrorCode.LOCAL_IO,
                    message=(
                        "Não foi possível copiar o prompt para o clipboard."
                    ),
                    retryable=False,
                ),
                duration_ms=int((time.monotonic() - started) * 1000),
            )
        if not send_paste():
            return self._failure_result(
                request=request,
                error=GenerationError(
                    code=ErrorCode.CONNECTION,
                    message=(
                        "Não foi possível colar (Ctrl+V) o prompt no "
                        "ChatGPT Desktop."
                    ),
                    retryable=True,
                ),
                duration_ms=int((time.monotonic() - started) * 1000),
            )
        time.sleep(_PROMPT_SETTLE_S)
        if not send_enter():
            return self._failure_result(
                request=request,
                error=GenerationError(
                    code=ErrorCode.CONNECTION,
                    message=(
                        "Não foi possível enviar (Enter) o prompt no "
                        "ChatGPT Desktop."
                    ),
                    retryable=True,
                ),
                duration_ms=int((time.monotonic() - started) * 1000),
            )

        # 5. Espera fixa (placeholder V1). Log explícito para o
        #    usuário entender o que está acontecendo.
        logger.info(
            "ChatGPTDesktopAutomationProvider: prompt enviado, aguardando "
            "%.1fs antes de observar a pasta de Downloads.",
            self._wait_generation_s,
        )
        time.sleep(self._wait_generation_s)

        # 6. Pedir gentilmente o download manual, e esperar.
        logger.warning(_MANUAL_DOWNLOAD_HINT)
        downloaded = wait_for_new_stable_file(
            self._downloads_folder,
            before,
            timeout_s=self._wait_download_s,
        )
        if downloaded is None:
            return self._failure_result(
                request=request,
                error=GenerationError(
                    code=ErrorCode.TIMEOUT,
                    message=(
                        "Nenhum arquivo novo detectado na pasta de Downloads. "
                        "Você clicou em baixar a imagem gerada no "
                        "ChatGPT Desktop?"
                    ),
                    retryable=True,
                ),
                duration_ms=int((time.monotonic() - started) * 1000),
            )

        # 7. Descobrir o destino final. Não sobrescrevemos arquivos
        #    existentes — devolvemos LOCAL_IO com mensagem clara.
        output_path = Path(request.output_path)
        if output_path.exists() and output_path.is_file():
            return self._failure_result(
                request=request,
                error=GenerationError(
                    code=ErrorCode.LOCAL_IO,
                    message=(
                        f"Arquivo de saída já existe e não será "
                        f"sobrescrito: {output_path}. Remova-o ou "
                        f"reprocesse com um nome diferente."
                    ),
                    retryable=False,
                ),
                duration_ms=int((time.monotonic() - started) * 1000),
            )

        # 8. Mover o arquivo baixado para o destino final.
        try:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(downloaded), str(output_path))
        except OSError as exc:
            return self._failure_result(
                request=request,
                error=GenerationError(
                    code=ErrorCode.LOCAL_IO,
                    message=(
                        f"Falha ao mover {downloaded} para {output_path}: "
                        f"{exc.strerror or exc.__class__.__name__}"
                    ),
                    retryable=False,
                ),
                duration_ms=int((time.monotonic() - started) * 1000),
            )

        # 9. Validar que o que ficou no destino é uma imagem válida
        #    — se a V1 tiver baixado um HTML de erro (ex.: rate
        #    limit), queremos falhar com LOCAL_IO em vez de subir um
        #    "PNG" que não é PNG.
        try:
            self._validate_image_file(output_path)
        except ValueError as exc:
            # ``_validate_image_file`` já devolve mensagens curtas do
            # tipo "Arquivo ... está vazio" / "Arquivo ... não é uma
            # imagem válida: ..." — usamos direto, sem re-prefixar.
            return self._failure_result(
                request=request,
                error=GenerationError(
                    code=ErrorCode.LOCAL_IO,
                    message=str(exc),
                    retryable=False,
                ),
                duration_ms=int((time.monotonic() - started) * 1000),
            )

        # 10. Sucesso.
        bytes_written = output_path.stat().st_size
        duration_ms = int((time.monotonic() - started) * 1000)
        logger.info(
            "ChatGPTDesktopAutomationProvider: OK (bytes=%d, duracao=%dms, "
            "output=%s)",
            bytes_written,
            duration_ms,
            output_path,
        )
        return GenerationResult(
            success=True,
            output_path=output_path,
            model_used="chatgpt-desktop",
            duration_ms=duration_ms,
            request_id="",
            error=None,
            bytes_written=bytes_written,
            attempts=1,
        )

    # ------------------------------------------------------------------ #
    # Helpers                                                            #
    # ------------------------------------------------------------------ #

    def _validate_request(self, request: GenerationRequest) -> None:
        """Mesma política do provider OpenAI: erros de programação
        viram ``RuntimeError`` (não classificados), erros previsíveis
        viram ``LocalIOError`` mapeado no caller.

        Aqui, qualquer inconsistência do request é capturada pelo
        ``try/except`` amplo de ``generate()`` e convertida em
        ``ErrorCode.UNKNOWN`` com mensagem específica.
        """
        if not request.reference_image_path:
            raise RuntimeError("reference_image_path vazio")
        ref = Path(request.reference_image_path)
        if not ref.exists() or not ref.is_file():
            raise RuntimeError(
                f"reference_image_path inexistente: {ref}"
            )
        if not request.prompt_text or not request.prompt_text.strip():
            raise RuntimeError("prompt_text vazio")
        if not request.output_path:
            raise RuntimeError("output_path vazio")
        # Sugere extensão de imagem para o destino — mas não falha
        # se vier outra (a UI pode preferir .jpg, .webp, etc.).
        out_ext = Path(request.output_path).suffix.lower()
        if out_ext and out_ext not in _VALID_IMAGE_SUFFIXES:
            logger.warning(
                "ChatGPTDesktopAutomationProvider: output_path tem extensão "
                "incomum %r; o provider aceita mas a UI pode estranhar.",
                out_ext,
            )

    @staticmethod
    def _validate_image_file(path: Path) -> None:
        """Confirma que ``path`` é uma imagem válida via Pillow.

        Reabrir com ``Image.open(...).verify()`` + ``.load()`` é o
        mesmo padrão do provider OpenAI (detecta arquivos truncados
        que só ``verify()`` deixa passar). Erros viram
        ``ValueError`` com mensagem específica da causa, pronta
        para a UI exibir.
        """
        path = Path(path)
        if path.stat().st_size == 0:
            raise ValueError(f"Arquivo de saída está vazio: {path}")
        try:
            with Image.open(path) as img:
                img.verify()
            with Image.open(path) as img:
                img.load()
        except UnidentifiedImageError as exc:
            raise ValueError(
                f"Arquivo baixado não é uma imagem válida "
                f"(formato não reconhecido): {path}"
            ) from exc
        except (ValueError, OSError) as exc:
            raise ValueError(
                f"Arquivo baixado não é uma imagem válida "
                f"({exc.__class__.__name__}): {path}"
            ) from exc

    @staticmethod
    def _failure_result(
        *,
        request: GenerationRequest,
        error: GenerationError,
        duration_ms: int,
    ) -> GenerationResult:
        """Monta um ``GenerationResult`` de falha padronizado."""
        # Mesmo padrão do provider OpenAI: `output_path` aponta para
        # o destino pretendido (mesmo que o arquivo não tenha sido
        # gravado), para que a UI saiba o que reportar ao usuário.
        try:
            fallback_path = Path(request.output_path)
        except TypeError:
            fallback_path = Path(".")
        return GenerationResult(
            success=False,
            output_path=fallback_path,
            model_used="chatgpt-desktop",
            duration_ms=duration_ms,
            request_id="",
            error=error,
            bytes_written=0,
            attempts=1,
        )


__all__ = ["ChatGPTDesktopAutomationProvider"]