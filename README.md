# Gerador de Imagens de Produto

Aplicativo desktop (Windows + Linux) para geração automatizada de imagens
de produto de vestuário, usando a API oficial da OpenAI. O usuário
aponta uma pasta com fotos originais, configura um prompt (preset),
divide o trabalho em lotes e deixa o software processar tudo com
controle de fila, pausa, retomada, retentativas e dashboard agregado.

O sistema foi desenhado para nunca cobrar (e nunca gastar créditos)
duas vezes pela mesma imagem gerada: cada `ImageJob` é identificado
por um hash do conteúdo do arquivo de entrada combinado com o hash do
prompt, modelo e parâmetros. Se um job já terminou com sucesso e o
arquivo de saída continua válido, o processamento pula essa imagem.

Este repositório implementa o projeto em 12 etapas. O estado atual
cobre **Prompts 1-6**: fundação, credenciais seguras, scanner de
imagens, presets de prompt, planejamento de lotes e a integração
oficial com a API da OpenAI via provedor isolado.

---

## Pré-requisitos

- Python 3.11 ou superior (`python --version`).
- Git (opcional, para clonar o repositório).
- Nenhuma chave de API é necessária para rodar este prompt.

---

## Credenciais e armazenamento seguro

A chave da API da OpenAI é gerenciada inteiramente pela interface
gráfica — em **Configuração → Configurar chave da OpenAI…**. Ela nunca
é gravada em texto puro no disco e nunca aparece em logs, banco,
exceções, título de janela ou ferramenta de diagnóstico.

O `CredentialManager` (em `app/data/storage/credential_manager.py`)
escolhe automaticamente entre dois backends:

1. **Primário: `keyring`** — o cofre do próprio sistema operacional
   (Credential Manager no Windows, Secret Service/libsecret no Linux).
   É o caminho preferido. O nosso processo só guarda uma referência;
   o segredo vive em `CredentialManager`, fora do nosso alcance.
2. **Fallback: arquivo criptografado local** com `Fernet`
   (`cryptography`). Usado **somente** quando o backend keyring
   não está disponível no ambiente — isso é detectado automaticamente,
   sem escolha manual.

O fallback é explicitamente **secundário**: ele evita texto puro, mas
se alguém tiver acesso de leitura à pasta de dados do usuário **e**
souber onde está a chave de criptografia, consegue decifrar. É mais
forte que texto puro, mais fraco que o cofre do SO. Os dois arquivos
ficam em locais separados para que um único backup não entregue
chave + cifrador juntos.

A validação de "chave válida" é feita com uma chamada mínima à API
oficial (`client.models.list()`). Não usamos heurística de formato.

Para depuração, a env var `GERADORIMAGENS_FORCE_FILE_BACKEND=1`
força o uso do backend de arquivo criptografado (útil em ambientes
sem Secret Service, como CI headless).

---

## Instalação e execução

### Windows (PowerShell)

```powershell
# a partir da raiz do projeto
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
python main.py
```

### Linux (bash)

```bash
# a partir da raiz do projeto
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
python main.py
```

Em ambos os sistemas a janela "Gerador de Imagens de Produto" deve
abrir com três abas vazias (Configuração, Processamento, Status
Geral) e nenhum processo Python deve permanecer em segundo plano
após o fechamento.

---

## Como rodar os testes

```bash
pytest
```

Os smoke tests não exigem rede, chave de API ou banco real.

---

## Arquitetura

O código está dividido em três camadas com dependências unidirecionais:

```
   ui  ──►  core  ◄──  data
                ▲
                │
             config
```

- **`app/core`** — regras de negócio. Modelos de domínio
  (`Project`, `Batch`, `ImageJob`, `GenerationAttempt`), serviços
  (scanner, planejador de lotes, orquestrador), interface
  `ImageGenerationProvider`, exceções tipadas e utilitários puros.
  **Nunca importa PySide6** (verificável com
  `grep -r "PySide6" app/core`).
- **`app/data`** — única camada que toca SQLite, keyring e arquivos
  de configuração. Expõe repositórios para `core`.
- **`app/ui`** — janela, diálogos, widgets e workers Qt. Pode
  importar de `core` e `data`; jamais o contrário. Workers Qt
  (`BaseWorker`) já existem para garantir que toda operação lenta
  futura rode fora da thread principal.
- **`app/config`** — constantes, resolução de diretórios por SO,
  configuração de logging. Não tem dependência de UI.

O app **não depende do diretório de trabalho atual** para localizar
seus próprios arquivos: `app/config/paths.py` resolve os diretórios
de dados do usuário usando `%APPDATA%` no Windows e `$XDG_DATA_HOME`
(ou `~/.local/share`) no Linux.

---

## Integração com a API da OpenAI (Prompt 6)

