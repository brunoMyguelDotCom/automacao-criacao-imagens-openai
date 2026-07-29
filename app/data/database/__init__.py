"""Conexão com o banco SQLite, migrations e schema inicial.

Responsável por abrir/fechar conexões, aplicar migrations versionadas
e expor a sessão de banco para os repositórios. As migrations vivem
em `migrations/v###_*.sql` e são aplicadas em ordem.

Prompt 4 introduziu a tabela `prompt_presets`. Prompt 5 adicionou
`app_config`. O schema completo (Project/Batch/ImageJob/
GenerationAttempt) virá no Prompt 8.
"""