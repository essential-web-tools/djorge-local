"""DJORGE dataset builder (djorge-synth-2.0): reproducible, versioned, template-disjoint.

Record: {id, split, task, mode?, target?, definition?, source, spans?[[s,e]], polarity?[...],
         options?, descriptions?, ask?, label?, meta:{lang, family, template, difficulty, provenance, license}}
tasks : resolve (CLASS/ENTITY spans + polarity) | answer (grounded question -> evidence span)
        | CHOICE | SCORE | NOUL | RANK | SET | general
splits: train | validation | calibration | test | test_unseen_member | test_unseen_class | general

Anti-leakage design (fixes H4):
  * every template family has three DISJOINT template pools: train / calibration / test
  * distractor vocabulary is split the same way
  * names/objects for answer families are split train vs test
So test scores measure transfer to unseen phrasings and unseen distractors, not template memory.
"""
from __future__ import annotations

import hashlib
import json
import random
import re
from typing import Dict, List

from .taxonomy import HARD, LANGS, NEUTRAL, ORGS, PEOPLE, PLACES, T

GENERATOR = "djorge-synth-2.3"
AND = {"en": "and", "pt": "e", "es": "y", "fr": "et", "de": "und"}
NUM = {"en": ["two", "three", "some", "a"], "pt": ["duas", "três", "uma", "um"], "es": ["dos", "tres", "una", "un"],
       "fr": ["deux", "trois", "une", "des"], "de": ["zwei", "drei", "eine", "einige"]}
# (template, polarity of {x}) ; pools: train / cal / test (disjoint)
TPL = {
    "en": {"train": [("{p} bought {x}.", "asserted"), ("There were {x} on the table.", "asserted"),
                     ("{p} did not buy {x}.", "negated"), ("{p} wanted to buy {x}.", "nonfactual")],
           "cal": [("At the market I saw {x}.", "asserted"), ("{p} never ate {x}.", "negated")],
           "test": [("Yesterday {p} brought {x} home.", "asserted"), ("{p} refused {x}.", "negated"),
                    ("The doctor recommended {x}.", "nonfactual")]},
    "pt": {"train": [("{p} comprou {x}.", "asserted"), ("Havia {x} na mesa.", "asserted"),
                     ("{p} não comprou {x}.", "negated"), ("{p} queria comprar {x}.", "nonfactual")],
           "cal": [("Vi {x} na feira.", "asserted"), ("{p} nunca comeu {x}.", "negated")],
           "test": [("Ontem {p} levou {x} para casa.", "asserted"), ("{p} recusou {x}.", "negated"),
                    ("A nutricionista recomendou {x}.", "nonfactual")]},
    "es": {"train": [("{p} compró {x}.", "asserted"), ("Había {x} en la mesa.", "asserted"),
                     ("{p} no compró {x}.", "negated"), ("{p} quería comprar {x}.", "nonfactual")],
           "cal": [("Vi {x} en el mercado.", "asserted"), ("{p} nunca comió {x}.", "negated")],
           "test": [("Ayer {p} llevó {x} a casa.", "asserted"), ("{p} rechazó {x}.", "negated"),
                    ("El médico recomendó {x}.", "nonfactual")]},
    "fr": {"train": [("{p} a acheté {x}.", "asserted"), ("{p} n'a pas acheté {x}.", "negated")],
           "cal": [("J'ai vu {x} au marché.", "asserted")],
           "test": [("Hier {p} a rapporté {x}.", "asserted")]},
    "de": {"train": [("{p} kaufte {x}.", "asserted"), ("{p} kaufte keine {x}.", "negated")],
           "cal": [("Ich sah {x} auf dem Markt.", "asserted")],
           "test": [("Gestern brachte {p} {x} nach Hause.", "asserted")]},
}
MIXED = {"en": "{p} bought {x}, but did not buy {y}.", "pt": "{p} comprou {x}, mas não comprou {y}.",
         "es": "{p} compró {x}, pero no compró {y}."}
MIXED2 = {"en": "{p} did not buy {y}. Later {p} bought {x}.", "pt": "{p} não comprou {y}. Depois levou {x} para casa.",
          "es": "{p} no compró {y}. Después compró {x}."}
FOOD_NOT_FRUIT = {"en": ["rice", "beans", "bread", "coffee", "cheese", "meat", "sandwich", "pasta", "eggs"],
                  "pt": ["arroz", "feijão", "pão", "café", "queijo", "carne", "sanduíche", "macarrão", "ovos"],
                  "es": ["arroz", "frijoles", "pan", "café", "queso", "carne", "sándwich", "pasta", "huevos"],
                  "fr": ["riz", "haricots", "pain", "café", "fromage", "viande", "sandwich", "pâtes"],
                  "de": ["Reis", "Bohnen", "Brot", "Kaffee", "Käse", "Fleisch", "Nudeln", "Eier"]}
CLASS_WORD = {"en": "fruit", "pt": "frutas", "es": "frutas", "fr": "fruits", "de": "Obst"}
BRANDS = [("en", "They had dinner at Banana Grill.", "Banana Grill"), ("pt", "Jantaram no restaurante Banana Grill.", "Banana Grill"),
          ("en", "Orange raised its phone prices.", "Orange"), ("pt", "A Apple lançou um novo computador.", "Apple"),
          ("es", "Apple presentó un portátil nuevo.", "Apple"), ("en", "Apple launched a new laptop today.", "Apple"),
          ("pt", "A loja Laranja Mecânica abriu ontem.", "Laranja Mecânica"), ("en", "Mango opened a new clothing store.", "Mango")]

