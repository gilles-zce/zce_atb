"""Accès au DUT : console de logs (lecture seule) et CLI usine sur BLE.

Deux liens distincts, conformes au câblage réel de la prise :

- la **console de logs** (port série USB Serial/JTAG, ex. COM19) ne porte
  que les ESP_LOG : journal ``mark()`` / ``wait_for_log()``, archivé dans
  ``log/``. Le banc n'y écrit jamais — la CLI n'est pas sur ce port.
- la **CLI usine** passe par le lien BLE de production_bench (GATT
  ``zce-cli``). La session GATT tombe à chaque reboot de la prise ; elle
  est rouverte automatiquement au prochain ``cli()``.

Synchronisation : les tests prennent un repère (``mark()``) avant l'action,
puis attendent un motif avec ``wait_for_log(pattern, since=mark)``. Jamais
de sleep fixe.
"""

from __future__ import annotations

import datetime as _dt
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from production_bench.application.cli_executor import CliExecutor
from production_bench.domain.cli_protocol import CliResponse, format_command
from production_bench.infrastructure.ble_link import BleLink, scan_ble_devices
from production_bench.infrastructure.uart_link import UartLink

from .config import DutConfig


@dataclass(frozen=True)
class JournalEvent:
    """Une ligne de `journal list` (cli_cmd_admin.c, journal_dump_record)."""
    index: int
    when: str            # "YYYY-MM-DD HH:MM:SS" (UTC) ou "not-synced"
    event: str           # BOOT, ERROR, RELAY_ON, RELAY_OFF, OTA_SUCCESS…
    payload: bytes


# Format firmware : "%4lu  %-19s  %-17s%s" puis " %02X" par octet de payload.
_JOURNAL_ROW = re.compile(r"^\s*(\d+)  (.{19})  (\S+)(.*)$")


@dataclass(frozen=True)
class BackendUris:
    """Sortie de `backend show` (cli_cmd_admin.c) : URIs effectives et leur
    origine — NVS override ou factory default (CONFIG_*_URI du profil)."""
    broker: str
    broker_overridden: bool
    websocket: str
    websocket_overridden: bool


_BACKEND_ROW = re.compile(r"^(broker|websocket)\s*:\s*(\S+)\s+\((NVS override|factory default)\)\s*$")


def parse_backend_show(lines) -> BackendUris:
    found: dict[str, tuple[str, bool]] = {}
    for line in lines:
        m = _BACKEND_ROW.match(line)
        if m:
            found[m.group(1)] = (m.group(2), m.group(3) == "NVS override")
    try:
        return BackendUris(*found["broker"], *found["websocket"])
    except KeyError as exc:
        raise AssertionError(f"`backend show` illisible : {list(lines)!r}") from exc


def parse_journal_rows(lines) -> list[JournalEvent]:
    events = []
    for line in lines:
        m = _JOURNAL_ROW.match(line)
        if m is None:
            continue                         # en-tête ou bruit
        try:
            payload = bytes.fromhex(m.group(4).replace(" ", ""))
        except ValueError:
            continue
        events.append(JournalEvent(int(m.group(1)), m.group(2).strip(), m.group(3), payload))
    return events


class DutLogTimeout(AssertionError):
    """Le motif attendu n'est pas apparu sur la console dans le délai imparti."""

    def __init__(self, pattern: str, timeout_s: float, tail: list[str]) -> None:
        extract = "\n".join(tail[-20:]) or "(aucune ligne reçue)"
        super().__init__(
            f"motif console absent après {timeout_s:g} s : {pattern!r}\n"
            f"--- dernières lignes ---\n{extract}"
        )


class DutCliUnavailable(AssertionError):
    """La CLI BLE n'a pas pu être (re)connectée dans le délai imparti."""


