"""
Interfaccia Streamlit per il motore di turnazione.

Layout (schede, in quest'ordine anche nel codice — vedi nota piu' sotto
su "with tab_regole:"):

  1. Regole & periodo: numero di lavoratori (aggiunge/rimuove righe in
     "Lavoratori"), anno/mese (i giorni del periodo si calcolano da soli
     in base al mese), regole contrattuali (notti consecutive, ore per
     fascia M/P/N, ore ferie giornaliere), parametri di fairness.

  2. Lavoratori: tabella editabile con id/nome/ore contratto/mai notti.

  3. Calendario: due griglie.
     - Fabbisogno: righe M/P/N, valori numerici, colonne = giorni del
       periodo (esteso se necessario fino alla domenica di chiusura).
     - Situazione iniziale + Richieste soft + Vincoli admin: griglia
       UNICA per lavoratore. Le prime colonne (giorni del mese
       precedente) registrano i turni gia' effettuati; le colonne del
       periodo accettano un codice breve per cella che rappresenta o
       una richiesta soft del lavoratore (ferie/riposo/turno, con
       priorita') o un vincolo imposto dal coordinatore — mai entrambi
       sulla stessa cella, mutuamente esclusivi per costruzione. Include
       anche: esporta/importa CSV, svuota celle in blocco per lavoratore
       o giorno (con multiselezione e "seleziona tutti").

Dopo "Genera turni": schema turni colorato (con FERIE/RIPOSO distinti),
indicazione se l'ottimalita' e' stata dimostrata o solo il tempo massimo
esaurito, copertura effettiva vs fabbisogno, pulsante per ricaricare il
risultato come vincoli e modificarlo, tabella "Turni per lavoratore"
(M/P/N/Ferie/ore, per mese e per settimana), richieste non soddisfatte,
grafico di equilibrio del carico.

Avvio: streamlit run app.py
"""

import calendar
import datetime
import threading
import time
from collections import defaultdict

import streamlit as st
import pandas as pd
import altair as alt

from engine.models import (
    InputTurnazione,
    Periodo,
    Lavoratore,
    VincoliPersonali,
    Fabbisogno,
    VincoloAdmin,
    RichiestaSoft,
    RegoleContrattuali,
    ParametriFairness,
    StatoIniziale,
)
from engine.solver import genera_turni
from engine.sample_data import get_sample_input
from engine.periodo_utils import (
    calcola_giorno_fine_periodo,
    data_da_indice_periodo,
    data_da_indice_mese_precedente,
)


st.set_page_config(page_title="Turnazione reparto", layout="wide")

COLORI_FASCIA = {
    "M": "#FFE9A8",
    "P": "#A8D8FF",
    "N": "#C9A8FF",
    "FERIE": "#D3D3D3",
    "RIPOSO": "#EAEAEA",
}

# ---------------------------------------------------------------------------
# Codici brevi per la griglia richieste/vincoli (una cella = un codice)
#   ""              -> niente
#   F1..F4          -> richiesta FERIE, priorita' 1 (bassa) - 4 (molto alta)
#   R1..R4          -> richiesta RIPOSO, priorita' 1 (bassa) - 4 (molto alta)
#   M1..M4/P1..P4/N1..N4 -> richiesta TURNO fascia+priorita'
#   AF              -> vincolo ADMIN: ferie forzata
#   AR              -> vincolo ADMIN: riposo forzato
#   AM / AP / AN    -> vincolo ADMIN: turno forzato in quella fascia
#                      (per le colonne del mese precedente, AM/AP/AN indicano
#                      invece un turno GIA' effettuato: concettualmente e'
#                      lo stesso concetto di "assegnazione hard", solo che
#                      e' gia' un fatto avvenuto invece di un'imposizione
#                      per il futuro)
#
# FERIE vs RIPOSO: bloccano entrambe i turni quel giorno allo stesso modo,
# ma FERIE aggiunge ore virtuali al monte ore settimanale (e' comunque
# tempo retribuito), RIPOSO no. Inoltre non e' mai possibile avere ferie
# il giorno subito dopo una notte (o serie di notti): quel giorno di stop
# e' un riposo fisiologico obbligatorio, non puo' essere "sostituito" da
# una ferie — il motore lo impedisce comunque anche se inserito per errore.
# ---------------------------------------------------------------------------
PRIORITA_LABEL = {1: "bassa", 2: "media", 3: "alta", 4: "molto alta"}

# Preset di pesi per i vincoli soft di fairness. Ogni vincolo ha un peso
# individuale (non piu' un unico peso condiviso): un peso condiviso
# penalizzava tutti i vincoli nella stessa proporzione, impedendo di dare
# piu' importanza a uno specifico senza alterare anche gli altri. Tutti i
# valori restano sotto 10 (il peso di una richiesta soft di priorita' 2),
# cosi' anche la preferenza piu' debole di un lavoratore continua a
# prevalere su qualunque combinazione di fairness.
PRESET_FAIRNESS = {
    "Equilibrio reparto (consigliato)": {
        "peso_bilancia_fasce": 7,
        "peso_bilancia_giorni_settimana": 4,
        "peso_bilancia_ore_settimanali": 4,
        "peso_bilancia_copertura_giornaliera": 7,
        "peso_minimizza_pm_consecutivo": 2,
        "peso_bilancia_proporzione_giornaliera": 6,
    },
    "Benessere lavoratori": {
        "peso_bilancia_fasce": 4,
        "peso_bilancia_giorni_settimana": 4,
        "peso_bilancia_ore_settimanali": 6,
        "peso_bilancia_copertura_giornaliera": 3,
        "peso_minimizza_pm_consecutivo": 6,
        "peso_bilancia_proporzione_giornaliera": 4,
    },
    "Leggero": {
        "peso_bilancia_fasce": 1,
        "peso_bilancia_giorni_settimana": 1,
        "peso_bilancia_ore_settimanali": 1,
        "peso_bilancia_copertura_giornaliera": 1,
        "peso_minimizza_pm_consecutivo": 1,
        "peso_bilancia_proporzione_giornaliera": 1,
    },
}
CHIAVI_PESI_FAIRNESS = list(PRESET_FAIRNESS["Equilibrio reparto (consigliato)"].keys())

# Preset per la durata dei turni (Mattino/Pomeriggio/Notte), stesso
# pattern di PRESET_FAIRNESS sopra: un selectbox con on_change scrive
# direttamente nei session_state key dei number_input sottostanti.
# Le ferie NON sono incluse nel preset (concettualmente indipendenti
# dalla "filosofia" di durata turno: restano sempre modificabili a
# parte).
PRESET_DURATA_TURNI = {
    "Standard (8h / 8h / 10h)": {
        "ore_M": 8, "minuti_M": 0, "ore_P": 8, "minuti_P": 0, "ore_N": 10, "minuti_N": 0,
    },
    "Turni da 7h30 (7h30 / 7h30 / 9h30)": {
        "ore_M": 7, "minuti_M": 30, "ore_P": 7, "minuti_P": 30, "ore_N": 9, "minuti_N": 30,
    },
    "Turni lunghi (12h / 12h / 12h)": {
        "ore_M": 12, "minuti_M": 0, "ore_P": 12, "minuti_P": 0, "ore_N": 12, "minuti_N": 0,
    },
}

# Valori di default "di fabbrica" per l'intera sezione Regole
# contrattuali (usati dal pulsante "Ripristina default"): stessi valori
# dei default della dataclass RegoleContrattuali in engine/models.py.
REGOLE_DEFAULT = {
    "max_notti_consecutive": 2,
    "giorni_riposo_dopo_notte": 2,
    "max_giorni_consecutivi_lavorati": 5,
    "giorni_riposo_dopo_serie_lavorativa": 2,
    "ore_M": 8, "minuti_M": 0,
    "ore_P": 8, "minuti_P": 0,
    "ore_N": 10, "minuti_N": 0,
    "ore_ferie_giornaliere": 8, "minuti_ferie_giornaliere": 0,
}

OPZIONI_CELLA = (
    [""]
    + [f"F{p}" for p in range(1, 5)]
    + [f"R{p}" for p in range(1, 5)]
    + [f"{fascia}{p}" for fascia in ("M", "P", "N") for p in range(1, 5)]
    + ["AF", "AR", "AM", "AP", "AN"]
)

# Per le colonne del mese precedente (situazione iniziale) ha senso solo
# registrare il turno gia' effettuato (o nulla): non ha senso una richiesta
# soft ne' una ferie/riposo forzata su un giorno gia' passato.
OPZIONI_CELLA_PASSATO = ["", "AM", "AP", "AN"]

LEGENDA_CODICI = (
    "**Come leggere i codici nella griglia:**\n\n"
    "- vuoto = nessuna richiesta/vincolo\n"
    "- `F1`...`F4` = richiesta **ferie** del lavoratore, priorita' bassa -> molto alta\n"
    "- `R1`...`R4` = richiesta **riposo** del lavoratore, priorita' bassa -> molto alta\n"
    "- `M1`...`N4` = richiesta **turno specifico** (M/P/N) del lavoratore, con priorita'\n"
    "- `AF` = **vincolo admin**: ferie forzata dal coordinatore (sempre rispettata)\n"
    "- `AR` = **vincolo admin**: riposo forzato dal coordinatore (sempre rispettato)\n"
    "- `AM` / `AP` / `AN` = **vincolo admin**: turno forzato dal coordinatore in quella "
    "fascia — sulle colonne del mese precedente (🕓) significa invece un turno **gia' "
    "effettuato**: concettualmente e' lo stesso tipo di informazione (un'assegnazione "
    "certa, non negoziabile), solo che li' e' un fatto del passato invece che "
    "un'imposizione per il futuro\n\n"
    "**Ferie vs riposo:**\n"
    "- entrambe bloccano i turni quel giorno allo stesso modo\n"
    "- la **ferie** conta ore virtuali nel monte ore settimanale (e' comunque "
    "tempo retribuito); il **riposo** no\n"
    "- il motore non permette mai una ferie il giorno subito dopo una notte "
    "(o serie di notti): quel giorno di stop e' un riposo fisiologico "
    "obbligatorio, non sostituibile da una ferie\n"
    "- in pratica: se imposti una ferie su un giorno, il motore blocca "
    "automaticamente la notte del giorno prima per lo stesso lavoratore e "
    "cerca di farla coprire da qualcun altro — la ferie resta valida, "
    "cambia solo chi copre quella notte (se nessun altro puo' coprirla, il "
    "problema puo' risultare irrisolvibile)\n\n"
    "Una cella puo' contenere solo un codice alla volta: richiesta del lavoratore "
    "e vincolo del coordinatore sono alternativi, mai entrambi sullo stesso giorno.\n\n"
    "**Icone nelle intestazioni delle colonne** (Streamlit non supporta colori di "
    "sfondo nelle griglie editabili, quindi usiamo icone per distinguere le zone):\n"
    "- 🕓 = giorni del **mese precedente** (situazione iniziale, sola lettura concettuale: "
    "turni gia' avvenuti)\n"
    "- nessuna icona = giorni del **mese selezionato**\n"
    "- ➡️ = giorni del **mese successivo** (periodo esteso fino alla domenica di chiusura)"
)

# Numero MINIMO di giorni finali del mese precedente da mostrare per la
# situazione iniziale. Il numero effettivo si adatta al giorno della
# settimana in cui inizia il mese corrente, cosi' la situazione iniziale
# copre sempre l'intera settimana calendario (lun-dom) su cui il mese
# inizia — utile per le statistiche di ore settimanali lato utente. Non
# tocca in alcun modo il motore di calcolo, che gestisce stato_iniziale
# in modo generico indipendentemente da quanti giorni gli vengono passati.
#   - mese che inizia lun-ven: 4 giorni (il minimo)
#   - mese che inizia sabato: 5 giorni (per coprire lun-ven precedenti)
#   - mese che inizia domenica: 6 giorni (per coprire lun-sab precedenti)
GIORNI_STATO_INIZIALE_MINIMO = 4


