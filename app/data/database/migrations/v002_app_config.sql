-- Migration v002 (Prompt 5): configurações gerais chave/valor.
-- Apenas valores escalares — NUNCA armazena credenciais aqui.
-- A chave da API OpenAI vive em keyring/arquivo criptografado.

CREATE TABLE IF NOT EXISTS app_config (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
