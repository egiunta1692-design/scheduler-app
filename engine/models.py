"""
Modelli dati del motore di turnazione.

Queste dataclass rispecchiano lo schema JSON di input/output concordato.
Usare dataclass invece di dict puri ci da' autocompletamento e controllo
errori in VS Code, e rende piu' facile evolvere lo schema in futuro.
"""

from dataclasses import dataclass, field
from typing import Optional, Literal

Fascia = Literal["M", "P", "N"]
TipoRichiesta = Literal["ferie", "turno", "riposo"]


@dataclass
class VincoliPersonali:
    mai_notti: bool = False
    max_notti_consecutive_override: Optional[int] = None


@dataclass
class Lavoratore:
    id: str
    nome: str
    # Le ore contrattuali settimanali sono un INTERVALLO, non un singolo
    # valore fisso: sotto il minimo non si puo' andare, sopra il massimo
    # nemmeno. Se minimo == massimo, le ore sono obbligatoriamente
    # uguali a quel valore unico (comportamento "a valore fisso").
    ore_settimanali_min: int
    ore_settimanali_max: int
    tempo_parziale: bool = False
    vincoli_personali: VincoliPersonali = field(default_factory=VincoliPersonali)


@dataclass
class Fabbisogno:
    giorno: int
    fascia: Fascia
    minimo: int
    ottimale: Optional[int] = None


@dataclass
class VincoloAdmin:
    id: str
    lavoratore_id: str
    giorno: int
    tipo: TipoRichiesta
    fascia: Optional[Fascia] = None  # richiesto solo se tipo == "turno"
    declassabile_se_infeasible: bool = False
    peso_se_declassato: Optional[int] = None


@dataclass
class RichiestaSoft:
    id: str
    lavoratore_id: str
    giorno: int
    tipo: TipoRichiesta
    priorita: int  # 1 (indifferente) - 4 (molto importante)
    fascia: Optional[Fascia] = None  # richiesto solo se tipo == "turno"


@dataclass
class RegoleContrattuali:
    max_ore_settimanali: int = 36
    max_notti_consecutive: int = 2
    min_riposo_ore_dopo_turno: int = 11
    vietato_dopo_notte: list[Fascia] = field(default_factory=lambda: ["M", "P"])
    # Numero di giorni di riposo obbligatorio dopo un turno notturno (o
    # dopo l'ultima notte di una serie consecutiva): default 2, non 1.
    # Si applica in cascata anche dopo serie di notti consecutive (es. 2
    # notti di fila -> 2 giorni di riposo dopo l'ultima, non dopo ognuna
    # singolarmente) grazie a come il vincolo viene costruito nel motore.
    giorni_riposo_dopo_notte: int = 2
    max_giorni_consecutivi_lavorati: int = 5
    # Giorni di riposo pieno obbligatorio dopo aver raggiunto il numero
    # massimo di giorni lavorativi consecutivi (qualsiasi fascia) definito
    # sopra: default 2, stesso principio di giorni_riposo_dopo_notte ma
    # applicato alla serie generale di giorni lavorati, non solo alle notti.
    giorni_riposo_dopo_serie_lavorativa: int = 2
    # Durata di ciascuna fascia in MINUTI (non ore): permette di
    # configurare turni con minuti (es. 7h30m = 450), non solo ore intere.
    # Nome esplicito "minuti_" invece di "ore_" per rendere l'unita' di
    # misura inequivocabile — un errore di unita' qui si propagherebbe
    # silenziosamente in tutto il calcolo delle ore settimanali.
    minuti_per_fascia: dict[Fascia, int] = field(
        default_factory=lambda: {"M": 480, "P": 480, "N": 600}  # 8h, 8h, 10h
    )
    # Minuti "virtuali" che una giornata di ferie aggiunge al monte ore
    # settimanale del lavoratore (per contratto), distinte dal riposo che
    # non aggiunge nulla. Valore unico per tutto il reparto.
    minuti_ferie_giornaliere: int = 480  # 8h


@dataclass
class ParametriFairness:
    bilancia_fasce: bool = True
    bilancia_giorni_settimana: bool = True
    bilancia_copertura_giornaliera: bool = True
    bilancia_ore_settimanali: bool = True
    minimizza_pm_consecutivo: bool = True
    bilancia_proporzione_giornaliera: bool = True
    # Pesi individuali per ciascun vincolo soft (default = preset
    # "Equilibrio reparto", vedi PRESET_FAIRNESS in app.py). Un peso
    # condiviso unico penalizzava tutti i vincoli nella stessa proporzione,
    # impedendo di dare piu' importanza a uno specifico senza alterare
    # anche gli altri.
    peso_bilancia_fasce: int = 7
    peso_bilancia_giorni_settimana: int = 4
    peso_bilancia_ore_settimanali: int = 4
    peso_bilancia_copertura_giornaliera: int = 7
    peso_minimizza_pm_consecutivo: int = 2
    peso_bilancia_proporzione_giornaliera: int = 6


@dataclass
class StatoIniziale:
    lavoratore_id: str
    giorno: int
    fascia: Fascia
    mese_precedente: bool = True


@dataclass
class Periodo:
    anno: int
    mese: int
    giorno_inizio: int
    giorno_fine: int


@dataclass
class InputTurnazione:
    reparto_id: str
    categoria: str  # "infermieri" | "oss"
    periodo: Periodo
    lavoratori: list[Lavoratore]
    fabbisogno: list[Fabbisogno]
    fasce_orarie: list[Fascia] = field(default_factory=lambda: ["M", "P", "N"])
    vincoli_admin: list[VincoloAdmin] = field(default_factory=list)
    richieste_soft: list[RichiestaSoft] = field(default_factory=list)
    regole_contrattuali: RegoleContrattuali = field(default_factory=RegoleContrattuali)
    parametri_fairness: ParametriFairness = field(default_factory=ParametriFairness)
    stato_iniziale: list[StatoIniziale] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

@dataclass
class Assegnazione:
    lavoratore_id: str
    giorno: int
    fascia: Fascia  # oppure "FERIE" / "RIPOSO"


@dataclass
class RichiestaNonSoddisfatta:
    richiesta_id: str
    motivo: str


@dataclass
class VincoloDeclassato:
    vincolo_id: str
    motivo: str


@dataclass
class OutputTurnazione:
    stato: str  # "feasible" | "infeasible" | "feasible_con_declassamenti" | "tempo_scaduto"
    assegnazioni: list[Assegnazione] = field(default_factory=list)
    richieste_non_soddisfatte: list[RichiestaNonSoddisfatta] = field(default_factory=list)
    vincoli_declassati: list[VincoloDeclassato] = field(default_factory=list)
    metriche_fairness: dict = field(default_factory=dict)
    ottimalita_provata: bool = False  # True se il motore ha dimostrato che non esiste soluzione migliore
    tempo_impiegato_secondi: float = 0.0