# Brand names that are also common nouns (v2.3). Company-context sentences: the brand is NOT a member.
# Mixed sentences contain a real member AND the brand: only the real member is a span.
BRAND_NAMES = {"Fruit": ["Apple", "Orange", "BlackBerry", "Mango", "Raspberry Pi", "Kiwi.com", "Lime", "Peach Aviation"],
               "Animal": ["Jaguar", "Puma", "Fox", "Dove", "Red Bull", "Mustang", "Lacoste Crocodile", "Firefox"]}
BRAND_TPL = {"en": ["Shares of {B} fell on Monday.", "She works at {B} as an engineer.", "{B} opened a new office in {L}.",
                    "The {B} store downtown was crowded.", "Analysts expect {B} to report higher profits."],
             "pt": ["As ações da {B} caíram na segunda-feira.", "Ela trabalha na {B} como engenheira.",
                    "A {B} abriu um escritório em {L}.", "A loja da {B} no centro estava cheia."],
             "es": ["Las acciones de {B} cayeron el lunes.", "Ella trabaja en {B} como ingeniera.", "{B} abrió una oficina en {L}."]}
BRAND_MIX = {"Fruit": {"en": ("She ate {m} at the {B} office.", ["an apple", "a banana", "a pear"]),
                       "pt": ("Ela comeu {m} no escritório da {B}.", ["uma maçã", "uma banana", "uma pera"]),
                       "es": ("Ella comió {m} en la oficina de {B}.", ["una manzana", "un plátano", "una pera"])},
             "Animal": {"en": ("He walked his {m} past the {B} dealership.", ["dog", "horse"]),
                        "pt": ("Ele passeou com o {m} perto da concessionária {B}.", ["cachorro", "cavalo"]),
                        "es": ("Paseó a su {m} cerca del concesionario {B}.", ["perro", "caballo"])}}

# NOTE (v2.3): lend/borrow wording is deliberately absent from training pools: djorge-real HIDDEN uses it.
# ---------------- grounded answer families (en/pt/es) ----------------
NAMES_TR = ["Arthur", "Maria", "João", "Ana", "Carlos", "Sofia", "Emma", "Pierre"]
NAMES_TE = ["Lucas", "Hiroshi", "Amara", "Lena", "Bruno", "Clara", "Omar", "Julia"]
OBJ = {"en": ["book", "phone", "watch", "letter", "ball", "key"], "pt": ["livro", "telefone", "relógio", "carta", "bola", "chave"],
       "es": ["libro", "teléfono", "reloj", "carta", "pelota", "llave"]}
COLORS = {"en": ["black", "yellow", "blue", "white", "red", "green"], "pt": ["preto", "amarelo", "azul", "branco", "vermelho", "verde"],
          "es": ["negro", "amarillo", "azul", "blanco", "rojo", "verde"]}
MEALS = {"en": ["breakfast", "lunch", "dinner", "a snack"], "pt": ["café da manhã", "almoço", "jantar", "um lanche"],
         "es": ["el desayuno", "el almuerzo", "la cena", "un bocadillo"]}
CITIES = ["London", "Paris", "Madrid", "Lisboa", "Tokyo", "Nairobi", "Lima", "Oslo"]
FILLER = {"en": ["It was a sunny day.", "The street was quiet.", "Someone was playing music nearby.", "The bus arrived late."],
          "pt": ["Era um dia ensolarado.", "A rua estava tranquila.", "Alguém tocava música por perto.", "O ônibus atrasou."],
          "es": ["Era un día soleado.", "La calle estaba tranquila.", "Alguien tocaba música cerca.", "El autobús llegó tarde."]}
