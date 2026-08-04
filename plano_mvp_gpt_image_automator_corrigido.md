# Plano Simplificado em Versões — GPT Image Automator

> Baseado no `app.zip` enviado (PySide6 + SQLite + arquitetura em camadas `ui / core / data`) e no `plano_gpt_image_automator.md` original. Este documento **reduz** o plano original para caber em **3 versões incrementais**, cada uma com prompts prontos para serem colados em uma IA local (Claude Code, Cursor, Copilot Chat, etc.) que tenha acesso ao diretório `/app`.
>
> Nenhum arquivo foi alterado — isto é só o roteiro.

---

## 0. O que já existe no `/app` (não recriar, reaproveitar)

A boa notícia: o app atual já tem quase toda a "canalização" que o plano original pedia. O que falta é **trocar o motor de geração** (API OpenAI → automação do ChatGPT Desktop) e **podar** telas/recursos que só existem por causa da API.

| Arquivo | Papel atual | O que fazer com ele |
|---|---|---|
| `app/core/providers/image_generation_provider.py` | Interface abstrata `ImageGenerationProvider` (contrato `generate(request) -> GenerationResult`) | **Não mexer.** É a peça que torna a troca de motor fácil. |
| `app/core/providers/openai_image_generation_provider.py` | Implementação via API OpenAI (`images.edit`) | Fica no lugar (não quebra nada), mas deixa de ser usada. Pode ser removida na V2. |
| `app/core/models/generation.py` | `GenerationRequest`, `GenerationResult`, `GenerationError`, `ErrorCode` | **Reaproveitar 100%.** É o contrato de dados entre UI e motor. |
| `app/core/services/batch_processor.py`, `batch_planner.py`, `batch_splitter.py` | Fila, lotes, pausa/retomada/cancelamento já implementados | **Reaproveitar.** Não recriar lógica de fila. |
| `app/ui/widgets/processing_page.py`, `batch_processing_widget.py`, `folder_scan_widget.py` | Tela de processamento (seleção de pastas, validação, progresso) já existe e já bate com o mockup do plano | **Reaproveitar.** Só trocar o provider injetado. |
| `app/ui/windows/main_window.py` | Monta as abas e decide qual provider instanciar (`_ensure_image_provider`, `_openai_client_factory`) | **Ponto central de troca.** É aqui que a V1 entra. |
| `app/data/storage/credential_manager.py` | Guarda a chave de API da OpenAI | Só necessário enquanto o provider antigo existir. Remover/isolar na V2. |
| `app/data/storage/app_config_store.py` | Key/value simples (`get`, `get_int`, `set`) persistido no SQLite | **Reaproveitar** para guardar config de automação (janela, timeout, tentativas). |
| `app/ui/widgets/dashboard_widget.py` + `app/core/services/dashboard_service.py` | Dashboard geral com métricas agregadas | Candidato a remoção (overengineering, plano original já sinalizava isso). Remover na V2. |
| `app/ui/dialogs/settings_dialog.py` | Diálogo hoje só de chave de API | Vira o diálogo de automação (Detectado / Sessão ativa / Testar conexão) na V2. |
| `app/data/database/migrations/*.sql` | Schema de `project`, `batch`, `image_job`, `generation_attempt` | **Reaproveitar.** Os estados de job podem crescer com novos valores (V2), sem redesenhar tabelas. |

Isso muda a estratégia do plano original: em vez de "reescrever tudo do zero para Windows com automação de acessibilidade", a ideia é **plugar um novo `ImageGenerationProvider`** no encaixe que já existe. É o caminho mais curto para ver uma imagem sendo gerada de verdade.

---

## Ajustes e regras confirmadas antes da implementação

Este roteiro foi revisado para reduzir riscos de implementação. As regras abaixo são parte do plano e devem ser respeitadas pelos prompts das versões.

### 1. A V1 é uma prova de integração, não a automação final

A V1 automatiza o envio da imagem e do prompt, mas o download ainda pode ser manual. Portanto, o critério da V1 é provar que o novo provider funciona dentro da arquitetura atual.

O fluxo da V1 é:

```text
Selecionar imagem
↓
Focar o ChatGPT Desktop
↓
Enviar imagem
↓
Enviar prompt
↓
Aguardar
↓
Usuário clica em baixar
↓
Aplicativo detecta e move o arquivo
```

A automação completa do download fica para a V3.

### 2. Validar o método de upload antes de construir a V1

Antes de implementar `CF_HDROP + Ctrl+V`, testar manualmente no Windows:

