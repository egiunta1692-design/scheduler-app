"""
Motore di generazione turni - STEP 6 (completo per i vincoli principali).

Livelli implementati, in ordine di priorita' (dal piu' al meno vincolante):

  1. VINCOLI STRUTTURALI DI SISTEMA (sempre hard):
     - un lavoratore fa al massimo una fascia al giorno
     - copertura minima per giorno/fascia (fabbisogno)
     - riposo obbligatorio dopo un turno notturno
     - vincolo personale "mai notti" (lavoratore.vincoli_personali.mai_notti)
     - massimo notti consecutive (con override personale possibile)
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
     massimo ore settimanali da contratto, sempre per singolo lavoratore
     (lavoratore.ore_settimanali_contratto, nessun fallback su un default
     globale), calcolato dopo i livelli 2 e 3 perche' le giornate di FERIE
     (forzate dall'admin o concesse tramite richiesta soft accolta)
     aggiungono ore "virtuali" al monte ore (regole_contrattuali.
     ore_ferie_giornaliere, e' comunque tempo retribuito), mentre il
     RIPOSO non aggiunge nulla. Tutto tiene conto di stato_iniziale per i
     casi a cavallo di mese, incluse le ore gia' maturate nella stessa
     settimana ISO se la settimana e' a cavallo con il mese precedente

  4. FAIRNESS (soft, priorita' piu' bassa):
     minimizza lo scarto (max - min) tra lavoratori sul numero di turni
     per fascia e sul numero di giorni lavorati totali; minimizza inoltre
     lo scarto (max - min) del TASSO DI UTILIZZO della capacita' oraria
     residua, SETTIMANA PER SETTIMANA (non solo sul totale del periodo):
     confrontiamo il tasso (ore nuove assegnate / capacita' residua quella
     settimana), non le ore grezze, perche' un lavoratore che ha gia'
     maturato ore in stato_iniziale ha una capacita' residua legittimamente
     piu' bassa quella settimana — confrontare le ore grezze spingerebbe
     un peso alto a "trascinare giu'" anche gli altri lavoratori pur di
     ridurre lo scarto, l'esatto opposto dell'effetto voluto; minimizza
     infine lo scarto (max - min) del TASSO di surplus di copertura
     (surplus / fabbisogno minimo, non il surplus grezzo), confrontato su
     un'unica scala tra tutte le fasce e i giorni insieme: cosi' un
     eventuale surplus si distribuisce in proporzione al fabbisogno invece
     di concentrarsi su una fascia o un giorno specifico, anche quando il
     fabbisogno non e' uguale ovunque

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

    # Riposo obbligatorio dopo un turno notturno
    vietato_dopo_notte = dati.regole_contrattuali.vietato_dopo_notte
    for w in lavoratori_ids:
        for g in giorni:
            giorno_dopo = g + 1
            if giorno_dopo in giorni and "N" in fasce:
                for f in vietato_dopo_notte:
                    if f in fasce:
                        model.Add(x[(w, giorno_dopo, f)] == 0).OnlyEnforceIf(x[(w, g, "N")])

    data_inizio_periodo = datetime.date(dati.periodo.anno, dati.periodo.mese, dati.periodo.giorno_inizio)
    data_giorno_prima_periodo = data_inizio_periodo - datetime.timedelta(days=1)

    for si in dati.stato_iniziale:
        if si.mese_precedente and si.fascia == "N":
            data_si = data_da_indice_mese_precedente(dati.periodo.anno, dati.periodo.mese, si.giorno)
            if data_si == data_giorno_prima_periodo:
                for f in vietato_dopo_notte:
                    if f in fasce and si.lavoratore_id in lavoratori_ids:
                        model.Add(x[(si.lavoratore_id, dati.periodo.giorno_inizio, f)] == 0)

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

            # Il giorno di riposo dopo una notte e' fisiologico, non puo'
            # essere "sostituito" da una ferie: se il giorno X e' ferie
            # forzata, il giorno X-1 non puo' essere notte (altrimenti la
            # ferie starebbe coprendo il riposo obbligatorio invece di
            # essere un giorno di assenza vero e proprio). Vale solo per
            # la ferie, non per il riposo: il riposo E' esattamente cio'
            # che ci si aspetta dopo una notte, non va impedito li'.
            if vadm.tipo == "ferie" and "N" in fasce:
                giorno_prima = vadm.giorno - 1
                if giorno_prima in giorni:
                    model.Add(x[(vadm.lavoratore_id, giorno_prima, "N")] == 0)
                # Nota: se il giorno prima e' fuori periodo (in
                # stato_iniziale), un'eventuale notte gia' effettuata li'
                # e' un fatto storico che non possiamo cambiare — un
                # conflitto in tal caso andrebbe segnalato dall'utente,
                # la validazione automatica di questo caso specifico non
                # e' ancora implementata (vedi nota sopra sulla
                # validazione preventiva rimandata).

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
            # FERIE viene concessa (miss == 0), il giorno prima non puo'
            # essere notte — il motore dovra' quindi valutare se concedere
            # la ferie vale la pena di ri-assegnare quella notte a
            # qualcun altro, invece di ignorare il problema.
            if req.tipo == "ferie" and "N" in fasce:
                giorno_prima = req.giorno - 1
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
    ore_per_fascia = dati.regole_contrattuali.ore_per_fascia
    ore_ferie_giornaliere = dati.regole_contrattuali.ore_ferie_giornaliere
    settimane = _raggruppa_per_settimana_iso(dati.periodo.anno, dati.periodo.mese, giorni)

    # Ore gia' maturate nel mese precedente per la stessa settimana ISO:
    # se la prima settimana del periodo e' a cavallo con l'ultima settimana
    # del mese precedente, le ore di stato_iniziale che cadono in quella
    # settimana vanno sommate al conteggio, altrimenti il vincolo settimanale
    # ignorerebbe turni gia' effettuati nella stessa settimana solare.
    ore_pregresse_per_settimana: dict[str, dict[tuple[int, int], int]] = {}
    for si in dati.stato_iniziale:
        if not si.mese_precedente:
            continue
        data_si = data_da_indice_mese_precedente(dati.periodo.anno, dati.periodo.mese, si.giorno)
        anno_iso, settimana_iso, _ = data_si.isocalendar()
        chiave = (anno_iso, settimana_iso)
        ore = ore_per_fascia.get(si.fascia, 0)
        per_lavoratore = ore_pregresse_per_settimana.setdefault(si.lavoratore_id, {})
        per_lavoratore[chiave] = per_lavoratore.get(chiave, 0) + ore

    # Giorni di ferie FORZATA dall'admin: contano sempre le ore virtuali,
    # e' un fatto certo (non condizionato a nessuna variabile del solver).
    ferie_forzata_per_settimana: dict[str, dict[tuple[int, int], int]] = {}
    for vadm in dati.vincoli_admin:
        if vadm.tipo != "ferie" or vadm.giorno not in giorni or vadm.lavoratore_id not in lavoratori_ids:
            continue
        data_v = data_da_indice_periodo(dati.periodo.anno, dati.periodo.mese, vadm.giorno)
        chiave = data_v.isocalendar()[:2]
        per_lavoratore = ferie_forzata_per_settimana.setdefault(vadm.lavoratore_id, {})
        per_lavoratore[chiave] = per_lavoratore.get(chiave, 0) + ore_ferie_giornaliere

    # Richieste soft di ferie: contano le ore virtuali SOLO se la richiesta
    # viene effettivamente concessa (variabile 'miss' della richiesta == 0).
    # Usiamo l'espressione (1 - miss) * ore_ferie_giornaliere, che vale
    # ore_ferie_giornaliere quando miss=0 (concessa) e 0 quando miss=1
    # (rifiutata, nel qual caso il lavoratore lavora davvero quel giorno e
    # le sue ore reali sono gia' contate a parte).
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
        # Nota: niente fallback su un default globale qui. Il campo e'
        # obbligatorio e specifico per lavoratore; un "or" con un default
        # globale tratterebbe erroneamente 0 (es. lavoratore con contratto
        # sospeso quel mese) come "non impostato", sostituendolo col
        # default 36h in modo silenzioso e sbagliato.
        max_ore = lavoratore.ore_settimanali_contratto

        for chiave_settimana, giorni_settimana in settimane.items():
            ore_gia_maturate = ore_pregresse_per_settimana.get(w, {}).get(chiave_settimana, 0)
            ore_ferie_forzata = ferie_forzata_per_settimana.get(w, {}).get(chiave_settimana, 0)
            miss_vars_ferie_soft = ferie_soft_per_settimana.get(w, {}).get(chiave_settimana, [])

            ore_espr = sum(
                ore_per_fascia.get(f, 0) * x[(w, g, f)]
                for g in giorni_settimana
                for f in fasce
            )
            ore_ferie_soft_espr = sum(
                ore_ferie_giornaliere * (1 - miss) for miss in miss_vars_ferie_soft
            )
            model.Add(
                ore_espr + ore_gia_maturate + ore_ferie_forzata + ore_ferie_soft_espr <= max_ore
            )

    # ==================================================================
    # LIVELLO 4: fairness (soft, priorita' piu' bassa)
    # ==================================================================
    peso_fairness = dati.parametri_fairness.peso_fairness
    n_giorni = len(giorni)

    if dati.parametri_fairness.bilancia_fasce:
        for f in fasce:
            conteggi = []
            for w in lavoratori_ids:
                c = model.NewIntVar(0, n_giorni, f"count_{w}_{f}")
                model.Add(c == sum(x[(w, g, f)] for g in giorni))
                conteggi.append(c)

            max_c = model.NewIntVar(0, n_giorni, f"max_{f}")
            min_c = model.NewIntVar(0, n_giorni, f"min_{f}")
            model.AddMaxEquality(max_c, conteggi)
            model.AddMinEquality(min_c, conteggi)

            scarto = model.NewIntVar(0, n_giorni, f"scarto_{f}")
            model.Add(scarto == max_c - min_c)
            termini_obiettivo.append(peso_fairness * scarto)

    if dati.parametri_fairness.bilancia_giorni_settimana:
        conteggi_giorni = []
        for w in lavoratori_ids:
            c = model.NewIntVar(0, n_giorni, f"giorni_lavorati_{w}")
            model.Add(c == sum(x[(w, g, f)] for g in giorni for f in fasce))
            conteggi_giorni.append(c)

        max_g = model.NewIntVar(0, n_giorni, "max_giorni")
        min_g = model.NewIntVar(0, n_giorni, "min_giorni")
        model.AddMaxEquality(max_g, conteggi_giorni)
        model.AddMinEquality(min_g, conteggi_giorni)

        scarto_giorni = model.NewIntVar(0, n_giorni, "scarto_giorni")
        model.Add(scarto_giorni == max_g - min_g)
        termini_obiettivo.append(peso_fairness * scarto_giorni)

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
                max_ore_w = lavoratore.ore_settimanali_contratto
                ore_gia_maturate = ore_pregresse_per_settimana.get(w, {}).get(chiave_settimana, 0)
                capacita_residua = max(max_ore_w - ore_gia_maturate, 0)

                ore_ferie_forzata = ferie_forzata_per_settimana.get(w, {}).get(chiave_settimana, 0)
                miss_vars_ferie_soft = ferie_soft_per_settimana.get(w, {}).get(chiave_settimana, [])
                ore_ferie_soft_espr = sum(
                    ore_ferie_giornaliere * (1 - miss) for miss in miss_vars_ferie_soft
                )

                # Le ore "nuove" includono anche le ore virtuali di ferie:
                # un lavoratore in ferie ha comunque "consumato" la sua
                # capacita' quella settimana, non e' sottoutilizzato solo
                # perche' non ha fisicamente lavorato quel giorno.
                ore_nuove_w = model.NewIntVar(0, max_ore_w, f"ore_nuove_{chiave_settimana}_{w}")
                model.Add(
                    ore_nuove_w
                    == sum(ore_per_fascia.get(f, 0) * x[(w, g, f)] for g in giorni_settimana for f in fasce)
                    + ore_ferie_forzata + ore_ferie_soft_espr
                )

                if capacita_residua > 0:
                    tasso_w = model.NewIntVar(0, SCALE, f"tasso_ore_{chiave_settimana}_{w}")
                    model.AddDivisionEquality(tasso_w, ore_nuove_w * SCALE, capacita_residua)
                    tassi_settimana.append(tasso_w)
                # Se capacita_residua == 0 (rarissimo: ha gia' esaurito il
                # monte ore solo con stato_iniziale), il vincolo hard altrove
                # gli impedisce comunque nuovi turni quella settimana; lo
                # escludiamo dal confronto proporzionale (divisione per zero).

            if len(tassi_settimana) >= 2:
                max_t = model.NewIntVar(0, SCALE, f"max_tasso_ore_{chiave_settimana}")
                min_t = model.NewIntVar(0, SCALE, f"min_tasso_ore_{chiave_settimana}")
                model.AddMaxEquality(max_t, tassi_settimana)
                model.AddMinEquality(min_t, tassi_settimana)

                scarto_tasso_ore = model.NewIntVar(0, SCALE, f"scarto_tasso_ore_{chiave_settimana}")
                model.Add(scarto_tasso_ore == max_t - min_t)
                termini_obiettivo.append(peso_fairness * scarto_tasso_ore)

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
        # degli altri termini di fairness prima di pesarlo.
        minimo_per_giorno_fascia = {
            (fab.giorno, fab.fascia): fab.minimo for fab in dati.fabbisogno
        }
        n_lavoratori = len(lavoratori_ids)
        SCALE = 100

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

            # Normalizziamo lo scarto togliendo il fattore di scala, cosi'
            # il peso_fairness pesa questo termine in modo comparabile agli
            # altri (che sono nell'ordine di 0..n_lavoratori/n_giorni)
            scarto_tasso_normalizzato = model.NewIntVar(0, n_lavoratori, "scarto_tasso_normalizzato")
            model.AddDivisionEquality(scarto_tasso_normalizzato, scarto_tasso, SCALE)
            termini_obiettivo.append(peso_fairness * scarto_tasso_normalizzato)

    # ------------------------------------------------------------------
    # Obiettivo finale: minimizza la somma pesata di tutte le penalita' soft
    # ------------------------------------------------------------------
    if termini_obiettivo:
        model.Minimize(sum(termini_obiettivo))

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = tempo_max_secondi
    status = solver.Solve(model)

    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return OutputTurnazione(stato="infeasible", tempo_impiegato_secondi=solver.WallTime())

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
