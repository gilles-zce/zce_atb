"""Fixtures du banc : configuration de poste, DUT, harnais backend.

Portée session : le harnais et la liaison série s'ouvrent une fois par
passe. Chaque test qui a besoin d'un état de boot connu redémarre la prise
lui-même (fixture ``fresh_boot``) plutôt que de dépendre de l'ordre des
tests.
"""

from __future__ import annotations

import pytest

from zce_atb.config import StationConfig, load_station
from zce_atb.dut import Dut
from zce_atb.harness import BenchHarness


@pytest.fixture(scope="session")
def station() -> StationConfig:
    return load_station()


@pytest.fixture(scope="session")
def harness(station: StationConfig):
    h = BenchHarness(serial=station.dut.serial, lan_ip=station.lan_ip,
                     services=station.harness.services)
    h.start()
    yield h
    h.stop()


@pytest.fixture(scope="session")
def dut(station: StationConfig):
    d = Dut(station.dut, log_dir=station.log_dir)
    d.open()
    yield d
    d.close()


@pytest.fixture()
def fresh_boot(dut: Dut, harness: BenchHarness, station: StationConfig) -> int:
    """Redémarre la prise (reset logiciel) et attend la fin du boot.
    Rend le repère UART pris juste avant le reset : tout ce qui suit dans le
    log appartient à ce boot."""
    harness.clear()
    mark = dut.mark()
    dut.reset()
    dut.wait_for_log(r"event_journal: mounted on 'journal'",
                     since=mark, timeout=station.timeouts.boot_s)
    return mark
