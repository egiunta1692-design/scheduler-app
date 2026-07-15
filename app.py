"""
Interfaccia Streamlit per il motore di turnazione.

Layout:
  - Lavoratori: tabella editabile con id/nome/ore contratto
  - Calendario: tre griglie
      1. Situazione iniziale (ultimi giorni del mese precedente): serve
         al motore per applicare correttamente riposo dopo notte e
         massimo notti consecutive anche a cavallo tra due mesi
      2. Fabbisogno (righe M/P/N, valori numerici, colonne = giorni del
         mese selezionato)
      3. Richieste soft / Vincoli admin per lavoratore (righe = lavoratori,
         una cella per giorno con un codice breve). Una cella puo'
         contenere SOLO una richiesta soft OPPURE SOLO un vincolo admin,
         mai entrambi: la mutua esclusivita' e' garantita dalla struttura
         stessa della griglia (un valore per cella).
  - Regole & periodo: anno/mese (i giorni del periodo si calcolano da
    soli in base al mese), ore per fascia, notti consecutive, fairness

Avvio: streamlit run app.py
"""

import calendar
import datetime
from collections import defaultdict

import streamlit as st
import pandas as pd

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
    "**Ferie vs riposo**: bloccano entrambe i turni quel giorno, ma la ferie conta ore "
    "virtuali nel monte ore settimanale (e' comunque tempo retribuito), il riposo no. "
    "Il motore non permette mai una ferie il giorno subito dopo una notte (o serie di "
    "notti): quel giorno di stop e' un riposo fisiologico obbligatorio, non e' "
    "sostituibile da una ferie — anche se inserita per errore, il motore la ignora in "
    "quel punto specifico e cerca un'altra soluzione.\n\n"
    "Una cella puo' contenere solo un codice alla volta: richiesta del lavoratore "
    "e vincolo del coordinatore sono alternativi, mai entrambi sullo stesso giorno.\n\n"
    "**Icone nelle intestazioni delle colonne** (Streamlit non supporta colori di "
    "sfondo nelle griglie editabili, quindi usiamo icone per distinguere le zone):\n"
    "- 🕓 = giorni del **mese precedente** (situazione iniziale, sola lettura concettuale: "
    "turni gia' avvenuti)\n"
    "- nessuna icona = giorni del **mese selezionato**\n"
    "- ➡️ = giorni del **mese successivo** (periodo esteso fino alla domenica di chiusura)\n\n"
    "Sotto la griglia trovi anche un'**anteprima colorata di sola lettura** "
    "(espandi \"Anteprima colorata\") con veri colori di sfondo per zona — "
    "utile a colpo d'occhio, ma la modifica dei dati resta nella griglia sopra."
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
            "ore_settimanali_contratto": l.ore_settimanali_contratto,
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

    st.session_state.regole = {
        "max_notti_consecutive": demo.regole_contrattuali.max_notti_consecutive,
        "ore_M": demo.regole_contrattuali.ore_per_fascia.get("M", 8),
        "ore_P": demo.regole_contrattuali.ore_per_fascia.get("P", 8),
        "ore_N": demo.regole_contrattuali.ore_per_fascia.get("N", 10),
        "ore_ferie_giornaliere": demo.regole_contrattuali.ore_ferie_giornaliere,
    }

    st.session_state.fairness = {
        "bilancia_fasce": demo.parametri_fairness.bilancia_fasce,
        "bilancia_giorni_settimana": demo.parametri_fairness.bilancia_giorni_settimana,
        "bilancia_ore_settimanali": demo.parametri_fairness.bilancia_ore_settimanali,
        "bilancia_copertura_giornaliera": demo.parametri_fairness.bilancia_copertura_giornaliera,
        "minimizza_pm_consecutivo": demo.parametri_fairness.minimizza_pm_consecutivo,
        "peso_fairness": demo.parametri_fairness.peso_fairness,
    }

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

    st.session_state.df_calendario = (
        st.session_state.df_calendario
        .reindex(index=lavoratori_ids, columns=colonne_tutte, fill_value="")
        .fillna("")
    )


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
                "ore_settimanali_contratto": 36,
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
        st.session_state.regole["max_notti_consecutive"] = st.number_input(
            "Massimo notti consecutive", value=st.session_state.regole["max_notti_consecutive"], min_value=1, max_value=5
        )
        st.session_state.regole["ore_M"] = st.number_input(
            "Ore turno Mattino", value=st.session_state.regole["ore_M"], min_value=1, max_value=12
        )
        st.session_state.regole["ore_P"] = st.number_input(
            "Ore turno Pomeriggio", value=st.session_state.regole["ore_P"], min_value=1, max_value=12
        )
        st.session_state.regole["ore_N"] = st.number_input(
            "Ore turno Notte", value=st.session_state.regole["ore_N"], min_value=1, max_value=12
        )
        st.session_state.regole["ore_ferie_giornaliere"] = st.number_input(
            "Ore ferie giornaliere",
            value=st.session_state.regole["ore_ferie_giornaliere"], min_value=0, max_value=12,
            help=(
                "Ore virtuali che una giornata di ferie aggiunge al monte ore "
                "settimanale (e' comunque tempo retribuito). Il riposo non "
                "aggiunge nulla."
            ),
        )

    with col2:
        st.subheader("Fairness (equilibrio tra lavoratori e giorni)")
        st.session_state.fairness["bilancia_fasce"] = st.checkbox(
            "Bilancia il numero di turni per fascia tra i lavoratori",
            value=st.session_state.fairness["bilancia_fasce"],
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
        st.session_state.fairness["minimizza_pm_consecutivo"] = st.checkbox(
            "Minimizza le sequenze Pomeriggio -> Mattino su giorni consecutivi",
            value=st.session_state.fairness["minimizza_pm_consecutivo"],
            help=(
                "Un turno Pomeriggio seguito da un turno Mattino il giorno dopo "
                "lascia un riposo molto piu' corto (P finisce sera tardi, M "
                "inizia presto la mattina dopo) rispetto a Mattino -> Pomeriggio "
                "(quasi un giorno intero di margine). Non viene vietato — spesso "
                "e' inevitabile per la copertura — ma minimizzato dove possibile, "
                "premiando implicitamente M->P rispetto a P->M."
            ),
        )
        st.session_state.fairness["peso_fairness"] = st.slider(
            "Peso della fairness rispetto alle richieste dei lavoratori",
            min_value=1, max_value=20, value=st.session_state.fairness["peso_fairness"],
            help="Basso = le preferenze dei lavoratori contano di piu' dell'equilibrio del team.",
        )

with tab_lavoratori:
    st.caption("Elenco del personale del reparto per questa categoria (infermieri o oss).")
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

    # Streamlit non supporta la colorazione di sfondo nelle colonne
    # EDITABILI di data_editor (limite noto della libreria, confermato
    # anche in issue aperte sul loro repository). Come compromesso,
    # mostriamo qui sotto un'anteprima di SOLA LETTURA della stessa
    # griglia, colorata per zona: la modifica dei dati avviene sempre
    # nella tabella sopra.
    with st.expander("Anteprima colorata (sola lettura)", expanded=False):
        st.caption(
            "Solo per visualizzazione: per modificare i dati usa la griglia sopra. "
            "🔵 = mese precedente (situazione iniziale) · bianco = mese selezionato · "
            "🟠 = mese successivo."
        )

        etichette = {col: _etichetta_colonna(col) for col in st.session_state.df_calendario.columns}
        df_anteprima = st.session_state.df_calendario.rename(columns=etichette)

        colonne_passato_lbl = [etichette[c] for c in _colonne_passato()]
        p_corrente = st.session_state.periodo
        colonne_estese_lbl = []
        for c in _colonne_periodo():
            data_c = data_da_indice_periodo(int(p_corrente["anno"]), int(p_corrente["mese"]), int(c))
            if data_c.month != int(p_corrente["mese"]) or data_c.year != int(p_corrente["anno"]):
                colonne_estese_lbl.append(etichette[c])

        styler_anteprima = df_anteprima.style
        if colonne_passato_lbl:
            styler_anteprima = styler_anteprima.set_properties(
                subset=colonne_passato_lbl, **{"background-color": "#D6EAF8"}
            )
        if colonne_estese_lbl:
            styler_anteprima = styler_anteprima.set_properties(
                subset=colonne_estese_lbl, **{"background-color": "#FDEBD0"}
            )

        st.dataframe(styler_anteprima, use_container_width=True)


# ---------------------------------------------------------------------------
# Costruzione input e lancio del motore
# ---------------------------------------------------------------------------

def _costruisci_input() -> InputTurnazione:
    p = st.session_state.periodo

    lavoratori = [
        Lavoratore(
            id=str(row["id"]),
            nome=str(row["nome"]),
            ore_settimanali_contratto=int(row["ore_settimanali_contratto"]),
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
        max_notti_consecutive=int(st.session_state.regole["max_notti_consecutive"]),
        ore_per_fascia={
            "M": int(st.session_state.regole["ore_M"]),
            "P": int(st.session_state.regole["ore_P"]),
            "N": int(st.session_state.regole["ore_N"]),
        },
        ore_ferie_giornaliere=int(st.session_state.regole["ore_ferie_giornaliere"]),
    )

    fairness = ParametriFairness(
        bilancia_fasce=st.session_state.fairness["bilancia_fasce"],
        bilancia_giorni_settimana=st.session_state.fairness["bilancia_giorni_settimana"],
        bilancia_ore_settimanali=st.session_state.fairness["bilancia_ore_settimanali"],
        bilancia_copertura_giornaliera=st.session_state.fairness["bilancia_copertura_giornaliera"],
        minimizza_pm_consecutivo=st.session_state.fairness["minimizza_pm_consecutivo"],
        peso_fairness=int(st.session_state.fairness["peso_fairness"]),
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
        with st.spinner(f"Calcolo dello schema turni in corso (fino a {tempo_max}s)..."):
            st.session_state.risultato = genera_turni(dati, tempo_max_secondi=tempo_max)
            st.session_state.ultimo_input = dati
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
            "Nessuna soluzione trovata: i vincoli inseriti (fabbisogno, "
            "vincoli admin, regole contrattuali) sono incompatibili tra loro. "
            "Prova a ridurre il fabbisogno minimo o i vincoli forzati."
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
        # vincoli admin venivano etichettati, le richieste soft honoree
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

        st.subheader("Schema turni")
        griglia_display = griglia.rename(columns={g: _etichetta_giorno(g) for g in griglia.columns})
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
            "assegnati, fabbisogno minimo richiesto, e differenza (surplus "
            "positivo se il motore ha assegnato piu' persone del minimo)."
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
        st.caption(
            "M / P / N / Ferie / Totale turni / Ore M / Ore P / Ore N / "
            "Ore F: solo il mese di riferimento selezionato (esclude sia "
            "la situazione iniziale del mese precedente sia l'eventuale "
            "sconfinamento nel mese successivo). 'Ferie' conta i giorni di "
            "ferie (forzati dall'admin o da richiesta soft accolta), non "
            "e' inclusa nel Totale turni perche' non e' un turno lavorato; "
            "'Ore F' sono le sue ore virtuali equivalenti. Ore per "
            "settimana (lun-dom): includono le ore effettivamente lavorate "
            "PIU' le ore virtuali di ferie (stesso criterio usato dal "
            "motore per il vincolo di ore settimanali), oltre alla "
            "situazione iniziale e agli eventuali giorni del mese "
            "successivo. Ore mese: solo le ore effettivamente lavorate nel "
            "mese di riferimento (non include le ore virtuali di ferie)."
        )

        lavoratori_ordinati_insights = st.session_state.df_lavoratori["id"].tolist()
        nomi_per_id = dict(zip(
            st.session_state.df_lavoratori["id"], st.session_state.df_lavoratori["nome"]
        ))

        ore_per_fascia_effettive = (
            ultimo_input.regole_contrattuali.ore_per_fascia if ultimo_input
            else {"M": 8, "P": 8, "N": 10}
        )
        ore_ferie_giornaliere_effettive = (
            ultimo_input.regole_contrattuali.ore_ferie_giornaliere if ultimo_input else 8
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
        ore_mese_per_lavoratore = defaultdict(int)

        for a in risultato.assegnazioni:
            data = data_da_indice_periodo(anno_ref, mese_ref, a.giorno)
            if data.month == mese_ref and data.year == anno_ref:
                if a.fascia in conteggi_per_fascia:
                    conteggi_per_fascia[a.fascia][a.lavoratore_id] += 1
                ore_mese_per_lavoratore[a.lavoratore_id] += ore_per_fascia_effettive.get(a.fascia, 0)

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
        # iniziale che cadono nella stessa settimana solare, PIU' le ore
        # virtuali di ferie (stesso criterio usato dal motore per il
        # vincolo di ore settimanali — se non le contassimo qui, questa
        # tabella non rispecchierebbe fedelmente cosa succede internamente).
        ore_settimana_per_lavoratore = defaultdict(lambda: defaultdict(int))
        settimane_incontrate = {}  # chiave iso -> (data_inizio, data_fine) per etichette ordinate

        if ultimo_input:
            for a in risultato.assegnazioni:
                data = data_da_indice_periodo(anno_ref, mese_ref, a.giorno)
                chiave = data.isocalendar()[:2]
                ore = ore_per_fascia_effettive.get(a.fascia, 0)
                ore_settimana_per_lavoratore[a.lavoratore_id][chiave] += ore
                settimane_incontrate.setdefault(chiave, chiave)

            for si in ultimo_input.stato_iniziale:
                if not si.mese_precedente:
                    continue
                data = data_da_indice_mese_precedente(anno_ref, mese_ref, si.giorno)
                chiave = data.isocalendar()[:2]
                ore = ore_per_fascia_effettive.get(si.fascia, 0)
                ore_settimana_per_lavoratore[si.lavoratore_id][chiave] += ore
                settimane_incontrate.setdefault(chiave, chiave)

            # Ore virtuali di ferie: admin forzata (sempre) o richiesta
            # soft accolta (solo se non e' tra le non soddisfatte).
            for v in ultimo_input.vincoli_admin:
                if v.tipo == "ferie":
                    data = data_da_indice_periodo(anno_ref, mese_ref, v.giorno)
                    chiave = data.isocalendar()[:2]
                    ore_settimana_per_lavoratore[v.lavoratore_id][chiave] += ore_ferie_giornaliere_effettive
                    settimane_incontrate.setdefault(chiave, chiave)
            for r in ultimo_input.richieste_soft:
                if r.tipo == "ferie" and r.id not in id_non_soddisfatte:
                    data = data_da_indice_periodo(anno_ref, mese_ref, r.giorno)
                    chiave = data.isocalendar()[:2]
                    ore_settimana_per_lavoratore[r.lavoratore_id][chiave] += ore_ferie_giornaliere_effettive
                    settimane_incontrate.setdefault(chiave, chiave)

        settimane_ordinate = sorted(settimane_incontrate.keys())

        def _etichetta_settimana(chiave):
            anno_iso, settimana_iso = chiave
            lun = datetime.date.fromisocalendar(anno_iso, settimana_iso, 1)
            dom = datetime.date.fromisocalendar(anno_iso, settimana_iso, 7)
            return f"Ore sett.{settimana_iso} ({lun.strftime('%d/%m')}-{dom.strftime('%d/%m')})"

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
                "Ore M": m * ore_per_fascia_effettive.get("M", 0),
                "Ore P": p * ore_per_fascia_effettive.get("P", 0),
                "Ore N": n * ore_per_fascia_effettive.get("N", 0),
                "Ore F": ferie * ore_ferie_giornaliere_effettive,
            }
            for chiave in settimane_ordinate:
                riga[_etichetta_settimana(chiave)] = ore_settimana_per_lavoratore[w].get(chiave, 0)
            riga[f"Ore mese ({nome_mese_ref})"] = ore_mese_per_lavoratore.get(w, 0)
            righe_insights.append(riga)

        df_insights = pd.DataFrame(righe_insights).set_index("lavoratore_id")
        st.dataframe(df_insights, use_container_width=True)

        # Richieste non soddisfatte
        if risultato.richieste_non_soddisfatte and ultimo_input:
            st.subheader("Richieste non soddisfatte")
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
        giorni_lavorati = risultato.metriche_fairness.get("giorni_lavorati_per_lavoratore", {})
        if giorni_lavorati:
            st.bar_chart(pd.Series(giorni_lavorati, name="giorni lavorati"))
else:
    st.info("Configura i dati nelle schede sopra, poi premi 'Genera turni'.")