# family -> lang -> pools of (context template, question template, answer slot)
FAM = {
  "relation_give": {
    "en": {"train": [("{a} gave a {o} to {b}.", "Who received the {o}?", "b"), ("{a} gave a {o} to {b}.", "Who gave the {o}?", "a"),
                     ("{b} got a {o} from {a}.", "Who gave the {o}?", "a"), ("{b} got a {o} from {a}.", "Who got the {o}?", "b")],
           "cal": [("{a} handed the {o} to {b}.", "Who got the {o}?", "b")],
           "test": [("{a} sent a {o} to {b}.", "Who received the {o}?", "b"), ("{b} received a {o} from {a}.", "Who sent the {o}?", "a")]},
    "pt": {"train": [("{a} deu um {o} para {b}.", "Quem recebeu o {o}?", "b"), ("{a} deu um {o} para {b}.", "Quem deu o {o}?", "a"),
                     ("{b} ganhou um {o} de {a}.", "Quem deu o {o}?", "a"), ("{b} ganhou um {o} de {a}.", "Quem ganhou o {o}?", "b")],
           "cal": [("{a} entregou o {o} a {b}.", "Quem ganhou o {o}?", "b")],
           "test": [("{a} enviou um {o} para {b}.", "Quem recebeu o {o}?", "b"), ("{b} recebeu um {o} de {a}.", "Quem enviou o {o}?", "a")]},
    "es": {"train": [("{a} le dio un {o} a {b}.", "¿Quién recibió el {o}?", "b")],
           "cal": [("{a} entregó el {o} a {b}.", "¿Quién obtuvo el {o}?", "b")],
           "test": [("{a} envió un {o} a {b}.", "¿Quién recibió el {o}?", "b")]}},
  "relation_trade": {
    "en": {"train": [("{a} sold a {o} to {b}.", "Who bought the {o}?", "b"), ("{b} bought a {o} from {a}.", "Who sold the {o}?", "a"),
                     ("{a} traded a {o} to {b}.", "Who got the {o}?", "b")],
           "cal": [("{b} purchased the {o} from {a}.", "Who sold the {o}?", "a")],
           "test": [("{a} sold the {o} to {b} yesterday.", "Who bought the {o}?", "b")]},
    "pt": {"train": [("{b} comprou um {o} de {a}.", "Quem vendeu o {o}?", "a"), ("{b} adquiriu um {o} de {a}.", "Quem vendeu o {o}?", "a"),
                     ("{a} repassou o {o} para {b}.", "Quem ficou com o {o}?", "b")],
           "cal": [("{b} comprou o {o} de {a} na feira.", "Quem vendeu o {o}?", "a")],
           "test": [("{a} vendeu o {o} a {b} ontem.", "Quem comprou o {o}?", "b")]},
    "es": {"train": [("{a} le vendió un {o} a {b}.", "¿Quién compró el {o}?", "b"), ("{b} le compró un {o} a {a}.", "¿Quién vendió el {o}?", "a")],
           "cal": [("{a} le prestó el {o} a {b}.", "¿Quién recibió el {o}?", "b")],
           "test": [("{b} recibió el {o} de {a}.", "¿Quién envió el {o}?", "a")]}},
  "state_update": {
    "en": {"train": [("The car was {c1}. Later, it was painted {c2}.", "What color is the car now?", "c2"),
                     ("The car was {c1}. Later, it was painted {c2}.", "What color was the car before?", "c1")],
           "cal": [("The wall used to be {c1}, but now it is {c2}.", "What color is the wall now?", "c2")],
           "test": [("The house was {c1}, but later it was painted {c2}.", "What color is the house now?", "c2"),
                    ("At first the door was {c1}. Then someone painted it {c2}.", "What color is the door now?", "c2")]},
    "pt": {"train": [("O carro era {c1}. Depois, foi pintado de {c2}.", "Qual é a cor do carro agora?", "c2"),
                     ("O carro era {c1}. Depois, foi pintado de {c2}.", "Qual era a cor do carro antes?", "c1")],
           "cal": [("A parede era {c1}, mas agora é {c2}.", "Qual é a cor da parede agora?", "c2")],
           "test": [("A casa era {c1}, mas depois foi pintada de {c2}.", "Qual é a cor da casa agora?", "c2")]},
    "es": {"train": [("El coche era {c1}. Después, lo pintaron de {c2}.", "¿De qué color es el coche ahora?", "c2")],
           "cal": [("La pared era {c1}, pero ahora es {c2}.", "¿De qué color es la pared ahora?", "c2")],
           "test": [("La casa era {c1}, pero luego la pintaron de {c2}.", "¿De qué color es la casa ahora?", "c2")]}},
  "temporal": {
    "en": {"train": [("{a} ate {m1}. Later, {a} ate {m2}.", "What did {a} eat later?", "m2"),
                     ("{a} ate {m1}. Later, {a} ate {m2}.", "What did {a} eat first?", "m1")],
           "cal": [("First {a} had {m1}, and after that {m2}.", "What did {a} have after that?", "m2")],
           "test": [("{a} had {m1} in the morning and {m2} in the evening.", "What did {a} have in the evening?", "m2")]},
    "pt": {"train": [("{a} comeu {m1}. Depois, {a} comeu {m2}.", "O que {a} comeu depois?", "m2"),
                     ("{a} comeu {m1}. Depois, {a} comeu {m2}.", "O que {a} comeu primeiro?", "m1")],
           "cal": [("Primeiro {a} tomou {m1} e em seguida {m2}.", "O que {a} tomou em seguida?", "m2")],
           "test": [("{a} tomou {m1} de manhã e {m2} à noite.", "O que {a} tomou à noite?", "m2")]},
    "es": {"train": [("{a} comió {m1}. Después, {a} comió {m2}.", "¿Qué comió {a} después?", "m2")],
           "cal": [("Primero {a} tomó {m1} y luego {m2}.", "¿Qué tomó {a} luego?", "m2")],
           "test": [("{a} tomó {m1} por la mañana y {m2} por la noche.", "¿Qué tomó {a} por la noche?", "m2")]}},
  "tracking": {
    "en": {"train": [("{a} lives in {l1}. {b} lives in {l2}.", "Where does {b} live?", "l2"), ("{a} lives in {l1}. {b} lives in {l2}.", "Who lives in {l1}?", "a")],
           "cal": [("{a} moved to {l1}, while {b} stayed in {l2}.", "Where did {b} stay?", "l2")],
           "test": [("{a} works in {l1} and {b} works in {l2}.", "Where does {a} work?", "l1")]},
    "pt": {"train": [("{a} mora em {l1}. {b} mora em {l2}.", "Onde {b} mora?", "l2"), ("{a} mora em {l1}. {b} mora em {l2}.", "Quem mora em {l1}?", "a")],
           "cal": [("{a} mudou para {l1}, enquanto {b} ficou em {l2}.", "Onde {b} ficou?", "l2")],
           "test": [("{a} trabalha em {l1} e {b} trabalha em {l2}.", "Onde {a} trabalha?", "l1")]},
    "es": {"train": [("{a} vive en {l1}. {b} vive en {l2}.", "¿Dónde vive {b}?", "l2")],
           "cal": [("{a} se mudó a {l1}, mientras {b} se quedó en {l2}.", "¿Dónde se quedó {b}?", "l2")],
           "test": [("{a} trabaja en {l1} y {b} trabaja en {l2}.", "¿Dónde trabaja {a}?", "l1")]}},
  "negation": {
    "en": {"train": [("{a} went outside. {b} did not go outside.", "Who went outside?", "a")],
           "cal": [("{b} did not call. {a} called.", "Who called?", "a")],
           "test": [("{b} did not attend the meeting, but {a} attended it.", "Who attended the meeting?", "a")]},
    "pt": {"train": [("{a} saiu. {b} não saiu.", "Quem saiu?", "a")],
           "cal": [("{b} não ligou. {a} ligou.", "Quem ligou?", "a")],
           "test": [("{b} não foi à reunião, mas {a} foi.", "Quem foi à reunião?", "a")]},
    "es": {"train": [("{a} salió. {b} no salió.", "¿Quién salió?", "a")],
           "cal": [("{b} no llamó. {a} llamó.", "¿Quién llamó?", "a")],
           "test": [("{b} no fue a la reunión, pero {a} fue.", "¿Quién fue a la reunión?", "a")]}},
  "state_change": {
    "en": {"train": [("{a} used to live in {l1}, but moved to {l2}.", "Where does {a} live now?", "l2"),
                     ("{a} used to live in {l1}, but moved to {l2}.", "Where did {a} live before?", "l1")],
           "cal": [("{a} lived in {l1} for years; now {a} lives in {l2}.", "Where does {a} live now?", "l2")],
           "test": [("{a} was based in {l1} until last year, when the job changed to {l2}.", "Where is {a} based now?", "l2")]},
    "pt": {"train": [("{a} morava em {l1}, mas se mudou para {l2}.", "Onde {a} mora agora?", "l2"),
                     ("{a} morava em {l1}, mas se mudou para {l2}.", "Onde {a} morava antes?", "l1")],
           "cal": [("{a} viveu anos em {l1}; agora {a} vive em {l2}.", "Onde {a} vive agora?", "l2")],
           "test": [("{a} trabalhava em {l1} até o ano passado, quando foi transferido para {l2}.", "Onde {a} trabalha agora?", "l2")]},
    "es": {"train": [("{a} vivía en {l1}, pero se mudó a {l2}.", "¿Dónde vive {a} ahora?", "l2")],
           "cal": [("{a} vivió años en {l1}; ahora vive en {l2}.", "¿Dónde vive {a} ahora?", "l2")],
           "test": [("{a} trabajaba en {l1} hasta el año pasado, cuando lo trasladaron a {l2}.", "¿Dónde trabaja {a} ahora?", "l2")]}},
  "temporal_year": {
    "en": {"train": [("{a} visited {l1} in {y1} and {l2} in {y2}.", "Which city did {a} visit first?", "first"),
                     ("{a} visited {l1} in {y1} and {l2} in {y2}.", "Which city did {a} visit last?", "last")],
           "cal": [("In {y1} {a} lived in {l1}; in {y2} in {l2}.", "Where did {a} live most recently?", "last")],
           "test": [("{a} studied in {l1} in {y1} and worked in {l2} in {y2}.", "Where was {a} first?", "first")]},
    "pt": {"train": [("{a} visitou {l1} em {y1} e {l2} em {y2}.", "Qual cidade {a} visitou primeiro?", "first")],
           "cal": [("Em {y1} {a} morou em {l1}; em {y2}, em {l2}.", "Onde {a} morou por último?", "last")],
           "test": [("{a} estudou em {l1} em {y1} e trabalhou em {l2} em {y2}.", "Onde {a} esteve primeiro?", "first")]}},
  "coreference": {
    "en": {"train": [("The courier today was {a}. {f} The courier handed the parcel to {b}.", "Who handed over the parcel?", "a"),
                     ("The manager is {a}. {f} The manager approved the request from {b}.", "Who approved the request?", "a")],
           "cal": [("{a} was the driver that day. {f} The driver dropped {b} at the station.", "Who dropped someone at the station?", "a")],
           "test": [("The teacher this year is {a}. {f} The teacher praised {b} in class.", "Who praised a student?", "a")]},
    "pt": {"train": [("O entregador hoje era o {a}. {f} O entregador entregou o pacote para {b}.", "Quem entregou o pacote?", "a"),
                     ("A gerente é {a}. {f} A gerente aprovou o pedido de {b}.", "Quem aprovou o pedido?", "a")],
           "cal": [("{a} era o motorista naquele dia. {f} O motorista deixou {b} na estação.", "Quem deixou alguém na estação?", "a")],
           "test": [("A professora deste ano é {a}. {f} A professora elogiou {b} na aula.", "Quem elogiou um aluno?", "a")]},
    "es": {"train": [("El mensajero hoy era {a}. {f} El mensajero entregó el paquete a {b}.", "¿Quién entregó el paquete?", "a")],
           "cal": [("{a} era el conductor ese día. {f} El conductor dejó a {b} en la estación.", "¿Quién dejó a alguien en la estación?", "a")],
           "test": [("La profesora de este año es {a}. {f} La profesora felicitó a {b} en clase.", "¿Quién felicitó a un alumno?", "a")]}},
  "comparison": {
    "en": {"train": [("{a} is {n1} cm tall. {b} is {n2} cm tall.", "Who is taller?", "max"), ("{a} is {n1} cm tall. {b} is {n2} cm tall.", "Who is shorter?", "min")],
           "cal": [("{a} is {n1} years old and {b} is {n2} years old.", "Who is older?", "max")],
           "test": [("{a} is {n1} years old. {b} is {n2} years old.", "Who is younger?", "min")]},
    "pt": {"train": [("{a} tem {n1} cm. {b} tem {n2} cm.", "Quem é mais alto?", "max")],
           "cal": [("{a} tem {n1} anos e {b} tem {n2} anos.", "Quem é mais velho?", "max")],
           "test": [("{a} tem {n1} anos. {b} tem {n2} anos.", "Quem é mais novo?", "min")]},
    "es": {"train": [("{a} mide {n1} cm. {b} mide {n2} cm.", "¿Quién es más alto?", "max")],
           "cal": [("{a} tiene {n1} años y {b} tiene {n2} años.", "¿Quién es mayor?", "max")],
           "test": [("{a} tiene {n1} años. {b} tiene {n2} años.", "¿Quién es menor?", "min")]}},
}
RUBRICS = {"train": [["poor", "good", "excellent"], ["bad", "okay", "great"], ["ruim", "bom", "excelente"], ["malo", "bueno", "excelente"],
                     ["terrible", "poor", "average", "good", "excellent"], ["péssimo", "ruim", "regular", "bom", "ótimo"]],
           "cal": [["weak", "fair", "strong"], ["fraco", "médio", "forte"]],
           "test": [["poor", "fair", "good", "excellent"], ["awful", "mediocre", "decent", "superb"], ["horrível", "fraco", "razoável", "bom", "excelente"]]}
