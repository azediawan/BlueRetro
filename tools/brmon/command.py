# SPDX-License-Identifier: Apache-2.0
"""Talking to the adapter's console, not just listening to it.

The firmware answers commands on the same UART it logs to, and prefixes every
reply with '+' so replies can be picked out of the '#' debug stream. That is the
whole trick: send a line, collect '+' lines until the console goes quiet.

Config edits live in RAM on the adapter until save(), so a wrong value costs
nothing and a cable pulled mid-edit changes nothing.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass

import serial

BAUD = 921600
REPLY = "+"
DEBUG = "#"


@dataclass
class Config:
    """Global config as the adapter reports it."""
    system: int = 0
    multitap: int = 0
    inquiry: int = 0
    bank: int = 0
    lock_inquiry: bool = False

    @property
    def inquiry_name(self) -> str:
        return "auto" if self.inquiry == 0 else "manual"

    @property
    def bank_name(self) -> str:
        return "debug" if self.bank == 0xDB else str(self.bank)

    @property
    def inquiry_effective(self) -> str:
        """What the adapter actually does, which is not always what it stores."""
        return "manual" if self.lock_inquiry else self.inquiry_name


_CFG = re.compile(r"\+cfg system=(\d+) multitap=(\d+) inquiry=(\d+)\S* bank=0x([0-9A-Fa-f]+) "
                  r"lock_inquiry=(\d)")


def open_port(port: str) -> serial.Serial:
    """Attach without rebooting the adapter.

    Both lines low before open, or the CH340 auto-reset circuit pulses EN and
    the ESP32 restarts every time we connect.
    """
    s = serial.Serial()
    s.port = port
    s.baudrate = BAUD
    s.timeout = 0.1
    s.dtr = False
    s.rts = False
    s.open()
    return s


def reset(s: serial.Serial) -> None:
    """Restart into normal run mode: IO0 high, pulse EN."""
    s.dtr = False
    s.rts = True
    time.sleep(0.12)
    s.rts = False


def send(s: serial.Serial, cmd: str, quiet_for: float = 0.35,
         timeout: float = 6.0, until: str | None = None) -> list[str]:
    """Send one command, return its '+' replies.

    Stops on `until` when given, otherwise when the port has been quiet for
    `quiet_for`. The quiet window exists because most commands answer in a
    couple of lines and there is no end marker to wait for.
    """
    s.reset_input_buffer()
    s.write((cmd + "\r\n").encode())
    s.flush()

    replies: list[str] = []
    buf = b""
    start = last = time.time()
    while time.time() - start < timeout:
        chunk = s.read(65536)
        if chunk:
            buf += chunk
            last = time.time()
            *lines, tail = buf.split(b"\n")
            buf = tail
            for raw in lines:
                text = raw.decode("utf-8", "replace").strip()
                if text.startswith(REPLY):
                    replies.append(text)
            if until and any(r.startswith(until) for r in replies):
                break
        elif time.time() - last > quiet_for:
            break
    return replies


def get_config(s: serial.Serial) -> Config | None:
    for line in send(s, "cfg"):
        m = _CFG.match(line)
        if m:
            return Config(system=int(m[1]), multitap=int(m[2]), inquiry=int(m[3]),
                          bank=int(m[4], 16), lock_inquiry=m[5] == "1")
    return None


def set_field(s: serial.Serial, field: str, value: str) -> tuple[bool, str]:
    replies = send(s, f"cfg {field} {value}")
    for r in replies:
        if r.startswith("+err"):
            return False, r
    note = next((r for r in replies if r.startswith("+note")), "")
    return any(r.startswith("+cfg") for r in replies), note


def save(s: serial.Serial) -> bool:
    return any(r.startswith("+save ok") for r in send(s, "save", timeout=8.0))


def version(s: serial.Serial) -> tuple[str, str]:
    ver = name = ""
    for r in send(s, "version"):
        if r.startswith("+version "):
            ver = r[9:]
        elif r.startswith("+name "):
            name = r[6:]
    return ver, name


def dump_trace(s: serial.Serial, progress=None) -> bytes:
    """Pull the 128 KB capture buffer as hex.

    Unlike the BLE route this is a pure read, so it does not pause the capture
    the way any config-app write does.
    """
    s.reset_input_buffer()
    s.write(b"trace\r\n")
    s.flush()

    raw = b""
    start = time.time()
    while time.time() - start < 40:
        chunk = s.read(65536)
        if chunk:
            raw += chunk
            if progress:
                progress(len(raw))
            if b"+trace end" in raw:
                break
        elif b"+trace begin" not in raw and time.time() - start > 5:
            raise TimeoutError("o adaptador não respondeu ao comando trace")

    lines = raw.split(b"\n")
    try:
        i0 = next(i for i, l in enumerate(lines) if l.startswith(b"+trace begin"))
        i1 = next(i for i, l in enumerate(lines) if l.startswith(b"+trace end"))
    except StopIteration:
        raise ValueError("despejo incompleto, marcadores não encontrados")

    body = [l.strip() for l in lines[i0 + 1:i1]
            if l.strip() and not l.startswith((b"+", b"#"))]
    return bytes.fromhex(b"".join(body).decode("ascii", "ignore"))
