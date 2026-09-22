#!/usr/bin/env python3
"""
Market News Bot — ti scrive su Telegram quando esce una news che può muovere il mercato.

Fonti: Truth Social di Trump, investingLive (ex ForexLive), CNBC, Fed, Google News
(geopolitica / AI / macro). Le news nuove vengono valutate da un'AI (Google Gemini,
gratis) con uno score 1-10 e riassunte in italiano; sopra la soglia arriva il messaggio.

Uso:
  python bot.py              # un solo controllo
  python bot.py --loop 60    # controlla ogni 60s, all'infinito (PC / VPS)
  python bot.py --test       # manda un messaggio di prova su Telegram
  python bot.py --dry-run    # stampa invece di inviare (per provare)

Variabili d'ambiente:
  TELEGRAM_TOKEN      (obbligatoria) token da @BotFather
  TELEGRAM_CHAT_ID    (obbligatoria) il tuo chat id
  GEMINI_API_KEY      (consigliata, gratis da aistudio.google.com) senza: filtro a parole chiave
  GEMINI_MODEL        default: gemini-flash-lite-latest
  ANTHROPIC_API_KEY   (facoltativa, a pagamento) usata solo se non c'è GEMINI_API_KEY
  CLAUDE_MODEL        default: claude-haiku-4-5
  LLM_INTERVAL        default: 120 (secondi minimi tra due chiamate all'AI, per stare nei limiti gratis)
  MIN_SCORE           default: 7   (soglia 1-10 per ricevere la notifica)
  MAX_AGE_MIN         default: 120 (ignora news più vecchie di N minuti)
  RUN_MINUTES         default: 0   (con --loop: esce dopo N minuti; 0 = mai)
  STATE_FILE          default: state.json
"""
import argparse
import calendar
import hashlib
import html
import json
import os
import re
import sys
import time
from urllib.parse import quote_plus

import feedparser
import requests

UA = {"User-Agent": "Mozilla/5.0 (MarketNewsBot)"}


def gnews(q):
    return ("https://news.google.com/rss/search?q=" + quote_plus(q + " when:1h")
            + "&hl=en-US&gl=US&ceid=US:en")


# (nome, url, categoria)
FEEDS = [
    ("Trump (Truth Social)", "https://www.trumpstruth.org/feed", "Trump"),
    ("investingLive", "https://investinglive.com/feed/news/", "Mercati"),
    ("investingLive CB", "https://investinglive.com/feed/centralbank/", "Banche centrali"),
    ("CNBC", "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114", "Mercati"),
    ("Federal Reserve", "https://www.federalreserve.gov/feeds/press_all.xml", "Banche centrali"),
    ("Google News – Guerra", gnews('(war OR missile OR airstrike OR invasion OR ceasefire OR "nuclear") '
                                  '(Iran OR Israel OR Russia OR Ukraine OR China OR Taiwan OR NATO)'), "Geopolitica"),
    ("Google News – AI", gnews('(Nvidia OR OpenAI OR Anthropic OR "AI chips" OR "export controls" '
                               'OR "data center" OR TSMC) (surge OR plunge OR ban OR deal OR announces)'), "AI/Tech"),
    ("Google News – Macro", gnews('(Fed OR Powell OR tariffs OR CPI OR "jobs report" OR recession '
                                  'OR "rate cut" OR "rate hike") breaking'), "Macro"),
]

# Filtro di riserva se non c'è la API key di Claude
KEYWORDS = {
    3: ["tariff", "dazi", "fed ", "powell", "rate cut", "rate hike", "emergency", "invasion",
        "nuclear", "missile", "strike on", "attack on", "declares war", "ceasefire", "sanction", "export control",
        "default", "shutdown", "halt", "crash", "plunge", "circuit breaker"],
    2: ["china", "xi ", "iran", "israel", "russia", "ukraine", "taiwan", "opec", "nvidia",
        "openai", "anthropic", " oil ", "crude", "cpi", "inflation", "payroll", "jobs report", "recession", "treasury",
        "stock market", " dow ", "nasdaq", "s&p"],
}