1. Copiar uma imagem pelo Explorer;
2. Abrir o ChatGPT Desktop;
3. Colar com `Ctrl+V` no campo da conversa;
4. Confirmar que a imagem aparece como anexo.

Se o ChatGPT Desktop não aceitar esse formato, não insistir no clipboard. Usar o botão de anexar e o seletor nativo de arquivos do Windows.

### 3. Detecção da janela não significa sessão ativa

Na V2, a interface deve mostrar:

```text
● ChatGPT Desktop detectado
○ Sessão será verificada durante o processamento
```

Não mostrar “Sessão ativa” apenas porque a janela foi encontrada. A verificação real de login fica para a V3, por meio da inspeção da árvore de acessibilidade.

### 4. A V3.1 deve ser executada antes da V3.2

Os seletores da interface do ChatGPT Desktop não devem ser inventados.

Ordem obrigatória:

```text
Executar V3.1
↓
Rodar o inspetor no Windows
↓
Salvar a árvore real de controles
↓
Identificar os controles reais
↓
Preencher os seletores
↓
Executar V3.2
```

A implementação da V3.2 deve usar os nomes, AutomationIds e tipos de controle observados na máquina real.

### 5. Comportamento ao detectar limite de uso

Ao detectar limite de uso:

1. Pausar automaticamente a fila;
2. Não marcar todos os itens pendentes como falha;
3. Registrar o motivo no item atual;
4. Manter os demais itens como pendentes;
5. Mostrar uma mensagem clara;
6. Permitir retomada manual posteriormente.

### 6. Regra para arquivos já gerados

Antes de iniciar um job, verificar se o arquivo de saída já existe e é válido.

Comportamento sugerido:

- Resultado válido já existe: não gerar novamente;
- Marcar como concluído ou solicitar confirmação do usuário;
- Não sobrescrever automaticamente.

### 7. Regra de nomenclatura

Exemplo:

```text
foto_001.jpg
→
foto_001_gerada.png
```

Se o destino já existir:

```text
foto_001_gerada_01.png
foto_001_gerada_02.png
```

A regra deve ser centralizada em um único utilitário, não duplicada na UI e no provider.

### 8. Reprocessar somente falhas

O recurso deve reutilizar o mesmo `BatchProcessor`, o mesmo provider e o mesmo fluxo de criação de jobs. Não criar uma fila paralela.

### 9. Execução interna da V2

Executar a V2 em duas etapas:

```text
V2A — Limpeza e integração
- Remover a exigência visível de API key;
- Adaptar Configurações;
- Adicionar pré-checagem;
- Confirmar fila e lotes.

V2B — Recuperação
- Processar somente falhas;
- Persistir estados;
- Melhorar tratamento de erros.
```


## Visão geral das 3 versões

| Versão | Objetivo | Critério de "pronto" |
|---|---|---|
| **V1 — MVP de automação** | Provar que dá pra gerar **1 imagem** automatizando o ChatGPT Desktop, plugado no encaixe de provider que já existe. Fila completa continua rodando (ela já existe), só que agora chamando o motor novo. | Rodar o app, escolher 1 imagem, clicar em Iniciar, e ver o arquivo gerado aparecer na pasta de saída — sem chave de API. |
| **V2 — Fila real + poda de overengineering** | Tirar a dependência de API key da UI, remover o Dashboard, adaptar a tela de Configurações para mostrar status da automação, adicionar pré-checagem antes de iniciar e o botão "Processar somente falhas". | Processar uma pasta com várias imagens em lote, pausar/retomar/cancelar, e reprocessar só as que falharam — tudo sem tocar em API key. |
| **V3 — Robustez** | Trocar a automação "burra" (clipboard + tempo fixo) por localização de elementos via árvore de acessibilidade do Windows, detectar limite de uso/sessão expirada, modo de calibração, relatório exportável. | App aguenta 200+ imagens sem travar em falso-positivo de timeout, e se recupera sozinho de sessão caída. |

Comece pela V1. Só avance para a próxima versão depois que a anterior estiver funcionando de verdade no Windows com o ChatGPT Desktop instalado.

---

## VERSÃO 1 — MVP: gerar 1 imagem via automação do ChatGPT Desktop

### Escopo desta versão

Inclui:
- Um novo provider (`ChatGPTDesktopAutomationProvider`) que implementa o mesmo contrato `ImageGenerationProvider`.
- Automação simples: focar a janela do ChatGPT Desktop → colar imagem via clipboard → colar prompt → Enter → esperar um tempo fixo → observar a pasta de Downloads até aparecer um arquivo novo e estável → mover para a pasta de saída.
- Troca do provider no `main_window.py`, sem exigir chave de API.

