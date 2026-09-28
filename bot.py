#!/usr/bin/env python3
"""
Market News Bot — ti scrive su Telegram quando esce una news che può muovere il mercato.

Fonti: Walter Bloomberg e First Squawk (copie Telegram dei canali X), Truth Social di Trump,
investingLive (ex ForexLive), Fed. Le news nuove vengono valutate da un'AI (Google Gemini,
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
  GROQ_API_KEY        (facoltativa, gratis da console.groq.com) riserva se Gemini è sovraccarico
  ANTHROPIC_API_KEY   (facoltativa, a pagamento) ultima riserva
  CLAUDE_MODEL        default: claude-haiku-4-5
  LLM_INTERVAL        default: 120 (secondi minimi tra due chiamate all'AI, per stare nei limiti gratis)
  MIN_SCORE           default: 8   (soglia 1-10 per ricevere la notifica)
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
from datetime import datetime
from urllib.parse import quote_plus

import feedparser
import requests

UA = {"User-Agent": "Mozilla/5.0 (MarketNewsBot)"}


def gnews(q):
    return ("https://news.google.com/rss/search?q=" + quote_plus(q + " when:1h")
            + "&hl=en-US&gl=US&ceid=US:en")


# (nome, url, categoria)  —  "tg:<canale>" = canale Telegram pubblico (letto da t.me/s/<canale>)
FEEDS = [
    ("Walter Bloomberg", "tg:WalterBloomberg", "Flash"),
    ("First Squawk", "tg:firstsquaw", "Flash"),
    ("Trump (Truth Social)", "https://www.trumpstruth.org/feed", "Trump"),
    ("investingLive", "https://investinglive.com/feed/news/", "Mercati"),
    ("investingLive CB", "https://investinglive.com/feed/centralbank/", "Banche centrali"),
    ("Federal Reserve", "https://www.federalreserve.gov/feeds/press_all.xml", "Banche centrali"),
    # Fonti tolte perché lente / piene di doppioni (riattivabili togliendo il #):
    # ("CNBC", "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114", "Mercati"),
    # ("Google News – Guerra", gnews('(war OR missile OR airstrike OR invasion OR ceasefire) (Iran OR Israel OR Russia OR Ukraine OR China OR Taiwan)'), "Geopolitica"),
]

# Filtro di riserva a parole chiave: usato se non c'è una chiave AI, o se l'AI è giù (in quel caso
# passano solo le news con punteggio molto alto). Sono regex su parole intere, testo in minuscolo.
KEYWORDS = {
    3: [  # eventi che muovono il mercato da soli
        r"tariffs?", r"sanctions?", r"export controls?", r"embargo",
        r"fomc", r"warsh", r"rate (cut|hike)s?", r"(cut|cuts|hike|hikes|raise|raises|lower|lowers) (interest )?rates?", r"emergency (meeting|cut)",
        r"invasion", r"invades?", r"declares? war", r"nuclear", r"missiles?", r"air ?strikes?", r"(strike|attack)s? on",
        r"ceasefire", r"truce", r"blockade", r"hormuz",
        r"shutdown", r"debt ceiling", r"downgrades?", r"defaults? on",
        r"trading halt", r"halts? trading", r"circuit breaker", r"crash(es)?", r"plunges?", r"soars?",
        r"nonfarm", r"payrolls?", r"cpi", r"pce", r"gdp",
    ],
    2: [  # attori e temi importanti, contano solo in combinazione
        r"trump", r"white house", r"xi", r"putin", r"netanyahu", r"khamenei",
        r"fed", r"powell", r"ecb", r"lagarde", r"boj", r"ueda", r"pboc", r"treasury", r"bessent",
        r"china", r"chinese", r"iran", r"israel", r"russia", r"ukraine", r"taiwan", r"north korea", r"houthis?", r"red sea",
        r"opec\+?", r"oil", r"crude", r"brent", r"wti", r"gold", r"yields?", r"10-year", r"bonds?",
        r"inflation", r"jobs report", r"unemployment", r"jobless claims", r"retail sales", r"ism", r"recession",
        r"nvidia", r"tsmc", r"openai", r"anthropic", r"microsoft", r"apple", r"alphabet", r"google", r"meta",
        r"amd", r"broadcom", r"semiconductors?", r"chips?", r"guidance", r"bans?",
        r"stocks?", r"s&p", r"nasdaq", r"dow", r"futures",
    ],
}
KEYWORDS_RE = {w: [re.compile(r"(?<![a-z0-9])" + k + r"(?![a-z0-9])") for k in ks] for w, ks in KEYWORDS.items()}


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


def similar(a, b, thr=0.7):
    """True solo per titoli quasi identici (stesso fatto riscritto): il resto lo giudica l'AI."""
    a, b = words(a), words(b)
    if len(a) < 3 or len(b) < 3:
        return False
    return len(a & b) / len(a | b) >= thr


