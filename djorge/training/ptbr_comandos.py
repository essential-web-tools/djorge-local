"""Pedidos em português do Brasil escritos à mão (por Claude, a pedido do dono do projeto), com os campos marcados.

Cada modelo de frase gera pedidos com nomes, datas, horas, lugares etc. trocados; os trechos de cada campo são
marcados pela própria montagem (sem anotação automática). Saem três tipos de registro, nos mesmos formatos do treino:
  * ptbr:ferramenta  - qual ferramenta do app resolve (CHOICE, como tools:ferramenta)
  * ptbr:escopo      - o app consegue fazer? (NOUL, como tools:fora_do_escopo)
  * ptbr:campos      - cada campo do app: o trecho certo, ou NENHUM quando o campo não está na frase
Os modelos de frase são divididos em treino / dev / teste: dev e teste usam modelos de frase que o treino nunca viu,
então medem generalização e não memória.

    python -m djorge.training.ptbr_comandos --out DIR [--n 9000]
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re

from .flash_data import tool_records

CAMPOS_APP = ["data", "hora", "período do dia", "pessoa", "evento ou compromisso", "lugar", "lista",
              "endereço de e-mail", "empresa ou estabelecimento", "frequência ou repetição"]

# ---- valores (prefixo fora do trecho, valor marcado) ----
F = {
    "pessoa": ["Ana", "João", "Maria Clara", "Pedro", "Fernanda", "Lucas", "Beatriz", "Rafael", "Juliana", "Carlos",
               "Dona Cida", "Gabriel", "Larissa", "Thiago", "Camila", "Seu Antônio", "Mariana", "Bruno", "Letícia",
               "Felipe", "Vanessa", "Rodrigo", "Patrícia", "Gustavo", "Aline", "Diego", "Renata", "Paulo", "Sofia",
               "Vitória", "Henrique", "Isabela", "Marcos", "Tatiane", "Eduardo", "Priscila", "Igor", "Natália"],
    "data": ["amanhã", "hoje", "depois de amanhã", "sexta", "sexta-feira", "segunda", "segunda-feira", "terça",
             "quarta-feira", "quinta", "sábado", "domingo", "dia 15", "dia 3", "dia 28", "3 de dezembro",
             "12 de janeiro", "20/11", "5/12", "semana que vem", "sábado que vem", "mês que vem", "fim de semana"],
    "hora": ["15h", "9h", "9h30", "10h", "18h", "7h", "14:30", "16:45", "8 horas", "meio-dia", "20h", "11h15",
             "13h", "17h30", "6h", "21h", "uma da tarde", "duas e meia"],
    "período do dia": ["de manhã", "à tarde", "à noite", "cedinho", "de madrugada", "no fim da tarde", "na hora do almoço"],
    "frequência ou repetição": ["toda segunda", "todo dia", "todo mês", "toda semana", "de 15 em 15 dias",
                                "todo sábado", "dia sim, dia não", "toda sexta", "todos os dias úteis", "todo ano"],
    "evento": [("uma", "reunião"), ("uma", "consulta no dentista"), ("a", "aula de inglês"), ("uma", "entrevista"),
               ("uma", "call com o time"), ("o", "treino na academia"), ("a", "prova de cálculo"),
               ("o", "aniversário da minha mãe"), ("uma", "consulta com a pediatra"), ("o", "almoço de família"),
               ("a", "reunião de pais"), ("o", "corte de cabelo"), ("a", "revisão do carro"), ("o", "culto"),
               ("a", "apresentação do projeto"), ("o", "happy hour"), ("a", "sessão de terapia"),
               ("o", "exame de sangue"), ("a", "festa da firma"), ("o", "jogo do Pedrinho")],
    "lugar": [("no", "escritório"), ("na", "padaria"), ("no", "shopping Center Norte"), ("na", "casa da minha mãe"),
              ("na", "Avenida Paulista"), ("no", "Centro"), ("na", "faculdade"), ("no", "hospital São Lucas"),
              ("na", "academia"), ("no", "Starbucks da Faria Lima"), ("em", "Copacabana"), ("na", "igreja"),
              ("no", "restaurante japonês"), ("na", "rodoviária"), ("em", "Campinas"), ("no", "Parque Ibirapuera")],
    "lista": [("do", "mercado"), ("de", "compras"), ("da", "farmácia"), ("de", "tarefas da casa"),
              ("de", "presentes de Natal"), ("da", "viagem"), ("de", "filmes"), ("do", "churrasco"),
              ("de", "material escolar"), ("da", "obra")],
    "empresa": ["Nubank", "Itaú", "Mercado Livre", "Amazon", "Vivo", "Correios", "iFood", "Magazine Luiza",
                "Banco do Brasil", "Claro", "Caixa", "Shopee", "Netflix", "Unimed", "Sabesp", "Enel", "Bradesco"],
    "email": ["joao.silva@gmail.com", "contato@empresa.com.br", "ana.souza@outlook.com", "financeiro@lojabr.com.br",
              "rh@construtora.com.br", "pedro123@hotmail.com", "suporte@escola.edu.br", "marina.l@yahoo.com.br"],
    # sem campo marcado
    "item": ["leite", "ovos", "café", "pão", "arroz", "feijão", "detergente", "papel higiênico", "banana", "frango",
             "sabão em pó", "carvão", "cerveja", "tomate", "dipirona", "protetor solar", "caderno", "fralda"],
    "tarefa": ["pagar a conta de luz", "tomar o remédio", "ligar pro banco", "levar o cachorro no veterinário",
               "regar as plantas", "buscar as crianças", "renovar a CNH", "pagar o boleto do condomínio",
               "mandar o relatório", "comprar ração", "trocar o filtro", "devolver o livro"],
    "msg": ["vou chegar atrasado", "a reunião foi cancelada", "já fiz o pix", "chego em 10 minutos",
            "não vou poder ir", "o documento está pronto", "pode me ligar quando puder", "amanhã eu levo",
            "deu tudo certo", "preciso remarcar"],
    "assunto": ["a fatura", "o contrato", "a entrega", "o reembolso", "a matrícula", "o orçamento"],
    "cidade": ["Tóquio", "Lisboa", "Nova York", "Manaus", "Londres", "Buenos Aires"],
    "comida": ["calabresa", "frango com catupiry", "marguerita", "quatro queijos"],
    "comodo": ["sala", "cozinha", "quarto", "varanda"],
    "artista": ["Djavan", "Anitta", "Legião Urbana", "Marília Mendonça", "Caetano Veloso", "Jorge Ben Jor"],
    "n": ["15", "10", "7", "20", "3", "12,5"],
    "v": ["2.340", "150", "89,90", "1.200", "37", "560", "4.500"],
}
SLOT = {"pessoa": "pessoa", "data": "data", "hora": "hora", "período do dia": "período do dia",
        "frequência ou repetição": "frequência ou repetição", "evento": "evento ou compromisso", "lugar": "lugar",
        "lista": "lista", "empresa": "empresa ou estabelecimento", "email": "endereço de e-mail"}

# ---- modelos de frase: (ferramenta, texto com {campo}) ; "{evento}" etc. levam o próprio artigo/preposição ----
T = [
    ("cards", "marca {evento} com {pessoa} {data} às {hora}"),
    ("cards", "marca {evento} {data} às {hora} {lugar}"),
    ("cards", "agenda {evento} pra {data} às {hora}"),
    ("cards", "me lembra de {tarefa} {data} {período do dia}"),
    ("cards", "me lembra de {tarefa} {frequência ou repetição}"),
    ("cards", "cria um lembrete pra {tarefa} {data} às {hora}"),
    ("cards", "coloca na agenda {evento} com {pessoa} {data}"),
    ("cards", "desmarca {evento} de {data}"),
    ("cards", "põe um alarme pras {hora}"),
    ("cards", "me acorda {data} às {hora}"),
    ("cards", "cancela o alarme das {hora}"),
    ("cards", "remarca {evento} com {pessoa} pra {data} às {hora}"),
    ("cards", "anota {evento} {lugar} {data} {período do dia}"),
    ("cards", "cria uma tarefa de {tarefa} pra {data}"),
    ("cards", "bota um lembrete {frequência ou repetição} às {hora} pra {tarefa}"),
    ("cards", "preciso marcar {evento} com {pessoa} {lugar} {data}"),
    ("cards", "apaga o lembrete de {tarefa}"),
    ("cards", "muda {evento} de {data} pras {hora}"),
    ("proximas_tarefas", "o que eu tenho {data}?"),
    ("proximas_tarefas", "tenho alguma coisa marcada {data} {período do dia}?"),
    ("proximas_tarefas", "qual é o meu próximo compromisso?"),
    ("proximas_tarefas", "que alarmes eu tenho ligados?"),
    ("proximas_tarefas", "a que horas é {evento} {data}?"),
    ("proximas_tarefas", "tem algo marcado com {pessoa} {data}?"),
    ("proximas_tarefas", "como tá minha agenda {data}?"),
    ("proximas_tarefas", "quais lembretes eu tenho pra {data}?"),
    ("proximas_tarefas", "quando é {evento}?"),
    ("proximas_tarefas", "tô livre {data} às {hora}?"),
    ("listas", "coloca {item}, {item2} e {item3} na lista {lista}"),
    ("listas", "adiciona {item} na lista {lista}"),
    ("listas", "tira {item} da lista {lista}"),
    ("listas", "o que tem na lista {lista}?"),
    ("listas", "cria uma lista {lista}"),
    ("listas", "ordena a lista {lista} por prioridade"),
    ("listas", "apaga a lista {lista}"),
    ("listas", "me mostra a lista {lista}"),
    ("listas", "marca {item} como comprado na lista {lista}"),
    ("bilhete", "manda uma mensagem pra {pessoa} dizendo que {msg}"),
    ("bilhete", "manda um e-mail pro {email} avisando que {msg}"),
    ("bilhete", "avisa {pessoa} no whatsapp que {msg}"),
    ("bilhete", "responde o e-mail da {empresa} dizendo que {msg}"),
    ("bilhete", "escreve pra {pessoa} que {msg}"),
    ("bilhete", "manda um zap pra {pessoa}: {msg}"),
    ("bilhete", "envia pro {email} que {msg}"),
    ("bilhete", "fala pra {pessoa} que {msg} {data}"),
    ("bilhete", "posta no meu status que {msg}"),
    ("buscar", "procura o e-mail da {empresa} sobre {assunto}"),
    ("buscar", "qual é o telefone de {pessoa}?"),
    ("buscar", "acha a mensagem que {pessoa} mandou {data}"),
    ("buscar", "busca os e-mails do {email}"),
    ("buscar", "qual o e-mail de {pessoa}?"),
    ("buscar", "me mostra as mensagens não lidas de {pessoa}"),
    ("buscar", "encontra o boleto da {empresa}"),
    ("calcular", "quanto é {n}% de {v} reais?"),
    ("calcular", "quanto dá {v} dividido por {n}?"),
    ("calcular", "converte {v} dólares pra reais"),
    ("calcular", "soma {v} com {v2}"),
    ("calcular", "quanto fica {v} parcelado em {n} vezes?"),
    ("calcular", "faz a conta de {v} menos {n}% de desconto"),
    ("datas_horas", "que dia da semana cai {data}?"),
    ("datas_horas", "quantos dias faltam pra {evento}?"),
    ("datas_horas", "que horas são em {cidade}?"),
    ("datas_horas", "que dia é hoje?"),
    ("datas_horas", "quanto tempo falta pras {hora}?"),
    ("datas_horas", "qual a diferença de fuso entre aqui e {cidade}?"),
    ("datas_horas", "{data} é feriado?"),
    ("regra", "toda vez que chegar e-mail da {empresa}, me avisa"),
    ("regra", "quando {pessoa} mandar mensagem, marca como importante"),
    ("regra", "se chegar boleto no e-mail, cria uma tarefa pra pagar"),
    ("regra", "sempre que {pessoa} me ligar, me lembra de retornar {período do dia}"),
    ("regra", "arquiva automaticamente os e-mails da {empresa}"),
    ("regra", "quando chegar mensagem do {email}, encaminha pra {pessoa}"),
    ("nenhuma", "pede uma pizza de {comida}"),
    ("nenhuma", "toca uma música do {artista}"),
    ("nenhuma", "liga a luz da {comodo}"),
    ("nenhuma", "como vai estar o tempo {data}?"),
    ("nenhuma", "chama um uber pro Centro"),
    ("nenhuma", "conta uma piada"),
    ("nenhuma", "compra {item} pela internet"),
    ("nenhuma", "qual foi o placar do jogo do Flamengo?"),
    ("nenhuma", "aumenta o volume"),
    ("nenhuma", "traduz bom dia pro japonês"),
    ("nenhuma", "faz um pix de {v} reais pra {pessoa}"),
    ("nenhuma", "abre o Instagram"),
    ("nenhuma", "qual a capital da Austrália?"),
    ("nenhuma", "desliga a TV da {comodo}"),
]


def _render(tpl: str, rng: random.Random):
    """-> (texto, {campo do app: [(ini, fim)]})"""
    out, spans, pos = "", {}, 0
    used_items = set()
    for m in re.finditer(r"\{([^}]+)\}", tpl):
        out += tpl[pos:m.start()]
        key = m.group(1)
        base = re.sub(r"\d$", "", key)
        pool = F[base]
        v = rng.choice(pool)
        if base == "item":
            while v in used_items and len(used_items) < len(pool):
                v = rng.choice(pool)
            used_items.add(v)
        pre, val = (v if isinstance(v, tuple) else ("", v))
        if pre:
            out += pre + " "
        if base in SLOT:
            spans.setdefault(SLOT[base], []).append((len(out), len(out) + len(val)))
        out += val
        pos = m.end()
    out += tpl[pos:]
    if rng.random() < 0.3:                                  # variação natural: maiúscula, "por favor", pontuação
        out = out[0].upper() + out[1:]
    if rng.random() < 0.15 and not out.endswith("?"):
        out += ", por favor"
    return out, spans


# ---- mensagens RECEBIDAS (triagem): (categoria, ação principal, texto) ----
F.update({"loja": ["Magazine Luiza", "Amazon", "Mercado Livre", "Shopee", "Americanas", "Netshoes"],
          "banco": ["Nubank", "Itaú", "Bradesco", "Caixa", "Banco do Brasil", "Inter"],
          "conta": ["luz", "água", "internet", "celular", "condomínio", "cartão"],
          "projeto": ["o relatório trimestral", "a proposta do cliente", "a apresentação de segunda", "o orçamento",
                      "o contrato novo", "a planilha de custos"],
          "codigo": ["482913", "731045", "1928", "550271", "88341"],
          "rastreio": ["QB123456789BR", "NX987654321BR", "LB555123987BR", "OJ302119845BR"]})
CATS = {"pessoal": "conversa pessoal de amigos ou família",
        "trabalho": "assunto de trabalho, cliente, projeto ou estudo",
        "financeiro": "cobrança, fatura, boleto, pagamento, banco ou pix",
        "compras e entregas": "pedido de compra, envio, entrega ou rastreio",
        "agenda": "convite, reunião, consulta ou compromisso com data",
        "promoções": "propaganda, oferta, cupom ou newsletter",
        "notificações": "aviso automático de sistema, login, código ou conta"}
ACOES = {"responder": "a pessoa espera uma resposta", "agendar": "tem data e hora para pôr na agenda",
         "pagar": "tem conta, boleto ou pix para pagar", "criar tarefa": "pede algo para fazer",
         "acompanhar entrega": "informa envio ou rastreio de pedido", "usar código": "traz um código temporário",
         "arquivar": "não precisa de nada, pode guardar", "ler depois": "informativo, sem pressa"}
M = [
    ("financeiro", "pagar", "Olá! Sua fatura de {conta} no valor de R$ {v} vence {data}."),
    ("financeiro", "pagar", "{banco}: o boleto de R$ {v} vence {data}. Evite juros."),
    ("financeiro", "pagar", "Oi, segue o pix pra gente dividir o churrasco: R$ {v}. Chave é meu celular."),
    ("financeiro", "arquivar", "{banco}: você recebeu um pix de R$ {v} de {pessoa}."),
    ("financeiro", "ler depois", "Seu extrato de {conta} já está disponível no app."),
    ("financeiro", "pagar", "Lembrete: a mensalidade da escola vence {data}, valor R$ {v}."),
    ("trabalho", "criar tarefa", "Oi, consegue revisar {projeto} até {data}? O cliente pediu."),
    ("trabalho", "responder", "{pessoa}, você vai poder participar da entrega de {projeto}?"),
    ("trabalho", "criar tarefa", "Preciso que você envie {projeto} até {data} {período do dia}, por favor."),
    ("trabalho", "ler depois", "Pessoal, segue em anexo {projeto} para conhecimento."),
    ("trabalho", "responder", "Bom dia! Pode me confirmar se recebeu {projeto}?"),
    ("trabalho", "criar tarefa", "Urgente: falta assinar {projeto} hoje ainda."),
    ("agenda", "agendar", "Convite: reunião de planejamento {data} às {hora}. Link: https://meet.google.com/abc-defg-hij"),
    ("agenda", "agendar", "Sua consulta com a Dra. {pessoa} está marcada para {data} às {hora}."),
    ("agenda", "responder", "Confirma presença na reunião de {data} às {hora}?"),
    ("agenda", "agendar", "A aula de amanhã foi remarcada para {data} às {hora}."),
    ("agenda", "agendar", "Lembrete: {evento} {data} às {hora} {lugar}."),
    ("compras e entregas", "acompanhar entrega", "Seu pedido na {loja} foi enviado! Código de rastreio: {rastreio}"),
    ("compras e entregas", "acompanhar entrega", "Seu pedido saiu para entrega e chega {data}."),
    ("compras e entregas", "arquivar", "{loja}: seu pedido foi entregue. Avalie sua compra!"),
    ("compras e entregas", "responder", "Oi, o produto chegou com defeito, como faço a troca?"),
    ("promoções", "arquivar", "Só hoje! 50% de desconto em toda a {loja}. Use o cupom BLACK50."),
    ("promoções", "arquivar", "Frete grátis em compras acima de R$ {v}. Aproveite!"),
    ("promoções", "arquivar", "Nossa newsletter da semana chegou: as novidades de outubro."),
    ("promoções", "arquivar", "{loja}: produtos que você viu estão com preço baixo. Confira!"),
    ("notificações", "usar código", "Seu código de verificação é {codigo}. Ele expira em 10 minutos."),
    ("notificações", "usar código", "{banco}: use o código {codigo} para confirmar o acesso."),
    ("notificações", "ler depois", "Novo acesso à sua conta detectado em um dispositivo Windows."),
    ("notificações", "arquivar", "Sua senha foi alterada com sucesso."),
    ("notificações", "ler depois", "Seu armazenamento está quase cheio (95%)."),
    ("pessoal", "responder", "Mãe, chego {data} às {hora}, me busca na rodoviária?"),
    ("pessoal", "responder", "Oi {pessoa}! Vamos marcar aquele café {data}?"),
    ("pessoal", "ler depois", "Olha essa foto da viagem, que saudade!"),
    ("pessoal", "agendar", "O aniversário da vó vai ser {data} às {hora}, não esquece!"),
    ("pessoal", "responder", "Você viu o jogo ontem? Que vergonha kkkk"),
    ("pessoal", "criar tarefa", "Amor, compra {item} quando passar no mercado?"),
]


def generate_triagem(n: int, rng: random.Random):
    """Mensagens recebidas -> CHOICE categoria (ptbr:triagem) e CHOICE ação principal (ptbr:acao)."""
    part_of = {}
    by = {}
    for i in range(len(M)):
        by.setdefault(M[i][0], []).append(i)
    for c, ii in by.items():
        for k, i in enumerate(ii):
            part_of[i] = "dev" if k % 5 == 1 else "test" if k % 5 == 3 else "train"
    parts = {"train": [], "dev": [], "test": []}
    seen = set()
    for _ in range(n * 4):
        if sum(len(v) for v in parts.values()) >= n:
            break
        i = rng.randrange(len(M))
        cat, acao, tpl = M[i]
        text, _sp = _render(tpl, rng)
        if text in seen:
            continue
        seen.add(text)
        cs = list(CATS); rng.shuffle(cs)
        al = rng.sample([a for a in ACOES if a != acao], 4) + [acao]; rng.shuffle(al)
        split = "train" if part_of[i] == "train" else "validation"
        meta = {"area": "triagem", "lang": "pt", "provenance": "ptbr_claude", "modelo_frase": 1000 + i}
        parts[part_of[i]].append([
            {"task": "CHOICE", "source": text, "options": cs, "label": cat, "descriptions": [CATS[c] for c in cs],
             "ask": "Que tipo de mensagem é esta?", "split": split, "meta": dict(meta, group="ptbr:triagem")},
            {"task": "CHOICE", "source": text, "options": al, "label": acao, "descriptions": [ACOES[a] for a in al],
             "ask": "O que fazer com esta mensagem?", "split": split, "meta": dict(meta, group="ptbr:acao")}])
    return parts


def generate(n: int = 9000, seed: int = 0):
    rng = random.Random(seed)
    idx = list(range(len(T)))
    rng.shuffle(idx)
    # cada ferramenta tem modelos de frase no treino E fora dele
    part_of = {}
    by_tool = {}
    for i in idx:
        by_tool.setdefault(T[i][0], []).append(i)
    for tool, ii in by_tool.items():
        for k, i in enumerate(ii):
            part_of[i] = "dev" if k % 5 == 1 else "test" if k % 5 == 3 else "train"
    parts = {"train": [], "dev": [], "test": []}
    seen = set()
    for j in range(n * 3):
        if sum(len(v) for v in parts.values()) >= n:
            break
        i = rng.randrange(len(T))
        tool, tpl = T[i]
        text, spans = _render(tpl, rng)
        if text.casefold() in seen:
            continue
        seen.add(text.casefold())
        part = part_of[i]
        prov = "ptbr_claude"
        recs = []
        for r in tool_records([(text, tool, "pt", prov)], rng):
            r["meta"]["group"] = "ptbr:ferramenta" if r["task"] == "CHOICE" else "ptbr:escopo"
            r["meta"]["modelo_frase"] = i
            recs.append(r)
        # um pedido ao app é conteúdo seguro: ensina a não acusar golpe num lembrete (mesmas opções do treino)
        ameacas = ["spam", "golpe", "phishing", "injeção de prompt", "malware"]
        DESC = {"seguro": "legítima, sem ameaça", "spam": "propaganda em massa não solicitada",
                "golpe": "engana a pessoa para pagar ou transferir dinheiro",
                "phishing": "rouba senhas, códigos ou dados de cartão se passando por alguém",
                "injeção de prompt": "tenta mudar as instruções de uma IA",
                "malware": "entrega programa ou arquivo malicioso"}
        ops = rng.sample(ameacas, rng.choice([1, 2])) + ["seguro"]
        rng.shuffle(ops)
        recs.append({"task": "CHOICE", "source": text, "options": ops, "label": "seguro",
                     "descriptions": [DESC[o] for o in ops],
                     "ask": rng.choice(["Classifique o risco de segurança deste conteúdo.",
                                        "Que tipo de ameaça é esta, se houver?"]),
                     "meta": {"area": "security", "lang": "pt", "provenance": prov, "threat": "safe",
                              "group": "ptbr:seguranca", "modelo_frase": i}})
        for campo in CAMPOS_APP:
            sp = sorted(spans.get(campo, []))
            recs.append({"task": "resolve", "mode": "ENTITY", "target": campo, "source": text,
                         "spans": [list(s) for s in sp], "polarity": ["asserted"] * len(sp),
                         "meta": {"area": "ptbr", "lang": "pt", "provenance": prov,
                                  "group": "ptbr:campos" if sp else "ptbr:campos_ausentes",
                                  "modelo_frase": i, "ausente": not sp}})
        for r in recs:
            r["split"] = "train" if part == "train" else "validation"
            assert all(text[a:b] for a, b in r.get("spans", []))
        parts[part].append(recs)
    tri = generate_triagem(max(600, n // 3), random.Random(seed + 1))
    for k in parts:
        parts[k] += tri[k]
    return {k: [r for recs in v for r in recs] for k, v in parts.items()}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--n", type=int, default=9000, help="pedidos (cada um vira ~12 registros)")
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args(argv)
    os.makedirs(a.out, exist_ok=True)
    parts = generate(a.n, a.seed)
    for k, rs in parts.items():
        with open(os.path.join(a.out, f"ptbr_{k}.jsonl"), "w", encoding="utf-8") as f:
            for r in rs:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print({k: len(v) for k, v in parts.items()}, "modelos de frase:", len(T))


if __name__ == "__main__":
    main()