# ---------------------------------------------------------------- stato
def load_state(path):
    try:
        with open(path) as f:
            s = json.load(f)
    except Exception:
        s = {}
    s.setdefault("seen", {})     # id -> timestamp
    s.setdefault("sent", [])     # ultimi titoli inviati (per evitare doppioni)
    return s


def save_state(path, s):
    cutoff = time.time() - 3 * 86400
    s["seen"] = {k: v for k, v in s["seen"].items() if v > cutoff}
    s["sent"] = s["sent"][-40:]
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(s, f)
    os.replace(tmp, path)


# ---------------------------------------------------------------- feed
def clean(text, n=500):
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = html.unescape(re.sub(r"\s+", " ", text)).strip()
    return text[:n]


def fetch_all(max_age_min):
    items, now = [], time.time()
    for name, url, cat in FEEDS:
        try:
            r = requests.get(url, headers=UA, timeout=20)
            r.raise_for_status()
            feed = feedparser.parse(r.content)
        except Exception as e:
            print(f"[warn] {name}: {e}", file=sys.stderr)
            continue
        for e in feed.entries[:30]:
            t = e.get("published_parsed") or e.get("updated_parsed")
            ts = calendar.timegm(t) if t else now
            if now - ts > max_age_min * 60:
                continue
            title = clean(e.get("title", ""), 300)
            body = clean(e.get("summary", "") or e.get("description", ""))
            # i post di Trump spesso hanno titolo vuoto o troncato: usa il testo
            if cat == "Trump" and (not title or len(body) > len(title)):
                title = body[:300]
            if not title:
                continue
            key = e.get("id") or e.get("link") or title
            uid = hashlib.sha1((name + key).encode()).hexdigest()[:16]
            items.append({"id": uid, "source": name, "cat": cat, "title": title,
                          "body": body, "link": e.get("link", ""), "ts": ts})
    return items


# ---------------------------------------------------------------- valutazione
PROMPT = """Sei un analista di mercato per un trader di futures (ES, NQ, YM, FDAX, oro, petrolio, EUR/USD).
Valuta queste news appena uscite. Per ciascuna dai uno "score" 1-10 di impatto IMMEDIATO sui mercati:
- 9-10: evento che muove tutto (dazi nuovi/annullati, attacco militare importante, decisione Fed a sorpresa, crollo/halt)
- 7-8: news rilevante che può muovere indici o un settore chiave (AI/semiconduttori, petrolio, bond)
- 1-6: rumore, opinioni, analisi, notizie già note, post politici senza impatto economico
Sii SEVERO: il trader vuole poche notifiche, solo quelle che contano.
Se una news è la stessa storia di una già inviata (lista "già inviate") o di un'altra nel lotto, dai score 1 ai doppioni.

Già inviate di recente:
{sent}

News nuove (JSON):
{items}

Rispondi SOLO con un array JSON, un oggetto per news. Per le news con score < 7 metti solo id e score
(niente altri campi), per risparmiare. Formato:
[{{"id":"...","score":8,"titolo":"titolo breve in italiano","cosa":"1-2 frasi in italiano: cosa è successo e perché conta",
"impatto":"asset coinvolti e direzione probabile, es. 'NQ ↓, Oro ↑, USD ↑'"}}]"""


def build_prompt(items, sent):
    payload = [{"id": i["id"], "fonte": i["source"], "categoria": i["cat"],
                "titolo": i["title"], "testo": i["body"][:400]} for i in items]
    return PROMPT.format(sent="\n".join("- " + s for s in sent[-20:]) or "(nessuna)",
                         items=json.dumps(payload, ensure_ascii=False))


def parse_json_list(text):
    m = re.search(r"\[.*\]", text, re.S)
    if not m:
        raise ValueError("risposta AI senza JSON: " + text[:200])
    return {str(d["id"]): d for d in json.loads(m.group(0)) if isinstance(d, dict) and "id" in d}


