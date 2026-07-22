"""
Motore di generazione turni - STEP 6 (completo per i vincoli principali).

Livelli implementati, in ordine di priorita' (dal piu' al meno vincolante):

  1. VINCOLI STRUTTURALI DI SISTEMA (sempre hard):
     - un lavoratore fa al massimo una fascia al giorno
     - copertura minima per giorno/fascia (fabbisogno)
     - riposo obbligatorio dopo un turno notturno: veri giorni di riposo
       (nessun turno di alcun tipo, notte compresa — non solo "niente
       M/P"), default 2 giorni, configurabile via
       regole_contrattuali.giorni_riposo_dopo_notte; rileva correttamente
       quando una notte e' l'ULTIMA di una serie consecutiva (tramite un
       vincolo condizionato "oggi notte E domani non notte"), applicando
       il riposo solo da li' in poi, non dopo ogni notte della serie
     - vincolo personale "mai notti" (lavoratore.vincoli_personali.mai_notti)
     - massimo notti consecutive (con override personale possibile)
     - massimo giorni di lavoro consecutivi, qualsiasi fascia (default 5,
       configurabile via regole_contrattuali.max_giorni_consecutivi_
       lavorati; tiene conto anche di stato_iniziale per i giorni gia'
       lavorati a cavallo di mese, stesso schema del massimo notti
       consecutive)
     - riposo obbligatorio dopo aver raggiunto il massimo di giorni
       lavorativi consecutivi (default 2 giorni, configurabile via
       regole_contrattuali.giorni_riposo_dopo_serie_lavorativa): veri
       giorni di riposo, non solo "un giorno libero" — stesso principio
       del riposo dopo la notte ma applicato alla serie generale invece
       che solo alle notti; tiene conto anche di stato_iniziale a
       cavallo di mese
     - vieto HARD opzionale (default disattivato) di Pomeriggio->Mattino
       su giorni consecutivi (regole_contrattuali.vieta_pm_consecutivo):
       alternativa piu' rigida al termine soft
       parametri_fairness.minimizza_pm_consecutivo, mutuamente esclusiva
       con esso (l'interfaccia disattiva il soft quando l'hard e' attivo)
     - scarto massimo HARD opzionale (default disattivato) tra
       lavoratori per fascia (parametri_fairness.bilancia_fasce_hard +
       scarto_massimo_M/P/N, default 5 ciascuno): alternativa piu' rigida
       al termine soft bilancia_fasce, mutuamente esclusiva con esso.
       ENTRAMBE le versioni (hard e soft) normalizzano i conteggi per la
       capacita' contrattuale (ore_settimanali_max) prima del confronto,
       cosi' un part-time con meta' delle ore non viene penalizzato per
       avere naturalmente meno turni. I lavoratori con
       vincoli_personali.mai_notti=True sono esclusi dal confronto sulla
       fascia N, in entrambe le versioni
     - scarto massimo HARD opzionale (default disattivato, in punti
       percentuali) tra giorni per il TASSO di surplus di copertura per
       fascia (parametri_fairness.bilancia_copertura_giornaliera_hard +
       scarto_massimo_copertura_M/P/N, default 50 ciascuno): alternativa
       piu' rigida al termine soft bilancia_copertura_giornaliera,
       mutuamente esclusiva con esso. Usa la STESSA proporzione
       surplus/fabbisogno_minimo del soft (non il conteggio grezzo, che
       sarebbe fuorviante con fabbisogni diversi tra giorni), calcolata
       separatamente per M, P e N. Giorni con fabbisogno 0 per quella
       fascia sono esclusi dal confronto (tasso surplus/0 non definito)
     (tutti tengono conto di stato_iniziale per i casi a cavallo di mese)

  2. VINCOLI ADMIN (hard, imposti dal coordinatore):
     - "ferie" / "riposo" forzati -> giorno bloccato (nessun turno). La
       differenza tra i due non e' nel blocco (identico), ma nel monte ore
       settimanale: la ferie aggiunge ore virtuali (vedi punto sotto), il
       riposo no
     - "turno" forzato -> fascia specifica imposta
     (nota: la validazione preventiva di conflitti e il meccanismo di
     declassamento automatico sono volutamente rimandati a una fase
     successiva, come concordato)

  3. RICHIESTE SOFT (preferenze lavoratore, pesate 1-4):
     entrano nella funzione obiettivo come penalita' se non soddisfatte,
     con pesi esponenziali cosi' una richiesta di priorita' alta non
     viene mai sacrificata per soddisfarne tante di priorita' bassa.
     Anche qui, ferie e riposo condividono lo stesso vincolo di blocco ma
     si comportano diversamente nel monte ore (vedi sotto)

  MONTE ORE SETTIMANALE E FERIE VS RIPOSO:
     ore settimanali da contratto come INTERVALLO [minimo, massimo], non
     un singolo valore fisso, sempre per singolo lavoratore
     (lavoratore.ore_settimanali_min / ore_settimanali_max, nessun
     fallback su un default globale). Sotto il minimo non si puo' andare
     (il motore puo' assegnare surplus oltre il fabbisogno minimo se
     necessario per garantirlo), sopra il massimo nemmeno; se minimo ==
     massimo, equivale a un valore fisso obbligatorio. Calcolato dopo i
     livelli 2 e 3 perche' le giornate di FERIE (forzate dall'admin o
     concesse tramite richiesta soft accolta) aggiungono ore "virtuali"
     al monte ore (regole_contrattuali.minuti_ferie_giornaliere, e' comunque
     tempo retribuito), mentre il RIPOSO non aggiunge nulla. Tutto tiene
     conto di stato_iniziale per i casi a cavallo di mese, incluse le ore
     gia' maturate nella stessa settimana ISO se la settimana e' a
     cavallo con il mese precedente.

     SETTIMANE PARZIALI: il minimo NON viene proporzionato quando la
     prima settimana del periodo ha meno di 7 giorni controllabili (il
     mese non inizia di lunedi' — l'ultima settimana e' invece sempre
     completa, dato che il periodo si estende fino alla domenica). La
     situazione iniziale (stato_iniziale) e' pensata per essere sempre
     compilata con i turni realmente effettuati nei giorni immediatamente
     precedenti al periodo: con dati veri, le ore gia' maturate si
     sommano naturalmente al totale della settimana, rendendo il minimo
     raggiungibile senza bisogno di ridurlo artificialmente. Una
     situazione iniziale vuota o incompleta puo' quindi rendere il
     problema infeasible per la prima settimana — segnale corretto che
     manca l'informazione, non un bug da mascherare abbassando il
     vincolo (versione precedente di questo vincolo proporzionava il
     minimo automaticamente; rimosso perche' la situazione iniziale
     compilata correttamente e' la soluzione piu' corretta, non
     un'approssimazione).

  4. FAIRNESS (soft, priorita' piu' bassa):
     minimizza la SOMMA degli scarti di ciascun lavoratore dalla MEDIA del
     gruppo, sul numero di turni per fascia e sul numero di giorni
     lavorati totali (non piu' un semplice max-min tra il lavoratore col
     conteggio piu' alto e quello piu' basso: quella misura restava fissa
     indipendentemente da quanti lavoratori fossero fuori media, e con
     volumi di surplus grandi finiva annegata da altri termini "a somma"
     sotto — la somma degli scarti dalla media invece cresce
     naturalmente con la scala del problema, restando comparabile agli
     altri); minimizza inoltre lo scarto (max - min) del TASSO DI
     UTILIZZO della capacita' oraria residua, SETTIMANA PER SETTIMANA
     (non solo sul totale del periodo): confrontiamo il tasso (ore nuove
     assegnate / capacita' residua quella settimana), non le ore grezze,
     perche' un lavoratore che ha gia' maturato ore in stato_iniziale ha
     una capacita' residua legittimamente piu' bassa quella settimana —
     confrontare le ore grezze spingerebbe un peso alto a "trascinare
     giu'" anche gli altri lavoratori pur di ridurre lo scarto, l'esatto
     opposto dell'effetto voluto; minimizza inoltre lo scarto (max - min)
     del TASSO di surplus di copertura (surplus / fabbisogno minimo, non
     il surplus grezzo), confrontato su un'unica scala tra tutte le
     fasce e i giorni insieme: cosi' un eventuale surplus si distribuisce
     in proporzione al fabbisogno invece di concentrarsi su una fascia o
     un giorno specifico, anche quando il fabbisogno non e' uguale
     ovunque; minimizza anche (sommando su ogni singolo giorno, non solo
     il caso peggiore) lo scarto tra le fasce presenti in ciascun
     giorno; minimizza infine (se attivo) le sequenze Pomeriggio->Mattino
     su giorni consecutivi per lo stesso lavoratore, che lasciano un
     riposo piu' corto rispetto a Mattino->Pomeriggio — non e' vietato
     (spesso inevitabile per la copertura), solo penalizzato, premiando
     implicitamente M->P su P->M

Ogni livello e' testato in tests/test_solver.py.
"""

