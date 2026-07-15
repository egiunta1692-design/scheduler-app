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
            Lavoratore(id="w1", nome="Test Uno", ore_settimanali_contratto=36),
            Lavoratore(id="w2", nome="Test Due", ore_settimanali_contratto=36),
            Lavoratore(id="w3", nome="Test Tre", ore_settimanali_contratto=36),
            Lavoratore(id="w4", nome="Test Quattro", ore_settimanali_contratto=36),
            # Quinto lavoratore aggiunto per mantenere margine di capacita':
            # w1 ha 8 ore gia' "consumate" nella settimana a cavallo (notte
            # del 30/06) e con soli 4 lavoratori la capacita' settimanale
            # coinciderebbe esattamente con la domanda, rendendo il test
            # troppo fragile.
            Lavoratore(id="w5", nome="Test Cinque", ore_settimanali_contratto=36),
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
            Lavoratore(id="w1", nome="Test Sature", ore_settimanali_contratto=36),
            Lavoratore(id="w2", nome="Test Backup 1", ore_settimanali_contratto=36),
            Lavoratore(id="w3", nome="Test Backup 2", ore_settimanali_contratto=36),
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

    ore_per_fascia = dati.regole_contrattuali.ore_per_fascia
    ore_per_settimana = defaultdict(int)

    for a in risultato.assegnazioni:
        data = data_da_indice_periodo(dati.periodo.anno, dati.periodo.mese, a.giorno)
        _, settimana_iso, _ = data.isocalendar()
        chiave = (a.lavoratore_id, settimana_iso)
        ore_per_settimana[chiave] += ore_per_fascia.get(a.fascia, 0)

    lavoratori_per_id = {l.id: l for l in dati.lavoratori}
    for (w, _settimana), ore in ore_per_settimana.items():
        max_ore = lavoratori_per_id[w].ore_settimanali_contratto
        assert ore <= max_ore


# ---------------------------------------------------------------------------
# STEP 4: vincoli admin di tipo "turno" forzato
# ---------------------------------------------------------------------------

def test_vincolo_admin_turno_forzato_rispettato():
    dati = get_sample_input()
    risultato = genera_turni(dati)
    assert risultato.stato in ("feasible", "feasible_con_declassamenti")

    # adm2: w4 deve avere M il giorno 2, imposto dal coordinatore
    fascia_w4_giorno2 = next(
        (a.fascia for a in risultato.assegnazioni if a.lavoratore_id == "w4" and a.giorno == 2),
        None,
    )
    assert fascia_w4_giorno2 == "M"


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
            Lavoratore(id="w1", nome="Unico Lavoratore", ore_settimanali_contratto=36),
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
            Lavoratore(id=f"w{i}", nome=f"Test {i}", ore_settimanali_contratto=36)
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
# Vincoli personali per-lavoratore: mai_notti e ore_settimanali_contratto
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
                id="w1", nome="Mai Notti", ore_settimanali_contratto=36,
                vincoli_personali=VincoliPersonali(mai_notti=True),
            ),
            Lavoratore(id="w2", nome="Test Due", ore_settimanali_contratto=36),
            Lavoratore(id="w3", nome="Test Tre", ore_settimanali_contratto=36),
            Lavoratore(id="w4", nome="Test Quattro", ore_settimanali_contratto=36),
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
    """Due lavoratori con ore_settimanali_contratto diverse (0 e 36) sullo
    stesso giorno: quello con 0 ore non deve MAI lavorare (verifica che il
    parametro sia rispettato per singolo lavoratore, senza fallback
    silenzioso su un default globale quando il valore e' 0)."""
    dati = InputTurnazione(
        reparto_id="rep_test_ore_zero",
        categoria="infermieri",
        periodo=Periodo(anno=2026, mese=7, giorno_inizio=1, giorno_fine=1),
        lavoratori=[
            Lavoratore(id="w1", nome="Contratto Zero", ore_settimanali_contratto=0),
            Lavoratore(id="w2", nome="Contratto Normale", ore_settimanali_contratto=36),
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
        "w1 ha ore_settimanali_contratto=0 ma gli e' stato assegnato un turno: "
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

    ore_per_fascia = dati.regole_contrattuali.ore_per_fascia
    ore_per_settimana_lavoratore = defaultdict(lambda: defaultdict(int))

    for a in risultato.assegnazioni:
        data = data_da_indice_periodo(dati.periodo.anno, dati.periodo.mese, a.giorno)
        chiave = data.isocalendar()[:2]
        ore_per_settimana_lavoratore[chiave][a.lavoratore_id] += ore_per_fascia.get(a.fascia, 0)

    lavoratori_ids = [l.id for l in dati.lavoratori]

    for chiave, ore_per_lavoratore in ore_per_settimana_lavoratore.items():
        ore_complete = [ore_per_lavoratore.get(w, 0) for w in lavoratori_ids]
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
            Lavoratore(id="w1", nome="Unico Disponibile", ore_settimanali_contratto=36),
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
            Lavoratore(id="w1", nome="Unico Disponibile", ore_settimanali_contratto=36),
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
            Lavoratore(id="w1", nome="Test Uno", ore_settimanali_contratto=36),
            Lavoratore(id="w2", nome="Test Due", ore_settimanali_contratto=36),
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
            Lavoratore(id="w1", nome="Test Uno", ore_settimanali_contratto=36),
            Lavoratore(id="w2", nome="Test Due", ore_settimanali_contratto=36),
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
