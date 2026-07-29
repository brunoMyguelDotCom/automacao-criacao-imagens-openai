-- Migration v001 (Prompt 4): schema inicial.
-- Tabelas `schema_version` e `prompt_presets`.

CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS prompt_presets (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    description     TEXT NOT NULL DEFAULT '',
    prompt_text     TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    is_default      INTEGER NOT NULL DEFAULT 0,

    -- Campos opcionais para parâmetros futuros (Prompt 6+).
    model           TEXT,
    resolution_w    INTEGER,
    resolution_h    INTEGER,
    quality         TEXT,
    output_format   TEXT,
    background      TEXT,
    n_variations    INTEGER
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_prompt_presets_only_one_default
    ON prompt_presets (is_default)
    WHERE is_default = 1;
