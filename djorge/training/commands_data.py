"""Commands -> DJORGE records, from MASSIVE (Amazon, CC BY 4.0): real assistant commands in Portuguese, Spanish and
English, annotated by people with the intent and the slots ("[person : maria]"). Used to teach the Flash to
understand orders such as "mova os e-mails da Ana para a pasta Trabalho":

  CHOICE  what the person is asking for (intent, with hard distractors from the same scenario)
  resolve which words are the person, the e-mail address, the folder, the date, the time, the app ... (ENTITY)

Nothing is generated: every text and every label comes from the published corpus. The official test partition
stays as test; the dev partition is not used.

    python -m djorge.training.commands_data --out-dir DATA [--langs pt-PT,es-ES,en-US] [--per-lang 12000]
writes  commands_train.jsonl  commands_test.jsonl  commands.manifest.json
"""
from __future__ import annotations

import argparse
import io
import json
import os
import random
import re
import tarfile
import urllib.request
from collections import Counter

URL = "https://amazon-massive-nlu-dataset.s3.amazonaws.com/amazon-massive-dataset-1.1.tar.gz"
LICENSE = "CC BY 4.0 (MASSIVE, Amazon; derived from SLURP)"

INTENT_PT = {
    "datetime_query": "perguntar a data ou a hora", "datetime_convert": "converter horário ou fuso",
    "iot_hue_lightchange": "mudar a cor da luz", "iot_hue_lightup": "aumentar a luz", "iot_hue_lightdim": "diminuir a luz",
    "iot_hue_lightoff": "apagar a luz", "iot_hue_lighton": "acender a luz", "iot_wemo_on": "ligar uma tomada ou aparelho",
    "iot_wemo_off": "desligar uma tomada ou aparelho", "iot_cleaning": "ligar o aspirador ou a limpeza",
    "iot_coffee": "fazer café na máquina", "transport_ticket": "comprar passagem", "transport_traffic": "ver o trânsito",
    "transport_query": "consultar transporte ou horários", "transport_taxi": "chamar um táxi",
    "takeaway_query": "consultar um pedido de comida", "takeaway_order": "pedir comida para entrega",
    "qa_stock": "cotação de ações", "qa_currency": "câmbio de moedas", "qa_definition": "definição de uma palavra",
    "qa_maths": "fazer uma conta", "qa_factoid": "pergunta de conhecimento geral", "general_greet": "cumprimentar",
    "general_quirky": "conversa sem pedido claro", "general_joke": "pedir uma piada",
    "recommendation_events": "sugerir eventos", "recommendation_locations": "sugerir lugares",
    "recommendation_movies": "sugerir filmes", "music_dislikeness": "dizer que não gosta de uma música",
    "music_likeness": "dizer que gosta de uma música", "music_settings": "ajustar a reprodução de música",
    "music_query": "perguntar sobre a música tocando", "cooking_recipe": "pedir uma receita",
    "cooking_query": "dúvida de cozinha", "weather_query": "previsão do tempo", "audio_volume_up": "aumentar o volume",
    "audio_volume_down": "diminuir o volume", "audio_volume_mute": "silenciar", "audio_volume_other": "outro ajuste de som",
    "email_addcontact": "adicionar um contato", "email_querycontact": "consultar dados de um contato",
    "email_sendemail": "enviar ou responder um e-mail", "email_query": "ver ou procurar e-mails",
    "play_audiobook": "tocar um audiolivro", "play_radio": "tocar uma rádio", "play_music": "tocar música",
    "play_podcasts": "tocar um podcast", "play_game": "abrir um jogo", "lists_createoradd": "criar lista ou adicionar item",
    "lists_remove": "remover item ou lista", "lists_query": "consultar uma lista", "news_query": "ver notícias",
    "alarm_query": "consultar alarmes", "alarm_set": "criar um alarme", "alarm_remove": "apagar um alarme",
    "social_query": "ver redes sociais", "social_post": "publicar ou reclamar numa rede social",
    "calendar_remove": "apagar um compromisso", "calendar_query": "consultar a agenda", "calendar_set": "marcar um compromisso ou lembrete",
}
SLOT_PT = {
    "date": "data", "time": "hora", "timeofday": "período do dia", "person": "pessoa", "relation": "parentesco ou relação",
    "place_name": "lugar", "event_name": "evento ou compromisso", "business_name": "empresa ou estabelecimento",
    "business_type": "tipo de estabelecimento", "house_place": "cômodo da casa", "device_type": "aparelho",
    "media_type": "tipo de mídia", "food_type": "comida", "meal_type": "refeição", "drink_type": "bebida",
    "music_genre": "gênero musical", "artist_name": "artista", "song_name": "música", "music_album": "álbum",
    "music_descriptor": "descrição da música", "playlist_name": "playlist", "radio_name": "rádio",
    "podcast_name": "podcast", "podcast_descriptor": "descrição do podcast", "audiobook_name": "audiolivro",
    "audiobook_author": "autor do audiolivro", "weather_descriptor": "condição do tempo", "list_name": "lista",
    "player_setting": "ajuste do reprodutor", "transport_type": "meio de transporte", "transport_agency": "empresa de transporte",
    "transport_name": "nome do transporte", "transport_descriptor": "descrição do transporte", "definition_word": "palavra",
    "news_topic": "assunto da notícia", "general_frequency": "frequência ou repetição", "currency_name": "moeda",
    "color_type": "cor", "time_zone": "fuso horário", "app_name": "aplicativo", "personal_info": "dado pessoal",
    "change_amount": "quantidade de mudança", "email_address": "endereço de e-mail", "email_folder": "pasta de e-mail",
    "joke_type": "tipo de piada", "game_name": "jogo", "game_type": "tipo de jogo", "order_type": "tipo de pedido",
    "coffee_type": "tipo de café", "movie_name": "filme", "movie_type": "tipo de filme", "ingredient": "ingrediente",
    "cooking_type": "modo de preparo", "sport_type": "esporte", "alarm_type": "tipo de alarme",
}
ASK = {"pt": ["O que a pessoa está pedindo?", "Qual é a ordem dada?", "O que deve ser feito?"],
       "es": ["¿Qué está pidiendo la persona?", "¿Qué hay que hacer?"],
       "en": ["What is the person asking for?", "What should be done?"]}
