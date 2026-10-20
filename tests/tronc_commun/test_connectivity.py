"""§3 du plan de smoke test : identité et connectivité.

Référence : ZCE_PLUG_MQTT/docs/functional_smoke_test_plan.md, étape 3.
Couvre l'identité device au boot, la connexion broker + availability, la
connexion au serveur de commandes et la reprise après son redémarrage
(régression corrigée le 2026-10-15, `enable_close_reconnect`).
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.tronc_commun


def test_identite_device_au_boot(dut, station, fresh_boot):
    """Prise provisionnée : log `Device identity: serial=…` avant les
    transports (politique D5)."""
    line = dut.wait_for_log(r"Device identity: serial=", since=fresh_boot,
                            timeout=station.timeouts.boot_s)
    assert station.dut.serial in line, f"sérial inattendu : {line!r}"


def test_connexion_broker(dut, station, harness, fresh_boot):
    """Wi-Fi + MQTT : MQTT_EVENT_CONNECTED côté console, puis télémétrie reçue
    par l'observateur — preuve que le lien prise↔broker est établi (la prise
    publie `telemetry` chaque seconde, oracle robuste)."""
    dut.wait_for_log(r"MQTT_EVENT_CONNECTED", since=fresh_boot,
                     timeout=station.timeouts.broker_connect_s)
    frame = harness.wait_for("telemetry", timeout=station.timeouts.broker_connect_s)
    assert isinstance(frame, dict), f"trame télémétrie inattendue : {frame!r}"


def test_availability_online(dut, station, harness, fresh_boot):
    """`availability` state=online publié par la prise (backend_publish_
    availability : {"state":"online","ts":<epoch>})."""
    msg = harness.wait_for("availability", predicate=lambda d: d.get("state") == "online",
                           timeout=station.timeouts.broker_connect_s)
    assert msg["state"] == "online"


def test_connexion_serveur_de_commandes(dut, station, harness, fresh_boot):
    """La prise se connecte au serveur websocket de commandes."""
    harness.wait_for_ws_client(timeout=station.timeouts.broker_connect_s)
    assert harness.ws_client_count >= 1


def test_reprise_apres_redemarrage_du_serveur(dut, station, harness):
    """CNX-10 : après redémarrage du serveur de commandes, la prise se
    reconnecte seule et reste pilotable.

    Le chemin intermédiaire côté firmware dépend du timing de fermeture
    (`websocket_manager.c`) : trame CLOSE traitée → WEBSOCKET_EVENT_CLOSED
    (« closed by the server »), ou chute TCP avant traitement →
    WEBSOCKET_EVENT_DISCONNECTED. Les deux déclenchent la reprise ; on ancre
    donc l'oracle sur la reconnexion effective, pas sur l'événement transitoire.
    """
    harness.wait_for_ws_client(timeout=station.timeouts.broker_connect_s)
    mark = dut.mark()

    harness.restart_command_server()

    # La prise était déjà connectée avant le repère : un nouveau
    # « Websocket connected » après ``mark`` prouve qu'elle a perdu puis
    # repris le lien.
    dut.wait_for_log(r"Websocket connected", since=mark,
                     timeout=station.timeouts.ws_reconnect_s)
    harness.wait_for_ws_client(timeout=station.timeouts.ws_reconnect_s)
    # À nouveau pilotable : une commande est délivrée à au moins une prise.
    assert harness.send_command({"cmd": "relay", "value": 0},
                                timeout=station.timeouts.command_ack_s) >= 1
