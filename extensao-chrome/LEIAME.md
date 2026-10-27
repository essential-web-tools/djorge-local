# Djorge Sentinel — Guarda (extensão do Chrome)

Avisa, desfoca, oculta ou bloqueia golpes, phishing e links perigosos **só nos sites que você escolher**, usando o
seu servidor Sentinel. Leve: nada roda nos outros sites, cada texto é verificado uma vez (cache), no máximo
3 consultas ao mesmo tempo, e a página não trava (lotes em segundo plano).

## Instalar
1. Abra `chrome://extensions`, ligue **Modo do desenvolvedor**.
2. **Carregar sem compactação** → escolha a pasta `extensao-chrome`.
3. A página de configurações abre sozinha: informe o endereço do Sentinel (https) e a chave da API.

## Como funciona
| o quê | como |
|---|---|
| **Mensagens** | WhatsApp Web, Gmail, Outlook, Telegram, Instagram, Messenger, Facebook, LinkedIn, X e Discord já vêm prontos; outros sites aceitam um seletor CSS |
| **Links** | cada domínio é verificado uma vez; links perigosos ficam sublinhados e pedem confirmação ao clicar |
| **Notificações** | as notificações que o site cria enquanto está aberto esperam até 1,5 s pelo Sentinel; perigosas podem ser escondidas |
| **O próprio site** | se o endereço parecer golpe, aparece um aviso em tela cheia |
| **Qualquer página** | clique direito num texto selecionado ou num link → *Verificar com o Sentinel* |

Ações por site: **alertar** (selo ao lado), **desfocar** (clique para ver) ou **ocultar** (clique para mostrar).

## Privacidade
* Só os sites ligados por você são lidos, e o Chrome pede permissão para cada um.
* Os textos vão apenas para o servidor Sentinel configurado (https). CPF, cartão, e-mail e telefone são
  mascarados antes de sair do navegador (pode desligar).
* Configurações e cache ficam só neste navegador.

## Limites
* Notificações enviadas pelo site com a aba fechada (push em segundo plano) não passam pela extensão.
* Se o site mudar o layout, o seletor das mensagens pode precisar de ajuste (campo do site nas configurações).
