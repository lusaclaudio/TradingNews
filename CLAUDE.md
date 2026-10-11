# CLAUDE.md — Market News Bot

Contesto per Claude Code. Il progetto è nato in una conversazione Cowork (settembre-ottobre 2026); questo file ne riassume decisioni, stato e prossimi passi.

## Utente
- Claudio, retail trader di futures (ES, NQ, YM, FDAX). Scrive in **italiano**; preferisce risposte concise e concrete, e contesta subito le risposte fuori bersaglio.
- Non è uno sviluppatore: aggiorna il bot caricando i file a mano su GitHub (Add file → Upload files), poi fa Cancel workflow + Run workflow. Le istruzioni vanno date passo per passo e bisogna consegnare **file completi**, non diff.
- **Verificare sempre i fatti attuali** (ruoli, modelli AI, URL) con una ricerca, invece di andare a memoria. È già capitato un errore: Powell indicato come presidente Fed quando dal 22/05/2026 il presidente è Kevin Warsh.

## Cosa fa
Bot Telegram che invia a Claudio le **breaking news che muovono il mercato** (Trump, geopolitica, sanzioni/dazi, Fed/BCE, macro, AI/semiconduttori), con riassunto in italiano, sezione "Perché conta" e asset coinvolti.

## Deploy
- Repo GitHub **pubblico** `lusaclaudio/TradingNews`. È pubblico perché i minuti di Actions dei repo privati non bastano, quindi anche i **log sono pubblici**: non stampare mai dati privati.
- `.github/workflows/news.yml`: cron `*/20`, ogni run fa `python bot.py --loop 60` con `RUN_MINUTES=25`. `concurrency` impedisce i run sovrapposti. Lo stato (`state.json`) passa da un run all'altro tramite `actions/cache` restore/save con chiave `news-state-<run_id>`.
- Secrets (solo i nomi; i valori non vanno mai scritti nei file): `TELEGRAM_TOKEN`, `TELEGRAM_CHAT_ID`, `GEMINI_API_KEY`, `GROQ_API_KEY` (facoltativo), `ANTHROPIC_API_KEY` (facoltativo, non usato). Variable: `MIN_SCORE` (8).
- Tutto gratuito: GitHub Actions, Gemini free tier, Groq free tier.

## Architettura (`bot.py`, file unico, dipendenze: `feedparser`, `requests`)
1. **Fonti (`FEEDS`)**:
   - `tg:WalterBloomberg` e `tg:firstsquaw`: copie Telegram dei canali X, lette con scraping HTML di `t.me/s/<canale>` (`fetch_telegram_channel`, regex su `tgme_widget_message_text js-message_text[^"]*` e `<time datetime>`);
   - trumpstruth.org/feed (Truth Social);
   - investingLive news + centralbank (RSS);
   - Fed `press_all.xml`.
   - CNBC e Google News sono commentati perché lenti e causa di doppioni.
2. **Finestra**: solo news con meno di `MAX_AGE_MIN=20` minuti.
3. **Dedup meccanico** (`similar`/`is_dup`): Jaccard ≥ 0.7 sulle parole, contro le news inviate nelle ultime 6 ore e contro le altre in coda. Prima la soglia era 0.45 e scartava news valide: non riabbassarla.
4. **AI (`score_with_ai`)**: catena Gemini → Groq → Claude.
   - Gemini: `ask_gemini` prova `GEMINI_FALLBACK` (flash-lite-latest, 3.5/3.1-flash-lite, flash-latest, 3.8/3.6-flash; 3.5-flash è dismesso e rediretto a 3.6). Dalla 3.6 `temperature`/`top_p`/`top_k` non vanno più inviati (in futuro danno 400): la chiamata Gemini non li manda. Su 404/429/500/503 passa al modello successivo; finiti quelli, chiama `discover_gemini_models` e usa la ListModels API. I modelli 2.5 non sono più disponibili per gli account nuovi.
   - Groq: `openai/gpt-oss-120b`, poi `gpt-oss-20b`.
   - Il `PROMPT` include `CONTESTO` (data di oggi, presidenti Fed/BCE/USA, **da tenere aggiornato**) e le news inviate nelle ultime 3 ore.
   - Output JSON: `score`, `doppione`, `doppione_di`, `titolo`, `cosa`, `perche`, `impatto`, `storia`.
   - Il voto misura solo l'importanza. `doppione` è separato e vale solo per lo stesso fatto, con la regola "nel dubbio false".
   - `LLM_INTERVAL=0`: l'AI viene chiamata a ogni giro in cui ci sono news nuove.
