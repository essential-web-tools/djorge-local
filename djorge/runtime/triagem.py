"""Triagem de mensagens RECEBIDAS (e-mail, WhatsApp, notificações): o que é, quão importante, o que fazer e onde pôr.

Pensada para quem recebe muito: cada mensagem sai com
  categoria   pessoal | trabalho | financeiro | compras e entregas | agenda | promoções | notificações | segurança
  prioridade  urgente | alta | normal | baixa  (+ nota 0-100 e os motivos)
  ações       responder | agendar | pagar | criar tarefa | acompanhar entrega | usar código | ler depois | arquivar
  destino     onde inserir no app (card de agenda, tarefa com prazo, financeiro, entregas, arquivo)
  dados       datas, horas, valores, boleto, rastreio, código, link de reunião, entidades (com o nome tirado do e-mail)
Regras leves dão sinais com peso; com o DOFL (engine) a categoria e a segurança vêm do modelo e as regras ajustam.

    from djorge.runtime.triagem import triar
    triar("Oi! A fatura de R$ 230,00 vence amanhã ...", remetente="cobranca@enel.com.br", engine=eng)
"""
from __future__ import annotations

import datetime as _dt
import re
from typing import Dict, Iterable, List, Optional

from .recognizers import _sem_acento, nome_do_email, reconhecer

CATEGORIAS = {
    "pessoal": "conversa pessoal de amigos ou família",
    "trabalho": "assunto de trabalho, cliente, projeto ou estudo",
    "financeiro": "cobrança, fatura, boleto, pagamento, banco ou pix",
    "compras e entregas": "pedido de compra, envio, entrega ou rastreio",
    "agenda": "convite, reunião, consulta ou compromisso com data",
    "promoções": "propaganda, oferta, cupom ou newsletter",
    "notificações": "aviso automático de sistema, login, código ou conta",
}
SINAIS = {   # categoria: [(regex sem acento, peso)]
    "financeiro": [(r"\b(boleto|fatura|vencimento|vence|pagamento|pagar|cobranca|debito|pix|transferencia|"
                    r"extrato|parcela|mensalidade|nota fiscal)\b", 2.0)],
    "compras e entregas": [(r"\b(pedido|entrega|enviado|despachado|rastreio|rastreamento|transportadora|"
                            r"saiu para entrega|a caminho|chegou|devolucao)\b", 2.0)],
    "agenda": [(r"\b(reuniao|convite|call|agenda|consulta|compromisso|encontro|marcad[ao]|remarcar|horario)\b", 1.5)],
    "promoções": [(r"\b(oferta|desconto|cupom|promocao|imperdivel|so hoje|frete gratis|black friday|liquidacao|"
                   r"descadastr|unsubscribe|newsletter|% off)\b", 2.0)],
    "notificações": [(r"\b(codigo de verificacao|seu codigo|login|novo acesso|senha|verifique|confirmar e-mail|"
                      r"notificacao|nao responda|noreply|no-reply)\b", 2.0)],
    "trabalho": [(r"\b(projeto|relatorio|cliente|prazo|entregavel|aprovacao|proposta|contrato|planilha|apresentacao|"
                  r"equipe|gestor|sprint|demanda|orcamento)\b", 1.5)],
    "pessoal": [(r"\b(saudade|beijo|bjs|abraco|familia|mae|pai|amor|aniversario|churrasco|fim de semana)\b", 1.5)],
}
URGENTE = r"\b(urgente|urgencia|imediatamente|o quanto antes|asap|hoje ainda|ultimo dia|prazo final|importante|atencao)\b"
PERGUNTA = r"(\?|\b(pode|poderia|consegue|conseguiria|me confirma|confirma|aguardo (seu )?retorno|me avisa|me responde|" \
           r"o que acha|topa|vamos)\b)"
PEDIDO = r"\b(preciso que|precisamos que|favor|por favor|pode enviar|envie|mande|revisar|revise|assinar|assine|" \
         r"preencher|preencha|aprovar|aprove|responder ate)\b"


