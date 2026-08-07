# GPT Image Batch Generator

Automação em Python para geração em lote de imagens usando o **ChatGPT Desktop
para Windows**. O operador apenas coloca imagens em `./input` e coleta o
resultado em `./output` — sem cliques manuais, sem copiar/colar prompts.

---

## ✨ Recursos

- 🤖 **100% automatizado** — anexa imagem, envia prompt, espera gerar, baixa
  e renomeia sozinho.
- 📁 **Descoberta de categoria** por nome do arquivo (`camisa_001.jpg`) ou por
  pasta (`input/Calças/foo.jpg`).
- 🧾 **Prompts em TXT** separados do código — basta editar `prompts/*.txt`.
- ⏱️ **Espera inteligente** — sem `time.sleep` fixo; o programa aguarda o
  botão "Stop generating" sumir, etc.
- 🔁 **Retry automático** com backoff — após N tentativas, a imagem vai para
  `./failed`.
- 🪟 **Windows UI Automation** via `pywinauto` / `uiautomation` — sem
  coordenadas de tela frágeis.
- 🧹 **Idempotente** — imagens processadas são movidas para `./processed`
  (ou apagadas, conforme config).
- 📊 **Logs rotacionados** em `logs/log.txt`.
- 🛠️ **Modo dry-run** para validar categorias/prompts sem automação.

---

## 📂 Estrutura

```
project/
├── main.py              # pipeline principal
├── config.py            # carrega config.json
├── config.json          # configuração
├── automation.py        # integração com ChatGPT Desktop (Windows)
├── prompt_loader.py     # lê prompts em ./prompts
├── downloader.py        # move arquivos (downloaded / output / failed / processed)
├── watcher.py           # monitora a pasta de downloads
├── logger.py            # logging com rotação
│
├── prompts/             # prompts por categoria (1 TXT por categoria)
│   ├── camisa.txt
│   ├── calca.txt
│   ├── vestido.txt
│   └── tenis.txt
│
├── input/               # coloque aqui as imagens de entrada
│   ├── camisa_001.jpg
│   └── Calcas/calca_001.jpg
│
├── output/              # resultado final (imagens geradas)
├── downloaded/          # downloads brutos do ChatGPT
├── processed/           # originais já processados
├── failed/              # imagens que falharam após N tentativas
├── logs/                # log.txt (rotacionado)
│
├── requirements.txt
├── run.bat              # atalho Windows
└── run.sh               # atalho Linux/macOS
```

---

## 🚀 Instalação (Windows)

1. **Instale o Python 3.12** e marque "Add Python to PATH".
2. **Instale o ChatGPT Desktop** e faça login uma vez (a sessão persiste).
3. Clone/extraia este projeto e, na pasta raiz, execute:

```bat
run.bat
```

O script cria um `.venv`, instala dependências e roda o pipeline.

### Instalação manual