def fetch_telegram_channel(channel):
    """Legge gli ultimi post dalla pagina pubblica t.me/s/<canale>. Ritorna (id, testo, ts, link)."""
    r = requests.get(f"https://t.me/s/{channel}", headers=UA, timeout=20)
    r.raise_for_status()
    posts = []
    for chunk in r.text.split('data-post="')[1:]:
        pid = chunk.split('"', 1)[0]
        m_text = re.search(r'<div class="tgme_widget_message_text js-message_text[^"]*"[^>]*>(.*?)</div>', chunk, re.S)
        m_time = re.search(r'<time[^>]*datetime="([^"]+)"', chunk)
        if not m_text or not m_time:
            continue
        text = re.sub(r"<br\s*/?>", " ", m_text.group(1))
        try:
            ts = datetime.fromisoformat(m_time.group(1)).timestamp()
        except ValueError:
            continue
        posts.append((pid, clean(text), ts, f"https://t.me/{pid}"))
    if not posts:
        raise ValueError("nessun post trovato (pagina cambiata o canale chiuso?)")
    return posts


_reported = set()


def fetch_all(max_age_min):
    items, now = [], time.time()
    for name, url, cat in FEEDS:
        if url.startswith("tg:"):
            try:
                posts = fetch_telegram_channel(url[3:])
            except Exception as e:
                print(f"[warn] {name}: {e}", file=sys.stderr)
                continue
            newest = max(p[2] for p in posts)
            if name not in _reported:
                _reported.add(name)
                print(f"[info] {name}: {len(posts)} post letti, l'ultimo di {int((now - newest) // 60)} min fa")
            for pid, text, ts, link in posts:
                if now - ts > max_age_min * 60 or not text:
                    continue
                uid = hashlib.sha1((name + pid).encode()).hexdigest()[:16]
                items.append({"id": uid, "source": name, "cat": cat, "title": text[:300],
                              "body": text, "link": link, "ts": ts})
            continue
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
# Contesto attuale da tenere aggiornato: l'AI può avere conoscenze vecchie sui ruoli delle persone.
CONTESTO = """- Presidente della Fed: Kevin Warsh (in carica dal 22 maggio 2026). Jerome Powell non è più presidente,
  resta solo membro del board: le sue parole contano molto meno.
- Presidente della BCE: Christine Lagarde (ha annunciato che lascerà nel 2027).
- Presidente USA: Donald Trump."""

