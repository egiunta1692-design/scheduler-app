"""
Test del motore: copre i livelli 1-4 (vincoli strutturali, vincoli admin,
richieste soft, fairness).

Ogni volta che aggiungiamo un nuovo vincolo al motore, aggiungiamo qui
il test corrispondente.
"""

from collections import defaultdict

from engine.models import (
    InputTurnazione,
    Periodo,
    Lavoratore,
    VincoliPersonali,
    Fabbisogno,
    StatoIniziale,
    RegoleContrattuali,
    RichiestaSoft,
    VincoloAdmin,
    ParametriFairness,
)
from engine.sample_data import get_sample_input
from engine.solver import genera_turni
from engine.periodo_utils import calcola_giorno_fine_periodo, data_da_indice_periodo


def test_soluzione_trovata():
    dati = get_sample_input()
    risultato = genera_turni(dati)
    assert risultato.stato == "feasible"


def test_un_turno_al_giorno_per_lavoratore():
    dati = get_sample_input()
    risultato = genera_turni(dati)

    conteggio = defaultdict(int)
    for a in risultato.assegnazioni:
        conteggio[(a.lavoratore_id, a.giorno)] += 1

    assert all(v <= 1 for v in conteggio.values())


def test_copertura_minima_rispettata():
    dati = get_sample_input()
    risultato = genera_turni(dati)

    copertura = defaultdict(int)
    for a in risultato.assegnazioni:
        copertura[(a.giorno, a.fascia)] += 1

    for fab in dati.fabbisogno:
        assert copertura[(fab.giorno, fab.fascia)] >= fab.minimo


def test_vincolo_admin_ferie_rispettato():
    dati = get_sample_input()
    risultato = genera_turni(dati)

    # adm1: w1 in ferie forzate il giorno 5 -> nessuna assegnazione quel giorno
    assegnazioni_w1_giorno5 = [
        a for a in risultato.assegnazioni if a.lavoratore_id == "w1" and a.giorno == 5
    ]
    assert assegnazioni_w1_giorno5 == []


# ---------------------------------------------------------------------------
# STEP 2: riposo dopo notte + max notti consecutive
# ---------------------------------------------------------------------------

def test_nessun_MP_dopo_notte():
    """Proprieta' strutturale: qualunque sia la soluzione trovata,
    nessun lavoratore deve avere M o P il giorno dopo una N."""
    dati = get_sample_input()
    risultato = genera_turni(dati)

    per_lavoratore_giorno = {
        (a.lavoratore_id, a.giorno): a.fascia for a in risultato.assegnazioni
    }

    for (w, g), fascia in per_lavoratore_giorno.items():
        if fascia == "N":
            fascia_giorno_dopo = per_lavoratore_giorno.get((w, g + 1))
            assert fascia_giorno_dopo not in dati.regole_contrattuali.vietato_dopo_notte


def test_max_notti_consecutive_rispettato():
    dati = get_sample_input()
    risultato = genera_turni(dati)
    max_consec = dati.regole_contrattuali.max_notti_consecutive

    notti_per_lavoratore = defaultdict(set)
    for a in risultato.assegnazioni:
        if a.fascia == "N":
            notti_per_lavoratore[a.lavoratore_id].add(a.giorno)

    for w, giorni_notte in notti_per_lavoratore.items():
        consecutive_max_trovate = 0
        consecutive_correnti = 0
        precedente = None
        for g in sorted(giorni_notte):
            if precedente is not None and g == precedente + 1:
                consecutive_correnti += 1
            else:
                consecutive_correnti = 1
            consecutive_max_trovate = max(consecutive_max_trovate, consecutive_correnti)
            precedente = g

        assert consecutive_max_trovate <= max_consec


def test_riposo_rispettato_a_cavallo_con_mese_precedente():
    """Se il lavoratore ha fatto notte l'ultimo giorno del mese precedente,
    il primo giorno del periodo corrente non puo' essere M o P."""
    dati = InputTurnazione(
        reparto_id="rep_test",
        categoria="infermieri",
        periodo=Periodo(anno=2026, mese=7, giorno_inizio=1, giorno_fine=7),
        lavoratori=[
            Lavoratore(id="w1", nome="Test Uno", ore_settimanali_min=36, ore_settimanali_max=36),
            Lavoratore(id="w2", nome="Test Due", ore_settimanali_min=36, ore_settimanali_max=36),
            Lavoratore(id="w3", nome="Test Tre", ore_settimanali_min=36, ore_settimanali_max=36),
            Lavoratore(id="w4", nome="Test Quattro", ore_settimanali_min=36, ore_settimanali_max=36),
            # Quinto lavoratore aggiunto per mantenere margine di capacita':
            # w1 ha 8 ore gia' "consumate" nella settimana a cavallo (notte
            # del 30/06) e con soli 4 lavoratori la capacita' settimanale
            # coinciderebbe esattamente con la domanda, rendendo il test
            # troppo fragile.
            Lavoratore(id="w5", nome="Test Cinque", ore_settimanali_min=36, ore_settimanali_max=36),
        ],
        fabbisogno=[
            Fabbisogno(giorno=g, fascia=f, minimo=1)
            for g in range(1, 8) for f in ("M", "P", "N")
        ],
        regole_contrattuali=RegoleContrattuali(),
        stato_iniziale=[
            StatoIniziale(lavoratore_id="w1", giorno=30, fascia="N", mese_precedente=True),
        ],
    )

    risultato = genera_turni(dati)
    assert risultato.stato == "feasible"

    assegnazione_w1_giorno1 = next(
        (a.fascia for a in risultato.assegnazioni if a.lavoratore_id == "w1" and a.giorno == 1),
        None,
    )
    assert assegnazione_w1_giorno1 not in ("M", "P")


def test_ore_pregresse_stato_iniziale_conteggiate_nella_settimana():
    """Il 1 febbraio 2026 e' una domenica: l'intera settimana precedente
    (26-31 gennaio) cade nella stessa settimana ISO. Se w1 ha gia' fatto
    4 notti (32 ore) in quella settimana, con un contratto da 36 ore
    settimanali gli resta solo 4 ore di margine: non puo' fare un altro
    turno da 8 ore il 1 febbraio senza sforare il monte ore."""
    dati = InputTurnazione(
        reparto_id="rep_test_ore_pregresse",
        categoria="infermieri",
        periodo=Periodo(anno=2026, mese=2, giorno_inizio=1, giorno_fine=1),
        lavoratori=[
            Lavoratore(id="w1", nome="Test Sature", ore_settimanali_min=36, ore_settimanali_max=36),
            Lavoratore(id="w2", nome="Test Backup 1", ore_settimanali_min=36, ore_settimanali_max=36),
            Lavoratore(id="w3", nome="Test Backup 2", ore_settimanali_min=36, ore_settimanali_max=36),
        ],
        fabbisogno=[
            Fabbisogno(giorno=1, fascia="M", minimo=1),
        ],
        regole_contrattuali=RegoleContrattuali(),
        stato_iniziale=[
            # 26-29 gennaio 2026: tutti nella stessa settimana ISO del
            # 1 febbraio (che e' una domenica, quindi l'intera settimana
            # lun-sab precedente e' "dentro" alla stessa settimana ISO)
            StatoIniziale(lavoratore_id="w1", giorno=26, fascia="N", mese_precedente=True),
            StatoIniziale(lavoratore_id="w1", giorno=27, fascia="N", mese_precedente=True),
            StatoIniziale(lavoratore_id="w1", giorno=28, fascia="N", mese_precedente=True),
            StatoIniziale(lavoratore_id="w1", giorno=29, fascia="N", mese_precedente=True),
        ],
    )

    risultato = genera_turni(dati)
    assert risultato.stato == "feasible"

    assegnazione_w1 = next(
        (a for a in risultato.assegnazioni if a.lavoratore_id == "w1" and a.giorno == 1),
        None,
    )
    assert assegnazione_w1 is None, (
        "w1 ha gia' maturato 32 ore questa settimana (mese precedente): "
        "un ulteriore turno da 8 ore supererebbe le 36 ore contrattuali"
    )


# ---------------------------------------------------------------------------
# STEP 3: massimo ore settimanali da contratto
# ---------------------------------------------------------------------------