Fica de fora (só chega na V2/V3): remoção do Dashboard, remoção da tela de credenciais, pré-checagem, detecção de elementos por acessibilidade, calibração, relatório.

### Dependências novas

```text
pip install pywin32 pygetwindow pyperclip
```

- `pywin32`: colar arquivo de imagem na área de transferência como `CF_HDROP` (o mesmo formato que o Explorer usa ao copiar um arquivo) e focar janelas do Windows.
- `pygetwindow`: localizar/ativar a janela do ChatGPT Desktop pelo título.
- `pyperclip`: colar o texto do prompt na área de transferência.

---

### Prompt 1.1 — Criar utilitários de automação

```
Crie a pasta app/core/automation/ com um arquivo __init__.py vazio e três
módulos novos:

1) app/core/automation/window_control.py
   - Função find_and_focus_window(title_hint: str, timeout_s: float = 10.0) -> bool
     * Usa pygetwindow.getWindowsWithTitle(title_hint) para procurar uma janela
       cujo título contenha title_hint (ex: "ChatGPT").
     * Se encontrar, chama win.activate() (ou win.restore() + win.activate()
       se estiver minimizada) e retorna True.
     * Se não encontrar dentro do timeout, tenta a cada 0.5s e no fim retorna False.
     * Não lança exceção — falhas viram retorno False (quem chama decide o que
       fazer, seguindo o padrão de erro do restante do projeto).

2) app/core/automation/clipboard_utils.py
   - Função copy_file_to_clipboard(path: Path) -> None
     * Usa win32clipboard (do pywin32) para colocar o caminho do arquivo na
       área de transferência no formato CF_HDROP, exatamente como "Copiar"
       de um arquivo no Explorer faz. Isso permite colar (Ctrl+V) o arquivo
       de imagem dentro do campo de anexo do ChatGPT Desktop.
   - Função copy_text_to_clipboard(text: str) -> None
     * Usa pyperclip.copy(text).
   - Função send_paste() -> None e send_enter() -> None
     * Usam pyautogui (adicionar também ao requirements) para simular
       Ctrl+V e Enter na janela focada.

3) app/core/automation/download_watcher.py
   - Função wait_for_new_stable_file(
         folder: Path,
         known_files_before: set[str],
         timeout_s: float,
         poll_interval_s: float = 1.0,
         stable_checks: int = 2,
     ) -> Path | None
     * Faz polling na pasta `folder` até aparecer um arquivo cujo nome NÃO
       estava em known_files_before.
     * Quando aparecer, espera o tamanho do arquivo parar de crescer por
       `stable_checks` leituras seguidas (sinal de que o download terminou),
       antes de retornar o Path.
     * Se estourar o timeout sem achar arquivo novo e estável, retorna None.
   - Função snapshot_files(folder: Path) -> set[str]
     * Retorna o conjunto de nomes de arquivo hoje na pasta (usado como
       "known_files_before" logo antes de disparar o download).

Todas as funções devem ter type hints e docstrings curtas em português,
seguindo o estilo dos arquivos existentes em app/core/services/.
```

### Prompt 1.2 — Criar o `ChatGPTDesktopAutomationProvider`