def _mese_precedente(anno: int, mese: int) -> tuple[int, int]:
    if mese > 1:
        return anno, mese - 1
    return anno - 1, 12


def _giorni_correnti() -> list[int]:
    p = st.session_state.periodo
    return list(range(1, p["giorno_fine"] + 1))


def _aggiorna_periodo_da_mese():
    """Calcola automaticamente il periodo dal mese selezionato: parte
    sempre dal giorno 1 e si estende, se necessario, fino alla domenica
    che chiude la settimana in cui cade l'ultimo giorno del mese. Esempio:
    se il mese finisce venerdi' 31, il periodo si estende fino a domenica
    2 del mese successivo, cosi' l'ultima settimana elaborata e' sempre
    completa (lun-dom) invece che spezzata a meta'."""
    p = st.session_state.periodo
    p["giorno_inizio"] = 1
    p["giorno_fine"] = calcola_giorno_fine_periodo(int(p["anno"]), int(p["mese"]))


GIORNI_SETTIMANA_IT = ["lun", "mar", "mer", "gio", "ven", "sab", "dom"]  # indice 0 = lunedi'
MESI_IT = [
    "", "Gennaio", "Febbraio", "Marzo", "Aprile", "Maggio", "Giugno",
    "Luglio", "Agosto", "Settembre", "Ottobre", "Novembre", "Dicembre",
]


def _nome_giorno_settimana(data) -> str:
    return GIORNI_SETTIMANA_IT[data.weekday()]


def _etichetta_giorno(giorno: int) -> str:
    """Etichetta leggibile per una colonna giorno: mostra il giorno della
    settimana e la data reale, utile soprattutto per i giorni che
    sconfinano nel mese successivo (es. '32 - ven 01/08')."""
    p = st.session_state.periodo
    data = data_da_indice_periodo(int(p["anno"]), int(p["mese"]), giorno)
    return f"{giorno} - {_nome_giorno_settimana(data)} {data.strftime('%d/%m')}"


# Ciclo di default (3 giorni: M, P, riposo) per la situazione iniziale,
# usato per generare un punto di partenza plausibile invece di lasciare
# le celle vuote. NIENTE NOTTI nel ciclo: una versione precedente usava
# un ciclo con N-N-riposo-riposo, ma verificato numericamente che poteva
# lasciare troppi pochi lavoratori "liberi e con credito sufficiente"
# per coprire le notti richieste nella prima settimana corta del periodo
# (8 su 20 disponibili contro 10 richieste in uno scenario reale — bug
# scoperto in produzione). Con solo M/P, nessun lavoratore e' mai
# bloccato dal riposo dovuto a una notte pregressa, e il credito minimo
# (8h) e' sempre sufficiente — molto piu' robusto.
CICLO_DEFAULT_SITUAZIONE_INIZIALE = ["M", "P", "riposo"]
# Ogni quanti lavoratori (per indice) inserire un giorno di assenza al
# posto della fascia "M" del ciclo, per un minimo di varieta'. La
# situazione iniziale non distingue ferie da riposo (nessuna delle due
# genera ore virtuali per il mese precedente), quindi usiamo
# semplicemente una cella vuota.
OGNI_N_LAVORATORI_GIORNO_ASSENZA = 7


def _genera_situazione_iniziale_default(
    lavoratori_ids: list[str], giorni_si: list[int]
) -> dict[tuple[str, str], str]:
    """Genera un pattern di default per le colonne di situazione iniziale
    (mese precedente), invece di lasciarle vuote. Ogni lavoratore parte da
    un punto diverso del ciclo (offset = indice % lunghezza_ciclo), cosi' la
    situazione iniziale mostra una rotazione plausibile invece che valori
    identici per tutti. Ritorna {(lavoratore_id, colonna): codice}."""
    risultato = {}
    n_ciclo = len(CICLO_DEFAULT_SITUAZIONE_INIZIALE)
    n_giorni = len(giorni_si)

    for indice_lav, lavoratore_id in enumerate(lavoratori_ids):
        offset = indice_lav % n_ciclo
        inserisci_assenza = indice_lav % OGNI_N_LAVORATORI_GIORNO_ASSENZA == 0

        for j, giorno in enumerate(giorni_si):
            # j=0 e' il giorno piu' vecchio, j=n_giorni-1 il piu' recente
            # (quello immediatamente prima del periodo). Allineiamo il
            # giorno piu' recente alla posizione 'offset' del ciclo, e
            # risaliamo all'indietro per i giorni precedenti.
            posizione_ciclo = (offset - (n_giorni - 1 - j)) % n_ciclo
            valore = CICLO_DEFAULT_SITUAZIONE_INIZIALE[posizione_ciclo]

            if valore == "M" and inserisci_assenza:
                codice = ""  # giorno di assenza (ferie o riposo, equivalenti qui)
            elif valore == "riposo":
                codice = ""
            else:
                codice = _codice_da_admin("turno", valore)  # "AM"/"AP"/"AN"

            col = f"{PREFISSO_PASSATO}{giorno}"
            risultato[(lavoratore_id, col)] = codice

    return risultato


def _giorni_stato_iniziale() -> tuple[list[int], int, int]:
    """Ritorna (lista giorni finali del mese precedente, anno_prec, mese_prec).

    Il numero di giorni si adatta al giorno della settimana in cui inizia
    il mese corrente, cosi' la situazione iniziale copre sempre l'intera
    settimana calendario (lun-dom) su cui il 1 del mese cade, con un
    minimo di GIORNI_STATO_INIZIALE_MINIMO giorni anche quando il mese
    inizia di lunedi' (dove tecnicamente basterebbe 0)."""
    p = st.session_state.periodo
    anno_prec, mese_prec = _mese_precedente(int(p["anno"]), int(p["mese"]))
    giorni_nel_mese_prec = calendar.monthrange(anno_prec, mese_prec)[1]

    primo_giorno_mese = datetime.date(int(p["anno"]), int(p["mese"]), 1)
    # isoweekday: lunedi'=1 ... domenica=7. Giorni della settimana ISO
    # precedenti al 1 del mese = isoweekday - 1 (0 se il mese inizia di
    # lunedi'). Usiamo comunque il minimo se questo valore e' piu' basso.
    giorni_necessari = max(GIORNI_STATO_INIZIALE_MINIMO, primo_giorno_mese.isoweekday() - 1)

    n = min(giorni_necessari, giorni_nel_mese_prec)
    giorni = list(range(giorni_nel_mese_prec - n + 1, giorni_nel_mese_prec + 1))
    return giorni, anno_prec, mese_prec


PREFISSO_PASSATO = "S"  # prefisso colonna per i giorni del mese precedente


def _colonne_passato() -> list[str]:
    giorni_si, _, _ = _giorni_stato_iniziale()
    return [f"{PREFISSO_PASSATO}{g}" for g in giorni_si]


def _colonne_periodo() -> list[str]:
    return [str(g) for g in _giorni_correnti()]


def _tutte_le_colonne() -> list[str]:
    """Colonne della griglia unificata: prima i giorni del mese precedente
    (situazione iniziale), poi i giorni del periodo selezionato, in ordine
    cronologico."""
    return _colonne_passato() + _colonne_periodo()


def _etichetta_colonna(col: str) -> str:
    """Etichetta con giorno della settimana e icona per distinguere a
    colpo d'occhio le tre zone della griglia (Streamlit non supporta
    colori di sfondo nelle griglie editabili, quindi usiamo icone nelle
    intestazioni)."""
    p = st.session_state.periodo
    if col.startswith(PREFISSO_PASSATO):
        giorno = int(col[len(PREFISSO_PASSATO):])
        data = data_da_indice_mese_precedente(int(p["anno"]), int(p["mese"]), giorno)
        return f"🕓 {_nome_giorno_settimana(data)} {data.strftime('%d/%m')}"

    giorno = int(col)
    data = data_da_indice_periodo(int(p["anno"]), int(p["mese"]), giorno)
    if data.month != int(p["mese"]) or data.year != int(p["anno"]):
        return f"➡️ {_nome_giorno_settimana(data)} {data.strftime('%d/%m')}"
    return f"{giorno} - {_nome_giorno_settimana(data)} {data.strftime('%d/%m')}"


def _codice_da_richiesta(tipo: str, fascia, priorita: int) -> str:
    if tipo == "ferie":
        return f"F{priorita}"
    if tipo == "riposo":
        return f"R{priorita}"
    return f"{fascia}{priorita}"


def _codice_da_admin(tipo: str, fascia) -> str:
    if tipo == "ferie":
        return "AF"
    if tipo == "riposo":
        return "AR"
    return f"A{fascia}"


def _decodifica_cella(codice: str):
    """Ritorna ('richiesta', tipo, fascia, priorita) oppure
    ('admin', tipo, fascia) oppure None se la cella e' vuota o non valida."""
    codice = (codice or "").strip().upper()
    if not codice:
        return None

    if codice in ("AF", "AR", "AM", "AP", "AN"):
        if codice == "AF":
            return ("admin", "ferie", None)
        if codice == "AR":
            return ("admin", "riposo", None)
        return ("admin", "turno", codice[1])

    if codice[0] == "F" and codice[1:].isdigit() and int(codice[1:]) in range(1, 5):
        return ("richiesta", "ferie", None, int(codice[1:]))

    if codice[0] == "R" and codice[1:].isdigit() and int(codice[1:]) in range(1, 5):
        return ("richiesta", "riposo", None, int(codice[1:]))

    if codice[0] in ("M", "P", "N") and codice[1:].isdigit() and int(codice[1:]) in range(1, 5):
        return ("richiesta", "turno", codice[0], int(codice[1:]))

    return None  # codice non riconosciuto, verra' ignorato


# ---------------------------------------------------------------------------
# Inizializzazione dati di default (dal caso di esempio) in session_state
# ---------------------------------------------------------------------------

