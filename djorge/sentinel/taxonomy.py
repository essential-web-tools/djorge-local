"""Djorge Sentinel taxonomy: families -> types, social-engineering techniques, danger bands, risk profiles.

Labels are what the model reads as options (Portuguese, with a short description). Each type maps to public
standards (MITRE ATT&CK technique, OWASP LLM Top 10 2025, Llama Guard 4 / MLCommons hazard code, LGPD article) so
integrators can read the output without learning a new vocabulary. DEFENSIVE ONLY: these labels describe content
to detect, never content to produce.
"""
from __future__ import annotations

from typing import Dict, List

# ------------------------------------------------------------------------------------------------ families --
FAMILIES: Dict[str, dict] = {
    "fraude": {"label": "fraude ou golpe", "desc": "engana a pessoa para tirar dinheiro, senhas ou dados"},
    "tecnica": {"label": "ameaça técnica", "desc": "link, arquivo, script ou comando malicioso, ou segredo exposto"},
    "ia": {"label": "ataque a IA", "desc": "tenta mudar as instruções de um assistente ou agente de IA"},
    "dados": {"label": "dados pessoais", "desc": "contém dados pessoais, financeiros ou sensíveis de alguém"},
    "nocivo": {"label": "conteúdo nocivo", "desc": "ódio, assédio, ameaça, autolesão, conteúdo sexual ou violento"},
    "manipulacao": {"label": "manipulação", "desc": "spam, desinformação, personificação de marca ou pessoa"},
}
FAMILY_KEYS = list(FAMILIES)