def test_max_ore_settimanali_rispettato():
    dati = get_sample_input()
    risultato = genera_turni(dati)
    assert risultato.stato == "feasible"

    minuti_per_fascia = dati.regole_contrattuali.minuti_per_fascia
    minuti_per_settimana = defaultdict(int)

    for a in risultato.assegnazioni:
        data = data_da_indice_periodo(dati.periodo.anno, dati.periodo.mese, a.giorno)
        _, settimana_iso, _ = data.isocalendar()
        chiave = (a.lavoratore_id, settimana_iso)
        minuti_per_settimana[chiave] += minuti_per_fascia.get(a.fascia, 0)

    lavoratori_per_id = {l.id: l for l in dati.lavoratori}
    for (w, _settimana), minuti in minuti_per_settimana.items():
        max_minuti = lavoratori_per_id[w].ore_settimanali_max * 60
        assert minuti <= max_minuti


# ---------------------------------------------------------------------------
# STEP 4: vincoli admin di tipo "turno" forzato
# ---------------------------------------------------------------------------

def test_vincolo_admin_turno_forzato_rispettato():
    dati = get_sample_input()
    risultato = genera_turni(dati)
    assert risultato.stato in ("feasible", "feasible_con_declassamenti")

    # adm2: w4 deve avere M il giorno 10, imposto dal coordinatore (spostato
    # da giorno 2 a giorno 10: troppo vicino all'inizio del periodo, rischiava
    # di collidere con l'eventuale riposo dovuto a notti nella situazione
    # iniziale generata automaticamente in app.py — bug scoperto in
    # produzione, vedi sample_data.py)
    fascia_w4_giorno10 = next(
        (a.fascia for a in risultato.assegnazioni if a.lavoratore_id == "w4" and a.giorno == 10),
        None,
    )
    assert fascia_w4_giorno10 == "M"


# ---------------------------------------------------------------------------
# STEP 5: richieste soft pesate nell'obiettivo
# ---------------------------------------------------------------------------

def test_richiesta_soft_alta_priorita_soddisfatta_se_possibile():
    """req1: w2 chiede ferie il giorno 10 con priorita' massima (4).
    Nel caso di esempio non c'e' nessun motivo strutturale per cui non
    possa essere soddisfatta, quindi ci aspettiamo che lo sia."""
    dati = get_sample_input()
    risultato = genera_turni(dati)

    assegnazioni_w2_giorno10 = [
        a for a in risultato.assegnazioni if a.lavoratore_id == "w2" and a.giorno == 10
    ]
    assert assegnazioni_w2_giorno10 == []


def test_richieste_non_soddisfatte_tracciate_in_output():
    """Costruiamo uno scenario dove una richiesta soft e' in conflitto
    diretto con la copertura minima, quindi non puo' essere soddisfatta:
    verifichiamo che il motore lo segnali in output invece di ignorarlo
    silenziosamente."""
    dati = InputTurnazione(
        reparto_id="rep_test_soft",
        categoria="infermieri",
        periodo=Periodo(anno=2026, mese=7, giorno_inizio=1, giorno_fine=1),
        lavoratori=[
            Lavoratore(id="w1", nome="Unico Lavoratore", ore_settimanali_min=36, ore_settimanali_max=36),
        ],
        fabbisogno=[
            Fabbisogno(giorno=1, fascia="M", minimo=1),
        ],
        richieste_soft=[
            # L'unico lavoratore disponibile chiede ferie, ma serve
            # comunque in copertura -> la richiesta non potra' essere
            # soddisfatta senza violare il fabbisogno minimo
            RichiestaSoft(id="reqX", lavoratore_id="w1", giorno=1, tipo="ferie", priorita=4),
        ],
    )

    risultato = genera_turni(dati)
    assert risultato.stato == "feasible_con_declassamenti"
    assert any(r.richiesta_id == "reqX" for r in risultato.richieste_non_soddisfatte)


# ---------------------------------------------------------------------------
# STEP 6: fairness
# ---------------------------------------------------------------------------

def test_fairness_presente_in_output():
    dati = get_sample_input()
    risultato = genera_turni(dati)

    assert "giorni_lavorati_per_lavoratore" in risultato.metriche_fairness
    giorni_lavorati = risultato.metriche_fairness["giorni_lavorati_per_lavoratore"]
    assert set(giorni_lavorati.keys()) == {l.id for l in dati.lavoratori}


def test_fairness_riduce_squilibrio_tra_lavoratori():
    """Non pretendiamo un bilanciamento perfetto (ci sono vincoli admin e
    richieste che creano asimmetrie legittime), ma verifichiamo che lo
    scarto tra chi lavora di piu' e chi lavora di meno resti contenuto,
    a conferma che il termine di fairness nell'obiettivo sta avendo effetto."""
    dati = get_sample_input()
    risultato = genera_turni(dati)

    giorni_lavorati = risultato.metriche_fairness["giorni_lavorati_per_lavoratore"]
    scarto = max(giorni_lavorati.values()) - min(giorni_lavorati.values())

    # Soglia larga apposta: e' un controllo di sanita', non un vincolo
    # esatto. Se lo scarto fosse enorme (es. meta' squadra a 0 turni)
    # indicherebbe un problema nella logica di fairness.
    assert scarto <= 4


def test_fairness_spalma_surplus_copertura_tra_giorni():
    """Con fabbisogno costante (3M+3P+2N ogni giorno nel sample), se il
    motore assegna surplus in alcuni giorni deve distribuirlo il piu'
    possibile invece di concentrarlo: verifichiamo che lo scarto tra il
    giorno con piu' surplus e quello con meno surplus, per ciascuna
    fascia, resti contenuto."""
    dati = get_sample_input()
    risultato = genera_turni(dati)
    assert risultato.stato in ("feasible", "feasible_con_declassamenti")

    minimo_per_giorno_fascia = {
        (fab.giorno, fab.fascia): fab.minimo for fab in dati.fabbisogno
    }
    conteggio_effettivo = defaultdict(int)
    for a in risultato.assegnazioni:
        conteggio_effettivo[(a.giorno, a.fascia)] += 1

    giorni = list(range(dati.periodo.giorno_inizio, dati.periodo.giorno_fine + 1))

    for f in ("M", "P", "N"):
        surplus_per_giorno = [
            conteggio_effettivo.get((g, f), 0) - minimo_per_giorno_fascia.get((g, f), 0)
            for g in giorni
        ]
        scarto_surplus = max(surplus_per_giorno) - min(surplus_per_giorno)

        # Soglia larga apposta (controllo di sanita', non vincolo esatto):
        # se il surplus fosse concentrato su un solo giorno mentre tutti
        # gli altri restano esattamente al minimo, lo scarto sarebbe alto.
        assert scarto_surplus <= 4, (
            f"Surplus di copertura per fascia {f} troppo concentrato: "
            f"{surplus_per_giorno}"
        )


def test_fairness_surplus_proporzionale_tra_fasce():
    """Nel sample M e P hanno lo stesso fabbisogno (3 al giorno): un
    eventuale surplus non deve concentrarsi su una fascia piuttosto che
    sull'altra solo perche' venivano bilanciate in modo indipendente.
    Verifichiamo che, mettendo insieme il surplus di M e di P (stesso
    fabbisogno, quindi confrontabili direttamente senza bisogno di
    normalizzare per il fabbisogno), lo scarto resti contenuto."""
    dati = get_sample_input()
    risultato = genera_turni(dati)
    assert risultato.stato in ("feasible", "feasible_con_declassamenti")

    minimo_per_giorno_fascia = {
        (fab.giorno, fab.fascia): fab.minimo for fab in dati.fabbisogno
    }
    conteggio_effettivo = defaultdict(int)
    for a in risultato.assegnazioni:
        conteggio_effettivo[(a.giorno, a.fascia)] += 1

    giorni = list(range(dati.periodo.giorno_inizio, dati.periodo.giorno_fine + 1))

    surplus_m_e_p = []
    for f in ("M", "P"):
        for g in giorni:
            minimo_gf = minimo_per_giorno_fascia.get((g, f), 0)
            if minimo_gf > 0:  # confronto valido solo se stesso ordine di grandezza
                surplus_m_e_p.append(conteggio_effettivo.get((g, f), 0) - minimo_gf)

    scarto = max(surplus_m_e_p) - min(surplus_m_e_p)

    # Soglia larga apposta (controllo di sanita'): prima di questa modifica,
    # M e P venivano bilanciate in modo completamente indipendente e
    # potevano divergere parecchio pur avendo lo stesso fabbisogno.
    assert scarto <= 4, (
        f"Surplus non bilanciato in modo proporzionale tra M e P: {surplus_m_e_p}"
    )