def _init_state():
    if "inizializzato" in st.session_state:
        return

    demo = get_sample_input()

    st.session_state.periodo = {
        "anno": demo.periodo.anno,
        "mese": demo.periodo.mese,
        "giorno_inizio": 1,
        "giorno_fine": calcola_giorno_fine_periodo(demo.periodo.anno, demo.periodo.mese),
    }

    st.session_state.df_lavoratori = pd.DataFrame([
        {
            "id": l.id,
            "nome": l.nome,
            "ore_settimanali_min": l.ore_settimanali_min,
            "ore_settimanali_max": l.ore_settimanali_max,
            "mai_notti": l.vincoli_personali.mai_notti,
        }
        for l in demo.lavoratori
    ])

    giorni = list(range(1, st.session_state.periodo["giorno_fine"] + 1))
    colonne_periodo = [str(g) for g in giorni]

    # Griglia fabbisogno: righe M/P/N, colonne giorni (solo periodo corrente,
    # il fabbisogno non ha senso per giorni gia' passati)
    df_fab = pd.DataFrame(0, index=["M", "P", "N"], columns=colonne_periodo)
    for f in demo.fabbisogno:
        if str(f.giorno) in df_fab.columns:
            df_fab.loc[f.fascia, str(f.giorno)] = f.minimo
    st.session_state.df_fabbisogno_cal = df_fab

    # Griglia unificata: situazione iniziale (mese precedente) + richieste
    # soft / vincoli admin del periodo corrente, tutto sulle stesse righe
    # (lavoratori). Le colonne del mese precedente usano il prefisso "S".
    lavoratori_ids = [l.id for l in demo.lavoratori]
    giorni_si, _, _ = _giorni_stato_iniziale()
    colonne_passato = [f"{PREFISSO_PASSATO}{g}" for g in giorni_si]
    colonne_tutte = colonne_passato + colonne_periodo

    df_cal = pd.DataFrame("", index=lavoratori_ids, columns=colonne_tutte)

    # Situazione iniziale: pattern di default plausibile (invece di celle
    # vuote), poi eventuali dati specifici di sample_data.py sovrascrivono
    # dove presenti. Vedi _genera_situazione_iniziale_default per il
    # perche': una situazione iniziale vuota puo' rendere infeasible la
    # prima settimana corta del periodo, quando il minimo ore settimanali
    # del lavoratore non e' raggiungibile nei soli giorni del nuovo mese.
    for (lavoratore_id, col), codice in _genera_situazione_iniziale_default(lavoratori_ids, giorni_si).items():
        if lavoratore_id in df_cal.index and col in df_cal.columns:
            df_cal.loc[lavoratore_id, col] = codice

    for si in demo.stato_iniziale:
        col = f"{PREFISSO_PASSATO}{si.giorno}"
        if si.lavoratore_id in df_cal.index and col in df_cal.columns:
            df_cal.loc[si.lavoratore_id, col] = _codice_da_admin("turno", si.fascia)

    for r in demo.richieste_soft:
        if r.lavoratore_id in df_cal.index and str(r.giorno) in df_cal.columns:
            df_cal.loc[r.lavoratore_id, str(r.giorno)] = _codice_da_richiesta(r.tipo, r.fascia, r.priorita)
    for v in demo.vincoli_admin:
        if v.lavoratore_id in df_cal.index and str(v.giorno) in df_cal.columns:
            df_cal.loc[v.lavoratore_id, str(v.giorno)] = _codice_da_admin(v.tipo, v.fascia)

    st.session_state.df_calendario = df_cal

    # Ore e minuti separati per ciascuna fascia (e per le ferie): il
    # motore lavora internamente in minuti (regole_contrattuali.
    # minuti_per_fascia / minuti_ferie_giornaliere), ma qui li scomponiamo
    # in ore+minuti per un input piu' naturale — ricombinati in
    # _costruisci_input().
    _minuti_M = demo.regole_contrattuali.minuti_per_fascia.get("M", 480)
    _minuti_P = demo.regole_contrattuali.minuti_per_fascia.get("P", 480)
    _minuti_N = demo.regole_contrattuali.minuti_per_fascia.get("N", 600)
    _minuti_ferie = demo.regole_contrattuali.minuti_ferie_giornaliere

    # Chiavi FLAT (non piu' annidate in un dict "regole") direttamente in
    # session_state: necessario per il pattern preset con key=/on_change
    # (stesso usato per i pesi fairness, PRESET_FAIRNESS) — un selectbox
    # puo' scrivere direttamente st.session_state[chiave] solo se il
    # number_input corrispondente e' anch'esso legato con key=, cosa che
    # richiede chiavi flat, non annidate in un dict.
    st.session_state.max_notti_consecutive = demo.regole_contrattuali.max_notti_consecutive
    st.session_state.giorni_riposo_dopo_notte = demo.regole_contrattuali.giorni_riposo_dopo_notte
    st.session_state.max_giorni_consecutivi_lavorati = demo.regole_contrattuali.max_giorni_consecutivi_lavorati
    st.session_state.giorni_riposo_dopo_serie_lavorativa = demo.regole_contrattuali.giorni_riposo_dopo_serie_lavorativa
    st.session_state.ore_M, st.session_state.minuti_M = _minuti_M // 60, _minuti_M % 60
    st.session_state.ore_P, st.session_state.minuti_P = _minuti_P // 60, _minuti_P % 60
    st.session_state.ore_N, st.session_state.minuti_N = _minuti_N // 60, _minuti_N % 60
    st.session_state.ore_ferie_giornaliere = _minuti_ferie // 60
    st.session_state.minuti_ferie_giornaliere = _minuti_ferie % 60

    st.session_state.fairness = {
        "vieta_pm_consecutivo": demo.regole_contrattuali.vieta_pm_consecutivo,
        "bilancia_fasce": demo.parametri_fairness.bilancia_fasce,
        "bilancia_fasce_hard": demo.parametri_fairness.bilancia_fasce_hard,
        "scarto_massimo_M": demo.parametri_fairness.scarto_massimo_M,
        "scarto_massimo_P": demo.parametri_fairness.scarto_massimo_P,
        "scarto_massimo_N": demo.parametri_fairness.scarto_massimo_N,
        "bilancia_giorni_settimana": demo.parametri_fairness.bilancia_giorni_settimana,
        "bilancia_ore_settimanali": demo.parametri_fairness.bilancia_ore_settimanali,
        "bilancia_copertura_giornaliera": demo.parametri_fairness.bilancia_copertura_giornaliera,
        "minimizza_pm_consecutivo": demo.parametri_fairness.minimizza_pm_consecutivo,
        "bilancia_proporzione_giornaliera": demo.parametri_fairness.bilancia_proporzione_giornaliera,
    }
    # Pesi come chiavi dirette di session_state (non dentro il dizionario
    # sopra) cosi' il preset puo' scriverle direttamente e i relativi
    # slider (key=stesso nome) le rileggono correttamente allo stesso
    # giro di esecuzione, senza il ritardo "un giro indietro" che si
    # avrebbe se il preset scrivesse in una struttura diversa da quella
    # che il widget usa come propria chiave.
    for chiave, valore in PRESET_FAIRNESS["Equilibrio reparto (consigliato)"].items():
        st.session_state[chiave] = valore
    st.session_state.preset_fairness_selezionato = "Equilibrio reparto (consigliato)"

    st.session_state.risultato = None
    st.session_state.ultimo_input = None
    st.session_state.editor_calendario_versione = 0
    st.session_state.inizializzato = True


def _sincronizza_griglie():
    """Riallinea le griglie calendario alla lista lavoratori corrente e
    al periodo corrente, preservando i valori gia' inseriti dove possibile.

    IMPORTANTE: questa funzione va richiamata DOPO che i widget di
    anno/mese/numero lavoratori (nella scheda Regole & periodo) hanno
    aggiornato session_state in questo stesso giro di esecuzione, non
    prima — altrimenti le altre schede userebbero ancora i valori
    dell'esecuzione precedente (bug di interfaccia "un giro indietro")."""
    _aggiorna_periodo_da_mese()

    colonne_periodo = _colonne_periodo()
    colonne_tutte = _tutte_le_colonne()
    lavoratori_ids = [
        str(row["id"]) for _, row in st.session_state.df_lavoratori.iterrows()
        if str(row["id"]).strip()
    ]

    st.session_state.df_fabbisogno_cal = (
        st.session_state.df_fabbisogno_cal
        .reindex(index=["M", "P", "N"], columns=colonne_periodo, fill_value=0)
        .fillna(0)
    )

    # Riallineo SENZA riempire subito le celle nuove (restano NaN),
    # cosi' posso distinguerle dalle celle gia' "" perche' l'utente le ha
    # volutamente svuotate — solo le celle genuinamente nuove (nuovo
    # lavoratore aggiunto, o nuovo mese con giorni di situazione iniziale
    # diversi) ricevono il pattern di default; quelle gia' esistenti
    # (modificate o deliberatamente vuote) restano come l'utente le ha
    # lasciate.
    df_riallineato = st.session_state.df_calendario.reindex(index=lavoratori_ids, columns=colonne_tutte)

    giorni_si, _, _ = _giorni_stato_iniziale()
    pattern_default = _genera_situazione_iniziale_default(lavoratori_ids, giorni_si)
    for (lavoratore_id, col), codice in pattern_default.items():
        if (
            lavoratore_id in df_riallineato.index
            and col in df_riallineato.columns
            and pd.isna(df_riallineato.loc[lavoratore_id, col])
        ):
            df_riallineato.loc[lavoratore_id, col] = codice

    st.session_state.df_calendario = df_riallineato.fillna("")


def _sincronizza_numero_lavoratori(n_target: int):
    """Aggiunge o rimuove lavoratori in fondo alla lista per raggiungere
    n_target. I lavoratori aggiunti prendono nome/cognome generati
    automaticamente (Nome<n> Cognome<n>); quelli gia' esistenti (e le
    eventuali modifiche fatte a mano, es. nome vero o ore contrattuali
    diverse) non vengono toccati."""
    df = st.session_state.df_lavoratori
    n_attuale = len(df)
    if n_target == n_attuale:
        return

    if n_target > n_attuale:
        nuove_righe = []
        for i in range(n_attuale, n_target):
            indice_persona = i + 1
            nuove_righe.append({
                "id": f"w{indice_persona}",
                "nome": f"Nome{indice_persona} Cognome{indice_persona}",
                "ore_settimanali_min": 36,
                "ore_settimanali_max": 40,
                "mai_notti": False,
            })
        st.session_state.df_lavoratori = pd.concat(
            [df, pd.DataFrame(nuove_righe)], ignore_index=True
        )
    else:
        st.session_state.df_lavoratori = df.iloc[:n_target].reset_index(drop=True)


_init_state()


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------

st.title("Turnazione reparto")

tab_regole, tab_lavoratori, tab_calendario = st.tabs(
    ["Regole & periodo", "Lavoratori", "Calendario"]
)

