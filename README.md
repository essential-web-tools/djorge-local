# Djorge Local — Origo e Flash no seu computador

Roda o **Origo** (catalogação, julgamento e decisão) e o **Flash** (segundo modelo, rápido)
localmente. Lê eventos de notificações, e-mail, pastas, área de transferência e de **qualquer programa** (pela
API local), converte imagens, PDFs, documentos e áudio em texto, e percorre **árvores de decisões
subsequentes**: cada resposta escolhe as próximas decisões ou ações. Os modelos **não escrevem texto**: decidem,
e o programa executa as ferramentas (relógio, valores, quantidades, documentos) e as ações que você configurar.

## Início rápido
1. Windows: `iniciar.bat` · Linux/macOS: `./iniciar.sh` (cria o ambiente, instala e abre o painel).
2. Na primeira vez é criado `~/.djorge-local/config.yaml` e `arvores.yaml`. Ajuste e reinicie.
3. Abra o endereço impresso: `http://127.0.0.1:8765/?token=...` (token em `~/.djorge-local/token.txt`).

## API local completa
Cada modelo carregado ganha a API inteira do DJORGE, só nesta máquina (padrão: Origo em `:8750`, Flash em
`:8751`), com `Authorization: Bearer <token do painel>`:

| rota | o que faz |
|---|---|
| `POST /v1/classify` | classificar em rótulos (`multi`, `certainty`, `calibration: "context"`, `prediction_set`) |
| `POST /v1/verify` · `/v1/score` · `/v1/select` | verdadeiro/falso · escala · seleção direta |
| `POST /v1/aggregate` | quantidades por significado (AGG/AGID) |
| `POST /v1/tag` | trechos/entidades com posições, polaridade e quantidade |
| `POST /v1/decide` | regras compostas → ações sugeridas (nunca executadas) |
| `POST /v1/document` | documentos longos: hierarquia, entidades, busca e resposta com trecho |
| `POST /v1/tools/{pii,mask,lang,validate,math,time}` | ferramentas exatas (CPF/CNPJ, contas, relógio...) |
| `POST /execute` | qualquer consulta da linguagem (envie o texto em `sources`, `now` opcional) |
| `GET /capabilities` · `/health` · `/model` | o que o modelo sabe fazer e estado |
| `/contexts/<cliente>/...` | aliases, definições de classe, entidades, limiares por cliente |

```bash
curl -s http://127.0.0.1:8750/v1/classify -H "Authorization: Bearer SEU_TOKEN" -H "Content-Type: application/json" \
     -d '{"text":"O boleto vence amanhã","labels":["cobrança","entrega","outro"]}'
```
Esta versão **não inclui o Sentinel** (sem `/v1/sentinel` e sem as análises de URL/e-mail/IP).

## Modelo
O **Origo Flash** vem dentro do instalador e carrega sozinho: não há download, chave ou configuração.
Para usar outro modelo (o Origo completo, por exemplo), abra a aba **Modelos → Adicionar outro modelo** e aponte
a pasta dele. A fonte do Flash usada no build fica em `modelo-fonte.txt`.

## Conectores (todos desligados, menos a API local)
| conector | o que lê | requisitos |
|---|---|---|
| `http_inbox` | qualquer programa: `POST /api/event {"text","title","app"}` com `X-Token` | — |
| `notifications` | notificações do sistema | Windows: `winsdk` + permissão em Privacidade › Notificações; Linux: `dbus-monitor`; macOS: Acesso Total ao Disco (leitura só do banco de notificações) |
| `folder` | arquivos novos/alterados | PDF `pypdf`, DOCX `python-docx`, imagem `pytesseract`, áudio `faster-whisper` |
| `clipboard` | texto copiado | `pyperclip` |
| `imap` | e-mails não lidos (somente leitura, continuam não lidos) | senha numa variável de ambiente |
| `command` | saída de um programa que você lista | — |
| `stdin` | texto digitado no terminal | — |

Exemplo, de qualquer linguagem:
```bash
curl -X POST http://127.0.0.1:8765/api/event -H "X-Token: SEU_TOKEN" -H "Content-Type: application/json" \
     -d '{"text":"Seu boleto vence 20/10","title":"Banco","app":"meu-sistema","sync":true}'
```
`sync: true` devolve na hora o caminho percorrido; sem ele, o evento entra na fila.
`POST /api/decide {"model":"origo","query":"CHOICE [...] FROM text","text":"..."}` executa uma consulta direta.