class Dut:
    """Le device sous test, vu du banc : console de logs + CLI BLE."""

    def __init__(self, config: DutConfig, log_dir: Path) -> None:
        self._cfg = config
        self._log_dir = log_dir
        # console de logs
        self._log_session = None
        self._lines: list[str] = []          # journal console, index absolu (= mark)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._logfile = None
        # CLI BLE (ouverte paresseusement, rouverte après chaque reboot)
        self._ble_session = None
        self._ble_address: str | None = config.ble_address
        self._admin_armed = False        # `help admin_mode` retombe à chaque boot

    # -- cycle de vie --------------------------------------------------------

    def open(self) -> None:
        self._log_dir.mkdir(parents=True, exist_ok=True)
        stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        self._logfile = (self._log_dir / f"{stamp}-{self._cfg.serial}-console.log").open(
            "a", encoding="utf-8")
        self._open_log_session()
        self._stop.clear()
        self._thread = threading.Thread(target=self._read_loop, name="zce-atb-console",
                                        daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
        self._close_ble()
        self._close_log_session()
        if self._logfile is not None:
            self._logfile.close()

    # -- console de logs -----------------------------------------------------

    def _open_log_session(self) -> None:
        link = UartLink(port=self._cfg.log_port, baudrate=self._cfg.baudrate)
        self._log_session = link.open(self._cfg.serial)

    def _close_log_session(self) -> None:
        if self._log_session is not None:
            close = getattr(self._log_session, "close", None)
            if close is not None:
                try:
                    close()
                except Exception:
                    pass
            self._log_session = None

    def _read_loop(self) -> None:
        failures = 0
        while not self._stop.is_set():
            try:
                received = self._log_session.poll()
                failures = 0
            except Exception:
                # Port momentanément indisponible — la console USB Serial/JTAG
                # se ré-énumère sur certains resets. Après quelques échecs on
                # tente une réouverture complète du port.
                failures += 1
                if failures >= 4:
                    self._close_log_session()
                    try:
                        self._open_log_session()
                        failures = 0
                    except Exception:
                        pass
                time.sleep(0.5)
                continue
            for line in received:
                with self._lock:
                    self._lines.append(line.text)
                self._logfile.write(f"{_dt.datetime.now().isoformat(timespec='milliseconds')} "
                                    f"{line.text}\n")
            if received:
                self._logfile.flush()
            time.sleep(0.02)

    def mark(self) -> int:
        """Repère à prendre AVANT l'action dont on veut observer l'effet."""
        with self._lock:
            return len(self._lines)

    def log_since(self, mark: int) -> list[str]:
        with self._lock:
            return self._lines[mark:]

    def wait_for_log(self, pattern: str, *, since: int | None = None,
                     timeout: float = 30.0) -> str:
        """Attend (regex) une ligne console à partir du repère ``since`` ;
        rend la ligne trouvée, lève DutLogTimeout sinon."""
        rx = re.compile(pattern)
        cursor = self.mark() if since is None else since
        deadline = time.monotonic() + timeout
        while True:
            with self._lock:
                new, cursor = self._lines[cursor:], len(self._lines)
            for line in new:
                if rx.search(line):
                    return line
            if time.monotonic() > deadline:
                raise DutLogTimeout(pattern, timeout, self.log_since(max(0, cursor - 50)))
            time.sleep(0.05)

    # -- CLI usine (BLE) -----------------------------------------------------

    def _resolve_ble_address(self) -> str:
        """MAC configurée, ou scan par nom si le poste n'a qu'une seule prise
        annonçant le service CLI (sinon : configurer ble_address)."""
        if self._ble_address:
            return self._ble_address
        found = scan_ble_devices(self._cfg.ble_name)
        if len(found) != 1:
            names = [f"{d.address} ({d.name}, {d.rssi} dBm)" for d in found]
            raise DutCliUnavailable(
                f"scan BLE {self._cfg.ble_name!r} : {len(found)} périphérique(s) "
                f"{names} — renseigner dut.ble_address dans la configuration du poste.")
        self._ble_address = found[0].address
        return self._ble_address

    def _ensure_cli(self, timeout_s: float = 30.0):
        """Session BLE vivante, (re)connectée si besoin — la prise redémarre
        souvent entre deux commandes (reset, OTA, coupure secteur)."""
        if self._ble_session is not None and self._ble_session.alive:
            return self._ble_session
        self._close_ble()
        address = self._resolve_ble_address()
        deadline = time.monotonic() + timeout_s
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            try:
                self._ble_session = BleLink(address).open(self._cfg.serial)
                self._admin_armed = False    # nouvelle session = firmware peut avoir rebooté
                return self._ble_session
            except Exception as exc:       # device en cours de boot, pub BLE absente…
                last_error = exc
                time.sleep(1.0)
        raise DutCliUnavailable(
            f"CLI BLE injoignable ({address}) après {timeout_s:g} s : {last_error}")

    def _close_ble(self) -> None:
        if self._ble_session is not None:
            try:
                self._ble_session.close()
            except Exception:
                pass
            self._ble_session = None

    def _exec(self, session, name: str, *args: str) -> CliResponse:
        session.poll()                      # purge le bruit d'avant la commande
        return CliExecutor(session, profile=self._cfg.cli_profile).execute(name, *args)

    def cli(self, name: str, *args: str, connect_timeout_s: float = 30.0) -> CliResponse:
        """Une commande CLI validée par le catalogue, réponse assemblée
        (CliTimeoutError au-delà du budget du catalogue)."""
        return self._exec(self._ensure_cli(connect_timeout_s), name, *args)

    def cli_admin(self, name: str, *args: str, connect_timeout_s: float = 30.0) -> CliResponse:
        """Commande de test/maintenance (err_raise, wifi_disco, journal clear…) :
        arme le mode admin du firmware si besoin (`help admin_mode <code>`,
        cli.c — seul le nombre d'arguments est vérifié ; l'état retombe au
        boot, d'où le réarmement à chaque nouvelle session BLE)."""
        session = self._ensure_cli(connect_timeout_s)
        if not self._admin_armed:
            armed = self._exec(session, "help", "admin_mode", "1")
            if not armed.ok:
                raise DutCliUnavailable(f"armement du mode admin refusé : CR 0x{armed.status:X}")
            self._admin_armed = True
        return self._exec(session, name, *args)

    def journal_list(self, last_n: int = 20) -> list[JournalEvent]:
        """Les `last_n` derniers événements du journal flash, du plus ancien
        au plus récent."""
        resp = self.cli("journal", "list", str(last_n))
        if not resp.ok:
            raise AssertionError(f"journal list : CR 0x{resp.status:X}")
        return parse_journal_rows(resp.data)

    def backend_show(self) -> BackendUris:
        """URIs backend effectives (produits MQTT ; sans mode admin)."""
        resp = self.cli("backend", "show")
        if not resp.ok:
            raise AssertionError(f"backend show : CR 0x{resp.status:X}")
        return parse_backend_show(resp.data)

    def forget_cli(self) -> None:
        """À appeler quand la prise redémarre par un autre chemin que reset()
        (commande websocket reset, OTA, coupure secteur) : la session BLE est
        morte, on la rouvrira au prochain cli()."""
        self._close_ble()

    def reset(self, connect_timeout_s: float = 30.0) -> None:
        """Reset logiciel sans attendre de statut : la cible redémarre avant
        d'avoir répondu. L'écriture GATT peut lever (WinError -2147023673
        « opération annulée ») parce que la prise coupe le lien BLE pendant le
        reboot — c'est le signe que le reset est bien parti, pas un échec. On
        ferme donc la session dans tous les cas ; le boot se vérifie sur la
        console, pas sur un ack BLE."""
        session = self._ensure_cli(connect_timeout_s)
        try:
            session.send(format_command("reset_mcu", profile=self._cfg.cli_profile))
        except Exception:
            pass
        finally:
            self._close_ble()