# ---------------------------------------------------------------------------
# Estensione del periodo fino a domenica (periodo_utils)
# ---------------------------------------------------------------------------

def test_periodo_esteso_se_mese_finisce_a_meta_settimana():
    """Luglio 2026 finisce venerdi' 31: il periodo deve estendersi fino a
    domenica 2 agosto (giorno indice 33)."""
    giorno_fine = calcola_giorno_fine_periodo(2026, 7)
    assert giorno_fine == 33

    data_fine = data_da_indice_periodo(2026, 7, giorno_fine)
    assert data_fine.year == 2026 and data_fine.month == 8 and data_fine.day == 2
    assert data_fine.isoweekday() == 7  # domenica


def test_periodo_non_esteso_se_mese_finisce_di_domenica():
    """Maggio 2026 finisce gia' di domenica: nessuna estensione necessaria."""
    giorno_fine = calcola_giorno_fine_periodo(2026, 5)
    assert giorno_fine == 31


def test_motore_gestisce_periodo_esteso_nel_mese_successivo():
    """Verifica che il motore funzioni correttamente anche quando il
    periodo si estende oltre la fine del mese (es. luglio 2026 esteso
    fino al 2 agosto), sia per la copertura sia per il vincolo ore
    settimanali sull'ultima settimana."""
    giorno_fine = calcola_giorno_fine_periodo(2026, 7)  # 33

    dati = InputTurnazione(
        reparto_id="rep_test_estensione",
        categoria="infermieri",
        periodo=Periodo(anno=2026, mese=7, giorno_inizio=1, giorno_fine=giorno_fine),
        lavoratori=[
            Lavoratore(id=f"w{i}", nome=f"Test {i}", ore_settimanali_min=36, ore_settimanali_max=36)
            for i in range(1, 9)
        ],
        fabbisogno=[
            Fabbisogno(giorno=g, fascia=f, minimo=1)
            for g in range(1, giorno_fine + 1) for f in ("M", "P", "N")
        ],
        regole_contrattuali=RegoleContrattuali(),
    )

    risultato = genera_turni(dati)
    assert risultato.stato in ("feasible", "feasible_con_declassamenti")

    # I giorni 32 e 33 (1 e 2 agosto) devono avere copertura come tutti gli altri
    copertura_giorno_32 = [a for a in risultato.assegnazioni if a.giorno == 32]
    copertura_giorno_33 = [a for a in risultato.assegnazioni if a.giorno == 33]
    assert len(copertura_giorno_32) >= 3  # almeno 1M+1P+1N
    assert len(copertura_giorno_33) >= 3


# ---------------------------------------------------------------------------
# Vincoli personali per-lavoratore: mai_notti e ore_settimanali_min/max
# ---------------------------------------------------------------------------

def test_mai_notti_rispettato():
    """w1 ha vincoli_personali.mai_notti=True: non deve MAI comparire con
    fascia N nell'output, nemmeno se questo costringe gli altri lavoratori
    a coprire tutte le notti."""
    dati = InputTurnazione(
        reparto_id="rep_test_mai_notti",
        categoria="infermieri",
        periodo=Periodo(anno=2026, mese=7, giorno_inizio=1, giorno_fine=7),
        lavoratori=[
            Lavoratore(
                id="w1", nome="Mai Notti", ore_settimanali_min=36, ore_settimanali_max=36,
                vincoli_personali=VincoliPersonali(mai_notti=True),
            ),
            Lavoratore(id="w2", nome="Test Due", ore_settimanali_min=36, ore_settimanali_max=36),
            Lavoratore(id="w3", nome="Test Tre", ore_settimanali_min=36, ore_settimanali_max=36),
            Lavoratore(id="w4", nome="Test Quattro", ore_settimanali_min=36, ore_settimanali_max=36),
        ],
        fabbisogno=[
            Fabbisogno(giorno=g, fascia=f, minimo=1)
            for g in range(1, 8) for f in ("M", "P", "N")
        ],
        regole_contrattuali=RegoleContrattuali(),
    )

    risultato = genera_turni(dati)
    assert risultato.stato in ("feasible", "feasible_con_declassamenti")

    notti_w1 = [a for a in risultato.assegnazioni if a.lavoratore_id == "w1" and a.fascia == "N"]
    assert notti_w1 == [], "w1 ha mai_notti=True ma gli sono state assegnate notti"


def test_ore_settimanali_specifiche_per_lavoratore():
    """Due lavoratori con ore_settimanali_min/max diverse (0 e 36) sullo
    stesso giorno: quello con 0 ore non deve MAI lavorare (verifica che il
    parametro sia rispettato per singolo lavoratore, senza fallback
    silenzioso su un default globale quando il valore e' 0)."""
    dati = InputTurnazione(
        reparto_id="rep_test_ore_zero",
        categoria="infermieri",
        periodo=Periodo(anno=2026, mese=7, giorno_inizio=1, giorno_fine=1),
        lavoratori=[
            Lavoratore(id="w1", nome="Contratto Zero", ore_settimanali_min=0, ore_settimanali_max=0),
            Lavoratore(id="w2", nome="Contratto Normale", ore_settimanali_min=36, ore_settimanali_max=36),
        ],
        fabbisogno=[
            Fabbisogno(giorno=1, fascia="M", minimo=1),
        ],
        regole_contrattuali=RegoleContrattuali(),
    )

    risultato = genera_turni(dati)
    assert risultato.stato == "feasible"

    assegnazioni_w1 = [a for a in risultato.assegnazioni if a.lavoratore_id == "w1"]
    assert assegnazioni_w1 == [], (
        "w1 ha ore_settimanali_min=0, ore_settimanali_max=0 ma gli e' stato assegnato un turno: "
        "probabile fallback errato sul default globale"
    )

    assegnazioni_w2 = [a for a in risultato.assegnazioni if a.lavoratore_id == "w2" and a.fascia == "M"]
    assert len(assegnazioni_w2) == 1, "w2 doveva coprire il fabbisogno al posto di w1"


# ---------------------------------------------------------------------------
# Fairness: ore settimanali bilanciate tra lavoratori, settimana per settimana
# ---------------------------------------------------------------------------

def test_fairness_bilancia_ore_per_settimana():
    """Verifica che le ore lavorate tra i lavoratori restino bilanciate
    settimana per settimana (non solo in media sul periodo intero):
    prima di questa modifica era possibile che una singola settimana
    fosse molto sbilanciata (es. qualcuno con 8 ore, qualcun altro con
    32) pur avendo un totale di periodo equilibrato."""
    dati = get_sample_input()
    risultato = genera_turni(dati)
    assert risultato.stato in ("feasible", "feasible_con_declassamenti")

    minuti_per_fascia = dati.regole_contrattuali.minuti_per_fascia
    minuti_per_settimana_lavoratore = defaultdict(lambda: defaultdict(int))

    for a in risultato.assegnazioni:
        data = data_da_indice_periodo(dati.periodo.anno, dati.periodo.mese, a.giorno)
        chiave = data.isocalendar()[:2]
        minuti_per_settimana_lavoratore[chiave][a.lavoratore_id] += minuti_per_fascia.get(a.fascia, 0)

    lavoratori_ids = [l.id for l in dati.lavoratori]

    for chiave, minuti_per_lavoratore in minuti_per_settimana_lavoratore.items():
        ore_complete = [minuti_per_lavoratore.get(w, 0) / 60 for w in lavoratori_ids]
        scarto = max(ore_complete) - min(ore_complete)

        # Soglia larga apposta (controllo di sanita', non vincolo esatto):
        # con 20 lavoratori e fabbisogno modesto (3M+3P+2N/giorno), uno
        # scarto enorme (es. 0 vs 32 ore) nella stessa settimana
        # indicherebbe che il bilanciamento settimanale non sta
        # funzionando.
        assert scarto <= 16, (
            f"Scarto ore troppo alto nella settimana {chiave}: {ore_complete}"
        )


# ---------------------------------------------------------------------------
# Ferie vs riposo: ore virtuali nel monte ore settimanale
# ---------------------------------------------------------------------------

