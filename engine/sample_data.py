"""
Caso di esempio: 20 infermieri, reparto fittizio, un mese intero (luglio
2026, esteso automaticamente fino alla domenica di chiusura settimana).
Serve per testare il motore in isolamento, senza passare da JSON o Streamlit,
ed e' anche il dataset di default usato dall'app Streamlit al primo avvio:
per questo copre l'intero periodo che Streamlit mostrerebbe di default,
cosi' la griglia fabbisogno non appare "vuota" oltre un certo giorno.
"""

import datetime

from engine.models import (
    InputTurnazione,
    Periodo,
    Lavoratore,
    Fabbisogno,
    VincoloAdmin,
    RichiestaSoft,
    RegoleContrattuali,
    ParametriFairness,
    StatoIniziale,
)
from engine.periodo_utils import calcola_giorno_fine_periodo


NOMI_LAVORATORI = [
    "Rossi Mario", "Bianchi Anna", "Verdi Luca", "Neri Sara",
    "Gialli Elena", "Blu Marco", "Russo Chiara", "Ferrari Davide",
    "Esposito Giulia", "Romano Matteo", "Colombo Francesca", "Ricci Alessandro",
    "Marino Valentina", "Greco Simone", "Bruno Federica", "Gallo Riccardo",
    "Conti Martina", "De Luca Andrea", "Costa Silvia", "Giordano Paolo",
]

ANNO_DEMO = 2026
MESE_DEMO = 7  # luglio 2026: finisce venerdi' 31 -> periodo esteso a domenica 2 agosto

# Ciclo di default (3 giorni: M, P, riposo) per generare una situazione
# iniziale plausibile. NIENTE NOTTI nel ciclo di default: una situazione
# iniziale con notti pregresse puo' lasciare alcuni lavoratori "bloccati"
# dal riposo dovuto proprio nei primi giorni del periodo (con margine
# insufficiente a coprire le notti richieste dal fabbisogno li' — bug
# scoperto verificando numericamente il caso reale: con notti nel ciclo,
# solo 8 lavoratori su 20 risultavano liberi e con credito sufficiente,
# contro le 10 notti richieste nella prima settimana). Con solo M/P,
# NESSUN lavoratore e' mai bloccato dal riposo, e il credito minimo (8h)
# e' sempre sufficiente a rendere raggiungibile il minimo contrattuale
# nella prima settimana corta — molto piu' robusto che affidarsi a un
# ciclo con notti che deve "azzeccare" l'equilibrio esatto.
_CICLO_SITUAZIONE_INIZIALE = ["M", "P", "riposo"]


def _genera_stato_iniziale_demo(lavoratori_ids: list[str], anno: int, mese: int) -> list[StatoIniziale]:
    n_ciclo = len(_CICLO_SITUAZIONE_INIZIALE)
    primo_giorno_mese = datetime.date(anno, mese, 1)
    # Come in app.py: minimo 4 giorni, esteso se serve a coprire l'intera
    # settimana calendario su cui il mese inizia.
    giorni_necessari = max(4, primo_giorno_mese.isoweekday() - 1)

    entries: list[StatoIniziale] = []
    for indice_lav, lavoratore_id in enumerate(lavoratori_ids):
        offset = indice_lav % n_ciclo
        for j in range(giorni_necessari):
            # j=0 e' il giorno piu' vecchio, l'ultimo e' il piu' recente
            # (immediatamente prima del periodo) — stesso allineamento
            # usato in app.py.
            posizione = (offset - (giorni_necessari - 1 - j)) % n_ciclo
            valore = _CICLO_SITUAZIONE_INIZIALE[posizione]
            if valore == "riposo":
                continue  # nessuna voce = implicitamente non lavorato
            data_si = primo_giorno_mese - datetime.timedelta(days=giorni_necessari - j)
            entries.append(
                StatoIniziale(
                    lavoratore_id=lavoratore_id, giorno=data_si.day, fascia=valore, mese_precedente=True
                )
            )
    return entries


def get_sample_input() -> InputTurnazione:
    lavoratori = [
        Lavoratore(id=f"w{i+1}", nome=nome, ore_settimanali_min=32, ore_settimanali_max=40)
        for i, nome in enumerate(NOMI_LAVORATORI)
    ]

    giorno_fine = calcola_giorno_fine_periodo(ANNO_DEMO, MESE_DEMO)

    # Fabbisogno costante per tutto il periodo (compresi gli eventuali
    # giorni di sconfinamento nel mese successivo): 3 Mattino + 3 Pomeriggio
    # + 2 Notte al giorno. Con 20 lavoratori e max_ore_settimanali=36
    # (=4 turni/settimana a persona), la capacita' totale e' 20*4=80
    # turni/settimana, ben oltre la domanda settimanale di 8 turni/giorno*7=56:
    # margine ampio per richieste soft, vincoli admin e fairness.
    fabbisogno = []
    for giorno in range(1, giorno_fine + 1):
        fabbisogno.append(Fabbisogno(giorno=giorno, fascia="M", minimo=3))
        fabbisogno.append(Fabbisogno(giorno=giorno, fascia="P", minimo=3))
        fabbisogno.append(Fabbisogno(giorno=giorno, fascia="N", minimo=2))

    vincoli_admin = [
        # Esempio: ferie forzata dal coordinatore
        VincoloAdmin(id="adm1", lavoratore_id="w1", giorno=5, tipo="ferie"),
        # Esempio: turno forzato dal coordinatore (es. sostituzione dell'ultimo
        # minuto). Giorno 10 (non un giorno vicino all'inizio del periodo):
        # i primi giorni del periodo sono spesso condizionati dal riposo
        # dovuto a eventuali notti nella situazione iniziale generata
        # automaticamente in app.py, quindi un vincolo forzato proprio li'
        # rischierebbe di entrare in conflitto con quel riposo a seconda
        # del pattern che tocca a w4.
        VincoloAdmin(id="adm2", lavoratore_id="w4", giorno=10, tipo="turno", fascia="M"),
    ]

    richieste_soft = [
        # Esempio: richiesta ferie con priorita' alta ma non obbligatoria
        RichiestaSoft(id="req1", lavoratore_id="w2", giorno=10, tipo="ferie", priorita=4),
        RichiestaSoft(id="req2", lavoratore_id="w3", giorno=3, tipo="turno", fascia="M", priorita=2),
    ]

    # Situazione iniziale generata con lo stesso ciclo sicuro usato in
    # app.py (vedi _genera_stato_iniziale_demo sopra): necessaria perche'
    # il minimo ore settimanali non viene piu' proporzionato per le
    # settimane parziali — la situazione iniziale compilata (qui,
    # generata) e' l'unico modo corretto di rendere raggiungibile il
    # minimo nella prima settimana corta del periodo.
    stato_iniziale = _genera_stato_iniziale_demo(
        [l.id for l in lavoratori], ANNO_DEMO, MESE_DEMO
    )

    return InputTurnazione(
        reparto_id="rep_demo",
        categoria="infermieri",
        periodo=Periodo(anno=ANNO_DEMO, mese=MESE_DEMO, giorno_inizio=1, giorno_fine=giorno_fine),
        lavoratori=lavoratori,
        fabbisogno=fabbisogno,
        vincoli_admin=vincoli_admin,
        richieste_soft=richieste_soft,
        regole_contrattuali=RegoleContrattuali(),
        parametri_fairness=ParametriFairness(),
        stato_iniziale=stato_iniziale,
    )
