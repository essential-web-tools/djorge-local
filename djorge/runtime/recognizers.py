"""Reconhecedores leves (regras, sem modelo) e a combinação com os campos do DOFL. Fazem parte do MODELO (o app só
chama): o DOFL entende o pedido; as regras cuidam do que tem forma fixa e dão a cada achado uma CERTEZA própria.

  * cada regra tem uma certeza média (precisão esperada): e-mail 0,99 ... "segunda" sozinha 0,70 ("segunda vez");
    num conflito com o modelo vence quem tem mais certeza - uma regra fraca não atropela o modelo;
  * entidades: o campo padrão é "entidade" (pessoa, empresa, contato...); o nome também sai de dentro do e-mail
    (edson@... -> Edson; contato@nubank.com.br -> Nubank) já formatado;
  * datas e horas: "todo dia 20 às 9h", "toda primeira segunda do mês", "dias úteis", "daqui a 3 horas",
    "das 14h às 16h", "15 de nov", "semana que vem na terça", "fim do mês", "próximo dia útil", "às 3" (tarde);
  * recorrência normalizada ({"freq": "mensal", "dia": 20, "hora": "09:00"}) e a próxima ocorrência (proxima);
  * resolver_quando junta tudo num só {data, hora, fim, inicio, termino, recorrencia}; data_humana lê um valor solto
    ("03/11/2026 14h", "amanhã 9h") para quem precisa de ISO 8601;
  * corpo da mensagem: o texto a enviar ("dizendo que ...", "avisando que ...", ": ...");
  * atalho opcional de envio: #destino &texto& (ex.: "#edson@x.com &chego às 10h&", "#Ana &tô chegando&").

    from djorge.runtime.recognizers import reconhecer, combinar, atalho_envio, corpo_mensagem, resolver_quando, proxima
"""
from __future__ import annotations

import calendar as _cal
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
DIAS_NOME = ["segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo"]
MESES = {"janeiro": 1, "fevereiro": 2, "marco": 3, "abril": 4, "maio": 5, "junho": 6, "julho": 7, "agosto": 8,
         "setembro": 9, "outubro": 10, "novembro": 11, "dezembro": 12, "jan": 1, "fev": 2, "mar": 3, "abr": 4,
         "mai": 5, "jun": 6, "jul": 7, "ago": 8, "set": 9, "out": 10, "nov": 11, "dez": 12}
MESES_NOME = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro",
              "novembro", "dezembro"]
NUM = {"uma": 1, "um": 1, "duas": 2, "dois": 2, "tres": 3, "quatro": 4, "cinco": 5, "seis": 6, "sete": 7, "oito": 8,
       "nove": 9, "dez": 10, "onze": 11, "doze": 12, "quinze": 15, "vinte": 20, "trinta": 30, "meia": 0.5}
ORDINAL = {"primeira": 1, "primeiro": 1, "segunda": 2, "segundo": 2, "terceira": 3, "terceiro": 3, "quarta": 4,
           "quarto": 4, "quinta": 5, "quinto": 5, "ultima": -1, "ultimo": -1, "penultima": -2}
PERIODO_HORA = {"cedinho": "07:00", "cedo": "07:00", "madrugada": "05:00", "inicio da manha": "08:00",
                "manha": "09:00", "fim da manha": "11:00", "final da manha": "11:00", "almoco": "12:00",
                "inicio da tarde": "13:00", "meio da tarde": "15:00", "fim da tarde": "17:00",
                "final da tarde": "17:00", "tarde": "14:00", "inicio da noite": "18:00", "noite": "19:00"}
_D = r"(?:segunda|ter[cç]a|quarta|quinta|sexta)(?:-feira)?|s[aá]bado|domingo"
_DS = r"(?:(?:segunda|ter[cç]a|quarta|quinta|sexta)s?(?:-feiras?)?|s[aá]bados?|domingos?)"
_M = r"janeiro|fevereiro|mar[cç]o|abril|maio|junho|julho|agosto|setembro|outubro|novembro|dezembro"
_MA = _M + r"|jan|fev|mar|abr|mai|jun|jul|ago|set|out|nov|dez"
_NH = r"uma|duas|tr[eê]s|quatro|cinco|seis|sete|oito|nove|dez|onze|doze"
_HH = r"\d{1,2}(?::\d{2}|h\d{2}|h)?"
_ORD = r"primeir[ao]|segund[ao]|terceir[ao]|quart[ao]|quint[ao]|[uú]ltim[ao]|pen[uú]ltim[ao]"
_UN = r"dias?|semanas?|m[eê]s(?:es)?|anos?|horas?|minutos?|min"
# feriados nacionais de data fixa (e datas fixas muito usadas): nome sem acento -> (mês, dia)
FERIADOS = {"vespera de natal": (12, 24), "natal": (12, 25), "reveillon": (12, 31), "virada do ano": (12, 31),
            "ano novo": (1, 1), "confraternizacao universal": (1, 1), "tiradentes": (4, 21),
            "dia do trabalho": (5, 1), "dia do trabalhador": (5, 1), "independencia": (9, 7),
            "sete de setembro": (9, 7), "nossa senhora aparecida": (10, 12), "dia das criancas": (10, 12),
            "finados": (11, 2), "proclamacao da republica": (11, 15), "consciencia negra": (11, 20)}