def test_ferie_forzata_riduce_capacita_oraria_disponibile():
    """w1 (unico lavoratore disponibile) ha ferie forzata il giorno 3.
    Il fabbisogno richiede 4 turni nella stessa settimana che solo lui puo'
    coprire: 4*8=32h + 8h virtuali di ferie = 40h > 36h contrattuali ->
    deve essere INFEASIBLE, anche se fisicamente lavorerebbe solo 32 ore."""
    dati = InputTurnazione(
        reparto_id="rep_test_ferie_ore",
        categoria="infermieri",
        periodo=Periodo(anno=2026, mese=6, giorno_inizio=1, giorno_fine=7),  # 1 giu 2026 = lunedi'
        lavoratori=[
            Lavoratore(id="w1", nome="Unico Disponibile", ore_settimanali_min=36, ore_settimanali_max=36),
        ],
        fabbisogno=[
            Fabbisogno(giorno=1, fascia="M", minimo=1),
            Fabbisogno(giorno=2, fascia="M", minimo=1),
            Fabbisogno(giorno=4, fascia="M", minimo=1),
            Fabbisogno(giorno=5, fascia="M", minimo=1),
        ],
        vincoli_admin=[
            VincoloAdmin(id="adm1", lavoratore_id="w1", giorno=3, tipo="ferie"),
        ],
        regole_contrattuali=RegoleContrattuali(),
    )

    risultato = genera_turni(dati)
    assert risultato.stato == "infeasible", (
        "Con la ferie che conta ore virtuali, 4 turni + 1 ferie nella stessa "
        "settimana dovrebbero superare il monte ore e rendere il problema "
        "irrisolvibile con un solo lavoratore disponibile"
    )


def test_riposo_forzato_non_riduce_capacita_oraria():
    """Stesso identico scenario del test sopra, ma con RIPOSO invece di
    FERIE: il riposo non aggiunge ore virtuali, quindi 4 turni da 8h (32h)
    restano sotto le 36h contrattuali e il problema deve essere risolvibile."""
    dati = InputTurnazione(
        reparto_id="rep_test_riposo_ore",
        categoria="infermieri",
        periodo=Periodo(anno=2026, mese=6, giorno_inizio=1, giorno_fine=7),
        lavoratori=[
            Lavoratore(id="w1", nome="Unico Disponibile", ore_settimanali_min=36, ore_settimanali_max=36),
        ],
        fabbisogno=[
            Fabbisogno(giorno=1, fascia="M", minimo=1),
            Fabbisogno(giorno=2, fascia="M", minimo=1),
            Fabbisogno(giorno=4, fascia="M", minimo=1),
            Fabbisogno(giorno=5, fascia="M", minimo=1),
        ],
        vincoli_admin=[
            VincoloAdmin(id="adm1", lavoratore_id="w1", giorno=3, tipo="riposo"),
        ],
        regole_contrattuali=RegoleContrattuali(),
    )

    risultato = genera_turni(dati)
    assert risultato.stato == "feasible", (
        "Il riposo non deve aggiungere ore virtuali: 4 turni da 8h (32h) "
        "restano sotto le 36h contrattuali"
    )

    turni_w1 = [a for a in risultato.assegnazioni if a.lavoratore_id == "w1"]
    assert len(turni_w1) == 4


# ---------------------------------------------------------------------------
# Niente notte il giorno prima di una ferie (forzata o concessa)
# ---------------------------------------------------------------------------

def test_niente_notte_prima_di_ferie_forzata():
    """w1 ha ferie forzata il giorno 2: non deve mai fare notte il giorno 1,
    anche se fabbisogno e disponibilita' lo renderebbero altrimenti comodo
    (con solo 2 lavoratori, deve essere w2 a coprire quella notte)."""
    dati = InputTurnazione(
        reparto_id="rep_test_notte_ferie",
        categoria="infermieri",
        periodo=Periodo(anno=2026, mese=6, giorno_inizio=1, giorno_fine=2),
        lavoratori=[
            Lavoratore(id="w1", nome="Test Uno", ore_settimanali_min=36, ore_settimanali_max=36),
            Lavoratore(id="w2", nome="Test Due", ore_settimanali_min=36, ore_settimanali_max=36),
        ],
        fabbisogno=[
            Fabbisogno(giorno=1, fascia="N", minimo=1),
        ],
        vincoli_admin=[
            VincoloAdmin(id="adm1", lavoratore_id="w1", giorno=2, tipo="ferie"),
        ],
        regole_contrattuali=RegoleContrattuali(),
    )

    risultato = genera_turni(dati)
    assert risultato.stato in ("feasible", "feasible_con_declassamenti")

    notte_giorno1 = [a for a in risultato.assegnazioni if a.giorno == 1 and a.fascia == "N"]
    assert len(notte_giorno1) == 1
    assert notte_giorno1[0].lavoratore_id == "w2", (
        "w1 ha ferie forzata il giorno 2: non puo' aver fatto notte il "
        "giorno prima, deve coprire w2"
    )


def test_niente_notte_prima_di_richiesta_ferie_concessa():
    """Se una richiesta soft di ferie viene concessa (il lavoratore risulta
    libero quel giorno), il giorno prima non deve avere una notte per lo
    stesso lavoratore — verifichiamo l'implicazione logica, qualunque sia
    la decisione presa dal motore sulla richiesta."""
    dati = InputTurnazione(
        reparto_id="rep_test_notte_ferie_soft",
        categoria="infermieri",
        periodo=Periodo(anno=2026, mese=6, giorno_inizio=1, giorno_fine=2),
        lavoratori=[
            Lavoratore(id="w1", nome="Test Uno", ore_settimanali_min=36, ore_settimanali_max=36),
            Lavoratore(id="w2", nome="Test Due", ore_settimanali_min=36, ore_settimanali_max=36),
        ],
        fabbisogno=[
            Fabbisogno(giorno=1, fascia="N", minimo=1),
        ],
        richieste_soft=[
            RichiestaSoft(id="req1", lavoratore_id="w1", giorno=2, tipo="ferie", priorita=4),
        ],
        regole_contrattuali=RegoleContrattuali(),
    )

    risultato = genera_turni(dati)
    assert risultato.stato in ("feasible", "feasible_con_declassamenti")

    w1_lavora_giorno2 = any(a.lavoratore_id == "w1" and a.giorno == 2 for a in risultato.assegnazioni)
    w1_notte_giorno1 = any(
        a.lavoratore_id == "w1" and a.giorno == 1 and a.fascia == "N" for a in risultato.assegnazioni
    )

    if not w1_lavora_giorno2:
        # La richiesta di ferie e' stata concessa: w1 non deve aver fatto
        # notte il giorno prima.
        assert not w1_notte_giorno1, (
            "La richiesta di ferie di w1 e' stata concessa (giorno 2 libero) "
            "ma w1 ha fatto notte il giorno prima: non dovrebbe essere possibile"
        )


# ---------------------------------------------------------------------------
# Minimizzazione sequenze Pomeriggio -> Mattino consecutive
# ---------------------------------------------------------------------------

def test_minimizza_pm_evita_quando_possibile():
    """Con 2 lavoratori intercambiabili e nessun altro vincolo che
    favorisca uno piuttosto che l'altro, il motore deve evitare che lo
    STESSO lavoratore faccia Pomeriggio il giorno 1 e Mattino il giorno 2
    (e' evitabile assegnando i due turni a lavoratori diversi)."""
    dati = InputTurnazione(
        reparto_id="rep_test_pm",
        categoria="infermieri",
        periodo=Periodo(anno=2026, mese=6, giorno_inizio=1, giorno_fine=2),
        lavoratori=[
            Lavoratore(id="w1", nome="Test Uno", ore_settimanali_min=36, ore_settimanali_max=36),
            Lavoratore(id="w2", nome="Test Due", ore_settimanali_min=36, ore_settimanali_max=36),
        ],
        fabbisogno=[
            Fabbisogno(giorno=1, fascia="P", minimo=1),
            Fabbisogno(giorno=2, fascia="M", minimo=1),
        ],
        regole_contrattuali=RegoleContrattuali(),
        parametri_fairness=ParametriFairness(minimizza_pm_consecutivo=True),
    )

    risultato = genera_turni(dati)
    assert risultato.stato in ("feasible", "feasible_con_declassamenti")

    for w in ("w1", "w2"):
        ha_p_giorno1 = any(a.lavoratore_id == w and a.giorno == 1 and a.fascia == "P" for a in risultato.assegnazioni)
        ha_m_giorno2 = any(a.lavoratore_id == w and a.giorno == 2 and a.fascia == "M" for a in risultato.assegnazioni)
        assert not (ha_p_giorno1 and ha_m_giorno2), (
            f"{w} ha fatto P il giorno 1 e M il giorno 2: evitabile "
            "assegnando i due turni a lavoratori diversi"
        )


