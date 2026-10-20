"""Source AC GW Instek ASR-2100 — pilote SCPI minimal (phase 2).

Rôles sur le banc (WATTELGO-ATB-SPEC-1.1 §2.2) : alimentation du DUT,
coupure secteur franche (persistance §6), variations de tension (seuils
métrologie réels), micro-coupures.

⚠ Commandes à valider sur l'instrument contre le manuel de programmation
ASR-2000 (dépôt de la spec ATB, doc/instruments/). Squelette non testé.
"""

from __future__ import annotations

import time


class Asr2100:
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

    def configure(self, voltage_v: float, freq_hz: float = 50.0,
                  current_limit_a: float | None = None) -> None:
        self._inst.write(f"SOURce:VOLTage {voltage_v}")
        self._inst.write(f"SOURce:FREQuency {freq_hz}")
        if current_limit_a is not None:
            # TODO valider la syntaxe exacte (limite RMS) sur l'instrument.
            self._inst.write(f"SOURce:CURRent:LIMit:RMS {current_limit_a}")

    def output(self, on: bool) -> None:
        self._inst.write(f"OUTPut {'ON' if on else 'OFF'}")

    def mains_cut(self, off_s: float = 3.0) -> None:
        """Coupure secteur franche du DUT (§6 du plan de smoke test)."""
        self.output(False)
        time.sleep(off_s)
        self.output(True)