_FER = (r"v[eé]spera\s+de\s+natal|natal|r[eé]veillon|virada\s+do\s+ano|ano\s+novo|confraterniza[cç][aã]o\s+universal|"
        r"tiradentes|dia\s+do\s+trabalh(?:o|ador)|independ[eê]ncia|sete\s+de\s+setembro|nossa\s+senhora\s+aparecida|"
        r"dia\s+das\s+crian[cç]as|finados|proclama[cç][aã]o\s+da\s+rep[uú]blica|consci[eê]ncia\s+negra")

# (campo, certeza média, regex) - a ordem decide quem fica com um trecho disputado entre regras
PADROES = [
    ("endereço de e-mail", 0.99, r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"),
    ("link de reunião", 0.97, r"https?://(?:meet\.google\.com|[\w.]*zoom\.us|teams\.microsoft\.com|teams\.live\.com)/[^\s<>\"']+"),
    ("url", 0.97, r"(?:https?://|www\.)[^\s<>\"'&]+|\b[\w-]+\.(?:com|net|org|br|io|ly|me|app|gov|edu)(?:\.br)?/[^\s<>\"'&]*"),
    ("boleto", 0.97, r"\b\d{5}[.\s]?\d{5}\s?\d{5}[.\s]?\d{6}\s?\d{5}[.\s]?\d{6}\s?\d\s?\d{14}\b|\b(?:\d{11}[\s-]?\d\s?){4}|\b\d{47,48}\b"),
    ("rastreio", 0.95, r"\b[A-Z]{2}\d{9}[A-Z]{2}\b"),
    ("cpf", 0.98, r"\b\d{3}\.\d{3}\.\d{3}-\d{2}\b"),
    ("cnpj", 0.98, r"\b\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}\b"),
    ("chave pix", 0.9, r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b"),
    ("telefone", 0.9, r"(?:\+?55\s?)?\(?\b\d{2}\)?\s?9?\d{4}[-\s]?\d{4}\b"),
    ("cep", 0.9, r"\b\d{5}-\d{3}\b"),
    ("código", 0.9, r"(?i:c[oó]digo|code|token|senha tempor[aá]ria|otp)\D{0,25}\b(\d{4,8})\b"),
    ("valor", 0.95, r"R\$\s?\d[\d.]*(?:,\d{1,2})?(?:\s?(?:mil|k|mi\b|milh[oõ]es))?|\b\d[\d.]*(?:,\d{1,2})?\s?(?:mil\s+)?"
                    r"(?:reais|d[oó]lares|euros)\b|\b\d+(?:,\d+)?\s?k\b"),
    ("valor", 0.75, r"\b\d+(?:,\d+)?\s+mil\b"),
    ("percentual", 0.97, r"\b\d+(?:,\d+)?\s?%"),
    # recorrência: as formas longas primeiro ("toda primeira segunda do mês" antes de "segunda")
    ("frequência ou repetição", 0.95, rf"\btod[ao]s?\s+(?:[ao]s?\s+)?(?:{_ORD})s?\s+(?:{_D})(?:\s+d[eo]\s+(?:cada\s+)?m[eê]s)?"),
    ("frequência ou repetição", 0.95, rf"\btod[ao]s?\s+(?:[ao]s?\s+)?dias?\s+\d{{1,2}}(?:\s+de\s+(?:cada\s+)?m[eê]s|\s+de\s+(?:{_MA})\b)?"),
    ("frequência ou repetição", 0.95, rf"\btod[ao]\s+(?:o\s+)?(?:fim|final|[uú]ltimo\s+dia)\s+d[eo]\s+m[eê]s|\bno\s+(?:fim|final)\s+de\s+todo\s+m[eê]s"),
    ("frequência ou repetição", 0.93, r"(?<!\d\s)\b(?:tod[ao]s?\s+(?:[ao]s?\s+)?)?(?:nos\s+|em\s+)?dias\s+[uú]teis\b"),
    ("frequência ou repetição", 0.93, rf"\ba\s+cada\s+(?:\d+|{_NH}|meia)?\s*(?:{_UN})\b|\bde\s+\d+\s+em\s+\d+\s+(?:{_UN})\b"),
    ("frequência ou repetição", 0.92, rf"\btod[ao]s?\s+(?:[ao]s?\s+)?{_DS}(?:\s*(?:,|e)\s*(?:[ao]s?\s+)?{_DS})*"),
    ("frequência ou repetição", 0.9, r"\b(?:tod[ao]s?\s+(?:[ao]s?\s+)?(?:dias?|semanas?|m[eê]s|meses|anos?|manh[aã]s?|tardes?|noites?)"
                                     r"|dia\s+sim,?\s+dia\s+n[aã]o|diariamente|semanalmente|quinzenalmente|mensalmente"
                                     r"|anualmente)\b"),
    # intervalos e durações
    ("hora", 0.96, rf"\bd[ao]s\s+(?:{_HH})\s+(?:[àa]s|at[ée](?:\s+[àa]s)?)\s+(?:{_HH})(?:\s+da\s+(?:manh[aã]|tarde|noite))?(?!\w)"
                   rf"|\b(?:de|entre)\s+\d{{1,2}}(?::\d{{2}}|h\d{{0,2}})\s+(?:[àa]s|a|e|at[ée])\s+\d{{1,2}}(?::\d{{2}}|h\d{{0,2}})(?!\w)"
                   rf"|\b\d{{1,2}}(?::\d{{2}}|h\d{{0,2}})\s*[-–]\s*\d{{1,2}}(?::\d{{2}}|h\d{{0,2}})(?!\w)"),
    ("hora", 0.95, rf"\b(?:daqui\s+a|em|dentro\s+de)\s+(?:\d+|{_NH}|meia)\s+(?:horas?|h|minutos?|min)(?:\s+e\s+meia)?\b"
                   r"|\bdaqui\s+a\s+(?:pouc[oa]|pouquinho)\b|\bj[aá]\s+j[aá]\b"),
    ("duração", 0.88, rf"\b(?:por|durante|dura(?:ndo)?|de)\s+(?:\d+(?:[,.]\d+)?|{_NH}|meia)\s+(?:horas?|h|minutos?|min)(?:\s+e\s+meia)?\b"
                      r"|\b\d+h\d{1,2}\s?min\b"),
    # datas
    ("data", 0.97, r"\b(?:hoje|amanh[aã]|depois\s+de\s+amanh[aã]|ontem|anteontem)\b"),
    ("data", 0.95, rf"\b(?:n[ao]\s+)?(?:{_D})\s+(?:d[ae]\s+|n[ae]\s+)?(?:semana\s+que\s+vem|pr[oó]xima\s+semana)\b"
                   rf"|\b(?:semana\s+que\s+vem|pr[oó]xima\s+semana),?\s+n[ao]\s+(?:{_D})\b"),
    ("data", 0.95, rf"\b(?:(?:n[ao]|para\s+o|pr[oó]|at[eé]\s+o)\s+)?(?:(?:dia|feriado)\s+d[aeo]s?\s+)?(?:{_FER})\b"),
    ("data", 0.95, r"\b(?:no\s+)?dia\s+\d{1,2}[º°]?\s+d[oe]\s+(?:m[eê]s\s+que\s+vem|pr[oó]ximo\s+m[eê]s)\b"),
    ("data", 0.95, r"\bpr[oó]ximo\s+dia\s+[uú]til\b|\b(?:no\s+)?(?:fim|final|in[ií]cio|come[cç]o|meio|metade)\s+do\s+m[eê]s"
                   r"(?:\s+que\s+vem)?\b"),
    ("data", 0.95, rf"\b(?:dia\s+)?\d{{1,2}}[º°]?\s+(?:de\s+)?(?:{_MA})\b\.?(?:\s+de\s+\d{{4}})?|\b\d{{1,2}}/\d{{1,2}}(?:/\d{{2,4}})?\b"
                   rf"|\b\d{{4}}-\d{{2}}-\d{{2}}\b|\bdaqui\s+a\s+(?:\d+|{_NH})\s+(?:dias|semanas|meses)\b"
                   rf"|\b(?:daqui\s+a|em)\s+(?:um|uma)\s+(?:semana|m[eê]s)\b"
                   rf"|\bem\s+(?:\d+|{_NH})\s+(?:dias|semanas|meses)\b"),
    ("data", 0.9, r"\b(?:(?:semana|m[eê]s|ano)\s+que\s+vem|pr[oó]xim[ao]\s+(?:semana|m[eê]s|ano)|fim\s+de\s+semana)\b"),
    ("data", 0.85, rf"\b(?:(?:n[ao]|nest[ae]|pr[oó]xim[ao]|est[ae])\s+)(?:{_D})(?:\s+que\s+vem)?\b|\b(?:{_D})(?:-feira|\s+que\s+vem)\b"),
    ("data", 0.8, r"\bdia\s+\d{1,2}(?:[º°]|\b)"),
    ("data", 0.7, rf"\b(?:{_D})\b"),                     # "segunda" sozinha pode ser "segunda vez"
    ("hora", 0.97, r"\b\d{1,2}(?::\d{2}|h\d{2}|h)(?!\w)|\bmeio[- ]dia(?:\s+e\s+meia)?\b|\bmeia[- ]noite\b"),
    ("hora", 0.93, rf"\b(?:\d{{1,2}}|{_NH})\s+(?:e\s+meia\s+)?da\s+(?:manh[aã]|tarde|noite|madrugada)\b|\b(?:{_NH})\s+e\s+meia\b"),
    ("hora", 0.75, r"\b\d{1,2}\s+horas\b"),               # "8 horas" pode ser duração
    ("hora", 0.8, r"\b(?:[àa]s|pelas|l[aá]\s+pelas)\s+\d{1,2}(?![\d/:,.%]|h|\s*(?:horas|reais|mil|pessoas|de\s+\w))"),
    ("período do dia", 0.9, r"\b(?:(?:no\s+)?(?:meio|in[ií]cio|come[cç]o|fim|final)\s+da\s+(?:tarde|manh[aã]|noite)"
                            r"|de\s+manh[aã]|pela\s+manh[aã]|[àa]\s+tarde|[àa]\s+noite|de\s+noite|de\s+tarde|de\s+madrugada"
                            r"|cedinho|cedo|(?:na\s+)?hora\s+do\s+almo[cç]o|no\s+almo[cç]o)\b"),
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


# ------------------------------------------------------------------ normalização de datas e horas --
def _n(w: str) -> Optional[float]:
    w = _sem_acento(w.casefold()).strip()
    if re.fullmatch(r"\d+(?:[.,]\d+)?", w):
        return float(w.replace(",", "."))
    return NUM.get(w)


def _ultimo_dia(y: int, m: int) -> int:
    return _cal.monthrange(y, m)[1]


def _mais_meses(d: _dt.date, n: int) -> _dt.date:
    m = d.month - 1 + n
    y, m = d.year + m // 12, m % 12 + 1
    return _dt.date(y, m, min(d.day, _ultimo_dia(y, m)))


def _proximo_util(d: _dt.date) -> _dt.date:
    d += _dt.timedelta(1)
    while d.weekday() >= 5:
        d += _dt.timedelta(1)
    return d


def _dia_semana(s: str) -> Optional[int]:
    for nome, wd in sorted(DIAS.items(), key=lambda x: -len(x[0])):
        if re.search(rf"\b{nome}s?\b", s):
            return wd
    return None


def _norm_data(t: str, hoje: _dt.date) -> Optional[str]:
    s = " ".join(_sem_acento(re.sub(r"(\d)[º°]", r"\1", t.casefold())).split())
    for nome, (mo, d) in sorted(FERIADOS.items(), key=lambda x: -len(x[0])):
        if re.search(rf"\b{nome}\b", s) and not re.search(r"\d", s):
            x = _dt.date(hoje.year, mo, d)
            return (x if x >= hoje else _dt.date(hoje.year + 1, mo, d)).isoformat()
    m = re.search(r"dia (\d{1,2}) d[oe] (?:mes que vem|proximo mes)", s)
    if m:
        base = _mais_meses(hoje.replace(day=1), 1)
        return base.replace(day=max(1, min(int(m.group(1)), _ultimo_dia(base.year, base.month)))).isoformat()
    m = re.search(r"(inicio|comeco|meio|metade) do mes( que vem)?", s)
    if m:
        dia = 1 if m.group(1) in ("inicio", "comeco") else 15
        base = _mais_meses(hoje.replace(day=1), 1) if m.group(2) else hoje.replace(day=1)
        x = base.replace(day=dia)
        if x < hoje and not m.group(2):
            x = _mais_meses(base, 1).replace(day=dia)
        return x.isoformat()
    if re.search(r"proximo dia util", s):
        return _proximo_util(hoje).isoformat()
    m = re.search(r"(?:fim|final) do mes( que vem)?", s)
    if m:
        base = _mais_meses(hoje.replace(day=1), 1) if m.group(1) else hoje
        return base.replace(day=_ultimo_dia(base.year, base.month)).isoformat()
    if re.search(r"semana que vem|proxima semana", s) and _dia_semana(s.replace("proxima semana", "")) is not None:
        wd = _dia_semana(re.sub(r"semana que vem|proxima semana", "", s))
        seg = hoje + _dt.timedelta(7 - hoje.weekday())
        return (seg + _dt.timedelta(wd)).isoformat()
    rel = {"depois de amanha": 2, "anteontem": -2, "amanha": 1, "ontem": -1, "hoje": 0}
    for k, d in rel.items():
        if k in s:
            return (hoje + _dt.timedelta(d)).isoformat()
    m = re.search(r"(?:daqui a|em) (\d+|\w+) (dias?|semanas?|meses|mes)", s)
    if m and _n(m.group(1)):
        n = int(_n(m.group(1)))
        if m.group(2).startswith("mes"):
            return _mais_meses(hoje, n).isoformat()
        return (hoje + _dt.timedelta(n * (7 if m.group(2).startswith("semana") else 1))).isoformat()
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        try:
            return _dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
        except ValueError:
            return None

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
    m = re.search(r"(\d{1,2}) (?:de )?([a-z]+)\.?(?: de (\d{4}))?", s)
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
    wd = _dia_semana(s)
    if wd is not None:
        delta = (wd - hoje.weekday()) % 7 or 7
        return (hoje + _dt.timedelta(delta)).isoformat()
    if "semana que vem" in s or "proxima semana" in s:
        return (hoje + _dt.timedelta(7 - hoje.weekday())).isoformat()
    if "mes que vem" in s or "proximo mes" in s:
        return (_dt.date(hoje.year + (hoje.month == 12), hoje.month % 12 + 1, 1)).isoformat()
    if "ano que vem" in s or "proximo ano" in s:
        return _dt.date(hoje.year + 1, 1, 1).isoformat()
    if "fim de semana" in s:
        return (hoje + _dt.timedelta((5 - hoje.weekday()) % 7)).isoformat()
    return None


_MANHA = re.compile(r"\b(?:manha|madrugada|cedo|cedinho)\b")
_TARDE = re.compile(r"\b(?:tarde|noite)\b")


def _hm(x: str, tarde: bool = False, manha: bool = False, nua: bool = False) -> Optional[str]:
    """'14h' '14:30' '9h15' '3' '3 e meia' -> 'HH:MM' (tarde/manhã pelo contexto; hora nua 1-6 é da tarde)."""
    s = _sem_acento(x.casefold()).strip()
    m = re.match(r"(\d{1,2})(?:(?::|h)(\d{2})?)?", s)
    if m:
        h, mi = int(m.group(1)), int(m.group(2) or 0)
        sufixo = bool(re.match(r"\d{1,2}(?::|h)", s))
    else:
        w = s.split()[0] if s.split() else ""
        if w not in NUM or w == "meia":
            return None
        h, mi, sufixo = int(NUM[w]), 0, False
    if re.search(r"\be meia\b", s):
        mi = 30
    if h == 12 and tarde and "noite" in s:
        h = 0
    elif h < 12 and tarde and not manha:
        h += 12
    elif nua and not sufixo and not manha and 1 <= h <= 6:
        h += 12
    return f"{h:02d}:{mi:02d}" if h < 24 and mi < 60 else None


def _norm_hora(t: str, contexto: str = "", antes: str = "") -> Optional[str]:
    s = _sem_acento(t.casefold())
    if "meio-dia" in s or "meio dia" in s:
        return "12:30" if "meia" in s else "12:00"
    if "meia-noite" in s or "meia noite" in s:
        return "00:00"
    s = re.sub(r"^(?:as|a|pelas|la pelas)\s+", "", s)
    ctx = s + " " + _sem_acento(contexto.casefold())[:25]
    tarde = bool(_TARDE.search(ctx))
    manha = bool(_MANHA.search(s)) or (bool(_MANHA.search(_sem_acento(contexto.casefold())[:25])) and not _TARDE.search(s))
    if _MANHA.search(_sem_acento(antes.casefold())[-25:]) and not _TARDE.search(ctx):
        manha = True
    return _hm(s, tarde=tarde and not _MANHA.search(s), manha=manha, nua=True)


def _duracao_min(t: str) -> Optional[int]:
    s = _sem_acento(t.casefold())
    m = re.search(r"(\d+)h(\d{1,2})", s)
    if m:
        return int(m.group(1)) * 60 + int(m.group(2))
    m = re.search(r"(\d+(?:[.,]\d+)?|[a-z]+)\s+(horas?|h|minutos?|min)", s)
    if not m or _n(m.group(1)) is None:
        return None
    n = _n(m.group(1)) * (60 if m.group(2).startswith("h") else 1)
    if "e meia" in s:
        n += 30
    return int(round(n))


def _num_str(x: float) -> str:
    return (f"{x:.2f}".rstrip("0").rstrip(".")) if x != int(x) else str(int(x))


def _norm_valor(t: str) -> Optional[str]:
    s = _sem_acento(t.casefold())
    mult = 1
    if re.search(r"\bmilh(?:oes|ao)\b|\bmi\b", s):
        mult = 1_000_000
    elif re.search(r"\bmil\b|\dk\b|\bk\b", s):
        mult = 1000
    num = re.search(r"\d[\d.]*(?:,\d+)?", s)
    if not num:
        return None
    v = num.group(0)
    if mult == 1:
        v = re.sub(r"[^\d,]", "", v).replace(",", ".")
        return v or None
    try:
        x = float(v.replace(".", "").replace(",", ".")) * mult
    except ValueError:
        return None
    return _num_str(x)


# ------------------------------------------------------------------ recorrência --
def recorrencia(texto: str, hoje: Optional[_dt.date] = None) -> Optional[Dict]:
    """'todo dia 20' -> {"freq": "mensal", "dia": 20}; 'toda primeira segunda do mês' -> {"freq": "mensal",
    "ordinal": 1, "dias_semana": [0]}; 'dias úteis' -> {"freq": "dias_uteis"}; 'a cada 2 semanas' ->
    {"freq": "semanal", "intervalo": 2}. A hora entra depois (resolver_quando / reconhecer)."""
    hoje = hoje or _dt.date.today()
    s = _sem_acento(texto.casefold())
    if re.search(r"dia sim,? dia nao", s):
        return {"freq": "diaria", "intervalo": 2}
    m = re.search(r"(?:a cada|de (\d+) em)\s*(\d+|[a-z]+)?\s*(dias?|semanas?|mes(?:es)?|anos?|horas?|minutos?|min)\b", s)
    if m:
        n = int(_n(m.group(1) or m.group(2) or "1") or 1) if (m.group(1) or (m.group(2) and _n(m.group(2)))) else 1
        un = m.group(3)
        freq = ("diaria" if un.startswith("dia") else "semanal" if un.startswith("semana") else
                "mensal" if un.startswith("mes") else "anual" if un.startswith("ano") else
                "horaria" if un.startswith("hora") else "minutos")
        r = {"freq": freq}
        if n != 1:
            r["intervalo"] = n
        return r
    if re.search(r"dias uteis", s):
        return {"freq": "dias_uteis"}
    m = re.search(rf"\b({'|'.join(ORDINAL)})s?\s+(segunda|terca|quarta|quinta|sexta|sabado|domingo)", s)
    if m and re.search(r"\btod[ao]s?\b", s):
        return {"freq": "mensal", "ordinal": ORDINAL[m.group(1)], "dias_semana": [DIAS[m.group(2)]]}
    if re.search(r"(?:fim|final|ultimo dia) d[eo] (?:todo )?mes", s):
        return {"freq": "mensal", "ultimo_dia": True}
    m = re.search(r"\btod[ao]s?\s+(?:[ao]s?\s+)?dias?\s+(\d{1,2})(?:\s+de\s+([a-z]+))?", s)
    if m:
        d = int(m.group(1))
        if m.group(2) in MESES:
            return {"freq": "anual", "dia": d, "mes": MESES[m.group(2)]}
        return {"freq": "mensal", "dia": d} if 1 <= d <= 31 else None
    if re.search(r"\btod[ao]s?\b", s):
        ds = sorted({DIAS[w] for w in re.findall(r"\b(segunda|terca|quarta|quinta|sexta|sabado|domingo)s?\b", s)})
        if ds:
            return {"freq": "semanal", "dias_semana": ds}
    if re.search(r"\btod[ao]s?\s+(?:[ao]s?\s+)?(?:manhas?|tardes?|noites?)\b", s):
        per = re.search(r"(manha|tarde|noite)", s).group(1)
        return {"freq": "diaria", "hora": PERIODO_HORA[per]}
    if re.search(r"\btod[ao]s?\s+(?:[ao]s?\s+)?dias?\b|\bdiariamente\b", s):
        return {"freq": "diaria"}
    if re.search(r"\bquinzenalmente\b", s):
        return {"freq": "semanal", "intervalo": 2}
    if re.search(r"\btod[ao]s?\s+(?:[ao]s?\s+)?semanas?\b|\bsemanalmente\b", s):
        return {"freq": "semanal", "dias_semana": [hoje.weekday()]}
    if re.search(r"\btod[ao]s?\s+(?:[ao]s?\s+)?(?:mes|meses)\b|\bmensalmente\b", s):
        return {"freq": "mensal", "dia": hoje.day}
    if re.search(r"\btod[ao]s?\s+(?:[ao]s?\s+)?anos?\b|\banualmente\b", s):
        return {"freq": "anual", "dia": hoje.day, "mes": hoje.month}
    return None


def _casa(rec: Dict, d: _dt.date, inicio: _dt.date) -> bool:
    f = rec.get("freq")
    n = int(rec.get("intervalo") or 1)
    if f == "diaria":
        return (d - inicio).days % n == 0
    if f == "dias_uteis":
        return d.weekday() < 5
    if f == "semanal":
        ds = rec.get("dias_semana") or [inicio.weekday()]
        semanas = ((d - _dt.timedelta(d.weekday())) - (inicio - _dt.timedelta(inicio.weekday()))).days // 7
        return d.weekday() in ds and semanas % n == 0
    if f == "mensal":
        meses = (d.year - inicio.year) * 12 + d.month - inicio.month
        if meses % n:
            return False
        ult = _ultimo_dia(d.year, d.month)
        if rec.get("ultimo_dia"):
            return d.day == ult
        if rec.get("ordinal"):
            ds = rec.get("dias_semana") or [0]
            if d.weekday() not in ds:
                return False
            o = rec["ordinal"]
            return (d.day - 1) // 7 + 1 == o if o > 0 else (ult - d.day) // 7 + 1 == -o
        return d.day == min(int(rec.get("dia") or inicio.day), ult)
    if f == "anual":
        mes, dia = int(rec.get("mes") or inicio.month), int(rec.get("dia") or inicio.day)
        return d.month == mes and d.day == min(dia, _ultimo_dia(d.year, mes))
    return False


def proxima(rec: Optional[Dict], agora: Optional[_dt.datetime] = None) -> Optional[_dt.datetime]:
    """A próxima ocorrência (datetime sem fuso) de uma recorrência normalizada; sem hora, a data às 00:00
    (vale o próprio dia de hoje)."""
    if not rec or not rec.get("freq"):
        return None
    agora = agora or _dt.datetime.now()
    f = rec["freq"]
    n = int(rec.get("intervalo") or 1)
    if f == "horaria":
        return (agora + _dt.timedelta(hours=n)).replace(second=0, microsecond=0)
    if f == "minutos":
        return (agora + _dt.timedelta(minutes=n)).replace(second=0, microsecond=0)
    hora = rec.get("hora")
    hh, mm = (int(x) for x in hora.split(":")) if hora else (0, 0)
    hoje = agora.date()
    for i in range(0, 1500):
        d = hoje + _dt.timedelta(i)
        if not _casa(rec, d, hoje):
            continue
        quando = _dt.datetime(d.year, d.month, d.day, hh, mm)
        if (hora and quando > agora) or (not hora and d >= hoje):
            return quando
    return None


def descrever_recorrencia(rec: Optional[Dict]) -> str:
    """{"freq": "mensal", "dia": 20, "hora": "09:00"} -> 'todo dia 20 às 09:00'."""
    if not rec:
        return ""
    f, n = rec.get("freq"), int(rec.get("intervalo") or 1)
    ds = [DIAS_NOME[i] for i in rec.get("dias_semana") or []]
    junta = lambda xs: xs[0] if len(xs) == 1 else ", ".join(xs[:-1]) + " e " + xs[-1]
    un = {"diaria": ("dia", "dias"), "semanal": ("semana", "semanas"), "mensal": ("mês", "meses"),
          "anual": ("ano", "anos"), "horaria": ("hora", "horas"), "minutos": ("minuto", "minutos")}
    if f == "dias_uteis":
        t = "dias úteis"
    elif f == "mensal" and rec.get("ultimo_dia"):
        t = "todo fim de mês"
    elif f == "mensal" and rec.get("ordinal"):
        o = {1: "primeira", 2: "segunda", 3: "terceira", 4: "quarta", 5: "quinta", -1: "última", -2: "penúltima"}
        nome = ds[0] if ds else "segunda"
        art = "todo" if nome in ("sábado", "domingo") else "toda"
        oo = o.get(rec["ordinal"], "primeira")
        if art == "todo":
            oo = oo[:-1] + "o"
        t = f"{art} {oo} {nome} do mês"
    elif f == "mensal" and rec.get("dia") and n == 1:
        t = f"todo dia {rec['dia']}"
    elif f == "anual" and rec.get("dia") and rec.get("mes"):
        t = f"todo dia {rec['dia']} de {MESES_NOME[rec['mes'] - 1]}"
    elif f == "semanal" and ds and n == 1:
        t = ("todo " if ds[0] in ("sábado", "domingo") else "toda ") + junta(ds)
    elif n > 1 and f in un:
        t = f"a cada {n} {un[f][1]}"
    elif f in un:
        t = {"diaria": "todo dia", "semanal": "toda semana", "mensal": "todo mês", "anual": "todo ano",
             "horaria": "a cada hora", "minutos": "a cada minuto"}[f]
    else:
        t = str(f)
    if rec.get("hora") and f not in ("horaria", "minutos"):
        t += f" às {rec['hora']}"
    return t


# ------------------------------------------------------------------ reconhecer --
def reconhecer(texto: str, hoje: Optional[_dt.date] = None, agora: Optional[_dt.datetime] = None) -> List[Trecho]:
    if agora is None:
        agora = _dt.datetime.now().replace(second=0, microsecond=0)
        if hoje is not None:
            agora = _dt.datetime.combine(hoje, agora.time())
    hoje = hoje or agora.date()
    ocupado = [False] * len(texto)
    out = []
    for campo, p, rx in _RX:
        for m in rx.finditer(texto):
            a, b = m.span(1) if (campo == "código" and m.lastindex) else m.span()
            if a == b or any(ocupado[a:b]):
                continue
            t = Trecho(a, b, campo, texto[a:b], p=p)
            low = _sem_acento(t.texto.casefold())
            if campo == "data":
                t.valor = _norm_data(t.texto, hoje)
            elif campo == "hora":
                if re.match(r"(?:daqui a|em|dentro de)\s|ja ja\b", low):        # "daqui a 3 horas" -> hora e data
                    mins = (15 if "pouquinho" in low else 30) if re.search(r"pou[cq]|^ja ja", low) else _duracao_min(low)
                    if mins is None:
                        continue
                    w = agora + _dt.timedelta(minutes=mins)
                    t.valor = w.strftime("%H:%M")
                    t.extra = {"data": w.date().isoformat(), "relativo_min": mins}
                elif re.match(r"(?:das?|dos?|de|entre)\s|\d", low) and re.search(r"\s(?:as|a|ate|e)\s|[-–]", low) \
                        and len(re.findall(r"\d{1,2}", low)) >= 2 and not re.match(r"\d{1,2}\s+(?:e meia\s+)?da\s", low):
                    hs = re.findall(r"\d{1,2}(?::\d{2}|h\d{2}|h)?", low)
                    ctx = low + " " + _sem_acento(texto[b:b + 25].casefold())
                    tarde, manha = bool(_TARDE.search(ctx)), bool(_MANHA.search(ctx))
                    ini, fim = _hm(hs[0], tarde, manha, True), _hm(hs[1], tarde, manha, True)
                    if ini and fim and fim < ini and int(fim[:2]) < 12:
                        fim = f"{int(fim[:2]) + 12:02d}{fim[2:]}"
                    if not ini:
                        continue
                    t.valor, t.extra = ini, {"fim": fim, "intervalo": True}
                else:
                    t.valor = _norm_hora(t.texto, texto[b:b + 25], texto[max(0, a - 25):a])
            elif campo == "duração":
                v = _duracao_min(t.texto)
                if v is None:
                    continue
                t.valor = str(v)
                t.extra = {"minutos": v}
            elif campo == "frequência ou repetição":
                rec = recorrencia(t.texto, hoje)
                t.extra = {"recorrencia": rec} if rec else {}
                t.valor = descrever_recorrencia(rec) if rec else None
            elif campo == "período do dia":
                k = next((k for k in sorted(PERIODO_HORA, key=len, reverse=True) if k in low), None)
                t.valor = PERIODO_HORA.get(k) if k else None
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
                t.valor = _norm_valor(t.texto)
            out.append(t)
            for i in range(a, b):
                ocupado[i] = True
    out.sort(key=lambda t: (t.ini, t.campo != "entidade"))
    # a hora (ou o período) ao lado da recorrência entra nela: "todo dia 20 às 9h", "dias úteis às 8h"
    for r in out:
        rec = (r.extra or {}).get("recorrencia")
        if not rec or rec.get("hora"):
            continue
        viz = [h for h in out if h.campo in ("hora", "período do dia") and h.valor
               and not (h.extra or {}).get("relativo_min")
               and (0 <= h.ini - r.fim <= 12 or 0 <= r.ini - h.fim <= 12)]
        if viz:
            h = sorted(viz, key=lambda h: (h.campo != "hora", abs(h.ini - r.fim)))[0]
            rec["hora"] = h.valor
            r.valor = descrever_recorrencia(rec)
    return out


def resolver_quando(texto: str, trechos: Optional[List[Trecho]] = None, agora: Optional[_dt.datetime] = None) -> Dict:
    """Junta datas, horas, período, intervalo, duração e recorrência num só quando:
    {"data", "hora", "fim", "duracao_min", "recorrencia", "recorrencia_txt", "inicio", "termino", "achou"}.
    Hora sem data: hoje se ainda não passou, senão amanhã; recorrência sem data: a próxima ocorrência."""
    agora = agora or _dt.datetime.now().replace(second=0, microsecond=0)
    hoje = agora.date()
    ts = trechos if trechos is not None else reconhecer(texto, hoje, agora)
    get = lambda c: [t for t in ts if t.campo == c and t.valor]
    data = next((t.valor for t in get("data")), None)
    d0 = next((t for t in get("data")), None)                 # "até sexta" = prazo, não começo
    prazo = bool(d0 and (re.search(r"(?i)\bat[eé]\s+(?:o\s+|a\s+|no\s+|na\s+|dia\s+)?$", texto[max(0, d0.ini - 12):d0.ini])
                         or re.match(r"(?i)at[eé]\s", d0.texto)))
    horas = get("hora")
    hora = next((t.valor for t in horas if not (t.extra or {}).get("relativo_min")), None)
    rel = next((t for t in horas if (t.extra or {}).get("relativo_min")), None)
    fim = next(((t.extra or {}).get("fim") for t in horas if (t.extra or {}).get("fim")), None)
    if rel is not None and not hora:
        hora = rel.valor
        data = data or rel.extra.get("data")
    if not hora and get("período do dia"):
        hora = get("período do dia")[0].valor
    dur = next((int(t.valor) for t in get("duração")), None)
    rec = next(((t.extra or {}).get("recorrencia") for t in ts if (t.extra or {}).get("recorrencia")), None)
    if rec is not None:
        rec = dict(rec)
        if hora and not rec.get("hora") and rec.get("freq") not in ("horaria", "minutos"):
            rec["hora"] = hora
        if not data:
            nx = proxima(rec, agora)
            if nx:
                data = nx.date().isoformat()
                if rec.get("hora"):
                    hora = rec["hora"]
                elif rec.get("freq") in ("horaria", "minutos"):
                    hora = nx.strftime("%H:%M")
    if hora and not data:
        data = hoje.isoformat() if hora > agora.strftime("%H:%M") else (hoje + _dt.timedelta(1)).isoformat()
    inicio = (f"{data}T{hora}" if hora else data) if data else None
    termino = None
    if inicio and hora:
        a = _dt.datetime.fromisoformat(inicio)
        if fim:
            e = _dt.datetime.fromisoformat(f"{data}T{fim}")
            termino = (e if e > a else e + _dt.timedelta(1)).isoformat(timespec="minutes")
        elif dur:
            termino = (a + _dt.timedelta(minutes=dur)).isoformat(timespec="minutes")
    return {"data": data, "hora": hora, "fim": fim, "duracao_min": dur, "recorrencia": rec,
            "recorrencia_txt": descrever_recorrencia(rec), "inicio": inicio, "termino": termino,
            "achou": bool(data or hora or rec), "prazo": prazo}


def data_humana(valor, agora: Optional[_dt.datetime] = None) -> Optional[str]:
    """Um valor de data solto -> ISO 8601 ('2026-11-03', '2026-11-03T14:00') ou None se não der para entender.
    Aceita ISO, '2026-11-03 14:00', '03/11/2026 14h', '3 de novembro às 9h', 'amanhã 9h', 'sexta 15h'."""
    if valor in (None, ""):
        return None
    s = str(valor).strip()
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
            return _dt.date.fromisoformat(s).isoformat()
        d = _dt.datetime.fromisoformat(s.replace(" ", "T", 1).replace("Z", "+00:00"))
        return d.replace(tzinfo=None).isoformat(timespec="minutes" if d.second == 0 else "seconds")
    except ValueError:
        pass
    q = resolver_quando(s, agora=agora)
    if not q["data"]:
        return None
    return q["inicio"]


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
         "valor", "percentual", "cpf", "cnpj", "cep", "boleto", "rastreio", "chave pix", "código", "link de reunião",
         "duração"}
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