def test_minimizza_pm_disattivabile():
    """Con l'opzione disattivata, il motore non deve piu' preoccuparsi di
    evitare le sequenze P->M (il test verifica solo che il flag non
    causi errori e che il problema resti risolvibile)."""
    dati = InputTurnazione(
        reparto_id="rep_test_pm_off",
        categoria="infermieri",
        periodo=Periodo(anno=2026, mese=6, giorno_inizio=1, giorno_fine=2),
        lavoratori=[
            Lavoratore(id="w1", nome="Test Uno", ore_settimanali_min=36, ore_settimanali_max=36),
        ],
        fabbisogno=[
            Fabbisogno(giorno=1, fascia="P", minimo=1),
            Fabbisogno(giorno=2, fascia="M", minimo=1),
        ],
        regole_contrattuali=RegoleContrattuali(),
        parametri_fairness=ParametriFairness(minimizza_pm_consecutivo=False),
    )

    risultato = genera_turni(dati)
    assert risultato.stato in ("feasible", "feasible_con_declassamenti")


def test_minimizza_pm_non_impedisce_soluzione_quando_inevitabile():
    """Con un solo lavoratore disponibile, la sequenza P->M e' inevitabile
    per coprire il fabbisogno: essendo un vincolo soft (non hard), il
    problema deve restare risolvibile anche in questo caso."""
    dati = InputTurnazione(
        reparto_id="rep_test_pm_inevitabile",
        categoria="infermieri",
        periodo=Periodo(anno=2026, mese=6, giorno_inizio=1, giorno_fine=2),
        lavoratori=[
            Lavoratore(id="w1", nome="Unico Disponibile", ore_settimanali_min=36, ore_settimanali_max=36),
        ],
        fabbisogno=[
            Fabbisogno(giorno=1, fascia="P", minimo=1),
            Fabbisogno(giorno=2, fascia="M", minimo=1),
        ],
        regole_contrattuali=RegoleContrattuali(),
        parametri_fairness=ParametriFairness(minimizza_pm_consecutivo=True),
    )

    risultato = genera_turni(dati)
    assert risultato.stato in ("feasible", "feasible_con_declassamenti"), (
        "Il vincolo e' soft: anche se P->M e' inevitabile con un solo "
        "lavoratore, il problema deve restare risolvibile"
    )


# ---------------------------------------------------------------------------
# Bug fix: doppia divisione in bilancia_copertura_giornaliera
# ---------------------------------------------------------------------------

def test_peso_bilancia_copertura_ha_effetto_misurabile():
    """Prima della correzione, la normalizzazione finale del termine
    'bilancia_copertura_giornaliera' divideva due volte per lo stesso
    fattore di scala, schiacciando il segnale quasi a zero: il peso non
    aveva quasi nessun effetto pratico. Verifichiamo che ora un peso alto
    produca uno scarto di surplus (tra M e P, che hanno lo stesso
    fabbisogno nel sample) uguale o minore di un peso basso."""
    dati_basso = get_sample_input()
    dati_basso.parametri_fairness.peso_bilancia_copertura_giornaliera = 1
    dati_basso.parametri_fairness.minimizza_pm_consecutivo = False  # isola l'effetto

    dati_alto = get_sample_input()
    dati_alto.parametri_fairness.peso_bilancia_copertura_giornaliera = 10
    dati_alto.parametri_fairness.minimizza_pm_consecutivo = False

    def _scarto_tasso_mp(risultato, dati):
        minimo = {(f.giorno, f.fascia): f.minimo for f in dati.fabbisogno}
        conteggio = defaultdict(int)
        for a in risultato.assegnazioni:
            conteggio[(a.giorno, a.fascia)] += 1
        tassi = []
        for (g, f), m in minimo.items():
            if m > 0 and f in ("M", "P"):
                surplus = conteggio.get((g, f), 0) - m
                tassi.append(surplus / m)
        return (max(tassi) - min(tassi)) if tassi else 0

    risultato_basso = genera_turni(dati_basso)
    risultato_alto = genera_turni(dati_alto)

    assert risultato_basso.stato in ("feasible", "feasible_con_declassamenti")
    assert risultato_alto.stato in ("feasible", "feasible_con_declassamenti")

    scarto_basso = _scarto_tasso_mp(risultato_basso, dati_basso)
    scarto_alto = _scarto_tasso_mp(risultato_alto, dati_alto)

    # Non pretendiamo che sia sempre strettamente minore (dipende da come
    # il solver risolve i pareggi), ma un margine di tolleranza ampio
    # dovrebbe comunque mostrare che il peso alto non e' peggiore di
    # quello basso — prima della correzione, il peso non aveva alcun
    # effetto pratico indipendentemente dal suo valore.
    assert scarto_alto <= scarto_basso + 0.34, (
        f"Con peso alto lo scarto di tasso M/P dovrebbe essere uguale o "
        f"minore di quello con peso basso: basso={scarto_basso:.2f}, "
        f"alto={scarto_alto:.2f}"
    )


# ---------------------------------------------------------------------------
# Nuovo vincolo: bilancia_proporzione_giornaliera (scarto per singolo giorno)
# ---------------------------------------------------------------------------

def test_bilancia_proporzione_giornaliera_riduce_scarti_per_giorno():
    """A differenza di bilancia_copertura_giornaliera (che minimizza solo
    il caso peggiore dell'intero mese), bilancia_proporzione_giornaliera
    somma lo scarto di OGNI singolo giorno: verifichiamo che con questo
    vincolo attivo, il massimo scarto giornaliero tra M e P (che hanno lo
    stesso fabbisogno nel sample) resti contenuto su tutti i giorni, non
    solo in media."""
    dati = get_sample_input()
    dati.parametri_fairness.bilancia_proporzione_giornaliera = True
    dati.parametri_fairness.peso_bilancia_proporzione_giornaliera = 8

    risultato = genera_turni(dati)
    assert risultato.stato in ("feasible", "feasible_con_declassamenti")

    minimo = {(f.giorno, f.fascia): f.minimo for f in dati.fabbisogno}
    conteggio = defaultdict(int)
    for a in risultato.assegnazioni:
        conteggio[(a.giorno, a.fascia)] += 1

    giorni = list(range(dati.periodo.giorno_inizio, dati.periodo.giorno_fine + 1))
    scarti_giornalieri = []
    for g in giorni:
        m_min = minimo.get((g, "M"), 0)
        p_min = minimo.get((g, "P"), 0)
        if m_min > 0 and p_min > 0:
            tasso_m = (conteggio.get((g, "M"), 0) - m_min) / m_min
            tasso_p = (conteggio.get((g, "P"), 0) - p_min) / p_min
            scarti_giornalieri.append(abs(tasso_m - tasso_p))

    # Soglia larga apposta (controllo di sanita'): senza questo vincolo
    # potevano capitare giorni con scarti anche di 1.5-2.0 (es. 8M/5P su
    # fabbisogno 3+3, ~166 punti percentuali di differenza); con il
    # vincolo attivo e un peso significativo, nessun giorno dovrebbe
    # avvicinarsi a quell'estremo.
    assert max(scarti_giornalieri) <= 1.0, (
        f"Scarto giornaliero M/P troppo alto in almeno un giorno: "
        f"{max(scarti_giornalieri):.2f}"
    )


def test_bilancia_proporzione_giornaliera_funziona_anche_senza_gli_altri():
    """Verifica che il nuovo vincolo non dipenda da variabili definite
    solo dentro bilancia_copertura_giornaliera: deve funzionare anche se
    quest'ultimo e' disattivato (bug di scope da evitare)."""
    dati = get_sample_input()
    dati.parametri_fairness.bilancia_copertura_giornaliera = False
    dati.parametri_fairness.bilancia_proporzione_giornaliera = True

    risultato = genera_turni(dati)
    assert risultato.stato in ("feasible", "feasible_con_declassamenti")


# ---------------------------------------------------------------------------
# Ore settimanali come intervallo [minimo, massimo], non solo massimo
# ---------------------------------------------------------------------------

