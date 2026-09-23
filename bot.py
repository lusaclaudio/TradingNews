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
  MAX_AGE_MIN         default: 20  (ignora news pubblicate più di N minuti fa: solo breaking)
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
    s.setdefault("sent", [])     # news inviate: {t, titolo, orig, storia}
    s["sent"] = [x if isinstance(x, dict) else {"t": time.time(), "titolo": x, "orig": x, "storia": ""}
                 for x in s["sent"]]
    return s


def save_state(path, s):
    cutoff = time.time() - 3 * 86400
    s["seen"] = {k: v for k, v in s["seen"].items() if v > cutoff}
    s["sent"] = [x for x in s["sent"] if x.get("t", 0) > time.time() - 24 * 3600][-80:]
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(s, f)
    os.replace(tmp, path)


# ---------------------------------------------------------------- feed
def clean(text, n=1500):
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = html.unescape(re.sub(r"\s+", " ", text)).strip()
    return text[:n]


STOP = set("""the and for with from that this after over into amid says said will would could about their
have has had been more than what when your just also news live update updates breaking report reports
della delle degli dopo sulla sullo sono come alla alle nella nelle anche contro""".split())


def words(text):
    return {w for w in re.findall(r"[a-z0-9%$.]+", (text or "").lower())
            if len(w) >= 3 and w not in STOP}


def similar(a, b, thr=0.45):
    a, b = words(a), words(b)
    if not a or not b:
        return False
    return len(a & b) / min(len(a), len(b)) >= thr and len(a & b) >= 3


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
Valuta queste news appena uscite. Il trader vuole solo BREAKING NEWS: fatti nuovi appena accaduti.
Per ciascuna dai uno "score" 1-10 di impatto IMMEDIATO sui mercati:
- 9-10: evento che muove tutto (dazi nuovi/annullati, attacco militare importante, decisione Fed a sorpresa, crollo/halt)
- 7-8: news rilevante che può muovere indici o un settore chiave (AI/semiconduttori, petrolio, bond)
- 1-6: rumore, opinioni, analisi, anteprime, riepiloghi ("markets wrap", "what to watch", "why X is moving"),
  aggiornamenti minori di una storia già nota, post politici senza impatto economico
Sii SEVERO: il trader vuole poche notifiche, solo quelle che contano.

DOPPIONI (importantissimo): se una news racconta lo STESSO EVENTO di una già inviata (lista sotto), anche con
parole diverse, da un'altra fonte o con un dettaglio in più, dai score 1. Dai score alto solo se c'è uno
sviluppo NUOVO e importante (es. prima "Trump minaccia dazi", poi "Trump firma i dazi" = nuova).
Se nel lotto più news raccontano lo stesso evento, dai lo score alto solo alla più completa, 1 alle altre.

Già inviate nelle ultime 24 ore:
{sent}

News nuove (JSON):
{items}