# ---------------------------------------------------------------------------------------------------- types --
# key: (family, label, description, standards)
TYPES: Dict[str, tuple] = {
    # fraude
    "phishing": ("fraude", "roubo de senha ou código", "pede login, senha, código ou dados de cartão se passando por alguém",
                 {"attack": "T1566", "lg": "S2"}),
    "falso_parente": ("fraude", "falso parente ou amigo", "diz ser conhecido com número novo e pede dinheiro", {"attack": "T1656", "lg": "S2"}),
    "falsa_central": ("fraude", "falsa central ou falso suporte", "se passa por banco ou suporte técnico e pede ação, acesso ou dados",
                      {"attack": "T1656", "lg": "S2"}),
    "pagamento_falso": ("fraude", "Pix, boleto ou cobrança falsa", "cobrança, taxa ou chave de pagamento de golpe", {"lg": "S2"}),
    "orgao_publico": ("fraude", "falso órgão público", "se passa por governo, INSS, Receita, Detran ou Correios", {"attack": "T1656", "lg": "S2"}),
    "investimento": ("fraude", "investimento ou cripto falso", "promete lucro alto, garantido ou rápido", {"lg": "S2"}),
    "emprego": ("fraude", "falso emprego ou tarefa paga", "oferta de trabalho fácil que pede pagamento ou dados", {"lg": "S2"}),
    "romance": ("fraude", "golpe amoroso", "relação afetiva usada para pedir dinheiro", {"lg": "S2"}),
    "compra_venda": ("fraude", "compra, venda ou prêmio falso", "loja, anúncio, sorteio ou prêmio falso que pede pagamento",
                     {"lg": "S2"}),
    "corporativo": ("fraude", "fraude corporativa (BEC)", "falso chefe ou fornecedor pede pagamento ou troca de conta",
                    {"attack": "T1656", "lg": "S2"}),
    "extorsao": ("fraude", "extorsão ou chantagem", "ameaça expor ou prejudicar se não pagar", {"lg": "S2"}),
    # tecnica
    "link_malicioso": ("tecnica", "link malicioso", "endereço que leva a página falsa ou baixa programa malicioso",
                       {"attack": "T1566.002"}),
    "arquivo_malicioso": ("tecnica", "arquivo ou app malicioso", "induz a abrir anexo, instalar app ou executar arquivo",
                          {"attack": "T1204"}),
    "script_malicioso": ("tecnica", "script ou comando malicioso", "código que baixa, executa, esconde ou destrói algo",
                         {"attack": "T1059", "lg": "S14"}),
    "segredo_exposto": ("tecnica", "segredo ou credencial exposta", "senha, chave de API ou token aparece no conteúdo",
                        {"attack": "T1552"}),
    "ofuscacao": ("tecnica", "conteúdo ofuscado", "texto ou código escondido por codificação ou caracteres ocultos",
                  {"attack": "T1027"}),
    # ia
    "injecao_direta": ("ia", "injeção de prompt direta", "manda a IA ignorar regras ou instruções", {"owasp": "LLM01"}),
    "injecao_indireta": ("ia", "injeção escondida em conteúdo", "instrução para a IA escondida em e-mail, página ou documento",
                         {"owasp": "LLM01"}),
    "jailbreak": ("ia", "jailbreak", "tenta tirar as proteções da IA com papéis, histórias ou truques", {"owasp": "LLM01"}),
    "vazamento_prompt": ("ia", "extração de instruções", "pede para a IA revelar instruções internas ou segredos", {"owasp": "LLM07"}),
    "acao_agente": ("ia", "ordem para agente agir", "manda um agente enviar, pagar, apagar ou acessar algo", {"owasp": "LLM06"}),
    # dados
    "identificador": ("dados", "documento ou contato pessoal", "CPF, RG, telefone, e-mail, endereço de alguém",
                      {"lgpd": "art. 5º I", "lg": "S7"}),
    "financeiro": ("dados", "dado financeiro", "cartão, conta, chave Pix ou dados bancários", {"lgpd": "art. 5º I", "lg": "S7"}),
    "sensivel": ("dados", "dado sensível", "saúde, religião, orientação sexual, biometria ou origem", {"lgpd": "art. 5º II", "lg": "S7"}),
    "credencial": ("dados", "login ou senha", "usuário, senha ou código de acesso", {"lgpd": "art. 46", "lg": "S7"}),
    # nocivo
    "odio": ("nocivo", "ódio ou discriminação", "ataca pessoas por raça, origem, religião, gênero ou orientação", {"lg": "S10"}),
    "assedio": ("nocivo", "insulto ou assédio", "ofende, humilha ou persegue uma pessoa", {"lg": "S10"}),
    "ameaca": ("nocivo", "ameaça ou violência", "ameaça ferir ou incentiva violência", {"lg": "S1"}),
    "autolesao": ("nocivo", "autolesão ou suicídio", "fala de se ferir ou de tirar a própria vida", {"lg": "S11"}),
    "sexual": ("nocivo", "conteúdo sexual", "conteúdo sexual explícito ou obsceno", {"lg": "S12"}),
    "infantil": ("nocivo", "risco a criança", "sexualização ou exploração de menores", {"lg": "S4"}),
    "extremismo": ("nocivo", "extremismo ou terrorismo", "apoio a grupos ou atos extremistas", {"lg": "S1"}),
    "armas_drogas": ("nocivo", "armas ou drogas", "venda ou fabricação de armas ou drogas", {"lg": "S9"}),
    # manipulacao
    "spam": ("manipulacao", "spam", "propaganda em massa não solicitada", {}),
    "desinformacao": ("manipulacao", "desinformação", "afirmação falsa apresentada como fato", {"lg": "S5"}),
    "eleitoral": ("manipulacao", "conteúdo eleitoral enganoso", "informação falsa sobre eleição ou votação", {"lg": "S13"}),
    "personificacao": ("manipulacao", "personificação", "se passa por marca, empresa ou pessoa conhecida", {"attack": "T1656"}),
}
TYPES_BY_FAMILY: Dict[str, List[str]] = {f: [k for k, v in TYPES.items() if v[0] == f] for f in FAMILY_KEYS}

# --------------------------------------------------------------------------------------------- techniques --
TECHNIQUES: Dict[str, tuple] = {
    "urgencia": ("urgência", "pressa artificial: hoje, agora, em 24 horas"),
    "medo": ("medo ou ameaça", "bloqueio, multa, processo, prisão ou perda"),
    "autoridade": ("autoridade", "diz ser banco, governo, chefe, polícia ou suporte"),
    "ganho": ("ganho fácil", "prêmio, lucro, desconto ou dinheiro fácil"),
    "sigilo": ("sigilo", "pede para não contar a ninguém ou não ligar para confirmar"),
    "troca_canal": ("mudança de canal", "pede para falar em outro número, app ou site"),
    "pede_pagamento": ("pedido de pagamento", "pede Pix, boleto, transferência, cartão-presente ou cripto"),
    "pede_dados": ("pedido de dados", "pede senha, código, documento, foto ou dados pessoais"),
    "pede_acao": ("pedido de ação", "pede clicar, baixar, instalar, abrir ou executar"),
}
TECH_KEYS = list(TECHNIQUES)

# ------------------------------------------------------------------------------------------------- bands --
BANDS = ["nenhum", "baixo", "médio", "alto", "crítico"]
BAND_DESC = ["nenhum risco", "risco baixo, só atenção", "risco médio, pede cuidado", "risco alto, provável dano",
             "risco crítico, dano grave ou imediato"]