# IMPORTANTE: questa scheda va PRIMA delle altre due nel codice (non solo
# visivamente) perche' i suoi widget (numero lavoratori, anno, mese)
# aggiornano session_state e vanno rieseguiti prima che le schede
# Lavoratori/Calendario leggano quegli stessi valori nello stesso giro di
# esecuzione. Streamlit esegue il codice di ogni `with tab:` nell'ordine
# in cui compare nello script, indipendentemente da quale scheda l'utente
# ha visivamente aperta — se questa scheda fosse dopo le altre (com'era
# in origine), un cambio di mese o di numero lavoratori si sarebbe visto
# nelle altre schede solo al giro di esecuzione successivo.
with tab_regole:
    st.subheader("Numero di lavoratori")
    numero_lavoratori_attuale = len(st.session_state.df_lavoratori)
    numero_lavoratori_target = st.number_input(
        "Numero di lavoratori",
        min_value=1, max_value=200,
        value=numero_lavoratori_attuale,
        step=1,
        help=(
            "Aggiunge o rimuove lavoratori in fondo alla lista (scheda "
            "Lavoratori). I nuovi ricevono un nome generato automaticamente "
            "(Nome<n> Cognome<n>), da modificare poi con il nome vero nella "
            "scheda Lavoratori. I lavoratori esistenti e le eventuali "
            "modifiche gia' fatte (nome, ore, mai notti) non vengono toccati."
        ),
    )
    _sincronizza_numero_lavoratori(int(numero_lavoratori_target))

    col1, col2 = st.columns(2)

    with col1:
        st.subheader("Periodo")
        st.session_state.periodo["anno"] = st.number_input(
            "Anno", value=st.session_state.periodo["anno"], step=1
        )
        st.session_state.periodo["mese"] = st.number_input(
            "Mese", value=st.session_state.periodo["mese"], min_value=1, max_value=12, step=1
        )
        # Sincronizza SUBITO periodo e griglie con i valori appena letti dai
        # widget sopra, cosi' le schede Lavoratori e Calendario (che nel
        # codice vengono dopo) vedono gia' i dati aggiornati in questo
        # stesso giro di esecuzione.
        _sincronizza_griglie()

        p = st.session_state.periodo
        data_inizio = data_da_indice_periodo(int(p["anno"]), int(p["mese"]), 1)
        data_fine = data_da_indice_periodo(int(p["anno"]), int(p["mese"]), int(p["giorno_fine"]))
        testo_periodo = (
            f"Periodo elaborato: dal {data_inizio.strftime('%d/%m/%Y')} "
            f"al {data_fine.strftime('%d/%m/%Y')} ({p['giorno_fine']} giorni)."
        )
        if data_fine.month != int(p["mese"]) or data_fine.year != int(p["anno"]):
            testo_periodo += (
                " Il periodo si estende oltre la fine del mese selezionato, fino "
                "alla domenica che chiude l'ultima settimana, cosi' il vincolo di "
                "ore settimanali lavora sempre su settimane complete."
            )
        st.caption(testo_periodo)

        st.subheader("Regole contrattuali")

        if st.button(
            "↺ Ripristina default", key="reset_regole_btn",
            help="Riporta tutte le regole di questa sezione ai valori di fabbrica.",
        ):
            for _chiave, _valore in REGOLE_DEFAULT.items():
                st.session_state[_chiave] = _valore
            # Riallinea anche la selectbox del preset: REGOLE_DEFAULT
            # corrisponde esattamente al preset "Standard", altrimenti
            # dopo il reset mostrerebbe ancora l'ultimo preset scelto pur
            # avendo gia' i valori standard sotto.
            st.session_state.preset_durata_selezionato = "Standard (8h / 8h / 10h)"
            st.rerun()

        with st.expander("⏱️ Riposi e turni consecutivi", expanded=True):
            col_a, col_b = st.columns(2)
            with col_a:
                st.number_input(
                    "Massimo notti consecutive", key="max_notti_consecutive",
                    min_value=1, max_value=5,
                )
                st.number_input(
                    "Massimo giorni di lavoro consecutivi", key="max_giorni_consecutivi_lavorati",
                    min_value=1, max_value=10,
                    help=(
                        "Numero massimo di giorni di fila con un turno assegnato "
                        "(qualsiasi fascia: M, P o N), oltre i quali serve almeno "
                        "un giorno libero. Tiene conto anche dei giorni gia' "
                        "lavorati nella situazione iniziale, a cavallo con il "
                        "mese precedente."
                    ),
                )
            with col_b:
                st.number_input(
                    "Giorni di riposo dopo la notte (o serie di notti)",
                    key="giorni_riposo_dopo_notte", min_value=1, max_value=5,
                    help=(
                        "Numero di giorni di riposo obbligatorio dopo un turno "
                        "notturno, o dopo l'ultima notte di una serie consecutiva "
                        "(non dopo ognuna singolarmente). Si applica anche al "
                        "divieto di ferie nei giorni precedenti una notte: con 2 "
                        "giorni, ne' il giorno prima ne' quello 2 giorni prima di "
                        "una ferie possono essere notte."
                    ),
                )
                st.number_input(
                    "Giorni di riposo dopo la serie massima di giorni lavorati",
                    key="giorni_riposo_dopo_serie_lavorativa", min_value=1, max_value=5,
                    help=(
                        "Quando un lavoratore raggiunge il numero massimo di "
                        "giorni di lavoro consecutivi (a sinistra), i successivi "
                        "N giorni devono essere vero riposo (nessun turno di "
                        "alcun tipo) — stesso principio del riposo dopo la "
                        "notte, ma applicato alla serie generale di giorni "
                        "lavorati."
                    ),
                )

            if st.session_state.max_notti_consecutive > st.session_state.max_giorni_consecutivi_lavorati:
                st.warning(
                    f"Il massimo di notti consecutive "
                    f"({st.session_state.max_notti_consecutive}) supera il massimo "
                    f"di giorni di lavoro consecutivi "
                    f"({st.session_state.max_giorni_consecutivi_lavorati}): una "
                    f"serie di sole notti finirebbe comunque per superare il "
                    f"limite generale, rendendo il primo vincolo di fatto mai "
                    f"raggiungibile per intero."
                )
            if st.session_state.giorni_riposo_dopo_serie_lavorativa < st.session_state.giorni_riposo_dopo_notte:
                st.warning(
                    f"Il riposo dopo la serie massima di giorni lavorati "
                    f"({st.session_state.giorni_riposo_dopo_serie_lavorativa} "
                    f"giorni) e' piu' corto di quello dopo la notte "
                    f"({st.session_state.giorni_riposo_dopo_notte} giorni): puo' "
                    f"essere intenzionale, ma e' una combinazione insolita — di "
                    f"norma una serie lunga di turni merita un riposo almeno "
                    f"pari a quello dopo una notte."
                )

            st.caption(
                f"Con queste regole: dopo {st.session_state.max_notti_consecutive} "
                f"notti consecutive servono {st.session_state.giorni_riposo_dopo_notte} "
                f"giorni di riposo pieno; dopo "
                f"{st.session_state.max_giorni_consecutivi_lavorati} giorni di "
                f"lavoro di fila (qualsiasi turno) ne servono "
                f"{st.session_state.giorni_riposo_dopo_serie_lavorativa}."
            )

        with st.expander("🕐 Durata turni e ferie"):
            def _applica_preset_durata():
                valori = PRESET_DURATA_TURNI[st.session_state.preset_durata_selezionato]
                for chiave, valore in valori.items():
                    st.session_state[chiave] = valore

            st.selectbox(
                "Preset durata turni",
                options=list(PRESET_DURATA_TURNI.keys()),
                key="preset_durata_selezionato",
                on_change=_applica_preset_durata,
                help=(
                    "Punto di partenza per Mattino/Pomeriggio/Notte sotto — "
                    "restano comunque modificabili singolarmente dopo averlo "
                    "scelto. Le ferie non sono incluse nel preset."
                ),
            )

            st.caption("Durata dei turni (ore e minuti)")
            col_oreM, col_minM = st.columns(2)
            with col_oreM:
                st.number_input("Ore Mattino", key="ore_M", min_value=0, max_value=23)
            with col_minM:
                st.number_input("Minuti Mattino", key="minuti_M", min_value=0, max_value=59, step=5)

            col_oreP, col_minP = st.columns(2)
            with col_oreP:
                st.number_input("Ore Pomeriggio", key="ore_P", min_value=0, max_value=23)
            with col_minP:
                st.number_input("Minuti Pomeriggio", key="minuti_P", min_value=0, max_value=59, step=5)

            col_oreN, col_minN = st.columns(2)
            with col_oreN:
                st.number_input("Ore Notte", key="ore_N", min_value=0, max_value=23)
            with col_minN:
                st.number_input("Minuti Notte", key="minuti_N", min_value=0, max_value=59, step=5)

            col_oreF, col_minF = st.columns(2)
            with col_oreF:
                st.number_input(
                    "Ore ferie giornaliere", key="ore_ferie_giornaliere",
                    min_value=0, max_value=23,
                    help=(
                        "Ore (+ minuti a fianco) virtuali che una giornata di "
                        "ferie aggiunge al monte ore settimanale (e' comunque "
                        "tempo retribuito). Il riposo non aggiunge nulla."
                    ),
                )
            with col_minF:
                st.number_input(
                    "Minuti ferie giornaliere", key="minuti_ferie_giornaliere",
                    min_value=0, max_value=59, step=5,
                )

            st.caption(
                f"Riepilogo: Mattino {st.session_state.ore_M}h{st.session_state.minuti_M:02d}m, "
                f"Pomeriggio {st.session_state.ore_P}h{st.session_state.minuti_P:02d}m, "
                f"Notte {st.session_state.ore_N}h{st.session_state.minuti_N:02d}m, "
                f"ferie {st.session_state.ore_ferie_giornaliere}h"
                f"{st.session_state.minuti_ferie_giornaliere:02d}m equivalenti."
            )

    with col2:
        st.subheader("Fairness (equilibrio tra lavoratori e giorni)")
        st.session_state.fairness["bilancia_fasce_hard"] = st.checkbox(
            "Scarto massimo tra lavoratori per fascia (vincolo rigido)",
            value=st.session_state.fairness["bilancia_fasce_hard"],
            help=(
                "Alternativa piu' rigida a 'Bilancia il numero di turni per "
                "fascia' (sotto): invece di scoraggiare lo squilibrio "
                "nell'obiettivo, impone che la differenza tra il "
                "lavoratore col conteggio piu' alto e quello col conteggio "
                "piu' basso (per M, P e N separatamente) non superi la "
                "soglia impostata. Le due opzioni sono mutuamente "
                "esclusive — attivando questa, quella soft viene "
                "disattivata automaticamente. I conteggi sono normalizzati "
                "per la capacita' contrattuale (ore settimanali massime): "
                "un part-time a meta' ore che fa 3 notti conta come "
                "equivalente a 6 di un full-time, non viene penalizzato "
                "per avere naturalmente meno turni. I lavoratori con "
                "'mai notti' sono esclusi dal confronto sulla fascia N. "
                "Puo' ridurre la flessibilita' del motore e, con pochi "
                "lavoratori disponibili, rendere infeasible cio' che con "
                "solo la penalizzazione soft sarebbe stato risolvibile."
            ),
        )
        if st.session_state.fairness["bilancia_fasce_hard"]:
            st.session_state.fairness["bilancia_fasce"] = False
            col_sM, col_sP, col_sN = st.columns(3)
            with col_sM:
                st.session_state.fairness["scarto_massimo_M"] = st.number_input(
                    "Scarto massimo M", value=st.session_state.fairness["scarto_massimo_M"],
                    min_value=1, max_value=20,
                )
            with col_sP:
                st.session_state.fairness["scarto_massimo_P"] = st.number_input(
                    "Scarto massimo P", value=st.session_state.fairness["scarto_massimo_P"],
                    min_value=1, max_value=20,
                )
            with col_sN:
                st.session_state.fairness["scarto_massimo_N"] = st.number_input(
                    "Scarto massimo N", value=st.session_state.fairness["scarto_massimo_N"],
                    min_value=1, max_value=20,
                )
        st.session_state.fairness["bilancia_fasce"] = st.checkbox(
            "Bilancia il numero di turni per fascia tra i lavoratori",
            value=st.session_state.fairness["bilancia_fasce"],
            disabled=st.session_state.fairness["bilancia_fasce_hard"],
            help=(
                "Disattivato: e' attivo il vincolo rigido equivalente "
                "sopra (le due opzioni sono mutuamente esclusive)."
                if st.session_state.fairness["bilancia_fasce_hard"] else None
            ),
        )
        st.session_state.fairness["bilancia_giorni_settimana"] = st.checkbox(
            "Bilancia il totale di giorni lavorati tra i lavoratori",
            value=st.session_state.fairness["bilancia_giorni_settimana"],
        )
        st.session_state.fairness["bilancia_ore_settimanali"] = st.checkbox(
            "Bilancia le ore lavorate tra i lavoratori, settimana per settimana",
            value=st.session_state.fairness["bilancia_ore_settimanali"],
            help=(
                "Bilancia il totale sull'intero periodo non basta: senza "
                "questa opzione una singola settimana potrebbe restare "
                "molto sbilanciata anche se sul periodo intero i totali si "
                "pareggiano. Confronta il tasso di utilizzo della capacita' "
                "residua (non le ore grezze): un lavoratore con ore gia' "
                "maturate nella situazione iniziale ha legittimamente meno "
                "ore residue quella settimana, ed e' considerato 'equo' se "
                "sfrutta bene quella capacita' residua, senza penalizzare "
                "gli altri lavoratori per compensare."
            ),
        )
        st.session_state.fairness["bilancia_copertura_giornaliera"] = st.checkbox(
            "Spalma il surplus di copertura il piu' possibile tra i giorni",
            value=st.session_state.fairness["bilancia_copertura_giornaliera"],
            help=(
                "Il fabbisogno minimo e' un vincolo di 'almeno N persone', quindi "
                "il motore puo' assegnare piu' persone del minimo in certi giorni. "
                "Con questa opzione attiva, un eventuale surplus si distribuisce il "
                "piu' possibile su tutti i giorni invece di concentrarsi su pochi."
            ),
        )
        st.session_state.fairness["vieta_pm_consecutivo"] = st.checkbox(
            "Vieta del tutto Pomeriggio -> Mattino su giorni consecutivi (vincolo rigido)",
            value=st.session_state.fairness["vieta_pm_consecutivo"],
            help=(
                "Alternativa piu' rigida a 'Minimizza le sequenze "
                "Pomeriggio -> Mattino' (sotto): invece di scoraggiarle "
                "nell'obiettivo, le vieta del tutto come vincolo rigido. "
                "Le due opzioni sono mutuamente esclusive — attivando "
                "questa, quella soft viene disattivata automaticamente. "
                "Puo' ridurre la flessibilita' del motore e, in scenari "
                "con pochi lavoratori disponibili, rendere il problema "
                "infeasible dove altrimenti sarebbe stato risolvibile con "
                "solo la penalizzazione soft."
            ),
        )
        if st.session_state.fairness["vieta_pm_consecutivo"]:
            st.session_state.fairness["minimizza_pm_consecutivo"] = False
        st.session_state.fairness["minimizza_pm_consecutivo"] = st.checkbox(
            "Minimizza le sequenze Pomeriggio -> Mattino su giorni consecutivi",
            value=st.session_state.fairness["minimizza_pm_consecutivo"],
            disabled=st.session_state.fairness["vieta_pm_consecutivo"],
            help=(
                "Un turno Pomeriggio seguito da un turno Mattino il giorno dopo "
                "lascia un riposo molto piu' corto (P finisce sera tardi, M "
                "inizia presto la mattina dopo) rispetto a Mattino -> Pomeriggio "
                "(quasi un giorno intero di margine). Non viene vietato — spesso "
                "e' inevitabile per la copertura — ma minimizzato dove possibile, "
                "premiando implicitamente M->P rispetto a P->M."
                + (
                    "\n\nDisattivato: e' attivo il vincolo rigido equivalente "
                    "sopra (le due opzioni sono mutuamente esclusive)."
                    if st.session_state.fairness["vieta_pm_consecutivo"] else ""
                )
            ),
        )
        st.session_state.fairness["bilancia_proporzione_giornaliera"] = st.checkbox(
            "Bilancia il surplus tra fasce, giorno per giorno",
            value=st.session_state.fairness["bilancia_proporzione_giornaliera"],
            help=(
                "'Spalma il surplus di copertura' (sopra) evita solo il caso "
                "peggiore in assoluto su tutto il mese: puo' lasciare che "
                "molti singoli giorni abbiano comunque M/P/N sbilanciati tra "
                "loro (es. un giorno con 8 Mattina e 5 Pomeriggio, pur avendo "
                "lo stesso fabbisogno) senza che questo emerga come 'il "
                "peggiore'. Questa opzione confronta invece le fasce "
                "PRESENTI OGNI SINGOLO GIORNO (proporzionalmente al loro "
                "fabbisogno) e somma lo scarto su tutti i giorni, non solo "
                "il caso peggiore — cosi' ogni giorno deve essere "
                "ragionevole, non solo il mese nel complesso."
            ),
        )

        st.divider()

        def _applica_preset_fairness():
            valori = PRESET_FAIRNESS[st.session_state.preset_fairness_selezionato]
            for chiave, valore in valori.items():
                st.session_state[chiave] = valore

        st.selectbox(
            "Preset di pesi fairness",
            options=list(PRESET_FAIRNESS.keys()),
            key="preset_fairness_selezionato",
            on_change=_applica_preset_fairness,
            help=(
                "Punto di partenza consigliato per i 5 pesi qui sotto. Dopo "
                "averlo scelto puoi comunque modificare ogni peso "
                "singolarmente nell'espansione 'Pesi avanzati'."
            ),
        )

        with st.expander("Pesi avanzati (personalizza singolarmente)", expanded=False):
            st.caption(
                "Ogni vincolo ha un peso indipendente invece di uno unico "
                "condiviso: cosi' puoi dare piu' importanza a uno specifico "
                "senza alterare gli altri nella stessa proporzione. Tutti i "
                "pesi restano intenzionalmente sotto 10 (il peso di una "
                "richiesta soft di priorita' media), cosi' le preferenze "
                "dei lavoratori continuano a prevalere sull'equilibrio del "
                "team."
            )
            st.slider(
                "Peso: bilancia turni per fascia", min_value=1, max_value=10,
                key="peso_bilancia_fasce",
            )
            st.slider(
                "Peso: bilancia giorni lavorati", min_value=1, max_value=10,
                key="peso_bilancia_giorni_settimana",
            )
            st.slider(
                "Peso: bilancia ore settimanali", min_value=1, max_value=10,
                key="peso_bilancia_ore_settimanali",
            )
            st.slider(
                "Peso: spalma surplus copertura", min_value=1, max_value=10,
                key="peso_bilancia_copertura_giornaliera",
                help=(
                    "Confronta il surplus (turni oltre il minimo richiesto) "
                    "come tasso proporzionale al fabbisogno, su un'unica "
                    "scala tra tutte le fasce e i giorni: minimizza il "
                    "divario peggiore tra una coppia giorno/fascia e "
                    "un'altra, cosi' M e P (se hanno lo stesso fabbisogno) "
                    "non finiscono con surplus molto diversi tra loro."
                ),
            )
            st.slider(
                "Peso: minimizza sequenze P->M", min_value=1, max_value=10,
                key="peso_minimizza_pm_consecutivo",
            )
            st.slider(
                "Peso: bilancia surplus tra fasce, giorno per giorno", min_value=1, max_value=10,
                key="peso_bilancia_proporzione_giornaliera",
                help=(
                    "Confronta le fasce presenti ogni singolo giorno "
                    "(proporzionalmente al fabbisogno di quel giorno) e "
                    "somma lo scarto su tutti i giorni — a differenza di "
                    "'spalma surplus copertura', che guarda solo al caso "
                    "peggiore in assoluto su tutto il mese."
                ),
            )

