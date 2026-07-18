"""
Script diagnostico mirato: ricostruisce lo scenario ESATTO segnalato
(dal CSV della griglia "Situazione iniziale + Richieste/Vincoli") e
stampa la contabilita' INTERNA che il motore usa per il vincolo di ore
settimanali — cosi' confrontiamo cosa il motore calcola davvero contro
cosa mostra la tabella "Turni per lavoratore", invece di continuare a
ipotizzare a partire dalla sola lettura del codice.

USO:
    python diagnostica_ore_settimana.py percorso/al/csv_situazione.csv

Il CSV deve essere quello scaricato con "Esporta / Importa CSV" dalla
griglia "Situazione iniziale + Richieste/Vincoli" (colonne S27..S30,
poi 1,2,3,... del periodo).

Modifica i parametri sotto (ANNO, MESE, regole contrattuali, numero
lavoratori, fabbisogno) per farli corrispondere esattamente al tuo
scenario prima di lanciare lo script.
"""

import sys
import csv as csv_module

from engine.models import (
    InputTurnazione, Periodo, Lavoratore, VincoliPersonali, Fabbisogno,
    VincoloAdmin, RichiestaSoft, StatoIniziale, RegoleContrattuali,
    ParametriFairness,
)
from engine.solver import genera_turni
from engine.periodo_utils import (
    calcola_giorno_fine_periodo, data_da_indice_periodo, data_da_indice_mese_precedente,
)

# ---------------------------------------------------------------------
# PARAMETRI DA ADATTARE al tuo scenario esatto
# ---------------------------------------------------------------------
ANNO = 2026
MESE = 7
N_LAVORATORI = 15  # solo i primi N (w1..wN)
ORE_MIN_PERSONA = 36
ORE_MAX_PERSONA = 40
MINUTI_M = 7 * 60 + 15   # 7h15m
MINUTI_P = 7 * 60 + 15   # 7h15m
MINUTI_N = 9 * 60 + 15   # 9h15m
MINUTI_FERIE = 8 * 60    # 8h
FABBISOGNO_M = 3
FABBISOGNO_P = 3
FABBISOGNO_N = 2
PREFISSO_PASSATO = "S"


def _decodifica_cella(codice: str):
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
    return None


def carica_csv(percorso):
    with open(percorso, encoding="utf-8-sig") as f:
        reader = csv_module.DictReader(f)
        righe = list(reader)

    richieste_soft, vincoli_admin, stato_iniziale = [], [], []
    for riga in righe:
        lavoratore_id = riga["lavoratore_id"]
        for col, val in riga.items():
            if col == "lavoratore_id":
                continue
            codice = (val or "").strip().upper()
            if not codice:
                continue
            if col.startswith(PREFISSO_PASSATO):
                if codice in ("AM", "AP", "AN"):
                    giorno_prec = int(col[len(PREFISSO_PASSATO):])
                    stato_iniziale.append(StatoIniziale(
                        lavoratore_id=lavoratore_id, giorno=giorno_prec,
                        fascia=codice[1], mese_precedente=True,
                    ))
                continue
            decodifica = _decodifica_cella(codice)
            if decodifica is None:
                continue
            giorno = int(col)
            if decodifica[0] == "richiesta":
                _, tipo, fascia, priorita = decodifica
                richieste_soft.append(RichiestaSoft(
                    id=f"req_{lavoratore_id}_{giorno}", lavoratore_id=lavoratore_id,
                    giorno=giorno, tipo=tipo, fascia=fascia, priorita=priorita,
                ))
            else:
                _, tipo, fascia = decodifica
                vincoli_admin.append(VincoloAdmin(
                    id=f"adm_{lavoratore_id}_{giorno}", lavoratore_id=lavoratore_id,
                    giorno=giorno, tipo=tipo, fascia=fascia,
                ))
    return richieste_soft, vincoli_admin, stato_iniziale


