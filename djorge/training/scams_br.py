"""Brazilian (and global) scam patterns for DETECTION training - Djorge Sentinel.

Short, generic messages in the style of what victims RECEIVE (as published in public awareness material from banks,
FEBRABAN, Procon and CERT.br), paired with hard negatives (legitimate messages that look similar) so the model learns
the difference instead of reacting to words like "Pix", "código" or "boleto". No operational instructions are
generated - only the bait a person would see. Templates with index % 5 == 4 and the held-out value lists are reserved
for validation/test, so the test measures NEW phrasings and brands.
"""
from __future__ import annotations

import random
from typing import List

from .sentinel_data import message_record

BANKS = (["Nubank", "Itaú", "Bradesco", "Caixa", "Banco do Brasil", "Santander", "Inter", "C6 Bank"], ["Sicredi", "PicPay", "Mercado Pago"])
STORES = (["Mercado Livre", "Shopee", "Magazine Luiza", "Americanas", "Amazon", "OLX"], ["Casas Bahia", "AliExpress", "Shein"])
GOV = (["Receita Federal", "gov.br", "Detran", "INSS", "Serasa", "Correios"], ["Caixa Tem", "Sefaz", "Poupatempo"])
NAMES = (["mãe", "pai", "filho", "tia", "amor", "vó"], ["irmão", "madrinha", "primo"])
BAD = ["seguranca-{b}.com", "{b}-atualizacao.online", "{b}.app-verificar.xyz", "{b}-br.top", "regularize-{b}.site", "bit.ly/{n}x{n}"]


def _slug(s):
    return "".join(c for c in s.lower().replace(" ", "") if c.isalnum())[:12]


def _v(rng, held, pair):
    return rng.choice(pair[1] if held else pair[0])


SCAMS = [  # (threat, template) - {b} bank, {s} store, {g} gov, {p} relative, {v} value, {u} link, {c} code, {d} day
    ("scam", "Oi {p}, troquei de número. Salva esse. Preciso pagar uma conta urgente hoje, me faz um Pix de R$ {v}? Amanhã te devolvo."),
    ("phishing", "{b}: identificamos uma compra de R$ {v} no seu cartão. Se não reconhece, acesse {u} e confirme seus dados."),
    ("phishing", "Correios: sua encomenda foi taxada em R$ {v} e será devolvida. Pague a taxa em {u} até hoje."),
    ("phishing", "{g}: seu CPF possui pendências e será cancelado. Regularize em {u} em até 24h."),
    ("scam", "Oportunidade! Ganhe R$ {v} por dia curtindo vídeos no app. Fale com a recrutadora pelo link {u}."),
    ("scam", "Investimento garantido: dobre seu dinheiro em 7 dias com cripto. Rendimento de {v}% ao mês, sem risco."),
    ("phishing", "Oi! Te mandei um código de 6 dígitos por SMS por engano, pode me repassar? É urgente."),
    ("scam", "Seu produto no {s} foi vendido! Para receber o pagamento, clique em {u} e pague a taxa de liberação de R$ {v}."),
    ("scam", "Parabéns! Você ganhou um sorteio do {s}. Para receber o prêmio, pague só o frete de R$ {v} via Pix."),
    ("scam", "Empréstimo pré-aprovado de R$ {v}0 sem consulta ao SPC. Deposite a taxa de cadastro de R$ {v} para liberar."),
    ("scam", "Aqui é do suporte do {b}. Sua conta foi invadida. Instale o aplicativo de acesso remoto que vou te passar para protegermos."),
    ("malware", "{b}: seu aplicativo está desatualizado e será bloqueado. Baixe a nova versão em {u}/app.apk"),
    ("phishing", "{g}: você tem R$ {v} de restituição disponível. Informe seus dados bancários em {u} para receber."),
    ("scam", "Seu boleto de R$ {v} venceu. Pague a 2ª via com desconto pelo Pix da empresa: chave aleatória abaixo."),
    ("phishing", "{g}: sua aposentadoria será suspensa por falta de prova de vida. Atualize em {u}."),
    ("scam", "Recebi um Pix seu de R$ {v} por engano, pode devolver para esta outra chave? Senão vou abrir reclamação."),
    ("phishing", "Seu WhatsApp será desativado em 24 horas. Confirme sua conta em {u}."),
    ("scam", "Leilão {g}: carros a partir de R$ {v}. Garanta o seu pagando a reserva via Pix em {u}."),
    ("spam", "SUPER PROMOÇÃO!!! {s} com até {v}% OFF só hoje. Responda SAIR para não receber mais."),
    ("spam", "Ganhe seguidores agora! Pacote com {v}0 seguidores reais por apenas R$ 9,90. Chame no direct."),
    # global
    ("phishing", "Your {s} account has been locked due to unusual activity. Verify your identity at {u} within 24 hours."),
    ("scam", "Hi, it's your CEO. I'm in a meeting, need you to buy {v} gift cards for a client and send me the codes."),
    ("scam", "Congratulations! You've been selected for a $1,000 reward. Pay the ${v} processing fee to claim it."),
    ("phishing", "IRS notice: you are eligible for a tax refund of ${v}. Submit your bank details at {u}."),
    ("scam", "Hola {p}, cambié de número. ¿Me puedes transferir {v} euros hoy? Mañana te los devuelvo."),
    ("phishing", "{b}: su cuenta será suspendida. Verifique sus datos en {u} para evitar el bloqueo."),
    ("malware", "Invoice attached. Open the file factura_{v}.zip (password: {c}) to see the amount due."),
    ("scam", "I'm a US soldier abroad and I fell in love with you. I need ${v} to pay customs for my package, please help."),
]
SAFE = [
    "{b}: você recebeu um Pix de R$ {v} de {p}. Saldo disponível no app.",
    "{b}: nunca pedimos senha, código ou dados por mensagem ou ligação. Em caso de dúvida, use o app oficial.",
    "Correios: seu objeto está em trânsito. Acompanhe pelo site oficial correios.com.br ou pelo app dos Correios.",
    "{p}, cheguei em casa. Amanhã passo aí para te devolver os R$ {v} que você me emprestou.",
    "Seu código de verificação do {s} é {c}. Não compartilhe este código com ninguém.",
    "{g}: o prazo para entrega da declaração termina dia {d}. Faça pelo programa oficial ou pelo app.",
    "Reunião de alinhamento amanhã às 10h. Segue a pauta em anexo, qualquer dúvida me chama.",
    "Notícia: polícia alerta para o golpe do falso parente que pede Pix pelo WhatsApp. Desconfie de números novos.",
    "{s}: seu pedido nº {v}{c} foi entregue. Avalie sua compra no app.",
    "Oi {p}! Já paguei a conta de luz pelo app do banco, pode ficar tranquila.",
    "Your {s} order has shipped. Track it in the app or on the official website.",
    "Reminder: your dentist appointment is on day {d} at 3 pm. Reply C to confirm.",
    "Hola {p}, ya te transferí los {v} euros de la cena. ¡Gracias!",
    "Treinamento obrigatório: como reconhecer phishing e proteger sua senha. Inscreva-se na intranet.",
    "{b}: sua fatura de R$ {v} vence dia {d}. Pague pelo app ou internet banking.",
]
SENDERS = [  # display, good domain
    ("Nubank", "nubank.com.br"), ("Itaú", "itau.com.br"), ("Mercado Livre", "mercadolivre.com.br"), ("Correios", "correios.com.br"),
    ("gov.br", "gov.br"), ("PayPal", "paypal.com"), ("Microsoft", "microsoft.com"), ("Netflix", "netflix.com"),
    ("Bradesco", "bradesco.com.br"), ("Amazon", "amazon.com.br"),
]
SENDER_BODIES_BAD = ["Detectamos um acesso suspeito. Confirme sua senha em {u} para manter a conta ativa.",
                     "Sua assinatura falhou. Atualize os dados do cartão em {u}.",
                     "Your password expires today. Keep it at {u}.",
                     "Você tem uma mensagem segura pendente. Faça login em {u} para ler.",
                     "Pagamento recusado. Reenvie os dados de pagamento em {u}."]