with tab_lavoratori:
    st.caption(
        "Elenco del personale del reparto per questa categoria (infermieri "
        "o oss). Ore settimanali min/max: intervallo di ore contrattuali, "
        "non un valore fisso — sotto il minimo non si puo' andare (il "
        "motore assegna turni extra se necessario per garantirlo), sopra "
        "il massimo nemmeno. Se minimo e massimo coincidono, le ore sono "
        "obbligatoriamente uguali a quel valore."
    )
    st.session_state.df_lavoratori = st.data_editor(
        st.session_state.df_lavoratori,
        num_rows="dynamic",
        use_container_width=True,
        key="editor_lavoratori",
    )

with tab_calendario:
    st.subheader("Fabbisogno di personale")
    p = st.session_state.periodo
    giorni_extra = p["giorno_fine"] - calendar.monthrange(int(p["anno"]), int(p["mese"]))[1]
    testo_fabbisogno = "Numero minimo di persone richieste per ogni giorno e fascia oraria."
    if giorni_extra > 0:
        testo_fabbisogno += (
            f" Le ultime {giorni_extra} colonne (➡️) appartengono gia' al mese successivo "
            "(il periodo e' esteso fino alla domenica che chiude la settimana)."
        )
    st.caption(testo_fabbisogno)
    st.session_state.df_fabbisogno_cal = st.data_editor(
        st.session_state.df_fabbisogno_cal,
        use_container_width=True,
        key="editor_fabbisogno_cal",
        column_config={
            col: st.column_config.NumberColumn(label=_etichetta_colonna(col), min_value=0, max_value=50, step=1)
            for col in st.session_state.df_fabbisogno_cal.columns
        },
    )

    st.divider()
    st.subheader("Situazione iniziale, richieste soft e vincoli admin per lavoratore")
    st.caption(
        "Una griglia unica: le prime colonne (🕓) sono gli ultimi giorni del mese "
        "precedente — turni gia' effettuati, trattati concettualmente come "
        "assegnazioni gia' 'chiuse' (stesso principio di un vincolo admin, solo "
        "che e' gia' un fatto avvenuto). Le colonne successive sono il periodo "
        "da pianificare: richieste soft del lavoratore oppure vincoli imposti "
        "dal coordinatore, mutuamente esclusivi per costruzione (una cella = un "
        "solo codice). Le ultime colonne (➡️), se presenti, sono gia' nel mese successivo."
    )
    with st.expander("Legenda codici", expanded=False):
        st.markdown(LEGENDA_CODICI)

    colonne_passato_correnti = set(_colonne_passato())

    with st.expander("Esporta / Importa CSV", expanded=False):
        st.caption(
            "Scarica la griglia come CSV per modificarla comodamente in "
            "Excel o Notepad, poi ricaricala qui per applicare le "
            "modifiche. Le colonne 'S<n>' sono i giorni del mese "
            "precedente (situazione iniziale); le colonne numeriche sono "
            "i giorni del periodo da pianificare. I codici validi per "
            "ogni cella sono elencati sopra in 'Legenda codici'."
        )

        csv_bytes = st.session_state.df_calendario.to_csv(
            index=True, index_label="lavoratore_id"
        ).encode("utf-8")
        st.download_button(
            "Scarica CSV",
            data=csv_bytes,
            file_name="situazione_richieste_vincoli.csv",
            mime="text/csv",
            key="download_csv_calendario",
        )

        file_caricato = st.file_uploader(
            "Carica CSV", type=["csv"], key="upload_csv_calendario"
        )
        if file_caricato is not None and st.button("Applica CSV caricato", key="btn_applica_csv"):
            try:
                df_caricato = pd.read_csv(file_caricato, index_col="lavoratore_id", dtype=str)
                df_caricato = df_caricato.fillna("")

                colonne_attuali = list(st.session_state.df_calendario.columns)
                lavoratori_attuali = list(st.session_state.df_calendario.index)
                colonne_mancanti = [c for c in colonne_attuali if c not in df_caricato.columns]
                lavoratori_mancanti = [w for w in lavoratori_attuali if w not in df_caricato.index]

                # Riallinea a righe/colonne attuali: eventuali righe/colonne
                # extra nel CSV vengono ignorate, quelle mancanti restano
                # vuote — cosi' un CSV con struttura leggermente diversa
                # (es. esportato prima di cambiare mese o numero lavoratori)
                # non causa un errore, solo un avviso.
                df_caricato = df_caricato.reindex(
                    index=lavoratori_attuali, columns=colonne_attuali, fill_value=""
                ).fillna("")

                st.session_state.df_calendario = df_caricato
                st.session_state.editor_calendario_versione += 1

                messaggio = "CSV caricato e applicato alla griglia."
                if colonne_mancanti or lavoratori_mancanti:
                    messaggio += (
                        " Attenzione: il CSV non copriva tutte le colonne/righe "
                        "attuali (probabilmente esportato con un periodo o un "
                        "numero di lavoratori diverso) — le celle mancanti sono "
                        "state lasciate vuote."
                    )
                st.success(messaggio)
                st.rerun()
            except Exception as e:
                st.error(f"Errore nel caricamento del CSV: {e}")

    with st.expander("Svuota celle in blocco", expanded=False):
        col_svuota_1, col_svuota_2 = st.columns(2)

        with col_svuota_1:
            st.caption("Rimuove tutti i codici dei lavoratori selezionati.")

            def _sync_tutti_lavoratori():
                if st.session_state.get("check_tutti_lavoratori"):
                    st.session_state["multiselect_lavoratori_svuota"] = list(
                        st.session_state.df_calendario.index
                    )
                else:
                    st.session_state["multiselect_lavoratori_svuota"] = []

            st.checkbox(
                "Seleziona tutti i lavoratori", value=False,
                key="check_tutti_lavoratori", on_change=_sync_tutti_lavoratori,
            )
            lavoratori_da_svuotare = st.multiselect(
                "Lavoratori", options=list(st.session_state.df_calendario.index),
                key="multiselect_lavoratori_svuota",
            )
            includi_passato_lavoratore = st.checkbox(
                "Includi anche i giorni del mese precedente (situazione iniziale)",
                value=False,
                key="check_svuota_lavoratore_passato",
            )
            if st.button("Svuota lavoratori selezionati", key="btn_svuota_lavoratore", disabled=not lavoratori_da_svuotare):
                colonne_da_svuotare = (
                    list(st.session_state.df_calendario.columns) if includi_passato_lavoratore
                    else _colonne_periodo()
                )
                st.session_state.df_calendario.loc[lavoratori_da_svuotare, colonne_da_svuotare] = ""
                st.session_state.editor_calendario_versione += 1
                st.rerun()

        with col_svuota_2:
            st.caption("Rimuove tutti i codici dei giorni selezionati (tutti i lavoratori).")
            etichette_colonne = {col: _etichetta_colonna(col) for col in st.session_state.df_calendario.columns}

            def _sync_tutti_giorni():
                if st.session_state.get("check_tutti_giorni"):
                    st.session_state["multiselect_giorni_svuota"] = list(etichette_colonne.values())
                else:
                    st.session_state["multiselect_giorni_svuota"] = []

            st.checkbox(
                "Seleziona tutti i giorni", value=False,
                key="check_tutti_giorni", on_change=_sync_tutti_giorni,
            )
            etichette_scelte = st.multiselect(
                "Giorni", options=list(etichette_colonne.values()),
                key="multiselect_giorni_svuota",
            )
            if st.button("Svuota giorni selezionati", key="btn_svuota_giorno", disabled=not etichette_scelte):
                colonne_scelte = [c for c, e in etichette_colonne.items() if e in etichette_scelte]
                st.session_state.df_calendario.loc[:, colonne_scelte] = ""
                st.session_state.editor_calendario_versione += 1
                st.rerun()

    st.session_state.df_calendario = st.data_editor(
        st.session_state.df_calendario,
        use_container_width=True,
        key=f"editor_calendario_v{st.session_state.editor_calendario_versione}",
        column_config={
            col: st.column_config.SelectboxColumn(
                label=_etichetta_colonna(col),
                options=OPZIONI_CELLA_PASSATO if col in colonne_passato_correnti else OPZIONI_CELLA,
            )
            for col in st.session_state.df_calendario.columns
        },
    )


