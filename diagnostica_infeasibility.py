"""
Script diagnostico per capire QUALE vincolo causa l'infeasibility dello
scenario segnalato (20 lavoratori, luglio 2026, situazione iniziale
generata di default, tutti gli altri parametri a default).

A differenza dei calcoli a mano fatti finora in chat (uno dei quali si e'
rivelato sbagliato), questo script usa il MOTORE VERO per rispondere in
modo affidabile: esegue la generazione piu' volte, rilassando UN vincolo
alla volta, e riporta quali rilassamenti trasformano il problema da
infeasible a risolvibile — cosi' isoliamo la causa reale invece di
continuare a ipotizzare.

USO:
    python diagnostica_infeasibility.py

Ogni test usa un tempo massimo breve (20s): se un test risulta
"tempo_scaduto" invece di "infeasible" o "feasible", vuol dire che quel
caso specifico e' troppo complesso per essere deciso in fretta — non e'
un'prova ne' in un senso ne' nell'altro, va rilanciato con piu' tempo
se serve capire quel caso specifico.
"""

import copy

from engine.models import RegoleContrattuali
from engine.sample_data import get_sample_input
from engine.solver import genera_turni

TEMPO_MAX_TEST = 20.0  # secondi per test — alza se troppi test danno "tempo_scaduto"


def prova(nome: str, dati) -> str:
    risultato = genera_turni(dati, tempo_max_secondi=TEMPO_MAX_TEST)
    simbolo = {
        "feasible": "✅ FEASIBLE",
        "feasible_con_declassamenti": "✅ FEASIBLE (con declassamenti)",
        "infeasible": "❌ INFEASIBLE (dimostrato impossibile)",
        "tempo_scaduto": "⏱️  TEMPO SCADUTO (non conclusivo)",
    }.get(risultato.stato, risultato.stato)
    print(f"{nome:55s} -> {simbolo}  ({risultato.tempo_impiegato_secondi:.1f}s)")
    return risultato.stato


print("=" * 90)
print("DIAGNOSTICA INFEASIBILITY — 20 lavoratori, luglio 2026, default ovunque")
print("=" * 90)
print()

# ---------------------------------------------------------------------
# BASELINE: esattamente lo scenario segnalato, cosi' com'e' oggi
# ---------------------------------------------------------------------
dati_base = get_sample_input()
print(f"Lavoratori: {len(dati_base.lavoratori)}")
print(f"Periodo: {dati_base.periodo.anno}-{dati_base.periodo.mese:02d}, "
      f"giorno_fine={dati_base.periodo.giorno_fine}")
print(f"Vincoli admin nel sample: {len(dati_base.vincoli_admin)}")
print(f"Richieste soft nel sample: {len(dati_base.richieste_soft)}")
print(f"Stato iniziale nel sample: {len(dati_base.stato_iniziale)} voci")
print()

stato_baseline = prova("0. BASELINE (scenario esatto segnalato)", copy.deepcopy(dati_base))
print()

if stato_baseline in ("feasible", "feasible_con_declassamenti"):
    print(">>> La baseline e' risolvibile: il problema segnalato potrebbe")
    print(">>> dipendere da differenze tra questo scenario e quello reale")
    print(">>> usato in app (es. situazione iniziale diversa, vincoli admin")
    print(">>> aggiunti a mano). Confronta con quanto hai davvero in app.")
    print()

# ---------------------------------------------------------------------
# TEST 1: stesso identico scenario ma senza situazione iniziale (vuota)
# ---------------------------------------------------------------------
dati_test = copy.deepcopy(dati_base)
dati_test.stato_iniziale = []
prova("1. Situazione iniziale VUOTA (nessun pregresso)", dati_test)