SCORE_TPL = {"train": ["The support we got was {w}.", "O suporte que recebemos foi {w}.", "La atención recibida fue {w}."],
             "cal": ["Overall the experience felt {w}."], "test": ["I would describe the food as {w}.", "A comida estava {w}."]}
RANKS = [(["gold", "silver", "bronze"], "{p} received the {r} medal."), (["first", "second", "third"], "{p} finished {r}."),
         (["ouro", "prata", "bronze"], "{p} ganhou a medalha de {r}."), (["primeiro", "segundo", "terceiro"], "{p} terminou em {r} lugar.")]
DEPT = {"billing": {"en": ["charged twice", "refund", "invoice"], "pt": ["cobrado duas vezes", "reembolso", "fatura"], "es": ["cobrado dos veces", "reembolso", "factura"]},
        "technical": {"en": ["app crashes", "login error", "screen freezes"], "pt": ["o app trava", "erro de login", "a tela congela"], "es": ["la app se cierra", "error de inicio", "la pantalla se congela"]},
        "sales": {"en": ["upgrade price", "discount", "new plan"], "pt": ["preço do upgrade", "desconto", "plano novo"], "es": ["precio de mejora", "descuento", "plan nuevo"]}}
DESC = {"billing": "payments, charges, refunds", "technical": "bugs, crashes, errors", "sales": "pricing, plans, purchases"}
YESNO = {"en": [("The door is open.", "The door is not open.", "Is the door open?"), ("The shop is closed.", "The shop is not closed.", "Is the shop closed?"),
                ("Maria likes coffee.", "Maria does not like coffee.", "Does Maria like coffee?")],
         "pt": [("A porta está aberta.", "A porta não está aberta.", "A porta está aberta?"), ("Maria gosta de café.", "Maria não gosta de café.", "Maria gosta de café?")]}
