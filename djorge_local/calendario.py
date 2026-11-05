"""Feriados, pontos facultativos e datas comemorativas do Brasil + conflitos de agenda e aniversários.

Puro Python, sem rede. API:
  datas(ano, uf=None) -> [{data, nome, tipo, nacional, alerta_sugerido}]
  proximas(agora, dias=30, uf=None)
  conflitos(card, contexto="", uf=None, engine=None) -> [{data, nome, motivo}]
  aniversarios(cards, agora, dias=0) -> [{data, nome, card_id, dias, idade?}]
  eh_dia_util(d, uf=None), dia_util_anterior(d, uf=None)
"""
import datetime as _dt
import re
import unicodedata
from typing import Dict, List, Optional

# Horário de verão: extinto pelo Decreto 9.772/2019 (último fim em fev/2019). Não há mudança de relógio.
HORARIO_DE_VERAO = {"ativo": False, "extinto_em": "2019-04-25", "norma": "Decreto 9.772/2019",
                    "nota": "Sem horário de verão no Brasil desde 2019; não ajustar relógios."}

_FIXOS = [  # (mês, dia, nome, tipo, alerta)
    (1, 1, "Confraternização Universal", "feriado", True),
    (4, 21, "Tiradentes", "feriado", True),
    (5, 1, "Dia do Trabalho", "feriado", True),
    (9, 7, "Independência do Brasil", "feriado", True),
    (10, 12, "Nossa Senhora Aparecida", "feriado", True),
    (11, 2, "Finados", "feriado", True),
    (11, 15, "Proclamação da República", "feriado", True),
    (11, 20, "Dia Nacional de Zumbi e da Consciência Negra", "feriado", True),  # nacional desde Lei 14.759/2023
    (12, 25, "Natal", "feriado", True),
    (10, 28, "Dia do Servidor Público", "facultativo", False),
    (12, 24, "Véspera de Natal", "facultativo", True),
    (12, 31, "Véspera de Ano Novo", "facultativo", True),
    (3, 8, "Dia Internacional da Mulher", "comemorativa", False),
    (3, 15, "Dia do Consumidor", "comemorativa", False),
    (4, 1, "Dia da Mentira", "comemorativa", False),
    (6, 12, "Dia dos Namorados", "comemorativa", True),
    (6, 24, "São João", "comemorativa", False),
    (7, 26, "Dia dos Avós", "comemorativa", False),
    (10, 12, "Dia das Crianças", "comemorativa", True),
    (10, 15, "Dia do Professor", "comemorativa", False),
    (10, 31, "Halloween", "comemorativa", False),
]

_ESTADUAIS = {  # UF -> [(mês, dia, nome)]
    "AC": [(1, 23, "Dia do Evangélico"), (6, 15, "Aniversário do Acre"), (9, 5, "Dia da Amazônia"), (11, 17, "Tratado de Petrópolis")],
    "AL": [(6, 24, "São João"), (6, 29, "São Pedro"), (9, 16, "Emancipação de Alagoas")],
    "AM": [(9, 5, "Elevação do Amazonas a Província")],
    "AP": [(3, 19, "São José"), (9, 13, "Criação do Território do Amapá")],
    "BA": [(7, 2, "Independência da Bahia")],
    "CE": [(3, 19, "São José"), (3, 25, "Data Magna do Ceará")],
    "DF": [(4, 21, "Fundação de Brasília"), (11, 30, "Dia do Evangélico")],
    "ES": [(10, 28, "Dia do Servidor Público")],
    "GO": [(10, 28, "Dia do Servidor Público")],
    "MA": [(7, 28, "Adesão do Maranhão à Independência")],
    "MG": [(4, 21, "Data Magna de Minas Gerais")],
    "MS": [(10, 11, "Criação do Mato Grosso do Sul")],
    "MT": [(11, 20, "Consciência Negra")],
    "PA": [(8, 15, "Adesão do Pará à Independência")],
    "PB": [(8, 5, "Fundação da Paraíba")],
    "PE": [(3, 6, "Revolução Pernambucana")],
    "PI": [(10, 19, "Dia do Piauí")],
    "PR": [(12, 19, "Emancipação do Paraná")],
    "RJ": [(4, 23, "Dia de São Jorge")],
    "RN": [(10, 3, "Mártires de Cunhaú e Uruaçu")],
    "RO": [(1, 4, "Criação de Rondônia"), (6, 18, "Dia do Evangélico")],
    "RR": [(10, 5, "Criação de Roraima")],
    "RS": [(9, 20, "Revolução Farroupilha")],
    "SC": [(8, 11, "Criação da Capitania de Santa Catarina")],
    "SE": [(7, 8, "Emancipação de Sergipe")],
    "SP": [(7, 9, "Revolução Constitucionalista")],
    "TO": [(10, 5, "Criação do Tocantins")],
}
UFS = set(_ESTADUAIS)


