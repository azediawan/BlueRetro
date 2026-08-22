# SPDX-License-Identifier: Apache-2.0
"""Serial side: one thread per adapter, lines out through a queue.

Nothing here knows about parsing or drawing. The UI drains the queue on its own
clock, so a burst of console output can never block the window.
"""
from __future__ import annotations

import queue
import threading
import time

import serial
import serial.tools.list_ports

BAUD = 921600
DESC_HINTS = ("CH340", "CP210", "FTDI", "USB-SERIAL", "Silicon Labs")


def find_adapters() -> list[tuple[str, str]]:
    """USB serial ports, likeliest adapters first. (device, description)."""
    ports = list(serial.tools.list_ports.comports())
    ports.sort(key=lambda p: (not any(h.lower() in (p.description or "").lower()
                                      for h in DESC_HINTS), p.device))
    return [(p.device, p.description or "?") for p in ports
            if any(h.lower() in (p.description or "").lower() for h in DESC_HINTS)]


class PortReader:
    """Reads one port, reconnecting on its own if the adapter is unplugged."""

    def __init__(self, port: str, out: queue.Queue):
        self.port = port
        self.out = out
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.connected = False

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name=f"read-{self.port}", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)

    def _emit(self, tag: str, text: str) -> None:
        self.out.put((self.port, tag, text))

    def _open(self) -> serial.Serial:
        s = serial.Serial()
        s.port = self.port
        s.baudrate = BAUD
        s.timeout = 0.2
        # Both low before open, or the CH340 auto-reset circuit reboots the
        # adapter every time we attach. Verified: with these set the ESP32 keeps
        # running, without them every connect costs a reboot.
        s.dtr = False
        s.rts = False
        s.open()
        return s

    def _run(self) -> None:
        buf = bytearray()
        ser = None
        while not self._stop.is_set():
            try:
                if ser is None:
                    ser = self._open()
                    self.connected = True
                    self._emit("sys", f"ligado em {self.port} a {BAUD} baud")

                chunk = ser.read(4096)
                if chunk:
                    buf += chunk
                    # Firmware terminates with \n, but a reboot can leave a
                    # partial line: keep the tail for the next round.
                    *lines, tail = buf.split(b"\n")
                    buf = bytearray(tail)
                    for raw in lines:
                        text = raw.decode("utf-8", "replace").rstrip("\r")
                        if text:
                            self._emit("line", text)
            except (serial.SerialException, OSError) as e:
                if self.connected:
                    self._emit("sys", f"perdeu {self.port}: {e}")
                self.connected = False
                if ser:
                    try:
                        ser.close()
                    except Exception:
                        pass
                    ser = None
                time.sleep(1.0)

        if ser:
            try:
                ser.close()
            except Exception:
                pass
        self.connected = False
        self._emit("sys", f"desligado de {self.port}")
