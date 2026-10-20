"""Harnais backend MQTT : les services d'em_mqtt pilotés hors GUI.

Broker amqtt, observateur de topics (décodage des trames, CRC8) et serveur
websocket de commandes tournent dans une boucle asyncio dédiée (thread
démon) ; les tests, synchrones, dialoguent avec elle par files thread-safe.

Oracles offerts :
- ``wait_for(kind, predicate)`` sur les messages décodés par l'observateur
  (kinds em_mqtt : telemetry, status, errors, availability) ;
- ``send_command(payload)`` vers la ou les prises connectées au serveur de
  commandes (``CommandServer.broadcast``) ;
- ``restart_command_server()`` pour le scénario de reprise CNX-10.
"""

from __future__ import annotations

import asyncio
import functools
import queue
import threading
import time
from typing import Callable

import websockets
from em_mqtt.config import Settings, load_settings
from em_mqtt.protocol.frames import FrameDecoder
from em_mqtt.services.broker import BrokerService
from em_mqtt.services.commands import CommandResponder, CommandServer
from em_mqtt.services.observer import ObserverService
from em_mqtt.services.runtime import ServiceRunner

from .preflight import assert_broker_ports_free


class HarnessTimeout(AssertionError):
    """L'observateur n'a pas vu le message attendu dans le délai imparti."""


class _TaggingDecoder(FrameDecoder):
    """FrameDecoder qui étiquette chaque dict décodé avec le `kind` du topic
    (telemetry / status / availability / errors). Les dicts d'em_mqtt ne
    portent pas ce champ (sauf errors), or le harnais trie les messages par
    kind : sans cette balise, tout tomberait dans une file indistincte."""

    def decode(self, topic: str, payload: bytes) -> dict | None:
        data = super().decode(topic, payload)
        if data is not None:
            data = {**data, "kind": topic.rsplit("/", 1)[-1]}
        return data


class BenchHarness:
    """Backend MQTT de test, démarré/arrêté par la fixture de session."""

    def __init__(self, serial: str, lan_ip: str | None = None,
                 services: tuple[str, ...] = ("broker", "observer", "commands")) -> None:
        self.settings: Settings = load_settings(serial=serial, lan_ip=lan_ip)
        self._service_names = services
        self._queues: dict[str, queue.Queue] = {}
        self._queues_lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._runner: ServiceRunner | None = None
        self._commands: CommandServer | None = None

    # -- cycle de vie ------------------------------------------------------

    def start(self, timeout_s: float = 15.0) -> None:
        if "broker" in self._service_names:
            assert_broker_ports_free((self.settings.mqtt_port,))

        services = []
        if "broker" in self._service_names:          # le broker avant ses clients
            services.append(BrokerService(self.settings))
        if "observer" in self._service_names:
            services.append(ObserverService(self.settings,
                                            _TaggingDecoder(self.settings.serial), self._sink))
        if "commands" in self._service_names:
            # ping_interval=None : le firmware ne répond pas aux pings du
            # serveur, qui sinon largue la prise toutes les ~20 s (close 1011
            # keepalive timeout) et parasite l'observation. La seule fermeture
            # du lien reste alors notre stop() explicite (chemin propre CNX-10).
            # close_timeout=2 : le firmware ne renvoie pas non plus de trame
            # CLOSE ; sans cette borne, stop() attend 10 s par prise connectée.
            serve = functools.partial(websockets.serve, ping_interval=None, close_timeout=2)
            self._commands = CommandServer(self.settings, CommandResponder(), serve=serve)
            services.append(self._commands)
        self._runner = ServiceRunner(services)

        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever,
                                        name="zce-atb-harness", daemon=True)
        self._thread.start()
        self._run(self._runner.start(), timeout_s)

    def stop(self, timeout_s: float = 20.0) -> None:
        # Un arrêt de service qui traîne (client ws encore accroché) ne doit
        # pas faire échouer le teardown : on force quand même l'arrêt de la boucle.
        if self._runner is not None and self._loop is not None:
            try:
                self._run(self._runner.stop(), timeout_s)
            except Exception:
                pass
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread is not None:
            self._thread.join(timeout=timeout_s)

    def _run(self, coro, timeout_s: float):
        """Exécute une coroutine dans la boucle du harnais, depuis un test."""
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result(timeout_s)

    # -- messages observés -------------------------------------------------

    def _sink(self, data: dict) -> None:
        """FrameSink d'em_mqtt — appelé depuis la boucle asyncio."""
        self._queue_for(data.get("kind", "?")).put(data)

    def _queue_for(self, kind: str) -> queue.Queue:
        with self._queues_lock:
            return self._queues.setdefault(kind, queue.Queue())

    def clear(self, kind: str | None = None) -> None:
        """Purge les messages déjà reçus (tous les kinds par défaut) —
        à faire avant l'action observée, comme ``Dut.mark()``."""
        with self._queues_lock:
            targets = [self._queues[kind]] if kind else list(self._queues.values())
        for q in targets:
            while not q.empty():
                try:
                    q.get_nowait()
                except queue.Empty:
                    break

    def wait_for(self, kind: str, predicate: Callable[[dict], bool] | None = None,
                 timeout: float = 15.0) -> dict:
        """Prochain message décodé du kind donné qui satisfait le prédicat."""
        q = self._queue_for(kind)
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise HarnessTimeout(f"aucun message {kind!r}"
                                     f"{' conforme au prédicat' if predicate else ''} "
                                     f"en {timeout:g} s")
            try:
                data = q.get(timeout=min(remaining, 0.5))
            except queue.Empty:
                continue
            if predicate is None or predicate(data):
                return data

    # -- serveur de commandes ----------------------------------------------

    @property
    def ws_client_count(self) -> int:
        return 0 if self._commands is None else self._commands.client_count

    def wait_for_ws_client(self, timeout: float = 30.0) -> None:
        deadline = time.monotonic() + timeout
        while self.ws_client_count < 1:
            if time.monotonic() > deadline:
                raise HarnessTimeout(f"aucune prise connectée au serveur de commandes "
                                     f"en {timeout:g} s")
            time.sleep(0.2)

    def send_command(self, payload: dict, timeout: float = 5.0) -> int:
        """Diffuse une commande de la spec ZCE_Wattel_EM_Protocol aux prises
        connectées ; rend le nombre de destinataires (>= 1 exigé)."""
        count = self._run(self._commands.broadcast(payload), timeout)
        if count == 0:
            raise HarnessTimeout("commande non délivrée : aucune prise connectée")
        return count

    def restart_command_server(self, timeout_s: float = 30.0) -> None:
        """Arrêt propre (trames CLOSE) puis redémarrage du serveur de
        commandes — scénario CNX-10, chemin « fermeture propre ».
        TODO : variante brutale (socket tué sans CLOSE) pour le second chemin."""
        self._run(self._commands.stop(), timeout_s)
        self._run(self._commands.start(), timeout_s)
