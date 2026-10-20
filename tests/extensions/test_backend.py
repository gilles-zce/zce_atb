"""§8 du plan de smoke test : extension backend (`set_backend`).

Scénario nominal du plan backend_uri — set_backend valide → persistance NVS
sans effet immédiat → prise d'effet au reboot (migration A→B) → retour par
`backend reset` — plus un cas de rejet (URI invalide, tout-ou-rien).

Le « backend B » est une seconde instance em_mqtt lancée par le harnais avec
un décalage de ports (+10) : ses URIs sont celles de l'instance A avec le
port décalé d'autant. Le firmware ne lit ses URIs qu'au boot
(backend_uri_storage.c) et logge `Broker URI: <uri> (NVS override|factory
default)` — c'est l'oracle de prise d'effet.

Garde-fou : la fixture `backend_defaults` remet la prise sur ses défauts
usine avant ET après chaque test, pour ne jamais laisser le banc pointer
sur l'instance B.
"""

from __future__ import annotations

import re

import pytest

from zce_atb.dut import BackendUris
from zce_atb.harness import BenchHarness, HarnessTimeout
from zce_atb.oracles import shift_uri_port

pytestmark = pytest.mark.backend

PORT_OFFSET_B = 10


@pytest.fixture(scope="module")
def harness_b(station):
    """Backend B : seconde instance em_mqtt, ports décalés de +10."""
    h = BenchHarness(serial=station.dut.serial, lan_ip=station.lan_ip,
                     services=station.harness.services, port_offset=PORT_OFFSET_B)
    h.start()
    yield h
    h.stop()


def _reboot_and_wait_backend(dut, station, harness_from, expected_log: str) -> None:
    """Reboot par la commande websocket `reset` envoyée depuis l'instance qui
    tient actuellement la prise, puis attente du boot et de l'URI loggée."""
    mark = dut.mark()
    harness_from.send_command({"cmd": "reset"}, timeout=station.timeouts.command_ack_s)
    dut.forget_cli()
    dut.wait_for_log(r"event_journal: mounted on 'journal'", since=mark,
                     timeout=station.timeouts.boot_s)
    dut.wait_for_log(expected_log, since=mark, timeout=station.timeouts.boot_s)


def _restore_factory_defaults(dut, station, harness) -> BackendUris:
    """Toujours `backend reset` (idempotent côté firmware) : `backend show`
    ne reflète que l'état EFFECTIF résolu au boot (backend_uri_storage.c), il
    ne peut pas révéler un override persisté mais pas encore appliqué. On ne
    redémarre que si l'état effectif est lui-même overridé (prise sur B)."""
    resp = dut.cli("backend", "reset")
    assert resp.ok, f"backend reset : CR 0x{resp.status:X}"
    state = dut.backend_show()
    if state.broker_overridden or state.websocket_overridden:
        harness.clear()
        mark = dut.mark()
        dut.reset()                        # par BLE : indépendant du backend courant
        dut.wait_for_log(r"Broker URI: .* \(factory default\)", since=mark,
                         timeout=station.timeouts.boot_s)
        harness.wait_for("telemetry", timeout=station.timeouts.broker_connect_s)
        state = dut.backend_show()
    assert not (state.broker_overridden or state.websocket_overridden), state
    return state


@pytest.fixture()
def backend_defaults(dut, station, harness) -> BackendUris:
    state = _restore_factory_defaults(dut, station, harness)
    yield state
    _restore_factory_defaults(dut, station, harness)


def test_set_backend_nominal_migration_et_retour(dut, station, harness, harness_b, backend_defaults):
    """set_backend valide → NVS override sans effet avant reboot → au reboot
    la prise bascule sur B (télémétrie et commandes sur B, plus rien sur A)
    → `backend reset` + reboot → retour sur A en factory default."""
    target_broker = shift_uri_port(backend_defaults.broker, PORT_OFFSET_B)
    target_ws = shift_uri_port(backend_defaults.websocket, PORT_OFFSET_B)

    # 1. Accepté et persisté, sans effet immédiat : l'état effectif (ce que
    #    `backend show` rapporte, résolu au boot) reste celui des défauts
    #    usine et la prise reste servie par A. La persistance ne se vérifie
    #    qu'au boot suivant (étape 2).
    mark = dut.mark()
    harness.send_command({"cmd": "set_backend", "broker_uri": target_broker,
                          "websocket_uri": target_ws}, timeout=station.timeouts.command_ack_s)
    dut.wait_for_log(r"Backend URI\(s\) persisted, reboot required", since=mark, timeout=5)
    assert dut.backend_show() == backend_defaults
    harness.clear("telemetry")
    harness.wait_for("telemetry", timeout=15)          # toujours servie par A

    # 2. Prise d'effet au reboot : migration A → B.
    harness_b.clear()
    _reboot_and_wait_backend(dut, station, harness,
                             re.escape(f"Broker URI: {target_broker} (NVS override)"))
    harness_b.wait_for("telemetry", timeout=station.timeouts.broker_connect_s)
    harness_b.wait_for_ws_client(timeout=station.timeouts.broker_connect_s)
    harness.clear("telemetry")
    with pytest.raises(HarnessTimeout):
        harness.wait_for("telemetry", timeout=10)      # A ne reçoit plus rien

    # 3. Retour : overrides levés par la CLI, reboot commandé depuis B.
    resp = dut.cli("backend", "reset")
    assert resp.ok and any("Overrides dropped" in line for line in resp.data), resp
    harness.clear()
    _reboot_and_wait_backend(dut, station, harness_b,
                             re.escape(f"Broker URI: {backend_defaults.broker} (factory default)"))
    harness.wait_for("telemetry", timeout=station.timeouts.broker_connect_s)
    harness.wait_for_ws_client(timeout=station.timeouts.broker_connect_s)
    assert dut.backend_show() == backend_defaults


def test_set_backend_rejet_tout_ou_rien(dut, station, harness, backend_defaults):
    """URI websocket invalide (schéma hors ws/wss) → rejet loggé, et le broker
    pourtant valide n'est pas persisté non plus : aucun override, pas de
    reboot nécessaire."""
    valid_broker = shift_uri_port(backend_defaults.broker, PORT_OFFSET_B)
    mark = dut.mark()
    harness.send_command({"cmd": "set_backend", "broker_uri": valid_broker,
                          "websocket_uri": "http://invalid"},
                         timeout=station.timeouts.command_ack_s)
    dut.wait_for_log(r"set_backend: rejected websocket URI: http://invalid", since=mark, timeout=5)
    assert dut.backend_show() == backend_defaults