SLOT_RE = re.compile(r"\[\s*([a-z_]+)\s*:\s*([^\]]*?)\s*\]")


def parse(annot: str):
    """'mova os e-mails da [person : ana] para ...' -> (plain text, {slot: [[start, end], ...]})."""
    out, spans, pos = [], {}, 0
    for m in SLOT_RE.finditer(annot):
        out.append(annot[pos:m.start()])
        start = sum(len(x) for x in out)
        val = m.group(2)
        out.append(val)
        if val:
            spans.setdefault(m.group(1), []).append([start, start + len(val)])
        pos = m.end()
    out.append(annot[pos:])
    text = "".join(out)
    return text, spans


def _download(cache):
    if cache and os.path.exists(cache):
        return open(cache, "rb").read()
    with urllib.request.urlopen(URL, timeout=600) as r:
        b = r.read()
    if cache:
        open(cache, "wb").write(b)
    return b


def build(out_dir, langs=("pt-PT", "es-ES", "en-US"), per_lang=12000, seed=0, cache=None, neg_share=0.3):
    rng = random.Random(seed)
    tf = tarfile.open(fileobj=io.BytesIO(_download(cache)), mode="r:gz")
    by_scn = {}
    for k in INTENT_PT:
        by_scn.setdefault(k.split("_")[0], []).append(k)
    scn_slots = {}
    rows = []
    for mem in tf.getmembers():
        loc = os.path.basename(mem.name).split(".")[0]
        if not mem.isfile() or loc not in langs:
            continue
        for line in tf.extractfile(mem).read().decode("utf-8").splitlines():
            if not line.strip():
                continue
            d = json.loads(line)
            if d.get("partition") not in ("train", "test") or d.get("intent") not in INTENT_PT:
                continue
            text, spans = parse(d.get("annot_utt") or d["utt"])
            if len(text.strip()) < 3:
                continue
            rows.append((loc, d["partition"], d["intent"], text, spans))
            scn_slots.setdefault(d["scenario"], Counter()).update(spans.keys())
    train, test, stats = [], [], Counter()
    for loc in langs:
        mine = [r for r in rows if r[0] == loc]
        rng.shuffle(mine)
        tr = [r for r in mine if r[1] == "train"][:per_lang]
        te = [r for r in mine if r[1] == "test"][: max(200, per_lang // 12)]
        lang = loc.split("-")[0]
        for split, part, sink in (("train", tr, train), ("test", te, test)):
            for _, _, intent, text, spans in part:
                meta = {"area": "commands", "lang": lang, "provenance": f"massive:{loc}", "license": LICENSE,
                        "intent": intent}
                scn = intent.split("_")[0]
                same = [x for x in by_scn[scn] if x != intent]
                other = [x for x in INTENT_PT if x.split("_")[0] != scn]
                dis = rng.sample(same, min(2, len(same))) + rng.sample(other, 5 - min(2, len(same)))
                keys = [intent] + dis
                rng.shuffle(keys)
                sink.append({"task": "CHOICE", "split": split, "source": text, "options": [INTENT_PT[k] for k in keys],
                             "label": INTENT_PT[intent], "ask": rng.choice(ASK[lang]), "meta": meta})
                stats[f"{split}:CHOICE"] += 1
                for slot, sp in spans.items():
                    sink.append({"task": "resolve", "split": split, "mode": "ENTITY", "target": SLOT_PT.get(slot, slot.replace("_", " ")),
                                 "source": text, "spans": sorted(sp), "polarity": ["asserted"] * len(sp),
                                 "meta": dict(meta, slot=slot)})
                    stats[f"{split}:resolve"] += 1
                common = [s for s, _ in scn_slots.get(scn, Counter()).most_common(6) if s not in spans]
                if common and rng.random() < neg_share:            # a slot this kind of command often has, absent here
                    s = rng.choice(common)
                    sink.append({"task": "resolve", "split": split, "mode": "ENTITY", "target": SLOT_PT.get(s, s),
                                 "source": text, "spans": [], "polarity": [], "meta": dict(meta, slot=s)})
                    stats[f"{split}:resolve_negative"] += 1
    rng.shuffle(train)
    os.makedirs(out_dir, exist_ok=True)
    for fn, recs in (("commands_train.jsonl", train), ("commands_test.jsonl", test)):
        with open(os.path.join(out_dir, fn), "w", encoding="utf-8") as f:
            for r in recs:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
    man = {"source": URL, "license": LICENSE, "langs": list(langs), "train": len(train), "test": len(test),
           "records": dict(stats), "attribution": "MASSIVE (FitzGerald et al., 2022), SLURP (Bastianelli et al., 2020)"}
    json.dump(man, open(os.path.join(out_dir, "commands.manifest.json"), "w"), indent=1, ensure_ascii=False)
    print(json.dumps(man, ensure_ascii=False))
    return man


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--langs", default="pt-PT,es-ES,en-US")
    ap.add_argument("--per-lang", type=int, default=12000)
    ap.add_argument("--cache", default=None, help="where to keep the downloaded archive (e.g. on Drive)")
    a = ap.parse_args(argv)
    build(a.out_dir, tuple(x.strip() for x in a.langs.split(",") if x.strip()), a.per_lang, cache=a.cache)


if __name__ == "__main__":
    main()
