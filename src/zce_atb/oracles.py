"""Oracles : lecture des observations du banc en faits vérifiables.

Regroupe la connaissance des formats (trame télémétrie, payload `errors`,
journal) pour que les tests s'écrivent en termes métier — « relais fermé »,
« code d'erreur publié » — et non en bits et en index.
"""

from __future__ import annotations

from .dut import JournalEvent

# Flags de la trame télémétrie (spec ZCE_Wattel_EM_Protocol, octets 3-4) :
# bit 0 = état relais connu, bit 1 = relais fermé, bits 2-3 = commande persistée.
_FLAG_RELAY_KNOWN = 0x01
_FLAG_RELAY_CLOSED = 0x02


def _flags(frame: dict) -> int:
    return int.from_bytes(bytes.fromhex(frame["flags"]), "little")


def relay_state_known(frame: dict) -> bool:
    return bool(_flags(frame) & _FLAG_RELAY_KNOWN)


def relay_closed(frame: dict) -> bool:
    """Vrai si la trame télémétrie dit « relais fermé » (et état connu)."""
    flags = _flags(frame)
    return bool(flags & _FLAG_RELAY_KNOWN) and bool(flags & _FLAG_RELAY_CLOSED)


def relay_open(frame: dict) -> bool:
    flags = _flags(frame)
    return bool(flags & _FLAG_RELAY_KNOWN) and not (flags & _FLAG_RELAY_CLOSED)


def error_codes(errors_msg: dict) -> list[str]:
    """Codes actifs d'un message `errors` décodé par em_mqtt
    (contrat mqtt_error_reporting.md : instantané complet, retenu)."""
    return [e["code"] for e in errors_msg.get("errors", [])]


def has_error(code: str, severity: str | None = None):
    """Prédicat pour harness.wait_for("errors", …)."""
    def _pred(msg: dict) -> bool:
        return any(e["code"] == code and (severity is None or e["severity"] == severity)
                   for e in msg.get("errors", []))
    return _pred


def no_error(msg: dict) -> bool:
    """Prédicat : payload `errors` vide (plus aucune erreur active)."""
    return not msg.get("malformed") and not msg.get("errors")


def last_event(events: list[JournalEvent], name: str) -> JournalEvent | None:
    for event in reversed(events):
        if event.event == name:
            return event
    return None


def events_after(events: list[JournalEvent], anchor: JournalEvent) -> list[JournalEvent]:
    return [e for e in events if e.index > anchor.index]
