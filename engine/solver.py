"""
Motore di generazione turni - STEP 6 (completo per i vincoli principali).

Livelli implementati, in ordine di priorita' (dal piu' al meno vincolante):

  1. VINCOLI STRUTTURALI DI SISTEMA (sempre hard):
     - un lavoratore fa al massimo una fascia al giorno
     - copertura minima per giorno/fascia (fabbisogno)
     - riposo obbligatorio dopo un turno notturno
     - massimo notti consecutive
     - massimo ore settimanali da contratto
       (tutti tengono conto di stato_iniziale per i casi a cavallo di mese,
       incluse le ore gia' maturate nella stessa settimana ISO se la
       settimana e' a cavallo con il mese precedente)

  2. VINCOLI ADMIN (hard, imposti dal coordinatore):
     - "ferie" / "riposo" forzati -> giorno bloccato
     - "turno" forzato -> fascia specifica imposta
     (nota: la validazione preventiva di conflitti e il meccanismo di
     declassamento automatico sono volutamente rimandati a una fase
     successiva, come concordato)

  3. RICHIESTE SOFT (preferenze lavoratore, pesate 1-4):
     entrano nella funzione obiettivo come penalita' se non soddisfatte,
     con pesi esponenziali cosi' una richiesta di priorita' alta non
     viene mai sacrificata per soddisfarne tante di priorita' bassa

  4. FAIRNESS (soft, priorita' piu' bassa):
     minimizza lo scarto (max - min) tra lavoratori sul numero di turni
     per fascia e sul numero di giorni lavorati totali; minimizza inoltre
     lo scarto (max - min) del TASSO di surplus di copertura (surplus /
     fabbisogno minimo, non il surplus grezzo), confrontato su un'unica
     scala tra tutte le fasce e i giorni insieme: cosi' un eventuale
     surplus si distribuisce in proporzione al fabbisogno invece di
     concentrarsi su una fascia o un giorno specifico, anche quando il
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


def genera_turni(dati: InputTurnazione) -> OutputTurnazione:
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

    # Massimo ore settimanali da contratto
    ore_per_fascia = dati.regole_contrattuali.ore_per_fascia
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

    for w in lavoratori_ids:
        lavoratore = next(l for l in dati.lavoratori if l.id == w)
        max_ore = lavoratore.ore_settimanali_contratto or dati.regole_contrattuali.max_ore_settimanali

        for chiave_settimana, giorni_settimana in settimane.items():
            ore_gia_maturate = ore_pregresse_per_settimana.get(w, {}).get(chiave_settimana, 0)
            ore_espr = sum(
                ore_per_fascia.get(f, 0) * x[(w, g, f)]
                for g in giorni_settimana
                for f in fasce
            )
            model.Add(ore_espr + ore_gia_maturate <= max_ore)

    # ==================================================================
    # LIVELLO 2: vincoli admin (hard, imposti dal coordinatore)
    # ==================================================================
    for vadm in dati.vincoli_admin:
        if vadm.giorno not in giorni or vadm.lavoratore_id not in lavoratori_ids:
            continue

        if vadm.tipo in ("ferie", "riposo"):
            for f in fasce:
                model.Add(x[(vadm.lavoratore_id, vadm.giorno, f)] == 0)

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
            # "miss" = 1 se il lavoratore lavora quel giorno (qualsiasi fascia)
            for f in fasce:
                model.Add(miss >= x[(req.lavoratore_id, req.giorno, f)])

        elif req.tipo == "turno" and req.fascia in fasce:
            # "miss" = 1 se NON gli viene assegnata la fascia richiesta
            model.Add(miss == 1 - x[(req.lavoratore_id, req.giorno, req.fascia)])

        else:
            continue

        termini_obiettivo.append(peso * miss)

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
    solver.parameters.max_time_in_seconds = 30
    status = solver.Solve(model)

    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return OutputTurnazione(stato="infeasible")

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
    )


if __name__ == "__main__":
    # Esecuzione rapida manuale: python -m engine.solver
    from engine.sample_data import get_sample_input

    risultato = genera_turni(get_sample_input())
    print("Stato:", risultato.stato)
    for a in sorted(risultato.assegnazioni, key=lambda a: (a.giorno, a.lavoratore_id)):
        print(f"  giorno {a.giorno:>2}  {a.lavoratore_id}  ->  {a.fascia}")

    if risultato.richieste_non_soddisfatte:
        print("\nRichieste non soddisfatte:")
        for r in risultato.richieste_non_soddisfatte:
            print(f"  {r.richiesta_id}: {r.motivo}")

    print("\nMetriche fairness:")
    for chiave, valore in risultato.metriche_fairness.items():
        print(f"  {chiave}: {valore}")
