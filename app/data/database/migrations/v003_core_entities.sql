-- Migration v003 (Prompt 8): Project / Batch / ImageJob / GenerationAttempt.
--
-- Idempotência por (input_hash, prompt_hash, model, parameters_hash)
-- e resiliência a crash do BatchProcessor (recover() reseta jobs
-- travados em PROCESSING para PENDING).
--
-- Não toca as tabelas dos prompts anteriores. As migrations v001 e
-- v002 já existem e são reaplicadas apenas se o banco for novo.

-- ============================================================================ --
-- 1. projects                                                                   --
-- ============================================================================ --

CREATE TABLE IF NOT EXISTS projects (
    id                  TEXT PRIMARY KEY,
    name                TEXT NOT NULL,
    input_folder_path   TEXT NOT NULL,
    output_folder_path  TEXT NOT NULL,
    created_at          TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_projects_created_at
    ON projects(created_at DESC);

-- ============================================================================ --
-- 2. batches                                                                    --
-- ============================================================================ --

CREATE TABLE IF NOT EXISTS batches (
    id           TEXT PRIMARY KEY,
    project_id   TEXT NOT NULL,
    name         TEXT NOT NULL,
    folder_path  TEXT NOT NULL,
    preset_id    TEXT NOT NULL,
    status       TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    source_total INTEGER NOT NULL DEFAULT 0,

    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
    FOREIGN KEY (preset_id)  REFERENCES prompt_presets(id) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_batches_project_id ON batches(project_id);
CREATE INDEX IF NOT EXISTS idx_batches_status     ON batches(status);

-- ============================================================================ --
-- 3. image_jobs                                                                 --
-- ============================================================================ --

CREATE TABLE IF NOT EXISTS image_jobs (
    id              TEXT PRIMARY KEY,
    batch_id        TEXT NOT NULL,
    input_path      TEXT NOT NULL,
    input_hash      TEXT NOT NULL,
    prompt_hash     TEXT NOT NULL,
    model           TEXT NOT NULL,
    parameters_hash TEXT NOT NULL,
    output_path     TEXT NOT NULL,
    status          TEXT NOT NULL,
    attempts_count  INTEGER NOT NULL DEFAULT 0,
    error_code      TEXT NOT NULL DEFAULT '',
    error_message   TEXT NOT NULL DEFAULT '',
    last_request_id TEXT NOT NULL DEFAULT '',
    created_at      TEXT NOT NULL,
    started_at      TEXT,
    completed_at    TEXT,

    FOREIGN KEY (batch_id) REFERENCES batches(id) ON DELETE CASCADE
);

-- Busca por jobs de um lote (dashboard, recover).
CREATE INDEX IF NOT EXISTS idx_image_jobs_batch_id
    ON image_jobs(batch_id);

-- Chave de idempotência — busca rápida de cache hit por (input, prompt, modelo, params).
CREATE INDEX IF NOT EXISTS idx_image_jobs_idempotency
    ON image_jobs(input_hash, prompt_hash, model, parameters_hash);

-- Crash recovery (recover filtra status='PROCESSING').
CREATE INDEX IF NOT EXISTS idx_image_jobs_status
    ON image_jobs(status);

-- ============================================================================ --
-- 4. generation_attempts                                                        --
-- ============================================================================ --

CREATE TABLE IF NOT EXISTS generation_attempts (
    id              TEXT PRIMARY KEY,
    image_job_id    TEXT NOT NULL,
    attempt_number  INTEGER NOT NULL,
    started_at      TEXT NOT NULL,
    finished_at     TEXT,
    success         INTEGER NOT NULL DEFAULT 0,
    error_code      TEXT NOT NULL DEFAULT '',
    error_message   TEXT NOT NULL DEFAULT '',
    http_status     INTEGER,
    duration_ms     INTEGER NOT NULL DEFAULT 0,

    FOREIGN KEY (image_job_id) REFERENCES image_jobs(id) ON DELETE CASCADE,

    -- Monotonicidade: cada job tem no máximo UM attempt por número.
    UNIQUE (image_job_id, attempt_number)
);

CREATE INDEX IF NOT EXISTS idx_generation_attempts_image_job_id
    ON generation_attempts(image_job_id);