```
Crie o arquivo app/core/providers/chatgpt_desktop_automation_provider.py.

A classe ChatGPTDesktopAutomationProvider deve herdar de
ImageGenerationProvider (import de app.core.providers.image_generation_provider)
e implementar o método generate(self, request: GenerationRequest) -> GenerationResult,
usando EXATAMENTE os mesmos campos que o OpenAIImageGenerationProvider já usa
(veja app/core/providers/openai_image_generation_provider.py como referência
de estilo, mas NÃO copie a lógica de API — só o padrão de retorno).

Construtor:
    def __init__(
        self,
        window_title_hint: str = "ChatGPT",
        downloads_folder: Path | None = None,
        temp_folder: Path | None = None,
        wait_generation_s: float = 90.0,
        wait_download_s: float = 30.0,
    ) -> None

    - downloads_folder default: Path.home() / "Downloads" (pasta padrão do
      Windows onde o ChatGPT Desktop salva downloads).
    - temp_folder default: Path.home() / "GPT Automator" / "Temporario".

Dentro de generate(request):
    1. Marca started = time.monotonic().
    2. Chama find_and_focus_window(self._window_title_hint, timeout_s=10).
       Se retornar False -> devolve GenerationResult(success=False,
       error=GenerationError(code=ErrorCode.CONNECTION,
       message="Janela do ChatGPT Desktop não encontrada.", retryable=True)).
       (Reaproveitar ErrorCode de app/core/models/generation.py — não criar
       um enum novo nesta versão.)
    3. snapshot = snapshot_files(self._downloads_folder).
    4. copy_file_to_clipboard(request.reference_image_path); send_paste();
       time.sleep(1.5)  # dar tempo da UI processar o anexo
    5. copy_text_to_clipboard(request.prompt_text); send_paste();
       time.sleep(0.5); send_enter()
    6. time.sleep(self._wait_generation_s)  # espera fixa nesta versão —
       será substituída por detecção real na V3.
    7. Aciona o download: nesta versão, o usuário deve baixar manualmente
       clicando no botão de download da imagem gerada dentro do
       ChatGPT Desktop (documentar isso claramente no README/log). O
       provider só ESPERA o arquivo aparecer:
       downloaded = wait_for_new_stable_file(self._downloads_folder,
       snapshot, timeout_s=self._wait_download_s).
    8. Se downloaded is None -> devolve GenerationResult(success=False,
       error=GenerationError(code=ErrorCode.TIMEOUT,
       message="Nenhum arquivo novo detectado na pasta de Downloads.",
       retryable=True)).
    9. Move/renomeia downloaded para request.output_path (usar
       shutil.move, criando os diretórios do destino com
       output_path.parent.mkdir(parents=True, exist_ok=True) antes).

       Antes de mover, verificar se request.output_path já existe e é um
       arquivo válido. Não sobrescrever automaticamente. Usar a regra
       centralizada de nomes:
           foto_001.jpg -> foto_001_gerada.png
           conflito -> foto_001_gerada_01.png, _02.png, etc.
   10. Retorna GenerationResult(success=True, output_path=request.output_path,
       model_used="chatgpt-desktop", duration_ms=int((time.monotonic()-started)*1000),
       bytes_written=request.output_path.stat().st_size, attempts=1).

Envolva os passos 2–9 num try/except Exception amplo que devolve
GenerationResult(success=False, error=GenerationError(code=ErrorCode.UNKNOWN,
message=str(exc))) — para nunca deixar uma exceção subir e derrubar a fila
(mesma garantia que o provider da OpenAI já oferece).

Implemente também close(self) -> None como no-op (pass), só para manter a
interface.
```

> **Por que a etapa 7 pede clique manual no download?** Porque clicar no botão de download dentro do ChatGPT Desktop exige localizar um elemento visual específico — isso é exatamente o tipo de automação frágil que o plano original queria evitar fazer por coordenadas fixas. Na V1, resolvemos isso deixando o clique manual e automatizando só a parte fácil (montar a mensagem e detectar o arquivo baixado). Na V3 isso é substituído por automação real via árvore de acessibilidade.

### Prompt 1.3 — Trocar o provider usado pelo `MainWindow`

```
Abra app/ui/windows/main_window.py.

Localize o método _ensure_image_provider(self). Ele hoje faz:
    if self._image_provider is not None:
        return self._image_provider
    if not self._cred.has_key():
        return None
    from app.core.providers import OpenAIImageGenerationProvider
    self._image_provider = OpenAIImageGenerationProvider(
        client_factory=self._openai_client_factory,
    )
    ...
    return self._image_provider

Substitua o CORPO deste método por:
    if self._image_provider is not None:
        return self._image_provider
    from app.core.providers.chatgpt_desktop_automation_provider import (
        ChatGPTDesktopAutomationProvider,
    )
    self._image_provider = ChatGPTDesktopAutomationProvider()
    logger.info("ChatGPTDesktopAutomationProvider instanciado e pronto")
    return self._image_provider

Não remova o método _openai_client_factory ainda (deixe-o intocado, mesmo
sem uso — será removido só na V2, junto com o resto da credencial).

Em seguida, procure em todo o arquivo por chamadas a self._cred.has_key()
que bloqueiam algum fluxo de UI (ex.: desabilitar botão "Iniciar" na
ProcessingPage por falta de chave). Se existir alguma checagem desse tipo
em processing_page.py ou batch_processing_widget.py que impede iniciar o
processamento sem chave, comente-a com um TODO:
    # TODO(V1): checagem de API key desativada — motor agora é automação local
mas NÃO apague o código ainda (evita quebrar outras partes que dependem do
mesmo método). A remoção definitiva do CredentialManager acontece na V2.
```

