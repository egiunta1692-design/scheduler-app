"""
Funzioni di supporto per il calcolo del periodo e la conversione tra
"indice giorno" (usato internamente da fabbisogno/richieste/vincoli/output)
e date reali del calendario.

Il periodo elaborato viene sempre esteso, se necessario, fino alla domenica
che chiude la settimana in cui cade l'ultimo giorno del mese selezionato.
Esempio: se il mese finisce venerdi' 31, il periodo si estende fino a
domenica 2 del mese successivo, cosi' il vincolo di ore settimanali lavora
sempre su settimane calendario complete (lun-dom) invece che su una
settimana finale "spezzata".

L'indice giorno resta un intero progressivo (1, 2, 3, ...): per il mese
selezionato coincide con il giorno del mese; se il periodo si estende nel
mese successivo, l'indice continua a salire oltre il numero di giorni del
mese corrente (es. 32, 33 per il 1 e 2 agosto se luglio ha 31 giorni).
Questo mantiene invariata la logica di fabbisogno/richieste/vincoli/output,
che trattano il giorno come un semplice intero.
"""

import calendar
import datetime


def giorni_nel_mese(anno: int, mese: int) -> int:
    return calendar.monthrange(anno, mese)[1]


def mese_successivo(anno: int, mese: int) -> tuple[int, int]:
    if mese < 12:
        return anno, mese + 1
    return anno + 1, 1


def mese_precedente(anno: int, mese: int) -> tuple[int, int]:
    if mese > 1:
        return anno, mese - 1
    return anno - 1, 12


def calcola_giorno_fine_periodo(anno: int, mese: int) -> int:
    """Ritorna l'indice giorno fino a cui estendere il periodo, cosi' che
    l'ultima settimana elaborata sia sempre completa (termina di domenica).
    Se l'ultimo giorno del mese e' gia' domenica, il periodo coincide con
    la fine del mese (nessuna estensione)."""
    ultimo = giorni_nel_mese(anno, mese)
    data_ultimo = datetime.date(anno, mese, ultimo)
    giorno_settimana = data_ultimo.isoweekday()  # lunedi'=1 ... domenica=7
    giorni_extra = 7 - giorno_settimana if giorno_settimana != 7 else 0
    return ultimo + giorni_extra


def data_da_indice_periodo(anno: int, mese: int, giorno_indice: int) -> datetime.date:
    """Converte un indice giorno del periodo (puo' superare il numero di
    giorni del mese corrente se il periodo e' stato esteso) nella data
    reale corrispondente."""
    giorni_mese = giorni_nel_mese(anno, mese)
    if giorno_indice <= giorni_mese:
        return datetime.date(anno, mese, giorno_indice)
    anno_succ, mese_succ = mese_successivo(anno, mese)
    return datetime.date(anno_succ, mese_succ, giorno_indice - giorni_mese)


def data_da_indice_mese_precedente(anno: int, mese: int, giorno: int) -> datetime.date:
    """Converte un giorno riferito al mese precedente (usato in
    stato_iniziale) in una data reale, gestendo il cambio di anno."""
    anno_prec, mese_prec = mese_precedente(anno, mese)
    return datetime.date(anno_prec, mese_prec, giorno)