def triar(texto: str, remetente: Optional[str] = None, assunto: Optional[str] = None, engine=None,
          vips: Iterable[str] = (), hoje: Optional[_dt.date] = None) -> Dict:
    hoje = hoje or _dt.date.today()
    corpo = f"{assunto or ''}\n{texto}".strip()
    s = _sem_acento(corpo.casefold())
    dados = reconhecer(corpo, hoje)
    por = {}
    for d in dados:
        por.setdefault(d.campo, []).append(d)
    motivos: List[str] = []

    # ---- categoria: regras (+ modelo, se houver) ----
    nota = {c: 0.0 for c in CATEGORIAS}
    for c, regras in SINAIS.items():
        for rx, w in regras:
            nota[c] += w * len(re.findall(rx, s))
    if "boleto" in por or "valor" in por:
        nota["financeiro"] += 1.5
    if "rastreio" in por:
        nota["compras e entregas"] += 3
    if "link de reunião" in por:
        nota["agenda"] += 3
    elif "data" in por and "hora" in por:
        nota["agenda"] += 2 if nota["agenda"] else 1
    if "código" in por:
        nota["notificações"] += 3
    rem = (remetente or "").lower()
    info_rem = nome_do_email(rem) if "@" in rem else {"entidade": remetente, "tipo": None}
    automatico = bool(re.search(r"no-?reply|nao-?responda|notifica|newsletter|mailer|news@|info@|marketing", rem))
    if automatico:
        nota["notificações"] += 1; nota["promoções"] += 0.5
    tot = sum(nota.values())
    prob = {c: (v / tot if tot else 0.0) for c, v in nota.items()}
    if engine is not None:
        from .semantic import SemReq
        r = engine.run([SemReq("CHOICE", corpo[:1500], options=list(CATEGORIAS), descriptions=list(CATEGORIAS.values()),
                               ask="Que tipo de mensagem é esta?")])[0]
        w = 0.5 if tot else 1.0                       # sem sinais, vale o modelo
        prob = {c: w * r["distribution"][c] + (1 - w) * prob[c] for c in CATEGORIAS}
    if not tot and engine is None:
        prob["pessoal" if not automatico else "notificações"] = 1.0
    categoria = max(prob, key=prob.get)

    # ---- segurança (só com o modelo) ----
    seguranca = None
    if engine is not None:
        from .semantic import SemReq
        LAB = {"seguro": "legítima, sem ameaça", "spam": "propaganda em massa não solicitada",
               "golpe": "engana a pessoa para pagar ou transferir dinheiro",
               "phishing": "rouba senhas, códigos ou dados de cartão se passando por alguém",
               "malware": "entrega programa ou arquivo malicioso"}
        r = engine.run([SemReq("CHOICE", corpo[:1500], options=list(LAB), descriptions=list(LAB.values()),
                               ask="Esta mensagem é segura ou uma ameaça?")])[0]
        seguranca = {"rotulo": r["answer"], "p": round(float(r["probability"]), 3)}
        if r["answer"] != "seguro" and r["probability"] >= 0.7:
            categoria = "segurança"

    # ---- prioridade ----
    pts = 20
    datas = sorted(d.valor for d in por.get("data", []) if d.valor)
    prazo = datas[0] if datas else None
    if any(v.lower() in rem or (info_rem.get("entidade") or "").lower() == v.lower() for v in vips if v):
        pts += 30; motivos.append("remetente importante")
    if re.search(URGENTE, s):
        pts += 40; motivos.append("pede urgência")
    if prazo:
        dias = (_dt.date.fromisoformat(prazo) - hoje).days
        if dias <= 0:
            pts += 30; motivos.append("prazo hoje")
        elif dias == 1:
            pts += 22; motivos.append("prazo amanhã")
        elif dias <= 3:
            pts += 12; motivos.append(f"prazo em {dias} dias")
    pergunta = bool(re.search(PERGUNTA, s)) and not automatico
    if pergunta:
        pts += 10; motivos.append("espera resposta")
    if "código" in por:
        pts += 20; motivos.append("código temporário")
    if categoria == "financeiro" and prazo:
        pts += 10
    if categoria == "promoções":
        pts -= 25
    if automatico and categoria != "notificações":
        pts -= 10
    if categoria == "segurança":
        pts = max(pts, 75); motivos.append("possível ameaça")
    pts = max(0, min(100, pts))
    if engine is not None:                        # urgência ordinal do modelo (SCORE, como no Jev), misturada às regras
        from .semantic import SemReq
        niv = ["baixa", "normal", "alta", "urgente"]
        r = engine.run([SemReq("SCORE", corpo[:1500], options=niv, ask="Qual a urgência desta mensagem para quem a recebe?")])[0]
        dist = r.get("distribution") or {}
        if dist:
            esperado = sum(dist.get(n, 0) * v for n, v in zip(niv, (10, 35, 60, 85)))
            pts = int(round(0.6 * pts + 0.4 * esperado))
            motivos.append(f"modelo: {max(dist, key=dist.get)}")
    prioridade = "urgente" if pts >= 70 else "alta" if pts >= 50 else "normal" if pts >= 25 else "baixa"

    # ---- ações e destino no app ----
    acoes, destino = [], None
    if categoria == "segurança":
        acoes, destino = ["não clicar em links", "apagar ou denunciar"], "quarentena"
    else:
        if pergunta:
            acoes.append("responder")
        if categoria == "agenda" or "link de reunião" in por or ("data" in por and "hora" in por and categoria != "financeiro"):
            acoes.append("agendar"); destino = "card de agenda"
        if categoria == "financeiro" and ("boleto" in por or "valor" in por or "chave pix" in por):
            acoes.append("pagar"); destino = destino or "financeiro (tarefa com vencimento)"
        if re.search(PEDIDO, s) and not automatico:
            acoes.append("criar tarefa"); destino = destino or ("tarefa com prazo" if prazo else "tarefa")
        if "rastreio" in por or categoria == "compras e entregas":
            acoes.append("acompanhar entrega"); destino = destino or "entregas"
        if "código" in por:
            acoes.append("usar código"); destino = destino or "notificação passageira"
        if categoria == "promoções":
            acoes.append("arquivar"); destino = destino or "promoções (resumo semanal)"
        if not acoes:
            acoes.append("ler depois" if prioridade in ("normal", "alta") else "arquivar")
            destino = destino or ("caixa de entrada" if prioridade != "baixa" else "arquivo")

    def _v(c):
        return [d.valor or d.texto for d in por.get(c, [])]
    entidades = [{"nome": d.valor, "tipo": d.extra.get("tipo")} for d in por.get("entidade", [])]
    if info_rem.get("entidade"):
        entidades.insert(0, {"nome": info_rem["entidade"], "tipo": info_rem.get("tipo"), "papel": "remetente"})
    primeira = re.split(r"(?<=[.!?])\s+|\n", texto.strip())[0][:160]
    return {"categoria": categoria, "categorias": {c: round(p, 3) for c, p in sorted(prob.items(), key=lambda x: -x[1])},
            "prioridade": prioridade, "nota": pts, "motivos": motivos, "acoes": acoes, "destino": destino,
            "prazo": prazo, "seguranca": seguranca, "resumo": primeira,
            "dados": {k: v for k, v in {"datas": _v("data"), "horas": _v("hora"), "valores": _v("valor"),
                                         "boleto": _v("boleto"), "rastreio": _v("rastreio"), "codigo": _v("código"),
                                         "reuniao": _v("link de reunião"), "pix": _v("chave pix"),
                                         "telefones": _v("telefone"), "emails": _v("endereço de e-mail"),
                                         "links": _v("url")}.items() if v},
            "entidades": entidades}