def test_ore_settimanali_minimo_forza_surplus():
    """w1 ha un minimo di ore settimanali (24h = 3 turni) superiore a
    quanto il solo fabbisogno richiederebbe (1 turno): il motore deve
    assegnargli turni extra (surplus oltre il fabbisogno minimo) per
    raggiungere il suo monte ore minimo contrattuale."""
    dati = InputTurnazione(
        reparto_id="rep_test_ore_minimo",
        categoria="infermieri",
        periodo=Periodo(anno=2026, mese=6, giorno_inizio=1, giorno_fine=7),  # 1 giu 2026 = lunedi'
        lavoratori=[
            Lavoratore(id="w1", nome="Minimo Alto", ore_settimanali_min=24, ore_settimanali_max=36),
            Lavoratore(id="w2", nome="Backup", ore_settimanali_min=0, ore_settimanali_max=36),
        ],
        fabbisogno=[
            Fabbisogno(giorno=1, fascia="M", minimo=1),
        ],
        regole_contrattuali=RegoleContrattuali(),
    )

    risultato = genera_turni(dati)
    assert risultato.stato in ("feasible", "feasible_con_declassamenti")

    ore_per_fascia = {"M": 8, "P": 8, "N": 10}
    ore_w1 = sum(
        ore_per_fascia.get(a.fascia, 0) for a in risultato.assegnazioni if a.lavoratore_id == "w1"
    )
    assert ore_w1 >= 24, f"w1 ha un minimo di 24h ma gliene sono state assegnate solo {ore_w1}"


def test_ore_settimanali_min_uguale_max_forza_valore_esatto():
    """Se minimo e massimo coincidono (16h), le ore settimanali devono
    essere ESATTAMENTE quel valore, non di piu' e non di meno — anche se
    il fabbisogno da solo richiederebbe un solo turno (8h)."""
    dati = InputTurnazione(
        reparto_id="rep_test_ore_fisse",
        categoria="infermieri",
        periodo=Periodo(anno=2026, mese=6, giorno_inizio=1, giorno_fine=7),
        lavoratori=[
            Lavoratore(id="w1", nome="Fisso 16h", ore_settimanali_min=16, ore_settimanali_max=16),
        ],
        fabbisogno=[
            Fabbisogno(giorno=1, fascia="M", minimo=1),
        ],
        regole_contrattuali=RegoleContrattuali(),
    )

    risultato = genera_turni(dati)
    assert risultato.stato == "feasible"

    ore_per_fascia = {"M": 8, "P": 8, "N": 10}
    ore_totali = sum(
        ore_per_fascia.get(a.fascia, 0) for a in risultato.assegnazioni if a.lavoratore_id == "w1"
    )
    assert ore_totali == 16, f"w1 ha min=max=16h ma gliene sono state assegnate {ore_totali}"


def test_ore_settimanali_minimo_irraggiungibile_da_infeasible():
    """Se il minimo settimanale e' fisicamente irraggiungibile (supera le
    ore ottenibili anche lavorando ogni giorno disponibile), il problema
    deve risultare infeasible invece di essere silenziosamente ignorato."""
    dati = InputTurnazione(
        reparto_id="rep_test_ore_minimo_impossibile",
        categoria="infermieri",
        periodo=Periodo(anno=2026, mese=6, giorno_inizio=1, giorno_fine=2),  # solo 2 giorni
        lavoratori=[
            # Minimo 100h in soli 2 giorni: fisicamente impossibile anche
            # lavorando entrambi i giorni su tutte e 3 le fasce.
            Lavoratore(id="w1", nome="Minimo Impossibile", ore_settimanali_min=100, ore_settimanali_max=100),
        ],
        fabbisogno=[
            Fabbisogno(giorno=1, fascia="M", minimo=1),
        ],
        regole_contrattuali=RegoleContrattuali(),
    )

    risultato = genera_turni(dati)
    assert risultato.stato == "infeasible"


# ---------------------------------------------------------------------------
# Giorni di riposo dopo la notte: default 2 (non piu' 1), configurabile
# ---------------------------------------------------------------------------

def test_due_giorni_riposo_dopo_singola_notte_default():
    """Con il default (2 giorni), anche il SECONDO giorno dopo una notte
    resta bloccato per M/P (non solo il primo, come con la vecchia regola
    a 1 giorno). Lo verifichiamo rendendo quel turno obbligatorio per
    l'unico lavoratore disponibile: se il vincolo non fosse esteso a 2
    giorni il problema risulterebbe risolvibile invece che infeasible
    (vedi anche test_giorni_riposo_dopo_notte_configurabile_a_1, stesso
    scenario ma con giorni_riposo_dopo_notte=1, che invece deve restare
    risolvibile — le due prove insieme dimostrano l'effetto esatto del
    parametro)."""
    dati = InputTurnazione(
        reparto_id="rep_test_riposo_2gg",
        categoria="infermieri",
        periodo=Periodo(anno=2026, mese=6, giorno_inizio=1, giorno_fine=3),
        lavoratori=[
            Lavoratore(id="w1", nome="Unico Disponibile", ore_settimanali_min=0, ore_settimanali_max=36),
        ],
        fabbisogno=[
            Fabbisogno(giorno=3, fascia="M", minimo=1),  # secondo giorno dopo la notte
        ],
        vincoli_admin=[
            VincoloAdmin(id="adm1", lavoratore_id="w1", giorno=1, tipo="turno", fascia="N"),
        ],
        regole_contrattuali=RegoleContrattuali(),  # default: giorni_riposo_dopo_notte=2
    )

    risultato = genera_turni(dati)
    assert risultato.stato == "infeasible", (
        "Con il default di 2 giorni di riposo, anche il secondo giorno "
        "dopo la notte (giorno 3) dovrebbe restare bloccato per l'unico "
        "lavoratore disponibile, rendendo impossibile coprire il "
        "fabbisogno di quel giorno"
    )


def test_riposo_dopo_serie_di_notti_si_applica_dopo_l_ultima():
    """Con 2 notti consecutive (giorno 1 e 2) e 2 giorni di riposo di
    default, il riposo deve applicarsi DOPO L'ULTIMA notte (giorno 2),
    quindi i giorni 3 e 4 non possono essere M/P — non dopo ognuna
    singolarmente (altrimenti si bloccherebbe anche il giorno 5, che
    invece deve restare libero di essere M/P).

    Per verificarlo in modo deterministico (senza dover indovinare quale
    lavoratore il motore sceglie), il giorno 5 richiede ENTRAMBI i
    lavoratori su M: se il vincolo si applicasse erroneamente anche li',
    il problema diventerebbe infeasible."""
    dati = InputTurnazione(
        reparto_id="rep_test_riposo_serie",
        categoria="infermieri",
        periodo=Periodo(anno=2026, mese=6, giorno_inizio=1, giorno_fine=5),
        lavoratori=[
            Lavoratore(id="w1", nome="Test Uno", ore_settimanali_min=0, ore_settimanali_max=40),
            Lavoratore(id="w2", nome="Backup", ore_settimanali_min=0, ore_settimanali_max=40),
        ],
        fabbisogno=[
            Fabbisogno(giorno=1, fascia="N", minimo=1),
            Fabbisogno(giorno=2, fascia="N", minimo=1),
            Fabbisogno(giorno=5, fascia="M", minimo=2),  # entrambi obbligatori
        ],
        vincoli_admin=[
            VincoloAdmin(id="adm1", lavoratore_id="w1", giorno=1, tipo="turno", fascia="N"),
            VincoloAdmin(id="adm2", lavoratore_id="w1", giorno=2, tipo="turno", fascia="N"),
        ],
        regole_contrattuali=RegoleContrattuali(),
    )

    risultato = genera_turni(dati)
    assert risultato.stato in ("feasible", "feasible_con_declassamenti"), (
        "Se il giorno 5 fosse erroneamente bloccato per w1 (rest esteso "
        "oltre l'ultima notte), coprire M con entrambi i lavoratori "
        "sarebbe impossibile e il problema risulterebbe infeasible"
    )

    for g in (3, 4):
        assegnazione = next(
            (a.fascia for a in risultato.assegnazioni if a.lavoratore_id == "w1" and a.giorno == g),
            None,
        )
        assert assegnazione not in ("M", "P"), (
            f"w1 ha fatto 2 notti (giorni 1-2): il giorno {g} non dovrebbe "
            f"poter essere M/P"
        )

    assegnazione_g5_w1 = next(
        (a.fascia for a in risultato.assegnazioni if a.lavoratore_id == "w1" and a.giorno == 5),
        None,
    )
    assert assegnazione_g5_w1 == "M", "w1 doveva coprire M il giorno 5 (richiesto da entrambi)"


