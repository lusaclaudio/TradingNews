# Market News Bot (Telegram)

Ti scrive su Telegram quando esce una **breaking news che può muovere il mercato**: post di Trump, guerre e geopolitica, sanzioni e dazi, Fed e banche centrali, dati macro, AI e semiconduttori.

Esempio di messaggio:

> 🚨 **Trump offre aiuti economici all'Iran in cambio di concessioni nucleari**
> *Flash · First Squawk · impatto 9/10 · 2 min fa*
>
> Secondo Axios, il presidente Trump ha offerto all'Iran un alleggerimento delle sanzioni economiche in cambio di passi concreti sul programma nucleare. Arriva poche ore dopo le indiscrezioni su uno sblocco di asset iraniani…
>
> 💡 **Perché conta:** una distensione con l'Iran riduce il rischio sull'offerta di petrolio e sullo Stretto di Hormuz.
>
> 📊 Petrolio ↓, Oro ↓, ES ↑

---

## Come funziona

1. **Fonti**, controllate ogni 60 secondi:
   - **Walter Bloomberg** e **First Squawk**: copie Telegram dei due canali X di headline flash più seguiti dai trader, lette dalla pagina pubblica `t.me/s/…` (gratis, senza account);
   - **Trump** su Truth Social, tramite l'archivio trumpstruth.org;
   - **investingLive** (ex ForexLive): news e banche centrali;
   - **Federal Reserve**: comunicati ufficiali.
2. **Solo breaking:** vengono considerate solo le news pubblicate negli **ultimi 20 minuti**.
3. **Valutazione AI:** ogni news nuova riceve un voto d'impatto da 1 a 10 e, se importante, un riassunto in italiano con "Perché conta" e gli asset coinvolti. Ti arrivano solo quelle **da 8 in su**.
4. **Doppioni** scartati in due modi:
   - titoli quasi identici già inviati nelle ultime 6 ore;
   - l'AI segnala se è **lo stesso fatto** di una news inviata nelle ultime 3 ore. Uno sviluppo nuovo sullo stesso tema passa, e nel dubbio la news viene inviata.
5. **Catena di riserva** se un'AI non risponde:
   - **Gemini**, che prova da solo più modelli e, se spariscono, cerca quelli nuovi;
   - **Groq**, se configurato;
   - **Claude**, se configurato (a pagamento);
   - se nessuna AI risponde, le news con punteggio **9 o più alle parole chiave** partono subito, senza riassunto, con la scritta "⚙️ Filtro di riserva".

---

## Installazione

### 1. Bot Telegram

1. Su Telegram apri **@BotFather** → `/newbot` → scegli nome e username (deve finire in `bot`).
2. Copia il **token** (tipo `123456:ABC-...`).
3. Apri il tuo bot, premi **Avvia** e scrivigli "ciao".
4. Apri nel browser `https://api.telegram.org/bot<TOKEN>/getUpdates`, mettendo il tuo token al posto di `<TOKEN>`, con la parola `bot` attaccata al numero. Il numero dopo `"chat":{"id":` è il tuo **chat_id**.
   Attenzione: il numero prima dei due punti nel token è l'ID del bot, non il tuo chat_id.

### 2. Chiavi AI (gratis)

- **Gemini** (obbligatoria): <https://aistudio.google.com/apikey> → **Create API key**. La chiave inizia con `AIza`.
- **Groq** (consigliata come riserva): <https://console.groq.com/keys> → **Create API Key**. La chiave inizia con `gsk_`.
  Non va confusa con *Grok* di X/xAI (chiavi `xai-…`), che il bot non usa.

Nel piano gratuito Google può usare i testi inviati per migliorare i suoi modelli: qui sono solo news pubbliche.

### 3. GitHub Actions

1. Crea un repository **pubblico** e carica tutti i file, inclusa la cartella `.github/workflows/`. Se il caricamento salta la cartella, crea il file a mano con **Add file → Create new file**, nome `.github/workflows/news.yml`.
   Il repo deve essere pubblico perché su un repo privato i minuti gratuiti di Actions non bastano. I segreti restano comunque privati.
2. **Settings → Secrets and variables → Actions → tab Secrets → New repository secret**. Vanno sotto *Repository secrets*, non *Environment* e non *Variables*:

   | Name | Valore |
   |---|---|
   | `TELEGRAM_TOKEN` | token di BotFather |
   | `TELEGRAM_CHAT_ID` | il tuo chat_id |
   | `GEMINI_API_KEY` | chiave `AIza…` |
   | `GROQ_API_KEY` | chiave `gsk_…` (facoltativa) |

3. Facoltativo: nel tab **Variables** crea `MIN_SCORE` (default 8; metti 9 per meno notifiche, 7 per di più).
4. Tab **Actions** → abilita i workflow → **Market News Bot** → **Run workflow**.

