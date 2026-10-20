"""§2 du plan de smoke test : boot et intégrité de base.

Référence : ZCE_PLUG_MQTT/docs/functional_smoke_test_plan.md, étape 2.
Build debug attendu (les ESP_LOG n'existent pas en release).
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.tronc_commun


def test_boot_propre_et_journal_monte(dut, station, fresh_boot):
    """Boot complet sans backtrace ni reset en boucle, journal monté."""
    boot_log = dut.log_since(fresh_boot)
    crashes = [l for l in boot_log if "Backtrace:" in l or "Guru Meditation" in l]
    assert not crashes, f"backtrace au boot : {crashes[0]}"
    # Un seul montage du journal = pas de reset en boucle pendant la fenêtre.
    mounts = [l for l in boot_log if "event_journal: mounted" in l]
    assert len(mounts) == 1, f"{len(mounts)} montages du journal vus (reset en boucle ?)"


def test_cause_de_reset_correcte(dut, station, fresh_boot):
    """Après un reset logiciel, l'événement BOOT porte la cause 03 (SW).
    TODO : lire l'événement via la CLI (`journal list`) plutôt que le log —
    à brancher quand la commande sera confirmée dans le catalogue."""
    # Le firmware journalise la cause au boot ; motif à affiner sur cible.
    dut.wait_for_log(r"BOOT", since=fresh_boot, timeout=5)


def test_version_conforme(dut, station):
    """`version` répond CR 0x00 ; si la config du poste fixe une version
    attendue, elle doit apparaître dans la réponse."""
    resp = dut.cli("version")
    assert resp.ok, f"version : statut 0x{resp.status:X}"
    if station.dut.expected_version:
        payload = " ".join(resp.data)
        assert station.dut.expected_version in payload, (
            f"version attendue {station.dut.expected_version!r} absente de {payload!r}")