Toda comunicação com o provedor remoto passa por uma única
interface, `ImageGenerationProvider`, em `app/core/providers/`.
A implementação concreta é `OpenAIImageGenerationProvider`.

### SDK Python oficial

Versão fixada em `pyproject.toml`/`requirements.txt`:
**`openai>=1.40,<2`**. A versão efetivamente instalada na
validação do prompt 6 foi **`2.50.0`**. Todos os nomes
(`client.images.edit`, `ImagesResponse`, exceções, header
`x-request-id`) foram confirmados por introspecção direta no
SDK instalado — **nenhum nome foi inventado**.

### Endpoint

`POST /v1/images/edits` (chamado por `client.images.edit(...)`).
Este é o único endpoint que aceita **simultaneamente** uma imagem de
referência (`image`) e um prompt de texto (`prompt`) na
documentação da OpenAI:

- `images.generate`: só texto, sem imagem de referência.
- `images.create_variation`: só imagem, sem prompt.

### Modelos suportados pelo endpoint (confirmados no SDK 2.50.0)

| Modelo | Resoluções (`size`) | Status |
|---|---|---|
| `gpt-image-1` | auto, 1024x1024, 1536x1024, 1024x1536 | ✅ padrão recomendado |
| `dall-e-2` | 256x256, 512x512, 1024x1024 | ⚠️ legado, restrito a 1024x1024 |

`model` é configurável pelo usuário via preset — o provider não
hardcoda nenhum valor. Identificadores inválidos são rejeitados
pela própria OpenAI com erro 400, que o provider converte em
`ERR_INVALID_PARAMS`.

### Parâmetros opcionais aceitos no `extra_parameters`

Filtrados por whitelist derivado da introspecção de
`inspect.signature(openai.resources.images.Images.edit)`:

- `size`, `quality`, `background`, `input_fidelity`,
  `output_format`, `output_compression`, `n`, `user`, `mask`

Qualquer chave fora desse conjunto é **ignorada** (com warning no
log) — não quebra o provider e protege contra presets antigos com
campos obsoletos.

### Decisões e limitações documentadas

1. **`response_format` é SEMPRE forçado para `"b64_json"`**, mesmo
   que o request peça `"url"`. URLs da OpenAI expiram em ~1 hora e
   não são confiáveis para download. Com `b64_json` os bytes
   chegam embutidos no response — sem segunda chamada HTTP.
2. **Validação do arquivo escrito**: depois de gravar em
   `output.png.part`, o provider reabre o arquivo com Pillow
   (`verify()` + `load()`) para confirmar que é uma imagem válida
   e não está vazia. Só então faz `os.replace` para o nome
   definitivo. Em qualquer falha, o `.part` é removido.
3. **Retry automático**: apenas para `ERR_RATE_LIMIT`,
   `ERR_TIMEOUT`, `ERR_CONNECTION`, `ERR_SERVER`. Backoff
   exponencial com jitter, `min(30s, 1s · 2^(n-1)) + rand(0, 0.5)`.
   `ERR_AUTH`, `ERR_CONTENT_REJECTED`, `ERR_INVALID_PARAMS`,
   `ERR_QUOTA_EXCEEDED` falham imediatamente.
4. **Mapeamento de exceções**: cada exceção do SDK é convertida
   num `GenerationError` com `error_code` da taxonomia do projeto.
   Nenhuma exceção do SDK vaza para fora do provider — o caller
   recebe sempre `GenerationResult`.
5. **Isolamento do SDK**: apenas `openai_image_generation_provider.py`
   importa o SDK. O restante do sistema fala com a interface
   `ImageGenerationProvider`. (Única exceção consciente:
   `credential_manager.py` usa `client.models.list()` apenas para
   VALIDAR a chave — não para gerar imagens.)

### Teste manual com chave real

Veja [`docs/MANUAL_TEST_PROVIDER.md`](docs/MANUAL_TEST_PROVIDER.md)
para o passo-a-passo de validação ao vivo com uma chave da OpenAI e
uma única imagem.

---

## Próximas etapas

| # | Tema | Status |
|---|---|---|
| 1 | Fundação e estrutura | ✅ |
| 2 | Credenciais seguras | ✅ |
| 3 | Scanner de imagens | ✅ |
| 4 | Presets de prompt | ✅ |
| 5 | Planejamento de lotes | ✅ |
| 6 | Integração com a API (provider) | ✅ este prompt |
| 7 | Motor de processamento (fila/pausa) | ⏳ |
| 8 | Persistência + idempotência | ⏳ |
| 9 | Dashboard | ⏳ |
| 10 | Resiliência e logs | ⏳ |
| 11 | QA completo | ⏳ |
| 12 | Empacotamento | ⏳ |