Ogni run resta attivo 25 minuti e ne parte uno nuovo ogni 20. Servono ripartenze perché GitHub non permette run infiniti e a volte ritarda quelli programmati. Si sovrappongono un po' per non lasciare buchi. La memoria delle news già viste passa da un run all'altro.

---

## Aggiornare il bot

1. Nel repo premi **Add file → Upload files**, trascina il nuovo `bot.py` e premi **Commit changes**.
2. In **Actions** apri il run in corso (pallino giallo) e premi **Cancel workflow**. Fai lo stesso con eventuali run in coda.
3. Premi **Market News Bot → Run workflow**.

---

## Leggere il log

Il log si trova in **Actions** → run più recente → riquadro **run** → passaggio **Controlla le news**. Per cercare un titolo usa "Search logs".

| Riga | Significato |
|---|---|
| `[info] stato caricato: 765 news già viste…` | memoria dei doppioni recuperata dal run precedente |
| `[info] First Squawk: 20 post letti, l'ultimo di 3 min fa` | lettura del canale Telegram riuscita |
| `[info] 12 news ultimi 20 min, 3 nuove…` | quante news nuove a ogni controllo |
| `[sent] 9/10 …` | inviata su Telegram |
| `[skip 5/10] …` | voto troppo basso |
| `[dup] …` | titolo quasi identico a una news già inviata |
| `[dup-AI 9/10] … = …` | l'AI l'ha riconosciuta come lo stesso fatto di una news già inviata |
| `[info] Gemini … (503)` | modello sovraccarico, il bot prova il successivo (normale) |
| `[warn] AI non disponibile…` | tutte le AI giù: resta attivo il filtro di riserva |
| `[warn] <canale>: nessun post trovato` | lettura del canale Telegram fallita: va controllato |

### Problemi comuni

- **`Mancano TELEGRAM_TOKEN e/o TELEGRAM_CHAT_ID`**: i secrets non sono in *Repository secrets* oppure hanno un nome sbagliato.
- **`Telegram: 401`**: token sbagliato o rigenerato. **`400 chat not found`**: chat_id sbagliato o bot mai avviato.
- **Una news importante non è arrivata**: cercala nel log. Se il titolo non compare, il bot non l'ha letta. Se compare con `[skip]` o `[dup-AI]`, si vede il motivo.

---

## Personalizzare (in `bot.py`)

- **`FEEDS`**: le fonti. Accetta feed RSS e canali Telegram pubblici (`"tg:NomeCanale"`). CNBC e Google News sono presenti ma disattivati (hanno `#` davanti) perché lenti e pieni di doppioni.
- **`CONTESTO`**: chi ricopre i ruoli chiave, per esempio il presidente della Fed (oggi Kevin Warsh) o della BCE. L'AI può avere conoscenze vecchie: **va aggiornato quando cambia qualcuno**, per esempio al successore di Lagarde nel 2027.
- **`PROMPT`**: i criteri per decidere cosa è "major".
- **`KEYWORDS`**: le parole del filtro di riserva.

Variabili d'ambiente:

| Variabile | Default | Cosa fa |
|---|---|---|
| `MIN_SCORE` | 8 | soglia minima per ricevere la notifica |
| `MAX_AGE_MIN` | 20 | ignora le news più vecchie di N minuti |
| `LLM_INTERVAL` | 0 | secondi minimi tra due chiamate AI (0 = a ogni controllo); alzalo se finisci spesso la quota |
| `GEMINI_MODEL` | gemini-flash-lite-latest | primo modello Gemini da provare |

Su GitHub, `MIN_SCORE` si imposta dal tab *Variables*. Le altre variabili vanno aggiunte nel blocco `env:` di `news.yml`.

---

## In alternativa: sul PC o su un VPS

```bash
pip install -r requirements.txt
export TELEGRAM_TOKEN=...  TELEGRAM_CHAT_ID=...  GEMINI_API_KEY=...  GROQ_API_KEY=...
python bot.py --test       # messaggio di prova
python bot.py --dry-run    # stampa invece di inviare
python bot.py --loop 60    # sempre attivo, controlla ogni 60 s
```
Su Windows (PowerShell) usa `$env:TELEGRAM_TOKEN="..."` al posto di `export`.

---

## Limiti

- I canali Telegram di Walter Bloomberg e First Squawk non sono necessariamente ufficiali: potrebbero chiudere o essere in ritardo di qualche minuto rispetto a X. X non viene letto direttamente perché la sua API è a pagamento (circa $0,005 a post).
- La pagina `t.me/s/…` non è un'interfaccia pensata per i programmi: se Telegram la cambia, la lettura va sistemata (nel log compare `nessun post trovato`).
- I post di Trump via trumpstruth.org arrivano con qualche minuto di ritardo, ma spesso Walter Bloomberg li riporta prima.
- Le AI gratuite a volte sono sovraccariche. Non è un terminale professionale: per le headline al secondo serve un servizio a pagamento.
