"""Configuration de poste : un YAML par banc, validé au chargement.

Même philosophie que production_bench (config.*.yaml) : tout ce qui varie
d'un poste à l'autre (ports COM, adresses VISA, sérial du DUT, tolérances)
vit ici, rien dans le code. Le chemin vient de ZCE_ATB_CONFIG ou, à défaut,
de config/station.yaml à la racine du dépôt.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = REPO_ROOT / "config" / "station.yaml"

PRODUCTS = ("zce", "wattel_em", "ocpp")


class StationConfigError(RuntimeError):
    """Configuration de poste absente ou invalide : on refuse de démarrer."""


@dataclass(frozen=True)
class DutConfig:
    serial: str                 # ex. ZCE-GE-f96dc3ee76ab4291a3 (modèle en majuscules, D9)
    log_port: str               # console de LOGS (lecture seule), ex. COM19 (USB Serial/JTAG)
    ble_address: str | None = None   # MAC du service CLI BLE ; None = scan par ble_name
    ble_name: str = "zce-cli"        # nom annoncé par le service CLI (production_bench)
    baudrate: int = 115200
    cli_profile: str = "internal"
    expected_version: str | None = None   # si renseigné, vérifié par test_version


@dataclass(frozen=True)
class HarnessConfig:
    services: tuple[str, ...] = ("broker", "observer", "commands")


@dataclass(frozen=True)
class Timeouts:
    boot_s: float = 40.0
    broker_connect_s: float = 90.0      # Wi-Fi + SNTP + MQTT, généreux à dessein
    ws_reconnect_s: float = 20.0        # ~10 s nominal après retour du serveur
    command_ack_s: float = 5.0


@dataclass(frozen=True)
class Instruments:
    """Adresses VISA (phase 2). None = instrument absent de ce poste ;
    les tests marqués `instruments` sont alors sautés."""
    asr2100: str | None = None          # ex. TCPIP0::192.168.1.50::inst0::INSTR
    npa701: str | None = None


@dataclass(frozen=True)
class Tolerances:
    """Écarts admis entre la télémétrie du DUT et la référence NPA701."""
    voltage_v: float = 2.0
    current_a: float = 0.5
    power_w: float = 5.0


@dataclass(frozen=True)
class StationConfig:
    product: str
    dut: DutConfig
    lan_ip: str | None = None           # None = détection automatique (em_mqtt)
    harness: HarnessConfig = field(default_factory=HarnessConfig)
    timeouts: Timeouts = field(default_factory=Timeouts)
    instruments: Instruments = field(default_factory=Instruments)
    tolerances: Tolerances = field(default_factory=Tolerances)
    log_dir: Path = REPO_ROOT / "log"


def load_station(path: str | Path | None = None) -> StationConfig:
    path = Path(path or os.environ.get("ZCE_ATB_CONFIG", DEFAULT_CONFIG))
    if not path.is_file():
        raise StationConfigError(
            f"Configuration de poste introuvable : {path}\n"
            f"Copier config/station.example.yaml vers {DEFAULT_CONFIG} et l'adapter, "
            f"ou pointer ZCE_ATB_CONFIG vers le fichier du poste."
        )
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    try:
        product = raw["product"]
        if product not in PRODUCTS:
            raise StationConfigError(f"product={product!r} inconnu (attendu : {PRODUCTS})")
        dut = DutConfig(**raw["dut"])
        harness = HarnessConfig(services=tuple(raw.get("harness", {}).get("services",
                                                                          HarnessConfig().services)))
        return StationConfig(
            product=product,
            dut=dut,
            lan_ip=raw.get("lan_ip"),
            harness=harness,
            timeouts=Timeouts(**raw.get("timeouts", {})),
            instruments=Instruments(**raw.get("instruments", {}) or {}),
            tolerances=Tolerances(**raw.get("tolerances", {})),
        )
    except (KeyError, TypeError) as exc:
        raise StationConfigError(f"{path} : clé manquante ou invalide ({exc})") from exc