def test_giorni_riposo_dopo_notte_configurabile_a_1():
    """Verifica che il parametro sia davvero configurabile: con
    giorni_riposo_dopo_notte=1 (comportamento precedente), solo il primo
    giorno dopo la notte resta bloccato — il SECONDO giorno dopo la
    notte torna libero (a differenza del nuovo default 2, che lo
    bloccherebbe anch'esso)."""
    dati = InputTurnazione(
        reparto_id="rep_test_riposo_1gg",
        categoria="infermieri",
        periodo=Periodo(anno=2026, mese=6, giorno_inizio=1, giorno_fine=3),
        lavoratori=[
            Lavoratore(id="w1", nome="Test Uno", ore_settimanali_min=0, ore_settimanali_max=36),
        ],
        fabbisogno=[
            Fabbisogno(giorno=3, fascia="M", minimo=1),  # secondo giorno dopo la notte
        ],
        vincoli_admin=[
            VincoloAdmin(id="adm1", lavoratore_id="w1", giorno=1, tipo="turno", fascia="N"),
        ],
        regole_contrattuali=RegoleContrattuali(giorni_riposo_dopo_notte=1),
    )

    risultato = genera_turni(dati)
    assert risultato.stato == "feasible", (
        "Con giorni_riposo_dopo_notte=1, il giorno 3 (secondo dopo la "
        "notte) dovrebbe essere libero per w1 (unico lavoratore): se il "
        "problema risulta infeasible, il parametro non sta riducendo "
        "correttamente la finestra di riposo"
    )

    assegnazione_g3 = next(
        (a.fascia for a in risultato.assegnazioni if a.lavoratore_id == "w1" and a.giorno == 3),
        None,
    )
    assert assegnazione_g3 == "M", (
        "Con giorni_riposo_dopo_notte=1, il giorno 3 dovrebbe poter essere M"
    )


def test_niente_notte_isolata_dopo_solo_1_giorno_di_pausa():
    """Bug di regressione trovato in produzione: il pattern 'notte, 1
    giorno di pausa, notte' passava inosservato perche' il vincolo
    bloccava solo M/P dopo la notte, mai un'altra N. Con 2 giorni di
    riposo richiesti, una notte isolata (non parte di una serie) seguita
    da una SOLA pausa e poi un'altra notte viola il vero requisito di 2
    giorni di riposo pieno. Verifichiamo rendendo la seconda notte
    obbligatoria per l'unico lavoratore disponibile: deve risultare
    infeasible."""
    dati = InputTurnazione(
        reparto_id="rep_test_notte_isolata_pausa_breve",
        categoria="infermieri",
        periodo=Periodo(anno=2026, mese=6, giorno_inizio=1, giorno_fine=3),
        lavoratori=[
            Lavoratore(id="w1", nome="Unico Disponibile", ore_settimanali_min=0, ore_settimanali_max=36),
        ],
        fabbisogno=[
            Fabbisogno(giorno=3, fascia="N", minimo=1),  # solo 1 giorno di pausa dopo la notte del giorno 1
        ],
        vincoli_admin=[
            VincoloAdmin(id="adm1", lavoratore_id="w1", giorno=1, tipo="turno", fascia="N"),
        ],
        regole_contrattuali=RegoleContrattuali(),  # default: giorni_riposo_dopo_notte=2
    )

    risultato = genera_turni(dati)
    assert risultato.stato == "infeasible", (
        "Notte isolata il giorno 1 seguita da una sola pausa (giorno 2) "
        "non dovrebbe permettere un'altra notte il giorno 3: servono 2 "
        "giorni pieni di riposo, non solo l'assenza di M/P"
    )


def test_due_notti_consecutive_poi_pausa_singola_non_permessa():
    """Stesso bug ma dopo una serie di 2 notti consecutive (non isolata):
    con 2 notti di fila e 2 giorni di riposo richiesti, una terza notte
    dopo una sola pausa deve restare vietata."""
    dati = InputTurnazione(
        reparto_id="rep_test_serie_poi_pausa_breve",
        categoria="infermieri",
        periodo=Periodo(anno=2026, mese=6, giorno_inizio=1, giorno_fine=4),
        lavoratori=[
            Lavoratore(id="w1", nome="Unico Disponibile", ore_settimanali_min=0, ore_settimanali_max=40),
        ],
        fabbisogno=[
            Fabbisogno(giorno=4, fascia="N", minimo=1),  # solo 1 giorno di pausa dopo la serie
        ],
        vincoli_admin=[
            VincoloAdmin(id="adm1", lavoratore_id="w1", giorno=1, tipo="turno", fascia="N"),
            VincoloAdmin(id="adm2", lavoratore_id="w1", giorno=2, tipo="turno", fascia="N"),
        ],
        regole_contrattuali=RegoleContrattuali(),
    )

    risultato = genera_turni(dati)
    assert risultato.stato == "infeasible", (
        "Serie di 2 notti (giorni 1-2) seguita da una sola pausa (giorno "
        "3) non dovrebbe permettere un'altra notte il giorno 4"
    )


# ---------------------------------------------------------------------------
# Settimane parziali: niente piu' proporzione automatica del minimo —
# la situazione iniziale (compilata con dati veri) e' ora l'unico modo
# corretto di gestirle
# ---------------------------------------------------------------------------

def test_settimana_parziale_senza_situazione_iniziale_puo_essere_infeasible():
    """Luglio 2026 inizia di mercoledi': la prima settimana del periodo
    ha solo 5 giorni disponibili (1-5 luglio), non 7. Un lavoratore che fa
    una notte il primo giorno (10h + 2 giorni di riposo pieno + al
    massimo 2 giorni residui = 26h max ottenibili nel periodo) non puo'
    raggiungere un minimo di 32h SENZA situazione iniziale — a
    differenza della versione precedente (proporzione automatica), ora
    questo e' un infeasible corretto: manca l'informazione su cosa il
    lavoratore ha gia' fatto nei giorni immediatamente prima, non un bug
    da compensare abbassando il vincolo."""
    dati = InputTurnazione(
        reparto_id="rep_test_no_prorata_senza_si",
        categoria="infermieri",
        periodo=Periodo(anno=2026, mese=7, giorno_inizio=1, giorno_fine=5),
        lavoratori=[
            Lavoratore(id="w1", nome="Test Uno", ore_settimanali_min=32, ore_settimanali_max=40),
        ],
        fabbisogno=[
            Fabbisogno(giorno=1, fascia="N", minimo=1),
        ],
        vincoli_admin=[
            VincoloAdmin(id="adm1", lavoratore_id="w1", giorno=1, tipo="turno", fascia="N"),
        ],
        regole_contrattuali=RegoleContrattuali(),
    )

    risultato = genera_turni(dati)
    assert risultato.stato == "infeasible", (
        "Senza situazione iniziale, un lavoratore che fa notte il primo "
        "giorno puo' ottenere al massimo 26h in questa settimana corta, "
        "sotto il minimo di 32h: deve risultare infeasible (nessuna "
        "proporzione automatica a mascherare il problema)"
    )


def test_settimana_parziale_con_situazione_iniziale_vera_diventa_feasible():
    """Stesso identico scenario del test sopra, ma con situazione
    iniziale compilata (8h gia' lavorate il 30 giugno, che cade nella
    stessa settimana ISO 27 del periodo): 8h pregresse + 26h max
    ottenibili nel periodo = 34h, sufficienti a superare il minimo di
    32h senza bisogno di nessuna proporzione — la soluzione corretta al
    problema della settimana corta e' compilare la situazione iniziale
    con dati veri, non abbassare artificialmente il vincolo."""
    dati = InputTurnazione(
        reparto_id="rep_test_no_prorata_con_si",
        categoria="infermieri",
        periodo=Periodo(anno=2026, mese=7, giorno_inizio=1, giorno_fine=5),
        lavoratori=[
            Lavoratore(id="w1", nome="Test Uno", ore_settimanali_min=32, ore_settimanali_max=40),
        ],
        fabbisogno=[
            Fabbisogno(giorno=1, fascia="N", minimo=1),
        ],
        vincoli_admin=[
            VincoloAdmin(id="adm1", lavoratore_id="w1", giorno=1, tipo="turno", fascia="N"),
        ],
        stato_iniziale=[
            # 30 giugno 2026 cade nella stessa settimana ISO 27 del
            # periodo (che inizia mercoledi' 1 luglio): queste 8 ore si
            # sommano correttamente al totale della settimana.
            StatoIniziale(lavoratore_id="w1", giorno=30, fascia="M", mese_precedente=True),
        ],
        regole_contrattuali=RegoleContrattuali(),
    )

    risultato = genera_turni(dati)
    assert risultato.stato in ("feasible", "feasible_con_declassamenti"), (
        "Con 8h di situazione iniziale vera (30 giugno, stessa settimana "
        "ISO del periodo) + fino a 26h ottenibili nel periodo = 34h, il "
        "minimo di 32h dovrebbe essere raggiungibile senza bisogno di "
        "nessuna proporzione automatica"
    )