Rispondi SOLO con un array JSON, un oggetto per news. Per le news con score < 7 metti solo id e score
(niente altri campi), per risparmiare. Formato:
[{{"id":"...","score":8,
"storia":"4-6 parole chiave in inglese minuscolo che identificano l'evento, es. 'trump china tariffs 100 november'",
"titolo":"titolo chiaro in italiano",
"cosa":"3-4 frasi in italiano, comprensibili anche a chi non segue la vicenda: chi ha fatto/detto cosa, con numeri, date e nomi precisi; il contesto essenziale (cosa era successo prima, cosa ci si aspettava). Niente sigle non spiegate.",
"perche":"1-2 frasi: perché questa news muove i mercati e cosa può succedere dopo",
"impatto":"asset coinvolti e direzione probabile, es. 'NQ ↓, Oro ↑, USD ↑'"}}]"""


def build_prompt(items, sent):
    payload = [{"id": i["id"], "fonte": i["source"], "categoria": i["cat"],
                "titolo": i["title"], "testo": i["body"][:1200]} for i in items]
    return PROMPT.format(sent="\n".join(f"- {x['titolo']} [{x.get('storia', '')}]" for x in sent[-40:])
                         or "(nessuna)",
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
    def e(t, quote=False):
        return html.escape(t, quote=quote)
    lines = [f"{icon} <b>{e(ev.get('titolo') or item['title'])}</b>",
             f"<i>{e(item['cat'])} · {e(item['source'])} · impatto {s}/10 · "
             f"{max(0, int((time.time() - item['ts']) // 60))} min fa</i>"]
    if ev.get("cosa"):
        lines += ["", e(ev["cosa"])]
    if ev.get("perche"):
        lines += ["", "💡 <b>Perché conta:</b> " + e(ev["perche"])]
    if ev.get("impatto"):
        lines += ["", "📊 " + e(ev["impatto"])]
    if item["cat"] == "Trump" and not ev.get("cosa") and ev.get("titolo") != item["title"]:
        lines += ["", "“" + e(item["title"][:500]) + "”"]
    if item["link"]:
        lines += ["", f'<a href="{e(item["link"], quote=True)}">Fonte</a>']
    return "\n".join(lines)


# ---------------------------------------------------------------- main
def is_dup(title, sent):
    return any(similar(title, x.get("orig", "")) or similar(title, x.get("titolo", "")) for x in sent)


def run_once(cfg, state):
    items = fetch_all(cfg["max_age"])
    new = [i for i in items if i["id"] not in state["seen"]]
    # 1) doppioni evidenti, senza AI: titolo simile a una news già inviata o già in coda
    uniq = []
    for i in sorted(new, key=lambda x: -len(x["body"])):   # tieni la versione più completa
        if is_dup(i["title"], state["sent"]) or any(similar(i["title"], u["title"]) for u in uniq):
            state["seen"][i["id"]] = time.time()
            continue
        uniq.append(i)
    print(f"[info] {len(items)} news ultimi {cfg['max_age']} min, {len(uniq)} nuove, "
          f"{len(state['seen'])} già viste")
    if not uniq:
        return

    evals = {}
    use_ai = bool(cfg["gemini_key"] or cfg["api_key"])
    if not use_ai:
        evals = score_with_keywords(uniq)
    elif time.time() - state.get("last_ai", 0) >= cfg["ai_interval"]:
        for n in range(0, len(uniq), 40):        # lotti da 40
            batch = uniq[n:n + 40]
            state["last_ai"] = time.time()
            try:
                res = score_with_ai(batch, state["sent"], cfg)
                for i in batch:                  # news ignorate dall'AI = irrilevanti
                    evals[i["id"]] = res.get(i["id"], {"id": i["id"], "score": 0})
            except Exception as e:
                print(f"[warn] AI non disponibile, riprovo al prossimo giro: {e}", file=sys.stderr)
    # altrimenti: troppo presto per chiamare l'AI, le news restano in coda

    # 2) invio: dalla più importante; salta gli eventi già inviati (anche in questo giro)
    ranked = sorted((i for i in uniq if i["id"] in evals),
                    key=lambda i: -int(evals[i["id"]].get("score", 0) or 0))
    for i in ranked:
        ev = evals[i["id"]]
        state["seen"][i["id"]] = time.time()
        score = int(ev.get("score", 0) or 0)
        if score < cfg["min_score"]:
            continue
        storia = ev.get("storia", "")
        if is_dup(i["title"], state["sent"]) or (storia and any(
                similar(storia, x.get("storia", ""), 0.6) for x in state["sent"])):
            print(f"[dup] {i['title'][:90]}")
            continue
        send_telegram(cfg["token"], cfg["chat_id"], format_msg(i, ev), cfg["dry"])
        state["sent"].append({"t": time.time(), "titolo": ev.get("titolo") or i["title"][:150],
                              "orig": i["title"][:200], "storia": storia})
        print(f"[sent] {score}/10 {i['title'][:90]}")


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
        "max_age": int(os.getenv("MAX_AGE_MIN") or "20"),
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
    print(f"[info] stato caricato: {len(state['seen'])} news già viste, {len(state['sent'])} inviate nelle ultime 24h")
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