SENDER_BODIES_OK = ["Seu extrato mensal está disponível no app.", "Sua compra foi aprovada. Detalhes no app oficial.",
                    "Your monthly statement is ready in the app.", "Atualizamos nossos termos de uso. Nenhuma ação é necessária.",
                    "Seu pedido saiu para entrega hoje."]


def _fill(rng, held, t):
    b = _v(rng, held, BANKS)
    u = "https://" + rng.choice(BAD).format(b=_slug(b), n=rng.randint(10, 99))
    return t.format(b=b, s=_v(rng, held, STORES), g=_v(rng, held, GOV), p=_v(rng, held, NAMES), v=rng.randint(30, 950),
                    u=u, c=rng.randint(100000, 999999), d=rng.randint(1, 28))


def _lang(t):
    if any(w in t for w in ("Hola", "su cuenta", "euros", "Verifique")):
        return "es"
    if any(w in t for w in ("Your", "Hi,", "IRS", "Congratulations", "Invoice", "I'm a", "Reminder")):
        return "en"
    return "pt"


def generate(rng: random.Random, n: int, held: bool = False) -> List[dict]:
    tpls = [t for i, t in enumerate(SCAMS) if (i % 5 == 4) == held]
    out = []
    for _ in range(n):
        threat, t = rng.choice(tpls)
        txt = _fill(rng, held, t)
        r = message_record(txt, threat, _lang(t), "gen_scams_br", "MIT (generated by DJORGE)", "message", rng)
        if r:
            r["split"] = "test" if held else "train"
            out.append(r)
    return out


def hard_negatives(rng: random.Random, n: int, held: bool = False) -> List[dict]:
    tpls = [t for i, t in enumerate(SAFE) if (i % 5 == 4) == held]
    out = []
    for _ in range(n):
        t = rng.choice(tpls)
        r = message_record(_fill(rng, held, t), "safe", _lang(t), "gen_hard_negatives", "MIT (generated by DJORGE)", "message", rng)
        if r:
            r["split"] = "test" if held else "train"
            out.append(r)
    return out


def spoofed_senders(rng: random.Random, n: int, held: bool = False) -> List[dict]:
    senders = [s for i, s in enumerate(SENDERS) if (i % 5 == 4) == held]
    out = []
    for _ in range(n):
        name, dom = rng.choice(senders)
        bad = rng.random() < 0.55
        if bad:
            fake = rng.choice([f"{_slug(name)}-seguranca.com", f"{_slug(name)}.suporte-online.net", f"{dom}.verify-id.ru",
                               f"atendimento-{_slug(name)}.xyz", "gmail.com"])
            body = rng.choice(SENDER_BODIES_BAD).format(u=f"https://{fake}/login")
            frm = f"{name} <alerta@{fake}>"
        else:
            body = rng.choice(SENDER_BODIES_OK)
            frm = f"{name} <noreply@{dom}>"
        txt = f"De: {frm}\nAssunto: {'Ação necessária' if bad else 'Aviso'}\n\n{body}"
        r = message_record(txt, "phishing" if bad else "safe", "pt", "gen_spoofed_senders", "MIT (generated by DJORGE)", "message", rng)
        if r:
            r["split"] = "test" if held else "train"
            out.append(r)
    return out
