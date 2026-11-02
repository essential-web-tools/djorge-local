"""Reconhecedores leves (regras, sem modelo) e a combinação com os campos do DOFL. Fazem parte do MODELO (o app só
chama): o DOFL entende o pedido; as regras cuidam do que tem forma fixa e dão a cada achado uma CERTEZA própria.

  * cada regra tem uma certeza média (precisão esperada): e-mail 0,99 ... "segunda" sozinha 0,70 ("segunda vez");
    num conflito com o modelo vence quem tem mais certeza - uma regra fraca não atropela o modelo;
  * entidades: o campo padrão é "entidade" (pessoa, empresa, contato...); o nome também sai de dentro do e-mail
    (edson@... -> Edson; contato@nubank.com.br -> Nubank) já formatado;
  * corpo da mensagem: o texto a enviar ("dizendo que ...", "avisando que ...", ": ...");
  * atalho opcional de envio: #destino &texto& (ex.: "#edson@x.com &chego às 10h&", "#Ana &tô chegando&").

    from djorge.runtime.recognizers import reconhecer, combinar, atalho_envio, corpo_mensagem
"""
from __future__ import annotations

import datetime as _dt
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class Trecho:
    ini: int
    fim: int
    campo: str
    texto: str
    valor: Optional[str] = None          # forma normalizada (ISO para data/hora, dígitos para telefone, nome formatado)
    p: float = 1.0                       # certeza: regra = precisão média da regra; modelo = probabilidade calibrada
    fonte: str = "regra"
    extra: Dict = field(default_factory=dict)


