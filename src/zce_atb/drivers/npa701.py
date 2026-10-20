"""Analyseur de puissance Rohde & Schwarz NPA701 — pilote SCPI minimal (phase 2).

Rôle sur le banc (WATTELGO-ATB-SPEC-1.1 §2.2) : référence métrologique
indépendante. Les tests comparent la télémétrie du DUT aux lectures NPA701
dans les tolérances de la configuration de poste.

⚠ Commandes à valider contre le manuel « Remote Control SCPI » de la série
NPA (dépôt de la spec ATB, doc/). Squelette non testé.
"""

from __future__ import annotations


class Npa701:
    def __init__(self, resource: str) -> None:
        self._resource = resource
        self._inst = None

    def open(self) -> str:
        import pyvisa

        rm = pyvisa.ResourceManager("@py")
        self._inst = rm.open_resource(self._resource)
        return self.idn()

    def close(self) -> None:
        if self._inst is not None:
            self._inst.close()

    def idn(self) -> str:
        return self._inst.query("*IDN?").strip()

    # TODO phase 2 : syntaxe exacte de la série NPA (manuel SCPI) pour
    # sélectionner les fonctions de mesure et lire URMS/IRMS/P/énergie/THD.
    def voltage_rms(self) -> float:
        raise NotImplementedError("phase 2 — cf. manuel NPA Remote Control SCPI")

    def current_rms(self) -> float:
        raise NotImplementedError("phase 2 — cf. manuel NPA Remote Control SCPI")

    def power_w(self) -> float:
        raise NotImplementedError("phase 2 — cf. manuel NPA Remote Control SCPI")