SENT = {"en": (["I love this", "great service", "very happy"], ["I hate this", "terrible service", "very angry"]),
        "pt": (["Eu adoro isso", "ótimo serviço", "muito feliz"], ["Eu odeio isso", "péssimo serviço", "muito irritado"]),
        "es": (["Me encanta", "excelente servicio", "muy feliz"], ["Lo odio", "pésimo servicio", "muy enfadado"])}

POOL_SPLIT = {"train": ["train"] * 90 + ["validation"] * 10, "cal": ["calibration"], "test": ["test"]}


def _pool(rng):
    r = rng.random()
    return "train" if r < 0.78 else ("cal" if r < 0.88 else "test")


def _fill(tpl: str, vals: Dict[str, str]):
    """Fill {slots}; return text and char spans for every slot."""
    out, spans, j = "", {}, 0
    for m in re.finditer(r"\{(\w+)\}", tpl):
        out += tpl[j:m.start()]
        v = vals[m.group(1)]
        spans.setdefault(m.group(1), []).append([len(out), len(out) + len(v)])
        out += v
        j = m.end()
    return out + tpl[j:], spans


def _items(lang, words_flags, rng):
    """List phrase with optional quantifiers; returns text and (relative span, flag) per item."""
    txt, spans = "", []
    for i, (w, f) in enumerate(words_flags):
        sep = "" if i == 0 else (", " if i < len(words_flags) - 1 else f" {AND.get(lang, 'and')} ")
        q = rng.choice(NUM[lang]) + " " if rng.random() < 0.45 else ""
        txt += sep + q
        spans.append(([len(txt), len(txt) + len(w)], f))
        txt += w
    return txt, spans