GEMINI_FALLBACK = ["gemini-flash-lite-latest", "gemini-2.5-flash-lite", "gemini-flash-latest"]


def ask_gemini(prompt, api_key, model):
    models = [model] + [m for m in GEMINI_FALLBACK if m != model]
    last = None
    for m in models:
        r = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent",
            headers={"x-goog-api-key": api_key, "content-type": "application/json"},
            json={"contents": [{"parts": [{"text": prompt}]}],
                  "generationConfig": {"temperature": 0.2,
                                       "responseMimeType": "application/json"}},
            timeout=90)
        if r.status_code == 404:          # modello non più disponibile: prova il prossimo
            last = f"modello {m} non trovato"
            continue
        if not r.ok:
            raise RuntimeError(f"Gemini {r.status_code}: {r.text[:300]}")
        parts = r.json()["candidates"][0]["content"]["parts"]
        return "".join(p.get("text", "") for p in parts)
    raise RuntimeError(last)


def ask_claude(prompt, api_key, model):
    r = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={"x-api-key": api_key, "anthropic-version": "2023-06-01",
                 "content-type": "application/json"},
        json={"model": model, "max_tokens": 4000,
              "messages": [{"role": "user", "content": prompt}]},
        timeout=90)
    if not r.ok:
        raise RuntimeError(f"Claude {r.status_code}: {r.text[:300]}")
    return "".join(b.get("text", "") for b in r.json()["content"])


def score_with_ai(items, sent, cfg):
    prompt = build_prompt(items, sent)
    if cfg["gemini_key"]:
        return parse_json_list(ask_gemini(prompt, cfg["gemini_key"], cfg["gemini_model"]))
    return parse_json_list(ask_claude(prompt, cfg["api_key"], cfg["model"]))


def score_with_keywords(items):
    out = {}
    for i in items:
        t = " " + (i["title"] + " " + i["body"]).lower() + " "
        s = sum(w for w, words in KEYWORDS.items() for k in words if k in t)
        if i["cat"] == "Trump":
            s += 2
        out[i["id"]] = {"id": i["id"], "score": min(10, 3 + s), "titolo": i["title"],
                        "cosa": "", "impatto": ""}
    return out


# ---------------------------------------------------------------- telegram
def send_telegram(token, chat_id, text, dry=False):
    if dry:
        print("----- TELEGRAM -----\n" + text + "\n--------------------")
        return
    r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                      json={"chat_id": chat_id, "text": text, "parse_mode": "HTML",
                            "disable_web_page_preview": True}, timeout=20)
    if not r.ok:
        print(f"[err] Telegram: {r.status_code} {r.text}", file=sys.stderr)


def format_msg(item, ev):
    s = int(ev.get("score", 0))
    icon = "🚨" if s >= 9 else "⚠️"
    e = html.escape
    lines = [f"{icon} <b>{e(ev.get('titolo') or item['title'])}</b>",
             f"<i>{e(item['cat'])} · {e(item['source'])} · impatto {s}/10</i>"]
    if ev.get("cosa"):
        lines += ["", e(ev["cosa"])]
    if ev.get("impatto"):
        lines += ["", "📊 " + e(ev["impatto"])]
    if item["cat"] == "Trump" and not ev.get("cosa") and ev.get("titolo") != item["title"]:
        lines += ["", "“" + e(item["title"][:500]) + "”"]
    if item["link"]:
        lines += ["", f'<a href="{e(item["link"], quote=True)}">Fonte</a>']
    return "\n".join(lines)


