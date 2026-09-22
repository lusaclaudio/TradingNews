# Market News Bot (Telegram)

Ti scrive su Telegram quando esce una news che può muovere il mercato: post di Trump su Truth Social, guerra/geopolitica, AI/semiconduttori, Fed e macro.
Un'AI gratuita (Google Gemini) valuta ogni news (impatto 1-10), scarta il rumore e i doppioni, e ti manda un messaggio in italiano con cosa è successo e quali asset si muovono.

Esempio:

> 🚨 **Trump annuncia dazi al 100% sulla Cina dal 1° novembre**
> *Trump · Truth Social · impatto 9/10*
> Annuncio a sorpresa su Truth Social, con controlli all'export sul software critico. Rischio escalation commerciale.
> 📊 NQ ↓, ES ↓, Oro ↑, CNH ↓

---

## 1. Crea il bot Telegram (2 minuti)

1. Su Telegram apri **@BotFather** → `/newbot` → scegli nome e username (deve finire in `bot`).
2. BotFather ti dà il **token** (tipo `123456:ABC-...`). Copialo.
3. Apri il tuo nuovo bot e premi **Avvia** (oppure scrivigli "ciao").
4. Nel browser apri `https://api.telegram.org/bot<TOKEN>/getUpdates` (sostituisci `<TOKEN>`).
   Cerca `"chat":{"id":123456789` → quel numero è il tuo **chat_id**.

## 2. API key di Gemini (gratis)

1. Vai su <https://aistudio.google.com/apikey> e accedi con il tuo account Google.
2. **Create API key**, poi copiala. Non serve la carta di credito.

Il piano gratuito ha un limite di richieste al giorno: il bot interroga l'AI al massimo una volta ogni 2 minuti, raggruppando le news, così ci sta dentro.
Nel piano gratuito Google può usare i testi inviati per migliorare i suoi modelli: qui sono solo news pubbliche, quindi non è un problema.
Senza chiave il bot funziona lo stesso, ma filtra solo con parole chiave (più rumore, nessun riassunto).
In alternativa puoi usare Claude, a pagamento: metti `ANTHROPIC_API_KEY` al posto di `GEMINI_API_KEY`.

## 3. Mettilo online con GitHub Actions (gratis)

1. Crea un repository **pubblico** su GitHub (es. `market-news-bot`) e carica tutti i file di questa cartella, inclusa `.github/workflows/news.yml`.
   Deve essere pubblico perché su un repo privato i minuti gratis di Actions (2000/mese) non bastano per un bot sempre attivo. Il codice non contiene segreti: token e chiavi stanno nei Secrets, che restano privati.
2. Repo → **Settings → Secrets and variables → Actions → New repository secret**, aggiungi:
   - `TELEGRAM_TOKEN`
   - `TELEGRAM_CHAT_ID`
   - `GEMINI_API_KEY`
3. (Facoltativo) nella tab **Variables** aggiungi `MIN_SCORE`: 7 è il default, 8 per meno notifiche, 6 per di più.
4. Tab **Actions** → abilita i workflow → "Market News Bot" → **Run workflow** per farlo partire subito.

Da quel momento parte da solo ogni 20 minuti e ogni run controlla le fonti ogni 60 secondi, quindi le news ti arrivano entro circa 1-2 minuti.
Nota: GitHub a volte ritarda i run programmati. Le news non vanno perse perché il bot guarda le ultime 2 ore, ma in quei casi possono arrivare in ritardo.

## In alternativa: sul PC o su un VPS

```bash
pip install -r requirements.txt
export TELEGRAM_TOKEN=...  TELEGRAM_CHAT_ID=...  GEMINI_API_KEY=...
python bot.py --test       # messaggio di prova
python bot.py --loop 60    # sempre attivo, controlla ogni 60 s
```
Su Windows (PowerShell) usa `$env:TELEGRAM_TOKEN="..."` al posto di `export`.

## Personalizzare

- **Fonti**: la lista `FEEDS` all'inizio di `bot.py` (RSS). Le ricerche Google News si modificano con la funzione `gnews(...)`.
- **Cosa conta come "major"**: il testo `PROMPT` in `bot.py`.
- **Soglia**: `MIN_SCORE`.
- **Finestra temporale**: `MAX_AGE_MIN` (default 120 minuti).
- **Frequenza chiamate AI**: `LLM_INTERVAL` (default 120 secondi). Abbassalo per avere le news prima, ma rischi di superare il limite gratuito.

## Limiti

- I post di Trump arrivano tramite trumpstruth.org, un archivio non ufficiale di Truth Social: di solito è in ritardo di pochi minuti. X/Twitter non è incluso perché la sua API è a pagamento.
- Non è un terminale professionale come Bloomberg o FinancialJuice: per gli headline "flash" al secondo serve un servizio a pagamento.