PROMPT = """Data di oggi: {oggi}. Contesto attuale (più aggiornato delle tue conoscenze):
{contesto}

Sei un analista di mercato per un trader di futures (ES, NQ, YM, FDAX, oro, petrolio, EUR/USD).
Valuta queste news appena uscite. Il trader vuole solo BREAKING NEWS: fatti nuovi appena accaduti.
Per ciascuna dai uno "score" 1-10 di impatto IMMEDIATO sui mercati:
- 9-10: evento che muove tutto (dazi nuovi/annullati, attacco militare importante, decisione Fed a sorpresa, crollo/halt,
  svolte su guerre/negoziati, es. "US OFFICIAL: TRUMP READY TO EASE IRAN SANCTIONS" = 9)
- 7-8: news rilevante che può muovere indici o un settore chiave (AI/semiconduttori, petrolio, bond, Stretto di Hormuz,
  dichiarazioni ufficiali di Casa Bianca/Fed/BCE/governi su dazi, sanzioni, conflitti)
I titoli in MAIUSCOLO dai canali "Flash" (Walter Bloomberg, First Squawk) sono dichiarazioni appena uscite: valutale
per il loro contenuto, anche se sono brevi.
- 1-6: rumore, opinioni, analisi, anteprime, riepiloghi ("markets wrap", "what to watch", "why X is moving"),
  aggiornamenti minori di una storia già nota, post politici senza impatto economico
Sii SEVERO: il trader vuole poche notifiche, solo quelle che contano.

Lo "score" misura SOLO l'importanza della news, NON se è un doppione.

DOPPIONI: metti "doppione": true solo se la news riporta LO STESSO FATTO di una già inviata (lista sotto, con orario),
cioè stessa persona, stessa azione, stessi dettagli, solo riscritta o ripresa da un'altra fonte.
È NUOVA (doppione false) se aggiunge un fatto rilevante: una conferma ufficiale, una cifra, una condizione, una
reazione, un passo successivo (es. "USA pronti ad allentare sanzioni all'Iran" e poi "Trump offre aiuti economici
all'Iran in cambio di concessioni nucleari" = nuova). Nel dubbio: false. Meglio un doppione che una news persa.
Se nel lotto più news riportano lo stesso fatto, doppione false solo alla più completa.

Già inviate nelle ultime 3 ore:
{sent}

News nuove (JSON):
{items}

Rispondi SOLO con un array JSON, un oggetto per news. Per le news con score < 7 metti solo id e score
(niente altri campi), per risparmiare. Formato:
[{{"id":"...","score":8,"doppione":false,"doppione_di":"se doppione, il titolo della news già inviata",
"storia":"4-6 parole chiave in inglese minuscolo che identificano l'evento, es. 'trump china tariffs 100 november'",
"titolo":"titolo chiaro in italiano",
"cosa":"3-4 frasi in italiano, comprensibili anche a chi non segue la vicenda: chi ha fatto/detto cosa, con numeri, date e nomi precisi; il contesto essenziale (cosa era successo prima, cosa ci si aspettava). Niente sigle non spiegate.",
"perche":"1-2 frasi: perché questa news muove i mercati e cosa può succedere dopo",
"impatto":"asset coinvolti e direzione probabile, es. 'NQ ↓, Oro ↑, USD ↑'"}}]"""


def build_prompt(items, sent):
    payload = [{"id": i["id"], "fonte": i["source"], "categoria": i["cat"],
                "titolo": i["title"], "testo": i["body"][:1200]} for i in items]
    return PROMPT.format(oggi=time.strftime("%d/%m/%Y"), contesto=CONTESTO, sent="\n".join(
        f"- {time.strftime('%H:%M', time.gmtime(x['t']))} UTC: {x['titolo']} ({x.get('orig', '')[:120]})"
        for x in sent if x.get("t", 0) > time.time() - 3 * 3600)
                         or "(nessuna)",
                         items=json.dumps(payload, ensure_ascii=False))


def parse_json_list(text):
    m = re.search(r"\[.*\]", text, re.S)
    if not m:
        raise ValueError("risposta AI senza JSON: " + text[:200])
    return {str(d["id"]): d for d in json.loads(m.group(0)) if isinstance(d, dict) and "id" in d}


# Ordine di prova (settembre 2026). Se tutti falliscono, il bot chiede a Google la lista dei modelli attuali.
GEMINI_FALLBACK = ["gemini-flash-lite-latest", "gemini-3.5-flash-lite", "gemini-3.1-flash-lite",
                   "gemini-flash-latest", "gemini-3.8-flash", "gemini-3.5-flash"]
_discovered = []