import datetime

from ortools.sat.python import cp_model

from engine.models import (
    InputTurnazione,
    OutputTurnazione,
    Assegnazione,
    RichiestaNonSoddisfatta,
)
from engine.periodo_utils import data_da_indice_periodo, data_da_indice_mese_precedente

# Pesi esponenziali per la scala di priorita' 1-4 delle richieste soft.
# L'esponenzialita' garantisce che il solver non sacrifichi mai una
# richiesta di livello alto per soddisfarne molte di livello basso.
PESI_PRIORITA = {1: 1, 2: 10, 3: 100, 4: 1000}


def _raggruppa_per_settimana_iso(anno: int, mese: int, giorni: list[int]) -> dict[tuple[int, int], list[int]]:
    """Raggruppa i giorni per (anno_iso, settimana_iso), cosi' le settimane
    sono calendario lun-dom. Gestisce sia i cambi di mese/anno sia il caso
    in cui il periodo si estenda oltre la fine del mese selezionato (indici
    giorno che superano il numero di giorni nel mese)."""
    settimane: dict[tuple[int, int], list[int]] = {}
    for g in giorni:
        data = data_da_indice_periodo(anno, mese, g)
        anno_iso, settimana_iso, _ = data.isocalendar()
        chiave = (anno_iso, settimana_iso)
        settimane.setdefault(chiave, []).append(g)
    return settimane