def _fold(s) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", str(s or "").lower()) if unicodedata.category(c) != "Mn")


def pascoa(ano: int) -> _dt.date:
    """Computus (algoritmo anônimo gregoriano / Meeus-Jones-Butcher)."""
    a, b, c = ano % 19, ano // 100, ano % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    mes = (h + l - 7 * m + 114) // 31
    dia = ((h + l - 7 * m + 114) % 31) + 1
    return _dt.date(ano, mes, dia)


def _nesimo_dom(ano, mes, n) -> _dt.date:
    d = _dt.date(ano, mes, 1)
    d += _dt.timedelta(days=(6 - d.weekday()) % 7)
    return d + _dt.timedelta(weeks=n - 1)


def _black_friday(ano) -> _dt.date:  # sexta após a 4ª quinta de novembro
    d = _dt.date(ano, 11, 1)
    d += _dt.timedelta(days=(3 - d.weekday()) % 7)
    return d + _dt.timedelta(weeks=3, days=1)


def _ent(d, nome, tipo, nacional=True, alerta=False, uf=None):
    e = {"data": d.isoformat(), "nome": nome, "tipo": tipo, "nacional": nacional, "alerta_sugerido": alerta}
    if uf:
        e["uf"] = uf
    return e


def datas(ano: int, uf: Optional[str] = None) -> List[Dict]:
    out = [_ent(_dt.date(ano, m, d), n, t, True, a) for m, d, n, t, a in _FIXOS]
    p = pascoa(ano)
    td = _dt.timedelta
    out += [
        _ent(p - td(days=48), "Carnaval (segunda-feira)", "facultativo", True, True),
        _ent(p - td(days=47), "Carnaval (terça-feira)", "facultativo", True, True),
        _ent(p - td(days=46), "Quarta-feira de Cinzas (até 14h)", "facultativo", True, False),
        _ent(p - td(days=2), "Sexta-feira Santa", "feriado", True, True),
        _ent(p, "Páscoa", "comemorativa", True, True),
        _ent(p + td(days=60), "Corpus Christi", "facultativo", True, True),
        _ent(_nesimo_dom(ano, 5, 2), "Dia das Mães", "comemorativa", True, True),
        _ent(_nesimo_dom(ano, 8, 2), "Dia dos Pais", "comemorativa", True, True),
        _ent(_black_friday(ano), "Black Friday", "comemorativa", True, False),
    ]
    u = (uf or "").upper().strip()
    for m, d, n in _ESTADUAIS.get(u, []):
        out.append(_ent(_dt.date(ano, m, d), n, "feriado", False, True, u))
    out.sort(key=lambda e: (e["data"], {"feriado": 0, "facultativo": 1, "comemorativa": 2}[e["tipo"]]))
    return out


def _d(v) -> Optional[_dt.date]:
    if isinstance(v, _dt.datetime):
        return v.date()
    if isinstance(v, _dt.date):
        return v
    if not v:
        return None
    try:
        return _dt.date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def do_dia(d, uf=None) -> List[Dict]:
    d = _d(d)
    return [e for e in datas(d.year, uf) if e["data"] == d.isoformat()] if d else []


def eh_feriado(d, uf=None, facultativo=False) -> bool:
    return any(e["tipo"] == "feriado" or (facultativo and e["tipo"] == "facultativo") for e in do_dia(d, uf))


def eh_dia_util(d, uf=None, facultativo=True) -> bool:
    """Dia útil bancário: seg-sex sem feriado (Carnaval/fim de ano tratados como fechados quando facultativo=True)."""
    d = _d(d)
    if d.weekday() >= 5 or eh_feriado(d, uf):
        return False
    if facultativo:  # bancos fecham no Carnaval e não abrem ao público em 31/12
        if any(e["tipo"] == "facultativo" and ("Carnaval" in e["nome"] or e["data"].endswith("12-31"))
               for e in do_dia(d, uf)):
            return False
    return True