def discover_gemini_models(api_key):
    """Modelli Flash di testo disponibili per questa chiave, dal più leggero."""
    try:
        r = requests.get("https://generativelanguage.googleapis.com/v1beta/models",
                         headers={"x-goog-api-key": api_key}, params={"pageSize": 200}, timeout=20)
        r.raise_for_status()
    except Exception as e:
        print(f"[warn] lista modelli Gemini non disponibile: {e}", file=sys.stderr)
        return []
    out = []
    for m in r.json().get("models", []):
        name = m.get("name", "").replace("models/", "")
        if ("generateContent" in m.get("supportedGenerationMethods", []) and "flash" in name
                and not re.search(r"image|tts|audio|live|embed|preview|exp|thinking", name)):
            out.append(name)
    return sorted(out, key=lambda n: ("lite" not in n, n))


def ask_gemini(prompt, api_key, model):
    tried, last = [], None
    queue = [model] + [m for m in GEMINI_FALLBACK if m != model] + _discovered
    discovered = bool(_discovered)
    while queue:
        m = queue.pop(0)
        if m in tried:
            continue
        tried.append(m)
        r = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent",
            headers={"x-goog-api-key": api_key, "content-type": "application/json"},
            json={"contents": [{"parts": [{"text": prompt}]}],
                  "generationConfig": {"temperature": 0.2,
                                       "responseMimeType": "application/json"}},
            timeout=90)
        if r.status_code in (404, 429, 500, 503):
            last = f"{m}: {r.status_code}"
            why = "non disponibile" if r.status_code == 404 else "sovraccarico/quota"
            print(f"[info] Gemini {m} {why} ({r.status_code}), provo un altro modello", file=sys.stderr)
            if r.status_code != 404:
                time.sleep(2)
            if not queue and not discovered:      # finiti: chiedi a Google quali modelli esistono
                discovered = True
                _discovered[:] = discover_gemini_models(api_key)
                queue += [x for x in _discovered if x not in tried][:4]
            continue
        if not r.ok:
            raise RuntimeError(f"Gemini {r.status_code}: {r.text[:300]}")
        parts = r.json()["candidates"][0]["content"]["parts"]
        return "".join(p.get("text", "") for p in parts)
    raise RuntimeError(f"nessun modello Gemini disponibile (ultimo: {last})")


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


GROQ_MODELS = ["openai/gpt-oss-120b", "openai/gpt-oss-20b"]


def ask_groq(prompt, api_key):
    last = None
    for m in GROQ_MODELS:
        r = requests.post("https://api.groq.com/openai/v1/chat/completions",
                          headers={"Authorization": f"Bearer {api_key}", "content-type": "application/json"},
                          json={"model": m, "temperature": 0.2,
                                "messages": [{"role": "user", "content": prompt}]},
                          timeout=90)
        if r.status_code in (404, 429, 500, 503):
            last = f"{m}: {r.status_code}"
            print(f"[info] Groq {m} non disponibile ({r.status_code}), provo il prossimo", file=sys.stderr)
            continue
        if not r.ok:
            raise RuntimeError(f"Groq {r.status_code}: {r.text[:300]}")
        return r.json()["choices"][0]["message"]["content"]
    raise RuntimeError(f"nessun modello Groq disponibile (ultimo: {last})")


def score_with_ai(items, sent, cfg):
    """Prova i fornitori configurati in ordine: Gemini, Groq, Claude."""
    prompt = build_prompt(items, sent)
    providers = []
    if cfg["gemini_key"]:
        providers.append(("Gemini", lambda: ask_gemini(prompt, cfg["gemini_key"], cfg["gemini_model"])))
    if cfg["groq_key"]:
        providers.append(("Groq", lambda: ask_groq(prompt, cfg["groq_key"])))
    if cfg["api_key"]:
        providers.append(("Claude", lambda: ask_claude(prompt, cfg["api_key"], cfg["model"])))
    errors = []
    for name, call in providers:
        try:
            return parse_json_list(call())
        except Exception as e:
            errors.append(f"{name}: {e}")
            if len(providers) > 1:
                print(f"[info] {name} non disponibile, passo al prossimo fornitore", file=sys.stderr)
    raise RuntimeError(" | ".join(errors))


