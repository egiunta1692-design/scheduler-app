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
     lavoratori. Le prime colonne (icona 🕓) sono gli ultimi **6 giorni**
     del mese precedente — turni gia' effettuati, concettualmente trattati
     come "assegnazioni chiuse" (stesso principio di un vincolo admin,
     solo che e' gia' un fatto avvenuto anziche' un'imposizione per il
     futuro). Sono sempre 6 (il massimo possibile: quando il mese inizia
     di domenica servono 6 giorni per coprire l'intera settimana
     calendario precedente) invece di adattarsi al minimo stretto
     necessario — utile sia per le statistiche di ore settimanali lato
     utente sia per dare sempre spazio al **pattern di default**
     descritto sotto (il motore di calcolo non e' toccato da questo,
     gestisce `stato_iniziale` in modo generico indipendentemente dal
     numero di giorni). Le colonne successive sono il periodo da
     pianificare, con un codice breve per cella (es. `F3` = richiesta
     ferie priorita' alta, `R2` = richiesta riposo priorita' media, `AM` =
     turno Mattino forzato dal coordinatore, `AR` = riposo forzato); le ultime colonne (icona ➡️), se presenti, sono gia' nel mese
     successivo — per queste non viene mostrato il numero progressivo
     del giorno (che sarebbe fuorviante, essendo un mese diverso), solo
     icona + giorno della settimana + data. Legenda disponibile
     nell'espansione "Legenda codici". **Una cella contiene un solo
     codice**: richiesta soft del lavoratore e vincolo admin del
     coordinatore sono quindi mutuamente esclusivi per costruzione, non
     serve validarlo a parte. Nota tecnica: Streamlit non supporta la
     colorazione di sfondo nelle griglie editabili (sono renderizzate su
     canvas), quindi le tre zone si distinguono con le icone nelle
     intestazioni invece che con colori.

     **Pattern di default per la situazione iniziale**: le colonne del
     mese precedente non partono vuote, ma con un pattern plausibile
     generato automaticamente (`_genera_situazione_iniziale_default` in
     `app.py`) — resta comunque completamente modificabile/svuotabile.
     Il motivo: **compilare la situazione iniziale con i turni realmente
     effettuati e' l'unico modo corretto di rendere raggiungibile il
     minimo ore settimanali nella prima settimana del periodo, quando
     questa e' piu' corta di 7 giorni** (es. se il mese inizia di
     mercoledi', la prima settimana ISO ha solo 5 giorni nel periodo) —
     le ore gia' maturate nei giorni precedenti si sommano naturalmente
     al totale della settimana (vedi "Monte ore settimanale" sopra: il
     minimo NON viene piu' proporzionato automaticamente, e' una
     versione precedente di questo vincolo che e' stata rimossa in
     favore di questo approccio, piu' corretto perche' basato su dati
     veri invece che su un'approssimazione). Il pattern di default segue
     un ciclo a 3 giorni (`M-P-riposo`), con un offset diverso per
     ciascun lavoratore cosi' da avere una rotazione plausibile invece
     di valori identici per tutti, e con un lavoratore ogni 7 senza
     turno al posto dell'"M" del ciclo (situazione iniziale non
     distingue ferie da riposo, nessuna delle due genera ore virtuali
     per il mese precedente, quindi una cella vuota rappresenta entrambe
     equivalentemente). **Deliberatamente senza notti**: una versione
     precedente del ciclo includeva notti (`M-P-N-N-riposo-riposo`), ma
     verificato numericamente che poteva lasciare troppi pochi
     lavoratori liberi e con credito sufficiente a coprire le notti
     richieste nella prima settimana (8 su 20 disponibili contro 10
     richieste in uno scenario reale) — un ciclo solo M/P e' molto piu'
     robusto: nessun lavoratore e' mai bloccato dal riposo dovuto a una
     notte pregressa, e tutti hanno credito sufficiente. Si rigenera
     solo per le celle genuinamente nuove (nuovo lavoratore aggiunto, o
     nuovo mese con giorni di situazione iniziale diversi) — le celle
     gia' modificate o deliberatamente svuotate dall'utente non vengono
     mai sovrascritte.

     **Importante**: dato che il minimo ore settimanali non viene piu'
     proporzionato per le settimane parziali, una situazione iniziale
     lasciata vuota o compilata solo in parte puo' rendere `infeasible`
     la prima settimana del periodo — il pattern di default sopra da'
     un punto di partenza ragionevole, ma per la massima affidabilita'
     va sostituito con i turni realmente effettuati dai lavoratori nei
     giorni immediatamente precedenti al periodo.

     **Esporta / Importa CSV** (espansione sopra la griglia): scarica la
     griglia come CSV per modificarla comodamente in Excel o Notepad
     (colonne 'S&lt;n&gt;' = giorni del mese precedente, colonne numeriche =
     giorni del periodo), poi ricaricala per applicare le modifiche. Se
     il CSV caricato ha una struttura leggermente diversa (es. esportato
     con un mese o un numero di lavoratori diverso), le righe/colonne
     mancanti restano vuote invece di dare errore, con un avviso.

     **Svuota celle in blocco** (espansione sopra la griglia): rimuove
     tutti i codici di uno o piu' lavoratori scelti (multiselezione, con
     una checkbox "Seleziona tutti" per selezionarli/deselezionarli tutti
     in un colpo — di default nessuno e' selezionato; per default svuota
     solo le colonne del periodo da pianificare, un'altra checkbox
     permette di includere anche i giorni del mese precedente/situazione
     iniziale) oppure di uno o piu' giorni scelti (stessa logica di
     multiselezione e "Seleziona tutti", tutti i lavoratori), senza
     doverlo fare cella per cella.

     **Carica risultato come vincoli** (bottone sotto lo schema turni,
     dopo aver premuto "Genera turni"): trasforma ogni turno assegnato
     nell'ultima soluzione in un vincolo admin (`AM`/`AP`/`AN`) nella
     griglia, cosi' puoi tenere fermo quasi tutto lo schema, modificare
     a mano solo le celle che vuoi cambiare, e premere di nuovo "Genera
     turni" per ricalcolare tenendo conto delle modifiche. I giorni
     senza assegnazione non vengono toccati.

