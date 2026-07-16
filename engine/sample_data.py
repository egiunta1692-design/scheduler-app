"""
Caso di esempio: 20 infermieri, reparto fittizio, un mese intero (luglio
2026, esteso automaticamente fino alla domenica di chiusura settimana).
Serve per testare il motore in isolamento, senza passare da JSON o Streamlit,
ed e' anche il dataset di default usato dall'app Streamlit al primo avvio:
per questo copre l'intero periodo che Streamlit mostrerebbe di default,
cosi' la griglia fabbisogno non appare "vuota" oltre un certo giorno.
"""

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
        # Esempio: turno forzato dal coordinatore (es. sostituzione dell'ultimo minuto)
        VincoloAdmin(id="adm2", lavoratore_id="w4", giorno=2, tipo="turno", fascia="M"),
    ]

    richieste_soft = [
        # Esempio: richiesta ferie con priorita' alta ma non obbligatoria
        RichiestaSoft(id="req1", lavoratore_id="w2", giorno=10, tipo="ferie", priorita=4),
        RichiestaSoft(id="req2", lavoratore_id="w3", giorno=3, tipo="turno", fascia="M", priorita=2),
    ]

    stato_iniziale = [
        # Esempio: w5 ha fatto notte l'ultimo giorno di giugno -> il motore
        # deve impedirgli M/P il 1 luglio (riposo dopo notte a cavallo di mese)
        # e conteggiare quelle 8 ore nella settimana ISO corrispondente se
        # a cavallo con la prima settimana del periodo.
        StatoIniziale(lavoratore_id="w5", giorno=30, fascia="N", mese_precedente=True),
    ]

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