# ---------------------------------------------------------------------
# TEST 2: periodo che inizia di LUNEDI' (settimane tutte complete),
# stesso mese/anno non importa: usiamo giugno 2026 (1 giugno = lunedi')
# per capire se il problema e' davvero legato alla settimana parziale
# ---------------------------------------------------------------------
dati_test = copy.deepcopy(dati_base)
dati_test.periodo.anno = 2026
dati_test.periodo.mese = 6
dati_test.periodo.giorno_fine = 30  # giugno ha 30 giorni, 1 giugno = lunedi'
dati_test.stato_iniziale = []  # nessun pregresso rilevante per un mese diverso
dati_test.vincoli_admin = []
dati_test.richieste_soft = []
prova("2. Periodo che inizia di LUNEDI' (nessuna settimana parziale)", dati_test)

# ---------------------------------------------------------------------
# TEST 3: minimo ore settimanali a 0 per tutti (rimuove il vincolo minimo)
# ---------------------------------------------------------------------
dati_test = copy.deepcopy(dati_base)
for l in dati_test.lavoratori:
    l.ore_settimanali_min = 0
prova("3. Minimo ore settimanali = 0 per tutti", dati_test)

# ---------------------------------------------------------------------
# TEST 4: riposo dopo notte a 1 giorno invece di 2 (comportamento vecchio)
# ---------------------------------------------------------------------
dati_test = copy.deepcopy(dati_base)
dati_test.regole_contrattuali.giorni_riposo_dopo_notte = 1
prova("4. Riposo dopo notte = 1 giorno (invece di 2)", dati_test)

# ---------------------------------------------------------------------
# TEST 5: massimo notti consecutive piu' alto (piu' flessibilita')
# ---------------------------------------------------------------------
dati_test = copy.deepcopy(dati_base)
dati_test.regole_contrattuali.max_notti_consecutive = 4
prova("5. Massimo notti consecutive = 4 (invece di 2)", dati_test)

# ---------------------------------------------------------------------
# TEST 6: fabbisogno notturno dimezzato (1N invece di 2N al giorno)
# ---------------------------------------------------------------------
dati_test = copy.deepcopy(dati_base)
for f in dati_test.fabbisogno:
    if f.fascia == "N":
        f.minimo = 1
prova("6. Fabbisogno Notte dimezzato (1/giorno invece di 2)", dati_test)

# ---------------------------------------------------------------------
# TEST 7: tutti i vincoli fairness disattivati (isola i vincoli HARD)
# ---------------------------------------------------------------------
dati_test = copy.deepcopy(dati_base)
dati_test.parametri_fairness.bilancia_fasce = False
dati_test.parametri_fairness.bilancia_giorni_settimana = False
dati_test.parametri_fairness.bilancia_ore_settimanali = False
dati_test.parametri_fairness.bilancia_copertura_giornaliera = False
dati_test.parametri_fairness.bilancia_proporzione_giornaliera = False
dati_test.parametri_fairness.minimizza_pm_consecutivo = False
prova("7. Tutti i vincoli FAIRNESS disattivati (solo hard)", dati_test)

# ---------------------------------------------------------------------
# TEST 8: ore massime piu' alte (piu' margine per assorbire il minimo)
# ---------------------------------------------------------------------
dati_test = copy.deepcopy(dati_base)
for l in dati_test.lavoratori:
    l.ore_settimanali_max = 48
prova("8. Ore massime = 48 (invece di 40)", dati_test)

# ---------------------------------------------------------------------
# TEST 9: doppio dei lavoratori (isola se e' un problema di capacita')
# ---------------------------------------------------------------------
dati_test = copy.deepcopy(dati_base)
lavoratori_extra = []
for l in dati_test.lavoratori:
    nuovo = copy.deepcopy(l)
    nuovo.id = l.id + "_bis"
    lavoratori_extra.append(nuovo)
dati_test.lavoratori.extend(lavoratori_extra)
prova("9. Doppio dei lavoratori (40 invece di 20)", dati_test)

print()
print("=" * 90)
print("FINE DIAGNOSTICA")
print("=" * 90)
print()
print("Interpretazione: i test che passano da INFEASIBLE (baseline) a")
print("FEASIBLE indicano quale vincolo, da solo, e' sufficiente a causare")
print("l'incompatibilita'. Se PIU' test risolvono il problema, la causa e'")
print("probabilmente un effetto combinato — copia qui l'output completo e")
print("lo analizziamo insieme.")