## Árvores de decisão (`arvores.yaml`)
Tipos de nó: `choice`, `check` (NOUL), `score`, `multi` (SET: segue todos os rótulos marcados), `select`
(qualquer seletor: `TOTAL AGID limão`, `TOTAL TYPE money`, `COUNT ENTITY Pessoa`...), `query` (DSL),
`if` (regras sem modelo) e `action`
(`notify`, `log`, `write_file`, `webhook`, `command`, `stop`).
`branches` escolhe o próximo nó pela resposta (`true/false`, rótulo, `>= 10`, `abstain`, `default`);
uma lista roda várias decisões seguintes. `min_probability` manda para `abstain` quando a certeza é baixa.
Placeholders: `{text}`, `{title}`, `{app}`, `{source}`, `{sender}`, `{time}`, `{no.answer}`, `{no.value}`,
`{no.probability}`; `{text:80}` corta. Prazos com data no texto são decididos pelo relógio local.

## Segurança das ações
* `command`, `webhook` e `write_file` esperam **aprovação no painel** (aba Confirmações), salvo `auto: true`.
* `command` só roda programas em `actions.command_allow`, sem shell; `webhook` só para URLs em
  `actions.webhook_allow`; `write_file` só dentro de `actions.write_dir`.
* O painel só escuta em 127.0.0.1 e exige o token; o histórico mascara CPF, cartão, e-mail e telefone.

## Comandos
`init` · `run` · `seal` · `info` · `download` · `test "texto" [--tree nome]` · `validate`
Executável opcional: `pip install pyinstaller && python build_exe.py`.

## Executável (início rápido, ícone próprio)
* **Windows:** `build_windows.bat` → `dist\Djorge Local\Djorge Local.exe` e, com o Inno Setup 6 instalado,
  `Output\DjorgeLocal-Setup-0.7.3.exe` (instalador com atalho no Menu Iniciar, área de trabalho e opção
  "Iniciar com o Windows").
* **macOS / Linux:** `./build_unix.sh` → `Djorge Local.app` / `dist/Djorge Local/`.
* **Sem montar nada no seu PC:** suba esta pasta num repositório do GitHub e rode a ação **build**
  (aba Actions → build → Run workflow). Ela gera o instalador do Windows, o app do macOS e o pacote Linux.

Abre numa **janela própria** (WebView2 no Windows, WebKit no macOS, Qt no Linux), sem navegador; a aba
**Modelos** escolhe a pasta ou o `.djx` de cada modelo, liga, carrega, descarrega e recebe a chave.
Por que abre rápido: modo *onedir* (nada é descompactado a cada início), ícone na bandeja e painel aparecem
antes dos modelos; o PyTorch e os modelos carregam em segundo plano (o painel mostra "carregando" → "pronto").
Abrir de novo só traz o painel da instância que já está rodando. A chave dos modelos é pedida numa janela e
pode ficar guardada no cofre do sistema (Credential Manager / Keychain / Secret Service).
Menu da bandeja: Abrir painel · Pausar conectores · Recarregar árvores · Abrir pasta de dados · Sair.

## Sistemas
| | Windows 10/11 | macOS 12+ (Intel e Apple) | Linux (X11/Wayland) |
|---|---|---|---|
| executável | `Djorge Local.exe` + instalador | `Djorge Local.app` | `Djorge Local` (pasta) |
| bandeja | sim | barra de menus | sim (AppIndicator) |
| notificações | sim (permissão do Windows) | sim (Acesso Total ao Disco) | sim (D-Bus) |
| cofre da chave | Gerenciador de Credenciais | Keychain | Secret Service |
| GPU | CUDA (opcional) | CPU | CUDA (opcional) |
Sem interface gráfica (servidor), o programa roda igual, só sem a bandeja.

## Segurança e privacidade
* **Antivírus:** executável em pasta (sem UPX, sem autoextração), bootloader compilado no build e informações
  de versão. Assinatura digital opcional (segredos `WIN_CERT_PFX_BASE64` e `WIN_CERT_PASSWORD`).
* **Privacidade:** tudo roda neste computador; painel e API só em 127.0.0.1 com token automático; histórico com
  dados pessoais mascarados; comandos e webhooks só das listas permitidas e com confirmação.