def generate(n_resolve=7000, n_entity=1500, n_answer=5000, n_decision=3000, n_general=800, seed=0) -> List[dict]:
    rng = random.Random(seed)
    out: List[dict] = []
    trc = [c for c in T if not T[c].get("heldout_class")]
    hcl = [c for c in T if T[c].get("heldout_class")]

    def rec(task, split, **kw):
        r = {"id": f"d{len(out)}", "split": split, "task": task, **kw}
        r.setdefault("meta", {})
        r["meta"].update(provenance="djorge-synth", license="MIT", generator=GENERATOR)
        out.append(r)

    # ---------- CLASS resolution with polarity ----------
    for i in range(n_resolve):
        lang = rng.choice(LANGS)
        pool = _pool(rng)
        held_class = i % 10 == 9
        cls = rng.choice(hcl if held_class else trc)
        spec = T[cls]
        held = set(spec.get("heldout_members", []))
        want_held = (i % 10 == 8) and not held_class
        cands = [m for m in spec["members"][lang] if (m in held) == want_held] or spec["members"][lang]
        pos = rng.sample(cands, min(rng.choice([0, 1, 1, 2, 2, 3, 3, 4, 5, 6]), len(cands)))      # larger lists
        neu = NEUTRAL[lang][:5] if pool == "train" else NEUTRAL[lang][5:]          # disjoint distractors
        food = FOOD_NOT_FRUIT[lang][:5] if pool == "train" else FOOD_NOT_FRUIT[lang][5:]
        neg = rng.sample(neu, rng.randint(0, 1)) + (rng.sample(food, rng.randint(1, 2)) if rng.random() < 0.5 else [])
        if rng.random() < 0.4:
            oc = rng.choice([c for c in trc if c != cls])
            neg.append(rng.choice(T[oc]["members"][lang]))
        if rng.random() < 0.08:
            neg.append(CLASS_WORD[lang])                                           # "frutas" is not a mention
        items = [(w, True) for w in pos] + [(w, False) for w in neg if w.casefold() not in {p.casefold() for p in pos}]
        rng.shuffle(items)
        if not items:
            items = [(rng.choice(neu), False)]
        mixed = lang in MIXED and rng.random() < 0.15 and len(items) >= 2
        if mixed:                                                                   # "bought X, but did not buy Y"
            k = rng.randint(1, len(items) - 1)
            xt, xs = _items(lang, items[:k], rng)
            yt, ys = _items(lang, items[k:], rng)
            text, sl = _fill(rng.choice([MIXED[lang], MIXED2[lang]]), {"p": rng.choice(PEOPLE), "x": xt, "y": yt})
            spans, pols = [], []
            for (rel, f) in xs:
                if f: spans.append([rel[0] + sl["x"][0][0], rel[1] + sl["x"][0][0]]); pols.append("asserted")
            for (rel, f) in ys:
                if f: spans.append([rel[0] + sl["y"][0][0], rel[1] + sl["y"][0][0]]); pols.append("negated")
            tname = "mixed"
        else:
            tpl, pol = rng.choice(TPL[lang][pool])
            xt, xs = _items(lang, items, rng)
            text, sl = _fill(tpl, {"p": rng.choice(PEOPLE), "x": xt})
            off = sl["x"][0][0]
            spans = [[a + off, b + off] for (a, b), f in xs if f]
            pols = [pol] * len(spans)
            tname = tpl
        if lang in FILLER and rng.random() < 0.35:            # long-text robustness: mentions inside filler
            pre = " ".join(rng.sample(FILLER[lang], rng.randint(1, 4))) + " "
            post = " " + " ".join(rng.sample(FILLER[lang], rng.randint(0, 3)))
            spans = [[a + len(pre), b + len(pre)] for a, b in spans]
            text = pre + text + post
        split = ("test_unseen_class" if held_class else "test_unseen_member" if want_held and pos else
                 rng.choice(POOL_SPLIT[pool]))
        name_lang = "en" if rng.random() < 0.65 else rng.choice(LANGS)
        rec("resolve", split, mode="CLASS", target=rng.choice(spec["names"][name_lang]), source=text, spans=spans,
            polarity=pols, meta={"lang": lang, "class": cls, "template": tname, "pool": pool})
    for lang, sent, word, is_mem, cls in HARD:
        s = sent.index(word)
        h = int(hashlib.sha1(f"{sent}|{cls}".encode()).hexdigest(), 16) % 10
        split = "test" if h < 3 else ("calibration" if h < 4 else "train")
        for _ in range(4 if split == "train" else 1):
            rec("resolve", split, mode="CLASS", target=rng.choice(T[cls]["names"]["en"]), source=sent,
                spans=[[s, s + len(word)]] if is_mem else [], polarity=["asserted"] if is_mem else [],
                meta={"lang": lang, "class": cls, "difficulty": "hard_context"})
    for k, (lang, sent, _) in enumerate(BRANDS):                                    # brand names are not fruit
        split = "test" if k % 3 == 2 else "train"
        for _ in range(4 if split == "train" else 1):
            rec("resolve", split, mode="CLASS", target="Fruit", source=sent, spans=[], polarity=[],
                meta={"lang": lang, "class": "Fruit", "difficulty": "brand"})
    for cls, brands in BRAND_NAMES.items():                                       # v2.3 brand hard negatives
        for bi, b in enumerate(brands):
            for lang, tpls in BRAND_TPL.items():
                for ti, tpl in enumerate(tpls):
                    split = "test" if (bi + ti) % 5 == 4 else ("calibration" if (bi + ti) % 5 == 3 else "train")
                    sent, _ = _fill(tpl, {"B": b, "L": rng.choice(CITIES)})
                    rec("resolve", split, mode="CLASS", target=rng.choice(T[cls]["names"][lang if lang in T[cls]["names"] else "en"]),
                        source=sent, spans=[], polarity=[], meta={"lang": lang, "class": cls, "difficulty": "brand"})
                mt, members = BRAND_MIX[cls][lang]
                m = members[bi % len(members)]
                sent, sl = _fill(mt, {"B": b, "m": m})
                a0, b0 = sl["m"][0]
                head = m.split()[-1]                                                     # span = the noun, not the article
                a1 = a0 + m.rfind(head)
                split = "test" if bi % 5 == 4 else "train"
                rec("resolve", split, mode="CLASS", target=rng.choice(T[cls]["names"]["en"]), source=sent,
                    spans=[[a1, a1 + len(head)]], polarity=["asserted"], meta={"lang": lang, "class": cls, "difficulty": "brand_mix"})
    # ---------- ENTITY ----------
    ENT = {"en": ["{P} works at {O} in {L}.", "{P} met {Q} in {L}."], "pt": ["{P} trabalha na {O} em {L}.", "{P} encontrou {Q} em {L}."],
           "es": ["{P} trabaja en {O} en {L}.", "{P} conoció a {Q} en {L}."]}
    for i in range(n_entity):
        lang = rng.choice(list(ENT))
        et = rng.choice(["Person", "Location", "Organization"])
        text, sl = _fill(rng.choice(ENT[lang]), {"P": rng.choice(PEOPLE), "Q": rng.choice(PEOPLE), "O": rng.choice(ORGS), "L": rng.choice(PLACES)})
        keys = {"Person": ("P", "Q"), "Location": ("L",), "Organization": ("O",)}[et]
        spans = [s for k in keys for s in sl.get(k, [])]
        rec("resolve", rng.choice(POOL_SPLIT[_pool(rng)]), mode="ENTITY", target=et, source=text, spans=sorted(spans),
            polarity=["asserted"] * len(spans), meta={"lang": lang, "class": et})
    # ---------- grounded ANSWER ----------
    fams = list(FAM)
    for i in range(n_answer):
        fam = fams[i % len(fams)]
        lang = rng.choice(list(FAM[fam]))
        pool = _pool(rng)
        ctx, q, slot = rng.choice(FAM[fam][lang][pool])
        names = NAMES_TR if pool == "train" else NAMES_TE
        a, b = rng.sample(names, 2)
        c1, c2 = rng.sample(COLORS[lang], 2)
        m1, m2 = rng.sample(MEALS[lang], 2)
        l1, l2 = rng.sample(CITIES, 2)
        n1, n2 = rng.sample(range(10, 200), 2)
        y1, y2 = rng.sample(range(1990, 2025), 2)
        vals = dict(a=a, b=b, o=rng.choice(OBJ[lang]), c1=c1, c2=c2, m1=m1, m2=m2, l1=l1, l2=l2, n1=str(n1), n2=str(n2),
                    y1=str(y1), y2=str(y2), f=" ".join(rng.sample(FILLER[lang], rng.randint(1, 4))))
        if rng.random() < 0.3:                                                        # long-context distractors
            ctx = " ".join(rng.sample(FILLER[lang], 2)) + " " + ctx + " " + rng.choice(FILLER[lang])
        text, sl = _fill(ctx, vals)
        qq, _ = _fill(q, vals)
        if slot in ("max", "min"):
            slot = "a" if (n1 > n2) == (slot == "max") else "b"
        if slot in ("first", "last"):
            slot = "l1" if (y1 < y2) == (slot == "first") else "l2"
        gold = sl[slot][:1] if fam != "temporal" else sl[slot][-1:]
        rec("answer", rng.choice(POOL_SPLIT[pool]), ask=qq, source=text, spans=gold,
            meta={"lang": lang, "family": fam, "template": ctx, "pool": pool})
    # ---------- decisions ----------
    for i in range(n_decision):
        kind = ["CHOICE", "NOUL", "SCORE", "SET", "RANK", "YESNO"][i % 6]
        pool = _pool(rng)
        sp = rng.choice(POOL_SPLIT[pool])
        lang = rng.choice(["en", "pt", "es"])
        if kind == "CHOICE":
            d = rng.choice(list(DEPT)); opts = list(DEPT); rng.shuffle(opts)
            rec("CHOICE", sp, source=rng.choice(DEPT[d][lang]).capitalize() + ".", options=opts,
                descriptions=[DESC[o] if rng.random() < .5 else "" for o in opts], ask="Which team should handle this?", label=d, meta={"lang": lang})
        elif kind == "YESNO":
            l2 = rng.choice(list(YESNO)); pos_s, neg_s, q = rng.choice(YESNO[l2]); yes = rng.random() < 0.5
            opts = ["yes", "no"] if l2 == "en" else ["sim", "não"]
            rec("CHOICE", sp, source=pos_s if yes else neg_s, options=opts, descriptions=["", ""], ask=q,
                label=opts[0] if yes else opts[1], meta={"lang": l2, "family": "yesno"})
        elif kind == "NOUL":
            d = rng.choice(list(DEPT)); q = rng.choice(list(DEPT))
            rec("NOUL", sp, source=rng.choice(DEPT[d][lang]).capitalize() + ".", options=[f"This is about {DESC[q]}."], label=q == d, meta={"lang": lang})
        elif kind == "SCORE":
            rub = rng.choice(RUBRICS[pool]); lvl = rng.randrange(len(rub))
            rec("SCORE", sp, source=rng.choice(SCORE_TPL[pool]).format(w=rub[lvl]), options=rub, ask="Rate it.", label=lvl,
                meta={"lang": lang, "rubric": rub})
        elif kind == "SET":
            vocab = NEUTRAL["en"][:6] + OBJ["en"] if pool == "train" else NEUTRAL["en"][6:] + ["laptop", "chair", "lamp", "cup"]
            opts = rng.sample(vocab, 3); present = rng.sample(opts, rng.randint(0, 3))
            txt = (f"{rng.choice(NAMES_TR)} has " + (", ".join("a " + w for w in present) if present else "nothing") + ".")
            rec("SET", sp, source=txt, options=opts, ask="Which items are mentioned?", label=present, meta={"lang": "en"})
        else:
            levels, tpl = rng.choice(RANKS); ppl = rng.sample(NAMES_TR if pool == "train" else NAMES_TE, 3)
            order = list(range(3)); rng.shuffle(order)
            txt = " ".join(tpl.format(p=ppl[j], r=levels[j]) for j in order)
            opts = levels[:]; rng.shuffle(opts)
            rec("RANK", sp, source=txt, options=opts, ask="Rank from best to worst.", label=levels, meta={"lang": lang})
    for i in range(n_general):
        lang = rng.choice(list(SENT)); pos = rng.random() < 0.5
        rec("general", "general", source=rng.choice(SENT[lang][0 if pos else 1]) + ".", label=int(pos), meta={"lang": lang, "probe": "sentiment"})
    return bench_guard(out)