# ---------------------------------------------------------------------------
# Costruzione input e lancio del motore
# ---------------------------------------------------------------------------

def _costruisci_input() -> InputTurnazione:
    p = st.session_state.periodo

    lavoratori = [
        Lavoratore(
            id=str(row["id"]),
            nome=str(row["nome"]),
            ore_settimanali_min=int(row["ore_settimanali_min"]),
            ore_settimanali_max=int(row["ore_settimanali_max"]),
            vincoli_personali=VincoliPersonali(mai_notti=bool(row.get("mai_notti", False))),
        )
        for _, row in st.session_state.df_lavoratori.iterrows()
        if str(row["id"]).strip()
    ]

    fabbisogno = []
    for fascia in st.session_state.df_fabbisogno_cal.index:
        for col in st.session_state.df_fabbisogno_cal.columns:
            minimo = int(st.session_state.df_fabbisogno_cal.loc[fascia, col] or 0)
            if minimo > 0:
                fabbisogno.append(Fabbisogno(giorno=int(col), fascia=fascia, minimo=minimo))

    richieste_soft = []
    vincoli_admin = []
    stato_iniziale = []
    codici_non_validi = []

    for lavoratore_id in st.session_state.df_calendario.index:
        for col in st.session_state.df_calendario.columns:
            codice = str(st.session_state.df_calendario.loc[lavoratore_id, col] or "").strip().upper()

            if col.startswith(PREFISSO_PASSATO):
                # Colonna del mese precedente: solo turno gia' effettuato
                # (AM/AP/AN) o nulla. Nessuna richiesta soft ne' ferie qui.
                if not codice:
                    continue
                if codice in ("AM", "AP", "AN"):
                    giorno_prec = int(col[len(PREFISSO_PASSATO):])
                    stato_iniziale.append(StatoIniziale(
                        lavoratore_id=str(lavoratore_id),
                        giorno=giorno_prec,
                        fascia=codice[1],
                        mese_precedente=True,
                    ))
                else:
                    codici_non_validi.append((lavoratore_id, col, codice))
                continue

            # Colonna del periodo corrente: richiesta soft o vincolo admin
            decodifica = _decodifica_cella(codice)
            if decodifica is None:
                if codice:
                    codici_non_validi.append((lavoratore_id, col, codice))
                continue

            giorno = int(col)
            if decodifica[0] == "richiesta":
                _, tipo, fascia, priorita = decodifica
                richieste_soft.append(RichiestaSoft(
                    id=f"req_{lavoratore_id}_{giorno}",
                    lavoratore_id=str(lavoratore_id),
                    giorno=giorno,
                    tipo=tipo,
                    fascia=fascia,
                    priorita=priorita,
                ))
            else:
                _, tipo, fascia = decodifica
                vincoli_admin.append(VincoloAdmin(
                    id=f"adm_{lavoratore_id}_{giorno}",
                    lavoratore_id=str(lavoratore_id),
                    giorno=giorno,
                    tipo=tipo,
                    fascia=fascia,
                ))

    if codici_non_validi:
        dettaglio = ", ".join(f"{w}/{c}: '{v}'" for w, c, v in codici_non_validi[:5])
        st.warning(f"Alcuni codici nella griglia calendario non sono validi e sono stati ignorati: {dettaglio}")

    regole = RegoleContrattuali(
        max_notti_consecutive=int(st.session_state.max_notti_consecutive),
        giorni_riposo_dopo_notte=int(st.session_state.giorni_riposo_dopo_notte),
        max_giorni_consecutivi_lavorati=int(st.session_state.max_giorni_consecutivi_lavorati),
        giorni_riposo_dopo_serie_lavorativa=int(st.session_state.giorni_riposo_dopo_serie_lavorativa),
        vieta_pm_consecutivo=bool(st.session_state.fairness["vieta_pm_consecutivo"]),
        minuti_per_fascia={
            "M": int(st.session_state.ore_M) * 60 + int(st.session_state.minuti_M),
            "P": int(st.session_state.ore_P) * 60 + int(st.session_state.minuti_P),
            "N": int(st.session_state.ore_N) * 60 + int(st.session_state.minuti_N),
        },
        minuti_ferie_giornaliere=(
            int(st.session_state.ore_ferie_giornaliere) * 60
            + int(st.session_state.minuti_ferie_giornaliere)
        ),
    )

    fairness = ParametriFairness(
        bilancia_fasce=st.session_state.fairness["bilancia_fasce"],
        bilancia_fasce_hard=st.session_state.fairness["bilancia_fasce_hard"],
        scarto_massimo_M=int(st.session_state.fairness["scarto_massimo_M"]),
        scarto_massimo_P=int(st.session_state.fairness["scarto_massimo_P"]),
        scarto_massimo_N=int(st.session_state.fairness["scarto_massimo_N"]),
        bilancia_giorni_settimana=st.session_state.fairness["bilancia_giorni_settimana"],
        bilancia_ore_settimanali=st.session_state.fairness["bilancia_ore_settimanali"],
        bilancia_copertura_giornaliera=st.session_state.fairness["bilancia_copertura_giornaliera"],
        minimizza_pm_consecutivo=st.session_state.fairness["minimizza_pm_consecutivo"],
        bilancia_proporzione_giornaliera=st.session_state.fairness["bilancia_proporzione_giornaliera"],
        peso_bilancia_fasce=int(st.session_state.peso_bilancia_fasce),
        peso_bilancia_giorni_settimana=int(st.session_state.peso_bilancia_giorni_settimana),
        peso_bilancia_ore_settimanali=int(st.session_state.peso_bilancia_ore_settimanali),
        peso_bilancia_copertura_giornaliera=int(st.session_state.peso_bilancia_copertura_giornaliera),
        peso_minimizza_pm_consecutivo=int(st.session_state.peso_minimizza_pm_consecutivo),
        peso_bilancia_proporzione_giornaliera=int(st.session_state.peso_bilancia_proporzione_giornaliera),
    )

    return InputTurnazione(
        reparto_id="rep_streamlit",
        categoria="infermieri",
        periodo=Periodo(**p),
        lavoratori=lavoratori,
        fabbisogno=fabbisogno,
        richieste_soft=richieste_soft,
        vincoli_admin=vincoli_admin,
        regole_contrattuali=regole,
        parametri_fairness=fairness,
        stato_iniziale=stato_iniziale,
    )


st.divider()
st.session_state.setdefault("tempo_max_secondi", 30)
st.session_state["tempo_max_secondi"] = st.slider(
    "Tempo massimo di calcolo (secondi)",
    min_value=5, max_value=300, value=st.session_state["tempo_max_secondi"], step=5,
    help=(
        "Se il motore non riesce a dimostrare che la soluzione trovata e' "
        "la migliore possibile entro questo tempo, la restituisce comunque "
        "(potrebbe non essere ottimale). Dopo aver generato, controlla "
        "l'indicazione di ottimalita': se non e' stata dimostrata, alzare "
        "questo valore e rigenerare puo' migliorare il risultato."
    ),
)

if st.button("Genera turni", type="primary"):
    try:
        dati = _costruisci_input()
        tempo_max = st.session_state["tempo_max_secondi"]

        # Il calcolo vero e proprio (genera_turni, bloccante) gira in un
        # thread separato, cosi' lo script principale di Streamlit resta
        # libero di aggiornare un contatore del tempo trascorso ogni
        # frazione di secondo — un semplice st.spinner mostrerebbe solo
        # un messaggio statico, senza indicazione di quanto tempo e'
        # davvero passato rispetto al limite impostato.
        risultato_container: dict = {}

        def _esegui_calcolo():
            try:
                risultato_container["risultato"] = genera_turni(dati, tempo_max_secondi=tempo_max)
            except Exception as exc:  # catturato qui, rilanciato piu' sotto nel thread principale
                risultato_container["errore"] = exc

        thread_calcolo = threading.Thread(target=_esegui_calcolo, daemon=True)
        istante_inizio = time.time()
        thread_calcolo.start()

        placeholder_contatore = st.empty()
        while thread_calcolo.is_alive():
            trascorso = time.time() - istante_inizio
            placeholder_contatore.info(
                f"⏳ Calcolo dello schema turni in corso... **{trascorso:.0f}s** "
                f"(limite impostato: {tempo_max}s)"
            )
            time.sleep(0.3)
        thread_calcolo.join()
        trascorso_totale = time.time() - istante_inizio
        placeholder_contatore.empty()

        if "errore" in risultato_container:
            raise risultato_container["errore"]

        st.session_state.risultato = risultato_container["risultato"]
        st.session_state.ultimo_input = dati
        st.success(f"Calcolo completato in {trascorso_totale:.1f}s")
    except Exception as e:
        st.error(f"Errore nella costruzione dei dati o nel calcolo: {e}")
        st.session_state.risultato = None


# ---------------------------------------------------------------------------
# Visualizzazione risultato
# ---------------------------------------------------------------------------

risultato = st.session_state.get("risultato")
ultimo_input = st.session_state.get("ultimo_input")

