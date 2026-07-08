# Turnazione App

## Setup

```powershell
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

In VS Code: `Ctrl+Shift+P` -> "Python: Select Interpreter" -> scegli quello dentro `.\venv`.

## Testare il motore

```powershell
python -m engine.solver
pytest tests/ -v
```

## Avviare l'interfaccia grafica (Streamlit)

```powershell
streamlit run app.py
```

Si apre il browser in automatico. Tre schede, **in quest'ordine anche nel
codice** (non solo visivamente — importante per come Streamlit propaga gli
aggiornamenti, vedi nota sotto). **Ogni intestazione giorno, in tutte le
griglie e tabelle, mostra anche il giorno della settimana**
(lun/mar/mer/gio/ven/sab/dom) accanto alla data.

- **Regole & periodo** (prima scheda) — **numero di lavoratori** con
  selettore numerico: aumentandolo aggiunge lavoratori con nome generato
  automaticamente (`Nome<n> Cognome<n>`, da rinominare poi nella scheda
  Lavoratori), diminuendolo li rimuove dal fondo; i lavoratori esistenti
  e le modifiche gia' fatte (nome vero, ore, mai notti) non vengono
  toccati. Poi anno e mese (il periodo si calcola da solo: parte dal
  giorno 1 e si estende fino alla **domenica** che chiude la settimana
  in cui cade l'ultimo giorno del mese, cosi' il vincolo di ore
  settimanali lavora sempre su settimane complete lun-dom invece che su
  una settimana finale spezzata a meta'. Esempio: se il mese finisce
  venerdi' 31, il periodo si estende fino a domenica 2 del mese
  successivo). Poi ore per fascia, notti consecutive, pesi fairness.
- **Lavoratori** — tabella editabile: id, nome, ore contratto (specifiche
  per singolo lavoratore, nessun default globale nascosto), "mai notti"
- **Calendario** — due griglie:
  1. **Fabbisogno**: righe M/P/N, valori numerici per giorno del periodo
  2. **Situazione iniziale + Richieste/Vincoli**: griglia unica, righe =
     lavoratori. Le prime colonne (icona 🕓) sono gli ultimi giorni del
     mese precedente — turni gia' effettuati, concettualmente trattati
     come "assegnazioni chiuse" (stesso principio di un vincolo admin,
     solo che e' gia' un fatto avvenuto anziche' un'imposizione per il
     futuro). Il numero di giorni mostrati e' minimo 4, ma si allarga
     automaticamente per coprire l'intera settimana calendario (lun-dom)
     su cui il mese inizia: 4 giorni se il mese inizia lun-ven, 5 se
     inizia sabato, 6 se inizia domenica — utile per le statistiche di
     ore settimanali lato utente (il motore di calcolo non e' toccato da
     questo, gestisce `stato_iniziale` in modo generico indipendentemente
     dal numero di giorni). Le colonne successive sono il periodo da
     pianificare, con un codice breve per cella (es. `F3` = richiesta
     ferie priorita' alta, `AM` = turno Mattino forzato dal coordinatore);
     le ultime colonne (icona ➡️), se presenti, sono gia' nel mese
     successivo. Legenda disponibile nell'espansione "Legenda codici".
     **Una cella contiene un solo codice**: richiesta soft del lavoratore
     e vincolo admin del coordinatore sono quindi mutuamente esclusivi per
     costruzione, non serve validarlo a parte. Nota tecnica: Streamlit
     non supporta la colorazione di sfondo nelle griglie editabili
     (sono renderizzate su canvas), quindi le tre zone si distinguono
     con le icone nelle intestazioni invece che con colori.

**Nota su un bug corretto**: Streamlit esegue il codice di ogni scheda
nell'ordine in cui compare nello script, non in base a quale scheda l'utente
ha aperta. Prima "Regole & periodo" era l'ultima scheda nel codice: cambiare
anno/mese aggiornava `session_state`, ma le altre schede (gia' eseguite
sopra in quello stesso giro) mostravano ancora i valori vecchi fino al giro
di esecuzione successivo. Spostando "Regole & periodo" per prima anche nel
codice, gli aggiornamenti si propagano subito, nello stesso giro.

Premi "Genera turni" per vedere:
- lo schema turni colorato
- la copertura effettiva vs fabbisogno (giorni in colonna, M/P/N in riga)
- **Turni per lavoratore**: M/P/N, Totale turni, Ore M/P/N sono calcolati
  **sul solo mese di riferimento selezionato** (escludono sia la
  situazione iniziale del mese precedente sia l'eventuale sconfinamento
  nel mese successivo). Le colonne "Ore sett.N" invece includono
  volutamente anche le ore di situazione iniziale e degli eventuali
  giorni nel mese successivo, per coerenza col vincolo di ore settimanali
  del motore (che ragiona su settimane calendario complete lun-dom, non
  sul solo mese). "Ore mese" segue lo stesso criterio di M/P/N: solo il
  mese di riferimento
- le richieste non soddisfatte e l'equilibrio del carico tra lavoratori

## Cosa fa il motore adesso (completo sui vincoli principali)

**Livello 1 - vincoli strutturali di sistema (sempre hard):**
- un lavoratore fa al massimo una fascia (M/P/N) al giorno
- copertura minima di personale per giorno/fascia (fabbisogno)
- riposo obbligatorio dopo un turno notturno (no M/P il giorno dopo N,
  fasce configurabili via `regole_contrattuali.vietato_dopo_notte`)
- vincolo personale "mai notti" (`lavoratore.vincoli_personali.mai_notti`)
- massimo notti consecutive (default 2, override possibile per singolo
  lavoratore)
- massimo ore settimanali da contratto, **sempre specifico per singolo
  lavoratore** (`lavoratore.ore_settimanali_contratto`, nessun fallback
  su un default globale — un valore 0 viene rispettato letteralmente,
  non sostituito silenziosamente) (settimane calendario lun-dom, ore per
  fascia configurabili, default 8h per M/P, 10h per N). Se la prima settimana del
  periodo e' a cavallo con l'ultima settimana del mese precedente, le ore
  gia' maturate in `stato_iniziale` in quella settimana vengono sommate
  al conteggio
- tutti questi vincoli tengono conto di `stato_iniziale` per i casi a
  cavallo con il mese precedente

**Livello 2 - vincoli admin (hard, imposti dal coordinatore):**
- "ferie"/"riposo" forzati -> giorno bloccato
- "turno" forzato -> fascia specifica imposta
- nota: la validazione preventiva di conflitti tra vincoli admin e il
  meccanismo di declassamento automatico sono rimandati a una fase
  successiva (come deciso insieme)

**Livello 3 - richieste soft pesate (preferenze lavoratore):**
- scala di priorita' 1 (indifferente) - 4 (molto importante), mappata
  internamente su pesi esponenziali (1, 10, 100, 1000) cosi' una
  richiesta di priorita' alta non viene mai sacrificata per soddisfarne
  tante di priorita' bassa
- le richieste non soddisfatte vengono riportate esplicitamente in output

**Livello 4 - fairness (soft, priorita' piu' bassa):**
- minimizza lo scarto (max - min) tra lavoratori sul numero di turni per
  fascia e sul totale di giorni lavorati
- minimizza inoltre lo scarto (max - min) del **tasso di utilizzo della
  capacita' oraria residua, settimana per settimana** (non solo sul
  totale del periodo): bilanciare solo il totale non basta, una singola
  settimana potrebbe restare molto sbilanciata pur avendo un totale di
  periodo equilibrato. **Non confrontiamo le ore grezze**: un lavoratore
  con ore gia' maturate in `stato_iniziale` (settimana a cavallo col mese
  precedente) ha legittimamente meno ore residue disponibili quella
  settimana — confrontare le ore grezze farebbe si' che un peso alto
  "trascini giu'" anche gli altri lavoratori pur di ridurre lo scarto
  (effetto opposto a quello voluto). Confrontiamo invece il tasso
  (ore nuove assegnate / capacita' residua quella settimana): un
  lavoratore gia' quasi al massimo della sua capacita' residua (es. 24
  ore su 28 disponibili = 86%) risulta gia' "equo" rispetto a un altro
  pieno al 100% su 36 ore, senza bisogno di penalizzare nessuno
- minimizza inoltre lo scarto (max - min) del **tasso di surplus di
  copertura** (surplus / fabbisogno minimo, non il surplus grezzo),
  confrontato su un'unica scala tra **tutte le fasce e i giorni insieme**:
  cosi' se M e P hanno lo stesso fabbisogno (es. 3 e 3) il surplus si
  distribuisce equamente tra le due invece che concentrarsi solo su una,
  e se il fabbisogno varia (es. N=2 contro M=3) il confronto resta
  significativo perche' e' proporzionale, non assoluto
- peso configurabile rispetto alle richieste soft (di default le
  richieste contano di piu' dell'equilibrio del team)

## Dataset di esempio

`engine/sample_data.py` (usato sia dai test che come default in Streamlit)
simula un reparto con **20 infermieri** e un fabbisogno giornaliero di
**3 Mattino + 3 Pomeriggio + 2 Notte** (8 turni/giorno), per **l'intero
periodo esteso** di luglio 2026 (1 luglio - 2 agosto, 33 giorni: luglio
finisce venerdi' 31, quindi il periodo si estende fino alla domenica
successiva). Con 20 lavoratori a 36 ore settimanali (4 turni/settimana
ciascuno) la capacita' e' 80 turni/settimana contro una domanda massima
di 56: margine ampio per lasciare spazio a richieste soft, vincoli admin
e fairness su tutte le settimane del periodo.

## Prossimi step possibili (non ancora implementati)

- Validazione preventiva dei vincoli admin (conflitti tra loro o con le
  regole strutturali), con messaggi di errore chiari invece di un
  generico "infeasible"
- Meccanismo di declassamento automatico dei vincoli admin quando
  causano infeasibility (`declassabile_se_infeasible`)
- Persistenza su database (Postgres) al posto delle tabelle in sessione
  Streamlit, che oggi si perdono alla chiusura dell'app
- Export/import da Excel per il caricamento massivo di richieste

## Deploy su Streamlit Community Cloud

1. Inizializza git e fai push su una repo GitHub (pubblica o privata):
   ```powershell
   git init
   git add .
   git commit -m "Prima versione motore turnazione"
   ```
   poi crea la repo su GitHub e collegala (`git remote add origin ...`, `git push`).

2. Vai su [share.streamlit.io](https://share.streamlit.io), accedi con GitHub,
   clicca "New app" e seleziona repo/branch/`app.py` come file principale.

3. **Importante sulla versione Python**: il file `runtime.txt` nel progetto
   prova a richiedere Python 3.12 (la versione usata in sviluppo, compatibile
   con `ortools`), ma ci sono segnalazioni recenti che Community Cloud lo
   ignora in alcuni casi. **Verifica sempre manualmente** nel menu
   "Advanced settings" durante il deploy che la versione Python selezionata
   sia 3.12 (o comunque una versione per cui `ortools` ha una wheel
   precompilata), altrimenti l'installazione delle dipendenze puo' fallire.

4. Deploy: Community Cloud installa automaticamente da `requirements.txt`.

**Limiti da tenere presenti**: l'app gratuita "dorme" dopo un periodo di
inattivita' (si riattiva al primo accesso, con qualche secondo di attesa);
`st.session_state` non e' persistente tra un risveglio e l'altro (i dati
inseriti in sessione si perdono se l'app si riavvia); l'app e' raggiungibile
pubblicamente da chiunque abbia il link. Per un uso reale con dati di
personale ospedaliero, valutare hosting privato con autenticazione prima
di andare oltre la fase di test/dimostrazione.

## Struttura progetto

```
turnazione-app/
├── engine/
│   ├── models.py          # strutture dati (dataclass)
│   ├── solver.py           # motore CP-SAT (livelli 1-4 completi)
│   ├── periodo_utils.py    # calcolo periodo esteso e conversioni indice-giorno/data
│   └── sample_data.py      # caso di esempio usato anche come default in Streamlit
├── app.py                  # interfaccia Streamlit
├── requirements.txt
└── tests/
    └── test_solver.py      # un test per ciascun vincolo/livello
```