def genera_turni(dati: InputTurnazione, tempo_max_secondi: float = 30.0) -> OutputTurnazione:
    model = cp_model.CpModel()

    giorni = list(range(dati.periodo.giorno_inizio, dati.periodo.giorno_fine + 1))
    fasce = dati.fasce_orarie
    lavoratori_ids = [l.id for l in dati.lavoratori]

    # ------------------------------------------------------------------
    # Variabili: x[(lavoratore, giorno, fascia)] = 1 se assegnato
    # ------------------------------------------------------------------
    x = {}
    for w in lavoratori_ids:
        for g in giorni:
            for f in fasce:
                x[(w, g, f)] = model.NewBoolVar(f"x_{w}_{g}_{f}")

    # ==================================================================
    # LIVELLO 1: vincoli strutturali di sistema
    # ==================================================================

    # Un lavoratore fa al massimo una fascia al giorno
    for w in lavoratori_ids:
        for g in giorni:
            model.Add(sum(x[(w, g, f)] for f in fasce) <= 1)

    # Copertura minima per giorno/fascia (fabbisogno)
    for fab in dati.fabbisogno:
        if fab.giorno in giorni and fab.fascia in fasce:
            model.Add(
                sum(x[(w, fab.giorno, fab.fascia)] for w in lavoratori_ids) >= fab.minimo
            )

    # Riposo obbligatorio dopo un turno notturno (o dopo l'ultima notte di
    # una serie consecutiva): per default 2 giorni interi di vero riposo,
    # NON solo "niente M/P" — niente turno di alcun tipo, notte compresa.
    #
    # Serve rilevare quando una notte e' davvero l'ULTIMA della sua serie:
    # se il giorno dopo e' ANCH'ESSO notte, la serie continua (fino al
    # massimo consentito) e non scatta ancora il riposo. Lo facciamo con
    # un vincolo condizionato a due letterali: "se oggi e' notte E domani
    # NON e' notte" -> blocca tutte le fasce nei giorni_riposo_dopo_notte
    # giorni successivi. Con 2 notti di fila, il vincolo NON scatta dalla
    # prima notte (perche' il giorno dopo e' notte anche lui) ma scatta
    # correttamente dalla seconda (l'ultima), producendo esattamente "2
    # giorni di vero riposo dopo l'ultima notte della serie".
    #
    # NOTA: prima di questa correzione veniva bloccato solo M/P (mai N),
    # quindi un pattern come "notte, 1 giorno di pausa, notte" passava
    # inosservato (non violava "niente M/P" ma violava il vero requisito
    # di 2 giorni di riposo pieno). Corretto qui.
    giorni_riposo_dopo_notte = max(dati.regole_contrattuali.giorni_riposo_dopo_notte, 1)
    if "N" in fasce:
        for w in lavoratori_ids:
            for g in giorni:
                giorno_dopo = g + 1
                if giorno_dopo in giorni:
                    letterali_fine_serie = [x[(w, g, "N")], x[(w, giorno_dopo, "N")].Not()]
                else:
                    # g e' l'ultimo giorno del periodo: non c'e' un giorno
                    # dopo da controllare, trattiamo g come fine-serie ai
                    # fini del blocco entro il periodo corrente.
                    letterali_fine_serie = [x[(w, g, "N")]]

                for offset in range(1, giorni_riposo_dopo_notte + 1):
                    giorno_riposo = g + offset
                    if giorno_riposo in giorni:
                        for f in fasce:
                            model.Add(x[(w, giorno_riposo, f)] == 0).OnlyEnforceIf(letterali_fine_serie)

    data_inizio_periodo = datetime.date(dati.periodo.anno, dati.periodo.mese, dati.periodo.giorno_inizio)
    data_giorno_prima_periodo = data_inizio_periodo - datetime.timedelta(days=1)

    # Stesso principio per le notti registrate in stato_iniziale (mese
    # precedente): dobbiamo comunque capire se sono "ultima notte della
    # serie" prima di bloccare tutte le fasce nella finestra di riposo.
    # Se il giorno successivo e' ancora nel mese precedente, e' un fatto
    # storico noto (deterministico, non serve una variabile del modello).
    # Se il giorno successivo e' il primo giorno del periodo, dipende da
    # una decisione del motore (variabile x[..., giorno_inizio, "N"]).
    for si in dati.stato_iniziale:
        if not (si.mese_precedente and si.fascia == "N" and si.lavoratore_id in lavoratori_ids):
            continue

        data_si = data_da_indice_mese_precedente(dati.periodo.anno, dati.periodo.mese, si.giorno)
        data_dopo = data_si + datetime.timedelta(days=1)

        letterali_fine_serie = None  # None = fatto certo, nessuna condizione
        if data_dopo < data_inizio_periodo:
            prossimo_e_notte = any(
                si2.lavoratore_id == si.lavoratore_id and si2.mese_precedente and si2.fascia == "N"
                and data_da_indice_mese_precedente(dati.periodo.anno, dati.periodo.mese, si2.giorno) == data_dopo
                for si2 in dati.stato_iniziale
            )
            if prossimo_e_notte:
                continue  # non e' l'ultima notte della serie, la gestira' quella successiva
        elif data_dopo == data_inizio_periodo:
            letterali_fine_serie = [x[(si.lavoratore_id, dati.periodo.giorno_inizio, "N")].Not()]
        else:
            continue  # caso non atteso

        for offset in range(1, giorni_riposo_dopo_notte + 1):
            data_da_bloccare = data_si + datetime.timedelta(days=offset)
            if data_da_bloccare < data_inizio_periodo:
                continue  # ancora nel mese precedente, non e' una nostra variabile
            indice_periodo = dati.periodo.giorno_inizio + (data_da_bloccare - data_inizio_periodo).days
            if indice_periodo not in giorni:
                continue
            for f in fasce:
                if letterali_fine_serie is None:
                    model.Add(x[(si.lavoratore_id, indice_periodo, f)] == 0)
                else:
                    model.Add(x[(si.lavoratore_id, indice_periodo, f)] == 0).OnlyEnforceIf(letterali_fine_serie)

    # Vincolo personale "mai notti": alcuni lavoratori (es. per motivi di
    # salute) non possono mai fare il turno N. Questo campo esisteva gia'
    # nel modello dati e nella UI ma non era ancora applicato dal motore.
    if "N" in fasce:
        for w in lavoratori_ids:
            lavoratore = next(l for l in dati.lavoratori if l.id == w)
            if lavoratore.vincoli_personali.mai_notti:
                for g in giorni:
                    model.Add(x[(w, g, "N")] == 0)

    # Massimo notti consecutive
    if "N" in fasce:
        max_consec = dati.regole_contrattuali.max_notti_consecutive
        for w in lavoratori_ids:
            lavoratore = next(l for l in dati.lavoratori if l.id == w)
            limite = lavoratore.vincoli_personali.max_notti_consecutive_override or max_consec

            for start_idx in range(len(giorni) - limite):
                finestra = giorni[start_idx: start_idx + limite + 1]
                model.Add(sum(x[(w, g, "N")] for g in finestra) <= limite)

            # Notti gia' fatte a cavallo con il mese precedente: convertiamo
            # in date reali per contare correttamente le notti consecutive
            # anche quando il mese precedente ha un numero di giorni diverso.
            date_notti_precedenti = sorted(
                (data_da_indice_mese_precedente(dati.periodo.anno, dati.periodo.mese, si.giorno)
                 for si in dati.stato_iniziale
                 if si.lavoratore_id == w and si.fascia == "N" and si.mese_precedente),
                reverse=True,
            )
            consecutive_pregresse = 0
            data_attesa = data_giorno_prima_periodo
            for data_notte in date_notti_precedenti:
                if data_notte == data_attesa:
                    consecutive_pregresse += 1
                    data_attesa -= datetime.timedelta(days=1)
                else:
                    break

            if consecutive_pregresse > 0:
                margine = max(limite - consecutive_pregresse, 0)
                finestra_iniziale = giorni[: margine + 1]
                if finestra_iniziale:
                    model.Add(sum(x[(w, g, "N")] for g in finestra_iniziale) <= margine)

    # Massimo giorni lavorativi consecutivi (qualsiasi fascia, non solo
    # notte): stesso schema di "massimo notti consecutive" sopra, ma
    # conta un giorno come "lavorato" se il lavoratore fa M, P O N quel
    # giorno (non serve sommare le tre fasce separatamente: "un turno al
    # giorno" garantisce gia' che al massimo una sia 1). Campo presente
    # nel modello dati fin dall'inizio ma mai collegato a un vincolo reale
    # fino ad ora — bug di progettazione trovato e corretto qui.
    max_consec_giorni = dati.regole_contrattuali.max_giorni_consecutivi_lavorati
    giorni_riposo_dopo_serie = dati.regole_contrattuali.giorni_riposo_dopo_serie_lavorativa
    for w in lavoratori_ids:
        for start_idx in range(len(giorni) - max_consec_giorni):
            finestra = giorni[start_idx: start_idx + max_consec_giorni + 1]
            model.Add(sum(x[(w, g, f)] for g in finestra for f in fasce) <= max_consec_giorni)

        # Giorni lavorativi consecutivi gia' effettuati a cavallo con il
        # mese precedente (qualsiasi fascia, non solo notte): stessa
        # logica di date reali usata sopra per le notti pregresse.
        date_lavorate_precedenti = sorted(
            (data_da_indice_mese_precedente(dati.periodo.anno, dati.periodo.mese, si.giorno)
             for si in dati.stato_iniziale
             if si.lavoratore_id == w and si.mese_precedente),
            reverse=True,
        )
        consecutivi_pregressi_giorni = 0
        data_attesa_giorni = data_giorno_prima_periodo
        for data_lavorata in date_lavorate_precedenti:
            if data_lavorata == data_attesa_giorni:
                consecutivi_pregressi_giorni += 1
                data_attesa_giorni -= datetime.timedelta(days=1)
            else:
                break

        if consecutivi_pregressi_giorni > 0:
            margine_giorni = max(max_consec_giorni - consecutivi_pregressi_giorni, 0)
            finestra_iniziale_giorni = giorni[: margine_giorni + 1]
            if finestra_iniziale_giorni:
                model.Add(
                    sum(x[(w, g, f)] for g in finestra_iniziale_giorni for f in fasce) <= margine_giorni
                )

        # Riposo obbligatorio dopo aver raggiunto il massimo di giorni
        # lavorativi consecutivi: se un lavoratore lavora esattamente
        # max_consec_giorni giorni di fila (qualsiasi fascia), i
        # successivi giorni_riposo_dopo_serie giorni devono essere vero
        # riposo (nessun turno di alcun tipo). Non serve rilevare
        # esplicitamente "e' davvero la fine della serie" come per le
        # notti: il vincolo sopra (finestra scorrevole) garantisce gia'
        # che il giorno dopo una finestra completamente lavorata non
        # possa essere lavorato, quindi se [g-max+1..g] sono tutti
        # lavorati, g e' per costruzione l'ULTIMO della serie.
        #
        # Le variabili booleane "lavora quel giorno" servono perche'
        # OnlyEnforceIf accetta liste di letterali booleani, non
        # espressioni lineari arbitrarie come sum(x[...] for f in fasce).
        lavora = {}
        for g in giorni:
            lavora_g = model.NewBoolVar(f"lavora_{w}_{g}")
            model.Add(lavora_g == sum(x[(w, g, f)] for f in fasce))
            lavora[g] = lavora_g

        for g in giorni:
            finestra_serie = list(range(g - max_consec_giorni + 1, g + 1))
            if all(gg in giorni for gg in finestra_serie):
                letterali_serie_completa = [lavora[gg] for gg in finestra_serie]
                for offset in range(1, giorni_riposo_dopo_serie + 1):
                    giorno_riposo = g + offset
                    if giorno_riposo in giorni:
                        for f in fasce:
                            model.Add(x[(w, giorno_riposo, f)] == 0).OnlyEnforceIf(
                                letterali_serie_completa
                            )

        # Stesso principio a cavallo con il mese precedente: se i giorni
        # pregressi, sommati ai primi giorni del periodo, raggiungono il
        # massimo, serve lo stesso riposo.
        if consecutivi_pregressi_giorni >= max_consec_giorni:
            # Il massimo e' gia' raggiunto SOLO con la situazione
            # iniziale (fatto certo, non condizionato a nessuna
            # variabile): riposo incondizionato dal primo giorno del
            # periodo.
            for offset in range(giorni_riposo_dopo_serie):
                giorno_riposo = giorni[0] + offset
                if giorno_riposo in giorni:
                    for f in fasce:
                        model.Add(x[(w, giorno_riposo, f)] == 0)
        elif consecutivi_pregressi_giorni > 0:
            # Il massimo si raggiungerebbe SE i primi giorni del periodo
            # necessari a completare la serie fossero tutti lavorati
            # (condizionato: dipende da una decisione del motore).
            margine_confine = max_consec_giorni - consecutivi_pregressi_giorni
            finestra_confine = giorni[:margine_confine]
            if finestra_confine and all(gg in giorni for gg in finestra_confine):
                letterali_confine = [lavora[gg] for gg in finestra_confine]
                for offset in range(giorni_riposo_dopo_serie):
                    giorno_riposo = finestra_confine[-1] + 1 + offset
                    if giorno_riposo in giorni:
                        for f in fasce:
                            model.Add(x[(w, giorno_riposo, f)] == 0).OnlyEnforceIf(letterali_confine)

    # Vincolo HARD opzionale: vieta del tutto un Mattino il giorno dopo un
    # Pomeriggio (alternativa piu' rigida al termine soft
    # minimizza_pm_consecutivo — le due opzioni sono mutuamente esclusive,
    # l'interfaccia disabilita il soft quando questo e' attivo). A
    # differenza del riposo dopo notte/serie, qui basta un vincolo diretto
    # tra coppie di giorni adiacenti: non serve rilevare "fine serie",
    # visto che la regola riguarda solo la coppia (P oggi, M domani), non
    # una sequenza di lunghezza variabile.
    if dati.regole_contrattuali.vieta_pm_consecutivo and "M" in fasce and "P" in fasce:
        for w in lavoratori_ids:
            for idx in range(len(giorni) - 1):
                g, g_dopo = giorni[idx], giorni[idx + 1]
                if g_dopo == g + 1:  # solo giorni davvero consecutivi (nessun salto)
                    model.Add(x[(w, g, "P")] + x[(w, g_dopo, "M")] <= 1)

            # Confine con il mese precedente: se l'ultimo giorno di
            # situazione iniziale prima del periodo e' Pomeriggio, vieta
            # il Mattino sul primo giorno del periodo.
            ultimo_turno_precedente = next(
                (si.fascia for si in dati.stato_iniziale
                 if si.lavoratore_id == w and si.mese_precedente
                 and data_da_indice_mese_precedente(dati.periodo.anno, dati.periodo.mese, si.giorno)
                 == data_giorno_prima_periodo),
                None,
            )
            if ultimo_turno_precedente == "P" and giorni:
                model.Add(x[(w, giorni[0], "M")] == 0)

    # Vincolo HARD opzionale: scarto massimo (per fascia) tra il
    # lavoratore col conteggio piu' alto e quello col conteggio piu'
    # basso, sull'INTERO periodo — alternativa piu' rigida al termine
    # soft parametri_fairness.bilancia_fasce (mutuamente esclusivi,
    # l'interfaccia disattiva il soft quando questo e' attivo).
    #
    # NORMALIZZAZIONE PROPORZIONATA: i conteggi grezzi NON vengono
    # confrontati direttamente. Un lavoratore part-time con meta' delle
    # ore contrattuali (ore_settimanali_max) fa naturalmente meno turni
    # di un full-time — non e' uno squilibrio da correggere, e' la
    # conseguenza attesa del contratto. Confrontare i conteggi grezzi con
    # uno scarto assoluto uguale per tutti penalizzerebbe ingiustamente
    # chi ha un contratto piu' piccolo. Invece, ogni conteggio viene
    # riscalato rispetto al lavoratore con la capacita' piu' alta nel
    # gruppo considerato (stessa proxy di capacita' — ore_settimanali_max
    # — gia' usata dal termine soft bilancia_ore_settimanali): un
    # part-time a meta' ore che fa 3 notti risulta "equivalente" a 6
    # notti di un full-time. Lo scarto configurato si applica ai
    # conteggi COSI' NORMALIZZATI.
    if dati.parametri_fairness.bilancia_fasce_hard:
        SCALE_SCARTO = 1000
        for f in ("M", "P", "N"):
            if f not in fasce:
                continue
            scarto_max = getattr(dati.parametri_fairness, f"scarto_massimo_{f}")

            # Per la fascia N, esclude chi ha mai_notti=True (fissi a 0
            # per contratto: includerli renderebbe il vincolo violato
            # quasi sempre, dato che chiunque altro faccia anche solo una
            # notte supererebbe lo scarto). Esclude anche chi ha
            # ore_settimanali_max=0 (capacita' nulla, non normalizzabile:
            # caso degenere, comunque forzato a 0 turni dal vincolo ore).
            lavoratori_considerati = []
            for w in lavoratori_ids:
                lavoratore = next(l for l in dati.lavoratori if l.id == w)
                if f == "N" and lavoratore.vincoli_personali.mai_notti:
                    continue
                if lavoratore.ore_settimanali_max <= 0:
                    continue
                lavoratori_considerati.append(w)

            if len(lavoratori_considerati) < 2:
                continue  # niente da confrontare con 0 o 1 lavoratore

            capacita_riferimento_minuti = max(
                next(l for l in dati.lavoratori if l.id == w).ore_settimanali_max * 60
                for w in lavoratori_considerati
            )

            conteggi_normalizzati = []
            limiti_superiori = []
            for w in lavoratori_considerati:
                lavoratore = next(l for l in dati.lavoratori if l.id == w)
                capacita_w_minuti = lavoratore.ore_settimanali_max * 60
                fattore = (SCALE_SCARTO * capacita_riferimento_minuti) // capacita_w_minuti
                conteggio_w = sum(x[(w, g, f)] for g in giorni)
                limite_superiore = len(giorni) * fattore
                conteggio_norm = model.NewIntVar(0, limite_superiore, f"conteggio_norm_{f}_{w}")
                model.Add(conteggio_norm == conteggio_w * fattore)
                conteggi_normalizzati.append(conteggio_norm)
                limiti_superiori.append(limite_superiore)

            limite_superiore_globale = max(limiti_superiori)
            massimo_f = model.NewIntVar(0, limite_superiore_globale, f"massimo_{f}")
            minimo_f = model.NewIntVar(0, limite_superiore_globale, f"minimo_{f}")
            model.AddMaxEquality(massimo_f, conteggi_normalizzati)
            model.AddMinEquality(minimo_f, conteggi_normalizzati)
            model.Add(massimo_f - minimo_f <= scarto_max * SCALE_SCARTO)

    # Massimo ore settimanali da contratto: vedi blocco dedicato PIU' SOTTO,
    # dopo i vincoli admin e le richieste soft — serve sapere quali giorni
    # sono ferie (forzate o concesse tramite richiesta soft) per contarne
    # correttamente le ore virtuali nel monte ore settimanale.

    # ==================================================================
    # LIVELLO 2: vincoli admin (hard, imposti dal coordinatore)
    # ==================================================================
    for vadm in dati.vincoli_admin:
        if vadm.giorno not in giorni or vadm.lavoratore_id not in lavoratori_ids:
            continue

        if vadm.tipo in ("ferie", "riposo"):
            for f in fasce:
                model.Add(x[(vadm.lavoratore_id, vadm.giorno, f)] == 0)

            # Il riposo dopo una notte (o serie di notti) e' fisiologico,
            # non puo' essere "sostituito" da una ferie: se il giorno X e'
            # ferie forzata, nessuno dei giorni_riposo_dopo_notte giorni
            # precedenti puo' essere notte (altrimenti la ferie starebbe
            # coprendo un giorno di riposo obbligatorio invece di essere
            # un giorno di assenza vero e proprio). Vale solo per la
            # ferie, non per il riposo: il riposo E' esattamente cio' che
            # ci si aspetta dopo una notte, non va impedito li'.
            if vadm.tipo == "ferie" and "N" in fasce:
                for offset in range(1, giorni_riposo_dopo_notte + 1):
                    giorno_prima = vadm.giorno - offset
                    if giorno_prima in giorni:
                        model.Add(x[(vadm.lavoratore_id, giorno_prima, "N")] == 0)
                    # Nota: se il giorno e' fuori periodo (in
                    # stato_iniziale), un'eventuale notte gia' effettuata
                    # li' e' un fatto storico che non possiamo cambiare —
                    # un conflitto in tal caso andrebbe segnalato
                    # dall'utente, la validazione automatica di questo
                    # caso specifico non e' ancora implementata (vedi
                    # nota sopra sulla validazione preventiva rimandata).

        elif vadm.tipo == "turno" and vadm.fascia in fasce:
            model.Add(x[(vadm.lavoratore_id, vadm.giorno, vadm.fascia)] == 1)

    # ==================================================================
    # LIVELLO 3: richieste soft pesate (preferenze lavoratore)
    # ==================================================================
    termini_obiettivo = []
    miss_vars_per_richiesta = {}

    for req in dati.richieste_soft:
        if req.giorno not in giorni or req.lavoratore_id not in lavoratori_ids:
            continue

        peso = PESI_PRIORITA.get(req.priorita, 1)
        miss = model.NewBoolVar(f"miss_{req.id}")
        miss_vars_per_richiesta[req.id] = miss

        if req.tipo in ("ferie", "riposo"):
            # "miss" = 1 se il lavoratore lavora quel giorno (qualsiasi fascia).
            # Vale sia per ferie che per riposo: la differenza tra le due non
            # e' nel vincolo (in entrambi i casi niente turno quel giorno),
            # ma in come contano nel monte ore settimanale (vedi sotto).
            for f in fasce:
                model.Add(miss >= x[(req.lavoratore_id, req.giorno, f)])

            # Stessa logica del vincolo admin sopra: se la richiesta di
            # FERIE viene concessa (miss == 0), nessuno dei
            # giorni_riposo_dopo_notte giorni precedenti puo' essere
            # notte — il motore dovra' quindi valutare se concedere la
            # ferie vale la pena di ri-assegnare quelle notti a
            # qualcun altro, invece di ignorare il problema.
            if req.tipo == "ferie" and "N" in fasce:
                for offset in range(1, giorni_riposo_dopo_notte + 1):
                    giorno_prima = req.giorno - offset
                    if giorno_prima in giorni:
                        model.Add(x[(req.lavoratore_id, giorno_prima, "N")] == 0).OnlyEnforceIf(miss.Not())

        elif req.tipo == "turno" and req.fascia in fasce:
            # "miss" = 1 se NON gli viene assegnata la fascia richiesta
            model.Add(miss == 1 - x[(req.lavoratore_id, req.giorno, req.fascia)])

        else:
            continue

        termini_obiettivo.append(peso * miss)

    # ==================================================================
    # Massimo ore settimanali da contratto
    #
    # Una giornata di FERIE (forzata dall'admin o concessa tramite
    # richiesta soft) aggiunge ore "virtuali" al monte ore settimanale,
    # perche' e' comunque tempo retribuito nel rapporto di lavoro — a
    # differenza del RIPOSO, che non aggiunge nulla. Esempio: con un
    # contratto da 36h, 4 giorni lavorati (32h) + 1 ferie (8h virtuali)
    # = 40h > 36h, quindi NON ammissibile anche se il lavoratore ha
    # fisicamente lavorato solo 32 ore.
    # ==================================================================
    minuti_per_fascia = dati.regole_contrattuali.minuti_per_fascia
    minuti_ferie_giornaliere = dati.regole_contrattuali.minuti_ferie_giornaliere
    settimane = _raggruppa_per_settimana_iso(dati.periodo.anno, dati.periodo.mese, giorni)

    # Ore gia' maturate nel mese precedente per la stessa settimana ISO,
    # ESPRESSE IN MINUTI internamente (permette turni con minuti, es.
    # 7h30m, non solo ore intere): se la prima settimana del periodo e'
    # a cavallo con l'ultima settimana del mese precedente, i minuti di
    # stato_iniziale che cadono in quella settimana vanno sommati al
    # conteggio, altrimenti il vincolo settimanale ignorerebbe turni gia'
    # effettuati nella stessa settimana solare.
    minuti_pregressi_per_settimana: dict[str, dict[tuple[int, int], int]] = {}
    for si in dati.stato_iniziale:
        if not si.mese_precedente:
            continue
        data_si = data_da_indice_mese_precedente(dati.periodo.anno, dati.periodo.mese, si.giorno)
        anno_iso, settimana_iso, _ = data_si.isocalendar()
        chiave = (anno_iso, settimana_iso)
        minuti = minuti_per_fascia.get(si.fascia, 0)
        per_lavoratore = minuti_pregressi_per_settimana.setdefault(si.lavoratore_id, {})
        per_lavoratore[chiave] = per_lavoratore.get(chiave, 0) + minuti

    # Giorni di ferie FORZATA dall'admin: contano sempre i minuti virtuali,
    # e' un fatto certo (non condizionato a nessuna variabile del solver).
    ferie_forzata_per_settimana: dict[str, dict[tuple[int, int], int]] = {}
    for vadm in dati.vincoli_admin:
        if vadm.tipo != "ferie" or vadm.giorno not in giorni or vadm.lavoratore_id not in lavoratori_ids:
            continue
        data_v = data_da_indice_periodo(dati.periodo.anno, dati.periodo.mese, vadm.giorno)
        chiave = data_v.isocalendar()[:2]
        per_lavoratore = ferie_forzata_per_settimana.setdefault(vadm.lavoratore_id, {})
        per_lavoratore[chiave] = per_lavoratore.get(chiave, 0) + minuti_ferie_giornaliere

    # Richieste soft di ferie: contano i minuti virtuali SOLO se la
    # richiesta viene effettivamente concessa (variabile 'miss' della
    # richiesta == 0). Usiamo l'espressione (1 - miss) *
    # minuti_ferie_giornaliere, che vale minuti_ferie_giornaliere quando
    # miss=0 (concessa) e 0 quando miss=1 (rifiutata, nel qual caso il
    # lavoratore lavora davvero quel giorno e i suoi minuti reali sono
    # gia' contati a parte).
    ferie_soft_per_settimana: dict[str, dict[tuple[int, int], list]] = {}
    for req in dati.richieste_soft:
        if req.tipo != "ferie" or req.giorno not in giorni or req.lavoratore_id not in lavoratori_ids:
            continue
        miss = miss_vars_per_richiesta.get(req.id)
        if miss is None:
            continue
        data_r = data_da_indice_periodo(dati.periodo.anno, dati.periodo.mese, req.giorno)
        chiave = data_r.isocalendar()[:2]
        per_lavoratore = ferie_soft_per_settimana.setdefault(req.lavoratore_id, {})
        per_lavoratore.setdefault(chiave, []).append(miss)

    for w in lavoratori_ids:
        lavoratore = next(l for l in dati.lavoratori if l.id == w)
        # Nota: niente fallback su un default globale qui. I campi sono
        # obbligatori e specifici per lavoratore; un "or" con un default
        # globale tratterebbe erroneamente 0 (es. lavoratore con contratto
        # sospeso quel mese) come "non impostato", sostituendolo col
        # default in modo silenzioso e sbagliato.
        # ore_settimanali_min/max restano espressi in ORE INTERE sul
        # lavoratore (non serve precisione a livello di minuti per un
        # totale settimanale) — convertiti in minuti solo qui, al momento
        # del confronto con i minuti dei singoli turni.
        min_minuti = lavoratore.ore_settimanali_min * 60
        max_minuti = lavoratore.ore_settimanali_max * 60

        for chiave_settimana, giorni_settimana in settimane.items():
            minuti_gia_maturati = minuti_pregressi_per_settimana.get(w, {}).get(chiave_settimana, 0)
            minuti_ferie_forzata = ferie_forzata_per_settimana.get(w, {}).get(chiave_settimana, 0)
            miss_vars_ferie_soft = ferie_soft_per_settimana.get(w, {}).get(chiave_settimana, [])

            minuti_espr = sum(
                minuti_per_fascia.get(f, 0) * x[(w, g, f)]
                for g in giorni_settimana
                for f in fasce
            )
            minuti_ferie_soft_espr = sum(
                minuti_ferie_giornaliere * (1 - miss) for miss in miss_vars_ferie_soft
            )
            minuti_totali_settimana = (
                minuti_espr + minuti_gia_maturati + minuti_ferie_forzata + minuti_ferie_soft_espr
            )

            # Intervallo [minimo, massimo]: sotto il minimo non si puo'
            # andare (per garantirlo, il motore puo' assegnare surplus
            # oltre il fabbisogno minimo se necessario — vedi il vincolo
            # di copertura, che e' un ">=", non un "="), sopra il massimo
            # nemmeno. Se minimo == massimo, questo equivale a un vincolo
            # di uguaglianza esatta (comportamento "a valore fisso").
            #
            # NOTA sulle settimane PARZIALI (meno di 7 giorni controllabili
            # nel periodo — tipicamente solo la prima, se il mese non
            # inizia di lunedi'): il minimo NON viene proporzionato (a
            # differenza di una versione precedente di questo vincolo).
            # La situazione iniziale e' pensata per essere compilata
            # sempre con i turni realmente effettuati nei giorni
            # immediatamente precedenti al periodo — con quei dati veri,
            # i minuti gia' maturati (minuti_gia_maturati sopra) si
            # sommano naturalmente al totale della settimana, rendendo il
            # minimo raggiungibile senza bisogno di ridurlo
            # artificialmente. Una situazione iniziale vuota o incompleta
            # puo' quindi rendere il problema infeasible per la prima
            # settimana — e' un segnale corretto che manca l'informazione,
            # non un bug da mascherare abbassando il vincolo.
            model.Add(minuti_totali_settimana <= max_minuti)
            model.Add(minuti_totali_settimana >= min_minuti)

    # ==================================================================
    # LIVELLO 4: fairness (soft, priorita' piu' bassa)
    # ==================================================================
    n_giorni = len(giorni)

    # Variabili condivise tra i termini "proporzionali" sotto
    # (bilancia_copertura_giornaliera e bilancia_proporzione_giornaliera):
    # definite qui, fuori dai singoli blocchi "if", cosi' sono disponibili
    # indipendentemente da quale dei due toggle sia effettivamente attivo
    # (evita un errore se uno e' disattivato ma l'altro no).
    minimo_per_giorno_fascia = {
        (fab.giorno, fab.fascia): fab.minimo for fab in dati.fabbisogno
    }
    n_lavoratori = len(lavoratori_ids)
    SCALE = 100
    FATTORE_RINORMALIZZAZIONE = 10  # 1 unita' finale = 10 punti percentuali di scarto

    # Vincolo HARD opzionale: scarto massimo (per fascia, in punti
    # percentuali) tra il TASSO di surplus di copertura (surplus/minimo,
    # stessa proporzione del soft bilancia_copertura_giornaliera sotto —
    # non il conteggio grezzo) del giorno peggiore e quello migliore.
    # Fisicamente qui invece che in LIVELLO 1 perche' riusa le variabili
    # condivise (minimo_per_giorno_fascia, SCALE) appena definite sopra —
    # l'ordine dei model.Add() non ha importanza per CP-SAT, e' solo
    # organizzazione del codice. MUTUAMENTE ESCLUSIVO con
    # bilancia_copertura_giornaliera (l'interfaccia disattiva il soft
    # quando questo e' attivo).
    if dati.parametri_fairness.bilancia_copertura_giornaliera_hard:
        for f in fasce:
            scarto_max = getattr(dati.parametri_fairness, f"scarto_massimo_copertura_{f}")
            tassi_surplus_f = []
            for g in giorni:
                minimo_gf = minimo_per_giorno_fascia.get((g, f), 0)
                if minimo_gf <= 0:
                    continue  # escluso: tasso surplus/0 non definito, come nel soft
                count_g = model.NewIntVar(0, n_lavoratori, f"copertura_hard_{f}_{g}")
                model.Add(count_g == sum(x[(w, g, f)] for w in lavoratori_ids))
                surplus_g = model.NewIntVar(0, n_lavoratori, f"surplus_hard_{f}_{g}")
                model.Add(surplus_g == count_g - minimo_gf)
                tasso_g = model.NewIntVar(0, n_lavoratori * SCALE, f"tasso_hard_{f}_{g}")
                model.AddDivisionEquality(tasso_g, surplus_g * SCALE, minimo_gf)
                tassi_surplus_f.append(tasso_g)

            if len(tassi_surplus_f) < 2:
                continue  # niente da confrontare con 0 o 1 giorno valido

            max_tasso_f = model.NewIntVar(0, n_lavoratori * SCALE, f"max_tasso_hard_{f}")
            min_tasso_f = model.NewIntVar(0, n_lavoratori * SCALE, f"min_tasso_hard_{f}")
            model.AddMaxEquality(max_tasso_f, tassi_surplus_f)
            model.AddMinEquality(min_tasso_f, tassi_surplus_f)
            model.Add(max_tasso_f - min_tasso_f <= scarto_max)

    if dati.parametri_fairness.bilancia_fasce:
        # RISTRUTTURATO: prima confrontava solo il lavoratore col piu' alto
        # conteggio e quello col piu' basso (max-min), un singolo numero che
        # non cresce con quanti lavoratori sono fuori media ne' con quanto
        # lo sono. Con volumi di surplus piccoli andava bene, ma con un
        # volume grande (es. generato da un minimo ore settimanali alto)
        # questo termine restava "piccolo e fisso" mentre altri termini
        # proporzionali (che SOMMANO su giorni/fasce) crescevano con la
        # scala del problema, finendo per annegare completamente il
        # segnale di bilancia_fasce nell'obiettivo complessivo.
        #
        # Ora sommiamo lo scarto di OGNI lavoratore dalla MEDIA del gruppo
        # (invece di confrontare solo il peggiore con il migliore): cosi'
        # il termine cresce naturalmente con quanti lavoratori sono fuori
        # media, restando comparabile in scala agli altri termini
        # proporzionali anche quando il volume di surplus aumenta.
        # Scartata l'alternativa "confronta ogni coppia di lavoratori"
        # (O(n^2): 190 coppie per fascia con 20 lavoratori) a favore di
        # "confronta ognuno con la media" (O(n): 20 confronti per fascia),
        # molto piu' efficiente e con lo stesso effetto pratico.
        #
        # NORMALIZZAZIONE PROPORZIONATA (stessa logica della versione
        # HARD sopra, bilancia_fasce_hard — vedi i commenti li' per il
        # ragionamento completo): i conteggi grezzi non sono confrontati
        # direttamente, altrimenti un part-time con meta' delle ore
        # contrattuali verrebbe "spinto" verso lo stesso conteggio di un
        # full-time, anche se soft. Ogni conteggio viene riscalato
        # rispetto al lavoratore con la capacita' (ore_settimanali_max)
        # piu' alta nel gruppo considerato. Per la fascia N, i lavoratori
        # con vincoli_personali.mai_notti=True sono esclusi dal confronto
        # (fissi a 0 per contratto: il loro scarto dalla media non
        # potrebbe mai essere corretto, e la loro presenza abbasserebbe
        # artificialmente la media, generando una pressione ingiustificata
        # sugli altri).
        SCALE_BILANCIA_FASCE = 1000
        for f in fasce:
            lavoratori_considerati = []
            for w in lavoratori_ids:
                lavoratore = next(l for l in dati.lavoratori if l.id == w)
                if f == "N" and lavoratore.vincoli_personali.mai_notti:
                    continue
                if lavoratore.ore_settimanali_max <= 0:
                    continue
                lavoratori_considerati.append(w)

            if len(lavoratori_considerati) < 2:
                continue  # niente da bilanciare con 0 o 1 lavoratore

            capacita_riferimento_minuti = max(
                next(l for l in dati.lavoratori if l.id == w).ore_settimanali_max * 60
                for w in lavoratori_considerati
            )

            conteggi_normalizzati = []
            limiti_superiori = []
            for w in lavoratori_considerati:
                lavoratore = next(l for l in dati.lavoratori if l.id == w)
                capacita_w_minuti = lavoratore.ore_settimanali_max * 60
                fattore = (SCALE_BILANCIA_FASCE * capacita_riferimento_minuti) // capacita_w_minuti
                c = model.NewIntVar(0, n_giorni, f"count_{w}_{f}")
                model.Add(c == sum(x[(w, g, f)] for g in giorni))
                limite_superiore = n_giorni * fattore
                c_norm = model.NewIntVar(0, limite_superiore, f"count_norm_{w}_{f}")
                model.Add(c_norm == c * fattore)
                conteggi_normalizzati.append(c_norm)
                limiti_superiori.append(limite_superiore)

            n_considerati = len(lavoratori_considerati)
            limite_superiore_gruppo = max(limiti_superiori)
            limite_superiore_totale = n_considerati * limite_superiore_gruppo
            totale_f = model.NewIntVar(0, limite_superiore_totale, f"totale_conteggi_{f}")
            model.Add(totale_f == sum(conteggi_normalizzati))
            media_f = model.NewIntVar(0, limite_superiore_gruppo, f"media_conteggi_{f}")
            model.AddDivisionEquality(media_f, totale_f, n_considerati)

            for w, c_norm in zip(lavoratori_considerati, conteggi_normalizzati):
                # NOTA sul bound: uso limite_superiore_gruppo (il massimo
                # dell'intero gruppo), non il limite del singolo
                # lavoratore — lo scarto dalla media puo' avvicinarsi al
                # range completo del gruppo se le capacita' contrattuali
                # sono molto diverse tra loro (es. c_norm=0 per un
                # lavoratore e media_f vicina al massimo del gruppo).
                scarto_media = model.NewIntVar(0, limite_superiore_gruppo, f"scarto_media_{w}_{f}")
                model.Add(scarto_media >= c_norm - media_f)
                model.Add(scarto_media >= media_f - c_norm)
                # Diviso per SCALE_BILANCIA_FASCE (con AddDivisionEquality,
                # coerente con come il resto del motore gestisce le
                # divisioni intere) per riportare il peso nello stesso
                # ordine di grandezza di prima della normalizzazione — la
                # normalizzazione non deve alterare l'intensita' relativa
                # di questo termine rispetto agli altri nell'obiettivo.
                # Stesso motivo del bound sopra: uso limite_superiore_gruppo,
                # non il limite del singolo lavoratore.
                scarto_media_scala_originale = model.NewIntVar(
                    0, limite_superiore_gruppo // SCALE_BILANCIA_FASCE, f"scarto_media_orig_{w}_{f}"
                )
                model.AddDivisionEquality(scarto_media_scala_originale, scarto_media, SCALE_BILANCIA_FASCE)
                termini_obiettivo.append(
                    dati.parametri_fairness.peso_bilancia_fasce * scarto_media_scala_originale
                )

    if dati.parametri_fairness.bilancia_giorni_settimana:
        # Stessa ristrutturazione di bilancia_fasce sopra, stesso motivo:
        # somma degli scarti dalla media invece di max-min, per scalare
        # correttamente col volume del problema.
        conteggi_giorni = []
        for w in lavoratori_ids:
            c = model.NewIntVar(0, n_giorni, f"giorni_lavorati_{w}")
            model.Add(c == sum(x[(w, g, f)] for g in giorni for f in fasce))
            conteggi_giorni.append(c)

        totale_giorni = model.NewIntVar(0, n_lavoratori * n_giorni, "totale_giorni_lavorati")
        model.Add(totale_giorni == sum(conteggi_giorni))
        media_giorni = model.NewIntVar(0, n_giorni, "media_giorni_lavorati")
        model.AddDivisionEquality(media_giorni, totale_giorni, n_lavoratori)

        for w, c in zip(lavoratori_ids, conteggi_giorni):
            scarto_media_giorni = model.NewIntVar(0, n_giorni, f"scarto_media_giorni_{w}")
            model.Add(scarto_media_giorni >= c - media_giorni)
            model.Add(scarto_media_giorni >= media_giorni - c)
            termini_obiettivo.append(
                dati.parametri_fairness.peso_bilancia_giorni_settimana * scarto_media_giorni
            )

    if dati.parametri_fairness.bilancia_ore_settimanali:
        # bilancia_giorni_settimana bilancia il TOTALE sull'intero periodo:
        # non impedisce che una singola settimana sia molto sbilanciata tra
        # le persone (es. qualcuno con 8 ore, qualcun altro con 32) anche
        # se sul periodo intero i totali si pareggiano. Qui bilanciamo le
        # ore settimana per settimana, cosi' lo squilibrio non puo'
        # nascondersi dietro una media complessiva.
        #
        # NON confrontiamo pero' le ore grezze: un lavoratore che ha gia'
        # maturato ore in stato_iniziale nella settimana a cavallo ha una
        # capacita' RESIDUA piu' bassa quella settimana in modo del tutto
        # legittimo (non e' un problema di contratto, e' un vincolo fisico
        # gia' successo). Se confrontassimo le ore grezze, un peso alto
        # spingerebbe il motore a "trascinare giu'" anche gli altri
        # lavoratori pur di ridurre lo scarto — esattamente l'effetto
        # opposto a quello desiderato. Confrontiamo invece il TASSO DI
        # UTILIZZO della capacita' residua di quella settimana (ore nuove
        # assegnate / capacita' residua), cosi' un lavoratore gia' quasi al
        # massimo della sua capacita' residua (es. 24 ore su 28 disponibili
        # = 86%) risulta gia' "equo" rispetto a un altro pieno al 100% su
        # 36, senza bisogno di penalizzare nessuno.
        SCALE = 100

        for chiave_settimana, giorni_settimana in settimane.items():
            tassi_settimana = []
            for w in lavoratori_ids:
                lavoratore = next(l for l in dati.lavoratori if l.id == w)
                max_minuti_w = lavoratore.ore_settimanali_max * 60
                minuti_gia_maturati = minuti_pregressi_per_settimana.get(w, {}).get(chiave_settimana, 0)
                capacita_residua_minuti = max(max_minuti_w - minuti_gia_maturati, 0)

                minuti_ferie_forzata = ferie_forzata_per_settimana.get(w, {}).get(chiave_settimana, 0)
                miss_vars_ferie_soft = ferie_soft_per_settimana.get(w, {}).get(chiave_settimana, [])
                minuti_ferie_soft_espr = sum(
                    minuti_ferie_giornaliere * (1 - miss) for miss in miss_vars_ferie_soft
                )

                # I minuti "nuovi" includono anche i minuti virtuali di
                # ferie: un lavoratore in ferie ha comunque "consumato" la
                # sua capacita' quella settimana, non e' sottoutilizzato
                # solo perche' non ha fisicamente lavorato quel giorno.
                minuti_nuovi_w = model.NewIntVar(0, max_minuti_w, f"minuti_nuovi_{chiave_settimana}_{w}")
                model.Add(
                    minuti_nuovi_w
                    == sum(minuti_per_fascia.get(f, 0) * x[(w, g, f)] for g in giorni_settimana for f in fasce)
                    + minuti_ferie_forzata + minuti_ferie_soft_espr
                )

                if capacita_residua_minuti > 0:
                    tasso_w = model.NewIntVar(0, SCALE, f"tasso_ore_{chiave_settimana}_{w}")
                    model.AddDivisionEquality(tasso_w, minuti_nuovi_w * SCALE, capacita_residua_minuti)
                    tassi_settimana.append(tasso_w)
                # Se capacita_residua_minuti == 0 (rarissimo: ha gia'
                # esaurito il monte ore solo con stato_iniziale), il
                # vincolo hard altrove gli impedisce comunque nuovi turni
                # quella settimana; lo escludiamo dal confronto
                # proporzionale (divisione per zero).

            if len(tassi_settimana) >= 2:
                max_t = model.NewIntVar(0, SCALE, f"max_tasso_ore_{chiave_settimana}")
                min_t = model.NewIntVar(0, SCALE, f"min_tasso_ore_{chiave_settimana}")
                model.AddMaxEquality(max_t, tassi_settimana)
                model.AddMinEquality(min_t, tassi_settimana)

                scarto_tasso_ore = model.NewIntVar(0, SCALE, f"scarto_tasso_ore_{chiave_settimana}")
                model.Add(scarto_tasso_ore == max_t - min_t)
                termini_obiettivo.append(dati.parametri_fairness.peso_bilancia_ore_settimanali * scarto_tasso_ore)

    if dati.parametri_fairness.bilancia_copertura_giornaliera:
        # Il vincolo di copertura minima e' "almeno N persone", non
        # "esattamente N": il motore puo' quindi assegnare surplus in
        # alcuni giorni/fasce per soddisfare altri vincoli/obiettivi (es.
        # ore settimanali). Senza un termine dedicato, questo surplus puo'
        # concentrarsi in modo poco realistico.
        #
        # Non basta bilanciare ogni fascia per conto suo: se M e P hanno
        # lo stesso fabbisogno (es. 3 e 3) ma il surplus finisce quasi
        # tutto su P, e' comunque uno squilibrio, anche se preso da solo
        # il "surplus di P nei vari giorni" risultasse ben distribuito.
        # Serve una misura PROPORZIONALE al fabbisogno (surplus/minimo),
        # confrontabile su un'unica scala tra fasce e giorni diversi anche
        # quando il fabbisogno non e' lo stesso ovunque.
        #
        # CP-SAT non supporta divisioni tra variabili in modo diretto per
        # numeri reali: usiamo AddDivisionEquality con un fattore di scala
        # (SCALE) per mantenere precisione lavorando solo con interi, poi
        # normalizziamo lo scarto finale allo stesso ordine di grandezza
        # degli altri termini di fairness prima di pesarlo. ATTENZIONE:
        # SCALE (100) serve per la precisione interna del rapporto
        # surplus/minimo (es. 1/3 diventa 33 invece di essere troncato a
        # 0), ma dividere di nuovo per lo STESSO SCALE=100 alla fine
        # schiaccia quasi tutto a 0-2 (bug corretto qui sotto: usiamo un
        # fattore di rinormalizzazione molto piu' piccolo, che preserva il
        # segnale invece di annullarlo quasi del tutto). Le variabili
        # condivise (minimo_per_giorno_fascia, n_lavoratori, SCALE,
        # FATTORE_RINORMALIZZAZIONE) sono definite all'inizio del Livello 4.

        tassi_surplus = []
        for f in fasce:
            for g in giorni:
                count_g = model.NewIntVar(0, n_lavoratori, f"copertura_{f}_{g}")
                model.Add(count_g == sum(x[(w, g, f)] for w in lavoratori_ids))

                minimo_gf = minimo_per_giorno_fascia.get((g, f), 0)
                surplus_g = model.NewIntVar(0, n_lavoratori, f"surplus_{f}_{g}")
                model.Add(surplus_g == count_g - minimo_gf)

                if minimo_gf > 0:
                    # tasso = (surplus / minimo) * SCALE, come intero
                    tasso_g = model.NewIntVar(0, n_lavoratori * SCALE, f"tasso_{f}_{g}")
                    model.AddDivisionEquality(tasso_g, surplus_g * SCALE, minimo_gf)
                    tassi_surplus.append(tasso_g)
                # Se il fabbisogno e' 0 quel giorno/fascia, la proporzione
                # non e' definita (divisione per zero): quel surplus resta
                # comunque vincolato a essere >= 0 dalla definizione sopra,
                # ma non entra nel bilanciamento proporzionale.

        if len(tassi_surplus) >= 2:
            max_t = model.NewIntVar(0, n_lavoratori * SCALE, "max_tasso_surplus")
            min_t = model.NewIntVar(0, n_lavoratori * SCALE, "min_tasso_surplus")
            model.AddMaxEquality(max_t, tassi_surplus)
            model.AddMinEquality(min_t, tassi_surplus)

            scarto_tasso = model.NewIntVar(0, n_lavoratori * SCALE, "scarto_tasso_surplus")
            model.Add(scarto_tasso == max_t - min_t)

            limite_normalizzato = (n_lavoratori * SCALE) // FATTORE_RINORMALIZZAZIONE
            scarto_tasso_normalizzato = model.NewIntVar(0, limite_normalizzato, "scarto_tasso_normalizzato")
            model.AddDivisionEquality(scarto_tasso_normalizzato, scarto_tasso, FATTORE_RINORMALIZZAZIONE)
            termini_obiettivo.append(
                dati.parametri_fairness.peso_bilancia_copertura_giornaliera * scarto_tasso_normalizzato
            )

    if dati.parametri_fairness.bilancia_proporzione_giornaliera:
        # bilancia_copertura_giornaliera (sopra) minimizza lo scarto
        # PEGGIORE in tutto il mucchio di (giorno,fascia): puo' benissimo
        # succedere che il "peggior scarto assoluto" sia tra due giorni
        # completamente diversi, lasciando che TANTI singoli giorni
        # abbiano comunque M/P/N sbilanciati tra loro senza che questo
        # emerga come il caso peggiore in assoluto (osservato in pratica:
        # M e P bilanciati sul totale mensile, ma un giorno con 8M/5P e
        # un altro con 4M/9P — nessuno dei due e' "il peggiore assoluto"
        # del mese, quindi il vincolo sopra non li corregge).
        #
        # Qui invece confrontiamo le fasce PRESENTI OGNI SINGOLO GIORNO
        # (proporzionalmente al loro fabbisogno di quel giorno, cosi'
        # resta corretto anche se M/P/N hanno fabbisogni diversi tra loro
        # o le ore per fascia cambiano).
        #
        # ATTENZIONE (bug corretto qui): la prima versione SOMMAVA lo
        # scarto di ogni giorno, rendendo il contributo di questo termine
        # proporzionale al numero di giorni del periodo — su un mese di
        # 33 giorni, la somma finiva per pesare fino a ~7 volte piu' del
        # massimo possibile di bilancia_fasce, schiacciando l'equita' tra
        # lavoratori (osservato: un lavoratore con 15 turni Mattina,
        # un altro con 2). Usiamo la MEDIA invece della somma: cosi' la
        # magnitudo resta comparabile agli altri termini indipendentemente
        # da quanti giorni ha il periodo.
        scarti_giorni = []
        for g in giorni:
            tassi_giorno = []
            for f in fasce:
                minimo_gf = minimo_per_giorno_fascia.get((g, f), 0)
                if minimo_gf <= 0:
                    continue
                count_gf = model.NewIntVar(0, n_lavoratori, f"copertura_giorno_{f}_{g}")
                model.Add(count_gf == sum(x[(w, g, f)] for w in lavoratori_ids))
                surplus_gf = model.NewIntVar(0, n_lavoratori, f"surplus_giorno_{f}_{g}")
                model.Add(surplus_gf == count_gf - minimo_gf)

                tasso_gf = model.NewIntVar(0, n_lavoratori * SCALE, f"tasso_giorno_{f}_{g}")
                model.AddDivisionEquality(tasso_gf, surplus_gf * SCALE, minimo_gf)
                tassi_giorno.append(tasso_gf)

            if len(tassi_giorno) >= 2:
                max_g = model.NewIntVar(0, n_lavoratori * SCALE, f"max_tasso_giorno_{g}")
                min_g = model.NewIntVar(0, n_lavoratori * SCALE, f"min_tasso_giorno_{g}")
                model.AddMaxEquality(max_g, tassi_giorno)
                model.AddMinEquality(min_g, tassi_giorno)

                scarto_g = model.NewIntVar(0, n_lavoratori * SCALE, f"scarto_tasso_giorno_{g}")
                model.Add(scarto_g == max_g - min_g)
                scarti_giorni.append(scarto_g)

        if scarti_giorni:
            somma_scarti = model.NewIntVar(
                0, len(scarti_giorni) * n_lavoratori * SCALE, "somma_scarti_giorni"
            )
            model.Add(somma_scarti == sum(scarti_giorni))

            media_scarto = model.NewIntVar(0, n_lavoratori * SCALE, "media_scarto_giorni")
            model.AddDivisionEquality(media_scarto, somma_scarti, len(scarti_giorni))

            limite_norm = (n_lavoratori * SCALE) // FATTORE_RINORMALIZZAZIONE
            media_scarto_norm = model.NewIntVar(0, limite_norm, "media_scarto_giorni_norm")
            model.AddDivisionEquality(media_scarto_norm, media_scarto, FATTORE_RINORMALIZZAZIONE)
            termini_obiettivo.append(
                dati.parametri_fairness.peso_bilancia_proporzione_giornaliera * media_scarto_norm
            )

    if dati.parametri_fairness.minimizza_pm_consecutivo and "P" in fasce and "M" in fasce:
        # Una sequenza Pomeriggio (giorno G) -> Mattino (giorno G+1) lascia
        # un riposo molto piu' corto tra i due turni (es. P finisce sera
        # tardi, M inizia presto la mattina dopo) rispetto a Mattino ->
        # Pomeriggio (M finisce a meta' giornata, P il giorno dopo inizia
        # solo nel pomeriggio: quasi un giorno intero di margine). Non e'
        # un vincolo hard: e' spesso inevitabile per esigenze di copertura,
        # quindi lo minimizziamo dove possibile invece di vietarlo, cosi'
        # M->P viene implicitamente premiato rispetto a P->M.
        #
        # "AND soft": pm_var deve valere 1 quando sia P(giorno) che
        # M(giorno+1) sono assegnati. Basta il vincolo di minimo (>=),
        # senza bisogno del vincolo di massimo, perche' la minimizzazione
        # nell'obiettivo spinge gia' pm_var a 0 in tutti gli altri casi.
        for w in lavoratori_ids:
            for g in giorni:
                giorno_dopo = g + 1
                if giorno_dopo not in giorni:
                    continue
                pm_var = model.NewBoolVar(f"pm_consecutivo_{w}_{g}")
                model.Add(pm_var >= x[(w, g, "P")] + x[(w, giorno_dopo, "M")] - 1)
                termini_obiettivo.append(dati.parametri_fairness.peso_minimizza_pm_consecutivo * pm_var)

    # ------------------------------------------------------------------
    # Obiettivo finale: minimizza la somma pesata di tutte le penalita' soft
    # ------------------------------------------------------------------
    if termini_obiettivo:
        model.Minimize(sum(termini_obiettivo))

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = tempo_max_secondi
    status = solver.Solve(model)

    if status == cp_model.INFEASIBLE:
        # Il solver ha DIMOSTRATO che non esiste alcuna soluzione: i
        # vincoli sono davvero incompatibili tra loro.
        return OutputTurnazione(stato="infeasible", tempo_impiegato_secondi=solver.WallTime())

    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        # UNKNOWN (o MODEL_INVALID): il tempo e' scaduto PRIMA che il
        # motore trovasse una soluzione O dimostrasse l'impossibilita'.
        # Non e' la stessa cosa di "infeasible" — il problema potrebbe
        # benissimo avere soluzione, il motore semplicemente non ha fatto
        # in tempo a trovarla. Distinguerlo evita di dire all'utente "i
        # vincoli sono incompatibili" quando in realta' serve solo piu'
        # tempo di calcolo.
        return OutputTurnazione(stato="tempo_scaduto", tempo_impiegato_secondi=solver.WallTime())

    assegnazioni = []
    for w in lavoratori_ids:
        for g in giorni:
            for f in fasce:
                if solver.Value(x[(w, g, f)]) == 1:
                    assegnazioni.append(Assegnazione(lavoratore_id=w, giorno=g, fascia=f))

    richieste_non_soddisfatte = []
    for req in dati.richieste_soft:
        miss = miss_vars_per_richiesta.get(req.id)
        if miss is not None and solver.Value(miss) == 1:
            richieste_non_soddisfatte.append(
                RichiestaNonSoddisfatta(richiesta_id=req.id, motivo="non_soddisfatta_dal_solver")
            )

    # Metriche di fairness calcolate a posteriori sul risultato, utili
    # per un riscontro immediato all'utente (es. in Streamlit)
    metriche_fairness = {}
    for f in fasce:
        conteggi = {
            w: sum(1 for a in assegnazioni if a.lavoratore_id == w and a.fascia == f)
            for w in lavoratori_ids
        }
        metriche_fairness[f"turni_{f}_per_lavoratore"] = conteggi

    giorni_lavorati = {
        w: sum(1 for a in assegnazioni if a.lavoratore_id == w)
        for w in lavoratori_ids
    }
    metriche_fairness["giorni_lavorati_per_lavoratore"] = giorni_lavorati

    stato = "feasible" if not richieste_non_soddisfatte else "feasible_con_declassamenti"

    return OutputTurnazione(
        stato=stato,
        assegnazioni=assegnazioni,
        richieste_non_soddisfatte=richieste_non_soddisfatte,
        metriche_fairness=metriche_fairness,
        # OPTIMAL = il motore ha DIMOSTRATO che non esiste soluzione
        # migliore: aumentare il tempo massimo non cambierebbe nulla.
        # FEASIBLE = ha trovato una soluzione valida ma il tempo e'
        # scaduto prima di dimostrare che sia la migliore possibile:
        # potrebbe esistere di meglio, aumentare il tempo puo' aiutare.
        ottimalita_provata=(status == cp_model.OPTIMAL),
        tempo_impiegato_secondi=solver.WallTime(),
    )


if __name__ == "__main__":
    # Esecuzione rapida manuale: python -m engine.solver
    from engine.sample_data import get_sample_input

    risultato = genera_turni(get_sample_input())
    print("Stato:", risultato.stato)
    print(f"Ottimalita' provata: {risultato.ottimalita_provata}")
    print(f"Tempo impiegato: {risultato.tempo_impiegato_secondi:.1f}s")
    for a in sorted(risultato.assegnazioni, key=lambda a: (a.giorno, a.lavoratore_id)):
        print(f"  giorno {a.giorno:>2}  {a.lavoratore_id}  ->  {a.fascia}")

    if risultato.richieste_non_soddisfatte:
        print("\nRichieste non soddisfatte:")
        for r in risultato.richieste_non_soddisfatte:
            print(f"  {r.richiesta_id}: {r.motivo}")

    print("\nMetriche fairness:")
    for chiave, valore in risultato.metriche_fairness.items():
        print(f"  {chiave}: {valore}")