### Prompt 1.4 — Adicionar dependências no projeto

```
Abra o arquivo de dependências do projeto (requirements.txt ou
pyproject.toml — use o que já existir no repositório) e adicione:
    pywin32
    pygetwindow
    pyperclip
    pyautogui

Não remova nenhuma dependência existente (openai continua necessária até
a V2, já que o provider antigo ainda está no código).
```

### Critério de aceite da V1

1. Rodar o app no Windows com o ChatGPT Desktop aberto e logado.
2. Ir na aba Processamento, selecionar uma pasta com 1 imagem, escolher a pasta de saída, digitar um prompt.
3. Clicar em "Validar imagens" e depois em "Iniciar processamento".
4. O app deve: focar a janela do ChatGPT, colar a imagem, colar o prompt, apertar Enter.
5. Usuário clica manualmente em "baixar" quando a imagem terminar de gerar dentro do ChatGPT.
6. O app detecta o arquivo baixado e move para a pasta de saída, marcando o job como concluído.

Se isso funcionar uma vez, a integração do provider está validada. A V1 ainda não é a automação completa: o download manual será substituído por automação real na V3.

---

## VERSÃO 2 — Fila real sem overengineering

### Escopo desta versão

- Remover a exigência de API key da interface (ela deixa de fazer sentido).
- Remover a aba "Status Geral" (Dashboard) — like o plano original já sinalizava, é complexidade que não participa do fluxo `Selecionar → Validar → Organizar → Processar → Salvar`.
- Adaptar a tela de Configurações para mostrar status da automação (Detectado / Sessão ativa / Testar conexão) em vez de campo de chave de API.
- Guardar as configs de automação (timeout, tentativas, pasta temporária, título da janela) usando o `AppConfigStore` que já existe.
- Adicionar pré-checagem antes de iniciar o processamento.
- Garantir que o botão "Processar somente falhas" (se já existir na base) esteja de fato ligado ao novo provider.

### Prompt 2.1 — Remover a aba "Status Geral" (Dashboard)

```
Abra app/ui/windows/main_window.py.

1. No construtor __init__, remova a linha:
    self._tabs.addTab(
        _build_placeholder_tab("Status Geral", "Carregando dashboard…"),
        "Status Geral",
    )

2. Remova o atributo self._dashboard_widget do __init__ e o método
   _ensure_dashboard(self).

3. No método _on_tab_changed, remova o bloco inteiro que trata
   self._tabs.tabText(index) == "Status Geral".

4. Remova o método _on_batch_activated e a linha que conecta
   self._dashboard_widget.batch_double_clicked (não existe mais o widget).

5. Remova os imports não usados: DashboardWidget (de app.ui.widgets) e
   qualquer import de DashboardService que só era usado dentro dos métodos
   removidos.

Não delete os arquivos app/ui/widgets/dashboard_widget.py e
app/core/services/dashboard_service.py do disco — só pare de referenciá-los
no main_window.py. Isso evita quebrar testes que ainda possam importá-los
diretamente. (Se quiser, pode apagá-los de verdade numa limpeza futura,
depois de confirmar que nada mais importa esses módulos.)
```

### Prompt 2.2 — Simplificar a aba Configuração (tirar API key, colocar status de automação)

```
Abra app/ui/windows/main_window.py, método _build_config_tab(self).

Troque o botão:
    open_btn = QPushButton("🔑  Configurar chave da OpenAI…")
    open_btn.clicked.connect(self._open_credentials_dialog)

por:
    open_btn = QPushButton("🖥️  Configurar automação do ChatGPT Desktop…")
    open_btn.clicked.connect(self._open_automation_settings_dialog)

Crie um novo arquivo app/ui/dialogs/automation_settings_dialog.py, no MESMO
padrão de app/ui/dialogs/settings_dialog.py (reaproveite a estrutura de
classe, layout e botões — troque só o conteúdo). O novo diálogo deve ter:

  - Um QLabel "Status:" seguido de dois indicadores de texto:
      "● ChatGPT Desktop detectado" / "● ChatGPT Desktop não detectado" (chama
      find_and_focus_window("ChatGPT", timeout_s=2) de
      app.core.automation.window_control para decidir qual mostrar,
      SEM focar de verdade — crie uma variante is_window_present(title_hint)
      -> bool no mesmo módulo, que só verifica pygetwindow.getWindowsWithTitle
      sem chamar activate()).
  - Um botão "Testar conexão" que chama find_and_focus_window de verdade e
    mostra uma QMessageBox com sucesso ou falha.
  - Um QSpinBox "Tempo máximo por imagem (segundos)" ligado a
    AppConfigStore.get_int("automation_wait_generation_s", default=90) /
    .set(...).
  - Um QSpinBox "Tentativas" ligado a
    AppConfigStore.get_int("automation_max_retries", default=2).
  - Um QLineEdit + botão "Selecionar" para "Pasta temporária", ligado a
    AppConfigStore.get("automation_temp_folder", default=str(Path.home() /
    "GPT Automator" / "Temporario")).

No main_window.py, crie o método _open_automation_settings_dialog(self)
espelhando _open_credentials_dialog, mas abrindo o novo diálogo. Ele deve
usar self._ensure_db() para obter a instância de AppConfigStore (crie o
mesmo padrão lazy que existe para _preset_store, com um novo atributo
self._app_config_store).

DEIXE os métodos _open_credentials_dialog, _openai_client_factory e o
diálogo antigo settings_dialog.py no lugar, mas remova o botão que os
chamava (já feito acima) — eles ficam "mortos" no código até uma limpeza
futura, pra não arriscar quebrar import de outro lugar.
```