def dia_util_anterior(d, uf=None) -> _dt.date:
    d = _d(d) - _dt.timedelta(days=1)
    while not eh_dia_util(d, uf):
        d -= _dt.timedelta(days=1)
    return d


def proximas(agora=None, dias: int = 30, uf: Optional[str] = None, tipos=None) -> List[Dict]:
    hoje = _d(agora) or _dt.date.today()
    fim = hoje + _dt.timedelta(days=dias)
    out = []
    for ano in range(hoje.year, fim.year + 1):
        for e in datas(ano, uf):
            d = _dt.date.fromisoformat(e["data"])
            if hoje <= d <= fim and (not tipos or e["tipo"] in tipos):
                out.append(dict(e, dias=(d - hoje).days))
    return out


# --- conflitos ---------------------------------------------------------------------------------

_PAGTO = re.compile(r"\b(boleto|pagar|pagamento|fatura|conta de|imposto|darf|ipva|iptu|ted|doc|transferencia|vencimento|vence)\b")
_TRAB = re.compile(r"\b(reuniao|entrega|prazo|deadline|cliente|apresentacao|relatorio|trabalho|expediente|plantao)\b")
_ESCOLA = re.compile(r"\b(escola|aula|creche|reuniao de pais|colegio)\b")
_SERVICO = re.compile(r"\b(cartorio|banco|agencia|inss|detran|poupatempo|receita|prefeitura|forum|correios|consulta|exame)\b")
_VIAGEM = re.compile(r"\b(viagem|voo|aeroporto|rodoviaria|estrada|viajar)\b")
NOUL_CONFLITO = "Esta data atrapalha este compromisso para esta pessoa?"


def _datas_card(card: Dict) -> List[_dt.date]:
    out = []
    for k in ("due", "start", "data", "date"):
        d = _d(card.get(k))
        if d and d not in out:
            out.append(d)
    return out


def conflitos(card: Dict, contexto: str = "", uf: Optional[str] = None, engine=None) -> List[Dict]:
    """Datas (feriado/véspera/ponte/Carnaval/fim de ano) que podem atrapalhar o card, dado o contexto do usuário."""
    try:
        from . import contexto_usuario as cu
        sin = cu.sinais(contexto or "")
    except Exception:
        sin = {}
    uf = (uf or sin.get("uf") or "").upper() or None
    txt = _fold(" ".join(str(card.get(k) or "") for k in ("title", "titulo", "text", "notes", "description")))
    pagto, trab = bool(_PAGTO.search(txt)), bool(_TRAB.search(txt))
    servico, escola, viagem = bool(_SERVICO.search(txt)), bool(_ESCOLA.search(txt)), bool(_VIAGEM.search(txt))
    trabalha_banco = sin.get("trabalho") == "banco"
    out: List[Dict] = []

    def add(d, nome, motivo, **extra):
        if not any(o["data"] == d.isoformat() and o["motivo"] == motivo for o in out):
            out.append(dict({"data": d.isoformat(), "nome": nome, "motivo": motivo}, **extra))

    for d in _datas_card(card):
        hoje = do_dia(d, uf)
        fer = [e for e in hoje if e["tipo"] == "feriado"]
        fac = [e for e in hoje if e["tipo"] == "facultativo"]
        nome = (fer or fac or [{"nome": ""}])[0]["nome"]
        if pagto and not eh_dia_util(d, uf):
            ant = dia_util_anterior(d, uf)
            motivo = (f"{nome}: bancos fechados" if nome else "fim de semana: bancos fechados")
            add(d, nome or "Fim de semana", motivo + f"; pague até {ant.strftime('%d/%m')} (dia útil anterior)",
                sugestao=ant.isoformat())
            continue
        if fer:
            if servico:
                add(d, nome, f"{nome} é feriado: o serviço pode estar fechado")
            elif trab or trabalha_banco:
                add(d, nome, f"{nome} é feriado: confirme se o compromisso de trabalho se mantém")
            elif escola:
                add(d, nome, f"{nome} é feriado: provavelmente sem aula")
            else:
                add(d, nome, f"cai no feriado de {nome}")
        elif fac and ("Carnaval" in nome or "Cinzas" in nome or d.month == 12):
            if trab or servico or trabalha_banco:
                add(d, nome, f"{nome}: muitos lugares fecham ou funcionam em horário reduzido")
        # véspera de feriado
        amanha = d + _dt.timedelta(days=1)
        fa = [e for e in do_dia(amanha, uf) if e["tipo"] == "feriado"]
        if fa and (viagem or trab):
            add(d, fa[0]["nome"], f"véspera de {fa[0]['nome']}: trânsito e saídas antecipadas")
        # ponte: feriado na terça (segunda é ponte) ou quinta (sexta é ponte)
        for delta, wd in ((1, 1), (-1, 3)):
            x = d + _dt.timedelta(days=delta)
            if d.weekday() in (0, 4) and x.weekday() == wd:
                fx = [e for e in do_dia(x, uf) if e["tipo"] == "feriado"]
                if fx and (trab or servico or escola):
                    add(d, fx[0]["nome"], f"provável ponte do feriado de {fx[0]['nome']}")
        if sin.get("filhos") and escola is False and d.month in (1, 7, 12) and trab:
            pass  # férias escolares: deixado para o modelo, evita alarme falso

    if engine is not None and out:
        try:
            from djorge.runtime.semantic import SemReq
            prefixo = ""
            try:
                from . import contexto_usuario as cu
                prefixo = cu.para_modelo(contexto or "")
            except Exception:
                pass
            reqs = [SemReq("NOUL", f"{prefixo}Compromisso: {card.get('title') or ''} em {o['data']} ({o['motivo']}).",
                           options=[NOUL_CONFLITO]) for o in out]
            res = engine.run(reqs)
            kept = []
            for o, r in zip(out, res):
                p = (r or {}).get("p_true")
                if p is None or p >= 0.35 or "sugestao" in o:  # boleto em dia sem banco: sempre mantém
                    kept.append(dict(o, p=p) if p is not None else o)
            out = kept
        except Exception:
            pass
    return out


