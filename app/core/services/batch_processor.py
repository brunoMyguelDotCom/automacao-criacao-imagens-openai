"""Motor de processamento assíncrono do projeto (Prompt 7).

Orquestra o processamento SEQUENCIAL de uma lista de `ImageJob`s
usando um `ImageGenerationProvider`. Não conhece PySide6 — emite
eventos via callbacks que a camada de UI (`BatchProcessorWorker`)
traduz para signals Qt.

Pontos de extensão para evolução futura:
    * `JobExecutor` é injetável. Hoje só existe a versão sequencial
      (`SequentialJobExecutor`), mas a forma de uso é a mesma que
      uma futura `ParallelJobExecutor` teria — basta trocar a
      injeção no construtor do `BatchProcessor`.
    * `on_event` é um callable único chamado com uma `Event`
      discriminada. A UI pode ignorar eventos que não conhece.
    * O provider em si é injetável — pode ser mock, OpenAI,
      cache local, etc.

Estado interno (não persistido):
    * ``_running`` — True entre `start()` e o término natural ou cancelamento.
    * ``_paused`` — True após `pause()`, fica False após `resume()`.
    * ``_cancelled`` — True após `cancel()`, permanece até o final do loop.
    * ``_current_job_id`` — id do job em PROCESSING (ou None).

Regras importantes:
    * `pause()` NÃO interrompe um job em PROCESSING. O job
      termina normalmente; a pausa passa a valer ANTES de iniciar
      o próximo job.
    * `cancel()` impede o início de novos jobs a partir daquele
      momento. Jobs já SUCCESS permanecem. Jobs PENDING viram
      CANCELLED explicitamente (não ficam perdidos).
    * `start()` é idempotente: se já está rodando, ignora. Chamar
      `start()` após `cancel()` ou após conclusão também é no-op.
    * `resume()` é equivalente a `start()` mas NÃO toca jobs já
      em estado terminal (SUCCESS, FAILED, CANCELLED) — só
      continua dos que estão PENDING/PAUSED.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable, Iterable, Protocol

from app.core.models import (
    ErrorCode,
    GenerationError,
    GenerationRequest,
    GenerationResult,
    ImageJob,
    ImageJobStatus,
)
from app.core.providers import ImageGenerationProvider

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Eventos                                                                      #
# --------------------------------------------------------------------------- #


class EventKind(str, Enum):
    """Tipos de evento emitidos pelo BatchProcessor."""

    JOB_STARTED = "job_started"
    JOB_SUCCEEDED = "job_succeeded"
    JOB_FAILED = "job_failed"
    JOB_RETRIED = "job_retried"
    JOB_REUSED = "job_reused"
    BATCH_STARTED = "batch_started"
    BATCH_PAUSED = "batch_paused"
    BATCH_RESUMED = "batch_resumed"
    BATCH_CANCELLED = "batch_cancelled"
    BATCH_COMPLETED = "batch_completed"
    PROGRESS_UPDATED = "progress_updated"


@dataclass(frozen=True)
class ProgressSnapshot:
    """Contadores para a barra de progresso da UI.

    Attributes:
        total: total de jobs no lote.
        success: quantos terminaram com SUCCESS.
        failed: quantos terminaram com FAILED.
        pending: quantos ainda vão rodar (PENDING ou PAUSED).
        processing: quantos estão PROCESSING (sempre 0 ou 1 na
            versão sequencial).
        cancelled: quantos viraram CANCELLED.
        percent: ``int(100 * done / total)`` onde ``done =
            success + failed + cancelled``. Nunca divide por zero.
    """

    total: int
    success: int
    failed: int
    pending: int
    processing: int
    cancelled: int
    percent: int = 0


@dataclass(frozen=True)
class BatchEvent:
    """Evento discriminado emitido pelo BatchProcessor.

    A UI usa ``kind`` para decidir o que fazer; os campos
    adicionais ficam disponíveis para quem precisar.
    """

    kind: EventKind
    job: ImageJob | None = None
    progress: ProgressSnapshot | None = None
    error: GenerationError | None = None
    message: str = ""

    # Campos derivados comuns (atalho para a UI não precisar fazer
    # getattr(job, '...') repetidamente).
    @property
    def job_id(self) -> str:
        return self.job.id if self.job else ""

    @property
    def file_name(self) -> str:
        if self.job is None:
            return ""
        return Path(self.job.reference_image_path).name


# Callback público — assinatura: ``Callable[[BatchEvent], None]``.
EventCallback = Callable[[BatchEvent], None]


# --------------------------------------------------------------------------- #
# JobExecutor — ponto de extensão para paralelismo futuro                     #
# --------------------------------------------------------------------------- #


class JobExecutor(Protocol):
    """Contrato para execução de UM job (ou vários).

    A versão atual (sequencial) executa UM por vez. A futura versão
    paralela pode executar N em pool e manter a mesma interface
    do ponto de vista do `BatchProcessor` (que chama
    ``executor.run(job, ctx)`` para cada job).
    """

    def run(
        self,
        job: ImageJob,
        ctx: "_JobContext",
    ) -> None:  # pragma: no cover - protocolo
        ...


@dataclass
class _JobContext:
    """Contexto passado ao executor para cada job.

    Encapsula tudo o que o executor precisa para rodar UM job e
    reportar o resultado. O executor pode chamar ``provider`` e
    emitir eventos via ``emit``.
    """

    provider: ImageGenerationProvider
    emit: EventCallback
    is_pause_requested: Callable[[], bool]
    is_cancel_requested: Callable[[], bool]
    sleep: Callable[[float], None]


class SequentialJobExecutor:
    """Executor que processa UM job por vez (atual)."""

    def run(self, job: ImageJob, ctx: _JobContext) -> None:
        # Pausa já foi checada antes de chegar aqui; cancelamento
        # é checado DEPOIS de cada job para honrar "não interrompe
        # uma chamada de rede em andamento".
        if ctx.is_cancel_requested():
            logger.debug("SequentialJobExecutor: cancel já solicitado; pulando %s", job.id)
            return
        ctx.emit(
            BatchEvent(
                kind=EventKind.JOB_STARTED,
                job=job.with_status(ImageJobStatus.PROCESSING),
                message=f"Iniciando {Path(job.reference_image_path).name}",
            )
        )

        request = GenerationRequest(
            reference_image_path=Path(job.reference_image_path),
            prompt_text=job.prompt_text,
            model=job.model,
            output_path=Path(job.output_path),
            extra_parameters=dict(job.extra_parameters or {}),
            max_retries=0,  # retries automáticos do provider já acontecem;
            # aqui no nível do orquestrador, não fazemos mais retries.
            # O número de tentativas reais aparece em result.attempts.
        )

        started_job = job.with_attempts_count_increment()
        result: GenerationResult = ctx.provider.generate(request)

        # `attempts_count` no ImageJob reflete o TOTAL de tentativas
        # (a incrementada por nós + as automáticas do provider).
        # Se o provider reportou mais tentativas que isso, vence.
        if result.attempts > started_job.attempts_count:
            started_job = started_job.with_attempts_count_increment(
                delta=result.attempts - started_job.attempts_count
            )

        if result.success:
            ctx.emit(
                BatchEvent(
                    kind=EventKind.JOB_SUCCEEDED,
                    job=started_job.with_status(
                        ImageJobStatus.SUCCESS,
                        request_id=result.request_id,
                    ),
                    message=f"Sucesso: {Path(job.reference_image_path).name}",
                )
            )
            return

        # Falha. O provider já tentou até max_retries. Marca como
        # FAILED e emite.
        err = result.error or GenerationError(
            code=ErrorCode.UNKNOWN, message="falha desconhecida"
        )
        # Se o provider já tinha sinalizado retryable, o BatchProcessor
        # poderia re-tentar; mas como `max_retries=0` no request, o
        # provider JÁ esgotou seus retries. Falha terminal.
        ctx.emit(
            BatchEvent(
                kind=EventKind.JOB_FAILED,
                job=started_job.with_status(
                    ImageJobStatus.FAILED,
                    error_code=err.code,
                    error_message=err.message,
                    request_id=result.request_id,
                ),
                error=err,
                message=f"Falha: {Path(job.reference_image_path).name} — {err.message}",
            )
        )


# --------------------------------------------------------------------------- #
# BatchProcessor                                                               #
# --------------------------------------------------------------------------- #


def _is_output_valid(path: Path) -> bool:
    """Verifica que `path` existe e pode ser reaberto pelo Pillow.

    Import lazy do Pillow para não penalizar casos em que o cache
    hit não é exercido. Levanta qualquer exceção Pillow →
    ``False`` (fail-safe).
    """
    try:
        if not path.exists() or not path.is_file():
            return False
        from PIL import Image, UnidentifiedImageError

        with Image.open(path) as img:
            img.verify()
        return True
    except (OSError, UnidentifiedImageError):
        return False


class BatchProcessor:
    """Orquestrador sequencial de um lote de `ImageJob`s.

    Uso típico:

        processor = BatchProcessor(
            provider=provider,
            jobs=[job1, job2, ...],
            on_event=my_callback,
            executor=SequentialJobExecutor(),
        )
        processor.start()
        ...
        processor.pause() / processor.resume() / processor.cancel()

    Thread-safety:
        * NÃO é seguro chamar `start/resume/pause/cancel` de
          múltiplas threads simultaneamente. Mas o `BatchProcessorWorker`
          (UI) chama esses métodos SEMPRE da thread principal,
          enquanto o loop roda em outra thread. Para evitar race
          conditions simples, um `threading.Lock` protege o estado.
        * O callback `on_event` é chamado NA thread de execução
          (não na main). Quem recebe (o Worker) é responsável por
          repassar para a thread da UI via Qt signals.
    """

    def __init__(
        self,
        provider: ImageGenerationProvider,
        jobs: list[ImageJob],
        on_event: EventCallback,
        *,
        batch_id: str = "",
        executor: JobExecutor | None = None,
        sleep: Callable[[float], None] | None = None,
        idempotency_checker: Callable[[ImageJob], ImageJob | None] | None = None,
    ) -> None:
        if on_event is None:
            raise ValueError("on_event é obrigatório")
        self._provider = provider
        self._jobs: list[ImageJob] = list(jobs)
        self._on_event = on_event
        self._batch_id = batch_id
        self._executor: JobExecutor = executor or SequentialJobExecutor()
        self._sleep = sleep or _default_sleep
        self._idempotency_checker = idempotency_checker

        self._lock = threading.Lock()
        self._running = False
        self._paused = False
        self._cancelled = False
        self._finished_naturally = False  # completou sem cancel
        self._current_job_id: str | None = None
        # Thread que executa o loop. Criada em `start()` e joinable
        # via `wait_until_done()`. Daemon=True garante que não
        # bloqueia o shutdown do Python se algo der errado.
        self._thread: threading.Thread | None = None

        # Observers adicionais (além do on_event principal). Cada
        # observer recebe CÓPIA do evento. Usado pelo worker Qt
        # para emitir signals sem precisar mexer no callback
        # principal.
        self._observers: list[EventCallback] = []

        # Cache do estado mais recente de cada job, atualizado
        # pelo wrapper abaixo.
        self._latest_jobs: dict[str, ImageJob] = {j.id: j for j in self._jobs}

        # Envelopa o callback do usuário: atualiza o cache interno
        # ANTES de chamar o callback real (e o loop usa esse cache
        # para ler o estado pós-execução).
        user_callback = self._on_event

        def wrapping_callback(event: BatchEvent) -> None:
            if event.job is not None:
                self._latest_jobs[event.job.id] = event.job
            # Notifica observers ANTES do callback principal
            # (observers tipicamente são Qt signals — devem
            # refletir o estado recém-alterado).
            for obs in list(self._observers):
                try:
                    obs(event)
                except Exception:  # noqa: BLE001
                    logger.exception("Observer falhou (ignorado)")
            user_callback(event)

        self._emit: EventCallback = wrapping_callback

    def add_observer(self, observer: EventCallback) -> None:
        """Registra um observer adicional.

        Cada observer recebe cada `BatchEvent` na ordem em que foi
        emitido. Usado pelo `BatchProcessorWorker` para emitir
        signals Qt sem acoplar a UI ao callback principal.
        """
        self._observers.append(observer)

    # ------------------------------------------------------------------ #
    # API pública                                                         #
    # ------------------------------------------------------------------ #

    def start(self) -> None:
        """Inicia o processamento do lote numa thread interna e
        retorna IMEDIATAMENTE (não-bloqueante).

        Idempotente: se já está rodando, retorna sem efeito.
        Se já terminou ou foi cancelado, também retorna sem efeito
        (caller deve criar um novo processor).

        Para esperar a conclusão de forma síncrona (ex.: em testes),
        use `wait_until_done(timeout=...)`.
        """
        with self._lock:
            if self._running:
                logger.debug("BatchProcessor.start: já rodando — ignorando")
                return
            if self._finished_naturally or self._cancelled:
                logger.debug(
                    "BatchProcessor.start: lote já %s — ignorando",
                    "concluído" if self._finished_naturally else "cancelado",
                )
                return
            self._running = True
            self._paused = False
            self._thread = threading.Thread(
                target=self._run_loop,
                name=f"BatchProcessor-{self._batch_id or id(self)}",
                daemon=True,
            )
            self._thread.start()
        # Emitimos BATCH_STARTED ANTES de retornar — em sincronia
        # com o flag _running=True, para que observers (incluindo o
        # que dispara pause()) não peguem um estado inconsistente.
        self._emit(
            BatchEvent(kind=EventKind.BATCH_STARTED, message="Lote iniciado")
        )

    def wait_until_done(self, timeout: float | None = None) -> bool:
        """Bloqueia até a thread interna terminar.

        Retorna True se o lote terminou dentro do timeout (ou se já
        não está rodando), False se o timeout expirou. Em caso de
        timeout a thread continua rodando — caller pode chamar de
        novo para esperar mais.
        """
        with self._lock:
            thread = self._thread
        if thread is None or not thread.is_alive():
            return True
        thread.join(timeout=timeout)
        return not thread.is_alive()

    def pause(self) -> None:
        """Solicita pausa. O job atual termina normalmente; a pausa
        vale antes de iniciar o próximo."""
        with self._lock:
            if not self._running or self._paused or self._cancelled:
                return
            self._paused = True
            self._emit(
                BatchEvent(kind=EventKind.BATCH_PAUSED, message="Pausa solicitada")
            )

    def resume(self) -> None:
        """Retoma de onde parou. Só continua se o lote está pausado.

        Equivalente a chamar `start()` num processor pausado — mas
        `start()` também funciona. Mantemos os dois para clareza
        na UI (cada botão chama seu método).
        """
        with self._lock:
            if not self._running or not self._paused or self._cancelled:
                return
            self._paused = False
            self._emit(
                BatchEvent(kind=EventKind.BATCH_RESUMED, message="Retomando")
            )

        # NÃO chama _run_loop de novo — o loop original já está
        # em _wait_while_paused. Apenas libera a flag.

    def cancel(self) -> None:
        """Solicita cancelamento. O job atual termina normalmente;
        nenhum job novo começa depois dele."""
        with self._lock:
            if not self._running or self._cancelled:
                return
            self._cancelled = True
            self._paused = False  # libera se estava pausado
            self._emit(
                BatchEvent(
                    kind=EventKind.BATCH_CANCELLED,
                    message="Cancelamento solicitado",
                )
            )

    # ------------------------------------------------------------------ #
    # Inspeção de estado (usada pela UI para habilitar/desabilitar)     #
    # ------------------------------------------------------------------ #

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def is_paused(self) -> bool:
        return self._paused

    @property
    def is_cancelled(self) -> bool:
        return self._cancelled

    @property
    def current_job_id(self) -> str | None:
        return self._current_job_id

    @property
    def jobs_snapshot(self) -> list[ImageJob]:
        """Cópia imutável da lista de jobs (estado atual)."""
        return list(self._jobs)

    # ------------------------------------------------------------------ #
    # Loop principal                                                     #
    # ------------------------------------------------------------------ #

    def _run_loop(self) -> None:
        """Itera sobre os jobs, pulando os terminais.

        Este método é executado na thread do worker (NÃO main).
        """
        try:
            # Calcula snapshot inicial do progresso.
            self._emit_progress()

            # Itera por ÍNDICE (não por objeto) — o estado dos jobs
            # na lista é atualizado a cada iteração e o `job` local
            # pode ficar obsoleto.
            i = 0
            while i < len(self._jobs):
                job = self._jobs[i]

                if self._cancelled:
                    # Marca o que sobrou como CANCELLED.
                    self._cancel_pending_from(job)
                    break

                # Jobs já em estado terminal são pulados. Isso
                # atende ao requisito 5: SUCCESS nunca é
                # reprocessado na mesma execução.
                if job.status in (
                    ImageJobStatus.SUCCESS,
                    ImageJobStatus.FAILED,
                    ImageJobStatus.CANCELLED,
                ):
                    i += 1
                    continue

                # Idempotency check (Prompt 8): antes de chamar o
                # provider, pergunta ao checker se já existe um
                # ImageJob SUCCESS com a mesma tupla 4-campos
                # (input_hash, prompt_hash, model, parameters_hash).
                # Se sim E o output_path ainda é um arquivo válido
                # (Pillow), reaproveita — pula o provider, marca
                # como SUCCESS e emite JOB_REUSED.
                cached = self._try_idempotency_hit(job)
                if cached is not None:
                    self._jobs[i] = cached
                    self._latest_jobs[cached.id] = cached
                    self._emit(
                        BatchEvent(
                            kind=EventKind.JOB_REUSED,
                            job=cached,
                            message=(
                                f"Reaproveitado do cache: "
                                f"{Path(cached.reference_image_path).name}"
                            ),
                        )
                    )
                    self._emit_progress()
                    i += 1
                    continue

                # Espera enquanto estiver pausado (não retorna até
                # resume() ou cancel()).
                self._wait_while_paused()
                if self._cancelled:
                    self._cancel_pending_from(job)
                    break

                # Marca como PROCESSING — atualiza a lista in-place.
                with self._lock:
                    self._current_job_id = job.id
                    self._jobs[i] = job.with_status(ImageJobStatus.PROCESSING)
                self._emit_progress()

                # Prepara contexto e delega ao executor. O executor
                # recebe uma CÓPIA via `with_status`, então não há
                # race condition entre ele e a lista.
                ctx = _JobContext(
                    provider=self._provider,
                    emit=self._emit,
                    is_pause_requested=lambda: self._paused,
                    is_cancel_requested=lambda: self._cancelled,
                    sleep=self._sleep,
                )
                current = self._jobs[i]
                self._executor.run(current, ctx)

                # Lê o estado pós-execução do cache interno (o
                # wrapper do processor já atualizou ao emitir os
                # eventos JOB_SUCCEEDED/JOB_FAILED).
                final_state = self._latest_jobs.get(current.id)
                if final_state is not None:
                    self._jobs[i] = final_state

                # Marca que nenhum job está em PROCESSING agora.
                with self._lock:
                    self._current_job_id = None

                # Se o provider tentou mais de uma vez, emite
                # job_retried.
                if final_state is not None and final_state.attempts_count > 1:
                    self._emit(
                        BatchEvent(
                            kind=EventKind.JOB_RETRIED,
                            job=final_state,
                            message=(
                                f"{Path(current.reference_image_path).name} "
                                f"recuperou após {final_state.attempts_count} tentativas"
                            ),
                        )
                    )

                self._emit_progress()
                i += 1
        finally:
            with self._lock:
                self._running = False
                self._current_job_id = None
                if not self._cancelled:
                    self._finished_naturally = True
            self._emit_progress()
            self._emit(
                BatchEvent(
                    kind=EventKind.BATCH_COMPLETED,
                    message=(
                        "Lote concluído"
                        if not self._cancelled
                        else "Lote cancelado"
                    ),
                )
            )

    def _wait_while_paused(self) -> None:
        """Bloqueia enquanto _paused for True. Acorda quando
        resume() ou cancel() for chamado. Polling a cada 50ms —
        simples e suficiente para uma versão inicial (sem
        Condition Variable para manter o código legível)."""
        while not self._cancelled:
            with self._lock:
                paused = self._paused
            if not paused:
                return
            self._sleep(0.05)

    def _try_idempotency_hit(self, job: ImageJob) -> ImageJob | None:
        """Consulta o checker; devolve um ImageJob SUCCESS reutilizável
        ou None se cache miss / arquivo inválido.

        Comportamento:
            * Sem checker configurado → None (sem cache hit).
            * Checker levanta exceção → log + None (fail-safe; o lote
              continua normalmente).
            * Checker devolve None → cache miss.
            * Checker devolve um ImageJob com status != SUCCESS →
              ignora (trata como cache miss).
            * Checker devolve um ImageJob SUCCESS mas o `output_path`
              não é válido no disco (Pillow) → cache miss.

        Quando há hit, o ImageJob devolvido já vem com os campos de
        identidade preservados; o caller só precisa ajustar o id
        local para o do job atual (caso o checker tenha devolvido o
        registro cacheado, cujo id é o do job original — o Batch
        Processor copia `output_path` e `status`, mantém o `id` do job
        atual via replace).
        """
        if self._idempotency_checker is None:
            return None
        try:
            cached = self._idempotency_checker(job)
        except Exception:  # noqa: BLE001
            logger.exception("idempotency_checker falhou (ignorado)")
            return None
        if cached is None:
            return None
        if cached.status is not ImageJobStatus.SUCCESS:
            return None
        if not _is_output_valid(Path(cached.output_path)):
            return None
        # Mantém o id do job atual, copia os campos de identidade +
        # output_path + status do cache hit. attempts_count/created_at
        # do job atual são preservados (são locais a esta execução).
        from dataclasses import replace

        return replace(
            job,
            status=ImageJobStatus.SUCCESS,
            output_path=Path(cached.output_path),
            input_hash=cached.input_hash,
            prompt_hash=cached.prompt_hash,
            model=cached.model,
            parameters_hash=cached.parameters_hash,
            completed_at=job.updated_at,
        )

    def _cancel_pending_from(self, from_job: ImageJob) -> None:
        """A partir de ``from_job`` (inclusive), marca todos os jobs
        ainda não terminais como CANCELLED e emite o evento de cada
        um."""
        try:
            start_idx = self._jobs.index(from_job)
        except ValueError:
            start_idx = 0
        for i in range(start_idx, len(self._jobs)):
            j = self._jobs[i]
            if j.status in (
                ImageJobStatus.SUCCESS,
                ImageJobStatus.FAILED,
                ImageJobStatus.CANCELLED,
            ):
                continue
            cancelled_job = j.with_status(ImageJobStatus.CANCELLED)
            self._jobs[i] = cancelled_job
            self._latest_jobs[cancelled_job.id] = cancelled_job
            self._emit(
                BatchEvent(
                    kind=EventKind.JOB_FAILED,  # usamos JOB_FAILED com msg "cancelado"
                    job=cancelled_job,
                    message=f"Cancelado: {Path(j.reference_image_path).name}",
                )
            )
        self._emit_progress()

    def _extract_final_state(self, job_id: str) -> ImageJob | None:
        """Procura, na lista interna, o estado mais recente emitido
        para esse job_id.

        O executor chama `ctx.emit(BatchEvent(kind=JOB_SUCCEEDED,
        job=job.with_status(SUCCESS), ...))`. O `event.job` carrega
        o estado novo. Aqui capturamos e atualizamos a lista."""
        # Como `on_event` é o callback passado ao processor, e o
        # executor só vê cópias via `with_status`, o processor
        # precisa "escutar" seus próprios eventos e atualizar.
        # Truque: definimos um wrapper em start() que atualiza a
        # lista E chama o callback real do usuário.
        return self._latest_jobs.get(job_id)

    def _emit_progress(self) -> None:
        snap = self._compute_progress()
        # O evento PROGRESS_UPDATED também é a oportunidade de
        # propagar o estado novo dos jobs; chamamos o callback
        # apenas com o snapshot.
        self._emit(
            BatchEvent(
                kind=EventKind.PROGRESS_UPDATED,
                progress=snap,
            )
        )

    def _compute_progress(self) -> ProgressSnapshot:
        total = len(self._jobs)
        success = sum(1 for j in self._jobs if j.status is ImageJobStatus.SUCCESS)
        failed = sum(1 for j in self._jobs if j.status is ImageJobStatus.FAILED)
        cancelled = sum(
            1 for j in self._jobs if j.status is ImageJobStatus.CANCELLED
        )
        processing = sum(
            1 for j in self._jobs if j.status is ImageJobStatus.PROCESSING
        )
        pending = sum(
            1
            for j in self._jobs
            if j.status in (ImageJobStatus.PENDING, ImageJobStatus.PAUSED)
        )
        done = success + failed + cancelled
        percent = int(round(100 * done / total)) if total else 100
        return ProgressSnapshot(
            total=total,
            success=success,
            failed=failed,
            pending=pending,
            processing=processing,
            cancelled=cancelled,
            percent=percent,
        )


def _default_sleep(seconds: float) -> None:
    import time

    time.sleep(seconds)


__all__ = [
    "BatchProcessor",
    "BatchEvent",
    "EventKind",
    "ProgressSnapshot",
    "JobExecutor",
    "SequentialJobExecutor",
    "EventCallback",
]