### Prompt 2.3 — Fazer o `ChatGPTDesktopAutomationProvider` ler as configs salvas

```
Abra app/core/providers/chatgpt_desktop_automation_provider.py.

Altere o construtor para aceitar opcionalmente um AppConfigStore:
    def __init__(
        self,
        config_store: AppConfigStore | None = None,
        window_title_hint: str = "ChatGPT",
        downloads_folder: Path | None = None,
    ) -> None:
        ...
        if config_store is not None:
            self._wait_generation_s = float(
                config_store.get_int("automation_wait_generation_s", default=90)
            )
            self._max_retries = config_store.get_int("automation_max_retries", default=2)
            temp = config_store.get("automation_temp_folder")
            self._temp_folder = Path(temp) if temp else (Path.home() / "GPT Automator" / "Temporario")
        else:
            self._wait_generation_s = 90.0
            self._max_retries = 2
            self._temp_folder = Path.home() / "GPT Automator" / "Temporario"

Em app/ui/windows/main_window.py, no método _ensure_image_provider,
passe self._ensure_app_config_store() (o método lazy criado no Prompt 2.2)
como config_store ao instanciar ChatGPTDesktopAutomationProvider.
```

### Prompt 2.4 — Pré-checagem antes de iniciar o processamento

```
Abra app/ui/widgets/processing_page.py e localize onde o botão "Iniciar
processamento" dispara o início da fila (procure pelo texto
"INICIAR PROCESSAMENTO" ou o handler conectado a esse botão).

Antes de iniciar a fila de verdade, adicione uma checagem síncrona rápida
que roda estas verificações, na ordem, parando na primeira que falhar e
mostrando uma QMessageBox explicando qual item falhou:

  1. Pasta de entrada acessível (os.access(input_folder, os.R_OK)).
  2. Pasta de saída acessível ou criável
     (output_folder.mkdir(parents=True, exist_ok=True) dentro de um try).
  3. Existe pelo menos 1 imagem válida (reaproveitar o resultado que a
     validação já calculou, não rodar de novo).
  4. Prompt preenchido (texto não vazio após strip()).
  5. Janela do ChatGPT Desktop detectável — chamar
     app.core.automation.window_control.is_window_present("ChatGPT")
     (criada no Prompt 2.2). Se False, mostrar mensagem clara: "Abra o
     ChatGPT Desktop e faça login antes de iniciar."
  6. Espaço em disco disponível na pasta de saída acima de um mínimo
     (ex.: 200 MB) usando shutil.disk_usage(output_folder).

Se todas passarem, segue o fluxo normal de iniciar o BatchProcessor que já
existe. Não recrie a lógica de fila — só adicione este "portão" antes dela.
```

### Prompt 2.5 — Confirmar que "Processar somente falhas" está ligado ao provider novo

```
Procure em app/core/services/batch_processor.py e
app/ui/widgets/batch_processing_widget.py por qualquer método/botão
relacionado a reprocessar falhas (buscar por "falha", "failed" ou "retry"
no texto do arquivo).

Se existir, apenas confirme que ele reusa self._provider (o mesmo atributo
que já recebe o provider via set_provider) e não instancia nenhum provider
próprio. Se NÃO existir ainda, crie:

  - Em app/data/repositories/image_job_repository.py: um método
    list_failed_by_batch(batch_id: str) -> list[ImageJob] que filtra os
    jobs com status de falha daquele lote (reaproveite os nomes de status
    já usados no schema em app/data/database/migrations/).
  - Em batch_processing_widget.py: um botão "Processar somente falhas" que
    chama esse método e alimenta o mesmo fluxo de set_jobs(jobs,
    batch_id=...) que o main_window.py já usa no _on_batch_activated
    (adapte esse trecho para cá, já que o Dashboard foi removido).
```