# --- aniversários --------------------------------------------------------------------------------

_ANIV = re.compile(r"\banivers[aá]rio\s+(?:d[aeo]\s+)?(.+)", re.I)


def aniversarios(cards, agora=None, dias: int = 0) -> List[Dict]:
    """Cards/pessoas com aniversário entre hoje e hoje+dias. Aceita card['aniversario'|'nascimento'|'birthday']
    (YYYY-MM-DD ou MM-DD), pessoas em card['people'] com esses campos, ou título 'Aniversário de X' com start/due."""
    hoje = _d(agora) or _dt.date.today()
    out = []

    def mmdd(v):
        s = str(v or "").strip()
        m = re.fullmatch(r"(?:(\d{4})-)?(\d{2})-(\d{2})", s[:10])
        if not m:
            return None
        return (int(m.group(1)) if m.group(1) else None, int(m.group(2)), int(m.group(3)))

    def check(nome, v, cid):
        t = mmdd(v)
        if not t or not nome:
            return
        ano, mo, di = t
        for y in (hoje.year, hoje.year + 1):
            try:
                d = _dt.date(y, mo, di)
            except ValueError:  # 29/02 em ano não bissexto -> 28/02
                d = _dt.date(y, 2, 28)
            dd = (d - hoje).days
            if 0 <= dd <= dias:
                e = {"data": d.isoformat(), "nome": nome, "card_id": cid, "dias": dd}
                if ano:
                    e["idade"] = y - ano
                out.append(e)
                break

    for c in cards or []:
        cid = c.get("id")
        for k in ("aniversario", "nascimento", "birthday"):
            if c.get(k):
                check(c.get("nome") or c.get("title"), c[k], cid)
        for p in c.get("people") or []:
            if isinstance(p, dict):
                for k in ("aniversario", "nascimento", "birthday"):
                    if p.get(k):
                        check(p.get("nome"), p[k], cid)
        m = _ANIV.search(str(c.get("title") or ""))
        if m and not any(c.get(k) for k in ("aniversario", "nascimento", "birthday")):
            d = _d(c.get("start") or c.get("due"))
            if d:  # aniversário é anual: usa só mês/dia do card
                check(m.group(1).strip(" .!-")[:60], f"{d.month:02d}-{d.day:02d}", cid)
    seen, uniq = set(), []
    for e in sorted(out, key=lambda e: (e["dias"], e["nome"])):
        k = (e["nome"], e["data"])
        if k not in seen:
            seen.add(k)
            uniq.append(e)
    return uniq