def _sem_acento(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


DIAS = {"segunda": 0, "segunda-feira": 0, "terca": 1, "terca-feira": 1, "quarta": 2, "quarta-feira": 2,
        "quinta": 3, "quinta-feira": 3, "sexta": 4, "sexta-feira": 4, "sabado": 5, "domingo": 6}
MESES = {"janeiro": 1, "fevereiro": 2, "marco": 3, "abril": 4, "maio": 5, "junho": 6, "julho": 7, "agosto": 8,
         "setembro": 9, "outubro": 10, "novembro": 11, "dezembro": 12, "jan": 1, "fev": 2, "mar": 3, "abr": 4,
         "mai": 5, "jun": 6, "jul": 7, "ago": 8, "set": 9, "out": 10, "nov": 11, "dez": 12}
NUM = {"uma": 1, "um": 1, "duas": 2, "dois": 2, "tres": 3, "quatro": 4, "cinco": 5, "seis": 6, "sete": 7, "oito": 8,
       "nove": 9, "dez": 10, "onze": 11, "doze": 12}
_D = r"(?:segunda|ter[cç]a|quarta|quinta|sexta)(?:-feira)?|s[aá]bado|domingo"
_M = r"janeiro|fevereiro|mar[cç]o|abril|maio|junho|julho|agosto|setembro|outubro|novembro|dezembro"
_NH = r"uma|duas|tr[eê]s|quatro|cinco|seis|sete|oito|nove|dez|onze|doze"

# (campo, certeza média, regex) - a ordem decide quem fica com um trecho disputado entre regras
PADROES = [
    ("endereço de e-mail", 0.99, r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"),
    ("url", 0.97, r"(?:https?://|www\.)[^\s<>\"'&]+|\b[\w-]+\.(?:com|net|org|br|io|ly|me|app|gov|edu)(?:\.br)?/[^\s<>\"'&]*"),
    ("boleto", 0.97, r"\b\d{5}[.\s]?\d{5}\s?\d{5}[.\s]?\d{6}\s?\d{5}[.\s]?\d{6}\s?\d\s?\d{14}\b|\b(?:\d{11}[\s-]?\d\s?){4}|\b\d{47,48}\b"),
    ("rastreio", 0.95, r"\b[A-Z]{2}\d{9}[A-Z]{2}\b"),
    ("cpf", 0.98, r"\b\d{3}\.\d{3}\.\d{3}-\d{2}\b"),
    ("cnpj", 0.98, r"\b\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}\b"),
    ("chave pix", 0.9, r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b"),
    ("telefone", 0.9, r"(?:\+?55\s?)?\(?\b\d{2}\)?\s?9?\d{4}[-\s]?\d{4}\b"),
    ("cep", 0.9, r"\b\d{5}-\d{3}\b"),
    ("código", 0.9, r"(?i:c[oó]digo|code|token|senha tempor[aá]ria|otp)\D{0,25}\b(\d{4,8})\b"),
    ("valor", 0.95, r"R\$\s?\d[\d.]*(?:,\d{2})?|\b\d[\d.]*(?:,\d{1,2})?\s?(?:reais|d[oó]lares|euros)\b"),
    ("percentual", 0.97, r"\b\d+(?:,\d+)?\s?%"),
    ("frequência ou repetição", 0.9, rf"\b(?:tod[ao]s?\s+(?:as?\s+|os?\s+)?(?:dia|dias|semana|m[eê]s|ano|{_D})s?(?:\s+[uú]teis)?"
                                     rf"|de\s+\d+\s+em\s+\d+\s+(?:dias|horas|semanas)|dia\s+sim,?\s+dia\s+n[aã]o|diariamente"
                                     rf"|semanalmente|mensalmente)\b"),
    ("data", 0.97, r"\b(?:hoje|amanh[aã]|depois\s+de\s+amanh[aã]|ontem|anteontem)\b"),
    ("data", 0.95, rf"\b(?:dia\s+)?\d{{1,2}}\s+de\s+(?:{_M})(?:\s+de\s+\d{{4}})?\b|\b\d{{1,2}}/\d{{1,2}}(?:/\d{{2,4}})?\b"
                   rf"|\b\d{{4}}-\d{{2}}-\d{{2}}\b|\bdaqui\s+a\s+\d+\s+(?:dias|semanas|meses)\b"),
    ("data", 0.9, r"\b(?:(?:semana|m[eê]s|ano)\s+que\s+vem|pr[oó]xim[ao]\s+(?:semana|m[eê]s|ano)|fim\s+de\s+semana)\b"),
    ("data", 0.85, rf"\b(?:(?:n[ao]|nest[ae]|pr[oó]xim[ao]|est[ae])\s+)(?:{_D})(?:\s+que\s+vem)?\b|\b(?:{_D})(?:-feira|\s+que\s+vem)\b"),
    ("data", 0.8, r"\bdia\s+\d{1,2}\b"),
    ("data", 0.7, rf"\b(?:{_D})\b"),                     # "segunda" sozinha pode ser "segunda vez"
    ("hora", 0.97, r"\b\d{1,2}(?::\d{2}|h\d{2}|h)(?!\w)|\bmeio-dia\b|\bmeia-noite\b"),
    ("hora", 0.93, rf"\b(?:\d{{1,2}}|{_NH})\s+(?:e\s+meia\s+)?da\s+(?:manh[aã]|tarde|noite)\b|\b(?:{_NH})\s+e\s+meia\b"),
    ("hora", 0.75, r"\b\d{1,2}\s+horas\b"),               # "8 horas" pode ser duração
    ("período do dia", 0.9, r"\b(?:de\s+manh[aã]|pela\s+manh[aã]|[àa]\s+tarde|[àa]\s+noite|de\s+madrugada|cedinho"
                            r"|no\s+fim\s+da\s+(?:tarde|manh[aã])|na\s+hora\s+do\s+almo[cç]o)\b"),
    ("link de reunião", 0.97, r"https?://(?:meet\.google\.com|[\w.]*zoom\.us|teams\.microsoft\.com|teams\.live\.com)/[^\s<>\"']+"),
]
_RX = [(c, p, re.compile(rx, re.I if c not in ("rastreio",) else 0)) for c, p, rx in PADROES]

# e-mails: provedores pessoais e caixas genéricas (a parte antes do @ não é um nome)
PROVEDORES = {"gmail", "hotmail", "outlook", "yahoo", "live", "icloud", "uol", "bol", "terra", "ig", "proton",
              "protonmail", "msn", "me", "aol", "zoho", "gmx"}
CAIXAS = {"contato", "contact", "financeiro", "rh", "suporte", "support", "noreply", "no-reply", "naoresponda",
          "nao-responda", "atendimento", "vendas", "sac", "info", "admin", "comercial", "cobranca", "faturamento",
          "newsletter", "marketing", "ola", "hello", "hi", "equipe", "time", "jobs", "vagas", "secretaria", "diretoria",
          "pedidos", "news", "agenda", "noticias", "loja", "compras", "notificacoes", "alertas", "conta", "seguranca",
          "bot", "avisos", "billing", "orders", "notifications", "alerts", "team", "cobranca", "boletos", "nfe"}


def nome_do_email(email: str) -> Dict[str, Optional[str]]:
    """edson.souza@gmail.com -> {"entidade": "Edson Souza", "tipo": "pessoa"};
    contato@nubank.com.br -> {"entidade": "Nubank", "tipo": "empresa", "caixa": "contato"}."""
    local, _, dom = email.lower().partition("@")
    base = dom.split(".")[0]
    loc = re.sub(r"\d+$", "", local)
    partes = [x for x in re.split(r"[._\-+]+", loc) if x and not x.isdigit()]
    empresa = None if base in PROVEDORES else base.replace("-", " ").title()
    if loc in CAIXAS or (partes and partes[0] in CAIXAS):
        return {"entidade": empresa, "tipo": "empresa" if empresa else None, "caixa": loc}
    nomes = [x for x in partes if len(x) > 1 and x.isalpha()]
    if nomes and len(nomes) <= 4:
        return {"entidade": " ".join(x.capitalize() for x in nomes), "tipo": "pessoa", "empresa": empresa}
    return {"entidade": empresa, "tipo": "empresa" if empresa else None}


def _norm_data(t: str, hoje: _dt.date) -> Optional[str]:
    s = _sem_acento(t.casefold())
    rel = {"depois de amanha": 2, "anteontem": -2, "amanha": 1, "ontem": -1, "hoje": 0}
    for k, d in rel.items():
        if k in s:
            return (hoje + _dt.timedelta(d)).isoformat()
    m = re.search(r"daqui a (\d+) (dias|semanas|meses)", s)
    if m:
        n = int(m.group(1)) * (7 if m.group(2) == "semanas" else 30 if m.group(2) == "meses" else 1)
        return (hoje + _dt.timedelta(n)).isoformat()
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        return m.group(0)

    def futuro(y, mo, d, explicito):
        try:
            x = _dt.date(y, mo, d)
        except ValueError:
            return None
        if not explicito and x < hoje:
            try:
                x = _dt.date(y + 1, mo, d)
            except ValueError:
                return None
        return x.isoformat()
    m = re.search(r"(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?", s)
    if m:
        y = int(m.group(3)) if m.group(3) else hoje.year
        return futuro(y + 2000 if y < 100 else y, int(m.group(2)), int(m.group(1)), bool(m.group(3)))
    m = re.search(r"(\d{1,2}) de (\w+)(?: de (\d{4}))?", s)
    if m and m.group(2) in MESES:
        return futuro(int(m.group(3) or hoje.year), MESES[m.group(2)], int(m.group(1)), bool(m.group(3)))
    m = re.search(r"dia (\d{1,2})", s)
    if m:
        d = int(m.group(1))
        x = futuro(hoje.year, hoje.month, d, True)
        if x is None or x < hoje.isoformat():
            ny, nm = (hoje.year + 1, 1) if hoje.month == 12 else (hoje.year, hoje.month + 1)
            x = futuro(ny, nm, d, True)
        return x
    for nome, wd in DIAS.items():
        if re.search(rf"\b{nome}\b", s):
            delta = (wd - hoje.weekday()) % 7 or 7
            return (hoje + _dt.timedelta(delta)).isoformat()
    if "semana que vem" in s or "proxima semana" in s:
        return (hoje + _dt.timedelta(7 - hoje.weekday())).isoformat()
    if "mes que vem" in s or "proximo mes" in s:
        return (_dt.date(hoje.year + (hoje.month == 12), hoje.month % 12 + 1, 1)).isoformat()
    if "fim de semana" in s:
        return (hoje + _dt.timedelta((5 - hoje.weekday()) % 7)).isoformat()
    return None


def _norm_hora(t: str, contexto: str = "") -> Optional[str]:
    s = _sem_acento(t.casefold())
    if "meio-dia" in s:
        return "12:00"
    if "meia-noite" in s:
        return "00:00"
    m = re.match(r"(\d{1,2})(?:(?::|h)(\d{2})?|\s+horas|\s+(?:e meia\s+)?da\s)", s)
    if m:
        h, mi = int(m.group(1)), int(m.group(2) or 0)
        if "e meia" in s:
            mi = 30
    else:
        w = s.split()[0]
        if w not in NUM:
            return None
        h, mi = NUM[w], 30 if "meia" in s else 0
    ctx = s + " " + _sem_acento(contexto.casefold())[:25]
    if h < 12 and ("tarde" in ctx or "noite" in ctx) and "manha" not in s:
        h += 12
    return f"{h:02d}:{mi:02d}" if h < 24 and mi < 60 else None


def reconhecer(texto: str, hoje: Optional[_dt.date] = None) -> List[Trecho]:
    hoje = hoje or _dt.date.today()
    ocupado = [False] * len(texto)
    out = []
    for campo, p, rx in _RX:
        for m in rx.finditer(texto):
            a, b = m.span(1) if (campo == "código" and m.lastindex) else m.span()
            if a == b or any(ocupado[a:b]):
                continue
            t = Trecho(a, b, campo, texto[a:b], p=p)
            if campo == "data":
                t.valor = _norm_data(t.texto, hoje)
            elif campo == "hora":
                t.valor = _norm_hora(t.texto, texto[b:b + 25])
            elif campo in ("telefone", "boleto", "cpf", "cnpj", "cep"):
                t.valor = re.sub(r"\D", "", t.texto)
            elif campo == "endereço de e-mail":
                t.valor = t.texto.lower()
                info = nome_do_email(t.texto)
                t.extra = info
                if info.get("entidade"):     # a entidade de dentro do e-mail, já formatada
                    out.append(Trecho(a, a + len(t.texto.split("@")[0]) if info.get("tipo") == "pessoa" else b,
                                      "entidade", t.texto, valor=info["entidade"], p=0.85 if info["tipo"] == "pessoa" else 0.8,
                                      extra={"tipo": info["tipo"], "de": "e-mail"}))
            elif campo == "valor":
                v = re.sub(r"[^\d,]", "", t.texto).replace(",", ".")
                t.valor = v if v else None
            out.append(t)
            for i in range(a, b):
                ocupado[i] = True
    return sorted(out, key=lambda t: (t.ini, t.campo != "entidade"))


# ------------------------------------------------------------------ corpo da mensagem e atalho de envio --
_ATALHO = re.compile(r"#(?P<dest>[^\s&#\[\]]+(?:\s+[A-ZÀ-Ý][\wÀ-ÿ]+)?)\s*(?:\[?\s*&(?P<corpo>.*?)&\s*\]?)?", re.S)
_CORPO = re.compile(r"\b(?:dizendo|avisando|falando|explicando|contando|perguntando|respondendo|lembrando)\s+(?:que|se)\s+"
                    r"(?P<c>.+)$|\b(?:que|:)\s*(?P<c2>.+)$", re.I | re.S)


def atalho_envio(texto: str) -> Optional[Dict]:
    """Atalho opcional: '#destino &texto&' (os colchetes são aceitos: '#a@b.com[ &oi&]'). -> {destino, corpo}."""
    m = _ATALHO.search(texto)
    if not m:
        return None
    dest = m.group("dest").strip()
    corpo = (m.group("corpo") or "").strip() or None
    if corpo is None:                                   # '#Ana chego às 10h' -> o resto é o corpo
        resto = texto[m.end():].strip()
        corpo = resto or None
    info = nome_do_email(dest) if "@" in dest else {"entidade": dest, "tipo": None}
    return {"destino": dest, "entidade": info.get("entidade"), "corpo": corpo, "p": 1.0}


_ENVIO = re.compile(r"\b(manda|mande|envia|envie|avisa|avise|escreve|escreva|fala|fale|diz|diga|responde|responda|"
                    r"pergunta|pergunte|lembra|manda|posta|poste|comunica|informa)\b", re.I)


def corpo_mensagem(texto: str) -> Optional[Trecho]:
    """O texto a ser enviado num pedido de mensagem/e-mail ('manda pra Ana dizendo que vou atrasar')."""
    if not _ENVIO.search(texto):
        return None
    m = _CORPO.search(texto)
    if not m:
        return None
    g = "c" if m.group("c") else "c2"
    a, b = m.span(g)
    corpo = texto[a:b].strip().rstrip(".")
    if len(corpo) < 2:
        return None
    return Trecho(a, a + len(corpo), "corpo", corpo, valor=corpo[0].upper() + corpo[1:], p=0.92 if g == "c" else 0.8)


# ------------------------------------------------------------------ combinação regras + modelo --
NUNCA = set("""me mim eu tu voce você ele ela nos nós a o as os um uma uns umas de da do das dos em na no nas nos pra pro
para por com que e ou se manda mande mandar marca marque marcar cria crie criar coloca coloque colocar bota põe poe
lembra lembre lembrar agenda agende agendar avisa avise avisar envia envie enviar escreve escreva responde responda
procura procure busca busque acha ache apaga apague tira tire remove remova cancela cancele desmarca remarca muda mude
anota anote adiciona adicione mostra mostre fala fale faz faça quanto quanta qual quais quando onde como dizendo
falando avisando perguntando pagar ligar tomar levar comprar mensagem e-mail email zap whatsapp lembrete alarme lista
tarefa reunião reuniao favor oi olá ola bom boa dia tarde noite obrigado obrigada valeu""".split())
FORMA = {"data", "hora", "período do dia", "frequência ou repetição", "endereço de e-mail", "telefone", "url",
         "valor", "percentual", "cpf", "cnpj", "cep", "boleto", "rastreio", "chave pix", "código", "link de reunião"}
ENTIDADE = {"pessoa", "empresa ou estabelecimento", "entidade"}


def combinar(texto: str, modelo: List[Trecho], regras: List[Trecho], limiar: float = 0.6) -> List[Trecho]:
    """Junta regras (com certeza própria) e modelo (probabilidade). Num trecho disputado vence a maior certeza;
    palavras de comando nunca viram campo; pessoa/empresa do modelo viram 'entidade' com o tipo ao lado."""
    cands = []
    for t in modelo:
        if t.p < limiar:
            continue
        palavra = texto[t.ini:t.fim].strip(" ,.!?:;").casefold()
        if not palavra or palavra in NUNCA or all(w in NUNCA for w in palavra.split()):
            continue
        if t.campo in ENTIDADE:
            t = Trecho(t.ini, t.fim, "entidade", t.texto, valor=t.texto.strip(" ,.!?:;"), p=t.p, fonte="modelo",
                       extra={"tipo": "pessoa" if t.campo == "pessoa" else "empresa" if t.campo != "entidade" else None})
        t.fonte = "modelo"
        cands.append(t)
    # o modelo concorda com uma regra no mesmo campo -> a certeza da regra sobe (as duas fontes concordam)
    for r in regras:
        for t in cands:
            if t.campo == r.campo and t.ini < r.fim and r.ini < t.fim:
                r.p = round(1 - (1 - r.p) * (1 - t.p), 4)
                r.extra["confirmado_pelo_modelo"] = True
    todos = [(r.p, 1, r) for r in regras] + [(t.p, 0, t) for t in cands
                                              if not any(t.campo == r.campo and t.ini < r.fim and r.ini < t.fim for r in regras)]
    finais, tomados = [], []
    for _, _, t in sorted(todos, key=lambda x: (-x[0], -x[1])):
        conflito = [f for f in finais if t.ini < f.fim and f.ini < t.fim]
        # a entidade de dentro do e-mail convive com o próprio e-mail
        if conflito and not all({t.campo, f.campo} == {"entidade", "endereço de e-mail"} for f in conflito):
            continue
        finais.append(t)
    return sorted(finais, key=lambda t: (t.ini, t.campo != "entidade"))