### Critério de aceite da V2

1. Não existe mais nenhuma tela pedindo chave de API.
2. O app abre com só 2 abas: "Configuração" e "Processamento".
3. A tela de Configuração mostra se o ChatGPT Desktop foi detectado, e permite testar a conexão.
4. Rodar um lote de 10+ imagens, pausar, retomar, cancelar — tudo continua funcionando como antes (a lógica de fila não mudou, só o motor por baixo).
5. Se 2-3 imagens falharem por timeout, dá pra reprocessar só essas com um clique.

---

## VERSÃO 3 — Robustez: automação por acessibilidade, calibração e resiliência

### Escopo desta versão

Esta é a versão que resolve as fragilidades deixadas de propósito na V1/V2: espera fixa, download manual, e nenhuma detecção de erro específico do ChatGPT (limite de uso, sessão caída).

### Prompt 3.1 — Trocar espera fixa por detecção real de fim de geração

```
Adicione a dependência uiautomation (pip install uiautomation) ao projeto.

Crie app/core/automation/ui_elements.py com funções que usam a biblioteca
uiautomation para navegar a árvore de acessibilidade da janela do ChatGPT
Desktop (já focada por find_and_focus_window):

  - find_generation_in_progress_indicator(window) -> bool
    Procura, dentro da janela, um elemento cujo AutomationId, Name ou
    ControlType indique que uma geração está em andamento (ex.: um botão
    "Parar geração" costuma existir enquanto a resposta está sendo
    produzida — o nome exato do controle deve ser DESCOBERTO rodando um
    script de inspeção local, veja abaixo. Não adivinhar nomes).
  - find_download_button_for_last_image(window) -> uiautomation.Control | None
    Procura o botão/ícone de download associado à última imagem gerada na
    conversa (geralmente um Button dentro do último "ListItem"/mensagem do
    assistente).

IMPORTANTE: como não há acesso a uma máquina Windows com o ChatGPT Desktop
neste ambiente de desenvolvimento, os nomes exatos de AutomationId/Name/
ControlType usados nestas funções NÃO podem ser garantidos de antemão.
Antes de implementar find_download_button_for_last_image, crie um script
auxiliar app/core/automation/_inspect_tree.py que, ao ser rodado
manualmente com `python -m app.core.automation._inspect_tree`, imprime a
árvore de controles da janela focada (usando
uiautomation.GetRootControl() e um percurso recursivo limitado a ~4
níveis, imprimindo ControlType, Name e AutomationId de cada nó). Rode esse
script com o ChatGPT Desktop aberto, uma imagem gerada na tela, para
identificar os valores reais antes de preencher as funções acima.
```

### Prompt 3.2 — Substituir a espera fixa e o download manual no provider

```
Abra app/core/providers/chatgpt_desktop_automation_provider.py.

Troque o passo "time.sleep(self._wait_generation_s)" por um polling que
chama find_generation_in_progress_indicator(window) a cada 1s, até ela
retornar False (geração terminou) ou até estourar
self._wait_generation_s — nesse caso, devolve GenerationResult(success=False,
error=GenerationError(code=ErrorCode.TIMEOUT, message="Tempo esgotado
aguardando a geração.", retryable=True)).

Troque o passo de download manual: depois que a geração terminar, chame
find_download_button_for_last_image(window). Se encontrar, dispare um
clique automatizado nesse controle (control.Click() da própria biblioteca
uiautomation) em vez de pedir para o usuário clicar. Se não encontrar em
até 5 tentativas com 1s de intervalo, devolve GenerationResult(success=False,
error=GenerationError(code=ErrorCode.UNKNOWN, message="Botão de download
não encontrado.", retryable=True)).

Mantenha wait_for_new_stable_file como está (continua útil para confirmar
que o arquivo baixado terminou de gravar em disco).
```

### Prompt 3.3 — Detectar limite de uso / sessão expirada