def _bench_sentences() -> set:
    import os
    here = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    path = os.path.join(here, "benchmarks", "real", "djorge_real_v1.json")
    if not os.path.exists(path):
        return set()
    norm = lambda x: " ".join(re.findall(r"\w+", x.casefold()))
    out = set()
    for it in json.load(open(path)):
        ctx = it["mc"]["context"]
        out.add(norm(ctx))
        out |= {norm(x) for x in re.split(r"(?<=[.!?])\s+", ctx) if len(x.split()) >= 3}
    return out


def bench_guard(records: List[dict]) -> List[dict]:
    """v2.3: no synthetic record may reproduce a djorge-real context (or one of its sentences) verbatim."""
    bad = _bench_sentences()
    if not bad:
        return records
    norm = lambda x: " ".join(re.findall(r"\w+", x.casefold()))
    keep = [r for r in records if norm(r["source"]) not in bad
            and not any(norm(x) in bad for x in re.split(r"(?<=[.!?])\s+", r["source"]) if len(x.split()) >= 3)]
    return keep


def dataset_version(records: List[dict]) -> str:
    h = hashlib.sha256()
    for r in records:
        h.update(json.dumps(r, sort_keys=True, ensure_ascii=False).encode())
    return f"{GENERATOR}:{h.hexdigest()[:12]}"


