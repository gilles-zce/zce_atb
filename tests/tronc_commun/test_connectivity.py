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


def _assert_reprise_apres_redemarrage(dut, station, harness, *, brutal: bool) -> int:
    """Redémarre le serveur de commandes et vérifie que la prise se
    reconnecte seule et reste pilotable. Rend le repère console pris avant
    le redémarrage, pour que l'appelant qualifie le chemin emprunté."""
    harness.wait_for_ws_client(timeout=station.timeouts.broker_connect_s)
    mark = dut.mark()

    harness.restart_command_server(brutal=brutal)

    # La prise était déjà connectée avant le repère : un nouveau
    # « Websocket connected » après ``mark`` prouve qu'elle a perdu puis
    # repris le lien.
    dut.wait_for_log(r"Websocket connected", since=mark,
                     timeout=station.timeouts.ws_reconnect_s)
    harness.wait_for_ws_client(timeout=station.timeouts.ws_reconnect_s)
    # À nouveau pilotable : une commande est délivrée à au moins une prise.
    assert harness.send_command({"cmd": "relay", "value": 0},
                                timeout=station.timeouts.command_ack_s) >= 1
    return mark


def test_reprise_apres_redemarrage_du_serveur(dut, station, harness):
    """CNX-10, chemin « fermeture propre » : le serveur envoie ses trames
    CLOSE puis ferme ; la prise se reconnecte seule et reste pilotable.

    L'événement intermédiaire côté firmware dépend du timing
    (`websocket_manager.c`) : trame CLOSE traitée → WEBSOCKET_EVENT_CLOSED
    (« closed by the server »), ou chute TCP avant traitement →
    WEBSOCKET_EVENT_DISCONNECTED. Les deux déclenchent la reprise ; l'oracle
    est la reconnexion effective, pas l'événement transitoire.
    """
    _assert_reprise_apres_redemarrage(dut, station, harness, brutal=False)


def test_reprise_apres_arret_brutal_du_serveur(dut, station, harness):
    """CNX-10, chemin « arrêt brutal » : sockets abandonnés sans handshake
    (processus serveur tué). Aucune trame CLOSE n'a été envoyée, la prise ne
    peut donc passer que par WEBSOCKET_EVENT_DISCONNECTED (ou ERROR), jamais
    par CLOSED — et doit reprendre quand même."""
    mark = _assert_reprise_apres_redemarrage(dut, station, harness, brutal=True)

    lines = dut.log_since(mark)
    assert not any("Websocket closed by the server" in l for l in lines), \
        "chemin CLOSED observé alors qu'aucune trame CLOSE n'a été envoyée"
    assert any("Websocket disconnected" in l or "Websocket error" in l for l in lines), \
        "ni DISCONNECTED ni ERROR loggé avant la reconnexion"