if risultato is not None:
    if risultato.stato == "infeasible":
        st.error(
            "Nessuna soluzione trovata: il motore ha DIMOSTRATO che i "
            "vincoli inseriti (fabbisogno, vincoli admin, regole "
            "contrattuali) sono incompatibili tra loro — non esiste "
            "alcuna soluzione possibile. Prova a ridurre il fabbisogno "
            "minimo o i vincoli forzati."
        )
    elif risultato.stato == "tempo_scaduto":
        st.warning(
            f"Il tempo massimo di calcolo ({risultato.tempo_impiegato_secondi:.0f}s) "
            "e' scaduto **prima** che il motore trovasse una soluzione o "
            "dimostrasse che i vincoli sono incompatibili. Questo **non** "
            "significa che il problema sia irrisolvibile — con problemi "
            "complessi (tanti lavoratori, vincoli stretti) il motore "
            "potrebbe semplicemente aver bisogno di piu' tempo. Prova ad "
            "alzare il 'Tempo massimo di calcolo' sopra e rigenerare."
        )
    else:
        if risultato.stato == "feasible_con_declassamenti":
            st.warning(
                "Soluzione trovata, ma alcune richieste dei lavoratori non "
                "sono state soddisfatte (vedi sotto)."
            )
        else:
            st.success("Soluzione trovata: tutte le richieste sono state soddisfatte.")

        if risultato.ottimalita_provata:
            st.caption(
                f"✅ Ottimalita' dimostrata in {risultato.tempo_impiegato_secondi:.1f}s: "
                "il motore ha verificato che non esiste una soluzione migliore "
                "di questa. Aumentare il tempo massimo non cambierebbe il risultato."
            )
        else:
            st.caption(
                f"⏱️ Tempo massimo ({risultato.tempo_impiegato_secondi:.0f}s) esaurito "
                "prima di dimostrare l'ottimalita': questa e' la migliore soluzione "
                "trovata finora, ma potrebbe esistere di meglio. Prova ad alzare "
                "il 'Tempo massimo di calcolo' sopra e rigenerare."
            )

        # Griglia lavoratore x giorno
        df_ass = pd.DataFrame([
            {"lavoratore_id": a.lavoratore_id, "giorno": a.giorno, "fascia": a.fascia}
            for a in risultato.assegnazioni
        ])

        lavoratori_ordinati = st.session_state.df_lavoratori["id"].tolist()
        giorni_periodo = _giorni_correnti()

        griglia = pd.DataFrame(index=lavoratori_ordinati, columns=giorni_periodo)
        griglia[:] = ""
        for _, row in df_ass.iterrows():
            if row["lavoratore_id"] in griglia.index and row["giorno"] in griglia.columns:
                griglia.loc[row["lavoratore_id"], row["giorno"]] = row["fascia"]

        # Segna ferie e riposo con etichette distinte (non piu' genericamente
        # "FERIE" per entrambi), sia per i vincoli admin sia per le
        # richieste soft effettivamente accolte dal motore — prima solo i
        # vincoli admin venivano etichettati, le richieste soft accolte
        # restavano genericamente vuote.
        if ultimo_input:
            id_non_soddisfatte = {r.richiesta_id for r in risultato.richieste_non_soddisfatte}

            for v in ultimo_input.vincoli_admin:
                if v.tipo in ("ferie", "riposo"):
                    if v.lavoratore_id in griglia.index and v.giorno in griglia.columns:
                        griglia.loc[v.lavoratore_id, v.giorno] = "FERIE" if v.tipo == "ferie" else "RIPOSO"

            for r in ultimo_input.richieste_soft:
                if r.tipo in ("ferie", "riposo") and r.id not in id_non_soddisfatte:
                    if r.lavoratore_id in griglia.index and r.giorno in griglia.columns:
                        # non sovrascrive un eventuale vincolo admin sulla
                        # stessa cella, che ha sempre precedenza logica
                        if griglia.loc[r.lavoratore_id, r.giorno] == "":
                            griglia.loc[r.lavoratore_id, r.giorno] = "FERIE" if r.tipo == "ferie" else "RIPOSO"

        def _colora(val):
            colore = COLORI_FASCIA.get(val, "white")
            return f"background-color: {colore}"

        # Colonne della situazione iniziale (mese precedente): stessi
        # giorni e stesso contenuto della griglia "Situazione iniziale +
        # Richieste/Vincoli" — cosi' lo Schema turni mostra il quadro
        # completo (cosa e' gia' successo + cosa il motore ha assegnato),
        # non solo il periodo pianificato.
        colonne_passato_st = _colonne_passato()
        griglia_passato = pd.DataFrame(index=lavoratori_ordinati, columns=colonne_passato_st)
        griglia_passato[:] = ""
        if ultimo_input:
            for si in ultimo_input.stato_iniziale:
                if not si.mese_precedente:
                    continue
                col = f"{PREFISSO_PASSATO}{si.giorno}"
                if si.lavoratore_id in griglia_passato.index and col in griglia_passato.columns:
                    griglia_passato.loc[si.lavoratore_id, col] = si.fascia

        # Le colonne del periodo sono attualmente interi (es. 1, 2, 33):
        # le converto in stringa per poter applicare la stessa
        # _etichetta_colonna() (con le stesse icone 🕓/➡️) gia' usata
        # nella griglia di input, invece della vecchia _etichetta_giorno()
        # che non distingueva le tre zone.
        griglia_periodo_str = griglia.rename(columns={g: str(g) for g in griglia.columns})
        griglia_completa = pd.concat([griglia_passato, griglia_periodo_str], axis=1)

        st.subheader("Schema turni")
        st.caption(
            "Colori: 🟡 giallo = Mattino · 🔵 blu = Pomeriggio · 🟣 viola = "
            "Notte · grigio = Ferie · grigio chiarissimo = Riposo esplicito "
            "(forzato o richiesto) · bianco = nessun turno quel giorno "
            "(giorno libero senza un riposo esplicito registrato). Le "
            "colonne 🕓 (situazione iniziale) mostrano gli stessi turni "
            "gia' effettuati inseriti nella griglia di input, per "
            "contesto — non sono decisioni del motore."
        )
        griglia_display = griglia_completa.rename(
            columns={c: _etichetta_colonna(c) for c in griglia_completa.columns}
        )
        try:
            griglia_stilizzata = griglia_display.style.map(_colora)
        except AttributeError:
            griglia_stilizzata = griglia_display.style.applymap(_colora)
        st.dataframe(griglia_stilizzata, use_container_width=True)

        st.caption(
            "Vuoi modificare qualche turno a mano e ricalcolare tenendo "
            "fermo il resto? Carica questo risultato nella scheda "
            "Calendario: ogni turno assegnato diventa un vincolo admin "
            "(AM/AP/AN) — i giorni senza assegnazione restano come sono "
            "gia' impostati. Poi modifica le celle che vuoi cambiare e "
            "premi di nuovo 'Genera turni'."
        )
        if st.button("Carica questo risultato come vincoli nella scheda Calendario"):
            for a in risultato.assegnazioni:
                col_giorno = str(a.giorno)
                if (
                    a.lavoratore_id in st.session_state.df_calendario.index
                    and col_giorno in st.session_state.df_calendario.columns
                ):
                    st.session_state.df_calendario.loc[a.lavoratore_id, col_giorno] = _codice_da_admin("turno", a.fascia)
            st.session_state.editor_calendario_versione += 1
            st.success(
                "Risultato caricato come vincoli nella scheda Calendario. "
                "Vai li' per modificare le celle che vuoi cambiare, poi "
                "premi di nuovo 'Genera turni'."
            )
            st.rerun()

        # Copertura effettiva vs fabbisogno, per giorno e fascia: utile per
        # capire quanto la soluzione si discosta dal minimo richiesto (il
        # motore garantisce sempre >= fabbisogno, ma puo' assegnare surplus
        # in alcuni giorni per soddisfare altri vincoli/obiettivi)
        st.subheader("Copertura effettiva vs fabbisogno")
        st.caption(
            "Per ogni fascia (riga) e giorno (colonna): turni effettivamente "
            "assegnati, fabbisogno minimo richiesto, e differenza (scarto). "
            "Uno scarto positivo (evidenziato in arancione) significa che il "
            "motore ha assegnato piu' persone del minimo richiesto quel "
            "giorno — normale, non un errore. Uno scarto negativo non "
            "dovrebbe mai comparire: la copertura minima e' un vincolo "
            "sempre rispettato."
        )

        conteggio_effettivo = defaultdict(int)
        for a in risultato.assegnazioni:
            conteggio_effettivo[(a.giorno, a.fascia)] += 1

        righe_copertura = {}
        for f in ("M", "P", "N"):
            riga_effettivo = {}
            riga_richiesto = {}
            riga_scarto = {}
            for g in giorni_periodo:
                etichetta_col = _etichetta_giorno(g)
                effettivo = conteggio_effettivo.get((g, f), 0)
                richiesto = 0
                col_fab = str(g)
                if col_fab in st.session_state.df_fabbisogno_cal.columns:
                    richiesto = int(st.session_state.df_fabbisogno_cal.loc[f, col_fab] or 0)
                riga_effettivo[etichetta_col] = effettivo
                riga_richiesto[etichetta_col] = richiesto
                riga_scarto[etichetta_col] = effettivo - richiesto
            righe_copertura[f"{f} effettivo"] = riga_effettivo
            righe_copertura[f"{f} richiesto"] = riga_richiesto
            righe_copertura[f"{f} scarto"] = riga_scarto

        df_copertura = pd.DataFrame(righe_copertura).T
        # Riordina le colonne (giorni) in ordine cronologico invece che
        # alfabetico, dato che pd.DataFrame(dict).T puo' riordinarle
        df_copertura = df_copertura[[_etichetta_giorno(g) for g in giorni_periodo]]

        def _colora_scarto(val):
            if isinstance(val, (int, float)) and val > 0:
                return "color: #B8860B"  # surplus rispetto al minimo
            return ""

        righe_scarto = [r for r in df_copertura.index if r.endswith("scarto")]
        try:
            styler_copertura = df_copertura.style.map(_colora_scarto, subset=(righe_scarto, slice(None)))
        except AttributeError:
            styler_copertura = df_copertura.style.applymap(_colora_scarto, subset=(righe_scarto, slice(None)))
        st.dataframe(styler_copertura, use_container_width=True)

        # Insights: turni e ore per lavoratore, per fascia / settimana / mese
        st.subheader("Turni per lavoratore")
        st.markdown(
            "- **M / P / N / Ferie / Totale turni / Ore M / Ore P / Ore N / Ore F**: "
            "solo il mese di riferimento (esclude situazione iniziale e "
            "l'eventuale sconfinamento nel mese successivo)\n"
            "- **Ferie**: conta i giorni di ferie (forzati dall'admin o da "
            "richiesta soft accolta); non e' inclusa nel Totale turni "
            "perche' non e' un turno lavorato — **Ore F** sono le sue ore "
            "virtuali equivalenti\n"
            "- **Ore sett.N**: ore effettivamente lavorate PIU' le ore "
            "virtuali di ferie (stesso criterio del motore per il vincolo "
            "di ore settimanali), incluse situazione iniziale ed eventuale "
            "sconfinamento nel mese successivo\n"
            "- **Ore mese**: solo le ore effettivamente lavorate nel mese "
            "di riferimento (non include le ore virtuali di ferie, a "
            "differenza di \"Ore sett.N\")"
        )

        lavoratori_ordinati_insights = st.session_state.df_lavoratori["id"].tolist()
        nomi_per_id = dict(zip(
            st.session_state.df_lavoratori["id"], st.session_state.df_lavoratori["nome"]
        ))

        minuti_per_fascia_effettive = (
            ultimo_input.regole_contrattuali.minuti_per_fascia if ultimo_input
            else {"M": 480, "P": 480, "N": 600}
        )
        minuti_ferie_giornaliere_effettive = (
            ultimo_input.regole_contrattuali.minuti_ferie_giornaliere if ultimo_input else 480
        )
        p_ref = st.session_state.periodo
        anno_ref, mese_ref = int(p_ref["anno"]), int(p_ref["mese"])

        # Turni (M/P/N, Totale) e ore del SOLO mese di riferimento: esclude
        # sia la situazione iniziale (mese precedente, che comunque non
        # rientra mai in risultato.assegnazioni) sia l'eventuale
        # sconfinamento nel mese successivo (giorni oltre la fine del
        # mese selezionato, che invece SONO in risultato.assegnazioni dato
        # che il periodo elaborato si estende fino alla domenica di
        # chiusura settimana).
        conteggi_m = defaultdict(int)
        conteggi_p = defaultdict(int)
        conteggi_n = defaultdict(int)
        conteggi_per_fascia = {"M": conteggi_m, "P": conteggi_p, "N": conteggi_n}
        conteggi_ferie = defaultdict(int)
        minuti_mese_per_lavoratore = defaultdict(int)

        for a in risultato.assegnazioni:
            data = data_da_indice_periodo(anno_ref, mese_ref, a.giorno)
            if data.month == mese_ref and data.year == anno_ref:
                if a.fascia in conteggi_per_fascia:
                    conteggi_per_fascia[a.fascia][a.lavoratore_id] += 1
                minuti_mese_per_lavoratore[a.lavoratore_id] += minuti_per_fascia_effettive.get(a.fascia, 0)

        # Giorni di ferie del mese di riferimento (admin forzata o
        # richiesta soft accolta), per il conteggio "Ferie" in tabella.
        id_non_soddisfatte = (
            {r.richiesta_id for r in risultato.richieste_non_soddisfatte} if risultato else set()
        )
        if ultimo_input:
            for v in ultimo_input.vincoli_admin:
                if v.tipo == "ferie":
                    data = data_da_indice_periodo(anno_ref, mese_ref, v.giorno)
                    if data.month == mese_ref and data.year == anno_ref:
                        conteggi_ferie[v.lavoratore_id] += 1
            for r in ultimo_input.richieste_soft:
                if r.tipo == "ferie" and r.id not in id_non_soddisfatte:
                    data = data_da_indice_periodo(anno_ref, mese_ref, r.giorno)
                    if data.month == mese_ref and data.year == anno_ref:
                        conteggi_ferie[r.lavoratore_id] += 1

        # Ore per settimana ISO (lun-dom), per lavoratore: qui invece
        # includiamo DI PROPOSITO sia le assegnazioni del periodo esteso
        # (anche i giorni nel mese successivo) sia le ore di situazione
        # iniziale che cadono nella STESSA settimana solare del periodo,
        # PIU' le ore virtuali di ferie (stesso criterio usato dal motore
        # per il vincolo di ore settimanali — se non le contassimo qui,
        # questa tabella non rispecchierebbe fedelmente cosa succede
        # internamente).
        #
        # ATTENZIONE: la griglia mostra sempre almeno
        # GIORNI_STATO_INIZIALE_MINIMO giorni di situazione iniziale per
        # motivi di leggibilita' (completare la settimana calendario a
        # schermo), ma quando il mese inizia lun-ven questo puo' includere
        # giorni di una settimana ISO PRECEDENTE a quella del periodo (es.
        # mese che inizia mercoledi': i primi 2 dei 4 giorni mostrati
        # cadono nella settimana prima, che il motore non sta affatto
        # pianificando). Queste voci vanno escluse dal conteggio, altrimenti
        # comparirebbe una colonna "Ore settimana" per una settimana
        # completamente estranea al periodo — scartate qui confrontando la
        # settimana ISO di ciascuna voce con quella del primo giorno del
        # periodo (l'unica settimana con cui la situazione iniziale puo'
        # davvero sovrapporsi, per costruzione).
        minuti_settimana_per_lavoratore = defaultdict(lambda: defaultdict(int))
        settimane_incontrate = {}  # chiave iso -> (data_inizio, data_fine) per etichette ordinate

        if ultimo_input:
            chiave_prima_settimana_periodo = data_da_indice_periodo(anno_ref, mese_ref, 1).isocalendar()[:2]

            for a in risultato.assegnazioni:
                data = data_da_indice_periodo(anno_ref, mese_ref, a.giorno)
                chiave = data.isocalendar()[:2]
                minuti = minuti_per_fascia_effettive.get(a.fascia, 0)
                minuti_settimana_per_lavoratore[a.lavoratore_id][chiave] += minuti
                settimane_incontrate.setdefault(chiave, chiave)

            for si in ultimo_input.stato_iniziale:
                if not si.mese_precedente:
                    continue
                data = data_da_indice_mese_precedente(anno_ref, mese_ref, si.giorno)
                chiave = data.isocalendar()[:2]
                if chiave != chiave_prima_settimana_periodo:
                    continue  # settimana estranea al periodo, mostrata solo per contesto visivo
                minuti = minuti_per_fascia_effettive.get(si.fascia, 0)
                minuti_settimana_per_lavoratore[si.lavoratore_id][chiave] += minuti
                settimane_incontrate.setdefault(chiave, chiave)

            # Minuti virtuali di ferie: admin forzata (sempre) o richiesta
            # soft accolta (solo se non e' tra le non soddisfatte).
            for v in ultimo_input.vincoli_admin:
                if v.tipo == "ferie":
                    data = data_da_indice_periodo(anno_ref, mese_ref, v.giorno)
                    chiave = data.isocalendar()[:2]
                    minuti_settimana_per_lavoratore[v.lavoratore_id][chiave] += minuti_ferie_giornaliere_effettive
                    settimane_incontrate.setdefault(chiave, chiave)
            for r in ultimo_input.richieste_soft:
                if r.tipo == "ferie" and r.id not in id_non_soddisfatte:
                    data = data_da_indice_periodo(anno_ref, mese_ref, r.giorno)
                    chiave = data.isocalendar()[:2]
                    minuti_settimana_per_lavoratore[r.lavoratore_id][chiave] += minuti_ferie_giornaliere_effettive
                    settimane_incontrate.setdefault(chiave, chiave)

        settimane_ordinate = sorted(settimane_incontrate.keys())

        def _etichetta_settimana(chiave):
            anno_iso, settimana_iso = chiave
            lun = datetime.date.fromisocalendar(anno_iso, settimana_iso, 1)
            dom = datetime.date.fromisocalendar(anno_iso, settimana_iso, 7)
            return f"Ore sett.{settimana_iso} ({lun.strftime('%d/%m')}-{dom.strftime('%d/%m')})"

        def _minuti_a_ore(minuti: int) -> float:
            """Converte minuti in ore decimali per la visualizzazione (es.
            450 minuti -> 7.5 ore), arrotondate a 2 decimali."""
            return round(minuti / 60, 2)

        nome_mese_ref = MESI_IT[mese_ref]

        righe_insights = []
        for w in lavoratori_ordinati_insights:
            m, p, n = conteggi_m.get(w, 0), conteggi_p.get(w, 0), conteggi_n.get(w, 0)
            ferie = conteggi_ferie.get(w, 0)
            riga = {
                "lavoratore_id": w,
                "nome": nomi_per_id.get(w, ""),
                "M": m, "P": p, "N": n,
                "Ferie": ferie,
                "Totale turni": m + p + n,
                "Ore M": _minuti_a_ore(m * minuti_per_fascia_effettive.get("M", 0)),
                "Ore P": _minuti_a_ore(p * minuti_per_fascia_effettive.get("P", 0)),
                "Ore N": _minuti_a_ore(n * minuti_per_fascia_effettive.get("N", 0)),
                "Ore F": _minuti_a_ore(ferie * minuti_ferie_giornaliere_effettive),
            }
            for chiave in settimane_ordinate:
                riga[_etichetta_settimana(chiave)] = _minuti_a_ore(minuti_settimana_per_lavoratore[w].get(chiave, 0))
            riga[f"Ore mese ({nome_mese_ref})"] = _minuti_a_ore(minuti_mese_per_lavoratore.get(w, 0))
            righe_insights.append(riga)

        df_insights = pd.DataFrame(righe_insights).set_index("lavoratore_id")
        st.dataframe(df_insights, use_container_width=True)

        # Richieste non soddisfatte
        if risultato.richieste_non_soddisfatte and ultimo_input:
            st.subheader("Richieste non soddisfatte")
            st.caption(
                "Richieste soft (ferie, riposo o turno specifico) che il "
                "motore non e' riuscito a concedere per rispettare gli "
                "altri vincoli. Non e' un errore: succede quando troppe "
                "richieste si sovrappongono sullo stesso giorno/fascia, o "
                "quando concederle violerebbe un vincolo hard (es. "
                "copertura minima, ore settimanali). Le richieste con "
                "priorita' piu' alta vengono sacrificate per ultime."
            )
            richieste_per_id = {r.id: r for r in ultimo_input.richieste_soft}
            righe = []
            for r in risultato.richieste_non_soddisfatte:
                dettaglio = richieste_per_id.get(r.richiesta_id)
                if dettaglio:
                    righe.append({
                        "lavoratore_id": dettaglio.lavoratore_id,
                        "giorno": dettaglio.giorno,
                        "tipo": dettaglio.tipo,
                        "fascia": dettaglio.fascia or "",
                        "priorita": f"{dettaglio.priorita} ({PRIORITA_LABEL.get(dettaglio.priorita, '')})",
                    })
            st.dataframe(pd.DataFrame(righe), use_container_width=True)

        # Metriche fairness
        st.subheader("Equilibrio del carico tra lavoratori")
        st.caption("Ore per fascia (M/P/N) e ore virtuali di ferie (F) per lavoratore, mese di riferimento.")
        colonne_ore_grafico = ["Ore M", "Ore P", "Ore N", "Ore F"]  # ordine esplicito M-P-N-F
        if not df_insights.empty and all(c in df_insights.columns for c in colonne_ore_grafico):
            df_ore_grafico = df_insights[colonne_ore_grafico].copy()
            df_ore_grafico["lavoratore"] = df_insights["nome"]

            # st.bar_chart (basato su Vega-Lite) forza l'ordine alfabetico
            # delle serie impilate/legenda, ignorando l'ordine della lista
            # passata a y= (limite noto, issue aperta su GitHub: la libreria
            # non espone un parametro per controllarlo). Costruiamo il
            # grafico direttamente con Altair per avere il pieno controllo
            # sull'ordine, sia nell'impilamento sia nella legenda.
            df_lungo = df_ore_grafico.melt(
                id_vars="lavoratore", value_vars=colonne_ore_grafico,
                var_name="fascia", value_name="ore",
            )
            ordine_fascia = {c: i for i, c in enumerate(colonne_ore_grafico)}
            df_lungo["ordine"] = df_lungo["fascia"].map(ordine_fascia)

            grafico = (
                alt.Chart(df_lungo)
                .mark_bar()
                .encode(
                    y=alt.Y("lavoratore:N", sort=None, title="lavoratore"),
                    x=alt.X("ore:Q", title="ore"),
                    color=alt.Color("fascia:N", sort=colonne_ore_grafico, title="fascia"),
                    order=alt.Order("ordine:Q"),
                    tooltip=["lavoratore", "fascia", "ore"],
                )
            )
            st.altair_chart(grafico, use_container_width=True)
else:
    st.info("Configura i dati nelle schede sopra, poi premi 'Genera turni'.")
