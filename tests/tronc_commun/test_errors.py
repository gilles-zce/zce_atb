"""§5 du plan de smoke test : chaîne d'erreurs (échantillon).

Un défaut de chaque classe, pas la campagne error manager complète. Exerce
d'un coup error manager, journal, pont MQTT (topic retenu `errors`, contrat
docs/error_manager/mqtt_error_reporting.md) et plug_manager.

Injection par la CLI admin `err_raise <code> <sévérité>` (codes = index de
l'enum error_types.h : 5 = ERROR_FLASH_READ, 11 = ERROR_METRO_OVER_CURRENT).
Sans charge commutable en phase 1, la « coupure de la charge » s'observe par
l'état du relais (journal RELAY_OFF, flags télémétrie), pas par le courant.
"""

from __future__ import annotations

import pytest

from zce_atb.harness import HarnessTimeout
from zce_atb.oracles import (
    error_codes,
    has_error,
    last_event,
    no_error,
    relay_closed,
    relay_open,
)

pytestmark = pytest.mark.tronc_commun

CODE_FLASH_READ = "5"            # ERROR_FLASH_READ, diagnostic MEDIUM non latché
CODE_METRO_OVER_CURRENT = "11"   # ERROR_METRO_OVER_CURRENT, HIGH : latch + coupure
SEV_MEDIUM, SEV_HIGH = "2", "3"


def _relay(harness, dut, station, on: bool) -> int:
    """Commande relais par websocket ; rend le repère console pris avant."""
    mark = dut.mark()
    harness.clear("telemetry")
    harness.send_command({"cmd": "relay", "value": 1 if on else 0},
                         timeout=station.timeouts.command_ack_s)
    dut.wait_for_log(r"Relay command: " + ("ON" if on else "OFF"), since=mark, timeout=5)
    return mark


def _wait_errors_republished_empty(harness, station) -> None:
    """Précondition d'un boot sain : le payload retenu `errors`, republié à
    la connexion broker, ne porte plus aucune erreur active."""
    harness.wait_for("errors", predicate=no_error, timeout=station.timeouts.broker_connect_s)


def test_erreur_medium_non_latchee(dut, station, harness, fresh_boot):
    """§5.1 : err_raise 5 2 (ERROR_FLASH_READ) → publication sur `errors`,
    `ERROR` au journal (payload 05 00 02), la prise reste opérationnelle."""
    _wait_errors_republished_empty(harness, station)
    before = dut.journal_list(5)

    resp = dut.cli_admin("err_raise", CODE_FLASH_READ, SEV_MEDIUM)
    assert resp.ok, f"err_raise : CR 0x{resp.status:X}"

    msg = harness.wait_for("errors", predicate=has_error("ERROR_FLASH_READ", "MEDIUM"),
                           timeout=10)
    assert not msg["malformed"], f"payload errors non conforme : {msg['raw']!r}"

    event = last_event(dut.journal_list(10), "ERROR")
    assert event is not None, "aucun événement ERROR au journal"
    anchor = last_event(before, "ERROR")
    assert anchor is None or event.index > anchor.index, "ERROR au journal antérieur à l'injection"
    assert event.payload[:3] == bytes.fromhex("05 00 02"), f"payload ERROR : {event.payload.hex(' ')}"

    # Opérationnelle : une commande relais passe (pas de latch sur MEDIUM).
    _relay(harness, dut, station, on=True)
    harness.wait_for("telemetry", predicate=relay_closed, timeout=15)
    _relay(harness, dut, station, on=False)
    harness.wait_for("telemetry", predicate=relay_open, timeout=15)