# ---------------------------------------------------------------- main
def run_once(cfg, state):
    items = fetch_all(cfg["max_age"])
    new = [i for i in items if i["id"] not in state["seen"]]
    # dedup titoli identici tra fonti diverse
    uniq, titles = [], set()
    for i in sorted(new, key=lambda x: x["ts"]):
        k = re.sub(r"\W+", "", i["title"].lower())[:80]
        if k not in titles:
            titles.add(k)
            uniq.append(i)
    print(f"[info] {len(items)} news recenti, {len(uniq)} nuove")
    if not uniq:
        return

    evals = {}
    use_ai = bool(cfg["gemini_key"] or cfg["api_key"])
    if use_ai and time.time() - state.get("last_ai", 0) < cfg["ai_interval"]:
        # troppo presto per un'altra chiamata: le news restano in coda per il prossimo giro
        # (tranne quelle ferme da troppo, che passano al filtro a parole chiave)
        stale = [i for i in uniq if time.time() - i["ts"] > 45 * 60]
        evals.update(score_with_keywords(stale))
        uniq_ai = []
    else:
        uniq_ai = uniq if use_ai else []
        if not use_ai:
            evals.update(score_with_keywords(uniq))
    for n in range(0, len(uniq_ai), 40):        # lotti da 40
        batch = uniq_ai[n:n + 40]
        state["last_ai"] = time.time()
        try:
            res = score_with_ai(batch, state["sent"], cfg)
            for i in batch:                      # news ignorate dall'AI = irrilevanti
                evals[i["id"]] = res.get(i["id"], {"id": i["id"], "score": 0})
        except Exception as e:
            print(f"[warn] AI non disponibile: {e}", file=sys.stderr)
            # riprova al prossimo giro; se la news è vecchia usa le parole chiave
            evals.update(score_with_keywords([i for i in batch if time.time() - i["ts"] > 45 * 60]))

    for i in uniq:
        ev = evals.get(i["id"])
        if ev is None:           # non valutata: riprova al giro dopo
            continue
        state["seen"][i["id"]] = time.time()
        if int(ev.get("score", 0)) >= cfg["min_score"]:
            send_telegram(cfg["token"], cfg["chat_id"], format_msg(i, ev), cfg["dry"])
            state["sent"].append(ev.get("titolo") or i["title"][:150])
            print(f"[sent] {ev.get('score')}/10 {i['title'][:90]}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--loop", type=int, default=0, help="secondi tra un controllo e l'altro")
    ap.add_argument("--test", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    cfg = {
        "token": os.getenv("TELEGRAM_TOKEN", ""),
        "chat_id": os.getenv("TELEGRAM_CHAT_ID", ""),
        "gemini_key": os.getenv("GEMINI_API_KEY", ""),
        "gemini_model": os.getenv("GEMINI_MODEL") or "gemini-flash-lite-latest",
        "api_key": os.getenv("ANTHROPIC_API_KEY", ""),
        "ai_interval": int(os.getenv("LLM_INTERVAL") or "120"),
        "model": os.getenv("CLAUDE_MODEL") or "claude-haiku-4-5",
        "min_score": int(os.getenv("MIN_SCORE") or "7"),
        "max_age": int(os.getenv("MAX_AGE_MIN", "120")),
        "run_min": float(os.getenv("RUN_MINUTES", "0")),
        "state": os.getenv("STATE_FILE", "state.json"),
        "dry": a.dry_run,
    }
    if not a.dry_run and not (cfg["token"] and cfg["chat_id"]):
        sys.exit("Mancano TELEGRAM_TOKEN e/o TELEGRAM_CHAT_ID")

    if a.test:
        send_telegram(cfg["token"], cfg["chat_id"],
                      "✅ <b>Market News Bot attivo</b>\nFiltro: "
                      + ("Gemini AI" if cfg["gemini_key"] else "Claude AI" if cfg["api_key"] else "parole chiave")
                      + f" · soglia {cfg['min_score']}/10", cfg["dry"])
        return

    state = load_state(cfg["state"])
    start = time.time()
    while True:
        try:
            run_once(cfg, state)
        except Exception as e:
            print(f"[err] {e}", file=sys.stderr)
        save_state(cfg["state"], state)
        if not a.loop:
            break
        if cfg["run_min"] and time.time() - start > cfg["run_min"] * 60:
            break
        time.sleep(a.loop)


if __name__ == "__main__":
    main()
