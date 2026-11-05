"""Contexto do usuário (texto curto das configurações) -> texto limpo, sinais por regra e prefixo p/ o DOFL."""
import re
import unicodedata
from typing import Dict

LIMITE = 400

_UF_NOMES = {"acre": "AC", "alagoas": "AL", "amapa": "AP", "amazonas": "AM", "bahia": "BA", "ceara": "CE",
             "distrito federal": "DF", "brasilia": "DF", "espirito santo": "ES", "goias": "GO", "maranhao": "MA",
             "mato grosso do sul": "MS", "mato grosso": "MT", "minas gerais": "MG", "minas": "MG", "para": "PA",
             "paraiba": "PB", "parana": "PR", "pernambuco": "PE", "piaui": "PI", "rio de janeiro": "RJ",
             "rio grande do norte": "RN", "rio grande do sul": "RS", "rondonia": "RO", "roraima": "RR",
             "santa catarina": "SC", "sao paulo": "SP", "sergipe": "SE", "tocantins": "TO"}
_CAPITAIS = {"sao paulo": "SP", "rio de janeiro": "RJ", "belo horizonte": "MG", "salvador": "BA", "recife": "PE",
             "fortaleza": "CE", "curitiba": "PR", "porto alegre": "RS", "florianopolis": "SC", "goiania": "GO",
             "manaus": "AM", "belem": "PA", "vitoria": "ES", "natal": "RN", "joao pessoa": "PB", "maceio": "AL",
             "aracaju": "SE", "teresina": "PI", "sao luis": "MA", "cuiaba": "MT", "campo grande": "MS",
             "palmas": "TO", "porto velho": "RO", "boa vista": "RR", "macapa": "AP", "rio branco": "AC",
             "brasilia": "DF", "campinas": "SP", "santos": "SP", "niteroi": "RJ"}
_SIGLAS = {"AC", "AL", "AP", "AM", "BA", "CE", "DF", "ES", "GO", "MA", "MT", "MS", "MG", "PA", "PB", "PR", "PE",
           "PI", "RJ", "RN", "RS", "RO", "RR", "SC", "SP", "SE", "TO"}
_TRABALHOS = [("banco", r"\b(banco|bancari[oa]|agencia bancaria)\b"),
              ("saude", r"\b(hospital|medic[oa]|enfermeir[oa]|plantao|clinica)\b"),
              ("educacao", r"\b(professor[a]?|escola|docente|leciono|dou aula)\b"),
              ("comercio", r"\b(loja|varejo|comercio|vendedor[a]?|shopping)\b"),
              ("servico_publico", r"\b(servidor[a]? publico|funcionari[oa] public[oa]|prefeitura|concursad[oa])\b"),
              ("autonomo", r"\b(autonom[oa]|freela|freelancer|mei|meu proprio negocio|empreendedor[a]?)\b"),
              ("ti", r"\b(programador[a]?|desenvolvedor[a]?|\bti\b|software)\b"),
              ("escritorio", r"\b(escritorio|empresa|clt)\b")]
_RELIG = [("catolica", r"\b(catolic[oa]|missa)\b"), ("evangelica", r"\b(evangelic[oa]|crente|culto)\b"),
          ("judaica", r"\b(judeu|judia|judaic[oa]|shabat)\b"), ("espirita", r"\bespirita\b"),
          ("matriz_africana", r"\b(umbanda|candomble)\b"), ("adventista", r"\badventista\b")]


def _fold(s) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", str(s or "").lower()) if unicodedata.category(c) != "Mn")


def normalizar(texto) -> str:
    s = re.sub(r"\s+", " ", str(texto or "")).strip()
    s = re.sub(r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b", "[CPF]", s)
    s = re.sub(r"\b\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}\b", "[CNPJ]", s)
    s = re.sub(r"\b(?:\d[ -]?){13,19}\b", "[CARTÃO]", s)
    s = re.sub(r"(?i)\b(senha|password|pin)\s*[:=]?\s*\S+", r"\1 [OCULTO]", s)
    s = re.sub(r"(?i)\bag(?:encia|\.)?\s*\d{3,5}[- ]?\d?\s*(?:c/?c|conta)\s*\d{4,}[- ]?\d?", "[CONTA]", s)
    if len(s) > LIMITE:
        s = s[:LIMITE].rsplit(" ", 1)[0].rstrip(" ,;") + "…"
    return s


def sinais(texto) -> Dict:
    raw = normalizar(texto)
    t = _fold(raw)
    out: Dict = {}
    for cid, uf in sorted(_CAPITAIS.items(), key=lambda x: -len(x[0])):
        if re.search(rf"\b(?:moro|vivo|resido|sou)\s+(?:em|no|na|de)\s+{cid}\b", t) or re.search(rf"\b{cid}\b", t):
            out["cidade"], out["uf"] = cid.title(), uf
            break
    for nome, uf in sorted(_UF_NOMES.items(), key=lambda x: -len(x[0])):
        if re.search(rf"\b(?:estado d[eoa]s?\s+|em\s+|no\s+|na\s+|interior d[eoa]\s+)?{nome}\b", t) and "uf" not in out:
            out["uf"] = uf
            break
    m = re.search(r"(?:\b[-/,]\s*|\b(?:em|no|na)\s+)([A-Z]{2})\b", raw)
    if m and m.group(1) in _SIGLAS:
        out["uf"] = m.group(1)
    for nome, rx in _TRABALHOS:
        if re.search(rx, t):
            out["trabalho"] = nome
            break
    if re.search(r"\b(tenho|temos|meus?|minhas?)\s+(\w+\s+)?(filh[oa]s?|crianca|criancas|bebe|enteado)", t) \
            or re.search(r"\b(sou (mae|pai)|mae de|pai de)\b", t):
        out["filhos"] = True
    elif re.search(r"\b(nao tenho filhos|sem filhos)\b", t):
        out["filhos"] = False
    for nome, rx in _RELIG:
        if re.search(rx, t):
            out["religiao"] = nome
            break
    if re.search(r"\b(fim de semana|sabado|domingo|escala|turno|plantao|noite)\b", t) and \
            re.search(r"\b(trabalho|trabalh[oa]|escala|plantao)\b", t):
        out["escala"] = True
    if re.search(r"\b(estud(o|ante)|faculdade|universidade|vestibular|enem)\b", t):
        out["estudante"] = True
    if re.search(r"\b(sem enfeite|sem floreio|serio|formal|direto)\b", t):
        out["sem_floreios"] = True
    if re.search(r"\b(viajo muito|viagens? a trabalho)\b", t):
        out["viaja"] = True
    m = re.search(r"\b(?:me chamo|meu nome e|sou o|sou a)\s+([a-z]{2,20})\b", t)
    if m and m.group(1) not in ("mae", "pai", "professor", "professora", "medico", "medica"):
        out["nome"] = m.group(1).title()
    return out


def para_modelo(texto) -> str:
    s = normalizar(texto)
    if not s:
        return ""
    return f"Sobre o usuário: {s[:300]}\n"