# ---------------------------------------------------------------------------
# Regressione: vincolo admin vicino all'inizio periodo + notti pregresse
# ---------------------------------------------------------------------------

def test_vincolo_admin_vicino_a_notti_pregresse_puo_essere_infeasible():
    """Bug scoperto in produzione (causa isolata in sample_data.py, dove
    un vincolo admin "turno forzato" era troppo vicino all'inizio del
    periodo rispetto a una situazione iniziale con notti pregresse).

    Se un lavoratore ha gia' esaurito il margine di notti consecutive con
    notti pregresse (es. 2 notti su un massimo di 2), e un vincolo admin
    forza un turno M/P troppo vicino all'inizio del periodo (dentro la
    finestra di riposo dovuta), il motore si trova in un vicolo cieco:
    - se NON continua la serie di notti, scatta il riposo pieno (blocca
      il turno forzato)
    - se continua la serie (per evitare il riposo), supera il massimo
      notti consecutive

    Nessuna delle due strade funziona: infeasible per costruzione, non
    per mancanza di capacita'."""
    dati = InputTurnazione(
        reparto_id="rep_test_conflitto_pregresse_admin",
        categoria="infermieri",
        periodo=Periodo(anno=2026, mese=7, giorno_inizio=1, giorno_fine=3),
        lavoratori=[
            Lavoratore(id="w1", nome="Test Uno", ore_settimanali_min=0, ore_settimanali_max=40),
        ],
        fabbisogno=[],
        vincoli_admin=[
            VincoloAdmin(id="adm1", lavoratore_id="w1", giorno=2, tipo="turno", fascia="M"),
        ],
        stato_iniziale=[
            StatoIniziale(lavoratore_id="w1", giorno=29, fascia="N", mese_precedente=True),
            StatoIniziale(lavoratore_id="w1", giorno=30, fascia="N", mese_precedente=True),
        ],
        regole_contrattuali=RegoleContrattuali(),  # max_notti_consecutive=2, giorni_riposo_dopo_notte=2
    )

    risultato = genera_turni(dati)
    assert risultato.stato == "infeasible", (
        "w1 ha gia' esaurito il margine di notti consecutive (2 pregresse "
        "su un massimo di 2) e ha un turno M forzato il giorno 2: sia "
        "continuare la serie (vietato dal massimo notti consecutive) sia "
        "fermarsi (il riposo dovuto blocca il turno forzato) portano a un "
        "vicolo cieco"
    )


# ---------------------------------------------------------------------------
# Turni con minuti frazionari (non solo ore intere)
# ---------------------------------------------------------------------------

def test_turni_con_minuti_rispettano_il_massimo_ore():
    """Verifica che un turno di 7h30m (450 minuti, non un numero intero di
    ore) venga contato correttamente nel vincolo di ore settimanali: con
    un massimo di 36h (2160 minuti), 4 turni da 7h30m fanno 30h
    (1800 minuti, ammissibile), un quinto turno supererebbe le 36h — con
    un lavoratore di riserva disponibile, il motore deve quindi limitare
    w1 a 4 turni al massimo e far coprire il quinto giorno a w2."""
    dati = InputTurnazione(
        reparto_id="rep_test_minuti_frazionari",
        categoria="infermieri",
        periodo=Periodo(anno=2026, mese=6, giorno_inizio=1, giorno_fine=7),  # 1 giu 2026 = lunedi'
        lavoratori=[
            Lavoratore(id="w1", nome="Test Uno", ore_settimanali_min=0, ore_settimanali_max=36),
            Lavoratore(id="w2", nome="Backup", ore_settimanali_min=0, ore_settimanali_max=36),
        ],
        fabbisogno=[
            Fabbisogno(giorno=g, fascia="M", minimo=1) for g in range(1, 6)  # 5 giorni, minimo 1 M/giorno
        ],
        regole_contrattuali=RegoleContrattuali(
            minuti_per_fascia={"M": 450, "P": 480, "N": 600},  # M = 7h30m
        ),
    )

    risultato = genera_turni(dati)
    assert risultato.stato == "feasible"

    turni_w1 = [a for a in risultato.assegnazioni if a.lavoratore_id == "w1" and a.fascia == "M"]
    minuti_totali = len(turni_w1) * 450
    assert minuti_totali <= 36 * 60, (
        f"w1 ha {len(turni_w1)} turni da 7h30m = {minuti_totali} minuti, "
        f"supera le 36h (2160 minuti) contrattuali"
    )
    # Con turni da 7h30m e un massimo di 36h, non dovrebbero essere
    # possibili piu' di 4 turni in una settimana per lo stesso lavoratore
    # (4*7h30m=30h, 5*7h30m=37h30m supererebbe il massimo) — col backup
    # disponibile, il motore ha la flessibilita' di rispettarlo davvero.
    assert len(turni_w1) <= 4, (
        "Con turni da 7h30m e un massimo di 36h, non dovrebbero essere "
        "possibili piu' di 4 turni in una settimana per lo stesso lavoratore"
    )


def test_ferie_giornaliere_con_minuti_frazionari():
    """Verifica che ore_ferie_giornaliere con minuti (es. 7h45m = 465
    minuti) venga aggiunto correttamente al monte ore quando una ferie
    forzata dall'admin si combina con turni regolari, e che il totale
    non superi mai il massimo settimanale — qualunque sia la
    distribuzione scelta dal motore (con un lavoratore di riserva
    disponibile, il motore ha la flessibilita' di rispettarlo davvero,
    a differenza di un singolo lavoratore forzato su tutta la
    copertura)."""
    dati = InputTurnazione(
        reparto_id="rep_test_ferie_minuti",
        categoria="infermieri",
        periodo=Periodo(anno=2026, mese=6, giorno_inizio=1, giorno_fine=7),
        lavoratori=[
            Lavoratore(id="w1", nome="Test Uno", ore_settimanali_min=0, ore_settimanali_max=36),
            Lavoratore(id="w2", nome="Backup", ore_settimanali_min=0, ore_settimanali_max=36),
        ],
        fabbisogno=[
            Fabbisogno(giorno=g, fascia="M", minimo=1) for g in range(1, 6)
        ],
        vincoli_admin=[
            VincoloAdmin(id="adm1", lavoratore_id="w1", giorno=1, tipo="ferie"),
        ],
        regole_contrattuali=RegoleContrattuali(
            minuti_per_fascia={"M": 480, "P": 480, "N": 600},  # 8h
            minuti_ferie_giornaliere=465,  # 7h45m
        ),
    )

    risultato = genera_turni(dati)
    assert risultato.stato == "feasible"

    # w1 ha ferie il giorno 1 (465 minuti virtuali fissi) + eventuali
    # turni M sui giorni 2-5: il totale non deve mai superare 36h (2160
    # minuti), qualunque sia il numero di turni che il motore gli assegna.
    turni_w1 = [a for a in risultato.assegnazioni if a.lavoratore_id == "w1" and a.fascia == "M"]
    minuti_totali = 465 + len(turni_w1) * 480
    assert minuti_totali <= 36 * 60, (
        f"w1: 465 minuti ferie + {len(turni_w1)} turni da 480 minuti = "
        f"{minuti_totali} minuti, supera le 36h (2160 minuti)"
    )
    # Verifica indiretta che i minuti di ferie siano stati davvero
    # contati (non ignorati): con budget residuo di 1695 minuti dopo la
    # ferie, al massimo 3 turni da 480 minuti sono ammissibili per w1
    # (1695 // 480 == 3) — se il vincolo ignorasse i minuti di ferie,
    # sarebbero ammissibili fino a 4 turni (2160 // 480 == 4).
    assert len(turni_w1) <= 3, (
        f"w1 ha {len(turni_w1)} turni M oltre alla ferie da 465 minuti: "
        "se i minuti di ferie fossero conteggiati correttamente, non "
        "dovrebbero essere possibili piu' di 3 turni (budget residuo "
        "1695 minuti // 480 = 3)"
    )
