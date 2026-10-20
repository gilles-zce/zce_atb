"""Contrôles d'avant-session : un banc ambigu refuse de démarrer.

Leçon du 2026-10-19 : un mosquitto WSL relayé par wslrelay sur
127.0.0.1:1883 captait en silence l'abonnement de l'observateur (« commandes
websocket OK, données MQTT absentes, statut offline »). Plutôt que produire
des faux négatifs, la session s'arrête ici avec un diagnostic.
"""

from __future__ import annotations

import socket


class PreflightError(RuntimeError):
    """Le banc n'est pas dans un état où les résultats seraient fiables."""


def port_in_use(host: str, port: int, timeout_s: float = 0.5) -> bool:
    """Vrai si quelque chose accepte déjà des connexions sur host:port."""
    try:
        with socket.create_connection((host, port), timeout=timeout_s):
            return True
    except OSError:
        return False


def assert_broker_ports_free(ports: tuple[int, ...], hosts: tuple[str, ...] = ("127.0.0.1",)) -> None:
    """À appeler AVANT de démarrer le broker du harnais : si un autre service
    tient déjà un port broker, la session doit échouer explicitement."""
    squatters = [(h, p) for p in ports for h in hosts if port_in_use(h, p)]
    if squatters:
        detail = ", ".join(f"{h}:{p}" for h, p in squatters)
        raise PreflightError(
            f"Port(s) broker déjà occupé(s) : {detail}. Causes habituelles : une application "
            f"em-mqtt (GUI) déjà lancée sur ce poste — l'arrêter, le harnais pytest fournit "
            f"son propre broker/observateur/serveur de commandes — ou le mosquitto d'une "
            f"distribution WSL relayé par wslrelay. Identifier le processus "
            f"(Windows : Get-NetTCPConnection -LocalPort {squatters[0][1]} | "
            f"% {{ Get-Process -Id $_.OwningProcess }} ; "
            f"Linux : ss -ltnp 'sport = :{squatters[0][1]}') puis relancer la session."
        )


def instrument_idn(resource: str) -> str:
    """Identification SCPI (*IDN?) d'un instrument — phase 2. Lève
    PreflightError si l'instrument ne répond pas."""
    import pyvisa

    try:
        rm = pyvisa.ResourceManager("@py")
        inst = rm.open_resource(resource)
        return inst.query("*IDN?").strip()
    except Exception as exc:  # pyvisa lève des types variés selon le transport
        raise PreflightError(f"Instrument injoignable ({resource}) : {exc}") from exc