def main():
    if len(sys.argv) < 2:
        print("Uso: python diagnostica_ore_settimana.py percorso/al/csv_situazione.csv")
        sys.exit(1)

    richieste_soft, vincoli_admin, stato_iniziale = carica_csv(sys.argv[1])
    richieste_soft = [r for r in richieste_soft if int(r.lavoratore_id[1:]) <= N_LAVORATORI]
    vincoli_admin = [v for v in vincoli_admin if int(v.lavoratore_id[1:]) <= N_LAVORATORI]
    stato_iniziale = [s for s in stato_iniziale if int(s.lavoratore_id[1:]) <= N_LAVORATORI]

    lavoratori = [
        Lavoratore(id=f"w{i+1}", nome=f"L{i+1}", ore_settimanali_min=ORE_MIN_PERSONA,
                   ore_settimanali_max=ORE_MAX_PERSONA)
        for i in range(N_LAVORATORI)
    ]

    giorno_fine = calcola_giorno_fine_periodo(ANNO, MESE)
    fabbisogno = []
    for giorno in range(1, giorno_fine + 1):
        fabbisogno.append(Fabbisogno(giorno=giorno, fascia="M", minimo=FABBISOGNO_M))
        fabbisogno.append(Fabbisogno(giorno=giorno, fascia="P", minimo=FABBISOGNO_P))
        fabbisogno.append(Fabbisogno(giorno=giorno, fascia="N", minimo=FABBISOGNO_N))

    dati = InputTurnazione(
        reparto_id="diagnostica",
        categoria="infermieri",
        periodo=Periodo(anno=ANNO, mese=MESE, giorno_inizio=1, giorno_fine=giorno_fine),
        lavoratori=lavoratori,
        fabbisogno=fabbisogno,
        vincoli_admin=vincoli_admin,
        richieste_soft=richieste_soft,
        stato_iniziale=stato_iniziale,
        regole_contrattuali=RegoleContrattuali(
            minuti_per_fascia={"M": MINUTI_M, "P": MINUTI_P, "N": MINUTI_N},
            minuti_ferie_giornaliere=MINUTI_FERIE,
        ),
        parametri_fairness=ParametriFairness(),
    )

    print(f"Lavoratori: {len(lavoratori)}, vincoli_admin: {len(vincoli_admin)}, "
          f"richieste_soft: {len(richieste_soft)}, stato_iniziale: {len(stato_iniziale)}")
    print("Genero turni (puo' richiedere fino a 30s)...")
    risultato = genera_turni(dati, tempo_max_secondi=30)
    print(f"Stato: {risultato.stato}")
    if risultato.stato not in ("feasible", "feasible_con_declassamenti"):
        print("Nessuna soluzione da analizzare.")
        return

    # Ricalcolo la settimana 27 (29/06-05/07) ESATTAMENTE come fa il
    # motore internamente (stessa logica di ore_pregresse_per_settimana
    # + ferie_forzata_per_settimana in solver.py), per ogni lavoratore.
    settimana_target = data_da_indice_periodo(ANNO, MESE, 1).isocalendar()[:2]
    print(f"\nSettimana target: {settimana_target} (quella del giorno 1 del periodo)\n")

    minuti_per_fascia = dati.regole_contrattuali.minuti_per_fascia

    for w in [l.id for l in lavoratori]:
        minuti_pregressi = 0
        for si in stato_iniziale:
            if si.lavoratore_id != w:
                continue
            data_si = data_da_indice_mese_precedente(ANNO, MESE, si.giorno)
            if data_si.isocalendar()[:2] == settimana_target:
                minuti_pregressi += minuti_per_fascia.get(si.fascia, 0)

        minuti_ferie_forzata = 0
        giorni_ferie_forzata = []
        for v in vincoli_admin:
            if v.lavoratore_id != w or v.tipo != "ferie":
                continue
            data_v = data_da_indice_periodo(ANNO, MESE, v.giorno)
            if data_v.isocalendar()[:2] == settimana_target:
                minuti_ferie_forzata += MINUTI_FERIE
                giorni_ferie_forzata.append(v.giorno)

        minuti_assegnati = 0
        turni_assegnati = []
        for a in risultato.assegnazioni:
            if a.lavoratore_id != w:
                continue
            data_a = data_da_indice_periodo(ANNO, MESE, a.giorno)
            if data_a.isocalendar()[:2] == settimana_target:
                minuti_assegnati += minuti_per_fascia.get(a.fascia, 0)
                turni_assegnati.append((a.giorno, a.fascia))

        totale = minuti_pregressi + minuti_ferie_forzata + minuti_assegnati
        ore_totale = totale / 60
        min_richiesto = ORE_MIN_PERSONA * 60
        ok = "OK" if totale >= min_richiesto else "*** SOTTO IL MINIMO ***"

        print(
            f"{w}: pregresse={minuti_pregressi}min ferie_forzata={minuti_ferie_forzata}min "
            f"({giorni_ferie_forzata}) assegnati={minuti_assegnati}min {turni_assegnati} "
            f"-> TOTALE={totale}min ({ore_totale:.2f}h) {ok}"
        )


if __name__ == "__main__":
    main()