def test_defaut_high_latche_coupe_la_charge(dut, station, harness, fresh_boot):
    """§5.2 : relais fermé, err_raise 11 3 (ERROR_METRO_OVER_CURRENT HIGH) →
    coupure (console « Latched fault: cutting the load », RELAY_OFF au
    journal, relais ouvert en télémétrie), défaut latché : `relay on` refusé."""
    _wait_errors_republished_empty(harness, station)
    _relay(harness, dut, station, on=True)
    harness.wait_for("telemetry", predicate=relay_closed, timeout=15)
    assert last_event(dut.journal_list(5), "RELAY_ON") is not None

    mark = dut.mark()
    harness.clear("telemetry")
    resp = dut.cli_admin("err_raise", CODE_METRO_OVER_CURRENT, SEV_HIGH)
    assert resp.ok, f"err_raise : CR 0x{resp.status:X}"

    dut.wait_for_log(r"Latched fault: cutting the load", since=mark, timeout=10)
    msg = harness.wait_for("errors", predicate=has_error("ERROR_METRO_OVER_CURRENT", "HIGH"),
                           timeout=10)
    assert "ERROR_METRO_OVER_CURRENT" in error_codes(msg)
    try:
        harness.wait_for("telemetry", predicate=relay_open, timeout=15)
    except HarnessTimeout:
        # Vu le 2026-10-20 sur Wattel EM (1 passe sur 3) : la commande
        # d'ouverture (trame Tuya 0x06, sans acquittement ni réémission dans
        # tuya_link) n'a pas été appliquée par le MCU, DP16 est resté à 1 alors
        # que RELAY_OFF était déjà journalisé. L'oracle est juste : on nomme
        # le cas plutôt que d'élargir la fenêtre.
        last = harness.wait_for("telemetry", timeout=5)
        pytest.fail("relais toujours fermé en télémétrie 15 s après la coupure sur défaut "
                    f"(flags={last['flags']}) : commande relais non appliquée par la "
                    "métrologie (Tuya : DP16 inchangé, cf. console « DP 16 … val0=1 »)")

    events = dut.journal_list(10)
    err = last_event(events, "ERROR")
    off = last_event(events, "RELAY_OFF")
    assert err is not None and err.payload[:3] == bytes.fromhex("0B 00 03"), \
        f"ERROR au journal : {err}"
    assert off is not None and off.index > err.index, "RELAY_OFF attendu après l'ERROR"

    # Latché : la fermeture est refusée tant que la prise n'a pas redémarré.
    mark = dut.mark()
    harness.send_command({"cmd": "relay", "value": 1}, timeout=station.timeouts.command_ack_s)
    dut.wait_for_log(r"Relay ON refused: fault latched until reset", since=mark, timeout=5)


def test_deblocage_par_reset_websocket(dut, station, harness, fresh_boot):
    """§5.3 : défaut latché, puis {"cmd":"reset"} → reboot (BOOT cause 03),
    retour nominal, topic `errors` purgé, relais à nouveau commandable."""
    _wait_errors_republished_empty(harness, station)
    resp = dut.cli_admin("err_raise", CODE_METRO_OVER_CURRENT, SEV_HIGH)
    assert resp.ok
    harness.wait_for("errors", predicate=has_error("ERROR_METRO_OVER_CURRENT"), timeout=10)

    harness.clear()
    mark = dut.mark()
    harness.send_command({"cmd": "reset"}, timeout=station.timeouts.command_ack_s)
    dut.forget_cli()                       # la session BLE tombe avec le reboot
    dut.wait_for_log(r"event_journal: mounted on 'journal'", since=mark,
                     timeout=station.timeouts.boot_s)

    boot = last_event(dut.journal_list(5), "BOOT")
    assert boot is not None and boot.payload[:1] == b"\x03", f"BOOT : {boot}"

    # Retour nominal : errors republié vide à la reconnexion, relais pilotable.
    msg = harness.wait_for("errors", predicate=no_error, timeout=station.timeouts.broker_connect_s)
    assert error_codes(msg) == []
    harness.wait_for_ws_client(timeout=station.timeouts.broker_connect_s)
    _relay(harness, dut, station, on=True)
    harness.wait_for("telemetry", predicate=relay_closed, timeout=15)
    _relay(harness, dut, station, on=False)
    harness.wait_for("telemetry", predicate=relay_open, timeout=15)


@pytest.mark.skip(reason="§5.4 couple ERROR→ERROR_CLEARED Wi-Fi : coupure réelle de l'AP "
                         "> 1 min, nécessite l'AP pilotable (phase 2, SPEC-1.1 §2.3)")
def test_couple_error_cleared_wifi():
    """§5.4 : box coupée > 1 min puis rétablie → une seule paire
    WIFI_DISCONNECTED/WIFI_CONNECTED au journal, ERROR puis ERROR_CLEARED,
    topic `errors` mis à jour."""