5. **Filtro di riserva (`KEYWORDS`)**: regex su parole intere con peso 3 o 2. Si usa se non c'è nessuna chiave AI (soglia `MIN_SCORE`) oppure se tutte le AI falliscono: in quel caso partono subito solo le news con punteggio ≥ 9, con la nota "⚙️ Filtro di riserva".
6. **Invio**: HTML Telegram con titolo, categoria · fonte · impatto · "N min fa", testo, 💡 Perché conta, 📊 impatto, link alla fonte. Si invia dalla news con voto più alto. `MIN_SCORE=8`.
7. **Pausa weekend** (`in_quiet`): da venerdì 23:00 a domenica 22:00 (ora italiana) le news non partono; quelle sopra soglia vanno in `state["weekend"]` e domenica alle 22:00 `weekend_recap` manda un solo messaggio con le 8 più impattanti (doppioni uniti; se non c'è nulla, una riga). Gli alarm F1 restano attivi.
8. **F1** (`f1_check`, a ogni giro del loop): calendario da API Jolpica (`api.jolpi.ca/ergast/f1/current.json`), in cache nello stato 12 ore. Anteprima il venerdì alle 19:00 italiane con le sessioni ancora da fare (Sprint Qualifying, Sprint, Qualifiche, Gara; niente prove libere) e promemoria 15 minuti prima di ogni sessione. Orari solo in ora italiana (`zoneinfo`, dipendenza `tzdata`). Chiavi già inviate in `f1_sent`.
9. **Stato**: `seen` (id → ts, pulito dopo 3 giorni), `sent` (dict con t, titolo, orig, storia; pulito dopo 24 ore), `last_ai`.
10. **Log**: stdout a buffer di riga (`reconfigure`). Righe principali: `[info] stato caricato`, `[info] <canale>: N post letti`, `[sent]`, `[skip n/10]`, `[dup]`, `[dup-AI]`, `[weekend]`, `[f1]`, `[warn] AI non disponibile`.

## Test
La rete esterna può essere bloccata nell'ambiente di sviluppo, quindi i test si fanno mockando `bot.requests.get/post`: pagine `t.me/s` finte, RSS finti, risposte Gemini/Groq finte e 503/404. Poi:
```
python -m py_compile bot.py
python bot.py --test --dry-run
```

## Storia dei problemi risolti
- Secrets non letti: erano stati creati nel posto sbagliato e vanno in Repository secrets.
- Doppioni e news vecchie: un fallback a parole chiave sulle news con più di 45 minuti, rimosso; finestra portata da 120 a 20 minuti.
- News Iran scartate: sia dal dedup troppo aggressivo, sia dall'AI che dava voto 1 ai doppioni. Corretto separando il flag `doppione`.
- Regex Telegram troppo rigida, che saltava i post con classi extra.
- Modelli Gemini 2.5 spariti e 503 frequenti: aggiunti fallback, discovery dei modelli e Groq.
- Log in ritardo per il buffering di stdout.
- Run falliti per "job not acquired by Runner": problema di GitHub, non del bot.

## Idee in sospeso (non implementate)
- **Chat con il bot**: Claudio risponde a una news su Telegram e l'AI gli risponde.
  - Piano: sostituire `time.sleep` con il long-polling `getUpdates` e salvare `offset` nello stato; mappare `reply_to_message.message_id` sulla news inviata (salvare `message_id` in `sent`); tenere un breve storico della conversazione per news; dare come contesto le news delle ultime ore.
  - Vincoli: rispondere **solo** a `TELEGRAM_CHAT_ID`; non loggare il testo delle domande (log pubblici).
  - Limiti: buchi di 30-60 s tra un run e l'altro (i messaggi restano in coda su Telegram fino a 24 h); l'AI non conosce i prezzi live.
  - Opzionali: grounding con Google Search di Gemini, prezzi live, comandi `/muto 1h`, `/soglia 9`, `/ultime`.
  - Claudio non ha ancora scelto la versione base o quella completa.
- Verificare il ritardo dei canali Telegram rispetto agli originali su X.
- Alternativa all'hosting: VPS o PC sempre acceso (eliminerebbe i buchi tra i run e i fallimenti dei runner GitHub).
- Aggiornare `CONTESTO` quando Lagarde lascerà la BCE (2027).