```
Em app/core/automation/ui_elements.py, adicione:
    find_usage_limit_message(window) -> str | None
    find_login_required_message(window) -> bool

Ambas procuram, na árvore de acessibilidade, textos que indiquem "limite
atingido" / "faça login novamente" (os textos reais em português/inglês
devem ser conferidos ao vivo com o script de inspeção do Prompt 3.1 — o
ChatGPT Desktop pode mostrar mensagens diferentes dependendo do idioma da
conta).

No ChatGPTDesktopAutomationProvider.generate(), logo depois de focar a
janela (passo 2 do Prompt 1.2), chame essas duas funções:
  - Se find_login_required_message retornar True -> devolve
    GenerationResult(success=False, error=GenerationError(
    code=ErrorCode.AUTH, message="Sessão do ChatGPT Desktop expirada — faça
    login novamente.", retryable=False)).
  - Se find_usage_limit_message retornar um texto não vazio -> devolve
    GenerationResult(success=False, error=GenerationError(
    code=ErrorCode.QUOTA_EXCEEDED, message=texto_encontrado,
    retryable=False)).

Além disso, o chamador da fila deve tratar QUOTA_EXCEEDED como condição de
PAUSA GLOBAL: não marcar os jobs pendentes como falha, manter os itens ainda
não iniciados como pendentes e permitir retomada manual depois.

Esses dois ErrorCode (AUTH, QUOTA_EXCEEDED) já existem em
app/core/models/generation.py — não é preciso criar nada novo, só usá-los
com o significado certo para o contexto de automação local.
```

### Prompt 3.4 — Modo de calibração

```
Na tela criada no Prompt 2.2 (automation_settings_dialog.py), adicione um
botão "Modo de calibração". Ao clicar, ele deve:
  1. Focar a janela do ChatGPT Desktop.
  2. Rodar o mesmo percurso de árvore do script _inspect_tree.py (Prompt
     3.1), mas mostrando o resultado numa QTextEdit somente-leitura dentro
     de um novo diálogo, em vez de print no console.
  3. Ter um botão "Copiar" que joga o texto pra área de transferência, para
     o usuário colar num chamado de suporte se algo mudar na interface do
     ChatGPT Desktop (ex.: depois de uma atualização do app) e os seletores
     precisarem ser atualizados.

Esse modo não precisa ALTERAR nenhum seletor automaticamente nesta versão
— o objetivo é só dar visibilidade rápida quando a automação parar de
funcionar por causa de uma mudança de layout no ChatGPT Desktop.
```

### Prompt 3.5 — Relatório exportável e "abrir pasta de saída"

```
Em app/ui/widgets/batch_processing_widget.py, ao final de um lote (quando
o BatchProcessor sinalizar que a fila terminou), garanta que existam dois
botões:

  - "Abrir pasta de saída": chama app.ui.paths.open_path_in_shell(output_folder)
    (já existe e é usado em _open_logs_folder no main_window.py — reaproveitar
    a mesma função).
  - "Exportar relatório": gera um .txt ou .csv simples listando, por job,
    arquivo de entrada, status final, tentativas e arquivo de saída (se
    houver). Reaproveite a leitura via ImageJobRepository.list_by_batch
    (já existe) — não crie uma nova fonte de dados, só formate o que já
    está no banco.

Use QFileDialog.getSaveFileName como já é feito em _export_diagnostic no
main_window.py, para manter o mesmo padrão de UX de salvar arquivo.
```

### Critério de aceite da V3

1. O app espera o tempo real da geração (não um valor fixo) e clica sozinho no botão de download.
2. Se a sessão do ChatGPT cair no meio de um lote, o app marca os jobs seguintes com um erro claro (`AUTH`) em vez de ficar tentando por timeout.
3. Se o plano do ChatGPT bater no limite de uso, o app para de tentar aquele lote e explica o motivo em vez de reprocessar em loop.
4. É possível gerar um relatório e abrir a pasta de saída direto da tela de progresso.
5. Existe um jeito rápido (modo de calibração) de descobrir por que a automação parou de funcionar, sem precisar debugar o código na mão.

---

## Ordem de execução recomendada

```
V1 → testar manualmente no Windows com 1 imagem
   ↓ (só avança se funcionar)
V2 → testar manualmente com lote de 10+ imagens, pausa/retomada, falhas
   ↓ (só avança se funcionar)
V3 → testar com lote grande (100+) e cenários de sessão caída / limite de uso
```

Cada versão é um estado do app que **funciona sozinho** — não é preciso terminar a V3 para ter algo utilizável; a V1 já entrega o núcleo do valor (gerar imagens sem precisar de API paga), e V2/V3 só reduzem atrito e aumentam confiabilidade.
