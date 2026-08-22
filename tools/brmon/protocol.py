# SPDX-License-Identifier: Apache-2.0
"""BlueRetro console protocol: log lines in, adapter state out.

Pure functions and plain data, no serial port and no UI. That keeps the part
that is easy to get wrong testable against recorded output, which is what
test_protocol.py does.

Two stages:

    line  -> parse_line()  -> Event | None
    Event -> AdapterState.apply()  -> updated state

The vocabulary comes from the printf calls in main/bluetooth/{hci,l2cap}.c,
main/bluetooth/hidp/ps.c and main/system/manager.c.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum


class Kind(Enum):
    """What a line means. Ordered roughly by how far a connection has gotten."""
    NOISE = "noise"                 # ESP-IDF chatter, BLE adverts, HCI bookkeeping
    BOOT = "boot"
    ADAPTER_ADDR = "adapter_addr"
    INQUIRY_START = "inquiry_start"
    DEVICE_FOUND = "device_found"
    DEVICE_SKIPPED = "device_skipped"   # our allowlist turned one away
    CONNECTED = "connected"             # ACL up
    IDENTIFIED = "identified"           # remote name known
    PAIRED = "paired"
    ENCRYPTED = "encrypted"
    HID_READY = "hid_ready"             # controller usable
    CHANNEL_REFUSED = "channel_refused"
    ERROR = "error"
    DISCONNECTED = "disconnected"


class State(Enum):
    UNKNOWN = "sem contato"
    IDLE = "ocioso"
    SEARCHING = "procurando"
    CONNECTING = "conectando"
    PAIRED = "pareado"
    READY = "pronto"
    FAILED = "falhou"


@dataclass(frozen=True)
class Event:
    kind: Kind
    raw: str
    detail: str = ""
    dev: int | None = None
    bdaddr: str | None = None
    name: str | None = None
    code: int | None = None


# --- line patterns -----------------------------------------------------------
#
# Anchored on the exact printf format strings so a firmware change that renames
# a message shows up as "unparsed" rather than as silently wrong state.

_PATTERNS: list[tuple[re.Pattern, Kind]] = [
    (re.compile(r"^local_bdaddr:\s*([0-9A-Fa-f:]{17})"), Kind.ADAPTER_ADDR),
    (re.compile(r"^# internal_flag_init:"), Kind.BOOT),
    (re.compile(r"^# bt_hci_cmd_periodic_inquiry"), Kind.INQUIRY_START),
    (re.compile(r"^Inquiry dev: (\d+) type: (\d+) bdaddr: ([0-9A-Fa-f:]{17})"), Kind.DEVICE_FOUND),
    (re.compile(r"^# Inquiry: skipping unknown bdaddr"), Kind.DEVICE_SKIPPED),
    (re.compile(r"^dev: (\d+) acl_handle: (0x[0-9A-Fa-f]+)"), Kind.CONNECTED),
    (re.compile(r"^dev: (\d+) type: (\d+):(\d+) (.+)$"), Kind.IDENTIFIED),
    (re.compile(r"^# dev: (\d+) Pairing done"), Kind.PAIRED),
    (re.compile(r"^# dev: (\d+) conn refused scid: (0x[0-9A-Fa-f]+) result: (0x[0-9A-Fa-f]+)"), Kind.CHANNEL_REFUSED),
    (re.compile(r"^# PS init done"), Kind.HID_READY),
    (re.compile(r"^# DISCONN from dev: (\d+)"), Kind.DISCONNECTED),
    (re.compile(r"^# dev: (\d+) error: (0x[0-9A-Fa-f]+)"), Kind.ERROR),
]

_ENCRYPT_EVT = re.compile(r"^# BT_HCI_EVT_ENCRYPT_CHANGE")

# L2CAP connection response result codes, from zephyr/l2cap_internal.h.
REFUSAL = {
    0x0000: "sucesso",
    0x0001: "pendente",
    0x0002: "PSM não suportado",
    0x0003: "bloqueio de segurança",   # o SEC_BLOCK que caçamos
    0x0004: "sem recursos",
    0x0006: "canal inválido",
    0x0007: "canal em uso",
}

DEVICE_TYPE = {
    0: "HID genérico", 1: "PS3", 2: "Wii", 3: "PlayStation", 4: "Switch", 5: "Switch 2",
}


def parse_line(line: str) -> Event | None:
    """One console line to an Event. None means the line carries no state."""
    line = line.strip()
    if not line:
        return None

    for pattern, kind in _PATTERNS:
        m = pattern.match(line)
        if not m:
            continue

        if kind is Kind.ADAPTER_ADDR:
            return Event(kind, line, bdaddr=m.group(1).upper())
        if kind is Kind.DEVICE_FOUND:
            return Event(kind, line, dev=int(m.group(1)), bdaddr=m.group(3).upper())
        if kind is Kind.CONNECTED:
            return Event(kind, line, dev=int(m.group(1)), detail=f"handle {m.group(2)}")
        if kind is Kind.IDENTIFIED:
            kind_name = DEVICE_TYPE.get(int(m.group(2)), f"tipo {m.group(2)}")
            return Event(kind, line, dev=int(m.group(1)), name=m.group(4).strip(), detail=kind_name)
        if kind is Kind.CHANNEL_REFUSED:
            code = int(m.group(3), 16)
            return Event(kind, line, dev=int(m.group(1)), code=code,
                         detail=REFUSAL.get(code, f"código {code:#06x}"))
        if kind is Kind.ERROR:
            code = int(m.group(2), 16)
            return Event(kind, line, dev=int(m.group(1)), code=code, detail=f"status {code:#04x}")
        if kind in (Kind.PAIRED, Kind.DISCONNECTED):
            return Event(kind, line, dev=int(m.group(1)))
        return Event(kind, line)

    if _ENCRYPT_EVT.match(line):
        return Event(Kind.ENCRYPTED, line)

    return Event(Kind.NOISE, line)


# --- state -------------------------------------------------------------------

@dataclass
class AdapterState:
    """What one adapter is doing, rebuilt from the events it has emitted."""
    port: str
    state: State = State.UNKNOWN
    adapter_addr: str | None = None
    ctrl_addr: str | None = None
    ctrl_name: str | None = None
    ctrl_type: str | None = None
    note: str = ""
    encrypted: bool = False
    skipped: int = 0                        # controllers turned away by the allowlist
    timeline: list[tuple[Kind, str]] = field(default_factory=list)

    def _mark(self, kind: Kind, text: str) -> None:
        self.timeline.append((kind, text))
        del self.timeline[:-40]

    def _forget_controller(self) -> None:
        self.ctrl_addr = self.ctrl_name = self.ctrl_type = None
        self.encrypted = False

    def apply(self, ev: Event) -> bool:
        """Fold an event in. True when something worth redrawing changed."""
        if ev.kind is Kind.NOISE:
            return False

        if ev.kind is Kind.BOOT:
            self.state = State.IDLE
            self._forget_controller()
            self.note = "reiniciou"
            self._mark(ev.kind, "adaptador reiniciou")

        elif ev.kind is Kind.ADAPTER_ADDR:
            self.adapter_addr = ev.bdaddr

        elif ev.kind is Kind.INQUIRY_START:
            self.state = State.SEARCHING
            self.note = "varrendo o ar"

        elif ev.kind is Kind.DEVICE_SKIPPED:
            self.skipped += 1
            self.note = "ignorou um controle que não é dele"
            self._mark(ev.kind, "ignorou desconhecido, fora do modo pareamento")

        elif ev.kind is Kind.DEVICE_FOUND:
            self.ctrl_addr = ev.bdaddr
            self.state = State.CONNECTING
            self.note = "achou um controle"
            self._mark(ev.kind, f"achou {ev.bdaddr}")

        elif ev.kind is Kind.CONNECTED:
            self.state = State.CONNECTING
            self.note = ev.detail
            self._mark(ev.kind, f"enlace aberto, {ev.detail}")

        elif ev.kind is Kind.IDENTIFIED:
            self.ctrl_name = ev.name
            self.ctrl_type = ev.detail
            self._mark(ev.kind, f"é um {ev.detail}: {ev.name}")

        elif ev.kind is Kind.PAIRED:
            self.state = State.PAIRED
            self.note = "pareado, abrindo canal"
            self._mark(ev.kind, "pareamento concluído")

        elif ev.kind is Kind.ENCRYPTED:
            self.encrypted = True
            self._mark(ev.kind, "link criptografado")

        elif ev.kind is Kind.HID_READY:
            self.state = State.READY
            self.note = "controle pronto"
            self._mark(ev.kind, "controle pronto para uso")

        elif ev.kind is Kind.CHANNEL_REFUSED:
            self.state = State.FAILED
            self.note = f"canal recusado: {ev.detail}"
            self._mark(ev.kind, self.note)

        elif ev.kind is Kind.ERROR:
            self.state = State.FAILED
            self.note = f"erro {ev.detail}"
            self._mark(ev.kind, self.note)

        elif ev.kind is Kind.DISCONNECTED:
            self.state = State.IDLE
            self._forget_controller()
            self.note = "controle desconectou"
            self._mark(ev.kind, "desconectado")

        return True