**Nota su un bug corretto**: Streamlit esegue il codice di ogni scheda
nell'ordine in cui compare nello script, non in base a quale scheda l'utente
ha aperta. Prima "Regole & periodo" era l'ultima scheda nel codice: cambiare
anno/mese aggiornava `session_state`, ma le altre schede (gia' eseguite
sopra in quello stesso giro) mostravano ancora i valori vecchi fino al giro
di esecuzione successivo. Spostando "Regole & periodo" per prima anche nel
codice, gli aggiornamenti si propagano subito, nello stesso giro.

## Tempo massimo di calcolo e ottimalita'

Sopra il pulsante "Genera turni" c'e' uno slider **"Tempo massimo di
calcolo"** (default 30s, fino a 300s). Il motore (CP-SAT) lavora per
approssimazioni successive verso la soluzione migliore: se il tempo scade
prima di aver *dimostrato* che la soluzione trovata e' la migliore
possibile, la restituisce comunque (stato `FEASIBLE` invece di `OPTIMAL`).

Dopo ogni generazione, un messaggio indica se l'**ottimalita' e' stata
dimostrata**:
- ✅ dimostrata → aumentare il tempo non cambierebbe il risultato
- ⏱️ tempo scaduto prima di dimostrarla → potrebbe esistere una soluzione
  migliore; alzare lo slider e rigenerare puo' aiutare

**"Nessuna soluzione trovata" vs "tempo scaduto" — due cose diverse.**
Se non trova nulla, l'app ora distingue due situazioni ben diverse (prima
venivano confuse, un bug corretto):
- **Infeasible** (dimostrato impossibile): il motore ha *dimostrato* che
  non esiste alcuna soluzione valida. Serve ridurre il fabbisogno minimo
  o i vincoli forzati.