def score_with_keywords(items, fallback=False):
    out = {}
    for i in items:
        t = (i["title"] + " " + i["body"]).lower()
        s = sum(w for w, pats in KEYWORDS_RE.items() for p in pats if p.search(t))
        if i["cat"] == "Trump":
            s += 2
        out[i["id"]] = {"id": i["id"], "score": min(10, 3 + s), "titolo": i["title"],
                        "cosa": "", "impatto": "", "riserva": fallback}
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
    if ev.get("riserva"):
        lines += ["", "<i>⚙️ Filtro di riserva: l'AI non era disponibile, nessun riassunto.</i>"]
    if item["link"]:
        lines += ["", f'<a href="{e(item["link"], quote=True)}">Fonte</a>']
    return "\n".join(lines)


# ---------------------------------------------------------------- main
def is_dup(title, sent):
    recent = [x for x in sent if x.get("t", 0) > time.time() - 6 * 3600]
    return any(similar(title, x.get("orig", "")) for x in recent)


def run_once(cfg, state):
    items = fetch_all(cfg["max_age"])
    new = [i for i in items if i["id"] not in state["seen"]]
    # 1) doppioni evidenti, senza AI: titolo simile a una news già inviata o già in coda
    uniq = []
    for i in sorted(new, key=lambda x: -len(x["body"])):   # tieni la versione più completa
        if is_dup(i["title"], state["sent"]) or any(similar(i["title"], u["title"]) for u in uniq):
            state["seen"][i["id"]] = time.time()
            print(f"[dup] {i['source']}: {i['title'][:100]}")
            continue
        uniq.append(i)
    print(f"[info] {len(items)} news ultimi {cfg['max_age']} min, {len(uniq)} nuove, "
          f"{len(state['seen'])} già viste")
    if not uniq:
        return

    evals = {}
    use_ai = bool(cfg["gemini_key"] or cfg["groq_key"] or cfg["api_key"])
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
                print(f"[warn] AI non disponibile: {e}", file=sys.stderr)
                # le news fortissime (>= 9 con le parole chiave) partono subito senza AI;
                # le altre restano in coda e riprovano con l'AI finché sono nella finestra MAX_AGE_MIN
                for k, v in score_with_keywords(batch, fallback=True).items():
                    if v["score"] >= 9:
                        evals[k] = v
    else:
        print(f"[info] AI in pausa ({cfg['ai_interval']}s tra chiamate), {len(uniq)} news in coda")

    # 2) invio: dalla più importante; salta gli eventi già inviati (anche in questo giro)
    ranked = sorted((i for i in uniq if i["id"] in evals),
                    key=lambda i: -int(evals[i["id"]].get("score", 0) or 0))
    for i in ranked:
        ev = evals[i["id"]]
        state["seen"][i["id"]] = time.time()
        score = int(ev.get("score", 0) or 0)
        if score < cfg["min_score"]:
            print(f"[skip {score}/10] {i['source']}: {i['title'][:100]}")
            continue
        storia = ev.get("storia", "")
        if is_dup(i["title"], state["sent"]):
            print(f"[dup] {i['title'][:90]}")
            continue
        if ev.get("doppione") is True:
            print(f"[dup-AI {score}/10] {i['source']}: {i['title'][:90]}  =  {str(ev.get('doppione_di', ''))[:90]}")
            continue
        send_telegram(cfg["token"], cfg["chat_id"], format_msg(i, ev), cfg["dry"])
        state["sent"].append({"t": time.time(), "titolo": ev.get("titolo") or i["title"][:150],
                              "orig": i["title"][:200], "storia": storia})
        print(f"[sent] {score}/10 {i['title'][:90]}")


def main():
    # su GitHub Actions l'output non è un terminale: senza questo le righe del log arrivano in ritardo
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)
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
        "groq_key": os.getenv("GROQ_API_KEY", ""),
        "api_key": os.getenv("ANTHROPIC_API_KEY", ""),
        "ai_interval": int(os.getenv("LLM_INTERVAL") or "120"),
        "model": os.getenv("CLAUDE_MODEL") or "claude-haiku-4-5",
        "min_score": int(os.getenv("MIN_SCORE") or "8"),
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
                      + (" + ".join(n for n, k in (("Gemini", "gemini_key"), ("Groq", "groq_key"), ("Claude", "api_key")) if cfg[k])
                         or "parole chiave")
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
