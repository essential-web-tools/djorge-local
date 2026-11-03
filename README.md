# Djorge v2 — assistente local para Windows

Organiza tarefas, agenda, lembretes e mensagens no seu computador. O modelo **DOFL** (Djorge Origo Flash
Local) só decide — escolhe a ferramenta, os campos, se pode agir —; nunca escreve texto. Tudo roda em
`127.0.0.1`. Requer **Windows 10 ou 11 (64 bits)**.

## Instalar
1. Rode `Djorge-Setup-2.0.0.exe` (não pede administrador; instala em `%LOCALAPPDATA%\Programs\Djorge`).
2. Se faltar o componente de janela (Microsoft Edge WebView2), o instalador o instala.
3. O instalador é pequeno porque **não traz o modelo**.

## Primeira abertura
- A janela abre em cerca de 1 s, já sem o modelo. As regras (datas, horas, valores, e-mails) já funcionam.
- No **canto superior direito** fica o **card do modelo**: clique em **Baixar DOFL V2** (~305 MB). O progresso
  aparece no card; dá para cancelar e continuar depois. Ao terminar, o modelo carrega sozinho.
- O modelo fica em `%LOCALAPPDATA%\Djorge\modelos\flash`; logs em `%LOCALAPPDATA%\Djorge\logs`.
- Fechar a janela deixa o Djorge na **bandeja** (Abrir · Pausar/Retomar Djorge · Liberar memória · Sair): as
  notificações continuam chegando. **Sair** encerra o processo de verdade. Abrir de novo só traz a janela de volta.

## Modo leve
- O DOFL roda em **ONNX Runtime, int8 na CPU** (sem PyTorch: o instalador não traz o PyTorch).
- Inicia com o Windows (padrão ligado, desmarcável no instalador) **minimizado na bandeja**.
- Ocioso, o processo baixa a própria prioridade; o modelo é descarregado após 15 min sem uso (configurável) e
  volta sozinho no próximo pedido. **Pausar Djorge** para conectores, integrações e secretário e tira o modelo
  da memória; **Liberar memória** descarrega o modelo e devolve a memória ao Windows.
- Configuração em `%LOCALAPPDATA%\Djorge\leveza.json` (`descarregar_apos_min`, `iniciar_com_windows`, ...).

## Integrações (resumo)
Configure em **Integrações**. Segredos ficam no Gerenciador de Credenciais do Windows.
- **Google** (Gmail, Agenda, Tarefas) e **Microsoft** (Outlook, Calendário, To Do): login OAuth no navegador.
- **WhatsApp** (Cloud API oficial): token + phone number id; só envio.
- **Gemini** (opcional): só para rascunhar o texto de uma mensagem quando você pedir — esse texto sai do computador.
Toda mensagem é mostrada para confirmação antes de enviar.

## Voz
Botão de microfone no campo de entrada: ditado pelo reconhecimento do Windows (ou faster-whisper, se instalado)
e respostas faladas pela voz do Windows (SAPI). Ligue/desligue em Preferências.

## Atalhos
| tecla | ação |
|---|---|
| `Ctrl+K` | foco no campo de comando |
| `Enter` / `Shift+Enter` | enviar / nova linha |
| `Ctrl+Enter` | confirmar a proposta |
| `Esc` | cancelar / fechar |
| `Ctrl+1..4` | Início · Kanban · Agenda · Lista |

## Para desenvolvedores
- Rodar do código: `iniciar.bat`.
- Gerar o instalador: `build_windows.bat` (Python 3.11 x64 + Inno Setup 6; coloque
  `assets\MicrosoftEdgeWebview2Setup.exe`). Saída: `dist\Djorge\` e `Output\Djorge-Setup-*.exe`.
- Fonte do modelo: `modelo-fonte.txt` ou `model_source` no `config.yaml`
  (`gdrive:`, `gdrive-folder:`, `hf:` ou URL `https://` de um `.zip`).
- Testes: `python -m pytest tests`.

### Publicar o pacote ONNX do modelo (obrigatório para o instalador sem PyTorch)
O `.zip` publicado deve ser o **pacote Local** (`onnx/encoder.int8.onnx`, `local_profiles.json`,
`decision/`, `calibration/`, `tokenizer/`, `metadata/`), não a pasta de treino (`DOFL_FINAL`, com
`backbone/model.safetensors` e `adapter/`). Com o pacote certo o app não converte nada e não precisa de PyTorch.
1. No repositório de treino (com PyTorch, `onnx`, `onnxscript`, `onnxruntime`):
   `python tools/export_local.py --artifact EXP/DOFL_FINAL --out /content/flash_local --check dados_dev.jsonl`
   — o script só aceita o pacote se o ONNX concordar com o PyTorch em ≥ 99 % (pacote **CANDIDATO**).
2. Compacte o **conteúdo** da pasta (`metadata/djorge.json` precisa existir dentro do zip, em qualquer nível):
   `cd /content/flash_local && zip -r ../dofl_v2_onnx.zip .`
3. Publique (Google Drive "qualquer pessoa com o link", Hugging Face ou URL `https://`) e troque a linha
   `flash=` de `modelo-fonte.txt` (ou `model_source` no `config.yaml`). Quem já baixou o pacote de treino:
   remova o modelo no card e baixe de novo.

Se o app encontrar um artefato de treino na pasta que ele mesmo baixou, ele tenta **otimizar uma vez em segundo
plano** ("Otimizando o modelo para este computador…", `djorge_local/otimizar.py`: mesma receita do
`export_local.py`, com teste de paridade). Isso só funciona onde há PyTorch + `onnx` + `onnxscript` (rodando do
código); no app instalado o card avisa que é preciso o pacote ONNX. Pastas escolhidas à mão nunca são trocadas.