- **Tempo scaduto** (stato incerto): il tempo massimo e' scaduto *prima*
  che il motore trovasse una soluzione O dimostrasse l'impossibilita'.
  **Non significa che il problema sia irrisolvibile** — con problemi
  complessi (tanti lavoratori, vincoli stretti come il riposo dopo
  notte) il motore potrebbe semplicemente aver bisogno di piu' tempo.
  In questo caso alza il "Tempo massimo di calcolo" e rigenera prima di
  concludere che i vincoli sono incompatibili.

Premi "Genera turni" per vedere:
- **lo schema turni colorato**, che include anche le colonne della
  situazione iniziale (icona 🕓, stessi giorni e stesso contenuto della
  griglia di input) — non sono decisioni del motore, solo contesto su
  cosa e' gia' successo prima del periodo. Le intestazioni di colonna
  usano la stessa etichettatura (icone 🕓/➡️ + giorno della settimana +
  data) della griglia "Situazione iniziale + Richieste/Vincoli"
- la copertura effettiva vs fabbisogno (giorni in colonna, M/P/N in riga)
- **Turni per lavoratore**: M/P/N/**Ferie**/Totale turni/Ore M/P/N/**Ore F**
  sono calcolati **sul solo mese di riferimento selezionato** (escludono
  sia la situazione iniziale del mese precedente sia l'eventuale
  sconfinamento nel mese successivo). "Ferie" conta i giorni di ferie
  (admin forzata o richiesta soft accolta), non e' inclusa nel Totale
  turni perche' non e' un turno lavorato; "Ore F" sono le sue ore
  virtuali equivalenti. Le colonne "Ore sett.N" includono le ore
  effettivamente lavorate **piu' le ore virtuali di ferie** (stesso
  criterio usato dal motore per il vincolo di ore settimanali), oltre
  alla situazione iniziale **della stessa settimana ISO del periodo** e
  agli eventuali giorni nel mese successivo. "Ore mese" conta invece
  solo le ore effettivamente lavorate nel mese di riferimento (non
  include le ore virtuali di ferie, a differenza di "Ore sett.N")

  **Nota su un bug corretto**: la griglia mostra sempre almeno
  `GIORNI_STATO_INIZIALE_MINIMO` giorni di situazione iniziale per
  motivi di leggibilita' (completare la settimana calendario a schermo);
  quando il mese inizia lun-ven, questo puo' includere giorni di una
  settimana ISO **precedente** a quella del periodo (es. mese che inizia
  mercoledi': i primi 2 dei 4 giorni mostrati cadono nella settimana
  prima, che il motore non pianifica affatto). Prima della correzione,
  queste voci comparivano come una colonna "Ore settimana" per una
  settimana completamente estranea al periodo. Il motore stesso non ne
  e' mai stato affetto (consulta le ore pregresse solo per le settimane
  che ha effettivamente in pianificazione, quindi il calcolo dei turni
  e' sempre stato corretto) — era un problema solo di visualizzazione,
  corretto scartando dal conteggio le voci di situazione iniziale la cui
  settimana ISO non coincide con quella del primo giorno del periodo.
- le richieste non soddisfatte
- **Equilibrio del carico tra lavoratori**: grafico a barre orizzontali
  (lavoratori sull'asse verticale, ore su quello orizzontale) con le ore
  M/P/N/F per lavoratore, **nell'ordine esplicito M-P-N-F garantito**.
  Costruito con Altair (`st.altair_chart`) invece del piu' semplice
  `st.bar_chart`: quest'ultimo forza l'ordine alfabetico delle serie
  impilate/legenda e non espone modo di cambiarlo (limite noto, issue
  aperta sul repository di Streamlit)

## Cosa fa il motore adesso (completo sui vincoli principali)

**Livello 1 - vincoli strutturali di sistema (sempre hard):**
- un lavoratore fa al massimo una fascia (M/P/N) al giorno
- copertura minima di personale per giorno/fascia (fabbisogno)
- **riposo obbligatorio dopo un turno notturno, o dopo l'ultima notte di
  una serie consecutiva**: **veri giorni di riposo** (nessun turno di
  alcun tipo, notte compresa — non solo "niente M/P"), default **2
  giorni** (non piu' 1), configurabile via
  `regole_contrattuali.giorni_riposo_dopo_notte`. Rileva correttamente
  quando una notte e' l'ULTIMA di una serie consecutiva (vincolo
  condizionato "oggi notte E domani non notte"), applicando il riposo
  pieno solo da li' in poi — non dopo ogni notte della serie
  singolarmente, altrimenti bloccherebbe anche la legittima continuazione
  della serie stessa
- vincolo personale "mai notti" (`lavoratore.vincoli_personali.mai_notti`)

**Nota su un bug corretto**: la prima versione di questo vincolo
bloccava solo M/P dopo la notte, mai un'altra N — quindi un pattern come
"notte, 1 giorno di pausa, notte" passava inosservato (non violava
"niente M/P" ma violava il vero requisito di 2 giorni di riposo pieno).
Corretto rilevando esplicitamente quando una notte e' l'ultima della sua
serie e bloccando in quel caso *tutte* le fasce nella finestra di
riposo, non solo M/P.
- massimo notti consecutive (default 2, override possibile per singolo
  lavoratore)
- **massimo giorni di lavoro consecutivi, qualsiasi fascia** (default
  **5**, configurabile via `regole_contrattuali.max_giorni_
  consecutivi_lavorati`): oltre 5 giorni di fila con un turno assegnato
  (M, P o N indifferentemente) serve almeno un giorno libero. Campo
  presente nel modello dati fin dall'inizio del progetto ma mai
  collegato a un vincolo reale — bug di progettazione trovato e
  corretto. Stesso schema del massimo notti consecutive: tiene conto
  anche dei giorni gia' lavorati nella situazione iniziale a cavallo di
  mese (una finestra scorrevole verifica che nessun gruppo di
  max+1 giorni consecutivi sia tutto lavorato, riducendo il margine
  iniziale in base a quanti giorni consecutivi risultano gia' lavorati
  subito prima dell'inizio del periodo)
- **riposo obbligatorio dopo aver raggiunto il massimo di giorni
  lavorativi consecutivi** (default **2** giorni, configurabile via
  `regole_contrattuali.giorni_riposo_dopo_serie_lavorativa`): quando un
  lavoratore raggiunge il numero massimo di giorni consecutivi (sopra),
  i successivi N giorni devono essere **vero riposo** (nessun turno di
  alcun tipo), non solo "un giorno libero" — stesso principio del
  riposo dopo la notte, applicato pero' alla serie generale di giorni
  lavorati invece che solo alle notti. Non serve rilevare esplicitamente
  se una serie e' davvero finita (come per le notti): il vincolo sul
  massimo giorni consecutivi garantisce gia' che il giorno dopo una
  finestra completamente lavorata non possa essere lavorato, quindi se
  gli ultimi N giorni sono tutti lavorati e' per costruzione la fine
  della serie. Tiene conto anche di `stato_iniziale` a cavallo di mese
- **vieto rigido opzionale di Pomeriggio -> Mattino su giorni
  consecutivi** (default **disattivato**, `regole_contrattuali.
  vieta_pm_consecutivo`): alternativa piu' restrittiva al termine di
  fairness "Minimizza le sequenze Pomeriggio -> Mattino" (Livello 4
  sotto) — invece di scoraggiarle nell'obiettivo, le vieta del tutto.
  **Mutuamente esclusivo** col termine soft: l'interfaccia disattiva
  automaticamente quello soft quando questo e' attivo (e lo mostra
  disabilitato). Puo' ridurre la flessibilita' del motore e in scenari
  con pochi lavoratori rendere infeasible cio' che con solo la
  penalizzazione soft sarebbe stato risolvibile. Tiene conto anche di
  `stato_iniziale`: se l'ultimo turno prima del periodo e' Pomeriggio,
  vieta il Mattino sul primo giorno del periodo
- **scarto massimo rigido opzionale tra lavoratori per fascia**
  (default **disattivato**, `parametri_fairness.bilancia_fasce_hard` +
  `scarto_massimo_M`/`scarto_massimo_P`/`scarto_massimo_N`, default
  **5** ciascuno): alternativa piu' restrittiva al termine di fairness
  "Bilancia il numero di turni per fascia" (Livello 4 sotto) — invece di
  scoraggiare lo squilibrio nell'obiettivo, impone che la differenza tra
  il lavoratore col conteggio piu' alto e quello col conteggio piu'
  basso (per fascia, sull'intero periodo) non superi la soglia.
  **Mutuamente esclusivo** col termine soft corrispondente (stessa
  disattivazione automatica di sopra). **Normalizzato per la capacita'
  contrattuale** (`ore_settimanali_max`, stessa proxy gia' usata dal
  termine soft "Bilancia le ore lavorate"): un part-time con meta' delle
  ore massime che fa 3 notti conta come equivalente a 6 di un full-time
  — non e' uno squilibrio da correggere, e' la conseguenza naturale del
  contratto. Senza questa normalizzazione, un part-time verrebbe
  penalizzato ingiustamente per avere naturalmente meno turni di chi ha
  piu' ore disponibili. I lavoratori con `vincoli_personali.
  mai_notti=True` sono esclusi dal confronto sulla fascia N (fissi a 0
  per contratto: includerli renderebbe il vincolo violato quasi sempre)
- tutti questi vincoli tengono conto di `stato_iniziale` per i casi a
  cavallo con il mese precedente

**Livello 2 - vincoli admin (hard, imposti dal coordinatore):**
- "ferie" forzata o "riposo" forzato -> giorno bloccato (nessun turno)
- "turno" forzato -> fascia specifica imposta
- **ferie e riposo bloccano allo stesso modo, ma non sono equivalenti**:
  vedi "Ferie vs riposo" sotto per la differenza sul monte ore
- **niente notte nei `giorni_riposo_dopo_notte` giorni prima di una ferie
  forzata**: il giorno di stop dopo una notte (o serie di notti) e' un
  riposo fisiologico obbligatorio, non sostituibile da una ferie — il
  motore lo impedisce anche se inserito per errore, cercando un'altra
  soluzione (es. assegnando quella notte a un altro lavoratore)
- nota: la validazione preventiva di conflitti tra vincoli admin e il
  meccanismo di declassamento automatico sono rimandati a una fase
  successiva (come deciso insieme)

**Attenzione — vincolo admin "turno forzato" vicino all'inizio del
periodo + notti pregresse**: se un lavoratore ha gia' esaurito il
margine di notti consecutive con notti nella situazione iniziale (es. 2
notti su un massimo di 2), un vincolo admin che forza un turno M/P nei
primissimi giorni del periodo (dentro la finestra di riposo dovuta a
quelle notti) puo' creare un vicolo cieco senza soluzione: continuare la
serie di notti per evitare il riposo viola il massimo notti consecutive,
fermarsi attiva il riposo che blocca il turno forzato. **Bug scoperto in
produzione** (causa isolata in un vincolo di esempio in
`sample_data.py`, troppo vicino all'inizio del periodo rispetto alla
situazione iniziale generata automaticamente — corretto spostandolo piu'
avanti nel mese). Se imposti un turno forzato nei primi giorni del
periodo, verifica che il lavoratore non abbia gia' notti pregresse
recenti nella situazione iniziale che potrebbero richiedere riposo
proprio in quei giorni.

**Livello 3 - richieste soft pesate (preferenze lavoratore):**
- scala di priorita' 1 (indifferente) - 4 (molto importante), mappata
  internamente su pesi esponenziali (1, 10, 100, 1000) cosi' una
  richiesta di priorita' alta non viene mai sacrificata per soddisfarne
  tante di priorita' bassa
- tipi disponibili: `ferie`, `riposo`, `turno` (fascia specifica)
- se una richiesta di **ferie** viene concessa (il lavoratore risulta
  libero quel giorno), vale la stessa regola del vincolo admin: niente
  notte il giorno prima — il motore valuta quindi se concedere la
  richiesta vale la "ri-assegnazione" di quella notte a qualcun altro
- le richieste non soddisfatte vengono riportate esplicitamente in output

**Monte ore settimanale:**
ore settimanali da contratto come **intervallo [minimo, massimo]**, non
un singolo valore fisso — sotto il minimo non si puo' andare (il motore
assegna turni extra oltre il fabbisogno minimo se necessario per
garantirlo), sopra il massimo nemmeno. Se minimo e massimo coincidono,
le ore sono obbligatoriamente uguali a quel valore unico (comportamento
"a valore fisso", utile se il contratto prevede un numero di ore preciso
senza flessibilita'). **Sempre specifico per singolo lavoratore**
(`lavoratore.ore_settimanali_min` / `ore_settimanali_max`, espressi in
ore intere, nessun fallback su un default globale — un valore 0 viene
rispettato letteralmente, non sostituito silenziosamente), calcolato su
settimane calendario lun-dom. Se la prima settimana del periodo e' a
cavallo con l'ultima settimana del mese precedente, le ore gia' maturate
in `stato_iniziale` in quella settimana vengono sommate al conteggio.
Vedi anche "Ferie vs riposo" sotto per come contano le giornate di ferie.

**Durata dei turni e delle ferie in ore E minuti**: la durata di
ciascuna fascia (`regole_contrattuali.minuti_per_fascia`, default M=8h,
P=8h, N=10h) e le ore virtuali di ferie
(`regole_contrattuali.minuti_ferie_giornaliere`, default 8h) sono
espresse **internamente in minuti**, non solo ore intere — permette
turni come 7h30m (450 minuti), non solo valori tondi. Nell'interfaccia,
ciascuna di queste durate ha due campi affiancati ("Ore" e "Minuti"),
combinati in minuti totali alla generazione dei turni. Il monte ore
settimanale (`ore_settimanali_min`/`max` sul lavoratore) resta invece in
ore intere — la conversione in minuti avviene automaticamente al
momento del confronto (es. 36h -> 2160 minuti), senza bisogno di
configurarlo separatamente. Le tabelle di riepilogo ("Turni per
lavoratore", grafico "Equilibrio del carico") mostrano le ore in
**formato decimale** (es. 7.5 per 7h30m), non in formato "7h 30m",
per restare compatibili con somme ed export CSV.

**Nota**: un minimo troppo alto rispetto ai giorni/turni disponibili
(es. superiore a quanto ottenibile anche lavorando ogni giorno) rende il
problema `infeasible`, coerentemente con lo stesso comportamento gia'
in uso per gli altri vincoli hard del motore.

**Settimane parziali — il minimo NON si proporziona (rimosso).** Se il
mese non inizia di lunedi', la prima settimana del periodo ha meno di 7
giorni controllabili (es. mese che inizia mercoledi' -> solo 5 giorni;
l'ultima settimana e' invece sempre completa, perche' il periodo si
estende fino alla domenica). Una versione precedente di questo vincolo
proporzionava automaticamente il minimo ai giorni disponibili — rimossa
perche' un'approssimazione: la soluzione corretta e' compilare la
**situazione iniziale** con i turni realmente effettuati nei giorni
immediatamente precedenti al periodo, cosi' le ore gia' maturate si
sommano naturalmente al totale della settimana (vedi sopra
`ore_pregresse`/`minuti_pregressi_per_settimana`), rendendo il minimo
raggiungibile senza bisogno di ridurlo artificialmente. **Una situazione
iniziale vuota o incompleta puo' quindi rendere infeasible la prima
settimana** — segnale corretto che manca l'informazione, non un bug.
L'app genera comunque un pattern di default plausibile per la
situazione iniziale invece di lasciarla vuota (vedi sopra nella sezione
Calendario), ma per la massima affidabilita' andrebbe sostituito con i
turni veri.

**Perche' non toccare anche riposo dopo notte e massimo notti
consecutive?** Questi vincoli gia' tengono conto della situazione
iniziale (per costruzione, da prima di questa modifica) e restano cosi'
— a differenza del monte ore (una questione di "contabilita'"), sono
vincoli di sicurezza fisiologica: ignorare notti pregresse per questi
due vincoli specifici rischierebbe di produrre turni realmente scorretti
(es. una terza notte consecutiva non rilevata, o un turno assegnato
senza il riposo dovuto), anche se il modello risultasse "corretto" sulla
carta.

**Ferie vs riposo — differenza sul monte ore:**
entrambe bloccano i turni allo stesso identico modo, ma non sono
equivalenti nel monte ore settimanale: una giornata di **ferie**
(forzata dall'admin o concessa tramite richiesta soft) aggiunge
`regole_contrattuali.ore_ferie_giornaliere` (default 8h, un solo valore
per reparto) al conteggio ore settimanali — e' comunque tempo retribuito
nel rapporto di lavoro. Il **riposo** non aggiunge nulla. Esempio: con un
contratto da 36h, 4 giorni lavorati (32h) + 1 ferie (8h virtuali) = 40h,
che supera il monte ore anche se il lavoratore ha fisicamente lavorato
solo 32 ore — il motore lo tiene in considerazione e riduce di
conseguenza i turni reali assegnabili quella settimana.

**Livello 4 - fairness (soft, priorita' piu' bassa):**
- minimizza la **somma degli scarti di ciascun lavoratore dalla media del
  gruppo** sul numero di turni per fascia e sul totale di giorni
  lavorati — non piu' un semplice max-min (il divario tra chi ne fa di
  piu' e chi di meno). Il max-min resta fisso indipendentemente da
  quanti lavoratori sono fuori media: con surplus piccoli andava bene,
  ma con surplus grandi (es. generati da un minimo ore settimanali alto)
  restava "piccolo e fisso" mentre altri termini che sommano su
  giorni/fasce crescevano con la scala del problema, finendo per
  annegare completamente questo segnale. La somma degli scarti dalla
  media cresce naturalmente con la scala del problema, restando
  comparabile agli altri termini. Scelta al posto del confronto tra ogni
  coppia di lavoratori (sarebbe O(n²): 190 coppie per fascia con
  20 lavoratori) perche' confrontare ciascuno con la media e' O(n) — 20
  confronti per fascia — con lo stesso effetto pratico. **Normalizzato
  per la capacita' contrattuale** (stessa logica della versione hard
  gemella `bilancia_fasce_hard` — vedi sopra): un part-time con meta'
  delle ore massime non viene "spinto" verso lo stesso conteggio grezzo
  di un full-time, ne' incluso nel confronto sulla fascia N se ha
  `mai_notti=True`. Nel caso comune (tutti i lavoratori con la stessa
  `ore_settimanali_max`) il comportamento e' matematicamente identico a
  prima — nessuna regressione, verificato numericamente
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
  significativo perche' e' proporzionale, non assoluto. **Limite di
  questo termine**: minimizza solo il caso peggiore in assoluto su tutto
  il mese, quindi puo' lasciare che molti singoli giorni restino
  comunque sbilanciati (es. un giorno con 8 Mattina e 5 Pomeriggio) senza
  che questo emerga come "il peggiore del mese" — vedi il termine
  successivo per il fix mirato a questo
- **bilancia il surplus tra fasce, giorno per giorno**
  (`bilancia_proporzione_giornaliera`): a differenza del termine sopra,
  confronta le fasce presenti in OGNI singolo giorno (proporzionalmente
  al fabbisogno di quel giorno) e SOMMA lo scarto su tutti i giorni, non
  solo il caso peggiore — cosi' ogni giorno deve essere ragionevole, non
  solo il mese nel complesso. Include tutte le fasce (anche N, non solo
  M/P), cosi' il meccanismo resta corretto anche se in futuro le ore per
  fascia cambiano o serve organizzativamente un surplus notturno
- **minimizza le sequenze Pomeriggio -> Mattino su giorni consecutivi**
  per lo stesso lavoratore (attivabile/disattivabile, attivo di default):
  un turno P seguito da un turno M il giorno dopo lascia un riposo molto
  piu' corto (P finisce sera tardi, M inizia presto la mattina dopo)
  rispetto a M -> P (M finisce a meta' giornata, P il giorno dopo inizia
  solo nel pomeriggio: quasi un giorno intero di margine). Non viene
  vietato — spesso e' inevitabile per esigenze di copertura, motivo per
  cui e' un termine soft e non un vincolo hard — ma minimizzato dove
  possibile, premiando implicitamente M->P rispetto a P->M
- **ciascuno dei 6 vincoli soft sopra ha un peso individuale**
  (`peso_bilancia_fasce`, `peso_bilancia_giorni_settimana`,
  `peso_bilancia_ore_settimanali`, `peso_bilancia_copertura_giornaliera`,
  `peso_minimizza_pm_consecutivo`, `peso_bilancia_proporzione_giornaliera`),
  non piu' un unico peso condiviso — un peso condiviso penalizzava tutti
  i vincoli nella stessa proporzione, impedendo di dare piu' importanza a
  uno specifico senza alterare anche gli altri. Tre preset di partenza
  disponibili in UI (poi modificabili singolarmente):
  - **Equilibrio reparto** (consigliato, default): privilegia il
    bilanciamento del surplus per singolo giorno (6) e sul complesso del
    mese (7), tenendo basso l'evitamento P->M (2), utile quando fasce con
    lo stesso fabbisogno (es. M e P) rischiano di finire sbilanciate tra
    loro sia sul totale mensile sia giorno per giorno
  - **Benessere lavoratori**: privilegia il riposo fisiologico
    (minimizza P->M a 6) e il bilanciamento ore settimanali (6),
    accettando un po' piu' di squilibrio tra fasce pur di proteggere
    meglio i tempi di recupero
  - **Leggero**: tutti i pesi a 1, la fairness interviene pochissimo
  
  Tutti i valori restano sotto 10 (il peso di una richiesta soft di
  priorita' media), cosi' le preferenze dei lavoratori continuano a
  prevalere sull'equilibrio del team

**Nota su un bug corretto**: la normalizzazione finale del termine
"spalma surplus copertura" divideva due volte per lo stesso fattore di
scala, schiacciando quasi a zero un segnale che avrebbe dovuto essere
significativo (uno scarto di 200 punti percentuali produceva un
contributo finale di appena 2 nell'obiettivo, contro scarti tipici di
10-20 degli altri termini fairness) — corretto usando un fattore di
rinormalizzazione molto piu' piccolo (10 invece di 100), che preserva
il segnale invece di annullarlo.

## Dataset di esempio

`engine/sample_data.py` (usato sia dai test che come default in Streamlit)
simula un reparto con **20 infermieri** e un fabbisogno giornaliero di
**3 Mattino + 3 Pomeriggio + 2 Notte** (8 turni/giorno), per **l'intero
periodo esteso** di luglio 2026 (1 luglio - 2 agosto, 33 giorni: luglio
finisce venerdi' 31, quindi il periodo si estende fino alla domenica
successiva). Include anche una situazione iniziale generata (ciclo
M-P-riposo, offset diverso per lavoratore — vedi
`_genera_stato_iniziale_demo`, stessa logica del generatore usato in
`app.py`): necessaria perche' luglio 2026 inizia di mercoledi', quindi
la prima settimana del periodo ha solo 5 giorni controllabili, e il
minimo ore settimanali (32h) non e' proporzionato automaticamente (vedi
"Monte ore settimanale" sopra) — senza situazione iniziale, lo scenario
di esempio risulterebbe `infeasible` sulla prima settimana.

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