BAND_SCORE = [0, 25, 50, 75, 95]                 # representative 0..100 value of each band

# ------------------------------------------------------------------------------------------------- spans --
SPAN_TARGETS = {
    "indicador": ("indicador do golpe", "link, telefone, e-mail, chave Pix, conta ou carteira usada pelo golpista"),
    "pedido": ("pedido perigoso", "trecho que pede pagamento, senha, código, dados ou ação arriscada"),
    "instrucao_ia": ("instrução para IA", "trecho que tenta mandar em um assistente ou agente de IA"),
    "dado_pessoal": ("dado pessoal", "documento, contato, cartão, conta ou senha de uma pessoa"),
    "marca": ("marca ou pessoa imitada", "nome de banco, empresa, órgão ou pessoa usado para enganar"),
}

# ----------------------------------------------------------------------------------------- risk profiles --
# block_from / warn_from: band index; min_conf: minimum probability for a flag to count; abstain: what "não sei" does.
PROFILES = {
    "conservador": {"block_from": 2, "warn_from": 1, "min_conf": 0.50, "abstain": "revisar"},
    "equilibrado": {"block_from": 3, "warn_from": 2, "min_conf": 0.70, "abstain": "avisar"},
    "permissivo": {"block_from": 4, "warn_from": 3, "min_conf": 0.85, "abstain": "permitir"},
    "analista": {"block_from": 99, "warn_from": 0, "min_conf": 0.05, "abstain": "revisar"},
}
# floors nobody can relax: (type key, minimum action, minimum probability)
FLOORS = [("infantil", "bloquear", 0.30), ("acao_agente", "bloquear", 0.60), ("phishing", "avisar", 0.60)]
FLOOR_CRITICAL = [("phishing", 4, "bloquear")]      # phishing judged critical is always blocked
ACTIONS = ["permitir", "avisar", "revisar", "bloquear"]

CHANNELS = ["sms", "mensageiro", "email", "voz", "site", "conversa", "documento", "codigo", "post", "imagem", "url",
            "desconhecido"]

REASON_TEXT = {  # fixed, translated reasons (never generated)
    "pt": {"urgencia": "cria urgência artificial", "medo": "usa medo ou ameaça", "autoridade": "diz ser uma autoridade",
           "ganho": "promete ganho fácil", "sigilo": "pede sigilo", "troca_canal": "pede para mudar de canal",
           "pede_pagamento": "pede pagamento", "pede_dados": "pede dados ou códigos", "pede_acao": "pede para clicar, baixar ou executar",
           "reputacao": "indicador listado em base de ameaças", "ocultos": "havia caracteres ocultos ou texto codificado",
           "link_divergente": "o texto do link não bate com o destino real", "remetente_divergente": "o nome do remetente não bate com o domínio",
           "auth_falhou": "a autenticação do e-mail falhou", "caso_parecido": "muito parecido com um golpe conhecido",
           "fora_conhecido": "conteúdo diferente de tudo que o modelo conhece", "pagina_formulario": "a página pede senha ou cartão"},
    "en": {"urgencia": "creates artificial urgency", "medo": "uses fear or threats", "autoridade": "claims to be an authority",
           "ganho": "promises easy gains", "sigilo": "asks for secrecy", "troca_canal": "asks to switch channel",
           "pede_pagamento": "asks for payment", "pede_dados": "asks for data or codes", "pede_acao": "asks to click, download or run",
           "reputacao": "indicator listed in a threat feed", "ocultos": "hidden characters or encoded text",
           "link_divergente": "link text does not match its real destination", "remetente_divergente": "sender name does not match the domain",
           "auth_falhou": "e-mail authentication failed", "caso_parecido": "very similar to a known scam",
           "fora_conhecido": "unlike anything the model knows", "pagina_formulario": "the page asks for a password or card"},
}


def family_options():
    return [FAMILIES[k]["label"] for k in FAMILY_KEYS], [FAMILIES[k]["desc"] for k in FAMILY_KEYS]


def type_options(family: str):
    ks = TYPES_BY_FAMILY[family]
    return ks, [TYPES[k][1] for k in ks], [TYPES[k][2] for k in ks]


def tech_options():
    return [TECHNIQUES[k][0] for k in TECH_KEYS], [TECHNIQUES[k][1] for k in TECH_KEYS]


ASK_FAMILY = "Quais riscos de segurança este conteúdo tem?"
ASK_TYPE = "Que tipos de {fam} aparecem neste conteúdo?"
ASK_TECH = "Que técnicas de manipulação este conteúdo usa?"
ASK_BAND = "Qual é o nível de perigo deste conteúdo para quem o recebe?"