def load_public(max_per_source: int = 2000) -> List[dict]:
    """Optional public data (needs `datasets` + hub access), provenance + license per record.
      squad_v2 (CC BY-SA 4.0)   -> answer (English extractive QA; unanswerable -> empty spans)
      xquad (CC BY-SA 4.0)      -> answer (es/de/... ; evaluation-sized, use as extra train only if not benchmarking it)
      wikiann (ODC-By)          -> ENTITY resolution
    """
    from datasets import load_dataset
    out = []
    sq = load_dataset("rajpurkar/squad_v2", split="train").shuffle(seed=0).select(range(max_per_source))
    for ex in sq:
        ctx = ex["context"]
        spans = [[s, s + len(t)] for s, t in zip(ex["answers"]["answer_start"], ex["answers"]["text"])][:1]
        if len(ctx) < 1500:
            out.append({"task": "answer", "split": "train", "ask": ex["question"], "source": ctx, "spans": spans,
                        "meta": {"lang": "en", "family": "squad_v2", "provenance": "squad_v2", "license": "CC BY-SA 4.0"}})
    tagmap = {1: "Person", 3: "Organization", 5: "Location"}
    for lang in ("en", "pt", "es"):
        for ex in load_dataset("unimelb-nlp/wikiann", lang, split="train").select(range(max_per_source // 3)):
            text, offs = "", []
            for t in ex["tokens"]:
                offs.append((len(text), len(text) + len(t))); text += t + " "
            text = text.strip()
            for bt, et in tagmap.items():
                spans, cur = [], None
                for (s, e), tg in zip(offs, ex["ner_tags"]):
                    if tg == bt:
                        if cur: spans.append(cur)
                        cur = [s, e]
                    elif tg == bt + 1 and cur:
                        cur[1] = e
                    else:
                        if cur: spans.append(cur); cur = None
                if cur: spans.append(cur)
                out.append({"task": "resolve", "mode": "ENTITY", "target": et, "source": text, "spans": spans,
                            "polarity": ["asserted"] * len(spans), "split": "train",
                            "meta": {"lang": lang, "provenance": "wikiann", "license": "ODC-By"}})
    return out