```bat
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

---

## 🧪 Uso

### 1. Coloque suas imagens em `input/`

Você pode organizar de **duas formas**:

**Método 1 — por prefixo no nome:**
```
input/camisa_001.jpg
input/camisa_002.jpg
input/calca_001.jpg
```

**Método 2 — por pasta:**
```
input/Camisas/p001.jpg
input/Calcas/p002.jpg
input/Vestidos/p003.jpg
```

### 2. Crie/edite os prompts

Para cada categoria, crie um arquivo `.txt` em `prompts/`:

```txt
prompts/camisa.txt → texto do prompt para camisas
prompts/calca.txt  → texto do prompt para calças
```

O nome do arquivo (sem extensão) é a chave. A busca é tolerante a acentos
e variações simples de plural (`calca` ↔ `calcas`).

### 3. Execute

```bat
python main.py
```

ou use o modo de validação (não chama o ChatGPT; só verifica arquivos e
prompts):

```bat
python main.py --dry-run
```

### 4. Colete os resultados em `output/`

O nome do arquivo de saída preserva o original:

```
output/camisa_001__abc123def.png
```

---

## ⚙️ Configuração (`config.json`)

| Chave | Padrão | Significado |
|---|---|---|
| `input_folder` | `input` | Pasta de entrada (relativo à raiz) |
| `output_folder` | `output` | Pasta de saída |
| `download_folder` | `downloaded` | Onde o download cai inicialmente |
| `failed_folder` | `failed` | Imagens que falharam |
| `processed_folder` | `processed` | Originais já processados |
| `prompts_folder` | `prompts` | Pasta dos prompts |
| `log_file` | `logs/log.txt` | Arquivo de log |
| `max_retries` | `3` | Tentativas por imagem |
| `wait_generation_timeout` | `180` | Timeout (s) para geração |
| `wait_generation_poll` | `2` | Intervalo (s) de checagem |
| `wait_download_timeout` | `120` | Timeout (s) para download |
| `wait_download_poll` | `2` | Intervalo (s) de checagem |
| `processed_action` | `move` | `move` (→ `processed/`) ou `delete` |
| `chatgpt_window_title_substring` | `ChatGPT` | Parte do título da janela |
| `image_extensions` | `[.jpg,.jpeg,.png,.webp,.bmp]` | Extensões aceitas |
| `watcher_enabled` | `false` | (reservado) ativar monitor contínuo |
| `use_clipboard_for_prompt` | `true` | Cola o prompt via clipboard |

Edite e recarregue a qualquer momento com:

```bat
python main.py --reload-config
```

---

## 🧠 Como funciona (resumo)

1. Lista todas as imagens em `input/`.
2. Para cada uma:
   1. Descobre a **categoria** (pasta ou prefixo).
   2. Carrega o **prompt** correspondente.
   3. Garante que o **ChatGPT Desktop** está aberto (lança se preciso).
   4. Clica no botão **Anexar** e seleciona a imagem.
   5. Cola o prompt e envia (`Enter`).
   6. Espera o **botão Stop generating sumir** (= geração concluída).
   7. Clica em **Download** (o app baixa a imagem).
   8. Localiza o arquivo novo em `./downloaded` e move para `./output`.
   9. Move o original para `./processed`.
   10. Em caso de erro, **tenta de novo**; após N tentativas, vai para `./failed`.

---

## 🛠️ Solução de problemas

- **"Janela do ChatGPT não encontrada"**
  - Abra o ChatGPT Desktop manualmente e faça login.
  - Verifique o título da janela (use `chatgpt_window_title_substring`).

- **"Nenhum prompt para categoria X"**
  - Crie `prompts/<categoria>.txt`. Lembre-se: sem acentos (é normalizado).

- **"Timeout aguardando geração"**
  - Aumente `wait_generation_timeout` em `config.json`.

- **"Nenhum arquivo novo em downloaded/"**
  - O ChatGPT pode estar baixando para a pasta padrão do navegador. Ajuste
    o destino de download do Windows para `./downloaded`.

- **pywinauto / uiautomation não encontrados**
  - `pip install -r requirements.txt`. Algumas dependências exigem Windows.

- **Linux/macOS**
  - A automação de UI **não funciona fora do Windows**. Use apenas para
    `--dry-run` ou para desenvolvimento/testes.

---

## 🔮 Melhorias futuras

- Interface gráfica
- Processamento paralelo
- Múltiplas contas
- Múltiplos computadores
- Filas de processamento
- Pausar/Continuar
- Dashboard
- Estatísticas
- Prompt Builder
- OCR automático
- IA para classificar roupa automaticamente

---

## 📜 Filosofia

> Depender o **mínimo possível de coordenadas de tela**. Sempre que possível,
> usar **Windows UI Automation** para que o sistema continue funcionando em
> diferentes resoluções, monitores e versões do ChatGPT Desktop.

---

## 📄 Licença

Uso pessoal / educacional. Adapte conforme sua necessidade.
