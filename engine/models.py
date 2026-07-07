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
    ore_settimanali_contratto: int
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
    max_giorni_consecutivi_lavorati: int = 6
    ore_per_fascia: dict[Fascia, int] = field(
        default_factory=lambda: {"M": 8, "P": 8, "N": 8}
    )
    ore_per_fascia: dict[str, int] = field(default_factory=lambda: {"M": 8, "P": 8, "N": 8})


@dataclass
class ParametriFairness:
    bilancia_fasce: bool = True
    bilancia_giorni_settimana: bool = True
    peso_fairness: int = 2


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
    stato: str  # "feasible" | "infeasible" | "feasible_con_declassamenti"
    assegnazioni: list[Assegnazione] = field(default_factory=list)
    richieste_non_soddisfatte: list[RichiestaNonSoddisfatta] = field(default_factory=list)
    vincoli_declassati: list[VincoloDeclassato] = field(default_factory=list)
    metriche_fairness: dict = field(default_factory=dict)
